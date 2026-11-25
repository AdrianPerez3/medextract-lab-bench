import csv
import gzip

import pytest

from medextract_lab_bench.qualification import qualify_demo


def inputs(tmp_path, label="Hemoglobin"):
    dictionary = tmp_path / "items.csv.gz"
    with gzip.open(dictionary, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["itemid", "label", "fluid", "category"])
        w.writerow([51222, label, "Blood", "Hematology"])
    events = tmp_path / "events.csv.gz"
    with gzip.open(events, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "subject_id",
                "itemid",
                "charttime",
                "storetime",
                "value",
                "valuenum",
                "valueuom",
            ]
        )
        for day, stored, unit, raw, number in [
            ("01-01", "01-01", "g/dL", "12", "12"),
            ("01-02", "01-02", "g/L", "120", "120"),
            ("01-03", "01-04", "g/dL", "12", "12"),
            ("01-04", "01-04", "g/dL", "12", "12"),
            ("01-05", "01-05", "g/dL", "12", "12"),
            ("01-05", "01-05", "g/dL", "12", "12"),
            ("04-05", "04-06", "g/dL", "11", "11"),
            ("02-01", "02-01", "g/dL", "<4", "4"),
        ]:
            w.writerow(
                [
                    "invented",
                    51222,
                    "2021-" + day + " 09:00:00",
                    "2021-" + stored + " 12:00:00",
                    raw,
                    number,
                    unit,
                ]
            )
    return events, dictionary


def test_qualification_groups_distinct_days_and_preserves_delay(tmp_path):
    report = qualify_demo(*inputs(tmp_path))
    assert report["with_four_eligible_days"] == report["with_target_in_window"] == 1
    assert report["available_after_draw_day"] == 2
    assert report["excluded"]["non_exact_result"] == 1
    assert "invented" not in str(report)
    assert "mae" not in str(report).lower()


def test_changed_dictionary_refused_before_reading_cohort(tmp_path):
    events, dictionary = inputs(tmp_path, label="Calculated hemoglobin")
    events.unlink()
    with pytest.raises(ValueError, match="dictionary"):
        qualify_demo(events, dictionary)
