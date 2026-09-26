"""Trend tests on a monthly series, in numpy only: Mann-Kendall and Theil-Sen.

Mann-Kendall asks whether later months tend to be higher (or lower) than earlier
ones. It counts only directions, so one extreme month moves it little. Theil-Sen
gives the slope: the median of the slopes between every pair of points, so one
extreme month does not tilt it either. Both are checked against scipy in
tests/test_stats.py; scipy is a test dependency only.

The verdict uses the seasonal forms (Hirsch, Slack & Smith 1982): each month is
compared only with the same month in other years, so a school year (high
September, low summer) is not mistaken for a trend.
"""

import math
from typing import NamedTuple

import numpy as np


class MannKendall(NamedTuple):
    s: int  # pairs where the later value is higher minus pairs where it is lower
    var_s: float  # variance of s when there is no trend, corrected for tied values
    z: float  # s scaled to a standard normal, with the continuity correction
    p: float  # two-sided p-value


def _test(s: int, var_s: float) -> MannKendall:
    """z moves s one step towards zero (continuity correction): s takes whole-number
    steps, the normal curve is continuous. No trend can be shown when nothing varies.
    """
    if var_s <= 0:
        return MannKendall(s, 0.0, 0.0, 1.0)
    z = (s - np.sign(s)) / math.sqrt(var_s)
    return MannKendall(s, var_s, float(z), math.erfc(abs(z) / math.sqrt(2)))


def mann_kendall(values: np.ndarray) -> MannKendall:
    """Mann-Kendall trend test over equally spaced points (as in Gilbert 1987).

    Tied values (e.g. months with the same count) make s less variable, so the
    variance drops by t(t-1)(2t+5)/18 for every group of t equal values.
    """
    y = np.asarray(values, dtype=float)
    n = len(y)
    earlier, later = np.triu_indices(n, k=1)
    s = int(np.sign(y[later] - y[earlier]).sum())
    _, sizes = np.unique(y, return_counts=True)
    ties = float((sizes * (sizes - 1) * (2 * sizes + 5)).sum())
    return _test(s, (n * (n - 1) * (2 * n + 5) - ties) / 18)


def seasonal_mann_kendall(values: np.ndarray, period: int = 12) -> MannKendall:
    """Mann-Kendall within each season (January with January, ...), summed: s and
    its variance are the sums over the seasons."""
    y = np.asarray(values, dtype=float)
    parts = [mann_kendall(y[season::period]) for season in range(period)]
    return _test(sum(p.s for p in parts), sum(p.var_s for p in parts))


def _pair_slopes(y: np.ndarray) -> np.ndarray:
    earlier, later = np.triu_indices(len(y), k=1)
    return (y[later] - y[earlier]) / (later - earlier)


def theil_sen(values: np.ndarray) -> float:
    """Theil-Sen slope per step over equally spaced points (at least two)."""
    return float(np.median(_pair_slopes(np.asarray(values, dtype=float))))


def seasonal_theil_sen(values: np.ndarray, period: int = 12) -> float:
    """Median of the slopes between the same season in different years, per year
    (at least one full year plus one season)."""
    y = np.asarray(values, dtype=float)
    slopes = [_pair_slopes(y[season::period]) for season in range(period)]
    return float(np.median(np.concatenate(slopes)))
