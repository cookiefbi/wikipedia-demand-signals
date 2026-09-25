"""Topic -> candidate Wikidata items with the article title in each requested language.

Three requests per query: Wikidata entity search, full-text search on the
search-language Wikipedia (one request, with page props), then one wbgetentities
for all candidates. Ranking and the "ambiguous" flag are decided here in code;
choosing the candidate that matches the user's meaning is left to the agent.
"""

from dataclasses import dataclass, field

from wds_lib import WdsError, api, langs
from wds_lib.langs import Lang

WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Results taken from each of the two searches; together they rarely exceed 20
# distinct items, well within wbgetentities' 50-id limit.
SEARCH_LIMIT = 10
MAX_CANDIDATES = 5

# P31 (instance of) = Wikimedia disambiguation page: a list of meanings, not a topic.
DISAMBIGUATION_ITEM = "Q4167410"

# A second meaning with at least a third of the Wikipedia articles of the first is
# a real alternative: Java island 149 vs programming language 123, Mercury planet
# 250 vs element 176. Astronomy (252) vs its namesakes (magazine 9, song 0) is not.
# Counts as recorded on 2026-09-25, see tests/fixtures/resolve/.
AMBIGUITY_RATIO = 1 / 3


@dataclass
class Candidate:
    qid: str
    wikidata_rank: int | None = None  # 0-based position in wbsearchentities
    fulltext_rank: int | None = None  # 0-based position in the full-text search
    disambiguation: bool = False
    label: str = ""
    description: str = ""
    names: set[str] = field(default_factory=set)  # normalized labels and aliases
    sitelinks: dict[str, str] = field(default_factory=dict)  # Wikipedia dbname -> title

    @property
    def found_via(self) -> str:
        if self.wikidata_rank is not None and self.fulltext_rank is not None:
            return "both"
        return "wikidata" if self.wikidata_rank is not None else "fulltext"

    @property
    def search_position(self) -> int:
        """Sum of ranks in both searches; missing from one counts as after its last result.

        Each search alone misleads: Wikidata puts the Ford brand first for "Mercury",
        full-text search puts the island first for "Java". An item near the top of
        both is the most likely meaning.
        """
        ranks = (self.wikidata_rank, self.fulltext_rank)
        return sum(SEARCH_LIMIT if rank is None else rank for rank in ranks)


def normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def wikipedia_sitelinks(entity: dict) -> dict[str, str]:
    """Wikipedia articles only: no Wikiquote, Commons, Wikisource and the like."""
    wikipedias = langs.wikipedia_dbnames()
    return {
        site: link["title"]
        for site, link in entity.get("sitelinks", {}).items()
        if site in wikipedias
    }


def search_wikidata(query: str, lang: Lang) -> list[str]:
    url = api.build_url(
        WIKIDATA_API,
        {
            "action": "wbsearchentities",
            "search": query,
            "language": lang.code,
            "uselang": lang.code,
            "type": "item",
            "limit": str(SEARCH_LIMIT),
            "format": "json",
        },
    )
    body = api.get_json(url, ttl=api.TTL_WEEK) or {}
    return [hit["id"] for hit in body.get("search", [])]


def search_fulltext(query: str, lang: Lang) -> list[tuple[str, bool]]:
    """(QID, is disambiguation page) in search order; pages without an item are skipped."""
    url = api.build_url(
        lang.api_url,
        {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrlimit": str(SEARCH_LIMIT),
            "gsrnamespace": "0",
            "prop": "pageprops",
            "ppprop": "wikibase_item|disambiguation",
            "format": "json",
            "formatversion": "2",
        },
    )
    body = api.get_json(url, ttl=api.TTL_WEEK) or {}
    pages = sorted(body.get("query", {}).get("pages", []), key=lambda p: p["index"])
    hits = []
    for page in pages:
        props = page.get("pageprops", {})
        if "wikibase_item" in props:
            hits.append((props["wikibase_item"], "disambiguation" in props))
    return hits


def fetch_entities(qids: list[str], lang: Lang) -> dict[str, dict]:
    """One wbgetentities call; keys are the requested ids (redirects resolved inside)."""
    languages = "en" if lang.code == "en" else f"en|{lang.code}"
    url = api.build_url(
        WIKIDATA_API,
        {
            "action": "wbgetentities",
            "ids": "|".join(qids),
            "props": "labels|descriptions|aliases|sitelinks|claims",
            "languages": languages,
            "format": "json",
        },
    )
    body = api.get_json(url, ttl=api.TTL_WEEK) or {}
    return {
        qid: entity
        for qid, entity in body.get("entities", {}).items()
        if "missing" not in entity
    }


def _text(values: dict, lang: Lang) -> str:
    for code in ("en", lang.code):
        if code in values:
            return values[code]["value"]
    return ""


def _instance_of(entity: dict) -> set[str]:
    classes = set()
    for claim in entity.get("claims", {}).get("P31", []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(value, dict) and "id" in value:
            classes.add(value["id"])
    return classes


def collect_candidates(query: str, search_lang: Lang) -> list[Candidate]:
    """Run both searches and fill every candidate from one wbgetentities call."""
    by_qid: dict[str, Candidate] = {}
    for rank, qid in enumerate(search_wikidata(query, search_lang)):
        by_qid.setdefault(qid, Candidate(qid)).wikidata_rank = rank
    for rank, (qid, disambiguation) in enumerate(search_fulltext(query, search_lang)):
        candidate = by_qid.setdefault(qid, Candidate(qid))
        candidate.fulltext_rank = rank
        candidate.disambiguation |= disambiguation
    if not by_qid:
        return []

    entities = fetch_entities(list(by_qid), search_lang)
    merged: dict[str, Candidate] = {}
    for qid, candidate in by_qid.items():
        entity = entities.get(qid)
        if entity is None:
            continue
        candidate.qid = entity["id"]  # a redirected id becomes its target
        candidate.label = _text(entity.get("labels", {}), search_lang)
        candidate.description = _text(entity.get("descriptions", {}), search_lang)
        candidate.disambiguation |= DISAMBIGUATION_ITEM in _instance_of(entity)
        candidate.sitelinks = wikipedia_sitelinks(entity)
        for code in {"en", search_lang.code}:
            if code in entity.get("labels", {}):
                candidate.names.add(normalize(entity["labels"][code]["value"]))
            for alias in entity.get("aliases", {}).get(code, []):
                candidate.names.add(normalize(alias["value"]))
        if candidate.qid in merged:  # two ids redirecting to one item
            _merge_ranks(merged[candidate.qid], candidate)
        else:
            merged[candidate.qid] = candidate
    return list(merged.values())


def _merge_ranks(kept: Candidate, other: Candidate) -> None:
    for attr in ("wikidata_rank", "fulltext_rank"):
        ranks = [
            r for r in (getattr(kept, attr), getattr(other, attr)) if r is not None
        ]
        setattr(kept, attr, min(ranks) if ranks else None)
    kept.disambiguation |= other.disambiguation


def coverage(candidate: Candidate, requested: list[Lang]) -> int:
    return sum(lang.dbname in candidate.sitelinks for lang in requested)


def rank_candidates(
    candidates: list[Candidate], query: str, requested: list[Lang]
) -> list[Candidate]:
    """Drop what cannot be analysed; order by exact name match, search position,
    coverage of the requested languages and, last, the number of Wikipedia articles.

    Dropped: disambiguation pages and items without any Wikipedia article (papers,
    clinical trials, paintings). Items with articles only in other languages stay;
    the coverage criterion already puts them lower.
    The article count comes last on purpose: otherwise the broad "Fasting" would
    outrank "Intermittent fasting".
    """
    wanted = normalize(query)
    topics = [c for c in candidates if not c.disambiguation and c.sitelinks]
    return sorted(
        topics,
        key=lambda c: (
            wanted not in c.names,
            c.search_position,
            -coverage(c, requested),
            -len(c.sitelinks),
        ),
    )


def is_ambiguous(
    candidates: list[Candidate], query: str, requested: list[Lang]
) -> bool:
    """Ask the user only when two items carry exactly the queried name, both have
    articles in the requested languages and neither dwarfs the other.

    A disambiguation page among search results is not a signal by itself:
    "learning English" has one but is a broad topic, not an ambiguous one.
    """
    wanted = normalize(query)
    exact = sorted(
        (
            len(c.sitelinks)
            for c in candidates
            if wanted in c.names and coverage(c, requested) > 0
        ),
        reverse=True,
    )
    return len(exact) >= 2 and exact[1] >= exact[0] * AMBIGUITY_RATIO


def resolve(query: str, requested: list[Lang], search_lang: Lang) -> dict:
    query = query.strip()
    if not query:
        raise WdsError(
            "empty topic", hint="pass the topic in English, e.g. 'astronomy'"
        )
    ranked = rank_candidates(collect_candidates(query, search_lang), query, requested)
    shown = ranked[:MAX_CANDIDATES]
    if not shown:
        raise WdsError(
            f"no Wikipedia topic found for '{query}'",
            hint="rephrase in English or use a broader term; for a topic that only "
            "exists in a local Wikipedia add --search-lang <code>",
        )
    return {
        "status": "ok",
        "query": query,
        "langs": [lang.code for lang in requested],
        "ambiguous": is_ambiguous(shown, query, requested),
        "candidates": [
            {
                "qid": c.qid,
                "label": c.label,
                "description": c.description,
                "sitelinks": {
                    lang.code: c.sitelinks.get(lang.dbname) for lang in requested
                },
                "sitelinks_total": len(c.sitelinks),
                "found_via": c.found_via,
            }
            for c in shown
        ],
    }
