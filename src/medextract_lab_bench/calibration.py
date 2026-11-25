"""Pooled isotonic calibration and disjoint-person split conformal summaries."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from math import isfinite
from typing import Iterable, Sequence

from .records import identifier


@dataclass(frozen=True)
class Estimate:
    value: float | None
    reason: str | None = None


def _finite(value) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
    ):
        raise ValueError("finite_number_required")
    return float(value)


def venn_abers(
    scores: Sequence[float], labels: Sequence[int], new_score: float
) -> tuple[float, float, float]:
    """Return augmented isotonic lower/upper values and the log-loss merger."""
    if len(scores) != len(labels):
        raise ValueError("score_label_length_mismatch")
    if not scores:
        raise ValueError("empty_calibration")
    groups = defaultdict(list)
    for score, label in zip(scores, labels):
        if type(label) not in (bool, int) or label not in (0, 1):
            raise ValueError("invalid_binary_label")
        groups[_finite(score)].append(int(label))
    new_score = _finite(new_score)
    levels = sorted(set(groups) | {new_score})

    def augmented(label: int) -> float:
        # Each block retains both endpoints, successes and weight. Equal scores
        # enter together, so neither source order nor the appended label breaks ties.
        blocks = []
        for score in levels:
            values = groups[score]
            count, positives = len(values), sum(values)
            if score == new_score:
                count += 1
                positives += label
            blocks.append((score, score, positives, count))
            while len(blocks) >= 2:
                a, b = blocks[-2:]
                if a[2] * b[3] <= b[2] * a[3]:
                    break
                blocks[-2:] = [(a[0], b[1], a[2] + b[2], a[3] + b[3])]
        return next(k / n for lo, hi, k, n in blocks if lo <= new_score <= hi)

    low, high = augmented(0), augmented(1)
    return low, high, high / (1 - low + high)


def _conformal_rank(n: int, alpha: float) -> int:
    if not 0 < _finite(alpha) < 1:
        raise ValueError("invalid_alpha")
    return int(
        ((n + 1) * (1 - Decimal(str(alpha)))).to_integral_value(rounding=ROUND_CEILING)
    )


def conformal_radius(residuals: Iterable[float], alpha: float) -> Estimate:
    """Symmetric radius at the finite-sample split-conformal rank."""
    if not 0 < _finite(alpha) < 1:
        raise ValueError("invalid_alpha")
    values = sorted(_finite(r) for r in residuals)
    if values and values[0] < 0:
        raise ValueError("negative_residual")
    k = _conformal_rank(len(values), alpha)
    if k > len(values):
        return Estimate(None, "insufficient_calibration_support")
    return Estimate(values[k - 1])


@dataclass(frozen=True)
class IntervalCase:
    source: str
    person: str
    target: float | None
    prediction: float | None

    def __post_init__(self):
        identifier(self.source)
        identifier(self.person)
        if self.target is not None:
            _finite(self.target)
        if self.prediction is not None:
            _finite(self.prediction)


def split_prediction_intervals(
    calibration: Iterable[IntervalCase],
    evaluation: Iterable[IntervalCase],
    alpha: float,
) -> dict:
    """Use one maximum residual per calibration person and evaluate observed test-landmark coverage."""
    calibration, evaluation = tuple(calibration), tuple(evaluation)
    if {(x.source, x.person) for x in calibration} & {
        (x.source, x.person) for x in evaluation
    }:
        raise ValueError("calibration_evaluation_person_overlap")

    # Each person contributes one score. A multi-landmark calibration person
    # contributes the maximum residual among their evaluable landmarks.
    def residual(row):
        # Preserve equality of decimal lab changes across different baselines.
        return abs(Decimal(str(row.target)) - Decimal(str(row.prediction)))

    by_person = defaultdict(list)
    for row in calibration:
        if row.target is not None and row.prediction is not None:
            by_person[row.source, row.person].append(residual(row))
    scores = sorted(max(r) for r in by_person.values())
    rank = _conformal_rank(len(scores), alpha)
    radius = scores[rank - 1] if rank <= len(scores) else None
    predicted = [x for x in evaluation if x.prediction is not None]
    observed = sum(x.target is not None for x in evaluation)
    paired = [x for x in predicted if x.target is not None]
    interval_count = len(predicted) if radius is not None else 0
    errors = [residual(row) for row in paired] if radius is not None else []
    evaluated = len(errors)
    covered = sum(error <= radius for error in errors)
    mean_score = None
    if evaluated:
        penalty = Decimal(2) / Decimal(str(alpha))
        mean_score = float(
            2 * radius
            + penalty * sum(max(error - radius, 0) for error in errors) / evaluated
        )
    reason = None
    if radius is None:
        reason = "insufficient_calibration_support"
    elif evaluated == 0:
        reason = "no_evaluable_targets"
    return {
        "alpha": alpha,
        "calibration_cases": len(calibration),
        "calibration_residuals": sum(map(len, by_person.values())),
        "calibration_people": len(by_person),
        "calibration_unit": "per_person_max_absolute_residual",
        "evaluation_cases": len(evaluation),
        "targets_observed": observed,
        "forecast_issued": len(predicted),
        "forecast_with_target": len(paired),
        "intervals_issued": interval_count,
        "evaluated": evaluated,
        "covered": covered,
        "radius": float(radius) if radius is not None else None,
        "mean_width": float(2 * radius) if interval_count else None,
        "mean_interval_score": mean_score,
        "boundary_rule": "closed interval; decimal residuals from reported numeric values",
        "empirical_coverage": covered / evaluated if evaluated else None,
        "reason": reason,
    }
