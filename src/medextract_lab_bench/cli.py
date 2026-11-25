"""Command line entry point for offline replay, evaluation and verification."""

import argparse
import json
import sys
from importlib.metadata import version
from pathlib import Path
from zipfile import BadZipFile

from . import __version__
from .lock import make_lock, verify_lock
from .receipts import GOLDENS, read_json, verify_replay, write_once


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
                "verify": "compare a historical report with the shipped aggregate reference",
            }[name],
        )
        choices = list(GOLDENS)
        if name == "run":
            choices += ["synthetic-demo", "synthetic-events"]
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
    if args.task.startswith("synthetic-"):
        if args.input is not None:
            raise ValueError("synthetic tasks generate their inputs; remove --input")
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
