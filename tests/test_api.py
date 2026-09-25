import email.message
import io
import itertools
import json
import types
import urllib.error

import pytest
from wds_lib import REPO_URL, WdsError, api

URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/cs.wikipedia/all-access/user/P%C5%99eru%C5%A1ovan%C3%BD_p%C5%AFst/daily/20200901/20260831"


class FakeClock:
    """Stands in for the time module: sleeping advances the clock instantly."""

    def __init__(self) -> None:
        self.now = 1_000_000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(URL, code, "error", headers, None)


class FakeNetwork:
    """Replays scripted outcomes: a dict/list is a 200 JSON body, an exception is raised."""

    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.requests: list = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if not self.outcomes:
            raise AssertionError("unexpected network request")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return io.BytesIO(json.dumps(outcome).encode("utf-8"))


@pytest.fixture
def clock(monkeypatch, tmp_path):
    fake = FakeClock()
    monkeypatch.setattr(
        api,
        "time",
        types.SimpleNamespace(
            **{name: getattr(fake, name) for name in ("time", "monotonic", "sleep")}
        ),
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv(api.USER_AGENT_ENV, raising=False)
    monkeypatch.setattr(api, "_last_request_at", None)
    monkeypatch.setattr(api, "stats", {"network": 0, "cache": 0})
    return fake


def install(monkeypatch, network: FakeNetwork) -> FakeNetwork:
    monkeypatch.setattr(api.urllib.request, "urlopen", network)
    return network


def test_user_agent_names_the_repo_and_can_be_overridden(clock, monkeypatch):
    network = install(monkeypatch, FakeNetwork({"items": []}))
    api.get_json(URL, ttl=api.TTL_FOREVER)
    sent = network.requests[0].get_header("User-agent")
    assert REPO_URL in sent and sent.startswith("wikipedia-demand-signals/")

    monkeypatch.setenv(api.USER_AGENT_ENV, "my-fork/1.0 (+https://example.org)")
    assert api.user_agent() == "my-fork/1.0 (+https://example.org)"


def test_429_waits_for_retry_after_then_succeeds(clock, monkeypatch):
    network = install(monkeypatch, FakeNetwork(http_error(429, "2"), {"items": [1]}))
    assert api.get_json(URL, ttl=api.TTL_FOREVER) == {"items": [1]}
    assert len(network.requests) == 2
    assert 2 in clock.sleeps


def test_503_without_retry_after_backs_off_exponentially_and_gives_up(
    clock, monkeypatch
):
    install(monkeypatch, FakeNetwork(*[http_error(503)] * api.MAX_ATTEMPTS))
    with pytest.raises(WdsError) as info:
        api.get_json(URL, ttl=api.TTL_FOREVER)
    assert "cached" in info.value.hint
    backoffs = [s for s in clock.sleeps if s >= api.BACKOFF_BASE_S]
    assert backoffs == [5, 10, 20, 40]


def test_retry_after_longer_than_a_bash_call_fails_fast(clock, monkeypatch):
    install(monkeypatch, FakeNetwork(http_error(429, "3600")))
    with pytest.raises(WdsError):
        api.get_json(URL, ttl=api.TTL_FOREVER)
    assert all(s < api.MAX_RETRY_WAIT_S for s in clock.sleeps)


def test_404_returns_none_not_an_exception(clock, monkeypatch):
    install(monkeypatch, FakeNetwork(http_error(404)))
    assert api.get_json(URL, ttl=api.TTL_FOREVER) is None


def test_other_http_errors_become_agent_readable_errors(clock, monkeypatch):
    install(monkeypatch, FakeNetwork(http_error(400)))
    with pytest.raises(WdsError) as info:
        api.get_json(URL, ttl=api.TTL_FOREVER)
    assert "400" in info.value.error


def test_second_call_is_served_from_cache_without_network(clock, monkeypatch):
    install(monkeypatch, FakeNetwork({"items": [1]}))
    api.get_json(URL, ttl=api.TTL_FOREVER)
    install(monkeypatch, FakeNetwork())  # any request now fails the test
    clock.now += 10 * 365 * 24 * 3600
    assert api.get_json(URL, ttl=api.TTL_FOREVER) == {"items": [1]}
    assert api.stats == {"network": 1, "cache": 1}


def test_404_is_cached_too(clock, monkeypatch):
    install(monkeypatch, FakeNetwork(http_error(404)))
    api.get_json(URL, ttl=api.TTL_FOREVER)
    install(monkeypatch, FakeNetwork())
    assert api.get_json(URL, ttl=api.TTL_FOREVER) is None


def test_week_ttl_expires(clock, monkeypatch):
    install(monkeypatch, FakeNetwork({"v": 1}, {"v": 2}))
    assert api.get_json(URL, ttl=api.TTL_WEEK) == {"v": 1}
    clock.now += api.TTL_WEEK - 60
    assert api.get_json(URL, ttl=api.TTL_WEEK) == {"v": 1}
    clock.now += 120
    assert api.get_json(URL, ttl=api.TTL_WEEK) == {"v": 2}


def test_cache_file_is_written_right_after_the_response(clock, monkeypatch, tmp_path):
    install(monkeypatch, FakeNetwork({"v": 1}, http_error(400)))
    api.get_json(URL, ttl=api.TTL_FOREVER)
    with pytest.raises(WdsError):
        api.get_json(URL + "&other", ttl=api.TTL_FOREVER)
    cached = list((tmp_path / "wikipedia-demand-signals").rglob("*.json"))
    assert len(cached) == 1  # the first answer survived the failing second call


def test_corrupt_cache_file_is_a_miss(clock, monkeypatch):
    install(monkeypatch, FakeNetwork({"v": 1}, {"v": 2}))
    api.get_json(URL, ttl=api.TTL_FOREVER)
    api._cache_path(URL).write_text("{half-writ", encoding="utf-8")
    assert api.get_json(URL, ttl=api.TTL_FOREVER) == {"v": 2}


def test_requests_are_spaced_by_at_least_the_minimum_interval(clock, monkeypatch):
    network = install(monkeypatch, FakeNetwork({"v": 1}, {"v": 2}, {"v": 3}))
    starts = []
    original = network.__call__

    def timed(request, timeout=None):
        starts.append(clock.now)
        return original(request, timeout)

    monkeypatch.setattr(api.urllib.request, "urlopen", timed)
    for i in range(3):
        api.get_json(f"{URL}&n={i}", ttl=api.TTL_FOREVER)
    gaps = [b - a for a, b in itertools.pairwise(starts)]
    assert all(gap >= api.MIN_INTERVAL_S - 1e-9 for gap in gaps)


def test_timeout_is_retried(clock, monkeypatch):
    install(monkeypatch, FakeNetwork(TimeoutError("read timed out"), {"v": 1}))
    assert api.get_json(URL, ttl=api.TTL_FOREVER) == {"v": 1}


def test_no_internet_is_reported_with_a_hint(clock, monkeypatch):
    install(
        monkeypatch, FakeNetwork(urllib.error.URLError(OSError("getaddrinfo failed")))
    )
    with pytest.raises(WdsError) as info:
        api.get_json(URL, ttl=api.TTL_FOREVER)
    assert "internet" in info.value.hint


def test_titles_are_percent_encoded_as_utf8():
    assert api.quote_title("Přerušovaný půst") == "P%C5%99eru%C5%A1ovan%C3%BD_p%C5%AFst"
    assert api.quote_title("AC/DC") == "AC%2FDC"


def test_build_url_is_stable_regardless_of_param_order():
    a = api.build_url("https://x.org/w/api.php", {"b": "2", "a": "z y"})
    b = api.build_url("https://x.org/w/api.php", {"a": "z y", "b": "2"})
    assert a == b == "https://x.org/w/api.php?a=z%20y&b=2"
