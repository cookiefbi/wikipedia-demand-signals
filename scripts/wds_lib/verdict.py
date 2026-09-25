"""Metrics and verdict for one article or one language's topic total; ranking.

Explicit rules with named thresholds, no weighted score. Every rule that fires adds
a message to `reasons`, so the agent can say why and a person can check it.
The trend comes from one signal only (normalized year-over-year growth); the other
checks can only lower the confidence.
"""

from dataclasses import dataclass

import numpy as np

from wds_lib import series
from wds_lib.i18n import Msg
from wds_lib.series import Span

# A topic with fewer than ~300 views/month moves by tens of percent from noise alone;
# growth on such a base is reported but never above "low" confidence.
MIN_MEDIAN_MONTHLY_VIEWS = 300

# Normalized year-over-year change that counts as a trend. Smaller moves are what
# ordinary year-to-year variation produces on a stable topic.
TREND_THRESHOLD_PCT = 10.0

# Two years of history show more than one lucky year; under one year there is
# nothing to compare at all.
MIN_HISTORY_MONTHS = 24
MIN_TREND_MONTHS = 12

PER_MILLION = 1_000_000

RISING, FALLING, FLAT, INSUFFICIENT = "rising", "falling", "flat", "insufficient_data"
HIGH, MEDIUM, LOW = "high", "medium", "low"


@dataclass(frozen=True)
class Monthly:
    """Monthly views of an article (or a sum of articles) next to its edition's."""

    span: Span  # months covered by both arrays
    views: np.ndarray
    edition: np.ndarray
    # Month the article appeared (first month with views), or None when it is older
    # than the download window. Months before it are zeros for lack of an article,
    # not for lack of interest.
    created: int | None


@dataclass(frozen=True)
class Metrics:
    views_last_12m: int
    views_prev_12m: int
    growth_pct: float | None
    edition_growth_pct: float | None
    per_million_last_12m: float | None
    per_million_growth_pct: float | None
    median_monthly_views: float
    history_months: int | None  # full months since creation; None: older than window
    warnings: tuple[Msg, ...]


@dataclass(frozen=True)
class Verdict:
    trend: str
    confidence: str
    reasons: tuple[Msg, ...]
    warnings: tuple[Msg, ...]


def growth_pct(last: float, prev: float) -> float | None:
    """Year-over-year change in percent; None when the base is zero."""
    return None if prev == 0 else round((last - prev) / prev * 100, 1)


def per_million(views: float, edition: float) -> float | None:
    return None if edition == 0 else views / edition * PER_MILLION


def measure(data: Monthly, period: Span) -> Metrics:
    last, prev = series.yoy_spans(period)

    def total(values: np.ndarray, part: Span) -> int:
        return int(series.cut(data.span, values, part).sum())

    views_last, views_prev = total(data.views, last), total(data.views, prev)
    edition_last, edition_prev = total(data.edition, last), total(data.edition, prev)

    warnings: list[Msg] = []
    if views_last == 0 and views_prev == 0:
        warnings.append(Msg("warn.no_views"))
    elif data.created is not None and data.created >= prev.first:
        # The creation month itself is partial: the base needs a full year after it.
        since = series.month_label(data.created)
        warnings.append(Msg("warn.base_incomplete", {"since": since}))
    elif views_prev == 0:
        warnings.append(Msg("warn.zero_base"))
    measurable = not warnings

    pm_last = per_million(views_last, edition_last)
    pm_prev = per_million(views_prev, edition_prev)
    pm_growth = None
    if measurable and pm_last is not None and pm_prev is not None:
        pm_growth = growth_pct(pm_last, pm_prev)

    # Volume is judged on the 24 months the growth is computed from, whatever the
    # period: with --period 5y an article that was big years ago but is small now
    # would otherwise pass. Zeros before the article existed are left out.
    first_existing = prev.first
    if data.created is not None:
        first_existing = min(max(prev.first, data.created + 1), last.last)
    existing = series.cut(data.span, data.views, Span(first_existing, last.last))

    return Metrics(
        views_last_12m=views_last,
        views_prev_12m=views_prev,
        growth_pct=growth_pct(views_last, views_prev) if measurable else None,
        edition_growth_pct=growth_pct(edition_last, edition_prev),
        per_million_last_12m=None if pm_last is None else round(pm_last, 2),
        per_million_growth_pct=pm_growth,
        median_monthly_views=float(np.median(existing)),
        history_months=None if data.created is None else period.last - data.created,
        warnings=tuple(warnings),
    )


def trend_of(pm_growth: float | None) -> str:
    if pm_growth is None:
        return INSUFFICIENT
    if pm_growth >= TREND_THRESHOLD_PCT:
        return RISING
    if pm_growth <= -TREND_THRESHOLD_PCT:
        return FALLING
    return FLAT


def _signs_differ(m: Metrics) -> Msg | None:
    """Absolute views and share moved in opposite directions: say it plainly."""
    raw, edition = m.growth_pct, m.edition_growth_pct
    if raw is None or edition is None or m.per_million_growth_pct is None:
        return None
    if raw * m.per_million_growth_pct >= 0:
        return None
    return Msg(
        "signs.differ",
        {
            "raw_dir": Msg("dir.grew" if raw > 0 else "dir.fell"),
            "raw_abs": abs(raw),
            "edition_dir": Msg("dir.grew" if edition > 0 else "dir.fell"),
            "edition_abs": abs(edition),
        },
    )


def _confidence(trend: str, m: Metrics, signs: Msg | None) -> tuple[str, list[Msg]]:
    """Base checks: enough volume, 24+ months of history; for rising/falling also
    absolute and per-million growth in the same direction. All pass -> high, one
    fails -> medium, two or more -> low. Too little volume or under 12 months of
    history -> low regardless.
    """
    if trend == INSUFFICIENT:
        return LOW, [Msg("conf.insufficient")]
    failed: list[Msg] = []
    capped = False
    if m.median_monthly_views < MIN_MEDIAN_MONTHLY_VIEWS:
        failed.append(
            Msg(
                "conf.volume_low",
                {
                    "median": m.median_monthly_views,
                    "min_views": MIN_MEDIAN_MONTHLY_VIEWS,
                },
            )
        )
        capped = True
    if m.history_months is not None and m.history_months < MIN_TREND_MONTHS:
        failed.append(Msg("conf.history_too_short", {"months": m.history_months}))
        capped = True
    elif m.history_months is not None and m.history_months < MIN_HISTORY_MONTHS:
        failed.append(
            Msg(
                "conf.history_short",
                {"months": m.history_months, "min_months": MIN_HISTORY_MONTHS},
            )
        )
    directional = trend in (RISING, FALLING)
    if directional and signs is not None:
        failed.append(signs)
    if not failed:
        passed = Msg(
            "conf.checks_passed",
            {
                "min_views": MIN_MEDIAN_MONTHLY_VIEWS,
                "min_months": MIN_HISTORY_MONTHS,
                "signs": Msg("conf.signs_agree") if directional else "",
            },
        )
        return HIGH, [passed]
    return (MEDIUM if len(failed) == 1 and not capped else LOW), failed


def judge(m: Metrics) -> Verdict:
    trend = trend_of(m.per_million_growth_pct)
    if trend == INSUFFICIENT:
        reasons = [Msg("trend.insufficient")]
    else:
        reasons = [
            Msg(
                f"trend.{trend}",
                {
                    "pm_growth": m.per_million_growth_pct,
                    "threshold": TREND_THRESHOLD_PCT,
                },
            )
        ]
    signs = _signs_differ(m)
    if signs is not None and trend == FLAT:
        reasons.append(signs)  # for rising/falling it is one of the failed checks
    confidence, checks = _confidence(trend, m, signs)
    return Verdict(trend, confidence, tuple(reasons + checks), m.warnings)


# --- ranking ----------------------------------------------------------------

RANK_FIELDS = {
    "growth": "per_million_growth_pct",
    "share": "per_million_last_12m",
    "size": "views_last_12m",
}


def rank(entries: list[tuple[str, Metrics, Verdict]], by: str) -> list[tuple[str, Msg]]:
    """Languages in order, each with why. Low confidence never ranks above a
    confident result; an unknown value goes after known ones; ties keep --langs order.
    """
    field = RANK_FIELDS[by]

    def key(entry: tuple[str, Metrics, Verdict]) -> tuple:
        value = getattr(entry[1], field)
        return (entry[2].confidence == LOW, value is None, -(value or 0))

    order = []
    for lang, metrics, verdict in sorted(entries, key=key):
        value = getattr(metrics, field)
        if value is None:
            why = Msg("rank.unknown")
        else:
            why = Msg(f"rank.{by}", {"value": value, "confidence": verdict.confidence})
            if verdict.confidence == LOW:
                why = Msg("rank.low_after", {"base": why})
        order.append((lang, why))
    return order
