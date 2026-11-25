from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from medextract_lab_bench.records import (
    A1C,
    GMI,
    HB,
    CGMReading,
    Measurement,
    as_of,
    cbc_daily_medians,
    gmi_window,
    normalize_value,
    parse_timestamp,
)

T = datetime(2024, 1, 1, tzinfo=timezone.utc)


def measurement(**overrides):
    fields = dict(
        source="demo",
        person="001",
        observation="draw",
        kind="measured",
        concept=HB,
        value="140",
        unit="g/L",
        effective=T,
        available=T,
    )
    return Measurement(**(fields | overrides))


def snapshot(rows, cutoff=T, **options):
    return as_of(
        rows, source="demo", person="001", concept=HB, cutoff=cutoff, **options
    )


def test_units_and_provenance():
    assert normalize_value(HB, 140, "g/L") == (14, "g/dL")
    assert normalize_value(A1C, 53, "mmol/mol", standard="IFCC")[0] == pytest.approx(
        7.00044
    )
    assert normalize_value(A1C, 10, "mmol/mol", standard="IFCC", difference=True)[
        0
    ] == pytest.approx(0.9148)
    for kw in (
        dict(value="<5"),
        dict(value="7,4"),
        dict(value="5±1"),
        dict(value=float("inf")),
        dict(comparator="<"),
        dict(unit="mg/dL"),
        dict(kind="calculated"),
        dict(kind="unknown"),
    ):
        s = snapshot([measurement(**kw)])
        assert len(s.points) == 0 and sum(s.excluded.values()) == 1
    assert snapshot([measurement(capture="ocr")]).points[0].capture == "ocr"
    assert (
        as_of(
            [measurement(person="01"), measurement(person="1")],
            source="demo",
            person="1",
            concept=HB,
            cutoff=T,
        )
        .points[0]
        .person
        == "1"
    )
    with pytest.raises(ValueError):
        snapshot([measurement(person=None)])
    with pytest.raises(ValueError):
        normalize_value(A1C, 53, "mmol/mol")


def test_as_of_correction_is_order_independent_and_does_not_resurrect_old_values():
    late = T + timedelta(days=1)
    a = measurement()
    b = replace(a, revision=2, status="corrected", value=130, available=late)
    for rows in ((a, b), (b, a)):
        assert snapshot(rows).points[0].value == 14
        assert snapshot(rows, late).points[0].value == 13
    assert snapshot([a, replace(b, status="entered-in-error")], late).points == ()
    assert snapshot([a, replace(b, value="<5")], late).points == ()
    assert (
        snapshot([a, replace(b, effective=late + timedelta(days=1))], late).points == ()
    )
    assert snapshot([a, a]).excluded == {"duplicate": 1}
    assert snapshot([a, replace(a, value=120)]).excluded == {"conflicting_revision": 2}


def test_source_scope_components_availability_and_daily_timezone():
    b = measurement(
        effective=T + timedelta(hours=20),
        available=T + timedelta(hours=20),
        component="b",
        value=120,
    )
    rows = [measurement(), b, replace(b, person="99"), replace(b, source="other")]
    s = snapshot(rows, T + timedelta(days=1))
    assert [p.value for p in s.points] == [14, 12]
    assert list(cbc_daily_medians(s.points, "UTC").values()) == [13]
    assert list(cbc_daily_medians(s.points, "America/New_York").values()) == [14, 12]
    assert snapshot([measurement(available=None)]).excluded == {
        "unknown_availability": 1
    }
    assert (
        snapshot([measurement(available=None)], allow_collection_proxy=True)
        .points[0]
        .availability_proxy
    )
    with pytest.raises(ValueError):
        cbc_daily_medians([*s.points, replace(s.points[0], person="other")], "UTC")
    assert parse_timestamp("2024-01-01T01:00:00+01:00") == T
    assert parse_timestamp("2024-01-01", zone="UTC", precision="day") == T
    for bad in ["2024-01", "2024", "2024-01-01T01:00:00"]:
        with pytest.raises(ValueError):
            parse_timestamp(bad)
    for val in ["2024-11-03T01:30:00", "2024-03-10T02:30:00"]:
        with pytest.raises(ValueError):
            parse_timestamp(val, zone="America/New_York")


def glucose(minutes, value=110, **extras):
    stamp = T + timedelta(minutes=minutes)
    return CGMReading("demo", "001", stamp, value, "mg/dL", stamp, **extras)


def window(rows, days=30, minutes=5, **opts):
    end = T + timedelta(days=days)
    options = dict(
        source="demo",
        person="001",
        start=T,
        end=end,
        as_of_time=end,
        cadence=timedelta(minutes=minutes),
    )
    return gmi_window(rows, **(options | opts))


def test_slot_coverage_gaps_and_early_window():
    two_days = [glucose(i * 5) for i in range(576)]
    w = window(two_days)
    assert (w.value, w.occupied, w.expected, w.required) == (None, 576, 8640, 6048)
    assert w.largest_gap_seconds == 28 * 86400
    assert w.data_end == T + timedelta(minutes=575 * 5)
    assert w.observed_span_seconds == 575 * 300
    assert window(two_days, as_of_time=T).status == "window_not_ended"
    assert window([glucose(0)] * 2000).occupied == 1
    assert window([glucose(0)] * 2000).duplicates == 1999
    hourly = window([glucose(i * 60) for i in range(720)], minutes=60)
    assert hourly.code == GMI and hourly.value == pytest.approx(3.31 + 0.02392 * 110)
    assert hourly.occupied == hourly.expected and hourly.largest_gap_seconds == 0


def test_gmi_slot_mean_conflicts_availability_and_boundary():
    rows = [
        glucose(0, 100),
        glucose(0, 120),
        glucose(60, 100),
        glucose(61, 120),
        glucose(61, 120),
        glucose(120, 140),
        glucose(180, 10000),
    ]
    w = window(rows, end=T + timedelta(hours=3), minutes=60, minimum_coverage=0.6)
    assert (w.occupied, w.conflicts, w.duplicates) == (2, 1, 1)
    assert w.value == pytest.approx(3.31 + 0.02392 * 125)
    assert w.as_measurement().kind == "calculated"
    projected = as_of(
        [w.as_measurement()],
        source="demo",
        person="001",
        concept=A1C,
        cutoff=w.window_end,
    )
    assert projected.points == ()
    assert (
        as_of(
            [w.as_measurement()],
            source="demo",
            person="001",
            concept=GMI,
            cutoff=w.window_end,
        )
        .points[0]
        .available
        == w.window_end
    )
    late = replace(glucose(0), available=T + timedelta(days=2))
    assert window([late], days=1, minimum_coverage=0.001).occupied == 0
    with pytest.raises(ValueError):
        window([], minutes=0)
    with pytest.raises(ValueError):
        window([], days=0)


def test_unknown_issue_of_latest_revision_and_mixed_device_cadence():
    initial = measurement()
    uncertain = replace(initial, revision=2, available=None, value=12)
    assert not snapshot([initial, uncertain]).points
    assert (
        snapshot([initial, uncertain]).excluded["unresolved_revision_availability"] == 1
    )
    newer = replace(initial, revision=3)
    assert snapshot([uncertain, newer]).points[0].revision == 3
    rows = [glucose(0), glucose(5, nominal_cadence=timedelta(hours=1))]
    w = window(rows, end=T + timedelta(minutes=10), minimum_coverage=0.5)
    assert w.status == "unsupported_cadence" and w.cadence_mismatches == 1


def test_unknown_concept_and_missing_provenance_have_counted_paths():
    assert snapshot([measurement(concept="unknown")]).excluded == {"unknown_concept": 1}
    assert snapshot([measurement(kind="unknown")]).excluded == {
        "incompatible_measurement_kind": 1
    }
    assert snapshot([measurement(capture="unknown")]).excluded == {"unknown_capture": 1}
    # Collection-proxy opt-in cannot date the release of a correction.
    assert not snapshot(
        [measurement(), measurement(revision=2, status="corrected", available=None)],
        allow_collection_proxy=True,
    ).points


def test_negative_absolute_labs_excluded_before_standardization_but_differences_are_signed():
    for concept, unit, standard in [
        (HB, "g/L", None),
        (HB, "g/dL", None),
        (A1C, "%", "NGSP"),
        (A1C, "mmol/mol", "IFCC"),
        (GMI, "%", None),
    ]:
        with pytest.raises(ValueError, match="negative_absolute_value"):
            normalize_value(concept, -1, unit, standard=standard)
    assert normalize_value(A1C, -10, "mmol/mol", standard="IFCC", difference=True)[
        0
    ] == pytest.approx(-0.9148)
    assert normalize_value(HB, -4, "g/dL", difference=True)[0] == -4


def test_gmi_rejects_readings_reported_before_sampling():
    early = replace(glucose(5), available=T)
    w = window([early], end=T + timedelta(minutes=10), minimum_coverage=0.5)
    assert (w.occupied, w.invalid_readings, w.status, w.value) == (
        0,
        1,
        "insufficient_coverage",
        None,
    )
