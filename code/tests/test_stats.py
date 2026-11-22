import math

import pytest

from orca import stats


def test_ols_exact_line():
    fit = stats.ols([1, 2, 3, 4, 5], [3, 5, 7, 9, 11])
    assert fit["slope"] == pytest.approx(2.0)
    assert fit["intercept"] == pytest.approx(1.0)
    assert fit["r2"] == pytest.approx(1.0)


def test_t_test_p_value_matches_tables():
    # Two-sided p for t=2.228, df=10 is 0.05 (standard t-table value).
    assert stats.t_two_sided_p(2.228, 10) == pytest.approx(0.05, abs=5e-4)
    assert stats.t_two_sided_p(0.0, 10) == pytest.approx(1.0)


def test_pearson_and_spearman():
    xs = [1, 2, 3, 4, 5, 6]
    ys = [2, 4, 5, 4, 5, 7]
    r = stats.pearson(xs, ys)
    assert r["r"] == pytest.approx(13.5 / math.sqrt(17.5 * 13.5))  # 0.8783, worked by hand
    rho = stats.spearman(xs, [10, 20, 30, 40, 50, 60])
    assert rho["rho"] == pytest.approx(1.0)


def test_mann_kendall_detects_monotonic_increase():
    mk = stats.mann_kendall([float(i) for i in range(15)])
    assert mk["S"] == 15 * 14 / 2
    assert mk["p_value"] < 0.001
    flat = stats.mann_kendall([1.0, 3.0, 2.0, 3.0, 1.0, 2.0])
    assert flat["p_value"] > 0.3


def test_sens_slope_and_percentile():
    # One outlier barely moves the median pairwise slope.
    assert stats.sens_slope([0, 1, 2, 3, 4], [0, 2, 4, 6, 100]) == pytest.approx(2.0)
    assert stats.percentile([1, 2, 3, 4, 5], 0.9) == pytest.approx(4.6)


def test_constant_series_rejected():
    with pytest.raises(ValueError):
        stats.pearson([1, 2, 3, 4], [5, 5, 5, 5])
    assert math.isfinite(stats.normal_two_sided_p(1.96))
