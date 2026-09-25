"""Record live Wikimedia API data into the repository.

Everything tests and the language table rely on comes from real API answers,
never from hand-written guesses. Rerun to refresh:

    python tests/fixtures/record.py languages   # scripts/wds_lib/languages.json
    python tests/fixtures/record.py resolve     # tests/fixtures/resolve/*.json
    python tests/fixtures/record.py analyze     # tests/fixtures/analyze.json
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from wds_lib import analyze, api, langs, pageviews, resolve

FRESH = 0  # ttl=0: always ask the API, never the local cache

SITEMATRIX_URL = api.build_url(
    "https://meta.wikimedia.org/w/api.php",
    {
        "action": "sitematrix",
        "format": "json",
        "formatversion": "2",
        "smtype": "language",
        "smlangprop": "code|name|localname|site",
        "smsiteprop": "url|dbname|code",
        "uselang": "en",
        "smlimit": "max",
    },
)


def record_languages() -> Path:
    """Open Wikipedias from the site matrix: domain code, names, Wikidata site key."""
    matrix = api.get_json(SITEMATRIX_URL, ttl=FRESH)["sitematrix"]
    wikis = []
    for key, language in matrix.items():
        if key == "count":
            continue
        for site in language.get("site", []):
            if site.get("code") != "wiki" or site.get("closed"):
                continue
            domain_code = site["url"].removeprefix("https://").split(".")[0]
            wikis.append(
                [
                    domain_code,
                    language["localname"],
                    language["name"],
                    site["dbname"],
                    language["code"],
                ]
            )
    wikis.sort()
    out = REPO / "scripts" / "wds_lib" / "languages.json"
    lines = [json.dumps(row, ensure_ascii=False) for row in wikis]
    header = {
        "source": "meta.wikimedia.org action=sitematrix (open Wikipedias only)",
        "recorded": datetime.datetime.now(datetime.UTC).date().isoformat(),
        "columns": [
            "domain_code",
            "english_name",
            "autonym",
            "dbname",
            "language_code",
        ],
    }
    body = ",\n    ".join(lines)
    text = json.dumps(header, ensure_ascii=False, indent=2)[:-2]
    text += f',\n  "wikis": [\n    {body}\n  ]\n}}\n'
    json.loads(text)  # must stay valid JSON
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"{len(wikis)} Wikipedias -> {out}", file=sys.stderr)
    return out


# SPEC section 7: the five queries the resolve rules were checked against.
RESOLVE_QUERIES = [
    "astronomy",
    "intermittent fasting",
    "learning English",
    "Java",
    "Mercury",
]
RESOLVE_DIR = Path(__file__).with_name("resolve")


def slug(text: str) -> str:
    return "-".join(text.lower().split())


def trim_entities(body: dict) -> dict:
    """Keep only what resolve.py reads; full answers are 0.3-1.3 MB per query."""
    entities = {}
    for qid, entity in body.get("entities", {}).items():
        if "missing" in entity:
            entities[qid] = entity
            continue
        p31 = [
            {"mainsnak": {"datavalue": {"value": {"id": value["id"]}}}}
            for claim in entity.get("claims", {}).get("P31", [])
            if isinstance(
                value := claim["mainsnak"].get("datavalue", {}).get("value"), dict
            )
        ]
        entities[qid] = {
            "id": entity["id"],
            "labels": entity.get("labels", {}),
            "descriptions": entity.get("descriptions", {}),
            "aliases": entity.get("aliases", {}),
            "sitelinks": {
                site: {"title": link["title"]}
                for site, link in entity.get("sitelinks", {}).items()
            },
            "claims": {"P31": p31},
        }
    return {"entities": entities}


def record_resolve() -> None:
    """Run the real resolve code against the live APIs and keep every answer."""
    RESOLVE_DIR.mkdir(exist_ok=True)
    search_lang = langs.lookup("en")
    requested = langs.parse_langs("pl,cs,uk")
    original = api.get_json
    for query in RESOLVE_QUERIES:
        responses: dict[str, object] = {}

        def recording_get(url: str, *, ttl: float | None, _store=responses):
            body = original(url, ttl=FRESH)
            if "action=wbgetentities" in url:
                body = trim_entities(body)
            _store[url] = body
            return body

        api.get_json = recording_get
        try:
            resolve.resolve(query, requested, search_lang)
        finally:
            api.get_json = original
        fixture = {
            "query": query,
            "search_lang": search_lang.code,
            "recorded": datetime.datetime.now(datetime.UTC).date().isoformat(),
            "responses": responses,
        }
        out = RESOLVE_DIR / f"{slug(query)}.json"
        text = json.dumps(fixture, ensure_ascii=False, indent=1) + "\n"
        out.write_bytes(text.encode("utf-8"))
        print(f"{query}: {len(responses)} responses -> {out.name}", file=sys.stderr)


# The task's examples on real data. One file: answers are shared by URL (the pl and
# cs edition totals serve both topics), and a test may replay any subset.
ANALYZE_SCENARIOS = {
    "astronomy": {"qids": ["Q333"], "articles": [], "langs_arg": "pl,cs,uk"},
    "intermittent-fasting": {
        "qids": ["Q1666254"],
        "articles": ["pl:Głodówka lecznicza"],
        "langs_arg": "pl,cs",
    },
}
ANALYZE_FIXTURE = Path(__file__).with_name("analyze.json")


def compact(url: str, body: object) -> object:
    """Keep what the code reads: pageviews as [timestamp, views] pairs (a tenth of
    the size; tests expand them back), sitelinks without badges.
    """
    if body is None:
        return None
    if url.startswith(pageviews.PAGEVIEWS_API):
        return {"items": [[i["timestamp"], i["views"]] for i in body["items"]]}
    if "action=wbgetentities" in url:
        return {
            "entities": {
                qid: {
                    key: value
                    if key != "sitelinks"
                    else {site: {"title": s["title"]} for site, s in value.items()}
                    for key, value in entity.items()
                }
                for qid, entity in body["entities"].items()
            }
        }
    return body


def record_analyze() -> None:
    """Run the real analyze code against the live APIs and keep every answer."""
    today = datetime.datetime.now(datetime.UTC).date()
    raw: dict[str, object] = {}
    original = api.get_json

    def recording_get(url: str, *, ttl: float | None):
        if url not in raw:  # one fresh answer per URL, also when asked twice
            raw[url] = original(url, ttl=FRESH)
        return raw[url]

    api.get_json = recording_get
    try:
        for name, args in ANALYZE_SCENARIOS.items():
            analyze.analyze(**args, today=today)
            print(f"{name}: ok", file=sys.stderr)
    finally:
        api.get_json = original
    head = {
        "today": today.isoformat(),
        "recorded": today.isoformat(),
        "scenarios": ANALYZE_SCENARIOS,
    }
    lines = [
        f"  {json.dumps(url)}: "
        f"{json.dumps(compact(url, body), ensure_ascii=False, separators=(',', ':'))}"
        for url, body in raw.items()
    ]
    text = json.dumps(head, ensure_ascii=False, indent=1)[:-2]
    text += ',\n "responses": {\n' + ",\n".join(lines) + "\n }\n}\n"
    json.loads(text)  # must stay valid JSON
    ANALYZE_FIXTURE.write_bytes(text.encode("utf-8"))
    print(f"{len(raw)} responses -> {ANALYZE_FIXTURE.name}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("what", choices=["languages", "resolve", "analyze"])
    args = parser.parse_args()
    if args.what == "languages":
        record_languages()
    elif args.what == "resolve":
        record_resolve()
    elif args.what == "analyze":
        record_analyze()


if __name__ == "__main__":
    main()
