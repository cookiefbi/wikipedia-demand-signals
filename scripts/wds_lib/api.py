"""HTTP access to Wikimedia APIs: User-Agent, throttling, retries, file cache.

Wikimedia rate limits (2026): 10 requests/min for unidentified clients, 200/min
with a User-Agent that names the client and a contact URL. We identify ourselves,
go strictly sequentially at ~3 requests/s and cache every response on disk, so a
rerun after a timeout continues where the previous run stopped.
"""

import email.utils
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from wds_lib import REPO_URL, WdsError, __version__, log

USER_AGENT_ENV = "WDS_USER_AGENT"

# ~3 requests/s (~176/min): below the 200/min limit for identified clients.
MIN_INTERVAL_S = 0.34

# 429/503 mean "slow down" or "try again": at most 5 attempts in total. Without a
# Retry-After header we wait 5, 10, 20, 40 s (exponential, starting at 5 s).
RETRY_STATUSES = (429, 503)
MAX_ATTEMPTS = 5
BACKOFF_BASE_S = 5.0
# An agent's Bash call times out after ~2 minutes; a longer server-requested wait
# cannot fit, so we stop and let the agent rerun (finished requests stay cached).
MAX_RETRY_WAIT_S = 60.0
REQUEST_TIMEOUT_S = 30.0

# Cache lifetimes. Pageviews of a closed, month-aligned window never change, so they
# are kept forever; search results and redirects can change and live one week.
TTL_FOREVER = None
TTL_WEEK = 7 * 24 * 3600

# Network vs cache counts for the stderr summary ("adding sk fetched only sk").
stats = {"network": 0, "cache": 0}
_last_request_at: float | None = None


def user_agent() -> str:
    default = f"wikipedia-demand-signals/{__version__} (+{REPO_URL})"
    return os.environ.get(USER_AGENT_ENV) or default


def cache_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / ".cache"
    return root / "wikipedia-demand-signals"


def quote_title(title: str) -> str:
    """Article title as a REST path segment: spaces to underscores, UTF-8 percent-encoded.

    Must happen in code: shells on Windows can pass non-ASCII in the ANSI code page
    and the API then answers 404 as if the article had no views.
    """
    return urllib.parse.quote(title.replace(" ", "_"), safe="")


def build_url(base: str, params: dict[str, str]) -> str:
    """Query string with sorted keys, so the same request always has the same cache key."""
    query = urllib.parse.urlencode(sorted(params.items()), quote_via=urllib.parse.quote)
    return f"{base}?{query}"


def get_json(url: str, *, ttl: float | None) -> dict | list | None:
    """GET a JSON document through the cache. Returns None for HTTP 404.

    ttl: seconds a cached answer stays valid; TTL_FOREVER (None) never expires.
    """
    path = _cache_path(url)
    entry = _read_cache(path, ttl)
    if entry is not None:
        stats["cache"] += 1
        return entry["body"]
    status, body = _fetch(url)
    stats["network"] += 1
    _write_cache(
        path, {"url": url, "fetched_at": time.time(), "status": status, "body": body}
    )
    return body


def is_cached(url: str) -> bool:
    """Whether an answer kept forever (TTL_FOREVER: pageviews) is already on disk."""
    return _cache_path(url).exists()


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir() / digest[:2] / f"{digest}.json"


def _read_cache(path: Path, ttl: float | None) -> dict | None:
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # missing or half-written file: treat as a miss
    if ttl is not None and time.time() - entry["fetched_at"] > ttl:
        return None
    return entry


def _write_cache(path: Path, entry: dict) -> None:
    """Write right after the response arrives; atomic, so a killed run leaves no junk."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log(f"cache not writable ({exc}); continuing without it")


def _throttle() -> None:
    global _last_request_at
    if _last_request_at is not None:
        wait = _last_request_at + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
    _last_request_at = time.monotonic()


def _retry_after_seconds(header: str | None) -> float | None:
    """Retry-After is either delay-seconds or an HTTP date."""
    if not header:
        return None
    try:
        return max(0.0, float(header))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


def _describe(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.unquote(parts.netloc + parts.path)[:160]


def _fetch(url: str) -> tuple[int, dict | list | None]:
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent(), "Accept": "application/json"}
    )
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _throttle()
        log(f"GET {_describe(url)}")
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                return 200, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return 404, None
            if exc.code not in RETRY_STATUSES:
                raise WdsError(
                    f"Wikimedia API answered HTTP {exc.code} for {_describe(url)}",
                    hint="not a temporary error; check the arguments (language code, "
                    "title) and rerun",
                ) from exc
            reason = f"HTTP {exc.code}"
            wait = _retry_after_seconds(exc.headers.get("Retry-After"))
        except (TimeoutError, ConnectionError, urllib.error.URLError) as exc:
            reason_obj = getattr(exc, "reason", exc)
            if not isinstance(reason_obj, (TimeoutError, ConnectionError)):
                raise WdsError(
                    f"cannot reach Wikimedia APIs: {reason_obj}",
                    hint="the skill needs internet access to wikimedia.org and "
                    "wikipedia.org; check the connection and rerun",
                ) from exc
            reason = type(reason_obj).__name__
            wait = None
        if wait is None:
            wait = BACKOFF_BASE_S * 2 ** (attempt - 1)
        if attempt == MAX_ATTEMPTS:
            break
        if wait > MAX_RETRY_WAIT_S:
            raise WdsError(
                f"Wikimedia asked to wait {wait:.0f} s ({reason})",
                hint="rerun the same command in a few minutes; "
                "everything fetched so far is cached",
            )
        log(f"{reason}, waiting {wait:.0f} s (attempt {attempt}/{MAX_ATTEMPTS})")
        time.sleep(wait)
    raise WdsError(
        f"Wikimedia APIs still failing after {MAX_ATTEMPTS} attempts ({reason})",
        hint="rerun the same command in a minute; everything fetched so far is cached",
    )
