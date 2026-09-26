"""Topic -> candidate Wikidata items with the article title in each requested language.

Three requests per query: Wikidata entity search, full-text search on the
search-language Wikipedia (one request, with page props), then one wbgetentities
for all candidates. Ranking and the "ambiguous" flag are decided here in code;
choosing the candidate that matches the user's meaning is left to the agent.
"""

import datetime as dt
from dataclasses import dataclass, field

from wds_lib import WdsError, api, langs
from wds_lib.langs import Lang

WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Redirects whose views are added to an article: each costs one pageviews request,
# so a cap keeps the first run of 3 languages x 2 articles within ~90 s (SPEC 9).
MAX_REDIRECTS = 10

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
# Meanings named in the clarifying question: more than three is a list, not a question.
MAX_MEANINGS_ASKED = 3


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
    exact = sorted(
        (len(c.sitelinks) for c in meanings(candidates, query, requested)),
        reverse=True,
    )
    return len(exact) >= 2 and exact[1] >= exact[0] * AMBIGUITY_RATIO


def meanings(
    candidates: list[Candidate], query: str, requested: list[Lang]
) -> list[Candidate]:
    """Items that carry exactly the queried name and have an article to compare."""
    wanted = normalize(query)
    return [c for c in candidates if wanted in c.names and coverage(c, requested) > 0]


def clarifying_question(
    candidates: list[Candidate], query: str, requested: list[Lang]
) -> str:
    """The one question for an ambiguous name, made of the meanings' descriptions."""
    options = [
        c.description or f"{c.label} ({c.qid})"
        for c in meanings(candidates, query, requested)[:MAX_MEANINGS_ASKED]
    ]
    listed = ", ".join(options[:-1]) + " or " + options[-1]
    return f'Which "{query}" do you mean: {listed}?'


def sitelink_titles(qids: list[str]) -> dict[str, dict]:
    """{qid: {"label": English label, "titles": {dbname: article title}}}.

    One request for all items, without a site filter: the cached answer then also
    serves "add Slovak" without asking Wikidata again.
    """
    url = api.build_url(
        WIKIDATA_API,
        {
            "action": "wbgetentities",
            "ids": "|".join(sorted(qids)),
            "props": "labels|sitelinks",
            "languages": "en",
            "format": "json",
        },
    )
    body = api.get_json(url, ttl=api.TTL_WEEK) or {}
    entities = body.get("entities", {})
    unknown = [q for q in qids if q not in entities or "missing" in entities[q]]
    if unknown:
        raise WdsError(
            f"unknown Wikidata item {', '.join(unknown)}",
            hint="copy the qid from the `resolve` output, e.g. --qid Q1666254",
        )
    return {
        qid: {
            "label": entities[qid].get("labels", {}).get("en", {}).get("value", ""),
            "titles": wikipedia_sitelinks(entities[qid]),
        }
        for qid in qids
    }


def lookup_article(lang: Lang, title: str) -> tuple[str, str | None]:
    """(canonical title, Wikidata item or None) of an existing article.

    Needed because the Pageviews API answers 404 both for "no views" and for "no
    such page": a typo would silently read as zero interest. Redirects are followed
    to the article they point to.
    """
    url = api.build_url(
        lang.api_url,
        {
            "action": "query",
            "titles": title,
            "redirects": "1",
            "prop": "pageprops",
            "ppprop": "wikibase_item|disambiguation",
            "format": "json",
            "formatversion": "2",
        },
    )
    body = api.get_json(url, ttl=api.TTL_WEEK) or {}
    pages = body.get("query", {}).get("pages", [])
    page = pages[0] if pages else {"missing": True}
    where = f"{lang.code}.wikipedia"
    if page.get("missing") or page.get("invalid"):
        raise WdsError(
            f"no article '{title}' in {where}",
            hint=f"check the exact title, e.g. with `resolve <topic> --langs {lang.code} "
            f"--search-lang {lang.code}`",
        )
    if page.get("ns") != 0:
        raise WdsError(
            f"'{page['title']}' in {where} is not an article",
            hint="pass an article title without a namespace prefix",
        )
    props = page.get("pageprops", {})
    if "disambiguation" in props:
        raise WdsError(
            f"'{page['title']}' in {where} is a disambiguation page",
            hint="pick the article for the meaning you want and pass that title",
        )
    return page["title"], props.get("wikibase_item")


@dataclass(frozen=True)
class ArticleMeta:
    created: dt.date | None  # day of the first edit; None if MediaWiki has no page
    redirects: tuple[str, ...]  # the ones whose views are counted, at most 10
    redirects_total: int  # all article-namespace redirects to the article
    more_redirects: bool = False  # MediaWiki had even more than it listed


def article_meta(lang: Lang, title: str) -> ArticleMeta:
    """First edit and redirects of an existing article, in one MediaWiki request.

    The first edit is when the article appeared: a renamed article keeps its
    history, so the date is the original creation. Redirects are the article's
    other and former titles; before a rename its views were recorded under the
    old title, so without them a renamed article looks like a new one.
    With more than MAX_REDIRECTS, redirects to the whole article go first (a former
    title never points to a section), then MediaWiki's order, oldest first.
    Ranking by views instead would cost 2-3 more requests per article.
    """
    url = api.build_url(
        lang.api_url,
        {
            "action": "query",
            "titles": title,
            "prop": "redirects|revisions",
            "rdnamespace": "0",
            "rdlimit": "max",
            "rdprop": "title|fragment",
            "rvdir": "newer",
            "rvlimit": "1",
            "rvprop": "timestamp",
            "format": "json",
            "formatversion": "2",
        },
    )
    body = api.get_json(url, ttl=api.TTL_WEEK) or {}
    pages = body.get("query", {}).get("pages", [])
    page = pages[0] if pages else {}
    revisions = page.get("revisions", [])
    created = (
        dt.date.fromisoformat(revisions[0]["timestamp"][:10]) if revisions else None
    )
    redirects = page.get("redirects", [])
    chosen = sorted(redirects, key=lambda r: "fragment" in r)[:MAX_REDIRECTS]
    return ArticleMeta(
        created=created,
        redirects=tuple(r["title"] for r in chosen),
        redirects_total=len(redirects),
        more_redirects="rdcontinue" in body.get("continue", {}),
    )


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
    ambiguous = is_ambiguous(shown, query, requested)
    # A status that is not "ok" is harder to read past than a flag inside one (T16).
    head: dict = {
        "status": "needs_clarification" if ambiguous else "ok",
        "query": query,
        "langs": [lang.code for lang in requested],
        "ambiguous": ambiguous,
    }
    if ambiguous:
        head["ask_user"] = clarifying_question(shown, query, requested)
    return head | {
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
