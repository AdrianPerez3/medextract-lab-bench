"""Rebuild the aggregate 90-day fit from the fingerprinted Lippe source workbook."""

import argparse
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from medextract_lab_bench.adapters import lippe
from medextract_lab_bench.hemoglobin import MODEL_SHA256, _PARAMETERS, _features
from medextract_lab_bench.replay import validate_source


def assignment(label, person, modulus):
    key = f"lippe-hb-residual-v1|{label}|{person}".encode("ascii")
    return int.from_bytes(sha256(key).digest()[:8], "big") % modulus


def coefficients(x, change, penalty):
    center = x.mean(axis=0)
    scale = x.std(axis=0)
    constant = (x == x[0]).all(axis=0)
    center[constant] = x[0, constant]
    scale[constant] = 1.0
    z = (x - center) / scale
    intercept = change.mean()
    n = len(z)
    weights = np.linalg.solve(
        z.T @ z / n + penalty * np.eye(z.shape[1]),
        z.T @ (change - intercept) / n,
    )
    return center, scale, weights, intercept


def rebuild(path):
    receipt = validate_source("lippe_hemoglobin_day90_v0", path)
    rows, changes, folds = [], [], []
    for person, visits in lippe.iter_people(path, {}, corrected=True):
        if assignment("outer", person, 10) < 3 or len(visits) < 4:
            continue
        days, values = zip(*visits[:4])
        target = lippe.select_target(visits[4:], days[-1])
        if target is not None:
            rows.append(_features(days, values))
            changes.append(target[1] - values[-1])
            folds.append(assignment("inner", person, 5))
    if len(rows) != 3273:
        raise ValueError("Unexpected number of measured training patients")

    x, y, fold = np.asarray(rows), np.asarray(changes), np.asarray(folds)
    errors = {}
    for penalty in (0.01, 0.1, 1.0, 10.0):
        held_out = np.empty(len(y))
        for part in range(5):
            evaluate = fold == part
            center, scale, weights, intercept = coefficients(
                x[~evaluate], y[~evaluate], penalty
            )
            held_out[evaluate] = intercept + ((x[evaluate] - center) / scale) @ weights
        errors[penalty] = float(np.mean(np.abs(held_out - y)))
    chosen = min(errors, key=lambda p: (errors[p], -p))
    center, scale, weights, intercept = coefficients(x, y, chosen)
    vector = np.concatenate((center, scale, weights, [intercept])).astype("<f8")
    fingerprint = sha256(vector.tobytes()).hexdigest()
    error = float(np.max(np.abs(vector - _PARAMETERS)))
    return {
        "source_sha256": receipt["sha256"],
        "training_people": len(y),
        "selected_penalty": chosen,
        "fold_pooled_mae_by_penalty": errors,
        "parameter_sha256": fingerprint,
        "matches_frozen_bytes": fingerprint == MODEL_SHA256,
        "max_abs_parameter_difference": error,
        "matches_within_1e-12": chosen == 0.1 and error <= 1e-12,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument(
        "--require-exact",
        action="store_true",
        help="also require the reference runtime's exact IEEE 754 parameter bytes",
    )
    args = parser.parse_args()
    result = rebuild(args.workbook)
    print(json.dumps(result, indent=2, allow_nan=False))
    passed = result["matches_within_1e-12"] and (
        result["matches_frozen_bytes"] or not args.require_exact
    )
    raise SystemExit(0 if passed else 1)
