"""Stream the original Lippe workbook and sort released patient-days on disk."""

import math
import sqlite3
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from itertools import groupby
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory

from openpyxl import load_workbook

from ..records import HB, normalize_value

EPOCH = datetime(1970, 1, 1).date()
HORIZON = 90


def _records(path, counts, corrected):
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = iter(book.active.values)
        header = next(rows, ())
        if len(header) != len(set(header)) or not {
            "PatientNum",
            "Timestamp",
            "HB",
        } <= set(header):
            raise ValueError("Unexpected CBC workbook header")
        person, stamp, hb = map(header.index, ("PatientNum", "Timestamp", "HB"))
        for row in rows:
            key, when, value = row[person], row[stamp], row[hb]
            # The original release declares a numeric person ID and a local, shifted clock.
            if (
                type(key) is not int
                or not isinstance(when, datetime)
                or when.tzinfo is not None
            ):
                raise ValueError("Malformed CBC identifier or released timestamp")
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid measured CBC hemoglobin")
            if corrected:
                value, _ = normalize_value(HB, value, "g/dL")
            counts["raw_records"] += 1
            yield key, (when.date() - EPOCH).days, float(value)
    finally:
        book.close()


def _reported_median(values):
    """Average the two central reported decimal Hb values for an even draw count."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    left, right = ordered[middle - 1], ordered[middle]
    return float((Decimal(str(left)) + Decimal(str(right))) / 2)


def iter_people(path: Path, counts: dict, *, corrected: bool = False):
    """Yield each numeric-ordered person's distinct days, including incomplete histories.

    SQLite owns the disk-backed external ordering. Its temporary files are scoped
    to this invocation, including on an interrupted or failed parse.
    """
    daily_median = _reported_median if corrected else median
    counts.update(raw_records=0, persons=0, person_days=0)
    with TemporaryDirectory(prefix="lab-bench-") as temp_dir:
        database = Path(temp_dir) / "observations.sqlite"
        with closing(sqlite3.connect(database)) as db:
            db.execute("PRAGMA temp_store=FILE")
            db.execute("CREATE TABLE draws (person INTEGER, day INTEGER, value REAL)")
            with db:
                db.executemany(
                    "INSERT INTO draws VALUES (?, ?, ?)",
                    _records(path, counts, corrected),
                )
            ordered = db.execute(
                "SELECT person, day, value FROM draws ORDER BY person, day"
            )
            for person, person_rows in groupby(ordered, key=lambda r: r[0]):
                daily = []
                for day, day_rows in groupby(person_rows, key=lambda r: r[1]):
                    daily.append((day, float(daily_median(r[2] for r in day_rows))))
                counts["persons"] += 1
                counts["person_days"] += len(daily)
                yield person, daily


def select_target(future, anchor):
    """Evaluator only. Return (observed gap, value) or None."""
    candidates = (
        (day - anchor, value) for day, value in future if 76 <= day - anchor <= 104
    )
    return min(candidates, key=lambda x: (abs(x[0] - HORIZON), x[0]), default=None)
