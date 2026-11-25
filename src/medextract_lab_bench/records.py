"""Source-scoped, time-versioned measurement records and uniform-cadence GMI."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import math
import re
from statistics import fmean, median
from typing import Iterable
from zoneinfo import ZoneInfo

HB = "718-7"
A1C = "4548-4"
GMI = "97506-0"
UTC = timezone.utc
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def identifier(value: str) -> str:
    """Retain exact source keys, including leading zeroes."""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("invalid_identifier")
    return value


def _utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("timezone_required")
    return value.astimezone(UTC)


def parse_timestamp(
    text: str, *, zone: str | None = None, precision: str = "instant"
) -> datetime:
    """Parse an ISO timestamp or a documented whole calendar date at local midnight."""
    if not isinstance(text, str):
        raise ValueError("invalid_time")
    if precision == "day" and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        stamp = datetime.combine(date.fromisoformat(text), datetime.min.time())
    elif precision == "instant" and re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?"
        r"(?:Z|[+-][0-9]{2}:[0-9]{2})?",
        text,
    ):
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    else:
        raise ValueError("unsupported_time_precision")
    if stamp.tzinfo is not None:
        return _utc(stamp)
    if zone is None:
        raise ValueError("source_timezone_required")
    local_zone = ZoneInfo(zone)
    candidates = {
        stamp.replace(tzinfo=local_zone, fold=fold).astimezone(UTC) for fold in (0, 1)
    }
    valid = [
        t for t in candidates if t.astimezone(local_zone).replace(tzinfo=None) == stamp
    ]
    if len(valid) != 1:
        raise ValueError("ambiguous_or_nonexistent_local_time")
    return valid[0]


def _number(raw: object) -> float:
    if isinstance(raw, str):
        if not _NUMBER.fullmatch(raw.strip()):
            raise ValueError("imprecise_or_invalid_value")
    elif isinstance(raw, bool) or not isinstance(raw, (float, int, Decimal)):
        raise ValueError("imprecise_or_invalid_value")
    try:
        value = float(raw)
    except OverflowError as exc:
        raise ValueError("nonfinite_value") from exc
    if not math.isfinite(value):
        raise ValueError("nonfinite_value")
    return value


def normalize_value(
    concept: str,
    value: object,
    unit: str,
    *,
    standard: str | None = None,
    difference: bool = False,
) -> tuple[float, str]:
    """Return canonical research units. An HbA1c result must declare standardization."""
    value = _number(value)
    if not difference and value < 0 and concept in (HB, A1C, GMI):
        raise ValueError("negative_absolute_value")
    if concept == HB:
        if unit == "g/L":
            return value / 10, "g/dL"
        if unit == "g/dL":
            return value, unit
        raise ValueError("unsupported_unit")
    if concept == A1C:
        if unit == "%" and standard == "NGSP":
            return value, "%"
        if unit == "mmol/mol" and standard == "IFCC":
            return 0.09148 * value + (0 if difference else 2.152), "%"
        raise ValueError("unsupported_unit_or_standard")
    if concept == GMI and unit == "%":
        return value, "%"
    raise ValueError("unsupported_unit" if concept == GMI else "unknown_concept")


@dataclass(frozen=True)
class Measurement:
    source: str
    person: str
    observation: str
    concept: str
    value: object
    unit: str
    effective: datetime
    available: datetime | None
    component: str = ""
    revision: int = 1
    status: str = "final"
    comparator: str = "="
    standard: str | None = None
    kind: str = "unknown"
    capture: str = "structured"
    method: str | None = None
    specimen: str | None = None
    precision: str = "instant"
    origin: str = "collection"
    nominal_day: int | None = None


@dataclass(frozen=True)
class Point:
    source: str
    person: str
    observation: str
    component: str
    concept: str
    value: float
    unit: str
    effective: datetime
    available: datetime
    availability_proxy: bool
    revision: int
    kind: str
    capture: str
    method: str | None
    specimen: str | None
    precision: str
    origin: str
    nominal_day: int | None


@dataclass(frozen=True)
class Snapshot:
    points: tuple[Point, ...]
    excluded: dict[str, int]


def _to_point(row: Measurement, available: datetime, cutoff: datetime) -> Point:
    effective = _utc(row.effective)
    if effective > cutoff:
        raise ValueError("future_effective")
    if available < effective:
        raise ValueError("issued_before_effective")
    if row.status not in ("final", "amended", "corrected"):
        raise ValueError(
            "withdrawn" if row.status == "entered-in-error" else "unsupported_status"
        )
    if row.comparator != "=":
        raise ValueError("unsupported_comparator")
    if row.kind != ("calculated" if row.concept == GMI else "measured"):
        raise ValueError("incompatible_measurement_kind")
    if row.capture not in ("structured", "ocr", "manual", "device", "calculation"):
        raise ValueError("unknown_capture")
    if (
        row.precision not in ("day", "instant")
        or not row.origin
        or row.origin == "unknown"
    ):
        raise ValueError("unsupported_clock")
    value, unit = normalize_value(
        row.concept, row.value, row.unit, standard=row.standard
    )
    return Point(
        row.source,
        row.person,
        row.observation,
        row.component,
        row.concept,
        value,
        unit,
        effective,
        available,
        row.available is None,
        row.revision,
        row.kind,
        row.capture,
        row.method,
        row.specimen,
        row.precision,
        row.origin,
        row.nominal_day,
    )


def as_of(
    rows: Iterable[Measurement],
    *,
    source: str,
    person: str,
    concept: str,
    cutoff: datetime,
    allow_collection_proxy: bool = False,
) -> Snapshot:
    """Resolve the latest visible version per logical observation/component before value filtering."""
    source, person = identifier(source), identifier(person)
    if concept not in (HB, A1C, GMI):
        raise ValueError("unknown_concept")
    cutoff = _utc(cutoff)
    versions = defaultdict(list)
    unknown_versions = defaultdict(list)
    excluded: Counter[str] = Counter()
    for row in rows:
        if (identifier(row.source), identifier(row.person)) != (source, person):
            continue
        identifier(row.observation)
        if not isinstance(row.component, str):
            raise ValueError("invalid_component")
        if type(row.revision) is not int or row.revision < 1:
            raise ValueError("invalid_revision")
        if row.available is None and (
            not allow_collection_proxy or row.revision != 1 or row.status != "final"
        ):
            if _utc(row.effective) > cutoff:
                continue
            unknown_versions[row.observation, row.component].append(row)
            if row.concept == concept:
                excluded["unknown_availability"] += 1
            continue
        issued = _utc(row.effective if row.available is None else row.available)
        if issued <= cutoff:
            versions[row.observation, row.component].append((row, issued))
    points = []
    for key, versions_of_result in versions.items():
        revision = max(row.revision for row, _ in versions_of_result)
        if any(r.revision >= revision for r in unknown_versions[key]):
            if any(r.concept == concept for r, _ in versions_of_result):
                excluded["unresolved_revision_availability"] += 1
            continue
        latest = [
            (row, issued)
            for row, issued in versions_of_result
            if row.revision == revision
        ]
        if not any(row.concept == concept for row, _ in latest):
            excluded["unknown_concept"] += sum(
                r.concept not in (HB, A1C, GMI) for r, _ in latest
            )
            continue
        row, issued = latest[0]
        if any(candidate != row for candidate, _ in latest[1:]):
            excluded["conflicting_revision"] += len(latest)
            continue
        if len(latest) > 1:
            excluded["duplicate"] += len(latest) - 1
        try:
            points.append(_to_point(row, issued, cutoff))
        except ValueError as exc:
            excluded[str(exc)] += 1
    points.sort(key=lambda p: (p.effective, p.observation, p.component))
    return Snapshot(tuple(points), dict(sorted((+excluded).items())))


def cbc_daily_medians(points: Iterable[Point], zone: str) -> dict[date, float]:
    """Reduce distinct accepted Hb observations from one scoped person in local calendar days."""
    grouped = defaultdict(list)
    owner = None
    seen = set()
    local_zone = ZoneInfo(zone)
    for p in points:
        if p.concept != HB or p.kind != "measured" or p.unit != "g/dL":
            raise ValueError("expected_measured_hemoglobin")
        if owner is not None and (p.source, p.person) != owner:
            raise ValueError("mixed_people")
        owner = p.source, p.person
        key = p.observation, p.component
        if key in seen:
            raise ValueError("resolve_versions_before_daily_reduction")
        seen.add(key)
        grouped[p.effective.astimezone(local_zone).date()].append(p.value)
    return {d: median(grouped[d]) for d in sorted(grouped)}


@dataclass(frozen=True)
class CGMReading:
    source: str
    person: str
    effective: datetime
    value: object
    unit: str
    available: datetime
    nominal_cadence: timedelta | None = None


@dataclass(frozen=True)
class GMIWindow:
    source: str
    person: str
    window_start: datetime
    window_end: datetime
    cadence_seconds: float
    minimum_coverage: float
    expected: int
    required: int
    occupied: int
    observed_span_seconds: float | None
    largest_gap_seconds: float
    data_start: datetime | None
    data_end: datetime | None
    duplicates: int
    conflicts: int
    invalid_readings: int
    cadence_mismatches: int
    status: str
    value: float | None
    code: str = GMI

    def as_measurement(self) -> Measurement:
        if self.value is None:
            raise ValueError(self.status)
        key = f"gmi/{self.window_start.isoformat()}/{self.window_end.isoformat()}/{self.cadence_seconds}"
        return Measurement(
            self.source,
            self.person,
            key,
            GMI,
            self.value,
            "%",
            self.window_end,
            self.window_end,
            kind="calculated",
            capture="calculation",
            method="GMI_3.31_plus_0.02392_mean_mg_dL",
            origin="assessment_end",
        )


def gmi_window(
    rows: Iterable[CGMReading],
    *,
    source: str,
    person: str,
    start: datetime,
    end: datetime,
    as_of_time: datetime,
    cadence: timedelta,
    minimum_coverage: float = 0.70,
) -> GMIWindow:
    """Assess [start,end) on a declared fixed cadence anchored at start, freezing results at end."""
    source, person = identifier(source), identifier(person)
    start, end, as_of_time = _utc(start), _utc(end), _utc(as_of_time)
    duration = end - start
    if cadence <= timedelta(0) or duration <= timedelta(0) or duration % cadence:
        raise ValueError("window_must_contain_positive_whole_slots")
    if not 0 < minimum_coverage <= 1:
        raise ValueError("invalid_coverage")
    expected = duration // cadence
    required = int(
        (Decimal(str(minimum_coverage)) * expected).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    available_by = min(end, as_of_time)
    per_time = defaultdict(set)
    duplicates = invalid = cadence_mismatches = 0
    for row in rows:
        if (identifier(row.source), identifier(row.person)) != (source, person):
            continue
        t = _utc(row.effective)
        if t < start or t >= end or t > available_by:
            continue
        issued = _utc(row.available)
        if issued > available_by:
            continue
        if issued < t:
            invalid += 1
            continue
        if row.nominal_cadence is not None and row.nominal_cadence != cadence:
            cadence_mismatches += 1
            continue
        try:
            value = _number(row.value)
            if row.unit != "mg/dL" or value <= 0:
                raise ValueError("invalid_glucose")
        except ValueError:
            invalid += 1
            continue
        if value in per_time[t]:
            duplicates += 1
        per_time[t].add(value)
    slots = defaultdict(list)
    conflict_slots = set()
    conflicts = 0
    for t, values in per_time.items():
        slot = (t - start) // cadence
        if len(values) > 1:
            conflict_slots.add(slot)
            conflicts += 1
        else:
            slots[slot].append((t, next(iter(values))))
    occupied = sorted(slots.keys() - conflict_slots)
    times = [t for slot in occupied for t, _ in slots[slot]]
    first, last = (min(times), max(times)) if times else (None, None)
    # Count missing nominal slots, including the leading and trailing window edges.
    gaps = [b - a - 1 for a, b in zip([-1, *occupied], [*occupied, expected])]
    status = "ok"
    value = None
    if as_of_time < end:
        status = "window_not_ended"
    elif cadence_mismatches:
        status = "unsupported_cadence"
    elif len(occupied) < required:
        status = "insufficient_coverage"
    else:
        slot_means = [fmean(v for _, v in sorted(slots[slot])) for slot in occupied]
        value = 3.31 + 0.02392 * fmean(slot_means)
    return GMIWindow(
        source,
        person,
        start,
        end,
        cadence.total_seconds(),
        float(minimum_coverage),
        expected,
        required,
        len(occupied),
        (last - first).total_seconds() if times else None,
        max(gaps) * cadence.total_seconds(),
        first,
        last,
        duplicates,
        conflicts,
        invalid,
        cadence_mismatches,
        status,
        value,
    )
