"""Aggregate-only numerical development replay and corrected evaluation."""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.resources import files
import hashlib
import math
from numbers import Real
from pathlib import Path
import re

import numpy as np

from . import __version__
from .adapters import bariatric, lippe
from .calibration import IntervalCase, split_prediction_intervals
from .numeric import (
    METHODS,
    SPAN_GATE,
    Forecast,
    bootstrap,
    forecasts,
    positive_horizon,
)

BARIATRIC = "bariatric_hba1c_9m_12m_v0"
LIPPE = "lippe_hemoglobin_day90_v0"
TASKS = {
    BARIATRIC: "ce595ea3de922e1bf4c68637100144b155e7baeac0baeab052c702702bcca1b6",
    LIPPE: "3f20282943ecaa7ce1936209ec9ad2273a0946da9b492dffc7f79b93f45ceef4",
}
SOURCE_COUNT_FIELDS = frozenset(
    {
        "persons",
        "raw_records",
        "person_days",
        "with_four_days",
        "with_target",
        "eligible_people",
        "paired_people",
        "with_index",
        "with_index_and_target",
        "cohort_n",
        "long_records",
    }
)
REFERENCE_CACHE = "7d64b8b1fcd7c21cb96f8ed30fa884675f0468d9fa046ae2d4f2669b90c12240"
CBC_DESIGN = (
    "One index per person, end of fourth distinct released draw day; same-day median; "
    "target nearest +90 in [76,104], ties earlier; no fitted parameters, no trimming or clipping."
)


def validate_source(task: str, path: Path) -> dict:
    if task not in TASKS:
        raise ValueError("Unknown development task")
    with Path(path).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != TASKS[task]:
        raise ValueError("Original development source SHA-256 does not match the task")
    return {"task": task, "sha256": digest, "status": "verified"}


@dataclass(frozen=True, slots=True)
class Case:
    """One eligible origin supplied by a site adapter in stable subject order."""

    person: str | int
    days: tuple[int, ...]
    values: tuple[float, ...]
    target: float | None
    target_gap: int | None = None


@dataclass(frozen=True, slots=True)
class _Scored:
    person: str | int
    span: float
    gap: int | None
    observed: float | None
    predictions: dict[str, Forecast]


def _score(person, days, values, horizon, unit, resolve_outcome):
    predictions = forecasts(days, values, horizon, unit)
    # An evaluator can inspect future observations only after the origin is forecast.
    outcome = resolve_outcome()
    gap, observed = outcome if outcome is not None else (None, None)
    if observed is not None and (
        isinstance(observed, bool)
        or not isinstance(observed, Real)
        or not math.isfinite(observed)
        or observed < 0
    ):
        raise ValueError("Invalid measured target")
    if gap is not None and (
        isinstance(gap, bool)
        or not isinstance(gap, Real)
        or not math.isfinite(gap)
        or gap <= 0
        or observed is None
    ):
        raise ValueError("An observed target gap requires a strictly post-index result")
    return _Scored(person, predictions["last"].span, gap, observed, predictions)


def _cbc_groups(rows):
    return {
        "all": rows,
        "span_lt_14_days": [r for r in rows if r.span < 14],
        "span_14_to_89_days": [r for r in rows if 14 <= r.span < 90],
        "span_ge_90_days": [r for r in rows if r.span >= 90],
        "sensitivity_existing_365_day_duration": [r for r in rows if r.span >= 365],
    }


def _error_metrics(observed, predictions):
    n = len(observed)
    if n == 0:
        return dict(
            n=0,
            mae=None,
            rmse=None,
            median_absolute_error=None,
            p90_absolute_error=None,
            mean_signed_error=None,
            reason="no_evaluable_pairs",
        )
    errors = predictions - observed
    absolute = np.abs(errors)
    return dict(
        n=n,
        mae=float(np.mean(absolute)),
        rmse=float(np.sqrt(np.mean(errors**2))),
        median_absolute_error=float(np.median(absolute)),
        p90_absolute_error=float(np.quantile(absolute, 0.9)),
        mean_signed_error=float(np.mean(errors)),
        reason=None,
    )


def _calibration(y, pred):
    if len(y) < 2 or np.ptp(pred) == 0:
        return dict(
            n=len(y),
            intercept=None,
            slope=None,
            reason="insufficient_prediction_variation",
        )
    centered = pred - pred.mean()
    slope = float(np.dot(centered, y - y.mean()) / np.dot(centered, centered))
    return dict(
        n=len(y),
        intercept=float(y.mean() - slope * pred.mean()),
        slope=slope,
        reason=None,
    )


def _fraction(count, total):
    return count / total if total else None


def _flow(predictions):
    finite = sum(p.value is not None for p in predictions)
    valid = sum(p.valid for p in predictions)
    return {
        "denominator": len(predictions),
        "predicted": finite,
        "valid_outputs": valid,
        "invalid_outputs": finite - valid,
        "abstentions": len(predictions) - finite,
        "fallbacks": sum(p.status == "fallback" for p in predictions),
        "prediction_coverage": _fraction(finite, len(predictions)),
        "valid_output_coverage": _fraction(valid, len(predictions)),
        "coverage_reason": None if predictions else "empty_denominator",
        "used_method": dict(sorted(Counter(p.used for p in predictions).items())),
        "reasons": dict(
            sorted(Counter(p.reason for p in predictions if p.reason).items())
        ),
    }


def _corrected_group(rows):
    eligible = len(rows)
    paired = [row for row in rows if row.observed is not None]
    observed = np.array([row.observed for row in paired], dtype=float)
    matrix = np.asarray(
        [[row.predictions[m].value for m in METHODS] for row in paired], dtype=float
    ).reshape((-1, len(METHODS)))
    common = np.isfinite(matrix).all(axis=1)
    differences = np.abs(matrix[common] - observed[common, None])
    differences -= differences[:, [0]]
    means = differences.mean(axis=0) if len(differences) else None
    bounds = bootstrap(differences, 5000, 2601) if len(differences) else None
    metrics, flows = {}, {}
    for i, name in enumerate(METHODS):
        per_origin = [r.predictions[name] for r in rows]
        per_pair = [r.predictions[name] for r in paired]
        flows[name] = {"eligible": _flow(per_origin), "paired": _flow(per_pair)}
        predicted = matrix[:, i]
        finite = np.isfinite(predicted)
        # Accepted-only excludes declared fallbacks and physically invalid outputs.
        accepted = np.array(
            [p.status == "success" and p.valid for p in per_pair], dtype=bool
        )
        stats = _error_metrics(observed[finite], predicted[finite])
        stats["accepted_only"] = _error_metrics(observed[accepted], predicted[accepted])
        stats["calibration"] = _calibration(observed[finite], predicted[finite])
        stats["paired_n"] = len(differences)
        stats["paired_minus_last_mae"] = float(means[i]) if means is not None else None
        stats["paired_minus_last_ci95"] = (
            bounds[:, i].tolist() if bounds is not None else None
        )
        stats["paired_reason"] = (
            None if len(differences) else "no_common_observed_pairs"
        )
        metrics[name] = stats
    return {
        "eligible_people": eligible,
        "paired_people": len(paired),
        "eligible_without_target": eligible - len(paired),
        "target_observed_fraction": _fraction(len(paired), eligible),
        "target_fraction_reason": None if eligible else "empty_denominator",
        "status": "supported" if len(differences) else "insufficient_support",
        "methods": flows,
        "metrics": metrics,
    }


def _interval_analysis(task, rows):
    calibration, evaluation = [], []
    for row in rows:
        # Type-tag keys, so e.g. numeric and textual forms do not alias.
        person = f"{type(row.person).__name__}:{row.person}"
        fingerprint = hashlib.sha256(f"split-v1|{task}|{person}".encode()).digest()
        destination = (
            calibration if int.from_bytes(fingerprint, "big") % 5 == 0 else evaluation
        )
        destination.append(
            IntervalCase(task, person, row.observed, row.predictions["last"].value)
        )
    return {
        "method": "last",
        "split": "SHA256(split-v1|task|typed_person) mod 5 = 0 for calibration",
        "scope": "within-cohort held-out people; interval coverage conditional on observed targets",
        "80_percent": split_prediction_intervals(calibration, evaluation, 0.2),
        "95_percent": split_prediction_intervals(calibration, evaluation, 0.05),
    }


def _report(task, rows, unit, horizon, clock, digest, counts, strata, intervals=False):
    result = {
        "scorecard_version": "1",
        "benchmark_version": __version__,
        "method_specification_sha256": hashlib.sha256(
            files("medextract_lab_bench")
            .joinpath("protocols", "methods-v1.json")
            .read_bytes()
        ).hexdigest(),
        "mode": "corrected",
        "task": task,
        "unit": unit,
        "horizon_days": horizon,
        "clock": clock,
        "input_sha256": digest,
        **_corrected_group(rows),
        "support": {
            "source_counts": dict(counts),
            "common_pair_rule": "observed target and finite output from every method",
            "score_basis": "conditional on a recorded target; full-route errors include finite negative outputs",
            "accepted_only_rule": "nonnegative finite output without a declared fallback",
            "calibration_rule": "ordinary least squares of observed on predicted with an intercept",
            "inference": {
                "unit": "person",
                "resamples": 5000,
                "seed": 2601,
                "rng": "NumPy PCG64",
                "numpy_version": np.__version__,
                "ordering": "numeric person keys ascending, then text person keys lexicographically",
                "percentiles": [2.5, 97.5],
                "percentile_method": "linear",
            },
        },
    }
    if intervals:
        result["prediction_intervals"] = _interval_analysis(task, rows)
    if strata:
        result["strata"] = {
            key: _corrected_group(group)
            for key, group in _cbc_groups(rows).items()
            if key != "all"
        }
    return result


def corrected_cases(
    task: str,
    cases: Iterable[Case],
    horizon: int,
    unit: str,
    *,
    clock: str,
    input_sha256: str | None = None,
    source_counts: dict | None = None,
    strata: bool = False,
    intervals: bool = False,
) -> dict:
    """Evaluate one origin per person. Never serialize subject keys or raw records."""
    positive_horizon(horizon)
    if input_sha256 is not None and (
        not isinstance(input_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", input_sha256)
    ):
        raise ValueError("The input fingerprint must be a lowercase SHA-256 digest")
    if source_counts is not None:
        if (
            not isinstance(source_counts, dict)
            or not source_counts.keys() <= SOURCE_COUNT_FIELDS
        ):
            raise ValueError("Unrecognized aggregate source count field")
        if any(
            type(number) is not int or number < 0 for number in source_counts.values()
        ):
            raise ValueError("Aggregate source counts must be nonnegative integers")
    if not all(isinstance(s, str) and s.strip() for s in (task, unit, clock)):
        raise ValueError("A task, unit and clock are required")
    seen = set()
    scored = []
    for case in cases:
        if type(case.person) not in (str, int) or (
            isinstance(case.person, str)
            and (not case.person or case.person != case.person.strip())
        ):
            raise ValueError("Eligible origin requires a nonempty person key")
        if case.person in seen:
            raise ValueError("More than one origin for a person in this task")
        seen.add(case.person)
        scored.append(
            _score(
                case.person,
                case.days,
                case.values,
                horizon,
                unit,
                lambda: (case.target_gap, case.target),
            )
        )
    scored.sort(key=lambda r: (isinstance(r.person, str), r.person))
    return _report(
        task,
        scored,
        unit,
        horizon,
        clock,
        input_sha256,
        source_counts or {},
        strata,
        intervals,
    )


def _historical_bariatric(rows, days, target, index_count, return_count):
    cases = [r for r in rows if r.observed is not None]
    y = np.array([r.observed for r in cases])
    methods = {}
    errors = []
    for name in ("last", "ses", "robust_slope"):
        error = np.array([r.predictions[name].value for r in cases]) - y
        errors.append(error)
        absolute = np.abs(error)
        methods[name] = dict(
            mae=float(absolute.mean()),
            rmse=float(np.sqrt(np.mean(error**2))),
            mean_signed_error=float(error.mean()),
            p90_absolute_error=float(np.quantile(absolute, 0.9)),
        )
    difference = np.abs(errors[1]) - np.abs(errors[0])
    return dict(
        n=len(cases),
        methods=methods,
        ses_minus_last_mae=float(difference.mean()),
        bootstrap95=bootstrap(difference, 5000, 307 + days[-1]).tolist(),
        history_nominal_days=list(days),
        index_day=days[-1],
        target_day=target,
        n_with_index=index_count,
        n_with_index_and_target=return_count,
    )


def _bariatric(path, digest, corrected):
    people = bariatric.read_visits(path)
    counts = {"cohort_n": len(people), "long_records": sum(map(len, people.values()))}
    comparisons = {}
    unit, clock = (
        "HbA1c percentage points",
        "scheduled visit columns, without exact sample or availability timestamps",
    )
    for label, days, target in bariatric.comparisons():
        index_count, return_count = 0, 0
        scored = []
        for key, visits in people.items():
            if days[-1] not in visits:
                continue
            index_count += 1
            if all(day in visits for day in days):
                scored.append(
                    _score(
                        key,
                        days,
                        tuple(visits[d] for d in days),
                        target - days[-1],
                        unit,
                        lambda: (
                            (target - days[-1], visits[target])
                            if target in visits
                            else None
                        ),
                    )
                )
            return_count += target in visits
        if corrected:
            source_counts = dict(
                counts, with_index=index_count, with_index_and_target=return_count
            )
            comparisons[label] = _report(
                BARIATRIC,
                scored,
                unit,
                target - days[-1],
                clock,
                digest,
                source_counts,
                False,
                intervals=True,
            )
            comparisons[label]["history_nominal_days"] = list(days)
            comparisons[label]["target_day"] = target
        else:
            comparisons[label] = _historical_bariatric(
                scored, days, target, index_count, return_count
            )
    if corrected:
        primary = comparisons.pop(bariatric.PRIMARY)
        primary["other_visit_analyses"] = comparisons
        return primary
    return {
        "doi": "10.7910/DVN/T9OBE3",
        "sha256": digest,
        "unit": unit,
        **counts,
        "primary": bariatric.PRIMARY,
        "n_bootstrap_per_comparison": 5000,
        "date_basis": clock,
        "outcome": "later recorded HbA1c; paired frozen methods, no fit, one pair per person per comparison",
        "comparisons": comparisons,
    }


def _historical_cbc_group(rows, seed):
    names = METHODS[:5]
    y = np.asarray([r.observed for r in rows])
    predictions = np.asarray(
        [[r.predictions[name].value for name in names] for r in rows]
    )
    errors = predictions - y[:, None]
    methods = {}
    for i, name in enumerate(names):
        error = errors[:, i]
        absolute = np.abs(error)
        methods[name] = dict(
            mae_g_dl=float(absolute.mean()),
            median_ae=float(np.median(absolute)),
            p90_ae=float(np.quantile(absolute, 0.9)),
            bias_g_dl=float(error.mean()),
            rmse=float(np.sqrt(np.mean(error**2))),
            n_negative_predictions=int((predictions[:, i] < 0).sum()),
        )
    differences = np.abs(errors[:, [1, 4]]) - np.abs(errors[:, [0]])
    bounds = bootstrap(differences, 3000, seed)
    return dict(
        n=len(rows),
        methods=methods,
        ses_minus_last_mae=float(differences[:, 0].mean()),
        paired_95_percentile_bootstrap_ci=bounds[:, 0].tolist(),
        exploratory_gated_minus_last_mae=float(differences[:, 1].mean()),
        exploratory_gated_minus_last_ci=bounds[:, 1].tolist(),
    )


def _lippe(path, digest, corrected):
    counts, scored = {}, []
    unit = "g/dL"
    for person, daily in lippe.iter_people(path, counts, corrected=corrected):
        if len(daily) < 4:
            continue
        days, values = zip(*daily[:4])
        scored.append(
            _score(
                person,
                days,
                values,
                lippe.HORIZON,
                unit,
                lambda: lippe.select_target(daily[4:], days[-1]),
            )
        )
    if corrected:
        return _report(
            LIPPE,
            scored,
            unit,
            lippe.HORIZON,
            "released shifted draw dates; collection proxy",
            digest,
            counts,
            True,
            intervals=True,
        )
    paired = [r for r in scored if r.observed is not None]
    groups = _cbc_groups(paired)
    return {
        "source": "10.5281/zenodo.15674541",
        "cache_sha256": REFERENCE_CACHE,
        "marker": "HB (hemoglobin, g/dL)",
        **counts,
        "with_four_days": len(scored),
        "with_outcome_in_window": len(paired),
        "observed_fraction_after_fourth": len(paired) / len(scored),
        "exploratory_gate_day_threshold_imported_from_repo": SPAN_GATE,
        "history_span_quartiles_days": np.quantile(
            [r.span for r in paired], [0.25, 0.5, 0.75]
        ).tolist(),
        "target_gap_quartiles_days": np.quantile(
            [r.gap for r in paired], [0.25, 0.5, 0.75]
        ).tolist(),
        "design": CBC_DESIGN,
        "comparisons": {
            key: _historical_cbc_group(group, 174 + i)
            for i, (key, group) in enumerate(groups.items())
        },
    }


def replay(task: str, path: Path, *, corrected: bool = False) -> dict:
    """Fingerprint the publisher's original, then return aggregates for the selected protocol."""
    path = Path(path)
    digest = validate_source(task, path)["sha256"]
    if task == BARIATRIC:
        return _bariatric(path, digest, corrected)
    return _lippe(path, digest, corrected)


def demo() -> dict:
    """Invented histories run through the same corrected numerical evaluator."""
    cases = [
        Case("invented-a", (0, 1, 2, 3), (16.0, 14.0, 12.0, 10.0), 11.0, 90),
        Case("invented-b", (0, 90, 180, 365), (11.0, 12.0, 13.0, 14.0), 14.5, 91),
        Case("invented-c", (0, 7, 14, 21), (10.0, 10.0, 10.0, 10.0), None),
        Case("invented-d", (0, 8, 14, 56), (10.0, 11.0, 12.0, 13.0), 13.0, 87),
    ]
    return corrected_cases(
        "synthetic-demo",
        cases,
        90,
        "g/dL",
        clock="invented draw days",
        source_counts={"persons": len(cases)},
        strata=True,
        intervals=True,
    )
