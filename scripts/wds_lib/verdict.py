"""Metrics and verdict for one article or one language's topic total; ranking.

Explicit rules with named thresholds, no weighted score. Every rule that fires adds
a message to `reasons`, so the agent can say why and a person can check it.
The trend comes from one signal only (normalized year-over-year growth); the other
checks can only lower the confidence. They all look at the same 24 months the growth
is computed from, month by month, in views per million edition views.
"""

import datetime as dt
from dataclasses import dataclass

import numpy as np

from wds_lib import series, stats
from wds_lib.i18n import Msg
from wds_lib.series import YEAR, Span

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

# Steady = seasonal Mann-Kendall significant at the usual 5%: each month against the
# same month a year earlier, which over two years means at least 10 of 12 months
# moved the same way. A plain Mann-Kendall over the 24 months mistakes a school year
# (high September, low summer) for a trend: on synthetic flat school-year series with
# 15% monthly noise it did so in 89% of runs, the seasonal test in 5%.
STEADY_P_VALUE = 0.05

# One-off spike (SPEC 4): a month with at least SPIKE_RATIO times the median of the
# SPIKE_NEIGHBOURS months on each side (fewer at the ends of the 24 months). 2.5x
# adds 1.5 months' worth of views, +12.5% to a 12-month total: alone it could push
# growth past TREND_THRESHOLD_PCT. Three months a side, so a peak cannot lift its
# own baseline.
SPIKE_RATIO = 2.5
SPIKE_NEIGHBOURS = 3
# It is the season, not a spike, when the same month a year apart also stands at
# least SEASONAL_PEAK_RATIO times above its own neighbours: a season's peak varies in
# height (uk "Астрономія": September 3.0x in 2024, 3.6x in 2025). On synthetic flat
# school-year series with 15% noise a bar of 1.5 finds a false spike in 1% of runs,
# a bar of 2.0 in 22%.
SEASONAL_PEAK_RATIO = 1.5

# Sharp lasting change of level (SPEC 4): the median of LEVEL_WINDOW_MONTHS months at
# least LEVEL_RATIO times the median of the same number of months right before it,
# either way. Steady growth, even +300% a year, moves 6-month medians far less; a
# rename that redirects do not cover, or a change in how views are counted, moves
# them at once. Half a year on the new level is what makes it lasting.
LEVEL_RATIO = 3.0
LEVEL_WINDOW_MONTHS = 6

RISING, FALLING, FLAT, INSUFFICIENT = "rising", "falling", "flat", "insufficient_data"
HIGH, MEDIUM, LOW = "high", "medium", "low"


@dataclass(frozen=True)
class Monthly:
    """Monthly views of an article (or a sum of articles) next to its edition's."""

    span: Span  # months covered by the arrays
    views: np.ndarray  # the article's own views plus its redirects'
    redirects: np.ndarray  # the part of `views` that came through redirects
    edition: np.ndarray
    # Day of the article's first edit, or None when it is older than the download
    # window. Views before it are dropped: zeros for lack of an article, not for
    # lack of interest.
    created: dt.date | None
    # Problems found while fetching (e.g. redirects left out); they do not stop
    # growth from being computed.
    warnings: tuple[Msg, ...] = ()

    @property
    def created_month(self) -> int | None:
        return None if self.created is None else series.month_of(self.created)


@dataclass(frozen=True)
class Steadiness:
    """Each of the last 12 months against the same month a year earlier."""

    higher: int
    lower: int
    p: float  # seasonal Mann-Kendall
    # Seasonal Theil-Sen slope as % of the base year's median month: how much a
    # typical month changed, whatever one extreme month did to the 12-month totals.
    typical_pct: float | None


@dataclass(frozen=True)
class Spike:
    month: int
    ratio: float  # views per million against the median of the months around it
    pair: int  # the same month a year apart, which has no such peak


@dataclass(frozen=True)
class LevelChange:
    month: int  # the first month on the new level
    ratio: float  # median of the months from it on / median of the months before

    @property
    def factor(self) -> float:
        """How many times the level changed, up or down."""
        return max(self.ratio, 1 / self.ratio)


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
    # Month-by-month checks, only when growth is measured.
    steadiness: Steadiness | None = None
    spikes: tuple[Spike, ...] = ()
    level_change: LevelChange | None = None


@dataclass(frozen=True)
class Verdict:
    trend: str
    confidence: str
    reasons: tuple[Msg, ...]
    warnings: tuple[Msg, ...]
    # The first rule that kept the confidence from being higher; None for high.
    # must_say names it as the main reason. Set where the rule fires rather than
    # read off `reasons` by position: a flat topic's note on opposite signs comes
    # before it there, and a passed check comes first when nothing failed.
    limit: Msg | None = None


def growth_pct(last: float, prev: float) -> float | None:
    """Year-over-year change in percent; None when the base is zero."""
    return None if prev == 0 else round((last - prev) / prev * 100, 1)


def per_million(views: float, edition: float) -> float | None:
    return None if edition == 0 else views / edition * PER_MILLION


def _shares(views: np.ndarray, edition: np.ndarray) -> np.ndarray:
    """Views per million edition views, month by month (0 if the edition had none)."""
    return np.divide(
        views * float(PER_MILLION),
        edition,
        out=np.zeros(len(views)),
        where=edition > 0,
    )


def _steadiness(shares: np.ndarray) -> Steadiness:
    base, recent = shares[:YEAR], shares[YEAR:]
    base_month = float(np.median(base))
    typical = None
    if base_month > 0:
        typical = round(stats.seasonal_theil_sen(shares, YEAR) / base_month * 100, 1)
    return Steadiness(
        higher=int((recent > base).sum()),
        lower=int((recent < base).sum()),
        p=stats.seasonal_mann_kendall(shares, YEAR).p,
        typical_pct=typical,
    )


def _peak_ratios(shares: np.ndarray) -> np.ndarray:
    """Each month against the median of its neighbours; NaN where they are all 0."""
    ratios = np.full(len(shares), np.nan)
    for i in range(len(shares)):
        around = np.concatenate(
            [
                shares[max(0, i - SPIKE_NEIGHBOURS) : i],
                shares[i + 1 : i + 1 + SPIKE_NEIGHBOURS],
            ]
        )
        baseline = np.median(around)
        if baseline > 0:
            ratios[i] = shares[i] / baseline
    return ratios


def _spikes(shares: np.ndarray, first: int) -> tuple[Spike, ...]:
    """Peaks that the same month a year apart does not have: a school September that
    comes back every year is the season, not a spike."""
    ratios = _peak_ratios(shares)
    found = []
    for i, ratio in enumerate(ratios):
        pair = i + YEAR if i < YEAR else i - YEAR
        seasonal = ratios[pair] >= SEASONAL_PEAK_RATIO  # False for NaN
        if ratio >= SPIKE_RATIO and not seasonal:
            found.append(Spike(first + i, round(float(ratio), 1), first + pair))
    return tuple(found)


def _level_change(shares: np.ndarray, first: int) -> LevelChange | None:
    """A sharp lasting change of level, if the medians show one.

    Medians decide whether there is a step (one spike cannot fake it), but around a
    sharp step several splits give nearly the same medians: 6 months hold up to 2
    months of the other level unchanged. Means do move at the exact month, so they
    pick where the step is.
    """
    window = LEVEL_WINDOW_MONTHS
    found: list[tuple[float, LevelChange]] = []
    for t in range(window, len(shares) - window + 1):
        before, after = shares[t - window : t], shares[t : t + window]
        if np.median(before) <= 0 or np.median(after) <= 0:
            continue
        change = LevelChange(first + t, float(np.median(after) / np.median(before)))
        if change.factor >= LEVEL_RATIO:
            sharpness = abs(np.log(after.mean() / before.mean()))
            found.append((sharpness, change))
    return max(found, key=lambda item: item[0])[1] if found else None


def _level_warning(change: LevelChange) -> Msg:
    return Msg(
        "warn.level_change",
        {
            "month": series.month_label(change.month),
            "factor": change.factor,
            "dir": Msg("dir.higher" if change.ratio > 1 else "dir.lower"),
            "months": LEVEL_WINDOW_MONTHS,
        },
    )


def measure(data: Monthly, period: Span) -> Metrics:
    last, prev = series.yoy_spans(period)

    def total(values: np.ndarray, part: Span) -> int:
        return int(series.cut(data.span, values, part).sum())

    views_last, views_prev = total(data.views, last), total(data.views, prev)
    edition_last, edition_prev = total(data.edition, last), total(data.edition, prev)

    created = data.created_month
    blockers: list[Msg] = []  # problems that leave growth uncomputed
    if views_last == 0 and views_prev == 0:
        blockers.append(Msg("warn.no_views"))
    elif created is not None and created >= prev.first:
        # The creation month itself is partial: the base needs a full year after it.
        blockers.append(Msg("warn.base_incomplete", {"created": str(data.created)}))
    elif views_prev == 0:
        blockers.append(Msg("warn.zero_base"))
    measurable = not blockers

    pm_last = per_million(views_last, edition_last)
    pm_prev = per_million(views_prev, edition_prev)
    pm_growth = None
    if measurable and pm_last is not None and pm_prev is not None:
        pm_growth = growth_pct(pm_last, pm_prev)

    # Volume is judged on the 24 months the growth is computed from, whatever the
    # period: with --period 5y an article that was big years ago but is small now
    # would otherwise pass. Zeros before the article existed are left out.
    first_existing = prev.first
    if created is not None:
        first_existing = min(max(prev.first, created + 1), last.last)
    existing = series.cut(data.span, data.views, Span(first_existing, last.last))

    # Month by month over the same 24 months; measured growth means the article
    # existed for all of them.
    steadiness, spikes, level = None, (), None
    if pm_growth is not None:
        months = Span(prev.first, last.last)
        shares = _shares(
            series.cut(data.span, data.views, months),
            series.cut(data.span, data.edition, months),
        )
        steadiness = _steadiness(shares)
        spikes = _spikes(shares, months.first)
        level = _level_change(shares, months.first)
    level_warning = () if level is None else (_level_warning(level),)

    return Metrics(
        views_last_12m=views_last,
        views_prev_12m=views_prev,
        growth_pct=growth_pct(views_last, views_prev) if measurable else None,
        edition_growth_pct=growth_pct(edition_last, edition_prev),
        per_million_last_12m=None if pm_last is None else round(pm_last, 2),
        per_million_growth_pct=pm_growth,
        median_monthly_views=float(np.median(existing)),
        history_months=None if created is None else period.last - created,
        warnings=tuple(blockers) + data.warnings + level_warning,
        steadiness=steadiness,
        spikes=spikes,
        level_change=level,
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


def _month_by_month(trend: str, st: Steadiness) -> tuple[str, Msg]:
    """The seasonal Mann-Kendall read against the trend: "pass", "fail" or
    "disagree" (steady, but the other way than the 12-month totals)."""
    significant = st.p < STEADY_P_VALUE
    typical = (
        "" if st.typical_pct is None else Msg("conf.typical", {"pct": st.typical_pct})
    )
    if trend == FLAT:
        if not significant:
            params = {"higher": st.higher, "lower": st.lower, "of": YEAR}
            return "pass", Msg("conf.no_drift", params | {"typical": typical})
        up = st.higher > st.lower
        params = {
            "count": max(st.higher, st.lower),
            "of": YEAR,
            "dir": Msg("dir.above" if up else "dir.below"),
            "typical": typical,
            "threshold": TREND_THRESHOLD_PCT,
        }
        return "fail", Msg("conf.drift", params)
    rising = trend == RISING
    same, opposite = (st.higher, st.lower) if rising else (st.lower, st.higher)
    params = {"of": YEAR, "typical": typical}
    if significant and opposite > same:
        params |= {
            "total_dir": Msg("dir.grew" if rising else "dir.fell"),
            "count": opposite,
            "dir": Msg("dir.below" if rising else "dir.above"),
        }
        return "disagree", Msg("conf.disagree", params)
    params |= {"count": same, "dir": Msg("dir.above" if rising else "dir.below")}
    if significant:
        return "pass", Msg("conf.steady", params)
    return "fail", Msg("conf.unsteady", params)


def _spike_reason(spike: Spike) -> Msg:
    return Msg(
        "conf.spike",
        {
            "month": series.month_label(spike.month),
            "ratio": spike.ratio,
            "pair": series.month_label(spike.pair),
            # the pair is a year earlier for a spike in the last 12 months
            "effect": Msg("spike.last" if spike.month > spike.pair else "spike.base"),
        },
    )


# Reasons that say "(see warnings)" -> the warning they point to.
WARNING_OF = {
    "conf.level_change": "warn.level_change",
    "conf.redirects_capped": "warn.redirects_capped",
}


def _confidence(
    trend: str, m: Metrics, signs: Msg | None
) -> tuple[str, list[Msg], Msg | None]:
    """Base checks: enough volume, no one-off spikes, no data warnings, 24+ months
    of history. rising/falling add: steady month by month in the same direction,
    and absolute and per-million growth agreeing; flat adds: no steady drift.
    All pass -> high, one fails -> medium, two or more -> low.
    Low regardless: too little volume, under 12 months of history, a sharp lasting
    level change, or a month-by-month test steady the other way ("signals disagree").
    Reasons come in that order: what limited the confidence first. Also returns
    the first of them (Verdict.limit).
    """
    if trend == INSUFFICIENT:
        # measure() lists what kept growth from being computed first
        blocker = m.warnings[0] if m.warnings else Msg("conf.insufficient")
        return LOW, [Msg("conf.insufficient")], blocker
    caps: list[Msg] = []  # each alone makes it low
    failed: list[tuple[Msg, ...]] = []  # one entry per failed check
    if m.median_monthly_views < MIN_MEDIAN_MONTHLY_VIEWS:
        params = {
            "median": m.median_monthly_views,
            "min_views": MIN_MEDIAN_MONTHLY_VIEWS,
        }
        caps.append(Msg("conf.volume_low", params))
    if m.history_months is not None and m.history_months < MIN_TREND_MONTHS:
        caps.append(Msg("conf.history_too_short", {"months": m.history_months}))
    if m.level_change is not None:
        caps.append(Msg("conf.level_change"))
    outcome, month_by_month = _month_by_month(trend, m.steadiness)
    if outcome == "disagree":
        caps.append(month_by_month)
    if m.spikes:
        failed.append(tuple(_spike_reason(spike) for spike in m.spikes))
    if outcome == "fail":
        failed.append((month_by_month,))
    directional = trend in (RISING, FALLING)
    if directional and signs is not None:
        failed.append((signs,))
    if (
        m.history_months is not None
        and MIN_TREND_MONTHS <= m.history_months < MIN_HISTORY_MONTHS
    ):
        params = {"months": m.history_months, "min_months": MIN_HISTORY_MONTHS}
        failed.append((Msg("conf.history_short", params),))
    # Only the newest redirects are left out, and a rename makes the newest one:
    # views of a former title may be missing from the base year. When they are, the
    # level check above catches the jump; otherwise it is one step down.
    if any(w.key == "warn.redirects_capped" for w in m.warnings):
        failed.append((Msg("conf.redirects_capped"),))

    passed = [month_by_month] if outcome == "pass" else []
    limits = caps + [msg for check in failed for msg in check]
    reasons = limits + passed
    if limits:
        limit = limits[0]
        # must_say has no warnings to point to: it gets the warning itself
        if limit.key in WARNING_OF:
            limit = next(w for w in m.warnings if w.key == WARNING_OF[limit.key])
        return (LOW if caps or len(failed) >= 2 else MEDIUM), reasons, limit
    checks = Msg(
        "conf.checks_passed",
        {
            "min_views": MIN_MEDIAN_MONTHLY_VIEWS,
            "min_months": MIN_HISTORY_MONTHS,
            "signs": Msg("conf.signs_agree") if directional else "",
        },
    )
    return HIGH, [*passed, checks], None


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
    confidence, checks, limit = _confidence(trend, m, signs)
    return Verdict(trend, confidence, tuple(reasons + checks), m.warnings, limit)


# --- ranking ----------------------------------------------------------------

RANK_FIELDS = {
    "growth": "per_million_growth_pct",
    "share": "per_million_last_12m",
    "size": "views_last_12m",
}


def rank(entries: list[tuple[str, Metrics, Verdict]], by: str) -> list[tuple[str, Msg]]:
    """Languages in order, each with why. Low confidence never ranks above a
    confident result; an unknown value goes after known ones; ties keep --langs order.

    `why` names the metric and its value as in the JSON. A low result says so when
    confident results come first, and names those it outscores: they rank above it
    only because of the confidence, which the user would not guess from the order.
    """
    field = RANK_FIELDS[by]

    def key(entry: tuple[str, Metrics, Verdict]) -> tuple:
        value = getattr(entry[1], field)
        return (entry[2].confidence == LOW, value is None, -(value or 0))

    ordered = sorted(entries, key=key)
    confident = [
        (lang, getattr(metrics, field))
        for lang, metrics, verdict in ordered
        if verdict.confidence != LOW
    ]
    order = []
    for lang, metrics, verdict in ordered:
        value = getattr(metrics, field)
        if value is None:
            why = Msg("rank.unknown", {"metric": Msg(f"rank_by.{by}")})
        else:
            why = Msg(f"rank.{by}", {"value": value, "confidence": verdict.confidence})
            if verdict.confidence == LOW and confident:
                outscored = [c for c, v in confident if v is not None and v < value]
                if outscored:
                    params = {"base": why, "langs": ", ".join(outscored)}
                    why = Msg("rank.low_outscores", params)
                else:
                    why = Msg("rank.low_after", {"base": why})
        order.append((lang, why))
    return order
