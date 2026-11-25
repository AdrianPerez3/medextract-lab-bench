"""Fixed scheduled-visit mapping for Dataverse T9OBE3 (reported HbA1c %)."""

import csv
import math
from pathlib import Path

VISITS = {
    "PREOPHBA": 0,
    "HBA1CAT3": 90,
    "HBA1CAT6": 180,
    "HBA1CAT9": 270,
    "HBA1CAT1": 365,
    "HBA1CAT2": 730,
    "V22_A": 1095,
    "HBA1CAT4": 1460,
    "HBA1CAT5": 1825,
}
PRIMARY = "270_to_365_with_preop"


def read_visits(path: Path) -> dict[str, dict[int, float]]:
    """Preserve text subject keys. Only an empty result cell denotes a missed visit."""
    people = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream, delimiter="\t", strict=True)
        header = rows.fieldnames or []
        if len(header) != len(set(header)) or not {"NO", *VISITS} <= set(header):
            raise ValueError("Unexpected scheduled-visit header")
        for row in rows:
            if None in row or any(v is None for v in row.values()):
                raise ValueError("Malformed scheduled-visit row")
            key = row["NO"]
            if not key.strip() or key.strip() != key or key in people:
                raise ValueError(
                    "Missing, ambiguous, or duplicated scheduled-visit identifier"
                )
            visits = {}
            for field, day in VISITS.items():
                cell = row[field]
                if cell == "":
                    continue
                try:
                    value = float(cell)
                except ValueError:
                    raise ValueError("Scheduled-visit HbA1c must be numeric") from None
                if not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid measured HbA1c")
                visits[day] = value
            people[key] = visits
    # Historical seeded resampling uses the lexical ordering of the text identifiers.
    return dict(sorted(people.items()))


def comparisons():
    for index, target in ((0, 90), (90, 180), (180, 270), (270, 365)):
        for preop in (True,) if index == 0 else (True, False):
            history_days = tuple(
                day for day in (0, 90, 180, 270) if day <= index and (day or preop)
            )
            suffix = "with_preop" if preop else "postop_only"
            yield f"{index}_to_{target}_{suffix}", history_days, target
