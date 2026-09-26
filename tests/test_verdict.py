"""Metrics, trend, confidence and ranking on synthetic monthly series."""

import numpy as np
import pytest
from wds_lib import i18n, series, verdict
from wds_lib.i18n import Msg
from wds_lib.series import YEAR, Span
from wds_lib.verdict import HIGH, LOW, MEDIUM, Metrics, Monthly, Steadiness

PERIOD = Span(series.parse_month("2024-09", "-"), series.parse_month("2026-08", "-"))
SPAN = series.analysis_span(PERIOD)  # 24 months: the base is inside the period
LAST, PREV = series.yoy_spans(PERIOD)
EDITION = 100_000_000  # views of the whole edition per month
# A school year, September first: the shape of uk "Астрономія" (high September,
# low summer), as a multiple of an ordinary month.
SCHOOL_YEAR = [3.0, 1.3, 1.3, 1.3, 1.2, 1.1, 1.0, 1.0, 0.9, 0.45, 0.3, 0.35]


def monthly(views, edition=EDITION, created=None, warnings=()) -> Monthly:
    """created: a month number; the first edit is then on the 14th of that month."""
    views = np.asarray(np.broadcast_to(views, SPAN.months), dtype=np.int64)
    return Monthly(
        span=SPAN,
        views=views,
        redirects=np.zeros_like(views),
        edition=np.asarray(np.broadcast_to(edition, SPAN.months), dtype=np.int64),
        created=None if created is None else series.first_day(created).replace(day=14),
        warnings=warnings,
    )


def two_years(prev_level, last_level) -> np.ndarray:
    return np.array([prev_level] * 12 + [last_level] * 12)


def run(data: Monthly):
    metrics = verdict.measure(data, PERIOD)
    return metrics, verdict.judge(metrics)


def reasons(v) -> list[str]:
    return i18n.texts(list(v.reasons))


def test_growth_pct_and_per_million():
    assert verdict.growth_pct(120, 100) == 20.0
    assert verdict.growth_pct(1, 0) is None
    assert verdict.per_million(5, 10_000_000) == 0.5
    assert verdict.per_million(5, 0) is None


@pytest.mark.parametrize(
    ("pm_growth", "trend"),
    [
        (10.0, "rising"),
        (9.9, "flat"),
        (0.0, "flat"),
        (-9.9, "flat"),
        (-10.0, "falling"),
        (None, "insufficient_data"),
    ],
)
def test_trend_thresholds(pm_growth, trend):
    assert verdict.trend_of(pm_growth) == trend


def test_stable_topic_is_flat_with_high_confidence():
    m, v = run(monthly(1000))
    assert (m.views_last_12m, m.views_prev_12m, m.growth_pct) == (12000, 12000, 0.0)
    assert m.per_million_last_12m == 10.0
    assert (v.trend, v.confidence) == ("flat", HIGH)
    assert "within ±10%" in reasons(v)[0]


def test_steady_growth_is_rising():
    m, v = run(monthly(np.linspace(1000, 2000, SPAN.months).round()))
    assert m.growth_pct > 10
    assert (v.trend, v.confidence) == ("rising", HIGH)
    assert reasons(v)[1].startswith(
        "steady: 12 of 12 months are above the same month a year earlier"
    )


def test_pure_seasonality_is_flat():
    season = 1000 + 500 * np.sin(np.arange(SPAN.months) * 2 * np.pi / 12)
    m, v = run(monthly(season.round()))
    assert abs(m.per_million_growth_pct) < 1
    assert (v.trend, v.confidence) == ("flat", HIGH)


def test_share_decides_the_trend_not_absolute_views():
    # The article lost 5 %, the whole edition 20 %: the topic's share grew.
    m, v = run(monthly(two_years(1000, 950), two_years(EDITION, EDITION * 0.8)))
    assert (m.growth_pct, m.edition_growth_pct) == (-5.0, -20.0)
    assert m.per_million_growth_pct == 18.8
    assert v.trend == "rising"
    # Opposite directions is one failed check: medium, explained in plain words.
    assert v.confidence == MEDIUM
    assert "absolute views fell 5.0% while the whole edition fell 20.0%" in reasons(v)


def test_opposite_directions_on_a_flat_topic_are_explained_but_not_penalised():
    # Months go both ways against a year earlier, so there is no steady drift either.
    views = two_years(1000, 1000)
    views[YEAR:] = [1070, 970] * 6
    _, v = run(monthly(views, two_years(EDITION, EDITION * 1.05)))
    assert v.trend == "flat"
    assert v.confidence == HIGH
    assert any("grew 2.0% while the whole edition grew 5.0%" in r for r in reasons(v))


def test_small_volume_caps_confidence_at_low():
    _, v = run(monthly(two_years(100, 200)))
    assert v.trend == "rising"
    assert v.confidence == LOW
    assert any("below 300" in r for r in reasons(v))


def test_young_article_gets_no_growth_and_a_warning():
    created = PREV.first + 3  # base year only partly covered
    views = np.where(np.arange(SPAN.months) > 3, 1000, 0)
    m, v = run(monthly(views, created=created))
    assert m.growth_pct is None and m.per_million_growth_pct is None
    assert (v.trend, v.confidence) == ("insufficient_data", LOW)
    warning = i18n.texts(list(v.warnings))[0]
    assert warning.startswith("article created 2024-12-14: the 12 months before")


def test_article_created_in_the_first_base_month_has_an_incomplete_base():
    # The creation month is partial, so the base misses part of its first month.
    m, v = run(monthly(1000, created=PREV.first))
    assert m.growth_pct is None
    assert "article created 2024-09-14" in i18n.texts(list(v.warnings))[0]


def test_article_created_just_before_the_base_year_is_measured():
    m, _ = run(monthly(1000, created=PREV.first - 1))
    assert m.growth_pct == 0.0
    assert m.history_months == PERIOD.last - PREV.first + 1


def test_left_out_redirects_warn_and_lower_confidence_one_step():
    capped = Msg("warn.redirects_capped", {"title": "A", "total": 12, "counted": 10})
    m, v = run(monthly(two_years(1000, 1200), warnings=(capped,)))
    assert m.growth_pct == 20.0  # a warning about the data, not a blocker
    assert i18n.texts(list(v.warnings)) == [
        (
            "'A': 12 redirects lead to it, only 10 are counted (redirects to the "
            "whole article first, oldest first): views may be undercounted"
        )
    ]
    # The newest redirects are the ones left out, and a rename makes the newest.
    assert (v.trend, v.confidence) == ("rising", MEDIUM)
    assert (
        "not every redirect is counted (see warnings): views may be undercounted"
        in reasons(v)
    )


def test_no_views_at_all():
    _, v = run(monthly(0))
    assert v.trend == "insufficient_data"
    assert i18n.texts(list(v.warnings)) == ["no views recorded in the analysed months"]


def test_growth_from_zero_is_not_computed():
    m, v = run(monthly(two_years(0, 500)))
    assert m.growth_pct is None
    assert v.trend == "insufficient_data"
    assert "growth from zero" in i18n.texts(list(v.warnings))[0]


def test_volume_median_ignores_months_before_creation():
    views = np.where(np.arange(SPAN.months) >= 12, 400, 0)
    m, _ = run(monthly(views, created=LAST.first - 1))
    assert m.median_monthly_views == 400


def test_volume_is_judged_on_the_growth_months_not_the_whole_period():
    # Big three years ago, small now: a 5-year period must not hide that.
    period = Span(PERIOD.last - 59, PERIOD.last)
    views = np.array([5000] * 36 + [200] * 24)
    data = Monthly(
        span=period,
        views=views,
        redirects=np.zeros_like(views),
        edition=np.full(60, EDITION),
        created=None,
    )
    m = verdict.measure(data, period)
    assert m.median_monthly_views == 200
    assert verdict.judge(m).confidence == LOW


def metrics(**changes) -> Metrics:
    base = {
        "views_last_12m": 24000,
        "views_prev_12m": 12000,
        "growth_pct": 100.0,
        "edition_growth_pct": 0.0,
        "per_million_last_12m": 20.0,
        "per_million_growth_pct": 100.0,
        "median_monthly_views": 1500.0,
        "history_months": None,
        "warnings": (),
        "steadiness": Steadiness(higher=12, lower=0, p=0.001, typical_pct=100.0),
    }
    return Metrics(**(base | changes))


@pytest.mark.parametrize(
    ("history", "confidence"), [(None, HIGH), (30, HIGH), (20, MEDIUM), (11, LOW)]
)
def test_history_length(history, confidence):
    assert verdict.judge(metrics(history_months=history)).confidence == confidence


def test_two_failed_checks_are_low():
    v = verdict.judge(metrics(history_months=20, growth_pct=-1.0))
    assert v.confidence == LOW


# --- month by month: steadiness, spikes, level changes (SPEC 4 and 7) ---------


def noisy(levels, sd: float = 0.05, seed: int = 0) -> np.ndarray:
    """Views with multiplicative noise, the same on every run."""
    rng = np.random.default_rng(seed)
    return (
        np.broadcast_to(levels, SPAN.months) * rng.lognormal(0, sd, SPAN.months)
    ).round()


def flat_with_ripple() -> np.ndarray:
    """1000 +- 3%, the same in both years: no month differs from a year earlier."""
    return np.array([1030, 970] * YEAR)


def test_flat_series_with_one_spike_is_low_and_names_the_spike():
    views = flat_with_ripple()
    views[16] *= 3  # 2026-01, in the last 12 months; 2025-01 had no such peak
    m, v = run(monthly(views))
    # The trend follows year-over-year growth alone, so one month can make it
    # "rising"; the confidence and its reasons say why not to trust it.
    assert (v.trend, v.confidence) == ("rising", LOW)
    assert [series.month_label(spike.month) for spike in m.spikes] == ["2026-01"]
    assert reasons(v)[1:] == [
        (
            "one-off spike in 2026-01: 3.2x the months around it, with no such peak "
            "in 2025-01; it inflates the last 12 months, so growth looks higher"
        ),
        (
            "not steady: only 1 of 12 months are above the same month a year earlier "
            "(typical month +0.0%): the change may rest on a few months"
        ),
    ]


def test_spike_in_the_base_year_makes_growth_look_lower():
    views = flat_with_ripple()
    views[4] *= 3  # 2025-01, in the 12 months before the last 12
    _, v = run(monthly(views))
    assert (v.trend, v.confidence) == ("falling", LOW)
    assert reasons(v)[1].endswith(
        "it inflates the 12 months before the last 12, so growth looks lower"
    )


def test_linear_growth_with_noise_is_rising_with_high_confidence():
    m, v = run(monthly(noisy(1000 * 1.3 ** (np.arange(SPAN.months) / YEAR))))
    assert (v.trend, v.confidence) == ("rising", HIGH)
    assert (m.steadiness.higher, m.spikes, m.level_change) == (12, (), None)


def test_school_year_is_flat_and_its_september_is_the_season_not_a_spike():
    views = noisy(1000 * np.tile(SCHOOL_YEAR, 2))
    m, v = run(monthly(views))
    assert (v.trend, v.confidence) == ("flat", HIGH)
    assert m.spikes == ()
    # The second September stands out from its summer neighbours: a candidate. The
    # first, with only October-December beside it, stays below SPIKE_RATIO but
    # above SEASONAL_PEAK_RATIO: enough to make the pair the season.
    ratios = verdict._peak_ratios(views)  # the edition is constant: same ratios
    assert ratios[YEAR] >= verdict.SPIKE_RATIO
    assert verdict.SEASONAL_PEAK_RATIO <= ratios[0] < verdict.SPIKE_RATIO


def test_level_jump_warns_and_makes_confidence_low():
    step = 8  # 2025-05: from then on 3.5 times the views
    m, v = run(monthly(noisy(np.where(np.arange(SPAN.months) < step, 1000, 3500))))
    assert m.level_change.month == SPAN.first + step
    assert v.confidence == LOW
    [warning] = i18n.texts(list(v.warnings))
    assert warning.startswith(
        "sharp lasting change of level around 2025-05: views per million are 3."
    )
    assert "x higher in the 6 months from then on than in the 6 before" in warning
    assert reasons(v)[1].startswith("a sharp lasting change of level (see warnings)")


def test_steady_growth_is_not_a_level_change():
    # +300% a year: 6-month medians move about 2x, not 3x
    m, _ = run(monthly(noisy(1000 * 4 ** (np.arange(SPAN.months) / YEAR))))
    assert m.level_change is None


def test_signals_disagree_when_one_month_carries_the_year():
    views = two_years(1000, 950)
    views[20] = 2900  # 2026-05: the year total grows only because of it
    _, v = run(monthly(views))
    assert (v.trend, v.confidence) == ("rising", LOW)
    assert reasons(v)[1] == (
        "signals disagree: the 12-month total grew, but 11 of 12 months are below "
        "the same month a year earlier (typical month -5.0%)"
    )
    assert reasons(v)[2].startswith("one-off spike in 2026-05")


def test_flat_with_a_slow_steady_drift_is_medium():
    _, v = run(monthly(two_years(1000, 950)))
    assert (v.trend, v.confidence) == ("flat", MEDIUM)
    assert reasons(v)[1] == (
        "slow steady drift: 12 of 12 months are below the same month a year earlier "
        "(typical month -5.0%), though the 12-month total moved less than 10%"
    )


def test_rising_that_is_not_steady_is_medium():
    views = two_years(1000, 1000)
    views[YEAR : YEAR + 6] = 1300
    views[YEAR + 6 :] = 940
    m, v = run(monthly(views))
    assert m.per_million_growth_pct == 12.0
    assert (v.trend, v.confidence) == ("rising", MEDIUM)
    assert reasons(v)[1].startswith("not steady: only 6 of 12 months are above")


# --- ranking ----------------------------------------------------------------


def entry(lang, confidence, growth, share=10.0, size=1000):
    m = metrics(
        per_million_growth_pct=growth,
        per_million_last_12m=share,
        views_last_12m=size,
    )
    trend = verdict.trend_of(growth)
    return lang, m, verdict.Verdict(trend, confidence, (), ())


def test_low_confidence_never_ranks_first():
    order = verdict.rank(
        [entry("sk", LOW, 80.0), entry("cs", MEDIUM, 12.0), entry("pl", HIGH, -5.0)],
        "growth",
    )
    assert [lang for lang, _ in order] == ["cs", "pl", "sk"]
    why = {lang: w.render() for lang, w in order}
    assert why["cs"] == "views per million +12.0% year over year, medium confidence"
    # The order alone would hide that sk has the highest value: why says it.
    assert why["sk"] == (
        "views per million +80.0% year over year, low confidence: higher than cs, "
        "pl by this measure, but listed after confident results"
    )


def test_low_result_names_only_the_confident_ones_it_outscores():
    order = verdict.rank(
        [entry("sk", LOW, 20.0), entry("cs", HIGH, 30.0), entry("pl", HIGH, 10.0)],
        "growth",
    )
    why = {lang: w.render() for lang, w in order}
    assert why["sk"].endswith(
        "low confidence: higher than pl by this measure, but listed after confident "
        "results"
    )
    order = verdict.rank([entry("sk", LOW, 5.0), entry("cs", HIGH, 30.0)], "growth")
    last = order[1][1].render()
    assert last.endswith("low confidence: listed after confident results")


def test_all_low_results_are_ranked_by_value_without_a_note():
    order = verdict.rank([entry("sk", LOW, -30.0), entry("cs", LOW, 5.0)], "growth")
    assert [(lang, w.render()) for lang, w in order] == [
        ("cs", "views per million +5.0% year over year, low confidence"),
        ("sk", "views per million -30.0% year over year, low confidence"),
    ]


def test_unknown_value_goes_last():
    order = verdict.rank([entry("uk", LOW, None), entry("sk", LOW, -30.0)], "growth")
    assert [lang for lang, _ in order] == ["sk", "uk"]
    assert order[1][1].render() == (
        "growth of share not measured (see warnings): listed last"
    )


@pytest.mark.parametrize(
    ("by", "first", "why"),
    [
        (
            "share",
            "pl",
            (
                "9.00 views per million edition views in the last 12 months, high "
                "confidence"
            ),
        ),
        ("size", "cs", "90,000 views in the last 12 months, high confidence"),
    ],
)
def test_rank_by_share_and_size(by, first, why):
    entries = [
        entry("cs", HIGH, 1.0, share=5.0, size=90_000),
        entry("pl", HIGH, 2.0, share=9.0, size=50_000),
    ]
    lang, msg = verdict.rank(entries, by)[0]
    assert (lang, msg.render()) == (first, why)


def test_ties_keep_the_requested_order():
    entries = [entry("uk", HIGH, 5.0), entry("pl", HIGH, 5.0), entry("cs", HIGH, 5.0)]
    assert [lang for lang, _ in verdict.rank(entries, "growth")] == ["uk", "pl", "cs"]


# --- messages ---------------------------------------------------------------


def test_nested_messages_render_in_the_same_language():
    msg = Msg("rank.unknown", {"metric": Msg("rank_by.size")})
    assert msg.render() == "audience size not measured (see warnings): listed last"


def test_every_caveat_renders():
    assert all(i18n.render(key) for key in i18n.CAVEATS)
