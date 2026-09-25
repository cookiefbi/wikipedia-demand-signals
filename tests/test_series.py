"""Calendar, zero-fill, monthly sums and period cuts on synthetic series."""

import datetime as dt

import numpy as np
import pytest
from wds_lib import WdsError, series
from wds_lib.series import Span

D = dt.date


def m(label: str) -> int:
    return series.parse_month(label, "--test")


def span(first: str, last: str) -> Span:
    return Span(m(first), m(last))


def items(counts: dict[dt.date, int]) -> list[dict]:
    """Pageviews API items; like the API, days without views are simply absent."""
    return [
        {"timestamp": day.strftime("%Y%m%d00"), "views": views}
        for day, views in sorted(counts.items())
    ]


# --- calendar ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (D(2026, 9, 25), "2026-08"),
        # On the 1st the last day of the previous month may not be published yet.
        (D(2026, 10, 1), "2026-08"),
        (D(2026, 10, 2), "2026-09"),
        (D(2026, 1, 1), "2025-11"),
        (D(2026, 1, 2), "2025-12"),
        (D(2024, 3, 1), "2024-01"),  # leap February not complete yet
        (D(2024, 3, 2), "2024-02"),
    ],
)
def test_last_full_month_accounts_for_the_publication_lag(today, expected):
    assert series.month_label(series.last_full_month(today)) == expected


def test_fetch_window_is_72_full_months_ending_last_full_month():
    window = series.fetch_window(D(2026, 9, 25))
    assert window.as_dict() == {"from": "2020-09", "to": "2026-08", "months": 72}
    assert (window.start, window.end) == (D(2020, 9, 1), D(2026, 8, 31))


def test_the_window_stays_the_same_for_the_whole_month():
    windows = {series.fetch_window(D(2026, 9, day)) for day in range(2, 31)}
    windows.add(series.fetch_window(D(2026, 10, 1)))
    assert len(windows) == 1


def test_month_helpers():
    assert series.last_day(m("2024-02")) == D(2024, 2, 29)
    assert series.last_day(m("2025-12")) == D(2025, 12, 31)
    assert series.first_day(m("2026-01")) == D(2026, 1, 1)
    assert series.month_label(m("2025-12") + 1) == "2026-01"
    assert series.month_of(D(2026, 1, 31)) == m("2026-01")


@pytest.mark.parametrize("bad", ["2025", "2025-13", "25-01", "January 2025", ""])
def test_bad_month_is_an_error_with_a_hint(bad):
    with pytest.raises(WdsError) as info:
        series.parse_month(bad, "--from")
    assert "YYYY-MM" in info.value.hint


# --- zero-fill and monthly sums --------------------------------------------


def test_days_missing_from_the_api_become_zeros():
    daily = series.daily_counts(
        items({D(2026, 1, 1): 5, D(2026, 1, 3): 7}), D(2026, 1, 1), D(2026, 1, 4)
    )
    assert daily.tolist() == [5, 0, 7, 0]


def test_404_means_all_zeros():
    assert series.daily_counts(None, D(2026, 1, 1), D(2026, 1, 3)).tolist() == [0, 0, 0]


def test_items_outside_the_range_are_ignored():
    daily = series.daily_counts(
        items({D(2025, 12, 31): 99, D(2026, 1, 1): 1, D(2026, 1, 2): 99}),
        D(2026, 1, 1),
        D(2026, 1, 1),
    )
    assert daily.tolist() == [1]


def test_month_boundaries_go_to_the_right_month():
    start, end = D(2024, 1, 1), D(2024, 3, 31)
    daily = series.daily_counts(
        items(
            {
                D(2024, 1, 31): 1,
                D(2024, 2, 1): 10,
                D(2024, 2, 29): 100,  # leap day
                D(2024, 3, 1): 1000,
            }
        ),
        start,
        end,
    )
    months, totals = series.monthly_totals(daily, start)
    assert months == span("2024-01", "2024-03")
    assert totals.tolist() == [1, 110, 1000]


def test_incomplete_trailing_month_is_dropped():
    start, end = D(2026, 7, 1), D(2026, 9, 24)  # September is still running
    daily = np.ones((end - start).days + 1, dtype=np.int64)
    months, totals = series.monthly_totals(daily, start)
    assert months == span("2026-07", "2026-08")
    assert totals.tolist() == [31, 31]


def test_incomplete_leading_month_is_dropped():
    start, end = D(2026, 6, 15), D(2026, 8, 31)
    daily = np.ones((end - start).days + 1, dtype=np.int64)
    months, totals = series.monthly_totals(daily, start)
    assert months == span("2026-07", "2026-08")
    assert totals.tolist() == [31, 31]


def test_a_range_ending_on_the_last_day_keeps_its_last_month():
    start = D(2026, 2, 1)
    daily = np.ones(28, dtype=np.int64)
    assert series.monthly_totals(daily, start)[1].tolist() == [28]


def test_full_window_sums_to_the_daily_total():
    window = series.fetch_window(D(2026, 9, 25))
    rng = np.random.default_rng(1)
    daily = rng.integers(0, 50, (window.end - window.start).days + 1)
    months, totals = series.monthly_totals(daily, window.start)
    assert months == window
    assert len(totals) == 72
    assert totals.sum() == daily.sum()


def test_first_month_with_views():
    months = span("2025-01", "2025-06")
    assert series.first_month_with_views(months, np.array([0, 0, 3, 0, 5, 1])) == m(
        "2025-03"
    )
    assert series.first_month_with_views(months, np.zeros(6, dtype=np.int64)) is None


# --- periods ----------------------------------------------------------------

WINDOW = span("2020-09", "2026-08")


@pytest.mark.parametrize(
    ("period", "first", "months"),
    [
        (None, "2024-09", 24),  # default 24m
        ("12m", "2025-09", 12),
        ("24m", "2024-09", 24),
        ("36m", "2023-09", 36),
        ("5y", "2021-09", 60),
    ],
)
def test_period_ends_with_the_last_full_month(period, first, months):
    chosen = series.resolve_period(WINDOW, period)
    assert chosen == Span(m(first), m("2026-08"))
    assert chosen.months == months


def test_every_period_including_its_growth_base_fits_the_window():
    for period in series.PERIODS:
        needed = series.analysis_span(series.resolve_period(WINDOW, period))
        assert WINDOW.first <= needed.first and needed.last == WINDOW.last


def test_yoy_compares_last_12_months_with_the_12_before():
    last, prev = series.yoy_spans(span("2024-09", "2026-08"))
    assert (last, prev) == (span("2025-09", "2026-08"), span("2024-09", "2025-08"))


def test_12m_period_takes_its_growth_base_from_before_the_period():
    period = series.resolve_period(WINDOW, "12m")
    assert series.analysis_span(period) == span("2024-09", "2026-08")


def test_from_to():
    assert series.resolve_period(WINDOW, None, "2022-01", "2023-12") == span(
        "2022-01", "2023-12"
    )


def test_only_from_runs_to_the_last_full_month():
    assert series.resolve_period(WINDOW, None, "2025-01") == span("2025-01", "2026-08")


def test_only_to_takes_the_default_length():
    assert series.resolve_period(WINDOW, None, None, "2025-12") == span(
        "2024-01", "2025-12"
    )


@pytest.mark.parametrize(
    ("args", "in_error", "in_hint"),
    [
        (("24m", "2025-01", None), "cannot be combined", "either"),
        ((None, "2025-01", "2026-09"), "not complete yet", "--to 2026-08"),
        ((None, "2026-01", "2026-08"), "shorter than 12 months", "at least 12"),
        ((None, "2026-01", "2025-01"), "after --to", "swap"),
        # 2021-01..2021-12 would need its growth base from 2020-01.
        # 2021-01..2021-12 would need its growth base from 2020-01.
        (
            (None, "2021-01", "2021-12"),
            "needs data from 2020-01",
            "earliest --to is 2022-08",
        ),
        (
            (None, "2020-01", "2022-12"),
            "before the data window",
            "earliest --from is 2020-09",
        ),
    ],
)
def test_bad_periods_are_errors_with_hints(args, in_error, in_hint):
    with pytest.raises(WdsError) as info:
        series.resolve_period(WINDOW, *args)
    assert in_error in info.value.error
    assert in_hint in info.value.hint


def test_cut():
    months = span("2025-01", "2025-12")
    values = np.arange(12)
    assert series.cut(months, values, span("2025-11", "2025-12")).tolist() == [10, 11]
    with pytest.raises(WdsError):
        series.cut(months, values, span("2024-12", "2025-01"))
