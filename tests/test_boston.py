"""Boston validation uses invented release-shaped events and local scratch only."""

import csv
from datetime import datetime, timedelta
import gzip

import pytest

from medextract_lab_bench.adapters import boston
from medextract_lab_bench.lock import digest

duckdb = pytest.importorskip("duckdb")
START = datetime(2000, 1, 1, 12)
COLUMNS = "labevent_id subject_id specimen_id itemid charttime storetime value valuenum valueuom".split()


def write_gzip(path, columns, rows):
    with gzip.open(path, "wt", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows(rows)


def lab(
    day,
    value="12",
    *,
    person=1,
    specimen=None,
    item="51222",
    unit="g/dL",
    hours=1,
    numeric=None,
):
    taken = START + timedelta(days=day)
    return [
        person,
        specimen if specimen is not None else day + 10,
        item,
        taken,
        taken + timedelta(hours=hours),
        value,
        value if numeric is None else numeric,
        unit,
    ]


def write_events(path, rows):
    write_gzip(path, COLUMNS, [[n, *row] for n, row in enumerate(rows, 1)])


def histories(tmp_path, rows, excluded=()):
    path = tmp_path / "events.csv.gz"
    write_events(path, rows)
    with duckdb.connect() as db:
        boston._prepare(db, path, excluded)
        return boston._counts(db), list(boston._score_rows(db))


def test_history_as_of_the_fourth_day_and_daily_specimen_reduction(tmp_path):
    rows = [
        lab(0, "10"),
        lab(1),
        lab(2),
        lab(3, "100", unit="g/L"),
        lab(0, "16", specimen=500, hours=48),  # available by the fourth day
        lab(0, "16", specimen=500, hours=48),  # identical specimen repeated
        lab(0, "90", specimen=501, hours=100),  # entered after the index
        lab(3, "18", specimen=502, hours=11),  # known before midnight
        lab(3, "40", specimen=503, hours=12),  # exactly midnight after the fourth day
        lab(91, "15", hours=72),
        lab(95, "17"),
    ]  # earlier wins the equal-distance tie
    counts, subjects = histories(tmp_path, rows)
    assert (counts["eligible"], counts["with_target"]) == (1, 1)
    days, vals, target = subjects[0]
    assert [d - days[0] for d in days] == [0, 1, 2, 3]
    assert vals == (13, 12, 12, 14) and target == 15


def test_late_qualifier_wrong_assay_and_units_never_create_a_history(tmp_path):
    events = [lab(d) for d in (0, 1, 2)]
    events += [
        lab(3, hours=24),
        lab(4, hours=-1),
        lab(5, "<12", numeric="12"),
        lab(6, "12", numeric="14"),
        lab(7, "nan"),
        lab(8, item="50811"),
        lab(9, unit="mg/dL"),
    ]
    counts, rows = histories(tmp_path, events)
    assert not rows and counts["eligible"] == 0
    _, rows = histories(tmp_path, events + [lab(10)])
    assert rows[0][1] == (12, 12, 12, 12)
    assert rows[0][2] is None


def test_exact_value_boundaries_missing_future_and_contact(tmp_path):
    rows = []
    for p, offset in enumerate((75, 76, 104, 105), 1):
        rows.extend(lab(d, person=p) for d in (0, 1, 2, 3))
        rows.append(lab(3 + offset, "0" if offset == 76 else "12", person=p))
    counts, subjects = histories(tmp_path, rows, excluded={"4"})
    assert (
        counts["eligible"] == 3
        and counts["with_target"] == 2
        and counts["without_target"] == 1
    )
    assert counts["later_lab_contact_through_104"] == 1
    assert [s[2] for s in subjects] == [None, 0, 12]


def test_markers_must_be_on_same_origin_specimen_and_available(tmp_path):
    rows = [lab(d) for d in (0, 1, 2, 3)] + [
        lab(3, "90", item="51250", unit="fL"),
        lab(3, "13", item="51277", unit="%", specimen=100),
        lab(3, "45", item="52172", unit="fL"),
        lab(3, "13", item="51277", unit="%", hours=12),
    ]
    counts, _ = histories(tmp_path, rows)
    assert (
        counts["index_mcv_linked"] == 1
        and counts["index_rdw_cv_linked"] == counts["index_both_linked"] == 0
    )
    counts, _ = histories(tmp_path, rows + [lab(3, "13", item="51277", unit="%")])
    assert counts["index_both_linked"] == 1


def test_full_entry_points_check_digest_overlap_and_counts_before_scores(
    tmp_path, monkeypatch
):
    hosp = tmp_path / "hosp"
    hosp.mkdir()
    events, dictionary, patients = (
        tmp_path / n for n in (boston.EVENTS, boston.DICTIONARY, boston.PATIENTS)
    )
    columns = ["subject_id", "gender", "anchor_age", "anchor_year", "anchor_year_group"]
    excluded = [[n, "F", 40, 2150, "2008 - 2010"] for n in range(1, 101)]
    demo = tmp_path / "fixture.csv.gz"
    write_gzip(demo, columns, excluded)
    monkeypatch.setitem(boston.DEMO, boston.PATIENTS, digest(demo))
    write_gzip(patients, columns, [*excluded, [101, "M", 51, 2160, "2011 - 2013"]])
    write_gzip(
        dictionary,
        ["itemid", "label", "category", "fluid"],
        [(k, v, "Hematology", "Blood") for k, v in boston.ITEMS.items()],
    )
    write_events(
        events,
        [
            lab(d, person=p, value="12" if d <= 3 else "13")
            for p in (1, 101)
            for d in (0, 1, 2, 3, 93)
        ],
    )
    manifest = tmp_path / "official-manifest"

    def sign_fixture():
        manifest.write_text(
            "".join(f"{digest(tmp_path / k)}  {k}\n" for k in boston.DEMO)
        )

    sign_fixture()
    # Matching local hashes alone must never label this 3-row dictionary v3.1.
    with pytest.raises(ValueError, match="v3.1 row count"):
        boston.run(tmp_path, manifest, demo, tmp_path, counts_only=False)
    monkeypatch.setattr(
        boston,
        "FULL_ROWS",
        {boston.DICTIONARY: 3, boston.PATIENTS: 101, boston.EVENTS: 10},
    )
    counts = boston.run(tmp_path, manifest, demo, tmp_path, counts_only=True)
    scores = boston.run(tmp_path, manifest, demo, tmp_path, counts_only=False)
    assert "scorecard" not in counts and counts["counts"] == scores["counts"]
    assert (
        scores["excluded_demo_people"] == 100
        and scores["scorecard"]["eligible"] == scores["scorecard"]["paired"] == 1
    )
    assert scores["scorecard"]["methods"]["last"]["metrics"]["mae"] == 1
    assert scores["scorecard"]["model_minus"]["last"]["ci95"] is None
    assert not list(tmp_path.glob("lab-bench-boston-*"))
    with monkeypatch.context() as patch:
        patch.setitem(boston.FULL_ROWS, boston.EVENTS, 9)
        with pytest.raises(ValueError, match="labevents.*v3.1 row count"):
            boston.run(tmp_path, manifest, demo, tmp_path, counts_only=True)
    with monkeypatch.context() as patch:
        patch.setitem(boston.FULL_ROWS, boston.PATIENTS, 100)
        with pytest.raises(ValueError, match="patients.*v3.1 row count"):
            boston.run(tmp_path, manifest, demo, tmp_path, counts_only=False)
    write_gzip(
        patients,
        columns,
        [
            *excluded[:99],
            [101, "M", 51, 2160, "2011 - 2013"],
            [102, "M", 50, 2161, "2011 - 2013"],
        ],
    )
    with pytest.raises(ValueError, match="checksum mismatch"):
        boston.run(tmp_path, manifest, demo, tmp_path, counts_only=True)
    sign_fixture()
    with pytest.raises(ValueError, match="100 demo"):
        boston.run(tmp_path, manifest, demo, tmp_path, counts_only=True)
    manifest.write_text("".join(f"{v}  {k}\n" for k, v in boston.DEMO.items()))
    with pytest.raises(ValueError, match="original full"):
        boston.run(tmp_path, manifest, demo, tmp_path, counts_only=False)


def test_synthetic_does_not_imply_boston_performance():
    report = boston.synthetic()
    assert report["site"] == "invented example"
    assert (report["counts"]["eligible"], report["counts"]["with_target"]) == (3, 2)
    assert report["scorecard"]["methods"]["last"]["metrics"]["mae"] == 1.5


def test_bad_schema_failure_keeps_event_contents_private(tmp_path):
    events = tmp_path / "malformed.csv.gz"
    write_gzip(events, ["invented_private_field"], [["private-example-canary"]])
    with pytest.raises(ValueError, match="Boston table processing failed") as exc:
        boston._evaluate(events, tmp_path, set(), "invented", counts_only=True)
    assert "private-example-canary" not in str(exc.value)
    assert "invented_private_field" not in str(exc.value)
    assert not list(tmp_path.glob("lab-bench-boston-*"))


def test_csv_late_quoted_multiline_comment_passes_both_scans(tmp_path):
    events = tmp_path / "events.csv.gz"
    with gzip.open(events, "wt", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow([*COLUMNS, "comments"])
        filler = lab(0, person=2, item="50811")
        # Past the default dialect-detection sample, exercise quotes in an unused field.
        for i in range(22000):
            writer.writerow([i + 1, *filler, "plain"])
        for i, day in enumerate((0, 1, 2, 3, 93)):
            writer.writerow([22001 + i, *lab(day), 'quoted, "word"\nnew line'])
    score = boston._evaluate(events, tmp_path, set(), "invented", counts_only=False)
    assert score["counts"]["eligible"] == score["scorecard"]["paired"] == 1
    assert score["scorecard"]["methods"]["last"]["metrics"]["mae"] == 0
