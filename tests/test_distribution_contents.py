"""Ensure local archive-owner metadata stays out of release downloads."""

from io import BytesIO
from pathlib import Path
import runpy
import tarfile
from zipfile import ZipFile

import pytest


check_dist = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "tools" / "check_dist.py")
)["check"]


def test_rejects_local_account_on_source_tar_directory(tmp_path):
    path = tmp_path / "example-1.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        folder = tarfile.TarInfo("example-1")
        folder.type = tarfile.DIRTYPE
        folder.uid, folder.gid = 501, 20
        folder.uname = "invented-builder"
        archive.addfile(folder)
        text = tarfile.TarInfo("example-1/README.md")
        text.size = 1
        archive.addfile(text, BytesIO(b"x"))
    with pytest.raises(ValueError, match="metadata"):
        check_dist(path)


def test_rejects_private_wheel_comment(tmp_path):
    path = tmp_path / "example.whl"
    with ZipFile(path, "w") as archive:
        archive.writestr("medextract_lab_bench/__init__.py", "")
        archive.comment = b"invented workstation label"
    with pytest.raises(ValueError, match="metadata"):
        check_dist(path)
