"""Counts-only adapter for the public MIMIC-IV 2.2 demo hospital lab files."""

import csv
import gzip
import math
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime
from importlib.resources import files

from .lock import digest
from .receipts import read_json


def qualify_demo(events_path, dictionary_path, *, check_release=False):
    spec = read_json(
        files("medextract_lab_bench").joinpath("protocols", "methods-v1.json")
    )["mimic_demo"]
    if check_release:
        for path, field in ((dictionary_path, "dictionary"), (events_path, "events")):
            if digest(path) != spec["release_checksums"][field]:
                raise ValueError(
                    f"{field} differs from the public MIMIC-IV demo v2.2 release"
                )
    with gzip.open(dictionary_path, "rt", newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        rows = [
            row
            for row in reader
            if row.get("itemid") == spec["routine_hemoglobin_itemid"]
        ]
        if len(rows) != 1 or any(
            rows[0].get(k) != spec[k] for k in ("label", "category", "fluid")
        ):
            raise ValueError(
                "hemoglobin dictionary mapping differs from the locked definition"
            )
    on_time = defaultdict(set)
    all_days = defaultdict(set)
    excluded = Counter()
    late = n_item = total = 0
    with gzip.open(events_path, "rt", newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {
            "subject_id",
            "itemid",
            "charttime",
            "storetime",
            "value",
            "valuenum",
            "valueuom",
        }
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("lab events schema incomplete")
        for row in reader:
            total += 1
            if row["itemid"] != spec["routine_hemoglobin_itemid"]:
                continue
            n_item += 1
            person = row["subject_id"].strip()
            if not person or row["valueuom"] not in spec["assay_unit"]:
                excluded["missing_subject_or_unsupported_unit"] += 1
                continue
            try:
                raw = float(row["value"])
                numeric = float(row["valuenum"])
            except (ValueError, TypeError):
                excluded["non_exact_result"] += 1
                continue
            if not math.isfinite(raw) or raw != numeric or numeric < 0:
                excluded["non_exact_result"] += 1
                continue
            try:
                taken = datetime.fromisoformat(row["charttime"])
                ready = datetime.fromisoformat(row["storetime"])
            except (ValueError, TypeError):
                excluded["missing_or_invalid_measurement_clock"] += 1
                continue
            if taken.tzinfo is not None or ready.tzinfo is not None or ready < taken:
                excluded["inconsistent_measurement_clock"] += 1
                continue
            day = taken.date().toordinal()
            all_days[person].add(day)
            if ready.date() == taken.date():
                on_time[person].add(day)
            else:
                late += 1
    eligible = paired = 0
    for person, eligible_days in on_time.items():
        if len(eligible_days) < 4:
            continue
        eligible += 1
        index = sorted(eligible_days)[3]
        later = sorted(all_days[person])
        earliest = bisect_left(later, index + 76)
        paired += int(earliest < len(later) and later[earliest] <= index + 104)
    return {
        "task": "mimic-demo-hb-feasibility-v1",
        "role": spec["role"],
        "all_lab_rows_read": total,
        "matching_item_rows": n_item,
        "excluded": dict(excluded),
        "people_with_usable_hb": len(all_days),
        "available_after_draw_day": late,
        "with_four_eligible_days": eligible,
        "with_target_in_window": paired,
        "eligible_without_target": eligible - paired,
        "release_hashes_checked": check_release,
        "timing": spec["clock"],
        "eligibility": spec["eligibility"],
        "followup": spec["followup"],
        "scope": spec["encounter_scope"],
    }
