"""Daily pageviews -> clean monthly series: calendar, zero-fill, month sums, periods.

Months are plain integers (year * 12 + month - 1), so "72 months back" or "the 12
months before the last 12" is integer arithmetic instead of date juggling.
"""

import datetime as dt
import re
from dataclasses import dataclass

import numpy as np

from wds_lib import WdsError

# Data for day D appears in the API during day D+1 (UTC): on 2026-09-25 the latest
# daily point was 2026-09-24. Two days back is the latest day that is surely there,
# so on the 1st the previous month still counts as incomplete.
DATA_LAG_DAYS = 2

# Download window: 5 years (the longest --period) + 12 months, so that
# year-over-year growth has a base for every period. The window only moves once a
# month: same URLs all month long, and every --period is cut from the same cache.
WINDOW_MONTHS = 72

# Year-over-year growth compares the last 12 months with the 12 months before them.
YEAR = 12

PERIODS = {"12m": 12, "24m": 24, "36m": 36, "5y": 60}
DEFAULT_PERIOD = "24m"


def month_of(day: dt.date) -> int:
    return day.year * 12 + day.month - 1


def first_day(month: int) -> dt.date:
    return dt.date(month // 12, month % 12 + 1, 1)


def last_day(month: int) -> dt.date:
    return first_day(month + 1) - dt.timedelta(days=1)


def month_label(month: int) -> str:
    return f"{month // 12:04d}-{month % 12 + 1:02d}"


def parse_month(text: str, option: str) -> int:
    match = re.fullmatch(r"(\d{4})-(\d{2})", text.strip())
    if not match or not 1 <= int(match[2]) <= 12:
        raise WdsError(
            f"{option} must be a month like 2025-01, got '{text}'",
            hint=f"pass {option} YYYY-MM, or use --period 12m|24m|36m|5y instead",
        )
    return int(match[1]) * 12 + int(match[2]) - 1


@dataclass(frozen=True)
class Span:
    """Consecutive calendar months, both ends included."""

    first: int
    last: int

    @property
    def months(self) -> int:
        return self.last - self.first + 1

    @property
    def start(self) -> dt.date:
        return first_day(self.first)

    @property
    def end(self) -> dt.date:
        return last_day(self.last)

    def shifted(self, months: int) -> "Span":
        return Span(self.first + months, self.last + months)

    def as_dict(self) -> dict:
        return {
            "from": month_label(self.first),
            "to": month_label(self.last),
            "months": self.months,
        }


def last_full_month(today: dt.date) -> int:
    """Latest month whose last day is surely published."""
    surely_published = today - dt.timedelta(days=DATA_LAG_DAYS)
    return month_of(surely_published + dt.timedelta(days=1)) - 1


def fetch_window(today: dt.date) -> Span:
    last = last_full_month(today)
    return Span(last - WINDOW_MONTHS + 1, last)


def yoy_spans(period: Span) -> tuple[Span, Span]:
    """(last 12 months, the 12 months before them), both ending at the period's end."""
    last = Span(period.last - YEAR + 1, period.last)
    return last, last.shifted(-YEAR)


def analysis_span(period: Span) -> Span:
    """Every month any number is computed from: the period plus the growth base."""
    return Span(min(period.first, yoy_spans(period)[1].first), period.last)


def resolve_period(
    window: Span,
    period: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> Span:
    """--period or --from/--to -> months, checked against the downloaded window.

    Dates are computed here, never by the agent: "last two years" is --period 24m.
    """
    if period and (date_from or date_to):
        raise WdsError(
            "--period cannot be combined with --from/--to",
            hint="use either --period 24m or --from YYYY-MM --to YYYY-MM",
        )
    if not (date_from or date_to):
        months = PERIODS[period or DEFAULT_PERIOD]
        return Span(window.last - months + 1, window.last)

    last = parse_month(date_to, "--to") if date_to else window.last
    if date_from:
        first = parse_month(date_from, "--from")
    else:
        first = last - PERIODS[DEFAULT_PERIOD] + 1
    if last > window.last:
        raise WdsError(
            f"--to {month_label(last)} is not complete yet; "
            f"the latest complete month is {month_label(window.last)}",
            hint=f"use --to {month_label(window.last)} or drop --to",
        )
    if first > last:
        raise WdsError(
            f"--from {month_label(first)} is after --to {month_label(last)}",
            hint="swap the two months",
        )
    if last - first + 1 < YEAR:
        raise WdsError(
            f"period {month_label(first)}..{month_label(last)} is shorter than 12 months",
            hint="year-over-year growth needs at least 12 months; "
            "widen --from/--to or use --period 12m",
        )
    data = f"{month_label(window.first)}..{month_label(window.last)}"
    if first < window.first:
        raise WdsError(
            f"--from {month_label(first)} is before the data window ({data})",
            hint=f"the earliest --from is {month_label(window.first)}",
        )
    chosen = Span(first, last)
    needed = analysis_span(chosen)
    if needed.first < window.first:
        raise WdsError(
            f"growth for a period ending {month_label(last)} needs data from "
            f"{month_label(needed.first)}, before the data window ({data})",
            hint=f"the earliest --to is {month_label(window.first + 2 * YEAR - 1)}: "
            "growth compares the last 12 months with the 12 before them",
        )
    return chosen


def daily_counts(items: list[dict] | None, start: dt.date, end: dt.date) -> np.ndarray:
    """Views per day from start to end, both included.

    The API omits days without views (a small cs article returned 250 of 365 days),
    and answers 404 when there are none at all (items=None): both become zeros.
    """
    days = (end - start).days + 1
    counts = np.zeros(days, dtype=np.int64)
    for item in items or []:
        stamp = item["timestamp"]
        day = dt.date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
        index = (day - start).days
        if 0 <= index < days:
            counts[index] += item["views"]
    return counts


def monthly_totals(daily: np.ndarray, start: dt.date) -> tuple[Span, np.ndarray]:
    """Sum days into calendar months; months not fully covered are dropped.

    A month cut short (the current month, or a range ending mid-month) would look
    like a sudden drop in interest.
    """
    end = start + dt.timedelta(days=len(daily) - 1)
    first = month_of(start) if start.day == 1 else month_of(start) + 1
    last = month_of(end) if end == last_day(month_of(end)) else month_of(end) - 1
    if last < first:
        raise WdsError(
            f"no complete month between {start} and {end}",
            hint="this is a bug in the skill; tell the user the command failed",
        )
    totals = []
    for month in range(first, last + 1):
        begin = (first_day(month) - start).days
        stop = (last_day(month) - start).days + 1
        totals.append(daily[begin:stop].sum())
    return Span(first, last), np.array(totals, dtype=np.int64)


def cut(span: Span, values: np.ndarray, part: Span) -> np.ndarray:
    """The values of `part`, a sub-span of `span`."""
    if part.first < span.first or part.last > span.last:
        raise WdsError(
            f"months {month_label(part.first)}..{month_label(part.last)} are outside "
            f"the data {month_label(span.first)}..{month_label(span.last)}",
            hint="this is a bug in the skill; tell the user the command failed",
        )
    return values[part.first - span.first : part.last - span.first + 1]


def drop_before(daily: np.ndarray, start: dt.date, day: dt.date) -> np.ndarray:
    """Daily views with every day before `day` set to zero.

    Used with the article's first edit: before it the title was missing or a
    redirect elsewhere, so its views (and its redirects' views) were not about
    this article.
    """
    kept = daily.copy()
    kept[: max(0, (day - start).days)] = 0
    return kept
