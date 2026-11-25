"""Replay the 90-day hemoglobin forecast on the VitalDB 1.0.0 surgical cohort.

Hyung-Chul Lee and Chul-Woo Jung, VitalDB v1.0.0, PhysioNet, 2022,
https://doi.org/10.13026/czw8-9p62 (CC BY 4.0, https://creativecommons.org/licenses/by/4.0/).
The derived scorecard summarizes daily medians and paired forecast errors.
"""

import csv
import math
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from statistics import median

from ..hemoglobin import MODEL_SHA256, summarize
from ..lock import digest
from .lippe import select_target


# PhysioNet VitalDB v1.0.0, https://physionet.org/files/vitaldb/1.0.0/SHA256SUMS.txt
FILE_HASHES = {
    "clinical_data.csv": "7d6edb471e5eee3fde75e417084240c97bdbf6eff41cbd61e5dace44f1585ecf",
    "lab_data.csv": "c6e84fb397afe8182a7e6cc3aac6b34502d6ac0fadf1abf0ef643cc8bd50ea8b",
    "clinical_parameters.csv": "3fd13678a26629df5f1352c2a3fa2951e731d3760b8fa47c06c9ac3fbcef2e35",
    "lab_parameters.csv": "0d937b50a19aa9c5105d8f477d4e5b3f3ef2f767f04badc76f2c3ecaa88056a6",
    "LICENSE.txt": "9a78e7f22742dde9f66ae235ec793ba2212019dc4d0ced75c4da09ced0b35fb2",
}
FIRST_BIN = -90
LAST_BIN = 89
SECONDS_PER_DAY = 86_400


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _roster(rows):
    """Choose the lowest random case number before inspecting lab availability."""
    seen = set()
    selected = {}
    for row in rows:
        case, person = int(row["caseid"]), int(row["subjectid"])
        if case in seen:
            raise ValueError("Duplicate case in clinical roster")
        seen.add(case)
        if person not in selected or case < selected[person][0]:
            selected[person] = case, _finite(row["anestart"])
    return selected, len(seen)


def _daily_draws(rows, selected):
    anesthesia = {case: start for case, start in selected.values() if start is not None}
    by_case = defaultdict(lambda: defaultdict(list))
    counts = {"selected_hb_rows": 0, "unusable_time_or_value": 0, "outside_extract": 0}
    for row in rows:
        if row["name"] != "hb":
            continue
        case = int(row["caseid"])
        if case not in anesthesia:
            continue
        counts["selected_hb_rows"] += 1
        when, hb = _finite(row["dt"]), _finite(row["result"])
        if when is None or hb is None or hb < 0:
            counts["unusable_time_or_value"] += 1
            continue
        day = math.floor((when - anesthesia[case]) / SECONDS_PER_DAY)
        if FIRST_BIN <= day <= LAST_BIN:
            by_case[case][day].append(Decimal(row["result"]))
        else:
            counts["outside_extract"] += 1
    return by_case, counts


def _histories(selected, by_case):
    """Yield one four-draw history per person in the frozen resampling order."""
    for person in sorted(selected):
        case, _ = selected[person]
        bins = by_case.get(case, {})
        if len(bins) < 4:
            continue
        daily = [(day, float(median(bins[day]))) for day in sorted(bins)]
        days, values = zip(*daily[:4])
        anchor = days[-1]
        if anchor + 104 <= LAST_BIN:
            group = "complete"
        elif anchor + 76 <= LAST_BIN:
            group = "partial"
        else:
            group = "unobservable"
        target = select_target(daily[4:], anchor)
        yield group, (days, values, None if target is None else target[1])


def run(folder: Path):
    """Return aggregate metrics for the hash-pinned source and frozen German fit."""
    folder = Path(folder)
    for name, expected in FILE_HASHES.items():
        if digest(folder / name) != expected:
            raise ValueError(f"VitalDB 1.0.0 SHA-256 mismatch for {name}")
    with (folder / "lab_parameters.csv").open(encoding="utf-8-sig", newline="") as f:
        mapping = [
            (r["Category"], r["Description"], r["Unit"])
            for r in csv.DictReader(f)
            if r["Parameter"] == "hb"
        ]
        if mapping != [("CBC", "Hemoglobin", "g/dL")]:
            raise ValueError("Unexpected VitalDB hemoglobin mapping")
    with (folder / "clinical_data.csv").open(encoding="utf-8-sig", newline="") as f:
        selected, cases = _roster(csv.DictReader(f))
    with (folder / "lab_data.csv").open(encoding="utf-8-sig", newline="") as f:
        by_case, counts = _daily_draws(csv.DictReader(f), selected)
    rows = list(_histories(selected, by_case))
    groups = {
        group: summarize(history for label, history in rows if label == group)
        for group in ("complete", "partial", "unobservable")
    }
    return {
        "source": "Seoul National University Hospital, VitalDB 1.0.0",
        "source_sha256": dict(FILE_HASHES),
        "fit_sha256": MODEL_SHA256,
        "unit": "g/dL",
        "cases": cases,
        "subjects": len(selected),
        "selected_without_anestart": sum(
            start is None for _, start in selected.values()
        ),
        "hb_row_flow": counts,
        "primary": groups["complete"],
        "administrative_groups": groups,
        "secondary_all_origins": summarize(history for _, history in rows),
    }
