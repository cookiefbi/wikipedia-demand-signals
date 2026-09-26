"""PNG and one-page PDF: real fixture data and a worst case built to overflow."""

import json
import re
import warnings

import numpy as np
import pytest
import wds
from matplotlib.backends.backend_agg import FigureCanvasAgg
from test_analyze import RESPONSES, TODAY, WINDOW, analyze_young_article
from wds_lib import analyze, api, i18n, pageviews, report, resolve, series


@pytest.fixture
def replay(monkeypatch):
    monkeypatch.setattr(api, "get_json", lambda url, *, ttl: RESPONSES[url])
    monkeypatch.setattr(analyze, "utc_today", lambda: TODAY)


def astronomy():
    return analyze.analyze(
        qids=["Q333"], articles=[], langs_arg="pl,cs,uk", today=TODAY
    )


LANGS = "pl,cs,uk,sk,de,fr,es,it,hu,ro"


@pytest.fixture
def crowded(monkeypatch):
    """10 languages x 3 articles with long titles: more than a page can hold."""
    codes = LANGS.split(",")
    long_title = "Very long article title about the history and practice of {} ({})"
    monkeypatch.setattr(
        resolve,
        "sitelink_titles",
        lambda qids: {
            qid: {
                "label": f"topic {qid} with a rather long English label for the header",
                "titles": {f"{c}wiki": long_title.format(qid, c) for c in codes},
            }
            for qid in qids
        },
    )
    monkeypatch.setattr(
        pageviews, "published_window", lambda today, lang: (WINDOW, False)
    )
    monkeypatch.setattr(
        resolve, "article_meta", lambda lang, title: resolve.ArticleMeta(None, (), 0)
    )
    days = (WINDOW.end - WINDOW.start).days + 1
    rng = np.random.default_rng(7)
    monkeypatch.setattr(
        pageviews, "edition_daily", lambda lang, w: np.full(days, 2_000_000)
    )
    monkeypatch.setattr(
        pageviews,
        "article_daily",
        lambda lang, title, w: rng.integers(0, 40, days),
    )
    return analyze.analyze(
        qids=["Q1", "Q2", "Q3"], articles=[], langs_arg=LANGS, today=TODAY
    )


def pdf_pages(path) -> int:
    return len(re.findall(rb"/Type\s*/Page\b", path.read_bytes()))


def test_pdf_is_one_page_with_the_embedded_font(replay, tmp_path):
    path = report.write_pdf(astronomy(), tmp_path / "report.pdf")
    assert pdf_pages(path) == 1
    data = path.read_bytes()
    assert b"DejaVuSans" in data
    assert b"/FontFile2" in data  # TrueType embedded: text stays selectable


def test_png_is_written(replay, tmp_path):
    path = report.write_png(astronomy(), tmp_path / "chart.png")
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_ukrainian_pdf_is_one_page_with_every_glyph_in_dejavu(replay, tmp_path):
    note = "Почніть із B2C-читачів українського розділу; ґрунтовно, з їхніми питаннями."
    with warnings.catch_warnings():
        # matplotlib warns "Glyph ... missing from font(s)" for a letter it lacks
        warnings.simplefilter("error")
        path = report.write_pdf(astronomy(), tmp_path / "report.pdf", "uk", note)
    assert pdf_pages(path) == 1
    assert b"DejaVuSans" in path.read_bytes()
    with report.matplotlib.rc_context(report.RC):
        texts = [t.get_text() for t in report.build_pdf_figure(astronomy(), "uk").texts]
    assert "Висновки" in texts and "Застереження" in texts
    assert "1. pl: стабільний, середня довіра" in texts


@pytest.mark.parametrize("lang", ["en", "uk"])
def test_overflowing_content_still_fits_one_page(crowded, tmp_path, lang):
    note = "Interview readers in the editions with rising share first. " * 30
    path = report.write_pdf(crowded, tmp_path / "report.pdf", lang, note)
    assert pdf_pages(path) == 1


def _text_boxes(fig):
    renderer = FigureCanvasAgg(fig).get_renderer()
    return [
        (t.get_text(), t.get_fontsize(), t.get_window_extent(renderer))
        for t in fig.texts
    ]


@pytest.mark.parametrize("lang", ["en", "uk"])
def test_nothing_leaves_the_page_or_runs_into_the_footer(crowded, lang):
    note = "Interview readers in the editions with rising share first. " * 30
    with report.matplotlib.rc_context(report.RC):
        fig = report.build_pdf_figure(crowded, lang, note)
        boxes = _text_boxes(fig)
    page = fig.bbox
    for text, _, box in boxes:
        assert box.x0 >= page.x0 - 1 and box.x1 <= page.x1 + 1, text
        assert box.y0 >= page.y0 - 1 and box.y1 <= page.y1 + 1, text
    footer = [box for _, size, box in boxes if size == report.FOOTER_PT]
    body = [box for _, size, box in boxes if size != report.FOOTER_PT]
    assert len(footer) >= 1
    assert min(b.y0 for b in body) > max(f.y1 for f in footer)
    texts = [text for text, _, _ in boxes]
    # verdicts or assumptions were cut
    assert i18n.render("report.truncated", lang=lang) in texts
    # 40 article rows do not fit: one row per language instead, all ten shown
    assert set(LANGS.split(",")) <= set(texts)
    assert texts.count(i18n.render("row.total", {"count": 3}, lang)) == 10


def test_long_titles_are_shortened_with_an_ellipsis():
    short = report.fit("Přerušovaný půst " * 10, 100, 8)
    assert short.endswith("…")
    assert report.text_width(short, 8) <= 100


def test_wrap_keeps_every_line_within_the_width():
    text = "views per million edition views -46.4% year over year " * 6
    lines = report.wrap(text, 200, 7.2)
    assert len(lines) > 1
    assert all(report.text_width(line, 7.2) <= 200 for line in lines)
    assert " ".join(lines).split() == text.split()


def test_missing_article_row_names_the_item_when_there_are_several(replay):
    a = analyze.analyze(
        qids=["Q1666254"],
        articles=["pl:Głodówka lecznicza"],
        langs_arg="pl,cs",
        today=TODAY,
    )
    rows = report.table_rows(a, "en")
    assert rows[-1] == ("pl", ["no article on 'intermittent fasting'"] + [""] * 6)
    alone = analyze.analyze(
        qids=["Q1666254"], articles=[], langs_arg="pl,cs", today=TODAY
    )
    assert report.table_rows(alone, "en")[-1][1][0] == "no article"


def test_colors_follow_the_langs_order_not_the_ranking(replay):
    lines, hidden = report.chart_lines(astronomy())
    assert [(line.lang, line.color) for line in lines] == [
        ("pl", report.SERIES_COLORS[0]),
        ("cs", report.SERIES_COLORS[1]),
        ("uk", report.SERIES_COLORS[2]),
    ]
    assert hidden == 0


def test_more_than_eight_languages_are_left_to_the_table(crowded):
    lines, hidden = report.chart_lines(crowded)
    assert len(lines) == len(report.SERIES_COLORS) and hidden == 2


def test_no_line_before_the_article_existed(monkeypatch):
    a = analyze_young_article(monkeypatch)  # first edit 2025-01-10
    [line], _ = report.chart_lines(a)
    first_full = list(line.months).index(series.parse_month("2025-02", "-"))
    assert np.isnan(line.views[:first_full]).all()  # its first month is partial
    assert not np.isnan(line.views[first_full:]).any()
    assert np.isnan(line.per_million[:first_full]).all()


def test_cli_report_writes_png_and_pdf(replay, tmp_path, capsys):
    out = tmp_path / "out"
    code = wds.main(
        [
            "analyze",
            "--qid",
            "Q333",
            "--langs",
            "pl,cs,uk",
            "--report",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    files = json.loads(capsys.readouterr().out)["files"]
    assert files["chart_png"] == str(out / "chart.png")
    assert files["report_pdf"] == str(out / "report.pdf")
    assert pdf_pages(out / "report.pdf") == 1


def test_pdf_follows_answer_lang_unless_report_lang_is_given(
    replay, tmp_path, capsys, monkeypatch
):
    langs = []
    write_pdf = report.write_pdf

    def spy(a, path, lang="en", note=None):
        langs.append(lang)
        return write_pdf(a, path, lang, note)

    monkeypatch.setattr(report, "write_pdf", spy)
    args = [
        "analyze",
        "--qid",
        "Q333",
        "--langs",
        "uk",
        "--report",
        "--out",
        str(tmp_path),
    ]
    assert wds.main([*args, "--answer-lang", "uk"]) == 0
    ukrainian = json.loads(capsys.readouterr().out)
    assert wds.main([*args, "--answer-lang", "uk", "--report-lang", "en"]) == 0
    assert json.loads(capsys.readouterr().out) == ukrainian  # PDF language only
    assert wds.main(args) == 0
    english = json.loads(capsys.readouterr().out)
    assert langs == ["uk", "en", "en"]
    assert ukrainian["must_say"][1] == "uk: висока довіра — перевірки пройдено."
    assert english["must_say"][1] == "uk: high confidence — checks passed."
