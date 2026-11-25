"""Offline MIMIC-IV hospital hemoglobin qualification and frozen-model evaluation."""

import csv
from datetime import datetime, timedelta
import gzip
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from ..lock import digest

DEMO = {
    "hosp/d_labitems.csv.gz": "72ea5f020469fd24543bd98ab3bd0a4f645a5a4b7802d9d52af20d736f9d76db",
    "hosp/labevents.csv.gz": "d51a4cf1ea63245abe7d544b0d50a928e9ee2ec5eb9a5ce85500aed0bd6c6dfc",
    "hosp/patients.csv.gz": "59dcc6cd8450ddf03ad32cc7d76e41caa4c9633541715987f590c74835cf6fae",
}
ITEMS = {"51222": "Hemoglobin", "51250": "MCV", "51277": "RDW"}
EVENTS = "hosp/labevents.csv.gz"
DICTIONARY = "hosp/d_labitems.csv.gz"
PATIENTS = "hosp/patients.csv.gz"
FULL_RELEASE = "MIMIC-IV v3.1"
FULL_ROWS = {DICTIONARY: 1650, PATIENTS: 364627, EVENTS: 158374764}


def _check_row_count(name, count):
    if count != FULL_ROWS[name]:
        raise ValueError(f"{name} differs from the published MIMIC-IV v3.1 row count")


def _full_hashes(manifest):
    hashes = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        fields = line.split(maxsplit=1)
        if len(fields) != 2:
            raise ValueError("Malformed source manifest")
        key, name = fields
        name = name.removeprefix("*").removeprefix("./")
        if name in DEMO:
            if name in hashes or not re.fullmatch("[0-9a-f]{64}", key):
                raise ValueError("Malformed or duplicate source checksum")
            hashes[name] = key
    if hashes.keys() != DEMO.keys() or hashes[EVENTS] == DEMO[EVENTS]:
        raise ValueError("Use the original full MIMIC-IV v3.1 release manifest")
    return hashes


def _verify_files(root, expected):
    for name, checksum in expected.items():
        if digest(root / name) != checksum:
            raise ValueError(f"Source checksum mismatch for {name}")


def _verify_dictionary(path, *, full_release=False):
    selected = []
    count = 0
    with gzip.open(path, "rt", newline="", encoding="utf-8") as stream:
        for count, row in enumerate(csv.DictReader(stream), 1):
            if row["itemid"] in ITEMS:
                selected.append(
                    tuple(row[k] for k in ("itemid", "label", "fluid", "category"))
                )
    if full_release:
        _check_row_count(DICTIONARY, count)
    expected = [(i, label, "Blood", "Hematology") for i, label in ITEMS.items()]
    if sorted(selected) != sorted(expected):
        raise ValueError(
            "Unexpected routine blood Hb, MCV or RDW-CV dictionary mapping"
        )


def _roster(path, keep=None):
    anchor_fields = ("gender", "anchor_age", "anchor_year", "anchor_year_group")
    roster = {}
    count = 0
    with gzip.open(path, "rt", newline="", encoding="utf-8") as stream:
        for count, row in enumerate(csv.DictReader(stream), 1):
            key = row["subject_id"]
            if keep is not None and key not in keep:
                continue
            values = tuple(row[k] for k in anchor_fields)
            if not re.fullmatch("[1-9][0-9]*", key) or key in roster or not all(values):
                raise ValueError("Incomplete or ambiguous cross-release patient roster")
            roster[key] = values
    if keep is not None:
        _check_row_count(PATIENTS, count)
    return roster


def _exclusions(public_path, full_path):
    if digest(public_path) != DEMO[PATIENTS]:
        raise ValueError("Use the original public v2.2 demo patients file")
    demo = _roster(public_path)
    if len(demo) != 100 or _roster(full_path, keep=demo) != demo:
        raise ValueError("Could not verify the 100 demo patients in the full release")
    return set(demo)


# Only the three assays enter the working tables. A second scan gives last
# contact from any assay, including labs outside hospital admissions.
def _prepare(db, events, excluded, *, full_release=False):
    db.execute("CREATE TEMP TABLE omitted (p BIGINT PRIMARY KEY)")
    if excluded:
        db.executemany("INSERT INTO omitted VALUES (?)", [(int(p),) for p in excluded])
    # The release uses RFC-style doubled quotes, including in multiline comments.
    reader = """read_csv(?, header=true, all_varchar=true, strict_mode=true,
                         delim=',', quote='"', escape='"')"""
    db.execute(
        f"""
        CREATE TEMP TABLE contact AS
        SELECT try_cast(subject_id AS BIGINT) AS p, max(try_cast(charttime AS DATE)) AS last_lab, count(*) AS n
        FROM {reader} GROUP BY p
    """,
        [str(events)],
    )
    if full_release:
        _check_row_count(
            EVENTS, db.execute("SELECT coalesce(sum(n),0) FROM contact").fetchone()[0]
        )
    db.execute(
        f"""
        CREATE TEMP TABLE assays AS
        WITH typed AS (
            SELECT try_cast(subject_id AS BIGINT) AS p, specimen_id, labevent_id, itemid,
                try_strptime(charttime, ['%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M:%S.%f']) AS taken,
                try_strptime(storetime, ['%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M:%S.%f']) AS ready,
                try_cast(value AS DOUBLE) AS raw, try_cast(valuenum AS DOUBLE) AS number,
                try_cast(value AS DECIMAL(28,12)) AS measured, valueuom
            FROM {reader}
            WHERE itemid IN ('51222','51250','51277') AND regexp_full_match(subject_id,'[1-9][0-9]*')
        )
        SELECT p, itemid, specimen_id, labevent_id, taken, ready, taken::DATE AS day,
          CASE WHEN valueuom='g/L' THEN measured * 0.1 ELSE measured END AS result
        FROM typed ANTI JOIN omitted USING(p)
        WHERE p IS NOT NULL AND ready>=taken AND isfinite(raw) AND raw=number AND raw>=0
          AND measured::DOUBLE=raw
          AND ((itemid='51222' AND valueuom IN ('g/dL','g/L'))
            OR (itemid='51250' AND lower(valueuom)='fl')
            OR (itemid='51277' AND valueuom='%'))
    """,
        [str(events)],
    )
    db.execute("""
        CREATE TEMP TABLE days AS
        SELECT p, day, bool_or(ready::DATE=day) AS visible
        FROM assays WHERE itemid='51222' GROUP BY p,day
    """)
    db.execute("""
        CREATE TEMP TABLE history AS
        SELECT p, day, row_number() OVER (PARTITION BY p ORDER BY day) AS seq
        FROM days WHERE visible QUALIFY seq<=4
    """)
    db.execute("""
        CREATE TEMP TABLE anchors AS
        SELECT p,day AS anchor FROM history WHERE seq=4
    """)
    db.execute("""
        CREATE TEMP TABLE targets AS
        SELECT a.p,d.day FROM anchors a JOIN days d ON a.p=d.p
        WHERE d.day BETWEEN a.anchor+76 AND a.anchor+104
        QUALIFY row_number() OVER (PARTITION BY a.p ORDER BY abs(d.day-a.anchor-90),d.day)=1
    """)


def _counts(db):
    cursor = db.execute("""
        WITH specimens AS (
            SELECT a.p,u.specimen_id,bool_or(itemid='51222') AS hb,
                bool_or(itemid='51250') AS mcv,bool_or(itemid='51277') AS rdw
            FROM anchors a JOIN assays u ON a.p=u.p AND a.anchor=u.day
            WHERE u.ready::DATE=u.day AND regexp_full_match(u.specimen_id,'[1-9][0-9]*')
            GROUP BY a.p,u.specimen_id
        ), markers AS (
            SELECT p,bool_or(mcv) AS mcv,bool_or(rdw) AS rdw,bool_or(mcv AND rdw) AS joint
            FROM specimens WHERE hb GROUP BY p
        )
        SELECT count(*) AS eligible, count(t.p) AS with_target,
          count_if(c.last_lab>=a.anchor+104) AS later_lab_contact_through_104,
          count_if(t.p IS NOT NULL AND c.last_lab>=a.anchor+104) AS with_target_and_later_lab_contact,
          count_if(mcv) AS index_mcv_linked,count_if(rdw) AS index_rdw_cv_linked,
          count_if(joint) AS index_both_linked,
          count_if(joint AND t.p IS NOT NULL) AS index_both_linked_and_target
        FROM anchors a JOIN contact c USING(p)
        LEFT JOIN targets t USING(p) LEFT JOIN markers m USING(p)
    """)
    result = dict(
        zip([d[0] for d in cursor.description], [v or 0 for v in cursor.fetchone()])
    )
    return {**result, "without_target": result["eligible"] - result["with_target"]}


def _score_rows(db):
    # An earlier qualifying draw date may acquire another result by the fourth
    # day. Preserve that information cutoff even when reducing duplicate rows.
    sql = """
        WITH needed AS (
            SELECT h.p,h.day,h.seq,a.anchor FROM history h JOIN anchors a USING(p)
            UNION ALL SELECT t.p,t.day,5 AS seq,NULL AS anchor FROM targets t
        ), distinct_results AS (
            SELECT DISTINCT n.p,n.day,n.seq,u.taken,
              CASE WHEN regexp_full_match(u.specimen_id,'[1-9][0-9]*')
                   THEN 's'||u.specimen_id ELSE 'e'||u.labevent_id END AS draw,
              u.result
            FROM needed n JOIN assays u ON n.p=u.p AND n.day=u.day
            WHERE u.itemid='51222' AND (n.seq=5 OR u.ready::DATE<=n.anchor)
        ), draws AS (
            SELECT p,day,seq,taken,draw,median(result) AS hb
            FROM distinct_results GROUP BY p,day,seq,taken,draw
        ), daily AS (
            SELECT p,day,seq,median(hb) AS value FROM draws GROUP BY p,day,seq
        )
        SELECT list(day ORDER BY seq) FILTER(WHERE seq<5),
               list(value ORDER BY seq) FILTER(WHERE seq<5),max(value) FILTER(WHERE seq=5)
        FROM daily GROUP BY p ORDER BY p
    """
    cursor = db.execute(sql)
    while batch := cursor.fetchmany(1024):
        for days, values, target in batch:
            yield (
                tuple(d.toordinal() for d in days),
                tuple(float(v) for v in values),
                (None if target is None else float(target)),
            )


def _evaluate(events, scratch, excluded, release, *, counts_only):
    try:
        import duckdb
    except ImportError as err:
        raise ValueError(
            "From the source checkout run python -m pip install '.[boston]'"
        ) from err

    with TemporaryDirectory(dir=scratch, prefix="lab-bench-boston-") as work:
        try:
            with duckdb.connect(
                str(Path(work) / "work.db"),
                config={
                    "memory_limit": "1GB",
                    "threads": 2,
                    "preserve_insertion_order": False,
                    "temp_directory": str(Path(work) / "spill"),
                },
            ) as db:
                _prepare(db, events, excluded, full_release=release == FULL_RELEASE)
                counts = _counts(db)
                report = {
                    "site": "invented example" if release == "invented" else "BIDMC",
                    "release": release,
                    "analysis": "counts" if counts_only else "scoring",
                    "protocol": "boston-hb-90d-v1",
                    "excluded_demo_people": len(excluded),
                    "counts": counts,
                }
                if not counts_only:
                    from ..hemoglobin import MODEL_SHA256, summarize

                    scores = summarize(_score_rows(db))
                    if (scores["eligible"], scores["paired"]) != (
                        counts["eligible"],
                        counts["with_target"],
                    ):
                        raise ValueError("Scoring flow differs from qualification")
                    report.update(model_sha256=MODEL_SHA256, scorecard=scores)
                return report
        except duckdb.Error:
            # CSV parser errors can include clinical row text. Keep errors aggregate-only.
            raise ValueError(
                "Boston table processing failed; check input schema, disk space and scratch permissions"
            ) from None


def run(
    root: Path, manifest: Path, demo_patients: Path, scratch: Path, *, counts_only: bool
):
    """Verify a custodian-provided full release and evaluate its non-demo population."""
    hashes = _full_hashes(manifest)
    _verify_files(root, hashes)
    _verify_dictionary(root / DICTIONARY, full_release=True)
    omitted = _exclusions(demo_patients, root / PATIENTS)
    report = _evaluate(
        root / EVENTS, scratch, omitted, FULL_RELEASE, counts_only=counts_only
    )
    return {**report, "input_sha256": hashes, "demo_roster_sha256": DEMO[PATIENTS]}


def qualify_public_demo(root: Path, scratch: Path):
    """Rehearse cohort linkage and measurement availability on the pinned public demo."""
    hashes = {k: DEMO[k] for k in (DICTIONARY, EVENTS)}
    _verify_files(root, hashes)
    _verify_dictionary(root / DICTIONARY)
    report = _evaluate(
        root / EVENTS, scratch, set(), "MIMIC-IV demo v2.2", counts_only=True
    )
    return {**report, "input_sha256": hashes}


def synthetic():
    """Write invented events into an ephemeral directory and exercise real extraction and scoring."""
    start = datetime(2000, 1, 1, 9)
    with TemporaryDirectory(prefix="lab-bench-invented-") as tmp:
        folder = Path(tmp)
        events = folder / "events.csv.gz"
        columns = (
            "labevent_id",
            "subject_id",
            "specimen_id",
            "itemid",
            "charttime",
            "storetime",
            "value",
            "valuenum",
            "valueuom",
        )
        with gzip.open(events, "wt", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            event = 0
            for p, offsets, values in [
                (1, (0, 1, 2, 3, 93), (14, 13, 12, 11, 13)),
                (2, (0, 180, 365, 400, 482), (11, 12, 12, 13, 14)),
                (3, (0, 3, 5, 6), (12, 12, 13, 12)),
            ]:
                for day, value in zip(offsets, values):
                    event += 1
                    taken = start + timedelta(days=day)
                    unit = "g/L" if event % 2 else "g/dL"
                    result = value * (10 if unit == "g/L" else 1)
                    writer.writerow(
                        (
                            event,
                            p,
                            event,
                            51222,
                            taken,
                            taken + timedelta(hours=2),
                            result,
                            result,
                            unit,
                        )
                    )
        return _evaluate(events, folder, set(), "invented", counts_only=False)
