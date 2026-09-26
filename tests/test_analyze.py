"""analyze on real API answers recorded by tests/fixtures/record.py (2026-09-26)."""

import csv
import datetime as dt
import json
import re
from pathlib import Path

import numpy as np
import pytest
import wds
from helpers import UrlNetwork
from wds_lib import WdsError, analyze, api, langs, pageviews, resolve, series, verdict

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "analyze.json").read_text(encoding="utf-8")
)
TODAY = dt.date.fromisoformat(FIXTURE["today"])
WINDOW = series.fetch_window(TODAY)


def expand(url: str, body):
    """Undo record.compact: pageviews come back in the API's own shape."""
    if body is None or not url.startswith(pageviews.PAGEVIEWS_API):
        return body
    return {"items": [{"timestamp": t, "views": v} for t, v in body["items"]]}


RESPONSES = {url: expand(url, body) for url, body in FIXTURE["responses"].items()}


@pytest.fixture
def replay(monkeypatch):
    def get_json(url: str, *, ttl: float | None):
        if url not in RESPONSES:
            raise AssertionError(
                "request not in the fixture; re-record with "
                f"`python tests/fixtures/record.py analyze`: {url}"
            )
        return RESPONSES[url]

    monkeypatch.setattr(api, "get_json", get_json)
    monkeypatch.setattr(analyze, "utc_today", lambda: TODAY)


def run(**args) -> dict:
    args.setdefault("articles", [])
    a = analyze.analyze(**args, today=TODAY)
    return analyze.to_json(a, files={})


def raw_sum(lang: str, title: str | None, first: str, last: str) -> int:
    """Views straight from the recorded API items, independent of series.py."""
    project = f"{lang}.wikipedia"
    lang_obj = langs.lookup(lang)
    url = (
        pageviews.article_url(lang_obj, title, WINDOW)
        if title
        else pageviews.edition_url(lang_obj, WINDOW)
    )
    assert project in url
    lo, hi = first.replace("-", ""), last.replace("-", "")
    return sum(
        item["views"]
        for item in RESPONSES[url]["items"]
        if lo <= item["timestamp"][:6] <= hi
    )


def raw_views(lang: str, titles: list[str], first: str, last: str) -> int:
    """An article plus its redirects, straight from the recorded API items."""
    return sum(raw_sum(lang, title, first, last) for title in titles)


def by_lang(result: dict) -> dict:
    return {r["lang"]: r for r in result["results"]}


# --- the task's examples ------------------------------------------------------


def test_intermittent_fasting_pl_is_no_article_cs_is_measured(replay):
    result = run(qids=["Q1666254"], langs_arg="pl,cs")
    pl, cs = by_lang(result)["pl"], by_lang(result)["cs"]
    assert pl == {"qid": "Q1666254", "lang": "pl", "status": "no_article"}
    assert cs["status"] == "ok" and cs["title"] == "Přerušovaný půst"
    assert cs["views_last_12m"] == raw_sum("cs", cs["title"], "2025-09", "2026-08")
    assert cs["views_prev_12m"] == raw_sum("cs", cs["title"], "2024-09", "2025-08")
    # no_article languages are reported in results but cannot be ranked
    assert [row["lang"] for row in result["ranking"]["order"]] == ["cs"]


def test_astronomy_uk_numbers_match_the_raw_api_answers(replay):
    result = run(qids=["Q333"], langs_arg="uk")
    uk = by_lang(result)["uk"]
    assert uk["title"] == "Астрономія"
    titles = ["Астрономія", "Astronomy"]  # the article and its only redirect
    last = raw_views("uk", titles, "2025-09", "2026-08")
    prev = raw_views("uk", titles, "2024-09", "2025-08")
    edition_last = raw_sum("uk", None, "2025-09", "2026-08")
    edition_prev = raw_sum("uk", None, "2024-09", "2025-08")
    assert (uk["views_last_12m"], uk["views_prev_12m"]) == (last, prev)
    assert uk["growth_pct"] == round((last - prev) / prev * 100, 1)
    assert uk["per_million_last_12m"] == round(last / edition_last * 1e6, 2)
    share_growth = (last / edition_last) / (prev / edition_prev) - 1
    assert uk["per_million_growth_pct"] == round(share_growth * 100, 1)


def test_default_period_is_24_months_to_the_last_full_month(replay):
    result = run(qids=["Q333"], langs_arg="uk")
    assert result["period"] == {"from": "2024-09", "to": "2026-08", "months": 24}


def test_every_language_gets_a_verdict_and_ranking_is_complete(replay):
    result = run(qids=["Q333"], langs_arg="pl,cs,uk")
    for r in result["results"]:
        assert r["trend"] in {"rising", "falling", "flat", "insufficient_data"}
        assert r["confidence"] in {"high", "medium", "low"}
        assert r["reasons"]
    ranked = [row["lang"] for row in result["ranking"]["order"]]
    assert sorted(ranked) == ["cs", "pl", "uk"]
    confidences = [by_lang(result)[lang]["confidence"] for lang in ranked]
    assert confidences == sorted(confidences, key=lambda c: c == "low")


def test_article_without_wikidata_route_is_measured_with_its_item(replay):
    result = run(
        qids=["Q1666254"], articles=["pl:Głodówka lecznicza"], langs_arg="pl,cs"
    )
    rows = result["results"]
    assert rows[0] == {"qid": "Q1666254", "lang": "pl", "status": "no_article"}
    article = rows[-1]
    assert (article["lang"], article["title"], article["qid"]) == (
        "pl",
        "Głodówka lecznicza",
        "Q352490",  # from the page's own Wikidata link
    )
    assert "topic_totals" not in result  # one article per language


def test_assumptions_and_caveats_are_always_there(replay):
    result = run(qids=["Q1666254"], langs_arg="pl,cs")
    text = " | ".join(result["assumptions"])
    assert "cs: topic measured by article 'Přerušovaný půst'" in text
    assert "agent=user" in text
    assert "2025-09..2026-08" in text and "2024-09..2025-08" in text
    assert any("language edition ≠ country" in c for c in result["caveats"])


def test_period_12m_uses_a_base_before_the_period(replay):
    result = run(qids=["Q333"], langs_arg="uk", period="12m")
    uk = by_lang(result)["uk"]
    assert result["period"]["months"] == 12
    titles = ["Астрономія", "Astronomy"]
    assert uk["views_prev_12m"] == raw_views("uk", titles, "2024-09", "2025-08")


# --- spikes and seasons on real series (checkpoint D) ---------------------------


def spike_months(row: dict) -> list[str]:
    prefix = "one-off spike in "
    return [
        r[len(prefix) : len(prefix) + 7] for r in row["reasons"] if r.startswith(prefix)
    ]


def test_uk_astronomy_september_is_the_school_season_not_a_spike(replay):
    """uk 'Астрономія' 2024-09: 4 687 views against ~1 700 around it, yet September
    2025 stands out from its own summer too: the season, which must not lower the
    confidence the way a one-off spike does."""
    uk = by_lang(run(qids=["Q333"], langs_arg="uk"))["uk"]
    assert spike_months(uk) == []
    assert (uk["trend"], uk["confidence"]) == ("falling", "high")
    assert uk["reasons"][1] == (
        "steady: 10 of 12 months are below the same month a year earlier "
        "(typical month -47.2%)"
    )


def test_pl_astronomy_november_2025_is_a_spike(replay):
    pl = by_lang(run(qids=["Q333"], langs_arg="pl"))["pl"]
    assert spike_months(pl) == ["2025-11"]
    assert pl["reasons"][1] == (
        "one-off spike in 2025-11: 2.8x the months around it, with no such peak in "
        "2024-11; it inflates the last 12 months, so growth looks higher"
    )
    # Everything else passes: the spike alone takes the confidence one step down.
    assert (pl["trend"], pl["confidence"]) == ("flat", "medium")


def test_charles_iii_accession_and_coronation_are_event_spikes(replay):
    result = run(
        qids=["Q43274"], langs_arg="pl,cs", date_from="2021-09", date_to="2023-08"
    )
    pl, cs = by_lang(result)["pl"], by_lang(result)["cs"]
    # 2022-09: Elizabeth II dies, Charles becomes king; 2023-05: the coronation.
    assert spike_months(pl) == spike_months(cs) == ["2022-09", "2023-05"]
    assert pl["trend"] == cs["trend"] == "rising"
    assert pl["confidence"] == cs["confidence"] == "low"
    # In pl the half year after the accession also stayed 3x higher.
    assert pl["warnings"][0].startswith(
        "sharp lasting change of level around 2022-09: views per million are 3.1x "
        "higher"
    )


# --- ranking on real series ----------------------------------------------------


def ranked(result: dict) -> list[str]:
    return [row["lang"] for row in result["ranking"]["order"]]


def why(result: dict) -> dict[str, str]:
    return {row["lang"]: row["why"] for row in result["ranking"]["order"]}


GROWTH_WHY = "views per million {:+.1f}% year over year, {confidence} confidence"
SHARE_WHY = (
    "{:.2f} views per million edition views in the last 12 months (trend: {trend}, "
    "{confidence} confidence)"
)
SIZE_WHY = "{:,} views in the last 12 months (trend: {trend}, {confidence} confidence)"


@pytest.mark.parametrize(
    ("by", "order", "field", "text"),
    [
        # all three fall; pl falls least (flat: its one-off spike props it up)
        ("growth", ["pl", "cs", "uk"], "per_million_growth_pct", GROWTH_WHY),
        # the smallest edition gives the topic the largest share of its views
        ("share", ["uk", "cs", "pl"], "per_million_last_12m", SHARE_WHY),
        # the largest edition gives it the most views
        ("size", ["pl", "uk", "cs"], "views_last_12m", SIZE_WHY),
    ],
)
def test_the_three_keys_rank_astronomy_three_ways(replay, by, order, field, text):
    """The same pl/cs/uk data, three different orders: the metric asked for decides
    the answer, so each why names its metric and the value from the JSON."""
    result = run(qids=["Q333"], langs_arg="pl,cs,uk", rank_by=by)
    assert result["ranking"]["by"] == by
    assert ranked(result) == order
    rows = by_lang(result)
    for lang in order:
        row = rows[lang]
        expected = text.format(row[field], **row)
        assert why(result)[lang] == expected


def test_trend_confidence_orders_growth_but_not_size_or_share(replay):
    """Up to 2023-10 uk had the most views and the largest share of pl/cs/uk; its
    trend is low only because of a spike in the base year. By growth that keeps
    it after the confident results; its 12-month number is sound, so by size and
    share it comes first."""
    result = run(qids=["Q333"], langs_arg="pl,cs,uk", date_to="2023-10")
    assert by_lang(result)["uk"]["confidence"] == "low"
    assert ranked(result) == ["cs", "pl", "uk"]
    assert why(result)["uk"].endswith("low confidence: listed after confident results")
    for by in ("size", "share"):
        result = run(qids=["Q333"], langs_arg="pl,cs,uk", date_to="2023-10", rank_by=by)
        field = verdict.RANK_FIELDS[by]
        rows = by_lang(result)
        assert ranked(result)[0] == "uk"
        assert rows["uk"][field] == max(r[field] for r in rows.values())
        assert "listed after" not in " ".join(why(result).values())
    assert why(result)["uk"] == (
        "33.54 views per million edition views in the last 12 months (trend: "
        "falling, low confidence)"
    )


def test_left_out_redirect_moves_the_largest_share_down(replay):
    """Charles III: cs has the larger share, but its 11th redirect is not counted,
    so the share itself may be too small or too large to compare."""
    result = run(qids=["Q43274"], langs_arg="pl,cs", rank_by="share")
    rows = by_lang(result)
    assert rows["cs"]["per_million_last_12m"] > rows["pl"]["per_million_last_12m"]
    assert ranked(result) == ["pl", "cs"]
    assert why(result)["cs"] == (
        "142.32 views per million edition views in the last 12 months (trend: flat, "
        "medium confidence); higher than pl, but not every redirect is counted, so "
        "views may be undercounted: listed after reliable numbers"
    )


def test_views_mostly_from_spikes_are_named_even_when_all_are_unreliable(replay):
    # Charles III's accession (2022-09) and coronation (2023-05): 74% of pl's and
    # 71% of cs's views in the 12 months to 2023-08.
    result = run(
        qids=["Q43274"],
        langs_arg="pl,cs",
        date_from="2021-09",
        date_to="2023-08",
        rank_by="size",
    )
    assert ranked(result) == ["pl", "cs"]  # by value: both numbers are unreliable
    spikes = (
        "more than half of these views came in one-off spike months: 2022-09, 2023-05"
    )
    assert why(result)["pl"] == (
        "1,904,394 views in the last 12 months (trend: rising, low confidence); "
        + spikes
    )
    assert result["must_say"][0].startswith(
        f"Ranked by audience size — pl: rising (low confidence), 1,904,394 views in "
        f"the last 12 months ({spikes}); "
    )


def test_unmeasured_growth_is_listed_last_with_its_reason(replay):
    # cs 'Přerušovaný půst' is younger than the growth base of 2020-09..2021-08.
    result = run(
        qids=["Q1666254"],
        articles=["pl:Głodówka lecznicza"],
        langs_arg="cs",
        date_from="2020-09",
        date_to="2022-08",
    )
    assert ranked(result) == ["pl", "cs"]
    assert why(result)["cs"] == (
        "growth of share not measured (see warnings): listed last"
    )


def test_when_all_results_are_low_the_values_decide(replay):
    result = run(
        qids=["Q1666254"], articles=["pl:Głodówka lecznicza"], langs_arg="pl,cs"
    )
    assert {r.get("confidence") for r in result["results"]} == {None, "low"}
    assert ranked(result) == ["pl", "cs"]
    assert why(result) == {
        "pl": "views per million -30.1% year over year, low confidence",
        "cs": "views per million -47.0% year over year, low confidence",
    }


# --- must_say on real series ---------------------------------------------------

LIMITS = (
    "A language edition is not a country (its readers live in many countries), and "
    "interest is not willingness to pay: views show curiosity, not demand for a "
    "product."
)
BOT_RULES = (
    "Wikimedia filters bots more strictly since 2025-03-20 and did not reprocess "
    "earlier data, so growth across that date partly reflects the rule change."
)
PROXY = (
    "One article (with its redirects) stands for the topic: related articles are "
    "not counted."
)

# Every scenario the fixture holds, with each ranking key.
MUST_SAY_CASES = [
    {"qids": ["Q333"], "langs_arg": "uk"},
    {"qids": ["Q333"], "langs_arg": "pl,cs,uk", "rank_by": "growth"},
    {"qids": ["Q333"], "langs_arg": "pl,cs,uk", "rank_by": "share"},
    {"qids": ["Q333"], "langs_arg": "pl,cs,uk", "rank_by": "size"},
    {"qids": ["Q333"], "langs_arg": "pl,cs,uk", "date_to": "2023-10"},
    {"qids": ["Q1666254"], "langs_arg": "pl,cs"},
    {"qids": ["Q1666254"], "articles": ["pl:Głodówka lecznicza"], "langs_arg": "pl,cs"},
    {
        "qids": ["Q1666254"],
        "articles": ["pl:Głodówka lecznicza"],
        "langs_arg": "cs",
        "date_from": "2020-09",
        "date_to": "2022-08",
    },
    {"qids": ["Q43274"], "langs_arg": "pl,cs", "rank_by": "size"},
    {"qids": ["Q43274"], "langs_arg": "pl,cs", "date_to": "2023-08"},
    {"qids": ["Q43274"], "langs_arg": "pl,cs", "date_to": "2023-08", "rank_by": "size"},
    {"qids": [], "articles": ["pl:Głodówka oczyszczająca"], "langs_arg": None},
]


def numbers(text: str) -> list[float]:
    """Numbers as written, sign aside; '17,175' is one number."""
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)
    return [float(token) for token in re.findall(r"\d+(?:\.\d+)?", text)]


def json_numbers(value) -> set[float]:
    """Every number in the JSON: its numeric fields and the numbers in its texts."""
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, int | float):
        return {abs(float(value))}
    if isinstance(value, str):
        return set(numbers(value))
    items = value.values() if isinstance(value, dict) else value
    return set().union(*(json_numbers(item) for item in items))


def test_must_say_for_one_language(replay):
    result = run(qids=["Q333"], langs_arg="uk")
    assert list(result)[:3] == ["status", "period", "must_say"]
    assert result["must_say"] == [
        "uk: falling, views per million -46.4% year over year.",
        "uk: high confidence — checks passed.",
        LIMITS,
        BOT_RULES,  # the growth base 2024-09..2025-08 straddles 2025-03-20
    ]


def test_must_say_for_several_languages_ranked_by_size(replay):
    result = run(qids=["Q333"], langs_arg="pl,cs,uk", rank_by="size")
    answer, trust, *rest = result["must_say"]
    assert answer == (
        "Ranked by audience size — pl: flat, 17,175 views in the last 12 months; "
        "uk: falling, 6,712 views in the last 12 months; cs: falling, 6,222 views "
        "in the last 12 months."
    )
    # equal levels with equal reasons are named together
    assert trust == (
        "pl: medium confidence — one-off spike in 2025-11: 2.8x the months around "
        "it, with no such peak in 2024-11; it inflates the last 12 months, so growth "
        "looks higher. uk, cs: high confidence — checks passed."
    )
    assert rest == [LIMITS, BOT_RULES]


def test_must_say_names_a_missing_article_and_low_confidence(replay):
    result = run(qids=["Q1666254"], langs_arg="pl,cs")
    assert result["must_say"] == [
        "cs: falling (low confidence), views per million -47.0% year over year.",
        (
            "cs: low confidence — median 232 views a month is below 300: "
            "percentages this small are mostly noise."
        ),
        (
            "pl: no article on this topic — little local coverage; interest there "
            "cannot be measured this way."
        ),
        LIMITS,
        BOT_RULES,
    ]


def test_must_say_gives_the_first_limiting_rule_not_the_note_on_signs(replay):
    """cs 'Karel III. Britský' is flat, but absolute views fell while its share
    grew: reasons[1] explains that, and the rule that kept the confidence at
    medium comes after it. must_say gives the rule, with the warning it points to
    rather than "(see warnings)"."""
    result = run(qids=["Q43274"], langs_arg="pl,cs")
    cs = by_lang(result)["cs"]
    assert cs["trend"] == "flat"
    assert cs["reasons"][1].startswith("absolute views fell 7.3% while the whole")
    assert cs["reasons"][2].startswith("not every redirect is counted (see warnings)")
    assert result["must_say"][1].endswith(
        "cs: medium confidence — " + cs["warnings"][0] + "."
    )


def test_insufficient_growth_gives_what_blocked_it(replay):
    result = run(
        qids=["Q1666254"],
        articles=["pl:Głodówka lecznicza"],
        langs_arg="cs",
        date_from="2020-09",
        date_to="2022-08",
    )
    answer, trust = result["must_say"][:2]
    assert answer.endswith("; cs: insufficient data (low confidence).")
    assert trust.endswith(
        "cs: low confidence — article created 2020-10-28: the 12 months before the "
        "last 12 are incomplete, growth not computed."
    )


@pytest.mark.parametrize(
    ("dates", "caveat"),
    [
        ({"date_from": "2022-09", "date_to": "2024-08"}, PROXY),
        ({"date_to": "2025-02"}, PROXY),  # the last month ends before 2025-03-20
        ({"date_to": "2025-03"}, BOT_RULES),
        ({}, BOT_RULES),
    ],
)
def test_bot_rules_caveat_only_when_growth_straddles_the_change(replay, dates, caveat):
    result = run(qids=["Q333"], langs_arg="uk", **dates)
    assert result["must_say"][-1] == caveat
    assert BOT_RULES not in result["must_say"][:-1]


@pytest.mark.parametrize("args", MUST_SAY_CASES)
def test_must_say_has_three_to_five_points(replay, args):
    points = run(**args)["must_say"]
    assert 3 <= len(points) <= 5
    assert LIMITS in points


@pytest.mark.parametrize("args", MUST_SAY_CASES)
def test_must_say_numbers_come_from_the_json(replay, args):
    result = run(**args)
    rest = json_numbers({k: v for k, v in result.items() if k != "must_say"})
    for point in result["must_say"]:
        assert set(numbers(point)) <= rest, point
    # The main point: the value each language is ranked by, in ranking order.
    rows = {r["lang"]: r for r in result["results"] if r["status"] == "ok"}
    field = verdict.RANK_FIELDS[result["ranking"]["by"]]
    values = [rows[lang][field] for lang in ranked(result)]
    # without the metric's "last 12 months" and the reasons in parentheses
    main = result["must_say"][0].replace("the last 12 months", "")
    main = re.sub(r"\([^)]*\)", "", main)
    assert numbers(main) == [abs(v) for v in values if v is not None]


# --- must_say in Ukrainian (--answer-lang uk) -----------------------------------


def uk_numbers(text: str) -> set[float]:
    """Numbers as a Ukrainian text writes them: '1 904 394', '-46,4 %'."""
    text = re.sub(r"(?<=\d) (?=\d{3}(?!\d))", "", text)
    return set(numbers(re.sub(r"(?<=\d),(?=\d)", ".", text)))


def test_must_say_in_ukrainian_for_one_language(replay):
    a = analyze.analyze(qids=["Q333"], articles=[], langs_arg="uk", today=TODAY)
    result = analyze.to_json(a, files={}, lang="uk")
    assert result["must_say"] == [
        "uk: спадає, перегляди на мільйон -46,4 % рік до року.",
        "uk: висока довіра — перевірки пройдено.",
        (
            "Мовний розділ — не країна (його читачі живуть у багатьох країнах), а "
            "інтерес — не готовність платити: перегляди показують цікавість, а не "
            "попит на продукт."
        ),
        (
            "З 2025-03-20 Вікімедіа фільтрує ботів суворіше, а раніші дані не "
            "перераховано, тож порівняння через цю дату частково відображає зміну "
            "правил."
        ),
    ]
    # the rest of the JSON is data for the agent and stays English
    english = analyze.to_json(a, files={})
    assert {k: v for k, v in result.items() if k != "must_say"} == {
        k: v for k, v in english.items() if k != "must_say"
    }


@pytest.mark.parametrize("args", MUST_SAY_CASES)
def test_ukrainian_must_say_has_the_same_points_and_numbers(replay, args):
    a = analyze.analyze(**{"articles": []} | args, today=TODAY)
    english, ukrainian = analyze.must_say(a, "en"), analyze.must_say(a, "uk")
    assert len(ukrainian) == len(english)
    for en, uk in zip(english, ukrainian, strict=True):
        # "the 12 months before the last 12" names 12 once in Ukrainian: sets
        assert uk_numbers(uk) == set(numbers(en)), uk
        assert not re.search(r"\d\.\d", uk), uk  # a decimal comma, never a point


# --- redirects and the first edit ----------------------------------------------


def recorded_redirects(lang: str, title: str) -> list[str]:
    """Redirects in the recorded MediaWiki answer, in MediaWiki's own order."""
    for url, body in RESPONSES.items():
        if f"//{lang}.wikipedia.org/" in url and "prop=redirects" in url:
            page = body["query"]["pages"][0]
            if page["title"] == title:
                return [r["title"] for r in page.get("redirects", [])]
    raise AssertionError(f"no recorded redirects for {lang}:{title}")


def test_renamed_article_keeps_its_views_under_the_old_title(replay, tmp_path):
    """'Karol III' was 'Karol (książę Walii)' until 2022-09-08: the growth base
    2021-09..2022-08 sits almost entirely under redirects."""
    a = analyze.analyze(
        qids=["Q43274"],
        articles=[],
        langs_arg="pl",
        date_from="2021-09",
        date_to="2023-08",
        today=TODAY,
    )
    pl = by_lang(analyze.to_json(a, files={}))["pl"]
    redirects = recorded_redirects("pl", "Karol III")
    assert len(redirects) == 9  # under the cap: all counted
    titles = ["Karol III", *redirects]
    prev = raw_views("pl", titles, "2021-09", "2022-08")
    last = raw_views("pl", titles, "2022-09", "2023-08")
    assert (pl["views_prev_12m"], pl["views_last_12m"]) == (prev, last)
    assert pl["growth_pct"] == round((last - prev) / prev * 100, 1)
    # Under its own title the article had almost nothing before the rename:
    # growth would read as hundreds of thousands of percent.
    assert raw_sum("pl", "Karol III", "2021-09", "2022-08") < prev / 1000

    path = tmp_path / "data.csv"
    analyze.write_csv(a, path)
    with open(path, encoding="utf-8-sig", newline="") as f:
        base = list(csv.DictReader(f))[:12]  # 2021-09..2022-08
    article = sum(int(row["article_views"]) for row in base)
    through_redirects = sum(int(row["redirect_views"]) for row in base)
    assert article == raw_sum("pl", "Karol III", "2021-09", "2022-08")
    assert through_redirects == raw_views("pl", redirects, "2021-09", "2022-08")
    assert all(
        int(row["article_views"]) + int(row["redirect_views"]) == int(row["views"])
        for row in base
    )


def test_more_than_ten_redirects_counts_ten_and_warns(replay):
    result = run(qids=["Q43274"], langs_arg="cs")
    cs = by_lang(result)["cs"]
    redirects = recorded_redirects("cs", "Karel III. Britský")
    assert len(redirects) == 11 and redirects[-1] == "Charles III."
    # No section redirects here, so MediaWiki's order decides: the newest is left out.
    counted = ["Karel III. Britský", *redirects[:10]]
    assert cs["views_last_12m"] == raw_views("cs", counted, "2025-09", "2026-08")
    assert cs["warnings"] == [
        (
            "'Karel III. Britský': 11 redirects lead to it, only 10 are counted "
            "(redirects to the whole article first, oldest first): views may be "
            "undercounted"
        )
    ]
    assert cs["growth_pct"] is not None  # a warning about the data, not a blocker
    assert (
        "cs: topic measured by article 'Karel III. Britský' (+10 redirects)"
        in result["assumptions"]
    )


def test_article_given_by_a_redirect_title_is_measured_as_its_target(replay):
    result = run(qids=[], articles=["pl:Głodówka oczyszczająca"], langs_arg=None)
    [row] = result["results"]
    assert (row["title"], row["qid"]) == ("Głodówka lecznicza", "Q352490")
    # The given title is counted once, as the article's redirect.
    titles = ["Głodówka lecznicza", "Głodówka oczyszczająca"]
    assert row["views_last_12m"] == raw_views("pl", titles, "2025-09", "2026-08")
    assert (
        "pl: topic measured by article 'Głodówka lecznicza' (+1 redirect)"
        in result["assumptions"]
    )


def test_young_article_real_case_starts_at_its_first_edit(replay):
    # cs 'Přerušovaný půst' was first edited on 2020-10-28: a base year of
    # 2020-09..2021-08 is incomplete.
    result = run(
        qids=["Q1666254"], langs_arg="cs", date_from="2020-09", date_to="2022-08"
    )
    cs = by_lang(result)["cs"]
    assert cs["growth_pct"] is None and cs["per_million_growth_pct"] is None
    assert cs["warnings"][0].startswith("article created 2020-10-28: ")
    assert (
        "cs: topic measured by article 'Přerušovaný půst', created 2020-10-28: "
        "earlier days are not counted" in result["assumptions"]
    )


def analyze_young_article(monkeypatch) -> analyze.Analysis:
    """Synthetic: 'New' first edited on 2025-01-10. Its redirect 'Old' has views
    all along: before that day the title led somewhere else."""
    monkeypatch.setattr(
        resolve,
        "sitelink_titles",
        lambda qids: {"Q1": {"label": "new", "titles": {"cswiki": "New"}}},
    )
    monkeypatch.setattr(
        resolve,
        "article_meta",
        lambda lang, title: resolve.ArticleMeta(dt.date(2025, 1, 10), ("Old",), 1),
    )
    monkeypatch.setattr(
        pageviews, "published_window", lambda today, lang: (WINDOW, False)
    )
    days = (WINDOW.end - WINDOW.start).days + 1
    monkeypatch.setattr(
        pageviews, "edition_daily", lambda lang, w: np.full(days, 1_000_000)
    )
    level = {"New": 100, "Old": 50}
    monkeypatch.setattr(
        pageviews, "article_daily", lambda lang, title, w: np.full(days, level[title])
    )
    return analyze.analyze(qids=["Q1"], articles=[], langs_arg="cs", today=TODAY)


def test_views_before_the_first_edit_are_dropped_for_redirects_too(monkeypatch):
    a = analyze_young_article(monkeypatch)
    [m] = a.measured.values()
    january = series.parse_month("2025-01", "-") - a.span.first
    assert m.data.views[:january].sum() == 0
    assert m.data.views[january] == 22 * (100 + 50)  # 10..31 January
    assert m.data.redirects[january] == 22 * 50
    [row] = analyze.to_json(a, files={})["results"]
    assert row["growth_pct"] is None
    assert row["warnings"][0].startswith("article created 2025-01-10: ")


# --- several articles in one language ----------------------------------------


def test_two_articles_in_one_language_are_summed_into_topic_totals(monkeypatch):
    # Synthetic: two items with articles in cs; the topic is their sum.
    monkeypatch.setattr(
        resolve,
        "sitelink_titles",
        lambda qids: {
            "Q1": {"label": "a", "titles": {"cswiki": "A"}},
            "Q2": {"label": "b", "titles": {"cswiki": "B"}},
        },
    )
    monkeypatch.setattr(
        pageviews, "published_window", lambda today, lang: (WINDOW, False)
    )
    monkeypatch.setattr(
        resolve, "article_meta", lambda lang, title: resolve.ArticleMeta(None, (), 0)
    )
    days = (WINDOW.end - WINDOW.start).days + 1
    monkeypatch.setattr(
        pageviews, "edition_daily", lambda lang, w: np.full(days, 1_000_000)
    )
    level = {"A": 100, "B": 50}
    monkeypatch.setattr(
        pageviews, "article_daily", lambda lang, title, w: np.full(days, level[title])
    )
    result = run(qids=["Q1", "Q2"], langs_arg="cs")
    [total] = result["topic_totals"]
    assert total["lang"] == "cs" and total["articles"] == 2
    a, b = result["results"]
    assert total["views_last_12m"] == a["views_last_12m"] + b["views_last_12m"]
    assert "cs: topic = sum of 2 articles (topic_totals)" in result["assumptions"]
    # Without the bot caveat, must_say names the articles, not one article.
    earlier = run(qids=["Q1", "Q2"], langs_arg="cs", date_to="2024-08")
    assert earlier["must_say"][-1] == (
        "The chosen articles (with their redirects) stand for the topic: related "
        "articles are not counted."
    )


def test_must_say_when_no_language_has_an_article(monkeypatch):
    monkeypatch.setattr(
        resolve,
        "sitelink_titles",
        lambda qids: {"Q1": {"label": "a", "titles": {"enwiki": "A"}}},
    )
    result = run(qids=["Q1"], langs_arg="pl,cs")
    assert result["ranking"]["order"] == []
    assert result["must_say"] == [
        (
            "pl, cs: no article on this topic — little local coverage; interest "
            "there cannot be measured this way."
        ),
        LIMITS,
        BOT_RULES,
    ]


# --- argument errors ---------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "in_error"),
    [
        ({"qids": ["1666254"]}, "not a Wikidata item id"),
        ({"qids": ["Q1"], "langs_arg": None}, "--langs is required with --qid"),
        (
            {
                "qids": [],
                "articles": ["sv:A"],
                "langs_arg": "pl,cs,uk,sk,de,fr,es,it,hu,ro",
            },
            "11 languages",
        ),
        ({"qids": [], "articles": ["Půst"]}, "must look like 'pl:Title'"),
        ({"qids": []}, "nothing to analyze"),
        (
            {"qids": ["Q1"], "period": "24m", "date_from": "2025-01"},
            "cannot be combined",
        ),
    ],
)
def test_argument_errors_come_before_any_request(monkeypatch, args, in_error):
    def no_network(url, *, ttl):
        raise AssertionError(f"unexpected request {url}")

    monkeypatch.setattr(api, "get_json", no_network)
    args = {"articles": [], "langs_arg": "pl"} | args
    with pytest.raises(WdsError) as info:
        analyze.analyze(**args, today=TODAY)
    assert in_error in info.value.error


@pytest.mark.parametrize(
    ("note", "rejected"),
    [
        ("cs grew 22% a year", "'22%'"),
        ("since 2024 the share fell", "'2024'"),
        ("about 3.5 times more", "'3.5'"),
        ("частка зросла на 22 % (+15%),", "'22', '(+15%),'"),
    ],
)
def test_note_with_a_number_is_rejected_before_any_request(
    monkeypatch, tmp_path, note, rejected
):
    def no_network(url, *, ttl):
        raise AssertionError(f"unexpected request {url}")

    monkeypatch.setattr(api, "get_json", no_network)
    args = {"qids": ["Q333"], "articles": [], "langs_arg": "uk"}
    with pytest.raises(WdsError) as info:
        analyze.run(args, str(tmp_path), report=True, note=note)
    assert info.value.error == f"--note must not contain numbers: {rejected}"
    assert "in words" in info.value.hint


@pytest.mark.parametrize(
    "note", ["Start with B2C readers in cs", "Interest since COVID-19 — ask why", ""]
)
def test_note_words_with_letters_pass(note):
    analyze.check_note(note)


def test_article_language_is_added_and_said_in_the_assumptions(replay):
    result = run(qids=["Q1666254"], articles=["pl:Głodówka lecznicza"], langs_arg="cs")
    rows = [(r["qid"], r["lang"], r["status"]) for r in result["results"]]
    assert rows == [
        ("Q1666254", "cs", "ok"),
        ("Q1666254", "pl", "no_article"),  # the item is checked in pl as well
        ("Q352490", "pl", "ok"),
    ]
    assert result["assumptions"][0] == (
        "pl added to the compared languages for --article 'pl:Głodówka lecznicza'"
    )


def test_article_alone_needs_no_langs(replay):
    result = run(qids=[], articles=["pl:Głodówka lecznicza"], langs_arg=None)
    assert [(r["lang"], r["title"]) for r in result["results"]] == [
        ("pl", "Głodówka lecznicza")
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("intermittent fasting", "intermittent-fasting"),
        ("Głodówka lecznicza", "glodowka-lecznicza"),
        ("Přerušovaný půst", "prerusovany-pust"),
        ("Астрономія", ""),
        ("AC/DC: Back in Black", "ac-dc-back-in-black"),
    ],
)
def test_folder_names_are_ascii(text, expected):
    assert analyze.slug(text) == expected


def test_qids_can_be_repeated_or_comma_separated():
    assert analyze.parse_qids(["Q1, q2", "Q3", "Q1"]) == ["Q1", "Q2", "Q3"]


def test_unknown_qid_is_an_error_with_a_hint(monkeypatch):
    monkeypatch.setattr(
        api, "get_json", lambda url, *, ttl: {"entities": {"Q9": {"missing": ""}}}
    )
    with pytest.raises(WdsError) as info:
        analyze.analyze(qids=["Q9"], articles=[], langs_arg="pl", today=TODAY)
    assert "Q9" in info.value.error and "resolve" in info.value.hint


def test_missing_article_title_is_an_error_not_zero_interest(monkeypatch):
    monkeypatch.setattr(
        api,
        "get_json",
        lambda url, *, ttl: {"query": {"pages": [{"title": "Xyz", "missing": True}]}},
    )
    with pytest.raises(WdsError) as info:
        analyze.analyze(qids=[], articles=["pl:Xyz"], langs_arg="pl", today=TODAY)
    assert "no article 'Xyz' in pl.wikipedia" in info.value.error


# --- CLI and files -------------------------------------------------------------


def cli(*args: str) -> int:
    return wds.main(["analyze", *args])


def test_cli_prints_json_and_writes_the_same_result_json_and_csv(
    replay, tmp_path, capsys
):
    out = tmp_path / "out dir"
    assert cli("--qid", "Q1666254", "--langs", "pl,cs", "--out", str(out)) == 0
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert printed == saved
    assert printed["files"] == {
        "result_json": str(out / "result.json"),
        "data_csv": str(out / "data.csv"),
    }
    with open(out / "data.csv", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert {row["title"] for row in rows} == {"Přerušovaný půst"}
    assert len(rows) == 24  # 24m period; its growth base lies inside it
    assert {row["redirect_views"] for row in rows} == {"0"}  # it has no redirects
    cs = by_lang(printed)["cs"]
    last_12 = sum(int(row["views"]) for row in rows[-12:])
    assert last_12 == cs["views_last_12m"]


def test_default_folder_is_per_question_with_last_result(
    replay, tmp_path, capsys, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    assert cli("--qid", "Q1666254", "--langs", "pl,cs") == 0
    assert cli("--qid", "Q333", "--langs", "uk", "--period", "12m") == 0
    root = tmp_path / "wds-output"
    fasting = root / "intermittent-fasting_pl-cs_24m"
    astronomy = root / "astronomy_uk_12m"
    assert (fasting / "data.csv").exists() and (astronomy / "data.csv").exists()
    printed = json.loads(capsys.readouterr().out.split("\n}\n")[1] + "\n}")
    assert printed["files"]["result_json"] == str(astronomy / "result.json")
    last = json.loads((root / "last-result.json").read_text(encoding="utf-8"))
    assert last == printed  # the latest run, at a fixed ASCII path

    assert cli("--qid", "Q333", "--langs", "uk", "--to", "2030-01") == wds.EXIT_ERROR
    last = json.loads((root / "last-result.json").read_text(encoding="utf-8"))
    assert last["status"] == "error"


def test_from_to_folder_names_the_months(replay, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli("--qid", "Q333", "--langs", "uk", "--from", "2023-01") == 0
    files = json.loads(capsys.readouterr().out)["files"]
    assert "astronomy_uk_2023-01-2026-08" in files["data_csv"]


def test_cli_error_replaces_a_stale_result_json(replay, tmp_path, capsys):
    out = tmp_path / "out"
    cli("--qid", "Q333", "--langs", "uk", "--out", str(out))
    assert cli("--qid", "Q333", "--langs", "uk", "--to", "2030-01", "--out", str(out))
    saved = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert saved["status"] == "error" and "not complete yet" in saved["error"]


def test_rerun_is_served_from_the_cache(clock, network, tmp_path, capsys, monkeypatch):
    """Criterion: a repeated question makes no network requests (see stderr)."""
    monkeypatch.setattr(analyze, "utc_today", lambda: TODAY)
    fake = network(UrlNetwork(RESPONSES))
    args = ("--qid", "Q1666254", "--langs", "pl,cs", "--out", str(tmp_path))
    assert cli(*args) == 0
    first = capsys.readouterr()
    # sitelinks, cs edition, cs first edit + redirects (none), cs article
    assert "requests: 4 network" in first.err
    # before the downloads: 1 edition + 2 requests, plus up to 10 redirects
    assert "first run: 1 article(s), 1 edition(s) to download, about 2-8 s" in first.err
    assert cli(*args) == 0
    second = capsys.readouterr()
    assert "requests: 0 network" in second.err
    assert "first run" not in second.err
    assert json.loads(second.out)["results"] == json.loads(first.out)["results"]
    assert len(fake.urls) == 4


def test_first_run_estimate_says_when_it_may_outlast_a_bash_call(clock):
    # clock: an empty cache in tmp_path
    found = [
        analyze.Target(langs.lookup(code), f"Q{i}", f"Title {i}")
        for code in ["pl", "cs", "uk", "sk", "de", "fr", "es", "it", "hu", "ro"]
        for i in range(3)
    ]
    text = analyze.download_estimate(found, WINDOW)
    # 10 editions + 30 articles x 2 = 70 requests; up to 370 with 10 redirects each
    assert "30 article(s), 10 edition(s) to download, about 46-240 s" in text
    assert "if the command times out, rerun it" in text
    small = analyze.download_estimate(found[:2], WINDOW)
    assert "times out" not in small
