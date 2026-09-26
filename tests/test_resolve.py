"""resolve rules on real API answers recorded by tests/fixtures/record.py (2026-09-25)."""

import datetime as dt
import json
from pathlib import Path

import pytest
import wds
from wds_lib import WdsError, api, langs, resolve
from wds_lib.resolve import Candidate

FIXTURES = Path(__file__).parent / "fixtures" / "resolve"
EN = langs.lookup("en")


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def replay(monkeypatch):
    """Serve recorded answers; any request not in the fixture fails loudly."""

    def use(name: str) -> dict:
        fixture = load(name)

        def get_json(url: str, *, ttl: float | None):
            if url not in fixture["responses"]:
                raise AssertionError(
                    f"request not in fixture {name}; re-record with "
                    f"`python tests/fixtures/record.py resolve`: {url}"
                )
            return fixture["responses"][url]

        monkeypatch.setattr(api, "get_json", get_json)
        return fixture

    return use


def run(replay, name: str, lang_codes: str) -> dict:
    fixture = replay(name)
    return resolve.resolve(fixture["query"], langs.parse_langs(lang_codes), EN)


def qids(result: dict) -> list[str]:
    return [c["qid"] for c in result["candidates"]]


@pytest.mark.parametrize("lang_codes", ["uk", "pl,cs,uk", "en"])
def test_astronomy_is_the_science_and_not_ambiguous(replay, lang_codes):
    result = run(replay, "astronomy", lang_codes)
    assert qids(result)[0] == "Q333"
    assert result["ambiguous"] is False


def test_intermittent_fasting_first_not_broader_fasting_and_pl_missing(replay):
    result = run(replay, "intermittent-fasting", "pl,cs")
    first = result["candidates"][0]
    assert first["qid"] == "Q1666254"
    assert first["sitelinks"] == {"pl": None, "cs": "Přerušovaný půst"}
    # "Fasting" has more language versions but must not win: article count is last.
    fasting = next(c for c in result["candidates"] if c["qid"] == "Q44602")
    assert fasting["sitelinks_total"] > first["sitelinks_total"]
    assert result["ambiguous"] is False


def test_learning_english_is_broad_not_ambiguous_and_skips_the_disambiguation(replay):
    result = run(replay, "learning-english", "pl,cs,uk")
    assert qids(result)[0] == "Q2731224"  # VOA "Learning English": exact title match
    assert result["ambiguous"] is False
    assert "Q16871974" not in qids(result)  # en "Learning English" disambiguation page


@pytest.mark.parametrize(
    ("name", "pair"), [("java", {"Q251", "Q3757"}), ("mercury", {"Q308", "Q925"})]
)
def test_java_and_mercury_are_ambiguous(replay, name, pair):
    result = run(replay, name, "pl,cs,uk")
    assert result["ambiguous"] is True
    assert pair <= set(qids(result))


@pytest.mark.parametrize(
    ("name", "disambiguation"),
    [("mercury", "Q48397"), ("astronomy", "Q3627653"), ("java", "Q110128680")],
)
def test_disambiguation_pages_never_become_candidates(replay, name, disambiguation):
    fixture = replay(name)
    found = resolve.collect_candidates(fixture["query"], EN)
    assert any(c.qid == disambiguation and c.disambiguation for c in found)
    assert disambiguation not in qids(run(replay, name, "pl,cs,uk"))


def test_sitelinks_total_counts_only_wikipedia_articles(replay):
    result = run(replay, "astronomy", "uk")
    astronomy = result["candidates"][0]
    # Q333 also links Commons and Abstract Wikipedia; neither is a language edition.
    assert astronomy["sitelinks_total"] == 252


def test_at_most_five_candidates_with_the_contract_fields(replay):
    for name in (
        "astronomy",
        "intermittent-fasting",
        "learning-english",
        "java",
        "mercury",
    ):
        result = run(replay, name, "pl,cs")
        assert result["status"] == "ok"
        assert 1 <= len(result["candidates"]) <= resolve.MAX_CANDIDATES
        for c in result["candidates"]:
            assert set(c) == {
                "qid",
                "label",
                "description",
                "sitelinks",
                "sitelinks_total",
                "found_via",
            }
            assert set(c["sitelinks"]) == {"pl", "cs"}
            assert c["found_via"] in {"wikidata", "fulltext", "both"}


def test_zero_article_items_are_gone_but_other_language_ones_stay(replay):
    for name in (
        "astronomy",
        "intermittent-fasting",
        "learning-english",
        "java",
        "mercury",
    ):
        result = run(replay, name, "uk")
        assert all(c["sitelinks_total"] > 0 for c in result["candidates"])
    astronomy = qids(run(replay, "astronomy", "uk"))
    assert "Q123958410" not in astronomy  # Conan Gray song, 0 articles
    assert "Q18889378" not in astronomy  # Munch painting, 0 articles
    assert "Q3232273" in astronomy  # the magazine: 9 articles, none in uk
    assert "Q112575736" not in qids(run(replay, "intermittent-fasting", "pl,cs"))


def test_found_via_reflects_which_search_found_the_item(replay):
    result = run(replay, "intermittent-fasting", "pl,cs")
    via = {c["qid"]: c["found_via"] for c in result["candidates"]}
    assert via["Q1666254"] == "both"
    assert via["Q44602"] == "fulltext"


# --- ranking and ambiguity on synthetic candidates -------------------------

PL, CS, UK = (langs.lookup(code) for code in ("pl", "cs", "uk"))


def cand(qid, names=(), wd=None, ft=None, sites=(), extra=1) -> Candidate:
    """extra: articles in languages nobody asked for (one by default, so the item
    is analysable somewhere and survives the zero-article filter)."""
    sitelinks = {site: "t" for site in sites} | {f"x{i}wiki": "t" for i in range(extra)}
    return Candidate(qid, wd, ft, names=set(names), sitelinks=sitelinks)


def order(candidates, query="topic", requested=(PL, CS)) -> list[str]:
    return [c.qid for c in resolve.rank_candidates(candidates, query, list(requested))]


def test_exact_name_beats_search_position():
    assert order([cand("A", wd=0, ft=0), cand("B", ["topic"], wd=9, ft=9)]) == [
        "B",
        "A",
    ]


def test_aliases_count_as_exact_and_case_is_ignored():
    assert order(
        [cand("A", wd=0), cand("B", ["the topic", "topic"], wd=5)], "  TOPIC "
    ) == ["B", "A"]


def test_position_beats_language_coverage():
    near = cand("A", ["topic"], wd=0, ft=1)
    covered = cand("B", ["topic"], wd=1, ft=2, sites=["plwiki", "cswiki"])
    assert order([covered, near]) == ["A", "B"]


def test_coverage_beats_article_count():
    covered = cand("A", ["topic"], wd=0, sites=["plwiki", "cswiki"])
    popular = cand("B", ["topic"], ft=0, extra=200)
    assert order([popular, covered]) == ["A", "B"]


def test_article_count_breaks_the_remaining_ties():
    assert order([cand("A", wd=0, extra=3), cand("B", ft=0, extra=30)]) == ["B", "A"]


def test_disambiguation_candidates_are_dropped():
    page = cand("D", ["topic"], wd=0)
    page.disambiguation = True
    assert order([page, cand("A", wd=1)]) == ["A"]


def test_items_without_any_wikipedia_article_are_dropped():
    nothing = cand("Z", ["topic"], wd=0, ft=0, extra=0)
    elsewhere = cand("E", wd=1, extra=5)  # articles, just not in pl/cs: stays
    assert order([nothing, elsewhere]) == ["E"]


def ambiguous(candidates, requested=(PL, CS)) -> bool:
    return resolve.is_ambiguous(candidates, "topic", list(requested))


def test_two_exact_meanings_of_similar_size_are_ambiguous():
    big = cand("A", ["topic"], sites=["plwiki"], extra=148)
    third = cand("B", ["topic"], sites=["cswiki"], extra=49)  # 50 >= 149/3
    assert ambiguous([big, third])


def test_a_small_namesake_is_not_ambiguous():
    big = cand("A", ["topic"], sites=["plwiki"], extra=148)
    small = cand("B", ["topic"], sites=["cswiki"], extra=48)  # 49 < 149/3
    assert not ambiguous([big, small])


def test_namesake_without_requested_languages_is_not_ambiguous():
    big = cand("A", ["topic"], sites=["plwiki"], extra=100)
    elsewhere = cand("B", ["topic"], extra=100)
    assert not ambiguous([big, elsewhere])


def test_non_exact_items_never_make_a_topic_ambiguous():
    big = cand("A", ["topic"], sites=["plwiki"], extra=100)
    related = cand("B", ["topic history"], sites=["plwiki"], extra=100)
    assert not ambiguous([big, related])


# --- first edit and redirects of an article ------------------------------------

# The live uk.wikipedia answer for 'Java' on 2026-09-26: 11 redirects, 4 of them to
# the section "Платформа".
JAVA_UK = {
    "continue": {"rvcontinue": "20040309211500|1331", "continue": "||redirects"},
    "query": {
        "pages": [
            {
                "ns": 0,
                "title": "Java",
                "revisions": [{"timestamp": "2004-03-06T17:51:33Z"}],
                "redirects": [
                    {"ns": 0, "title": "Мова програмування Java"},
                    {"ns": 0, "title": "Ява (мова програмування)"},
                    {"ns": 0, "title": "Java (мова програмування)"},
                    {"ns": 0, "title": "Платформа Java"},
                    {"ns": 0, "title": "JAVA"},
                    {"ns": 0, "title": "Java (programming language)"},
                    {
                        "ns": 0,
                        "title": "Java (платформа програмного забезпечення)",
                        "fragment": "Платформа",
                    },
                    {
                        "ns": 0,
                        "title": "Java (програмна платформа)",
                        "fragment": "Платформа",
                    },
                    {"ns": 0, "title": "Java (платформа)", "fragment": "Платформа"},
                    {"ns": 0, "title": "Java (Sun)", "fragment": "Платформа"},
                    {"ns": 0, "title": "Специфікація мови Java"},
                ],
            }
        ]
    },
}


def test_article_meta_counts_whole_article_redirects_first(monkeypatch):
    urls = []

    def get_json(url, *, ttl):
        urls.append(url)
        return JAVA_UK

    monkeypatch.setattr(api, "get_json", get_json)
    meta = resolve.article_meta(UK, "Java")
    assert meta.created == dt.date(2004, 3, 6)
    assert (meta.redirects_total, len(meta.redirects), meta.more_redirects) == (
        11,
        10,
        False,
    )
    # 7 redirects to the whole article in MediaWiki's order, then 3 of the 4
    # section ones: the last section redirect is left out.
    assert meta.redirects[6] == "Специфікація мови Java"
    assert "Java (Sun)" not in meta.redirects
    assert len(urls) == 1 and "rdnamespace=0" in urls[0]


def test_article_meta_notices_a_redirect_list_cut_by_mediawiki(monkeypatch):
    body = {
        "continue": {"rdcontinue": "123", "continue": "||revisions"},
        "query": {"pages": [{"title": "X", "redirects": [{"title": "Y"}]}]},
    }
    monkeypatch.setattr(api, "get_json", lambda url, *, ttl: body)
    meta = resolve.article_meta(UK, "X")
    assert meta.more_redirects and meta.created is None
    assert meta.redirects == ("Y",)


# --- requests, errors, CLI --------------------------------------------------


def test_search_lang_drives_both_searches(monkeypatch):
    urls = []

    def get_json(url, *, ttl):
        urls.append(url)
        return {}

    monkeypatch.setattr(api, "get_json", get_json)
    with pytest.raises(WdsError):
        resolve.resolve("астрономія", [UK], langs.lookup("uk"))
    assert any("wikidata.org" in u and "language=uk" in u for u in urls)
    assert any(u.startswith("https://uk.wikipedia.org/w/api.php?") for u in urls)


def test_nothing_found_is_an_error_with_a_hint(monkeypatch):
    monkeypatch.setattr(api, "get_json", lambda url, *, ttl: {})
    with pytest.raises(WdsError) as info:
        resolve.resolve("qqqzzz nothing", [PL], EN)
    assert "--search-lang" in info.value.hint


def test_cli_resolve_prints_the_json(replay, capsys):
    replay("intermittent-fasting")
    assert (
        wds.main(["resolve", "intermittent fasting", "--langs", "pl,cs"]) == wds.EXIT_OK
    )
    result = json.loads(capsys.readouterr().out)
    assert result["candidates"][0]["sitelinks"]["cs"] == "Přerušovaný půst"


def test_cli_rejects_unknown_language(capsys):
    assert wds.main(["resolve", "astronomy", "--langs", "polski"]) == wds.EXIT_ERROR
    result = json.loads(capsys.readouterr().out)
    assert result["error"] == "unknown language 'polski', did you mean 'pl'?"


@pytest.mark.live
def test_live_resolve_intermittent_fasting():
    result = resolve.resolve("intermittent fasting", [PL, CS], EN)
    first = result["candidates"][0]
    assert first["qid"] == "Q1666254"
    assert first["sitelinks"]["cs"] == "Přerušovaný půst"
