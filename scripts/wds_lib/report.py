"""Chart (PNG) and a one-page A4 report (PDF) built from an Analysis.

Every word on the page comes from the i18n templates and every number from the
analysis; the agent contributes only --note. The page is a single matplotlib
figure, so it cannot spill onto a second page: each text block gets a line
budget, and whatever does not fit ends with a pointer to result.json.
"""

import functools
import itertools
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # files only, never a window

import numpy as np
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.lines import Line2D
from matplotlib.textpath import TextToPath
from matplotlib.ticker import FuncFormatter, MaxNLocator

from wds_lib import __version__, i18n, series
from wds_lib.analyze import Analysis, Measured, Target, per_language
from wds_lib.i18n import Msg

# Ships with matplotlib and covers Cyrillic and Central European letters (ł, ř, ě),
# so titles render the same on every machine.
FONT = "DejaVu Sans"
RC = {
    "font.family": FONT,
    "pdf.fonttype": 42,  # embedded TrueType: text stays selectable and searchable
    "axes.unicode_minus": False,
}

# Categorical palette (reference instance of the dataviz method), validated on a
# white surface: worst adjacent colour-blind ΔE 9.1, normal-vision ΔE 19.6.
# Assigned by position in --langs, never by rank, so adding a language never
# repaints the others. Past eight languages the chart would need a ninth hue that
# no palette provides; those languages stay in the table only.
SERIES_COLORS = (
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#4a3aa7",
    "#e34948",
)
SURFACE = "#ffffff"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

LINE_PT = 1.5  # 2 px
MARKER_PT = 6.0  # end dot, 8 px across
RING_PT = 1.5  # surface ring around the end dot
# Direct end labels only while they stay readable: few lines, far enough apart.
MAX_DIRECT_LABELS = 4
MIN_LABEL_GAP = 0.07  # share of the axis height between neighbouring end labels

A4_IN = (8.27, 11.69)
MARGIN_IN = 0.6
PT_PER_IN = 72
LINE_SPACING = 1.35
# Text widths are summed per character (kerning ignored): keep a little slack.
WIDTH_SLACK = 0.96
MAX_TABLE_ROWS = 12
MAX_NOTE_LINES = 6


# --- text measurement -------------------------------------------------------


@functools.cache
def _char_width(char: str, bold: bool = False) -> float:
    """Width of one character at 1 pt, in points."""
    font = FontProperties(family=FONT, size=1, weight="bold" if bold else "normal")
    width, _, _ = TextToPath().get_text_width_height_descent(char, font, ismath=False)
    return width


def text_width(text: str, size: float, bold: bool = False) -> float:
    return sum(_char_width(c, bold) for c in text) * size


def wrap(text: str, width_pt: float, size: float, bold: bool = False) -> list[str]:
    """Greedy word wrap by measured width; a single overlong word is cut."""
    limit = width_pt * WIDTH_SLACK
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if text_width(candidate, size, bold) <= limit:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = fit(word, width_pt, size, bold)
    if current:
        lines.append(current)
    return lines


def fit(text: str, width_pt: float, size: float, bold: bool = False) -> str:
    """The text, shortened with an ellipsis if it is wider than width_pt."""
    limit = width_pt * WIDTH_SLACK
    if text_width(text, size, bold) <= limit:
        return text
    while text and text_width(text + "…", size, bold) > limit:
        text = text[:-1]
    return text.rstrip() + "…"


# --- chart ------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    lang: str
    color: str
    months: np.ndarray  # month numbers of the period
    per_million: np.ndarray
    views: np.ndarray


def chart_lines(a: Analysis) -> tuple[list[Line], int]:
    """(lines to draw in --langs order, number of languages left out)."""
    data = per_language(a)
    codes = [code for code in a.langs if code in data]
    shown = [code for code in codes if a.langs.index(code) < len(SERIES_COLORS)]
    lines = []
    for code in shown:
        d = data[code].data
        months = np.arange(a.period.first, a.period.last + 1)
        views = series.cut(d.span, d.views, a.period).astype(float)
        if d.created is not None:
            # No line before the article existed; its first month is partial.
            views[months <= d.created_month] = np.nan
        edition = series.cut(d.span, d.edition, a.period).astype(float)
        share = np.divide(
            views * 1_000_000,
            edition,
            out=np.full_like(views, np.nan),
            where=edition > 0,
        )
        lines.append(
            Line(
                lang=code,
                color=SERIES_COLORS[a.langs.index(code)],
                months=months,
                per_million=share,
                views=views,
            )
        )
    return lines, len(codes) - len(shown)


def _month_ticks(first: int, last: int) -> list[int]:
    """Calendar-aligned ticks, at most ~8: quarters for two years, halves beyond."""
    count = last - first + 1
    step = next(s for s in (1, 2, 3, 6, 12) if count / s <= 8)
    return [m for m in range(first, last + 1) if m % step == 0]


def _style_axis(ax, title: str, size: float) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=size, color=INK, pad=6)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.spines["bottom"].set_linewidth(0.75)
    ax.grid(axis="y", color=GRID, linewidth=0.75, linestyle="-")
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=size - 1.5, length=0, pad=4)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4, min_n_ticks=3))


def _direct_labels(ax, lines: list[Line], values: str, size: float) -> None:
    """Language code at each line's end, unless the ends crowd each other."""
    if len(lines) > MAX_DIRECT_LABELS:
        return
    ends = []
    for line in lines:
        y = getattr(line, values)
        finite = np.flatnonzero(np.isfinite(y))
        if finite.size:
            ends.append((line, line.months[finite[-1]], y[finite[-1]]))
    low, high = ax.get_ylim()
    heights = sorted((y - low) / (high - low) for _, _, y in ends)
    if any(b - a < MIN_LABEL_GAP for a, b in itertools.pairwise(heights)):
        return  # converging lines: the legend carries identity instead
    for line, x, y in ends:
        ax.annotate(
            line.lang,
            (x, y),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            fontsize=size - 1,
            color=INK_2,
        )


def draw_chart(
    fig: Figure,
    rect: tuple[float, float, float, float],
    a: Analysis,
    lang: str,
    size: float,
) -> None:
    """Two panels in rect (figure fractions): share of the edition, then absolute
    views. Two measures get two axes of their own, never one dual-axis plot.

    Rows from the top, in inches: legend (2+ languages), title, panel, gap, title,
    panel, month labels. Room on the right is kept for direct end labels.
    """
    lines, hidden = chart_lines(a)
    fig_w, fig_h = fig.get_size_inches()
    left, bottom, width, height = rect
    top_in = (bottom + height) * fig_h
    height_in = height * fig_h
    row = size * LINE_SPACING / PT_PER_IN
    legend_in = row * 1.6 if len(lines) > 1 else 0.0
    title_in = row + 6 / PT_PER_IN  # title text + its pad above the axes
    gap_in = row * 1.2
    ticks_in = row * 1.6
    panel_in = (height_in - legend_in - 2 * title_in - gap_in - ticks_in) / 2
    axes_w = width - 0.3 / fig_w

    def axes_at(top: float):
        return fig.add_axes((left, (top - panel_in) / fig_h, axes_w, panel_in / fig_h))

    upper_top = top_in - legend_in - title_in
    top_ax = axes_at(upper_top)
    low_ax = axes_at(upper_top - panel_in - gap_in - title_in)
    low_ax.sharex(top_ax)
    panels = (
        (top_ax, "per_million", i18n.render("chart.per_million", lang=lang)),
        (low_ax, "views", i18n.render("chart.views", lang=lang)),
    )
    for ax, values, title in panels:
        _style_axis(ax, title, size)
        for line in lines:
            y = getattr(line, values)
            ax.plot(
                line.months,
                y,
                color=line.color,
                linewidth=LINE_PT,
                solid_capstyle="round",
                solid_joinstyle="round",
            )
            finite = np.flatnonzero(np.isfinite(y))
            if finite.size:
                ax.plot(
                    line.months[finite[-1]],
                    y[finite[-1]],
                    marker="o",
                    markersize=MARKER_PT,
                    color=line.color,
                    markeredgecolor=SURFACE,
                    markeredgewidth=RING_PT,
                )
        ax.set_ylim(bottom=0)
        ax.margins(x=0.01)
    # Per-million values run from hundredths (small topics) to hundreds.
    high = top_ax.get_ylim()[1]
    decimals = 0 if high >= 10 else 1 if high >= 1 else 2
    top_ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.{decimals}f}"))
    low_ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ticks = _month_ticks(a.period.first, a.period.last)
    low_ax.set_xticks(ticks, [series.month_label(m) for m in ticks])
    top_ax.tick_params(labelbottom=False)
    for ax, values, _ in panels:
        _direct_labels(ax, lines, values, size)
    if len(lines) > 1:
        handles = [
            Line2D([], [], color=line.color, linewidth=LINE_PT * 1.5) for line in lines
        ]
        fig.legend(
            handles,
            [line.lang for line in lines],
            loc="upper left",
            bbox_to_anchor=(left, (bottom + height)),
            ncol=len(lines),
            frameon=False,
            fontsize=size - 1,
            labelcolor=INK_2,
            handlelength=1.6,
            columnspacing=1.4,
            borderaxespad=0,
            borderpad=0,
        )
    if hidden:
        fig.text(
            left + width,
            bottom + height,
            i18n.render("chart.more_langs", {"shown": len(lines)}, lang),
            ha="right",
            va="top",
            fontsize=size - 2,
            color=MUTED,
        )


def write_png(a: Analysis, path: Path, lang: str = "en") -> Path:
    with matplotlib.rc_context(RC):
        fig = Figure(figsize=(8, 5.4), facecolor=SURFACE)
        fig.text(0.06, 0.965, a.label, fontsize=13, color=INK, va="top", weight="bold")
        fig.text(0.06, 0.915, _subtitle(a, lang), fontsize=8.5, color=INK_2, va="top")
        draw_chart(fig, (0.08, 0.08, 0.86, 0.76), a, lang, size=10)
        fig.savefig(path, dpi=150, facecolor=SURFACE)
    return path


# --- page -------------------------------------------------------------------


def _subtitle(a: Analysis, lang: str) -> str:
    items = sorted({t.qid for t in a.targets if t.qid}) or ["--article"]
    return i18n.render(
        "report.subtitle",
        {
            "items": ", ".join(items),
            "langs": ", ".join(a.langs),
            "first": series.month_label(a.period.first),
            "last": series.month_label(a.period.last),
            "months": a.period.months,
            "rank_by": Msg(f"rank_by.{a.rank_by}"),
        },
        lang,
    )


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.1f}%"


def _num(value: float | None, digits: int = 0) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def table_rows(a: Analysis, lang: str) -> list[tuple[str, list[str]]]:
    """(language code, cells) in ranking order; missing articles last.

    One row per article, plus the topic total where a language has several. If
    that is more than the page holds, one row per language: what the ranking is about.
    """

    def cells(title: str, m: Measured) -> list[str]:
        metrics, v = m.metrics, m.verdict
        return [
            title,
            _num(metrics.views_last_12m),
            _pct(metrics.growth_pct),
            _num(metrics.per_million_last_12m, 2),
            _pct(metrics.per_million_growth_pct),
            i18n.render(f"trend_name.{v.trend}", lang=lang),
            i18n.render(f"conf_name.{v.confidence}", lang=lang),
        ]

    def total_label(code: str) -> str:
        count = sum(t.lang.code == code for t in a.measured)
        return i18n.render("row.total", {"count": count}, lang)

    ranked = [code for code, _ in a.ranking]
    no_article = [i18n.render("row.no_article", lang=lang)] + [""] * 6
    several = len({t.qid or t.title for t in a.targets}) > 1

    def missing_row(target: Target) -> list[str]:
        if not several:
            return no_article
        item = a.item_labels.get(target.qid, target.qid)
        return [i18n.render("row.no_article_item", {"item": item}, lang)] + [""] * 6

    detailed = []
    for code in ranked:
        detailed += [
            (code, cells(t.title, m))
            for t, m in a.measured.items()
            if t.lang.code == code
        ]
        if code in a.totals:
            detailed.append((code, cells(total_label(code), a.totals[code])))
    detailed += [(t.lang.code, missing_row(t)) for t in a.targets if t.title is None]
    if len(detailed) <= MAX_TABLE_ROWS:
        return detailed

    data = per_language(a)
    compact = []
    for code in ranked:
        if code in a.totals:
            title = total_label(code)
        else:
            title = next(t.title for t in a.measured if t.lang.code == code)
        compact.append((code, cells(title, data[code])))
    missing = dict.fromkeys(t.lang.code for t in a.targets if t.lang.code not in data)
    compact += [(code, no_article) for code in missing]
    return compact


def clamp(
    lines: list[str], count: int, width_pt: float, size: float, bold: bool = False
) -> list[str]:
    """At most `count` lines; the last one ends with an ellipsis if text was cut."""
    if len(lines) <= count:
        return lines
    rest = " ".join(lines[count - 1 :])
    return [*lines[: count - 1], fit(rest + " …", width_pt, size, bold)]


class Page:
    """Top-down layout on one A4 figure, in inches from the top-left corner."""

    def __init__(self, fig: Figure) -> None:
        self.fig = fig
        self.y = MARGIN_IN - 0.1
        self.width_in = A4_IN[0] - 2 * MARGIN_IN

    def fx(self, x_in: float) -> float:
        return x_in / A4_IN[0]

    def fy(self, y_in: float) -> float:
        return 1 - y_in / A4_IN[1]

    @staticmethod
    def line_in(size: float) -> float:
        return size * LINE_SPACING / PT_PER_IN

    def text(
        self, x_in: float, text: str, size: float, color: str = INK, **kwargs
    ) -> None:
        self.fig.text(
            self.fx(x_in),
            self.fy(self.y),
            text,
            fontsize=size,
            color=color,
            va="top",
            **kwargs,
        )

    def lines(
        self,
        lines: list[str],
        size: float,
        color: str = INK,
        indent: float = 0.0,
        **kwargs,
    ) -> None:
        for line in lines:
            self.text(MARGIN_IN + indent, line, size, color, **kwargs)
            self.y += self.line_in(size)

    def hairline(self, color: str = GRID) -> None:
        y = self.fy(self.y)
        self.fig.add_artist(
            Line2D(
                [self.fx(MARGIN_IN), self.fx(A4_IN[0] - MARGIN_IN)],
                [y, y],
                color=color,
                linewidth=0.75,
            )
        )


# Table columns: (i18n key, width in inches, right-aligned); they add up to the
# text width of the page. Widths fit the measured headers and the longest values
# ("insufficient data" is 0.90 in at 8 pt); the article title takes the rest.
COLUMNS = (
    ("col.lang", 0.62, False),
    ("col.article", 1.41, False),
    ("col.views", 0.9, True),
    ("col.growth", 0.72, True),
    ("col.per_million", 0.75, True),
    ("col.pm_growth", 0.9, True),
    ("col.trend", 1.05, False),
    ("col.confidence", 0.72, False),
)
BODY_PT = 8.0
SMALL_PT = 7.2
FOOTER_PT = 6.5
# Space between a column's text and the next column.
CELL_PAD_IN = 0.18
LEFT_PAD_IN = 0.1
# The chart takes the height the text below it leaves free, within these bounds.
CHART_MAX_IN = 3.3
CHART_MIN_IN = 2.3
CHART_GAP_IN = 0.3
# Verdicts the chart must leave room for before it may grow.
MIN_VERDICTS = 3


def _cell_width(width: float, right: bool) -> float:
    return (width - (CELL_PAD_IN if right else LEFT_PAD_IN)) * PT_PER_IN


def _draw_table(page: Page, a: Analysis, lang: str) -> None:
    colors = {code: SERIES_COLORS[i] for i, code in enumerate(a.langs[:8])}
    rows = table_rows(a, lang)
    extra = len(rows) - MAX_TABLE_ROWS
    if extra > 0:
        rows = rows[: MAX_TABLE_ROWS - 1]
    x = MARGIN_IN
    for key, width, right in COLUMNS:
        label = fit(i18n.render(key, lang=lang), _cell_width(width, right), SMALL_PT)
        page.text(
            x + width - CELL_PAD_IN if right else x,
            label,
            SMALL_PT,
            MUTED,
            ha="right" if right else "left",
        )
        x += width
    page.y += page.line_in(SMALL_PT) + 0.03
    page.hairline(AXIS)
    page.y += 0.05
    for code, cells in rows:
        swatch = colors.get(code)
        if swatch:
            page.fig.add_artist(
                Line2D(
                    [page.fx(MARGIN_IN + 0.01), page.fx(MARGIN_IN + 0.13)],
                    [page.fy(page.y + 0.065)] * 2,
                    color=swatch,
                    linewidth=LINE_PT * 2,
                    solid_capstyle="round",
                )
            )
        page.text(MARGIN_IN + 0.2, code, BODY_PT)
        x = MARGIN_IN + COLUMNS[0][1]
        for (_, width, right), cell in zip(COLUMNS[1:], cells, strict=True):
            cell = fit(cell, _cell_width(width, right), BODY_PT)
            page.text(
                x + width - CELL_PAD_IN if right else x,
                cell,
                BODY_PT,
                INK,
                ha="right" if right else "left",
            )
            x += width
        page.y += page.line_in(BODY_PT) + 0.04
    if extra > 0:
        more = i18n.render("row.more", {"count": extra + 1}, lang)
        page.lines([more], SMALL_PT, MUTED)


def _verdict_paragraphs(a: Analysis, lang: str) -> list[tuple[str, list[str]]]:
    """(head line, detail sentences) per ranked language, then missing articles."""
    data = per_language(a)
    out = []
    for rank, (code, _) in enumerate(a.ranking, start=1):
        v = data[code].verdict
        head = i18n.render(
            "verdict.head",
            {
                "rank": rank,
                "lang": code,
                "trend": Msg(f"trend_name.{v.trend}"),
                "confidence": Msg(f"conf_name.{v.confidence}"),
            },
            lang,
        )
        details = i18n.texts(list(v.reasons) + list(v.warnings), lang)
        out.append((head, details))
    missing = dict.fromkeys(t.lang.code for t in a.targets if t.title is None)
    for code in missing:
        if code not in data:
            out.append((i18n.render("verdict.no_article", {"lang": code}, lang), []))
    return out


def build_pdf_figure(a: Analysis, lang: str = "en", note: str | None = None) -> Figure:
    """Header, table, chart, verdicts, note, caveats, assumptions, footer.

    Two passes: everything below the chart is measured first, then the chart gets
    the height that is left (within bounds). Caveats and the note are always
    shown; verdicts take whole paragraphs while they fit; assumptions get the rest.
    """
    fig = Figure(figsize=A4_IN, facecolor=SURFACE)
    page = Page(fig)
    width_pt = page.width_in * PT_PER_IN
    small, body = page.line_in(SMALL_PT), page.line_in(BODY_PT)
    heading, gap = body + 0.04, 0.12
    more = i18n.render("report.truncated", lang=lang)

    page.lines([i18n.render("report.kicker", lang=lang)], SMALL_PT, MUTED)
    page.y += 0.04
    title = clamp(wrap(a.label, width_pt, 17, bold=True), 2, width_pt, 17, bold=True)
    page.lines(title, 17, INK, weight="bold")
    page.y += 0.02
    page.lines(wrap(_subtitle(a, lang), width_pt, BODY_PT), BODY_PT, INK_2)
    page.y += 0.2
    _draw_table(page, a, lang)
    page.y += 0.2

    footer = wrap(
        i18n.render(
            "report.footer", {"date": str(a.generated), "version": __version__}, lang
        ),
        width_pt,
        FOOTER_PT,
    )
    footer_top = A4_IN[1] - MARGIN_IN + 0.1 - len(footer) * page.line_in(FOOTER_PT)
    bottom = footer_top - 0.1
    note_lines = []
    if note:
        note_lines = clamp(
            wrap(note, width_pt, SMALL_PT), MAX_NOTE_LINES, width_pt, SMALL_PT
        )
    caveat_lines = [
        line
        for key in i18n.CAVEATS
        for line in wrap(f"• {i18n.render(key, lang=lang)}", width_pt, SMALL_PT)
    ]
    indent_pt = 12
    paragraphs = [
        (head, wrap("; ".join(details), width_pt - indent_pt, SMALL_PT))
        for head, details in _verdict_paragraphs(a, lang)
    ]
    para_h = [body + len(lines) * small + 0.05 for _, lines in paragraphs]
    assumption_lines = [
        line
        for text in i18n.texts(a.assumptions, lang)
        for line in wrap(f"• {text}", width_pt, SMALL_PT)
    ]
    fixed = heading + gap  # the verdicts heading
    if note_lines:
        fixed += heading + len(note_lines) * small + gap
    fixed += heading + len(caveat_lines) * small + gap

    wanted = fixed + sum(para_h[:MIN_VERDICTS]) + small + heading + 3 * small
    free = bottom - page.y - CHART_GAP_IN - wanted
    chart_h = min(CHART_MAX_IN, max(CHART_MIN_IN, free))
    draw_chart(
        fig,
        (
            page.fx(MARGIN_IN + 0.45),
            page.fy(page.y + chart_h),
            (page.width_in - 0.5) / A4_IN[0],
            chart_h / A4_IN[1],
        ),
        a,
        lang,
        size=BODY_PT,
    )
    page.y += chart_h + CHART_GAP_IN

    room = bottom - page.y - fixed
    shown = 0
    for i, h in enumerate(para_h):
        reserve = small if i < len(para_h) - 1 else 0.0
        if h + reserve > room:
            break
        room -= h
        shown += 1
    if shown < len(paragraphs):
        room -= small

    def section(key: str) -> None:
        page.lines([i18n.render(key, lang=lang)], BODY_PT, INK, weight="bold")
        page.y += 0.04

    section("section.verdicts")
    for head, lines in paragraphs[:shown]:
        page.lines([head], BODY_PT, INK)
        page.lines(lines, SMALL_PT, INK_2, indent=indent_pt / PT_PER_IN)
        page.y += 0.05
    if shown < len(paragraphs):
        page.lines([more], SMALL_PT, MUTED)
    page.y += gap
    if note_lines:
        section("section.note")
        page.lines(note_lines, SMALL_PT, INK)
        page.y += gap
    section("section.caveats")
    page.lines(caveat_lines, SMALL_PT, INK_2)
    page.y += gap

    fits = int((room - heading) / small + 1e-9)
    if fits >= 2:  # a heading with a single line is not worth it
        if len(assumption_lines) > fits:
            assumption_lines = [*assumption_lines[: fits - 1], more]
        section("section.assumptions")
        page.lines(assumption_lines, SMALL_PT, INK_2)

    page.y = footer_top
    page.lines(footer, FOOTER_PT, MUTED)
    return fig


def write_pdf(
    a: Analysis, path: Path, lang: str = "en", note: str | None = None
) -> Path:
    with matplotlib.rc_context(RC):
        fig = build_pdf_figure(a, lang, note)
        fig.savefig(
            path,
            format="pdf",
            facecolor=SURFACE,
            metadata={
                "Title": f"{a.label}: {i18n.render('report.kicker', lang=lang)}",
                "Creator": f"wikipedia-demand-signals {__version__}",
            },
        )
    return path
