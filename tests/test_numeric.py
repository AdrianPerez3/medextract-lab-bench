import math

import numpy as np
import pytest

from medextract_lab_bench.numeric import bootstrap, forecasts


def test_short_and_long_spans_change_extrapolation():
    short = forecasts([0, 1, 2, 3], [16, 14, 12, 10], 90, "g/dL")
    long = forecasts([0, 90, 180, 270], [16, 14, 12, 10], 90, "g/dL")
    assert short["ses"].value == pytest.approx(12.352 - 180)
    assert short["ses"].status == "invalid"
    assert short["ses"].reason == "negative_raw_forecast"
    assert short["robust_slope"].value == pytest.approx(10 - 180)
    assert short["damped_span_slope_v1"].value == pytest.approx(
        10 - 6 * (1 - math.exp(-1))
    )
    assert long["damped_span_slope_v1"].value == pytest.approx(
        10 - 2 * (1 - math.exp(-1))
    )
    assert long["history_median"].value == 13


@pytest.mark.parametrize(
    "span,used,status", [(364, "last", "fallback"), (365, "ses", "success")]
)
def test_span_gate_includes_day_365(span, used, status):
    result = forecasts([span, 0], [14, 10], 90, "g/dL")
    gate = result["existing_span_gate_else_last"]
    assert (gate.used, gate.value, gate.status) == (used, result[used].value, status)
    assert gate.count == 2 and gate.span == span


def test_damped_single_point_returns_last_and_records_fallback():
    result = forecasts([0], [6.2], 95, "%")
    assert all(p.value == 6.2 for p in result.values())
    damped = result["damped_span_slope_v1"]
    assert (damped.requested, damped.used, damped.status) == (
        "damped_span_slope_v1",
        "last",
        "fallback",
    )


@pytest.mark.parametrize(
    "days,values,horizon",
    [
        ([], [], 1),
        ([0], [], 1),
        ([0, 0], [1, 1], 1),
        ([math.inf], [1], 1),
        ([0], [math.nan], 1),
        ([0], [-1], 1),
        ([0], [1], 0),
        ([0], [1], -1),
        ([0], [1], math.nan),
        ([0], [1], math.inf),
    ],
)
def test_invalid_histories(days, values, horizon):
    with pytest.raises(ValueError):
        forecasts(days, values, horizon, "g/dL")


def test_nonfinite_output_has_no_issued_value():
    out = forecasts([0, 1], [1, 1e308], 90, "g/dL")
    assert out["ses"].value is None
    assert out["ses"].status == "abstention"
    assert out["ses"].reason == "nonfinite_method_output"


def test_paired_bootstrap_matches_unbatched_sampling():
    differences = np.array([[1.0, -2.0], [5.0, 4.0], [1.0, 0.0]])
    indices = np.random.default_rng(174).integers(3, size=(523, 3))
    expected = np.quantile(differences[indices].mean(axis=1), [0.025, 0.975], axis=0)
    np.testing.assert_array_equal(bootstrap(differences, 523, 174), expected)


@pytest.mark.parametrize("horizon", [None, True, "90", -math.inf])
def test_horizon_must_be_numeric_and_positive(horizon):
    with pytest.raises(ValueError, match="horizon"):
        forecasts([0], [10.0], horizon, "g/dL")


def test_fixed_tasks_bound_all_pairs_slope_work():
    with pytest.raises(ValueError, match="four history days"):
        forecasts(range(5), [14.0] * 5, 90, "g/dL")
