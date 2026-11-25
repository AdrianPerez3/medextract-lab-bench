"""Command line entry point for offline replay, evaluation and verification."""

import argparse
import json
import sys
from importlib.metadata import version
from pathlib import Path
from zipfile import BadZipFile

from . import __version__
from .lock import make_lock, verify_lock
from .receipts import GOLDENS, SITE_GOLDENS, read_json, verify_replay, write_once


def _parser():
    parser = argparse.ArgumentParser(prog="lab-bench", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="show runtime versions and installed tasks")
    for name in ("replay", "run", "validate", "verify"):
        part = sub.add_parser(
            name,
            help={
                "replay": "reproduce the historical aggregate report",
                "run": "score the corrected methods or run the synthetic demo",
                "validate": "check an original development source against its release hash",
                "verify": "compare a report with its shipped aggregate reference",
            }[name],
        )
        choices = list(GOLDENS)
        if name == "verify":
            choices += list(SITE_GOLDENS)
        if name == "run":
            choices += ["synthetic-demo", "synthetic-events", "synthetic-boston"]
        part.add_argument("--task", required=True, choices=choices)
        if name == "verify":
            part.add_argument("--scorecard", required=True, type=Path)
        else:
            part.add_argument("--input", required=name != "run", type=Path)
        if name in ("run", "replay"):
            part.add_argument(
                "--out", type=Path, help="write once to a new file (default stdout)"
            )
    for name in ("lock", "verify-lock", "qualify-demo"):
        part = sub.add_parser(
            name,
            help={
                "lock": "write a content-addressed local methods lock",
                "verify-lock": "verify a local methods lock against installed code and runtime",
                "qualify-demo": "report follow-up counts on the hash-pinned MIMIC-IV public demo",
            }[name],
        )
        part.add_argument("--wheel", required=True, type=Path)
        part.add_argument("--dependencies", required=True, type=Path)
        if name != "lock":
            part.add_argument("--lock", required=True, type=Path)
        if name != "verify-lock":
            part.add_argument("--out", required=True, type=Path)
        if name == "qualify-demo":
            part.add_argument("--events", required=True, type=Path)
            part.add_argument("--dictionary", required=True, type=Path)
    for name, help_text in (
        (
            "replay-sao-paulo",
            "evaluate the frozen hemoglobin model on the original FAPESP hospital archive",
        ),
        ("replay-seoul", "evaluate the frozen hemoglobin model on VitalDB 1.0.0"),
        (
            "qualify-boston-demo",
            "count eligible patients in the public MIMIC-IV 2.2 demo",
        ),
        ("qualify-boston", "count eligible patients in full MIMIC-IV 3.1"),
        ("score-boston", "score the frozen hemoglobin model on full MIMIC-IV 3.1"),
    ):
        part = sub.add_parser(name, help=help_text, description=help_text)
        if name == "replay-sao-paulo":
            part.add_argument(
                "--archive",
                required=True,
                type=Path,
                help="original EINSTEINAgosto.zip from FAPESP item 98",
            )
        else:
            part.add_argument(
                "--root", required=True, type=Path, help="local dataset directory"
            )
        part.add_argument(
            "--out", type=Path, help="write once to a new file (default stdout)"
        )
        if name in ("qualify-boston-demo", "qualify-boston", "score-boston"):
            part.add_argument(
                "--scratch",
                required=True,
                type=Path,
                help="local directory for temporary analysis files",
            )
        if name in ("qualify-boston", "score-boston"):
            part.add_argument(
                "--manifest",
                required=True,
                type=Path,
                help="official 3.1 SHA256SUMS.txt",
            )
            part.add_argument(
                "--demo-patients",
                required=True,
                type=Path,
                help="public 2.2 demo patients.csv.gz for overlap exclusion",
            )
    return parser


def _execute(args):
    command = args.command
    if command == "doctor":
        return {
            "package": __version__,
            "python": sys.version.split()[0],
            "numpy": version("numpy"),
            "openpyxl": version("openpyxl"),
            "historical_tasks": list(GOLDENS),
            "site_commands": [
                "replay-seoul",
                "replay-sao-paulo",
                "qualify-boston-demo",
                "qualify-boston",
                "score-boston",
            ],
        }
    if command == "verify":
        return verify_replay(args.task, read_json(args.scorecard))
    if command == "lock":
        return make_lock(args.wheel, args.dependencies)
    if command in ("verify-lock", "qualify-demo"):
        match = verify_lock(read_json(args.lock), args.wheel, args.dependencies)
        if command == "verify-lock":
            return match
        from .qualification import qualify_demo

        report = qualify_demo(args.events, args.dictionary, check_release=True)
        return {**report, "methods_lock_digest": match["digest"]}
    if command == "replay-sao-paulo":
        from .adapters import sao_paulo

        return sao_paulo.run(args.archive)
    if command == "replay-seoul":
        from .adapters import vitaldb

        return vitaldb.run(args.root)
    if command in ("qualify-boston-demo", "qualify-boston", "score-boston"):
        from .adapters import boston

        if command == "qualify-boston-demo":
            return boston.qualify_public_demo(args.root, args.scratch)
        return boston.run(
            args.root,
            args.manifest,
            args.demo_patients,
            args.scratch,
            counts_only=command == "qualify-boston",
        )
    if args.task.startswith("synthetic-"):
        if args.input is not None:
            raise ValueError("synthetic tasks generate their inputs; remove --input")
        if args.task == "synthetic-boston":
            from .adapters.boston import synthetic

            return synthetic()
        if args.task == "synthetic-events":
            from .events import synthetic_checks

            return synthetic_checks()
        from .replay import demo

        return demo()
    if args.input is None:
        raise ValueError("supply the original development file with --input")
    from .replay import replay, validate_source

    if command == "validate":
        return validate_source(args.task, args.input)
    return replay(args.task, args.input, corrected=command == "run")


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        out = getattr(args, "out", None)
        if out is not None and out.exists():
            raise FileExistsError("output already exists; choose a new receipt path")
        data = _execute(args)
        if out is None:
            print(json.dumps(data, allow_nan=False, indent=2))
        else:
            write_once(out, data)
    except (ValueError, OSError, BadZipFile) as exc:
        print(f"lab-bench: {exc}", file=sys.stderr)
        return 1
    return 0


def entrypoint():
    raise SystemExit(main())
