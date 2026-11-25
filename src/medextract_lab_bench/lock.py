"""Bind a method receipt to the exact installed package and its distribution."""

from datetime import datetime, timezone
from hashlib import sha256, file_digest
from importlib import metadata
from pathlib import Path
import json
import platform
import re
from zipfile import ZipFile

from . import __version__


def digest(path):
    with Path(path).open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def _source_files():
    root = Path(__file__).parent
    return {
        f"medextract_lab_bench/{p.relative_to(root).as_posix()}": digest(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix in (".py", ".json")
    }


def _locked_runtime(dependencies):
    """Require exact, hash-pinned runtime requirements matching this interpreter."""
    runtime = {
        name: metadata.version(name) for name in ("numpy", "openpyxl", "et-xmlfile")
    }
    pins = {}
    lines = Path(dependencies).read_text(encoding="utf-8").splitlines()
    continuation = ""
    for line in lines:
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        continuation += " " + stripped.removesuffix(chr(92)).strip()
        if stripped.endswith(chr(92)):
            continue
        fields = continuation.split()
        continuation = ""
        pin = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^ ;]+)", fields[0])
        if (
            pin is None
            or len(fields) < 2
            or not all(
                re.fullmatch(r"--hash=sha256:[0-9a-f]{64}", h) for h in fields[1:]
            )
        ):
            raise ValueError("expected exact SHA-256-pinned runtime requirements")
        name = re.sub(r"[-_.]+", "-", pin[1]).lower()
        if name in pins:
            raise ValueError("duplicate runtime requirement")
        pins[name] = pin[2]
    if continuation or pins != runtime:
        raise ValueError("installed runtime versions differ from dependency lock")
    return runtime


def _material(wheel, dependencies):
    installed = _source_files()
    with ZipFile(wheel) as archive:
        members = [
            n
            for n in archive.namelist()
            if n.startswith("medextract_lab_bench/") and not n.endswith("/")
        ]
        if len(set(members)) != len(members):
            raise ValueError("duplicate package member in wheel")
        wheel_files = {name: sha256(archive.read(name)).hexdigest() for name in members}
    if installed != wheel_files:
        raise ValueError("installed program differs from supplied wheel")
    return {
        "release": __version__,
        "python": platform.python_version(),
        "runtime": _locked_runtime(dependencies),
        "installed_files": installed,
        "wheel_sha256": digest(wheel),
        "dependency_lock_sha256": digest(dependencies),
    }


def _fingerprint(body):
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return sha256(encoded).hexdigest()


def make_lock(wheel, dependencies):
    body = {
        "kind": "local-methods-lock-v1",
        "issued_utc": datetime.now(timezone.utc).isoformat(),
        "status": "local_technical_lock",
        "material": _material(wheel, dependencies),
    }
    return {**body, "digest": _fingerprint(body)}


def verify_lock(lock, wheel, dependencies):
    if not isinstance(lock, dict) or set(lock) != {
        "kind",
        "status",
        "issued_utc",
        "material",
        "digest",
    }:
        raise ValueError("invalid lock fields")
    if (
        lock["status"] != "local_technical_lock"
        or lock["kind"] != "local-methods-lock-v1"
    ):
        raise ValueError("unknown lock status")
    body = {k: v for k, v in lock.items() if k != "digest"}
    if lock["digest"] != _fingerprint(body) or lock["material"] != _material(
        wheel, dependencies
    ):
        raise ValueError("lock, runtime or artifact digest differs")
    return {"verification": "matched_local_technical_lock", "digest": lock["digest"]}
