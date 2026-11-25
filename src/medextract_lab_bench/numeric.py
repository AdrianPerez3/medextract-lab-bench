"""Frozen, unfitted numerical forecasts on one person's retained distinct days."""

from dataclasses import dataclass
from itertools import combinations
import math
from numbers import Real
from statistics import median

import numpy as np

METHODS = (
    "last",
    "ses",
    "robust_slope",
    "history_median",
    "existing_span_gate_else_last",
    "damped_span_slope_v1",
)
SPAN_GATE = 365


@dataclass(frozen=True, slots=True)
class Forecast:
    requested: str
    used: str
    count: int
    span: float
    horizon: float
    unit: str
    value: float | None
    status: str
    reason: str | None

    @property
    def valid(self) -> bool:
        return self.value is not None and self.value >= 0


def positive_horizon(horizon):
    if (
        isinstance(horizon, bool)
        or not isinstance(horizon, Real)
        or not math.isfinite(horizon)
        or horizon <= 0
    ):
        raise ValueError("The requested horizon must be positive and finite")


def forecasts(days, values, horizon, unit) -> dict[str, Forecast]:
    """All methods receive the same as-of history; targets are outside this API.

    Source adapters own the assay-specific day reduction. Duplicate dates
    here are ambiguous. Finite negative predictions remain scoreable raw outputs.
    """
    days, values = tuple(days), tuple(values)
    if not isinstance(unit, str) or not unit.strip():
        raise ValueError("A declared outcome unit is required")
    positive_horizon(horizon)
    if not days or len(days) != len(values):
        raise ValueError("History days and measurements must have equal nonzero length")
    if len(days) > 4:
        raise ValueError(
            "These fixed numerical tasks support at most four history days"
        )
    if not all(
        isinstance(day, Real) and not isinstance(day, bool) and math.isfinite(day)
        for day in days
    ):
        raise ValueError("Nonfinite history day")
    if not all(
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
        for value in values
    ):
        raise ValueError("Invalid measured history")
    ordered = sorted(zip(map(float, days), map(float, values)))
    if any(later[0] == earlier[0] for earlier, later in zip(ordered, ordered[1:])):
        raise ValueError("Duplicate history day requires source-specific resolution")
    first_day, first = ordered[0]
    last_day, last = ordered[-1]
    span = last_day - first_day
    slope = 0.0
    level = first
    for _, reading in ordered[1:]:
        level = 0.4 * reading + 0.6 * level
    ses = last
    if len(ordered) >= 2:
        slopes = (
            (y2 - y1) / (d2 - d1) for (d1, y1), (d2, y2) in combinations(ordered, 2)
        )
        slope = median(slopes)
        ses = level + (last - first) / span * horizon
    # Match the historical annualized slope's floating-point sequence in v0.
    robust = last + (slope * 365.25) * horizon / 365.25
    damped = last + slope * horizon * (-math.expm1(-1)) * min(1.0, span / horizon)
    used_gate = "ses" if span >= SPAN_GATE else "last"
    values_by_method = (
        last,
        ses,
        robust,
        median(values),
        ses if span >= SPAN_GATE else last,
        damped,
    )
    result = {}
    for method, raw in zip(METHODS, values_by_method, strict=True):
        used, status, reason = method, "success", None
        if method == "existing_span_gate_else_last":
            used = used_gate
            if used == "last":
                status, reason = "fallback", "history_span_below_365_days"
        elif method == "damped_span_slope_v1" and len(ordered) == 1:
            used, status, reason = "last", "fallback", "single_distinct_day"
        if not math.isfinite(raw):
            raw, status, reason = None, "abstention", "nonfinite_method_output"
        elif raw < 0:
            status, reason = "invalid", "negative_raw_forecast"
        result[method] = Forecast(
            method, used, len(ordered), span, horizon, unit, raw, status, reason
        )
    return result


def bootstrap(differences, repeats, seed):
    """Paired person bootstrap, preserving the historical PCG64 sampling order."""
    differences = np.asarray(differences, dtype=float)
    n = len(differences)
    if n == 0:
        raise ValueError("A bootstrap needs at least one observed pair")
    rng = np.random.Generator(np.random.PCG64(seed))
    means = np.empty((repeats, *differences.shape[1:]), dtype=float)
    # Bound each resampled cell array, including for larger independent sites.
    width = differences.shape[1] if differences.ndim > 1 else 1
    batch = max(1, min(100, 1_000_000 // (n * width)))
    for start in range(0, repeats, batch):
        stop = min(repeats, start + batch)
        indices = rng.integers(n, size=(stop - start, n))
        means[start:stop] = differences[indices].mean(axis=1)
    return np.quantile(means, [0.025, 0.975], axis=0)
