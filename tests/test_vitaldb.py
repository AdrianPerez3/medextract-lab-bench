import csv
from hashlib import sha256

import pytest

from medextract_lab_bench.adapters import vitaldb


def write_csv(path, header, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)


def test_replay_checks_release_before_reading_clinical_values(tmp_path):
    (tmp_path / "clinical_data.csv").write_text("changed")
    with pytest.raises(ValueError, match="SHA-256"):
        vitaldb.run(tmp_path)


def test_smallest_case_chosen_before_examining_followup():
    roster = [
        {"caseid": "7", "subjectid": "1", "anestart": "10"},
        {"caseid": "2", "subjectid": "1", "anestart": ""},
        {"caseid": "5", "subjectid": "2", "anestart": "20"},
    ]
    selected, case_count = vitaldb._roster(roster)
    assert case_count == 3
    assert selected == {1: (2, None), 2: (5, 20)}
    with pytest.raises(ValueError, match="Duplicate"):
        vitaldb._roster(roster + [roster[0]])


def test_anesthesia_relative_floor_decimal_median_target_tie_and_bounds():
    selected = {1: (4, 300.0)}

    def draw(day, value, offset=0, name="hb", case=4):
        return {
            "caseid": str(case),
            "name": name,
            "result": str(value),
            "dt": str(300 + day * 86400 + offset),
        }

    draws = [
        draw(-91, 8),
        draw(-90, 12),
        draw(-40, 11),
        draw(-30, 13),
        draw(-14, 10.3, -1),
        draw(-14, 10.9, -2),
        draw(-14, 5, -1, "blood_gas_hb"),
        draw(73, 0),
        draw(77, 13),
        draw(90, 11),
        draw(0, "<10"),
        draw(1, 88, case=12),
    ]
    histories, counts = vitaldb._daily_draws(draws, selected)
    [(group, (days, values, target))] = vitaldb._histories(selected, histories)
    assert group == "complete"
    assert days == (-90, -40, -30, -15)
    assert values == (12.0, 11.0, 13.0, 10.6)
    assert target == 0.0
    assert counts == {
        "selected_hb_rows": 10,
        "unusable_time_or_value": 1,
        "outside_extract": 2,
    }


@pytest.mark.parametrize(
    "anchor,expected",
    [(-15, "complete"), (-14, "partial"), (13, "partial"), (14, "unobservable")],
)
def test_target_window_boundaries(anchor, expected):
    selected = {1: (4, 300.0)}
    draws = [
        {"caseid": "4", "name": "hb", "result": "12", "dt": str(300 + d * 86400)}
        for d in (-30, -29, -28, anchor)
    ]
    histories, _ = vitaldb._daily_draws(draws, selected)
    [(group, (_, _, target))] = vitaldb._histories(selected, histories)
    assert (group, target) == (expected, None)


def test_invented_source_replay_and_dictionary(tmp_path, monkeypatch):
    header = ("caseid", "dt", "name", "result")
    write_csv(
        tmp_path / "clinical_data.csv",
        ("caseid", "subjectid", "anestart"),
        [(1, 5, 0), (7, 8, 0)],
    )
    write_csv(
        tmp_path / "lab_data.csv",
        header,
        [(1, d * 86400, "hb", 10) for d in (-90, -60, -45, -15, 75)]
        + [(7, d * 86400, "hb", 11) for d in (-2, -1, 0, 14)],
    )
    mapping = tmp_path / "lab_parameters.csv"
    cols = ("Parameter", "Category", "Description", "Unit")
    write_csv(mapping, cols, [("hb", "CBC", "Hemoglobin", "g/dL")])
    (tmp_path / "clinical_parameters.csv").write_text("invented dictionary")
    (tmp_path / "LICENSE.txt").write_text("invented release")
    hashes = {
        name: sha256((tmp_path / name).read_bytes()).hexdigest()
        for name in vitaldb.FILE_HASHES
    }
    monkeypatch.setattr(vitaldb, "FILE_HASHES", hashes)
    score = vitaldb.run(tmp_path)
    assert score["cases"] == score["subjects"] == 2
    assert score["primary"]["eligible"] == score["primary"]["paired"] == 1
    assert score["secondary_all_origins"]["eligible"] == 2
    assert score["administrative_groups"]["unobservable"]["paired"] == 0
    write_csv(mapping, cols, [("hb", "CBC", "Hemoglobin", "g/L")])
    hashes[mapping.name] = sha256(mapping.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="mapping"):
        vitaldb.run(tmp_path)
