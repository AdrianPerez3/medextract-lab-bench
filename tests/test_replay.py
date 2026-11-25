import csv
import json
from datetime import datetime, timedelta

import pytest
from openpyxl import Workbook

from medextract_lab_bench.adapters import bariatric, lippe
from medextract_lab_bench.replay import BARIATRIC, Case, corrected_cases, demo, replay


def write_wide(path, people):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=("NO", *bariatric.VISITS), delimiter="\t"
        )
        writer.writeheader()
        for key, visits in people:
            row = {
                field: visits[day]
                for field, day in bariatric.VISITS.items()
                if day in visits
            }
            writer.writerow({"NO": key, **row})


def test_invented_bariatric_source_preserves_keys_and_visit_gaps(tmp_path):
    from medextract_lab_bench.replay import _bariatric

    path = tmp_path / "invented.tab"
    base = {0: 7.2, 90: 7, 180: 6.75, 270: 6.25, 365: 6.5}
    write_wide(
        path,
        [
            ("01", base),
            ("1", {d: v for d, v in base.items() if d != 90}),
            ("2", {d: v for d, v in base.items() if d != 365}),
        ],
    )
    assert list(bariatric.read_visits(path)) == ["01", "1", "2"]
    card = _bariatric(path, None, True)
    assert (card["eligible_people"], card["paired_people"]) == (2, 1)
    assert card["support"]["source_counts"]["with_index_and_target"] == 2
    assert len(card["other_visit_analyses"]) == 6
    assert card["horizon_days"] == 95


def test_altered_source_rejected_before_parsing(tmp_path):
    path = tmp_path / "wrong-source"
    path.write_text("invented malformed content")
    with pytest.raises(ValueError, match="SHA-256"):
        replay(BARIATRIC, path)


def test_duplicate_subject_or_ambiguous_result_rejected(tmp_path):
    path = tmp_path / "invented.tab"
    write_wide(path, [("01", {0: 8.2}), ("01", {90: 7.2})])
    with pytest.raises(ValueError, match="duplicated"):
        bariatric.read_visits(path)
    write_wide(path, [("01", {0: "<7.2"})])
    with pytest.raises(ValueError, match="numeric"):
        bariatric.read_visits(path)


def workbook(path, rows):
    book = Workbook()
    sheet = book.active
    sheet.append(("HB", "PatientNum", "Timestamp"))
    for person, day, hb in rows:
        sheet.append((hb, person, datetime(2001, 1, 1) + timedelta(days=day)))
    book.save(path)
    book.close()


def test_workbook_external_sort_daily_median_and_evaluator_tie(tmp_path):
    from medextract_lab_bench.replay import _lippe

    path = tmp_path / "invented.xlsx"
    rows = [
        (2, 9, 13),
        (1, 0, 11),
        (2, 1, 15),
        (1, 2, 11),
        (1, 3, 12),
        (1, 1, 9),
        (2, 4, 15),
        (1, 3, 10),
        (1, 3 + 91, 18),
        (2, 5, 14),
        (1, 3 + 89, 13),
    ]
    workbook(path, rows)
    counts = {}
    first = list(lippe.iter_people(path, counts))
    assert counts == {"raw_records": 11, "persons": 2, "person_days": 10}
    assert first[0][1][3] == (first[0][1][0][0] + 3, 11.0)
    expected = _lippe(path, None, True)
    assert (expected["eligible_people"], expected["paired_people"]) == (2, 1)
    assert expected["metrics"]["last"]["mae"] == 2
    workbook(path, reversed(rows))
    assert _lippe(path, None, True) == expected


@pytest.mark.parametrize(
    "gaps,winner",
    [
        ([75], None),
        ([76], 76),
        ([104], 104),
        ([105], None),
        ([104, 76], 76),
        ([91, 89], 89),
        ([90, 91], 90),
    ],
)
def test_window_and_earlier_tie(gaps, winner):
    result = lippe.select_target([(10 + gap, 11.0) for gap in gaps], 10)
    assert (None if result is None else result[0]) == winner


def test_missing_or_nonfinite_source_values_fail_with_a_diagnostic(tmp_path):
    path = tmp_path / "invented.xlsx"
    workbook(path, [(1, 8, "<12")])
    with pytest.raises(ValueError, match="hemoglobin"):
        list(lippe.iter_people(path, {}))


def test_counts_retain_difficult_unobserved_and_negative_predictions():
    rows = [
        Case("A", (0, 1, 2, 3), (13.0, 12.0, 11.0, 10.0), 11.0, 90),
        Case("B", (0, 10, 20, 365), (10.0, 12.0, 13.0, 14.0), None),
    ]
    card = corrected_cases("invented", rows, 90, "g/dL", clock="invented", strata=True)
    assert card["metrics"]["ses"]["accepted_only"]["n"] == 0
    assert card["metrics"]["ses"]["n"] == card["metrics"]["ses"]["paired_n"] == 1
    assert card["methods"]["ses"]["paired"]["invalid_outputs"] == 1
    gate = card["methods"]["existing_span_gate_else_last"]
    assert gate["eligible"]["used_method"] == {"last": 1, "ses": 1}
    assert gate["paired"]["used_method"] == {"last": 1}
    assert (
        gate["eligible"]["prediction_coverage"]
        == gate["paired"]["prediction_coverage"]
        == 1.0
    )
    assert (
        card["strata"]["sensitivity_existing_365_day_duration"]["status"]
        == "insufficient_support"
    )


def test_future_values_only_change_metrics_not_issued_method_counts():
    def evaluate(target):
        return corrected_cases(
            "invented",
            [Case("01", (0, 9, 14, 38), (9.0, 10.0, 11.0, 12.0), target, 90)],
            90,
            "g/dL",
            clock="invented",
        )

    a, b = evaluate(12.0), evaluate(112.0)
    assert a["methods"] == b["methods"]
    assert a["metrics"]["last"]["mae"] == 0
    assert b["metrics"]["last"]["mae"] == 100
    assert b["metrics"]["ses"]["mean_signed_error"] == pytest.approx(
        a["metrics"]["ses"]["mean_signed_error"] - 100
    )


def test_empty_no_return_support_and_per_person_contract():
    card = corrected_cases("empty", [], 90, "g/dL", clock="invented")
    assert card["status"] == "insufficient_support" and card["eligible_people"] == 0
    assert card["metrics"]["last"]["paired_minus_last_ci95"] is None
    assert card["target_fraction_reason"] == "empty_denominator"
    row = Case("secret-invented-id", (0,), (12.0,), None)
    card = corrected_cases("empty", [row], 90, "g/dL", clock="invented")
    assert card["methods"]["last"]["eligible"]["predicted"] == 1
    assert card["metrics"]["last"]["reason"] == "no_evaluable_pairs"
    assert row.person not in json.dumps(card, allow_nan=False)
    with pytest.raises(ValueError, match="More than one"):
        corrected_cases("dup", [row, row], 90, "g/dL", clock="invented")


def test_synthetic_demo_is_deterministic_and_aggregate_only():
    assert demo() == demo()
    card = demo()
    assert card["eligible_people"] == 4 and card["paired_people"] == 3
    assert "invented-a" not in json.dumps(card, allow_nan=False)
    assert (
        card["prediction_intervals"]["95_percent"]["reason"]
        == "insufficient_calibration_support"
    )


def test_public_case_permutation_and_intervals_are_deterministic():
    rows = [
        Case(k, (0, 20, 40, 90), (10.0, 11.0, 10.0, 12.0), float(i % 15 + 1), 90)
        for i, k in enumerate([*range(16), "01", "1", "2"])
    ]

    def card(cases):
        return corrected_cases(
            "invented", cases, 90, "g/dL", clock="invented", intervals=True
        )

    assert card(rows) == card(reversed(rows))


@pytest.mark.parametrize(
    "metadata",
    [
        {"arbitrary_subject_key": 1},
        {"persons": "an identifier"},
        {"persons": True},
        {"persons": -1},
    ],
)
def test_source_counts_only_export_approved_numeric_fields(metadata):
    with pytest.raises(ValueError, match="source count"):
        corrected_cases(
            "invented", [], 90, "%", clock="invented", source_counts=metadata
        )


@pytest.mark.parametrize(
    "target,gap", [(True, 90), ("12", 90), (10.0, True), (10.0, "90"), (None, 90)]
)
def test_malformed_targets_rejected_before_scoring(target, gap):
    with pytest.raises(ValueError):
        corrected_cases(
            "invented",
            [Case("1", (0,), (10.0,), target, gap)],
            90,
            "g/dL",
            clock="invented",
        )


def test_input_fingerprint_is_validated():
    with pytest.raises(ValueError, match="fingerprint"):
        corrected_cases(
            "invented", [], 90, "g/dL", clock="invented", input_sha256="/local/file"
        )


def test_corrected_same_day_median_preserves_closed_decimal_boundary(tmp_path):
    from medextract_lab_bench.calibration import (
        IntervalCase,
        split_prediction_intervals,
    )

    path = tmp_path / "invented.xlsx"
    workbook(path, [(1, 3, 10.3), (1, 3, 10.9)])
    median_hb = list(lippe.iter_people(path, {}, corrected=True))[0][1][0][1]
    assert median_hb == 10.6
    evaluation = [IntervalCase("site", "eval", 7.1, median_hb)]
    calibration = [IntervalCase("site", "cal", 14.5, 11.0)]
    assert split_prediction_intervals(calibration, evaluation, 0.5)["covered"] == 1
