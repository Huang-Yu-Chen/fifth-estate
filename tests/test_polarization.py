import math

from fifth_estate.metrics.polarization import Params, attention_skew, gini_mean_difference, polarization

import numpy as np

NO_SHRINK = Params(shrinkage_kappa=0.0, bootstrap=200)


def test_identical_outlets_give_zero():
    lean = {o: [2, 2, 2, 2] for o in "abcde"}
    r = polarization(lean, {}, n_total=20, params=NO_SHRINK)
    assert r["P"] == 0.0
    assert r["china_axis_used"] is False


def test_even_split_at_extremes_gives_100():
    lean = {"a": [-5] * 10, "b": [-5] * 10, "c": [5] * 10, "d": [5] * 10}
    r = polarization(lean, {}, n_total=40, params=NO_SHRINK)
    assert r["P"] == 100.0
    assert r["sufficient"] is True


def test_odd_number_of_outlets_cannot_reach_100():
    x = np.array([-1.0, -1.0, 1.0, 1.0, 1.0])
    assert gini_mean_difference(x) == 96.0


def test_shrinkage_pulls_small_outlets_in():
    lean = {"a": [-5] * 3, "b": [5] * 3, "c": [0] * 50, "d": [0] * 50}
    raw = polarization(lean, {}, n_total=106, params=NO_SHRINK)["P"]
    shrunk = polarization(lean, {}, n_total=106, params=Params(bootstrap=0))["P"]
    assert shrunk < raw


def test_outlets_below_minimum_are_ignored():
    lean = {"a": [-5, -5], "b": [1, 1, 1], "c": [1, 1, 1], "d": [1, 1, 1], "e": [1, 1, 1]}
    r = polarization(lean, {}, n_total=14, params=NO_SHRINK)
    assert r["n_outlets"] == 4 and r["P"] == 0.0
    assert r["sufficient"] is False  # fewer than 15 articles


def test_china_axis_used_only_with_enough_coverage():
    lean = {o: [0, 0, 0, 0] for o in "abcd"}
    china = {"a": [-5, -5, -5], "b": [5, 5, 5], "c": [-5, -5, -5], "d": [5, 5, 5]}
    with_china = polarization(lean, china, n_total=16, params=NO_SHRINK)
    assert with_china["china_axis_used"] is True
    assert with_china["P_lean"] == 0.0 and with_china["P_china"] == 100.0 and with_china["P"] == 50.0
    few = polarization(lean, china, n_total=100, params=NO_SHRINK)  # 12% of coverage involves China
    assert few["china_axis_used"] is False and few["P"] == 0.0 and few["P_china"] is None


def test_bootstrap_interval_brackets_estimate():
    rng = np.random.default_rng(1)
    lean = {o: list(np.clip(np.round(rng.normal(mu, 1.5, 30)), -5, 5)) for o, mu in zip("abcdef", [-3, -2, 0, 1, 2, 3])}
    r = polarization(lean, {}, n_total=180, params=Params(bootstrap=500))
    assert r["ci_low"] <= r["P"] <= r["ci_high"]


def test_attention_skew_sign():
    baseline = {"green1": -2.0, "green2": -1.5, "blue1": 2.0, "blue2": 1.5}
    only_blue = {"green1": 0.0, "green2": 0.0, "blue1": 0.1, "blue2": 0.1}
    assert attention_skew(only_blue, baseline) > 0
    equal = {o: 0.05 for o in baseline}
    assert attention_skew(equal, baseline) == 0.0
    assert attention_skew({}, baseline) is None
    assert math.isnan(gini_mean_difference(np.array([0.5])))
