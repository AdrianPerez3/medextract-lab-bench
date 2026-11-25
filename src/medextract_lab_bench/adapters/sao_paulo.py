"""Replay the frozen 90-day Hb comparison on FAPESP Data Sharing/BR item 98."""

import csv
import hashlib
import io
import re
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from statistics import median
from zipfile import ZipFile

from ..hemoglobin import MODEL_SHA256, summarize
from .lippe import select_target

ZIP_SHA = "6fe4a880a55daf90f27e688ed21cd7ab4480c3901d5b4f52fa3a3750ae5c9d29"
FIT_SHA = "7dfc1270ecf6165910e3fe6189c17a869ee1d5cc49d1baf119dab4ea138671ac"
DECIMAL = re.compile(r"[+]?(?:[0-9]+(?:[.,][0-9]+)?|[.,][0-9]+)")
EXAMS = "EINSTEIN_Exames_2.csv"
ROSTER = "EINSTEIN_Pacientes_2.csv"


@contextmanager
def _csv_rows(archive, member):
    with (
        archive.open(member) as raw,
        io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as inp,
    ):
        yield csv.DictReader(inp, delimiter="|", strict=True)


@lru_cache(maxsize=4096)
def _day_number(raw):
    if not re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", raw):
        raise ValueError("Unrecognized collection date format")
    d, m, y = map(int, raw.split("/"))
    return date(y, m, d).toordinal()


def _assemble(people, exams):
    """Keep one origin per rostered person, ordered by source key for the bootstrap."""
    roster = set()
    for row in people:
        key = row["ID_PACIENTE"]
        if not key.strip() or key in roster:
            raise ValueError("Blank or duplicate patient key in roster")
        roster.add(key)
    draws = defaultdict(lambda: defaultdict(list))
    counter = Counter()
    origins = Counter()
    start, end = None, None
    for row in exams:
        counter["all_exam_rows"] += 1
        day = _day_number(row["DT_COLETA"])
        start = day if start is None else min(start, day)
        end = day if end is None else max(end, day)
        if (row["DE_EXAME"], row["DE_ANALITO"]) != (
            "Hemograma com Plaquetas",
            "Hemoglobina",
        ):
            continue
        counter["matched_before_unit"] += 1
        if row["CD_UNIDADE"] != "g/dL":
            counter["excluded_unit"] += 1
            continue
        key = row["ID_PACIENTE"]
        if key not in roster:
            counter["excluded_unlinked"] += 1
            continue
        result = row["DE_RESULTADO"].strip()
        if DECIMAL.fullmatch(result) is None:
            counter["excluded_value"] += 1
            continue
        draws[key][day].append(Decimal(result.replace(",", ".")))
        counter["retained_rows"] += 1
        origins[row["DE_ORIGEM"]] += 1
    if start is None:
        raise ValueError("Empty exam file")
    histories = []
    group_counts = {
        g: {"eligible": 0, "paired": 0} for g in ("complete", "partial", "unobservable")
    }
    for key in sorted(draws):
        daily_draws = draws[key]
        counter["person_days"] += len(daily_draws)
        counter["days_with_multiple_rows"] += sum(
            len(v) > 1 for v in daily_draws.values()
        )
        if len(daily_draws) < 4:
            continue
        daily = [(d, float(median(daily_draws[d]))) for d in sorted(daily_draws)]
        days, values = zip(*daily[:4])
        remaining = end - days[-1]
        if remaining >= 104:
            group = "complete"
        elif remaining >= 76:
            group = "partial"
        else:
            group = "unobservable"
        target = select_target(daily[4:], days[-1])
        group_counts[group]["eligible"] += 1
        group_counts[group]["paired"] += int(target is not None)
        histories.append((group, (days, values, None if target is None else target[1])))
    paired = sum(v["paired"] for v in group_counts.values())
    return {
        "source": "Hospital Israelita Albert Einstein, FAPESP COVID-19 Data Sharing/BR, item 98",
        "collection_dates": [date.fromordinal(d).isoformat() for d in (start, end)],
        "roster": len(roster),
        "with_one_or_more_days": len(draws),
        "eligible": len(histories),
        "paired": paired,
        "without_target": len(histories) - paired,
        "window_groups": group_counts,
        "row_flow": dict(sorted(counter.items())),
        "source_settings_retained_rows": dict(sorted(origins.items())),
    }, histories


def run(path: Path):
    """Validate and stream the original ZIP. The returned scorecard contains aggregates."""
    with Path(path).open("rb") as src:
        if hashlib.file_digest(src, "sha256").hexdigest() != ZIP_SHA:
            raise ValueError(
                "São Paulo archive SHA-256 mismatch; use the original EINSTEINAgosto.zip from FAPESP item 98"
            )
        if MODEL_SHA256 != FIT_SHA:
            raise ValueError("Unexpected frozen hemoglobin model version")
        src.seek(0)
        with (
            ZipFile(src) as archive,
            _csv_rows(archive, ROSTER) as people,
            _csv_rows(archive, EXAMS) as exams,
        ):
            counts, histories = _assemble(people, exams)
    return {
        "qualification": {"archive_sha256": ZIP_SHA, **counts},
        "model_sha256": MODEL_SHA256,
        "all": summarize(row for _, row in histories),
        "complete": summarize(row for group, row in histories if group == "complete"),
    }
