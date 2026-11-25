"""Invented histories, fixed-model identity and paired aggregate semantics."""

import hashlib
import json

import numpy as np
import pytest

from medextract_lab_bench import hemoglobin as hb
from medextract_lab_bench.numeric import forecasts


def test_parameter_fingerprint_and_invented_forecasts():
    assert hashlib.sha256(hb._PARAMETERS.tobytes()).hexdigest() == hb.MODEL_SHA256
    assert (
        hb.MODEL_SHA256
        == "7dfc1270ecf6165910e3fe6189c17a869ee1d5cc49d1baf119dab4ea138671ac"
    )
    assert hb.predict([0, 2, 10, 11], [10, 11, 13, 12]) == pytest.approx(
        12.070759799659712, abs=1e-12, rel=0
    )
    assert hb.predict([-8, 0, 3, 371], [9, 12, 13, 10]) == pytest.approx(
        11.305478895426354, abs=1e-12, rel=0
    )


@pytest.mark.parametrize(
    ("days", "values"),
    [
        ([1, 2, 3], [8, 8, 8]),
        ([1, 3, 2, 4], [8] * 4),
        ([1, 2, 2, 4], [8] * 4),
        ([1, 2, 3, 4], [8, 8, -1, 8]),
        ([1, 2, 3, 4], [8, True, 8, 8]),
        ([1, 2, 3, 4], [8, np.inf, 8, 8]),
        ([1, 2, 3, np.nan], [8] * 4),
    ],
)
def test_rejects_malformed_histories(days, values):
    with pytest.raises(ValueError):
        hb.predict(days, values)


def test_missing_targets_zero_outcomes_and_forecast_sign():
    days = (0, 1, 2, 365)
    values = (1, 2, 3, 4)
    score = hb.summarize((days, values, y) for y in (0, 4, 5, None))
    assert (score["eligible"], score["paired"], score["observed_fraction"]) == (
        4,
        3,
        0.75,
    )
    last = score["methods"]["last"]
    assert (last["nonfinite"], last["negative"]) == (0, 0)
    m = last["metrics"]
    assert m["overpredicted_count"] == 1
    assert m["overpredicted_fraction"] == pytest.approx(1 / 3)
    assert m["mean_overprediction_when_high"] == 4
    assert m["bias"] == 1
    assert m["mae"] == pytest.approx(5 / 3)
    assert m["median_ae"] == 1
    assert m["p90_ae"] == pytest.approx(3.4)
    assert m["rmse"] == pytest.approx(np.sqrt(17 / 3))
    gate = forecasts(days, values, 90, "g/dL")["existing_span_gate_else_last"].value
    assert score["methods"]["span_rule"]["metrics"]["bias"] == pytest.approx(gate - 3)
    assert score["model_minus"]["last"]["ci95"] is None


def test_bootstrap_pairs_patient_errors_and_reproduces_direct_resampling():
    days = (0, 3, 4, 6)
    values = (9, 8, 7, 6)
    targets = np.array([9, 8, 4] * 10)
    result = hb.summarize((days, values, y) for y in targets)
    differences = np.abs(hb.predict(days, values) - targets) - np.abs(6 - targets)
    indices = np.random.default_rng(20261126).integers(30, size=(5000, 30))
    expected = np.percentile(differences[indices].mean(axis=1), [2.5, 97.5])
    for comparator in ("span_rule", "last"):
        contrast = result["model_minus"][comparator]
        assert contrast["delta_mae"] == pytest.approx(differences.mean())
        assert contrast["ci95"] == pytest.approx(expected)


def test_empty_group_missing_measurements_and_invalid_target_are_distinct():
    empty = hb.summarize([])
    assert empty["observed_fraction"] is None
    assert empty["eligible"] == empty["paired"] == 0
    assert empty["methods"]["last"]["metrics"] is None
    assert empty["model_minus"]["last"] == {"ci95": None, "delta_mae": None}
    history = ([0, 3, 4, 5], [8, 9, 10, 11])
    missing = hb.summarize([(*history, None)])
    assert missing["observed_fraction"] == 0
    assert missing["eligible"] == 1
    assert missing["paired"] == 0
    json.dumps(missing, allow_nan=False)
    for invalid in (True, np.nan, "11", -1):
        with pytest.raises(ValueError):
            hb.summarize([(*history, invalid)])
    under = hb.summarize([(*history, 20)])
    assert under["methods"]["last"]["metrics"]["mean_overprediction_when_high"] is None


def test_large_finite_target_does_not_overflow_rmse():
    row = ([0, 1, 2, 3], [10, 11, 12, 13], 1e155)
    score = hb.summarize([row])
    assert score["methods"]["model"]["metrics"]["rmse"] == 1e155
    json.dumps(score, allow_nan=False)
