"""Record live Wikimedia API data into the repository.

Everything tests and the language table rely on comes from real API answers,
never from hand-written guesses. Rerun to refresh:

    python tests/fixtures/record.py languages   # scripts/wds_lib/languages.json
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from wds_lib import api

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("what", choices=["languages"])
    args = parser.parse_args()
    if args.what == "languages":
        record_languages()


if __name__ == "__main__":
    main()
