from pathlib import Path
from zipfile import ZipFile, BadZipFile

import pytest

from medextract_lab_bench.lock import make_lock, verify_lock


def test_lock_detects_digest_and_evidence_tamper(tmp_path):
    wheel = tmp_path / "demo.whl"
    from medextract_lab_bench import lock as lock_module

    root = Path(lock_module.__file__).parent
    with ZipFile(wheel, "w") as archive:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".json"):
                archive.write(
                    path, "medextract_lab_bench/" + path.relative_to(root).as_posix()
                )
    receipt = tmp_path / "dependency-lock.txt"
    from importlib.metadata import version

    receipt.write_text(
        "".join(
            f"{name}=={version(name)} --hash=sha256:{'0' * 64}\n"
            for name in ("numpy", "openpyxl", "et-xmlfile")
        )
    )
    lock = make_lock(wheel, receipt)
    verify_lock(lock, wheel, receipt)
    altered = dict(lock, status="formally_approved")
    with pytest.raises(ValueError):
        verify_lock(altered, wheel, receipt)
    wheel.write_bytes(b"changed")
    with pytest.raises((ValueError, BadZipFile)):
        verify_lock(lock, wheel, receipt)


def test_lock_requires_real_dependency_pins(tmp_path):
    from medextract_lab_bench.lock import _locked_runtime

    path = tmp_path / "lock.txt"
    path.write_text("numpy==0.0.0 \n")
    with pytest.raises(ValueError):
        _locked_runtime(path)
