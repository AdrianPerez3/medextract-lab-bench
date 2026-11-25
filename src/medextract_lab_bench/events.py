"""First documented post-index measurements, observed follow-up and research baselines."""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from itertools import groupby
from functools import partial
from math import isfinite
import operator
from statistics import fmean
from typing import Iterable, Sequence

from .calibration import Estimate, IntervalCase, split_prediction_intervals
from .records import (
    HB,
    A1C,
    CGMReading,
    Measurement,
    Point,
    _utc,
    as_of,
    gmi_window,
    identifier,
    normalize_value,
)


def _time(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError("expected_nonnegative_finite_time")
    return value


def _horizons(cuts: Iterable[float], *, allow_zero=False) -> tuple[float, ...]:
    cuts = tuple(_time(v) for v in cuts)
    if (
        not cuts
        or (cuts[0] == 0 and not allow_zero)
        or any(a >= b for a, b in zip(cuts, cuts[1:]))
    ):
        raise ValueError("expected_increasing_horizons")
    return cuts


@dataclass(frozen=True, order=True)
class CaseKey:
    source: str
    person: str
    concept: str
    index: datetime
    clock: str = "elapsed_utc_days"

    def __post_init__(self):
        identifier(self.source)
        identifier(self.person)
        identifier(self.concept)
        object.__setattr__(self, "index", _utc(self.index))
        if self.clock != "elapsed_utc_days":
            raise ValueError("unsupported_clock")


def align_cases(left: Sequence[CaseKey], right: Sequence[CaseKey]) -> None:
    if len(set(left)) != len(left) or len(set(right)) != len(right):
        raise ValueError("duplicate_case_key")
    if tuple(left) != tuple(right):
        raise ValueError("ordered_case_keys_differ")


def require_disjoint_people(*splits: Iterable[CaseKey]) -> None:
    seen = set()
    for keys in splits:
        people = {(k.source, k.person) for k in keys}
        if people & seen:
            raise ValueError("person_split_overlap")
        seen.update(people)


@dataclass(frozen=True)
class EventRule:
    comparison: str
    threshold: float
    incident_first_ever: bool = True

    def __post_init__(self):
        if self.comparison not in ("lt", "le", "gt", "ge") or not isfinite(
            self.threshold
        ):
            raise ValueError("invalid_event_rule")
        if type(self.incident_first_ever) is not bool:
            raise ValueError("invalid_entry_policy")

    def qualifies(self, value: float) -> bool:
        return getattr(operator, self.comparison)(value, self.threshold)


@dataclass(frozen=True)
class FollowUp:
    duration: float
    event: bool
    last_assessment: float | None = None

    def __post_init__(self):
        _time(self.duration)
        if type(self.event) is not bool or (self.event and self.duration == 0):
            raise ValueError("invalid_incident_event_flag_or_duration")
        if self.last_assessment is not None:
            last = _time(self.last_assessment)
            if last < self.duration or (not self.event and last != self.duration):
                raise ValueError("inconsistent_last_assessment")


@dataclass(frozen=True)
class CompetingEvent:
    cause: str
    time: datetime

    def __post_init__(self):
        identifier(self.cause)
        object.__setattr__(self, "time", _utc(self.time))


@dataclass(frozen=True)
class EventCase:
    key: CaseKey
    history: tuple[Point, ...]
    status: str
    outcome: FollowUp | None = None
    last_nonqualifying: datetime | None = None
    first_qualifying: datetime | None = None
    competing: CompetingEvent | None = None
    rule: EventRule | None = None
    collection_proxy_allowed: bool = False


def build_case(
    rows: Iterable[Measurement],
    key: CaseKey,
    rule: EventRule,
    *,
    analysis_time: datetime,
    allow_collection_proxy: bool = False,
    competing: CompetingEvent | None = None,
) -> EventCase:
    """Freeze history at index. Ascertain subsequent effective results available at analysis_time."""
    end = _utc(analysis_time)
    if end < key.index:
        raise ValueError("analysis_before_index")
    rows = tuple(rows)
    options = dict(
        source=key.source,
        person=key.person,
        concept=key.concept,
        allow_collection_proxy=allow_collection_proxy,
    )
    history = as_of(rows, cutoff=key.index, **options).points
    case = partial(
        EventCase,
        key,
        history,
        rule=rule,
        collection_proxy_allowed=allow_collection_proxy,
    )
    anchor_states = {
        rule.qualifies(p.value) for p in history if p.effective == key.index
    }
    if not anchor_states:
        return case("missing_anchor_assessment")
    if len(anchor_states) > 1:
        return case("ambiguous_anchor")
    if True in anchor_states:
        return case("prevalent")
    if rule.incident_first_ever and any(rule.qualifies(p.value) for p in history):
        return case("prior_qualifying")
    outcomes = as_of(rows, cutoff=end, **options).points
    last_nonqualifying = last_assessed = key.index
    first = ambiguous = None
    for t, group in groupby(
        (p for p in outcomes if p.effective > key.index), key=lambda p: p.effective
    ):
        state = {rule.qualifies(p.value) for p in group}
        if first is None:
            if len(state) > 1:
                ambiguous = t
                break
            if True in state:
                first = t
            else:
                last_nonqualifying = t
        last_assessed = t
    # Cause-specific and subdistribution estimands are not registered for this protocol.
    if (
        competing
        and competing.time <= end
        and (first is None or competing.time <= first)
    ):
        return case(
            "competing_endpoint_unavailable",
            last_nonqualifying=last_nonqualifying,
            competing=competing,
        )
    if ambiguous:
        return case(
            "ambiguous_future_assessment",
            last_nonqualifying=last_nonqualifying,
            competing=competing if competing and competing.time <= end else None,
        )
    days = timedelta(days=1)
    followup = FollowUp(
        ((first or last_assessed) - key.index) / days,
        first is not None,
        (last_assessed - key.index) / days,
    )
    return case(
        "eligible",
        followup,
        last_nonqualifying,
        first,
        competing if competing and competing.time <= end else None,
    )


def horizon_status(record: FollowUp, horizon: float) -> int | None:
    horizon = _time(horizon)
    if record.event:
        return int(record.duration <= horizon)
    return 0 if record.duration >= horizon else None


@dataclass(frozen=True)
class IntervalSupport:
    start: float
    end: float
    entering: int
    known: int
    events: int
    pre_end_censors: int


def interval_support(
    records: Sequence[FollowUp], horizons: Iterable[float]
) -> list[IntervalSupport]:
    end_points = _horizons(horizons)
    result = []
    for left, right in zip((0, *end_points), end_points):
        entering = [r for r in records if left == 0 or r.duration > left]
        events = sum(r.event and r.duration <= right for r in entering)
        censors = sum(not r.event and r.duration < right for r in entering)
        result.append(
            IntervalSupport(
                left, right, len(entering), len(entering) - censors, events, censors
            )
        )
    return result


def fit_interval_risk(
    training: Sequence[FollowUp], horizons: Iterable[float]
) -> list[Estimate]:
    """Fit an intercept-only empirical hazard per completed interval, composing supported hazards."""
    cumulative = []
    survival = 1.0
    supported = True
    for interval in interval_support(training, horizons):
        if not interval.known or not supported:
            reason = (
                "no_training_support" if supported else "unsupported_preceding_interval"
            )
            supported = False
            cumulative.append(Estimate(None, reason))
            continue
        survival *= 1 - interval.events / interval.known
        cumulative.append(Estimate(1 - survival))
    return cumulative


def median_horizon(horizons: Sequence[float], risks: Sequence[Estimate]) -> Estimate:
    cuts = _horizons(horizons)
    if len(cuts) != len(risks):
        raise ValueError("risk_horizon_length_mismatch")
    for day, risk in zip(cuts, risks):
        if risk.value is None:
            return Estimate(None, "unsupported_horizon")
        if risk.value >= 0.5:
            return Estimate(day)
    return Estimate(None, "median_not_reached")


def _km_curve(records: Sequence[FollowUp]) -> tuple[list[float], list[float]]:
    """Each time's failures act on the risk set immediately before any same-time censor leaves."""
    counts = Counter(r.duration for r in records)
    events = Counter(r.duration for r in records if r.event)
    times = sorted(counts)
    remaining = len(records)
    survival = 1.0
    risk = []
    for t in times:
        survival *= 1 - events[t] / remaining
        risk.append(1 - survival)
        remaining -= counts[t]
    return times, risk


def _step_at(times: list[float], values: list[float], horizon: float) -> float:
    index = bisect_right(times, horizon) - 1
    return values[index] if index >= 0 else 0.0


def km_risk(
    records: Sequence[FollowUp], horizons: Iterable[float]
) -> list[float | None]:
    cuts = _horizons(horizons, allow_zero=True)
    times, values = _km_curve(records)
    return [
        _step_at(times, values, h) if times and h <= times[-1] else None for h in cuts
    ]


def pseudo_values(
    records: Sequence[FollowUp],
    horizons: Iterable[float],
    *,
    people: Sequence | None = None,
) -> list[list[float | None]]:
    """One row per person in first-appearance order. Leave all of their landmarks out together."""
    cuts = _horizons(horizons, allow_zero=True)
    records = tuple(records)
    full = km_risk(records, cuts)
    owners = tuple(range(len(records))) if people is None else tuple(people)
    if len(owners) != len(records):
        raise ValueError("person_outcome_length_mismatch")
    distinct = tuple(dict.fromkeys(owners))
    n = len(distinct)
    if n < 2:
        return [[None] * len(cuts) for _ in distinct]
    matrix = []
    for owner in distinct:
        times, curve = _km_curve([r for r, key in zip(records, owners) if key != owner])
        matrix.append(
            [
                None if risk is None else n * risk - (n - 1) * _step_at(times, curve, h)
                for h, risk in zip(cuts, full)
            ]
        )
    return matrix


def fit_pseudo_risk(
    training: Sequence[FollowUp],
    horizons: Iterable[float],
    *,
    people: Sequence | None = None,
) -> list[Estimate]:
    """Intercept-only least squares on per-person pseudo-outcomes, with explicit support."""
    cuts = _horizons(horizons)
    matrix = pseudo_values(training, cuts, people=people)
    output = []
    for i in range(len(cuts)):
        if len(matrix) < 2:
            output.append(Estimate(None, "insufficient_jackknife_people"))
        elif matrix[0][i] is None:
            output.append(Estimate(None, "no_training_support"))
        else:
            value = fmean(row[i] for row in matrix)
            output.append(
                Estimate(value)
                if 0 <= value <= 1
                else Estimate(None, "invalid_fitted_probability")
            )
    return output


def concordance(outcomes: Sequence[FollowUp], risks: Sequence[float]) -> Estimate:
    """Harrell's C; event precedes censor at the same instant. Equal event times are incomparable."""
    if len(outcomes) != len(risks) or any(not isfinite(p) for p in risks):
        raise ValueError("invalid_or_misaligned_risks")
    concordant = comparable = 0
    for i, earlier in enumerate(outcomes):
        if not earlier.event:
            continue
        for j, other in enumerate(outcomes):
            if other.duration > earlier.duration or (
                other.duration == earlier.duration and not other.event
            ):
                comparable += 1
                concordant += (risks[i] > risks[j]) + 0.5 * (risks[i] == risks[j])
    return (
        Estimate(concordant / comparable)
        if comparable
        else Estimate(None, "no_comparable_pairs")
    )


def conditional_score(
    outcomes: Sequence[FollowUp],
    risks: Sequence[float | None],
    horizon: float,
    *,
    bins: int = 10,
) -> dict:
    horizon = _time(horizon)
    if len(outcomes) != len(risks) or type(bins) is not int or bins < 1:
        raise ValueError("invalid_bins_or_misaligned_risks")
    labels = [horizon_status(r, horizon) for r in outcomes]
    paired = []
    strata = defaultdict(list)
    for y, p in zip(labels, risks):
        if p is None:
            continue
        if not isfinite(p) or not 0 <= p <= 1:
            raise ValueError("invalid_probability")
        if y is not None:
            paired.append((y, p))
            strata[min(int(p * bins), bins - 1)].append((y, p))
    counts = Counter(labels)
    reason = (
        None
        if paired
        else (
            "no_known_outcomes"
            if not counts[0] + counts[1]
            else "no_supported_forecast"
        )
    )
    calibration = [
        {
            "bin": k,
            "n": len(pairs),
            "mean_probability": fmean(p for _, p in pairs),
            "observed_fraction": fmean(y for y, _ in pairs),
        }
        for k, pairs in sorted(strata.items())
    ]
    return {
        "horizon": horizon,
        "cases": len(outcomes),
        "positive": counts[1],
        "negative": counts[0],
        "unknown": counts[None],
        "predicted": sum(p is not None for p in risks),
        "evaluated": len(paired),
        "conditional_brier": fmean((y - p) ** 2 for y, p in paired) if paired else None,
        "conditional_calibration": calibration,
        "reason": reason,
    }


def event_scorecard(
    training: Sequence[EventCase],
    evaluation: Sequence[EventCase],
    horizons: Iterable[float],
) -> dict:
    """Fit intercept-only baselines on training people and report complete-status test performance."""
    cuts = _horizons(horizons)
    for split in (training, evaluation):
        keys = [c.key for c in split]
        align_cases(keys, keys)
    require_disjoint_people([c.key for c in training], [c.key for c in evaluation])
    task = {
        (c.key.concept, c.key.clock, c.rule, c.collection_proxy_allowed)
        for c in (*training, *evaluation)
    }
    if len(task) > 1:
        raise ValueError("mixed_event_tasks")
    train, test = (
        [c.outcome for c in split if c.outcome is not None]
        for split in (training, evaluation)
    )
    risks = fit_interval_risk(train, cuts)
    pseudo = fit_pseudo_risk(
        train,
        cuts,
        people=[
            (c.key.source, c.key.person) for c in training if c.outcome is not None
        ],
    )

    def performance(curve):
        return [
            dict(
                forecast=asdict(p), **conditional_score(test, [p.value] * len(test), h)
            )
            for p, h in zip(curve, cuts)
        ]

    return {
        "estimand": "first_documented_qualifying_post_index_measurement",
        "training_status": dict(sorted(Counter(c.status for c in training).items())),
        "evaluation_status": dict(
            sorted(Counter(c.status for c in evaluation).items())
        ),
        "training_support": [asdict(s) for s in interval_support(train, cuts)],
        "evaluation_support": [asdict(s) for s in interval_support(test, cuts)],
        "completed_interval_empirical_hazard": performance(risks),
        "km_pseudo_intercept": performance(pseudo),
        "interval_median_days": asdict(median_horizon(cuts, risks)),
    }


def synthetic_checks() -> dict:
    """Produce aggregate-only diagnostics on invented observations, available through the public CLI."""
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rule = EventRule("le", 10.0)

    def cohort(prefix, n):
        cases = []
        for i in range(n):
            person = f"{prefix}-{i:02d}"
            key = CaseKey("invented", person, HB, start)
            endpoint = start + timedelta(days=60 if i % 2 == 0 else 200)
            rows = [
                Measurement(
                    "invented",
                    person,
                    "baseline",
                    HB,
                    120,
                    "g/L",
                    start,
                    start,
                    kind="measured",
                ),
                Measurement(
                    "invented",
                    person,
                    "followup",
                    HB,
                    100 if i % 2 == 0 else 110,
                    "g/L",
                    endpoint,
                    endpoint,
                    kind="measured",
                ),
            ]
            cases.append(
                build_case(rows, key, rule, analysis_time=start + timedelta(days=200))
            )
        return cases

    train, test = cohort("train", 40), cohort("test", 4)
    source, person = "invented", "sensor"
    glucose = [
        CGMReading(source, person, t, 100.0, "mg/dL", t)
        for t in (start + timedelta(minutes=5 * i) for i in range(576))
    ]
    cgm = gmi_window(
        glucose,
        source=source,
        person=person,
        start=start,
        end=start + timedelta(days=30),
        as_of_time=start + timedelta(days=30),
        cadence=timedelta(minutes=5),
    )
    return {
        "task": "synthetic-events",
        "evidence": "invented_fixtures",
        "measurements": {
            "hb_g_per_dl_from_140_g_per_l": normalize_value(HB, 140, "g/L")[0],
            "ngsp_percent_from_ifcc_53": normalize_value(
                A1C, 53, "mmol/mol", standard="IFCC"
            )[0],
            "gmi": {
                "code": cgm.code,
                "value": cgm.value,
                "reason": cgm.status,
                "occupied": cgm.occupied,
                "expected": cgm.expected,
                "required": cgm.required,
                "largest_gap_seconds": cgm.largest_gap_seconds,
            },
        },
        "events": event_scorecard(train, test, [90, 180, 730, 1095]),
    }


def observed_event_intervals(
    calibration: Sequence[EventCase],
    calibration_times: dict[CaseKey, float | None],
    evaluation: Sequence[EventCase],
    evaluation_times: dict[CaseKey, float | None],
    alpha: float,
) -> dict:
    """Evaluate a days-to-measured-event band among people whose first event was documented."""

    def observations(cases, times):
        keys = [c.key for c in cases]
        if len(set(keys)) != len(keys) or set(keys) != times.keys():
            raise ValueError("event_time_prediction_key_mismatch")
        result = []
        for case in cases:
            prediction = times[case.key]
            if prediction is not None:
                _time(prediction)
            observed = (
                case.outcome.duration if case.outcome and case.outcome.event else None
            )
            result.append(
                IntervalCase(case.key.source, case.key.person, observed, prediction)
            )
        return result

    report = split_prediction_intervals(
        observations(calibration, calibration_times),
        observations(evaluation, evaluation_times),
        alpha,
    )
    return dict(
        report,
        estimand="interval_coverage_conditional_on_documented_event_and_forecast",
        unit="elapsed_days",
    )


def paired_concordance_delta(
    cases: Sequence[EventCase],
    first: dict[CaseKey, float],
    second: dict[CaseKey, float],
    *,
    draws: int = 5000,
    seed: int = 2601,
) -> dict:
    """Paired source-person percentile bootstrap on aligned eligible landmarks."""
    import numpy as np

    keys = [c.key for c in cases]
    if (
        len(set(keys)) != len(keys)
        or set(keys) != first.keys()
        or set(keys) != second.keys()
    ):
        raise ValueError("paired_risk_key_mismatch")
    if type(draws) is not int or draws <= 0:
        raise ValueError("invalid_bootstrap_draws")
    # Sort once so reordering input cannot change the seeded result.
    clusters = defaultdict(list)
    for case in sorted(cases, key=lambda c: c.key):
        if case.outcome is not None:
            clusters[case.key.source, case.key.person].append(
                (case.outcome, first[case.key], second[case.key])
            )
    groups = list(clusters.values())

    def difference(rows):
        outcomes = [x[0] for x in rows]
        a = concordance(outcomes, [x[1] for x in rows]).value
        if a is None:
            return None
        b = concordance(outcomes, [x[2] for x in rows]).value
        return a - b

    observed = difference([row for group in groups for row in group])
    deltas = []
    if observed is not None:
        rng = np.random.Generator(np.random.PCG64(seed))
        for _ in range(draws):
            chosen = rng.integers(0, len(groups), size=len(groups))
            delta = difference([row for j in chosen for row in groups[j]])
            if delta is not None:
                deltas.append(delta)
    bounds = (
        np.quantile(deltas, [0.025, 0.975], method="linear").tolist()
        if deltas
        else None
    )
    return {
        "delta_first_minus_second": observed,
        "ci95": bounds,
        "people": len(groups),
        "eligible_landmarks": sum(map(len, groups)),
        "draws": draws,
        "defined_draws": len(deltas),
        "undefined_draws": draws - len(deltas),
        "rng": "PCG64",
        "seed": seed,
        "percentile_method": "linear",
        "reason": None if observed is not None else "no_comparable_pairs",
    }
