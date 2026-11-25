"""Portable JSON receipts and comparison with the original aggregate results."""

import json
import math
import os
import tempfile
from importlib.resources import files
from pathlib import Path

GOLDENS = {
    "bariatric_hba1c_9m_12m_v0": "bariatric-results.json",
    "lippe_hemoglobin_day90_v0": "cbc-results.json",
}


def reject_constant(text):
    raise ValueError(f"invalid JSON number {text}")


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=unique_keys,
    )


def write_once(path: Path, payload):
    """Atomically publish a complete receipt, also when two processes race."""
    path = Path(path)
    data = json.dumps(payload, allow_nan=False, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=".lab-bench-", delete=False, encoding="utf-8"
    ) as temp:
        staged = Path(temp.name)
        try:
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
            os.link(staged, path)
        finally:
            staged.unlink(missing_ok=True)


def compare_golden(expected, actual, location="$", tolerance=1e-9):
    """Check every field, count, bootstrap endpoint and method against its recipe."""
    mismatch = ValueError(f"historical receipt differs at {location}")
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or expected.keys() != actual.keys():
            raise mismatch
        for key, value in expected.items():
            eps = 1e-8 if "bootstrap" in key or key.endswith("_ci") else tolerance
            compare_golden(value, actual[key], f"{location}.{key}", eps)
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise mismatch
        for i, (left, right) in enumerate(zip(expected, actual)):
            compare_golden(left, right, f"{location}[{i}]", tolerance)
    elif isinstance(expected, float):
        if type(actual) not in (int, float) or not math.isclose(
            expected, actual, rel_tol=0, abs_tol=tolerance
        ):
            raise mismatch
    elif type(expected) is not type(actual) or expected != actual:
        raise mismatch


def verify_replay(task, actual):
    golden = files("medextract_lab_bench").joinpath("protocols", GOLDENS[task])
    compare_golden(read_json(golden), actual)
    return {"task": task, "verification": "matched_complete_historical_receipt"}
