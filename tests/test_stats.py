"""Mann-Kendall and Theil-Sen against scipy on random series.

scipy has no Mann-Kendall test by name, but Kendall's tau between time and values
is the same statistic: with method="asymptotic" scipy uses the same tie-corrected
variance and the plain z = s / sqrt(var). Ours adds the continuity correction on
top, so s and its variance are compared through scipy's p-value, and our p-value
through the corrected z.
"""

import math

import numpy as np
import pytest
from scipy import stats as scipy_stats
from wds_lib import stats

SEEDS = range(40)


def random_series(seed: int) -> np.ndarray:
    """Different lengths (3..72 months) and shapes, half of them full of ties."""
    rng = np.random.default_rng(seed)
    n = int(rng.integers(3, 73))
    if seed % 2:
        return rng.integers(0, 4, n).astype(float)  # few distinct values: many ties
    trend = rng.normal(0, 0.05) * np.arange(n)
    return 10 + trend + rng.normal(0, 1, n)


@pytest.mark.parametrize("seed", SEEDS)
def test_mann_kendall_matches_scipy(seed):
    y = random_series(seed)
    ours = stats.mann_kendall(y)
    theirs = scipy_stats.kendalltau(np.arange(len(y)), y, method="asymptotic")
    assert np.sign(ours.s) == np.sign(theirs.statistic)
    plain_z = ours.s / math.sqrt(ours.var_s)
    assert 2 * scipy_stats.norm.sf(abs(plain_z)) == pytest.approx(
        theirs.pvalue, rel=1e-9, abs=1e-12
    )
    corrected_z = (ours.s - np.sign(ours.s)) / math.sqrt(ours.var_s)
    assert ours.z == pytest.approx(corrected_z)
    assert ours.p == pytest.approx(2 * scipy_stats.norm.sf(abs(corrected_z)))


@pytest.mark.parametrize("seed", SEEDS)
def test_theil_sen_matches_scipy(seed):
    y = random_series(seed)
    assert stats.theil_sen(y) == pytest.approx(scipy_stats.theilslopes(y).slope)


def test_textbook_case():
    # 1..5: all 10 pairs rise; var = 5*4*15/18; z = (10 - 1) / sqrt(var).
    mk = stats.mann_kendall(np.arange(1, 6))
    assert (mk.s, mk.var_s) == (10, pytest.approx(50 / 3))
    assert mk.p == pytest.approx(0.0275, abs=1e-4)
    assert stats.theil_sen(np.arange(1, 6)) == 1.0


def test_ties_lower_the_variance():
    no_ties = stats.mann_kendall(np.array([1, 2, 3, 4, 5, 6]))
    ties = stats.mann_kendall(np.array([1, 1, 1, 4, 5, 6]))
    # one group of 3 equal values: 3*2*11/18 less variance
    assert ties.var_s == pytest.approx(no_ties.var_s - 66 / 18)


def test_one_extreme_value_barely_moves_either_statistic():
    y = np.arange(24.0)
    spiked = y.copy()
    spiked[5] = 1000
    assert stats.theil_sen(spiked) == pytest.approx(1.0)
    assert stats.mann_kendall(spiked).s > 0.8 * stats.mann_kendall(y).s


@pytest.mark.parametrize("seed", SEEDS)
def test_seasonal_forms_sum_and_pool_the_seasons(seed):
    y = np.random.default_rng(seed).normal(10, 1, 36)  # three years
    seasons = [y[month::12] for month in range(12)]
    parts = [stats.mann_kendall(season) for season in seasons]
    seasonal = stats.seasonal_mann_kendall(y)
    assert seasonal.s == sum(p.s for p in parts)
    assert seasonal.var_s == pytest.approx(sum(p.var_s for p in parts))
    slopes = [
        (s[later] - s[earlier]) / (later - earlier)
        for s in seasons
        for earlier in range(3)
        for later in range(earlier + 1, 3)
    ]
    assert stats.seasonal_theil_sen(y) == pytest.approx(np.median(slopes))


def test_over_two_years_the_seasonal_forms_compare_each_month_with_a_year_earlier():
    y = np.random.default_rng(0).normal(10, 1, 24)
    higher = int((y[12:] > y[:12]).sum())
    assert stats.seasonal_mann_kendall(y).s == higher - (12 - higher)
    assert stats.seasonal_theil_sen(y) == pytest.approx(np.median(y[12:] - y[:12]))


def test_a_school_year_is_a_trend_only_for_the_plain_test():
    # High September, low summer, the same both years: no trend at all.
    school = np.array([3.0, 1.3, 1.3, 1.3, 1.2, 1.1, 1.0, 1.0, 0.9, 0.45, 0.3, 0.35])
    y = np.tile(school, 2) * np.random.default_rng(0).lognormal(0, 0.05, 24)
    assert stats.mann_kendall(y).p < 0.05
    assert stats.seasonal_mann_kendall(y).p > 0.05


def test_constant_series_shows_no_trend():
    mk = stats.mann_kendall(np.full(24, 7.0))
    assert (mk.s, mk.var_s, mk.z, mk.p) == (0, 0.0, 0.0, 1.0)
    assert stats.theil_sen(np.full(24, 7.0)) == 0.0
