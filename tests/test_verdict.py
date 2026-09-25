"""Metrics, trend, confidence and ranking on synthetic monthly series."""

import numpy as np
import pytest
from wds_lib import i18n, series, verdict
from wds_lib.i18n import Msg
from wds_lib.series import Span
from wds_lib.verdict import HIGH, LOW, MEDIUM, Metrics, Monthly

PERIOD = Span(series.parse_month("2024-09", "-"), series.parse_month("2026-08", "-"))
SPAN = series.analysis_span(PERIOD)  # 24 months: the base is inside the period
LAST, PREV = series.yoy_spans(PERIOD)
EDITION = 100_000_000  # views of the whole edition per month
P0_CAP = verdict.CONFIDENCE_CAP


@pytest.fixture(autouse=True)
def without_cap(monkeypatch):
    """The rules themselves; the temporary P0 cap has its own test below."""
    monkeypatch.setattr(verdict, "CONFIDENCE_CAP", None)


def monthly(views, edition=EDITION, created=None) -> Monthly:
    return Monthly(
        span=SPAN,
        views=np.asarray(np.broadcast_to(views, SPAN.months), dtype=np.int64),
        edition=np.asarray(np.broadcast_to(edition, SPAN.months), dtype=np.int64),
        created=created,
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


def test_pure_seasonality_is_flat():
    season = 1000 + 500 * np.sin(np.arange(SPAN.months) * 2 * np.pi / 12)
    m, v = run(monthly(season.round()))
    assert abs(m.per_million_growth_pct) < 1
    assert v.trend == "flat"


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
    _, v = run(monthly(two_years(1000, 1020), two_years(EDITION, EDITION * 1.05)))
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
    assert "views start only in 2024-12" in warning


def test_article_created_just_before_the_base_year_is_measured():
    m, _ = run(monthly(1000, created=PREV.first - 1))
    assert m.growth_pct == 0.0
    assert m.history_months == PERIOD.last - PREV.first + 1


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
    data = Monthly(period, views, np.full(60, EDITION), created=None)
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
    }
    return Metrics(**(base | changes))


@pytest.mark.parametrize(
    ("history", "confidence"), [(None, HIGH), (30, HIGH), (20, MEDIUM), (11, LOW)]
)
def test_history_length(history, confidence):
    assert verdict.judge(metrics(history_months=history)).confidence == confidence


def test_p0_caps_confidence_at_medium_and_says_why(monkeypatch):
    monkeypatch.setattr(verdict, "CONFIDENCE_CAP", P0_CAP)
    assert P0_CAP == MEDIUM
    _, v = run(monthly(np.linspace(1000, 2000, SPAN.months).round()))
    assert (v.trend, v.confidence) == ("rising", MEDIUM)
    assert reasons(v)[-1] == (
        "capped at medium until trend stability and one-off spikes are checked"
    )
    _, low = run(monthly(two_years(100, 200)))
    assert low.confidence == LOW  # the cap never raises a level


def test_two_failed_checks_are_low():
    v = verdict.judge(metrics(history_months=20, growth_pct=-1.0))
    assert v.confidence == LOW


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
    assert why["cs"] == "per-million growth +12.0%, medium confidence"
    assert why["sk"].endswith("listed after confident results")


def test_unknown_value_goes_last():
    order = verdict.rank([entry("uk", LOW, None), entry("sk", LOW, -30.0)], "growth")
    assert [lang for lang, _ in order] == ["sk", "uk"]


@pytest.mark.parametrize(("by", "first"), [("share", "pl"), ("size", "cs")])
def test_rank_by_share_and_size(by, first):
    entries = [
        entry("cs", HIGH, 1.0, share=5.0, size=90_000),
        entry("pl", HIGH, 2.0, share=9.0, size=50_000),
    ]
    assert verdict.rank(entries, by)[0][0] == first


def test_ties_keep_the_requested_order():
    entries = [entry("uk", HIGH, 5.0), entry("pl", HIGH, 5.0), entry("cs", HIGH, 5.0)]
    assert [lang for lang, _ in verdict.rank(entries, "growth")] == ["uk", "pl", "cs"]


# --- messages ---------------------------------------------------------------


def test_nested_messages_render_in_the_same_language():
    msg = Msg("rank.low_after", {"base": Msg("rank.unknown")})
    assert (
        msg.render()
        == "no value to rank by: listed last; listed after confident results"
    )


def test_every_caveat_renders():
    assert all(i18n.render(key) for key in i18n.CAVEATS)
