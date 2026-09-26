"""Trend tests on a monthly series, in numpy only: Mann-Kendall and Theil-Sen.

Mann-Kendall asks whether later months tend to be higher (or lower) than earlier
ones. It counts only directions, so one extreme month moves it little. Theil-Sen
gives the slope: the median of the slopes between every pair of points, so one
extreme month does not tilt it either. Both are checked against scipy in
tests/test_stats.py; scipy is a test dependency only.
"""

import math
from typing import NamedTuple

import numpy as np


class MannKendall(NamedTuple):
    s: int  # pairs where the later value is higher minus pairs where it is lower
    var_s: float  # variance of s when there is no trend, corrected for tied values
    z: float  # s scaled to a standard normal, with the continuity correction
    p: float  # two-sided p-value


def mann_kendall(values: np.ndarray) -> MannKendall:
    """Mann-Kendall trend test over equally spaced points (as in Gilbert 1987).

    Tied values (e.g. months with the same count) make s less variable, so the
    variance drops by t(t-1)(2t+5)/18 for every group of t equal values. z moves
    s one step towards zero (continuity correction): s takes whole-number steps,
    the normal curve is continuous. No trend can be shown when nothing varies.
    """
    y = np.asarray(values, dtype=float)
    n = len(y)
    earlier, later = np.triu_indices(n, k=1)
    s = int(np.sign(y[later] - y[earlier]).sum())
    _, sizes = np.unique(y, return_counts=True)
    ties = float((sizes * (sizes - 1) * (2 * sizes + 5)).sum())
    var_s = (n * (n - 1) * (2 * n + 5) - ties) / 18
    if var_s <= 0:
        return MannKendall(s, 0.0, 0.0, 1.0)
    z = (s - np.sign(s)) / math.sqrt(var_s)
    return MannKendall(s, var_s, float(z), math.erfc(abs(z) / math.sqrt(2)))


def theil_sen(values: np.ndarray) -> float:
    """Theil-Sen slope per step over equally spaced points (at least two)."""
    y = np.asarray(values, dtype=float)
    earlier, later = np.triu_indices(len(y), k=1)
    return float(np.median((y[later] - y[earlier]) / (later - earlier)))
