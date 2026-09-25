"""Language codes and English names -> Wikipedia language editions.

The table (languages.json) is recorded from the live site matrix by
tests/fixtures/record.py. Accepted input: the Wikipedia domain code (pl, zh-yue),
the ISO language code where it differs (yue, nan), or the English name (Polish).
Anything else is an error with a "did you mean" hint, so the agent retries with a
valid code instead of silently analysing the wrong edition.
"""

import difflib
import functools
import json
from dataclasses import dataclass
from pathlib import Path

from wds_lib import WdsError

TABLE_PATH = Path(__file__).with_name("languages.json")

# Each language costs several API requests; beyond ~10 a first run no longer fits
# into an agent's 2-minute Bash call. The agent can split a bigger comparison.
MAX_LANGS = 10

# Codes people use that the site matrix does not list: ISO 639 'nb' is Norwegian
# Bokmål, whose Wikipedia lives at no.wikipedia.org.
EXTRA_CODES = {"nb": "no"}
EXTRA_NAMES = {"bokmål": "no", "norwegian bokmål": "no", "slovene": "sl", "farsi": "fa"}

# Country codes often typed instead of language codes. Suggested, never accepted:
# a silent guess could analyse the wrong edition.
COUNTRY_CODE_HINTS = {
    "cz": "cs",
    "ua": "uk",
    "gr": "el",
    "jp": "ja",
    "dk": "da",
    "cn": "zh",
    "kr": "ko",
}

# How similar a typo must be to a known code or name to be suggested.
SUGGESTION_CUTOFF = 0.75


@dataclass(frozen=True)
class Lang:
    code: str  # Wikipedia domain code: pl, no, zh-yue
    name: str  # English name
    dbname: str  # Wikidata sitelink key: plwiki, be_x_oldwiki

    @property
    def project(self) -> str:
        """Project name in the pageviews API."""
        return f"{self.code}.wikipedia"

    @property
    def api_url(self) -> str:
        return f"https://{self.code}.wikipedia.org/w/api.php"


@functools.cache
def _tables() -> tuple[dict[str, Lang], dict[str, str], dict[str, str]]:
    """(by domain code, accepted alias -> domain code, suggestion key -> domain code)."""
    rows = json.loads(TABLE_PATH.read_text(encoding="utf-8"))["wikis"]
    by_code = {row[0]: Lang(code=row[0], name=row[1], dbname=row[3]) for row in rows}
    # Domain codes first, so an alias can never shadow a real edition's code.
    accepted = {code: code for code in by_code}
    for domain_code, english, _, _, language_code in rows:
        accepted.setdefault(language_code, domain_code)
        accepted.setdefault(english.lower(), domain_code)
    for alias, domain_code in {**EXTRA_CODES, **EXTRA_NAMES}.items():
        accepted.setdefault(alias, domain_code)
    suggest = {autonym.lower(): row_code for row_code, _, autonym, _, _ in rows}
    suggest.update(accepted)
    suggest.update(COUNTRY_CODE_HINTS)
    return by_code, accepted, suggest


def lookup(token: str) -> Lang | None:
    by_code, accepted, _ = _tables()
    domain_code = accepted.get(token.strip().lower())
    return by_code[domain_code] if domain_code else None


def suggest(token: str) -> str | None:
    """Closest valid domain code for an unknown token, or None."""
    _, _, keys = _tables()
    key = token.strip().lower()
    if key in keys:
        return keys[key]
    close = difflib.get_close_matches(key, keys, n=1, cutoff=SUGGESTION_CUTOFF)
    return keys[close[0]] if close else None


def parse_langs(value: str) -> list[Lang]:
    """'pl, Czech,nb' -> [pl, cs, no]; order kept, duplicates dropped."""
    tokens = [token.strip() for token in value.split(",") if token.strip()]
    if not tokens:
        raise WdsError(
            "no languages given", hint="pass --langs with codes, e.g. --langs pl,cs"
        )
    langs: list[Lang] = []
    unknown: list[str] = []
    for token in tokens:
        lang = lookup(token)
        if lang is None:
            guess = suggest(token)
            unknown.append(
                f"'{token}', did you mean '{guess}'?" if guess else f"'{token}'"
            )
        elif lang not in langs:
            langs.append(lang)
    if unknown:
        label = "unknown language " if len(unknown) == 1 else "unknown languages: "
        raise WdsError(
            label + "; ".join(unknown),
            hint="use Wikipedia language codes (pl, cs, uk) or English names "
            "(Polish, Czech, Ukrainian)",
        )
    if len(langs) > MAX_LANGS:
        raise WdsError(
            f"{len(langs)} languages requested, the limit is {MAX_LANGS} per run",
            hint=f"split into runs of up to {MAX_LANGS} languages; "
            "a rerun with overlapping languages reuses the cache",
        )
    return langs
