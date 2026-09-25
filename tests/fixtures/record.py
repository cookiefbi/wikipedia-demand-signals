"""Record live Wikimedia API data into the repository.

Everything tests and the language table rely on comes from real API answers,
never from hand-written guesses. Rerun to refresh:

    python tests/fixtures/record.py languages   # scripts/wds_lib/languages.json
    python tests/fixtures/record.py resolve     # tests/fixtures/resolve/*.json
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from wds_lib import api, langs, resolve

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("what", choices=["languages", "resolve"])
    args = parser.parse_args()
    if args.what == "languages":
        record_languages()
    elif args.what == "resolve":
        record_resolve()


if __name__ == "__main__":
    main()
