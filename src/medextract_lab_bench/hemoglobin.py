"""The frozen 90-day Lippe hemoglobin forecast and its external evaluation.

The aggregate parameters were fitted to the four-draw histories of 3,273 training
participants in Sürmeli et al., Pseudonymized Complete Blood Count Test Data
(CC BY 4.0, https://doi.org/10.5281/zenodo.15674541). See CITATION.cff.
"""

import math
from numbers import Real

import numpy as np

from .numeric import bootstrap, forecasts

MODEL_SHA256 = "7dfc1270ecf6165910e3fe6189c17a869ee1d5cc49d1baf119dab4ea138671ac"
# Little-endian binary64: six feature means, six standard deviations,
# six standardized ridge coefficients, then the unpenalized intercept.
_PARAMETERS = np.array(
    [
        12.576978307363309,
        -0.06683470821875963,
        -0.23982584784601263,
        0.6811405267014844,
        4.209522716957182,
        2.65167152038495,
        2.0445864833777407,
        1.1177432426481106,
        0.9213131744383153,
        0.48449388913318653,
        1.623500305658874,
        1.6581483633199172,
        -0.5673750944641858,
        -0.09195508894884041,
        -0.12579078468997015,
        0.13378618693364944,
        -0.040937999071795565,
        0.08568609631997116,
        -0.1280323861900397,
    ],
    dtype="<f8",
)
_PARAMETERS.flags.writeable = False
_CENTER, _SCALE, _WEIGHTS = _PARAMETERS[:6], _PARAMETERS[6:12], _PARAMETERS[12:18]
_INTERCEPT = _PARAMETERS[-1]


def _finite_number(value):
    return (
        isinstance(value, Real)
        and not isinstance(value, (bool, np.bool_))
        and math.isfinite(value)
    )


def _features(days, values):
    try:
        days, values = tuple(days), tuple(values)
    except TypeError as exc:
        raise ValueError("Supply two sequences of four numbers") from exc
    if len(days) != 4 or len(values) != 4:
        raise ValueError(
            "Exactly four draw days and four hemoglobin results are required"
        )
    if not all(_finite_number(d) for d in days) or not all(
        _finite_number(v) and v >= 0 for v in values
    ):
        raise ValueError("Expected finite draw days and finite nonnegative hemoglobin")
    d = np.asarray(days, dtype=float)
    if np.any(d[1:] <= d[:-1]):
        raise ValueError("Supply four distinct draw days in chronological order")
    v = np.asarray(values, dtype=float)
    return np.array(
        [
            v[-1],
            v[-1] - v[-2],
            v[-1] - np.median(v),
            np.std(v, ddof=0),
            np.log1p(d[-1] - d[0]),
            np.log1p(d[-1] - d[-2]),
        ]
    )


def predict(days, values):
    """Predict a future measured Hb in g/dL from four prior chronological draws."""
    with np.errstate(over="ignore", invalid="ignore"):
        x = _features(days, values)
        change = _INTERCEPT + ((x - _CENTER) / _SCALE) @ _WEIGHTS
        return float(x[0] + change)


def _metrics(errors):
    absolute = np.abs(errors)
    high = errors[errors > 0]
    peak = float(absolute.max())
    rmse = peak * np.sqrt(np.mean((errors / peak) ** 2)) if peak else 0.0
    return {
        "mae": float(absolute.mean()),
        "median_ae": float(np.median(absolute)),
        "p90_ae": float(np.quantile(absolute, 0.9)),
        "rmse": float(rmse),
        "bias": float(errors.mean()),
        "overpredicted_count": len(high),
        "overpredicted_fraction": len(high) / len(errors),
        "mean_overprediction_when_high": float(high.mean()) if len(high) else None,
    }


def summarize(rows, *, seed=20261126, min_pairs=30):
    """Score (days, values, observed Hb or None) in deterministic patient order.

    Each person contributes at most one origin. Numerical accuracy and paired
    intervals use the same measured people for all methods. Availability and
    failure counts cover everyone. Negative finite forecasts retain their error.
    """
    if type(min_pairs) is not int or min_pairs < 1:
        raise ValueError("min_pairs must be a positive integer")
    names = ("model", "last", "span_rule")
    methods = {name: {"nonfinite": 0, "negative": 0, "metrics": None} for name in names}
    comparisons = {name: {"delta_mae": None, "ci95": None} for name in names[1:]}
    observed_errors = []
    eligible = 0
    for days, values, observed in rows:
        days, values = tuple(days), tuple(values)
        candidate = predict(days, values)
        published = forecasts(days, values, 90, "g/dL")
        predictions = (
            candidate,
            values[-1],
            published["existing_span_gate_else_last"].value,
        )
        for name, value in zip(names, predictions, strict=True):
            if value is None or not math.isfinite(value):
                methods[name]["nonfinite"] += 1
            elif value < 0:
                methods[name]["negative"] += 1
        eligible += 1
        if observed is not None:
            if not _finite_number(observed) or observed < 0:
                raise ValueError("Supply a finite nonnegative target or None")
            if any(v is None or not math.isfinite(v) for v in predictions):
                raise ValueError(
                    "A nonfinite forecast prevents complete paired scoring"
                )
            observed_errors.append(
                tuple(float(v) - float(observed) for v in predictions)
            )

    paired = len(observed_errors)
    if paired:
        errors = np.asarray(observed_errors)
        for name, column in zip(names, errors.T, strict=True):
            methods[name]["metrics"] = _metrics(column)
        absolute = np.abs(errors)
        differences = absolute[:, :1] - absolute[:, 1:]
        bounds = bootstrap(differences, 5000, seed) if paired >= min_pairs else None
        for index, name in enumerate(names[1:]):
            comparisons[name] = {
                "delta_mae": float(differences[:, index].mean()),
                "ci95": bounds[:, index].tolist() if bounds is not None else None,
            }
    return {
        "eligible": eligible,
        "paired": paired,
        "observed_fraction": paired / eligible if eligible else None,
        "methods": methods,
        "model_minus": comparisons,
    }
