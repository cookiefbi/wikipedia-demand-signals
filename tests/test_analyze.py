"""analyze on real API answers recorded by tests/fixtures/record.py (2026-09-25)."""

import csv
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pytest
import wds
from helpers import UrlNetwork
from wds_lib import WdsError, analyze, api, langs, pageviews, resolve, series

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
    last = raw_sum("uk", "Астрономія", "2025-09", "2026-08")
    prev = raw_sum("uk", "Астрономія", "2024-09", "2025-08")
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
    assert uk["views_prev_12m"] == raw_sum("uk", "Астрономія", "2024-09", "2025-08")


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
    cs = by_lang(printed)["cs"]
    last_12 = sum(int(row["article_views"]) for row in rows[-12:])
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
    assert "requests: 3 network" in first.err  # sitelinks, cs edition, cs article
    assert cli(*args) == 0
    second = capsys.readouterr()
    assert "requests: 0 network" in second.err
    assert json.loads(second.out)["results"] == json.loads(first.out)["results"]
    assert len(fake.urls) == 3
