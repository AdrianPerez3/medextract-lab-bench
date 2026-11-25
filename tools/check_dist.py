"""Reject unexpected contents in a MedExtract wheel or source distribution."""

from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
from zipfile import ZipFile


def allowed(relative, wheel):
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        return False
    if not path.parts:
        return False
    head = path.parts[0]
    package = "medextract_lab_bench"
    if head == package and wheel or path.parts[:2] == ("src", package) and not wheel:
        return (
            path.suffix == ".py"
            or path.parent.name == "protocols"
            and path.name
            in {
                "bariatric-results.json",
                "cbc-results.json",
                "methods-v1.json",
                "scorecard.schema.json",
                "vitaldb-hemoglobin-results.json",
                "sao-paulo-hemoglobin-results.json",
            }
        )
    if wheel and head.endswith(".dist-info"):
        return path.name in {
            "METADATA",
            "WHEEL",
            "entry_points.txt",
            "top_level.txt",
            "LICENSE",
            "RECORD",
        }
    if wheel:
        return False
    if head == "docs":
        return relative in {
            "docs/data.md",
            "docs/measurement-events.md",
            "docs/reproduction.md",
            "docs/vitaldb-results.md",
            "docs/boston-validation.md",
            "docs/sao-paulo-results.md",
            "docs/sao-paulo-protocol.md",
        }
    if head in {"tests", "tools"}:
        return path.suffix == ".py"
    if head == "requirements":
        return len(path.parts) == 2 and path.name in {"runtime.lock", "constraints.txt"}
    if len(path.parts) == 3 and path.parts[:2] == ("src", package + ".egg-info"):
        return path.name in {
            "PKG-INFO",
            "SOURCES.txt",
            "dependency_links.txt",
            "entry_points.txt",
            "requires.txt",
            "top_level.txt",
        }
    return relative in {
        "LICENSE",
        "README.md",
        "CITATION.cff",
        "CONTRIBUTING.md",
        "MANIFEST.in",
        "pyproject.toml",
        "src/medextract_build.py",
        "setup.cfg",
        "PKG-INFO",
    }


def check(path):
    wheel = path.suffix == ".whl"
    if wheel:
        with ZipFile(path) as archive:
            members = archive.infolist()
            if archive.comment or any(
                member.comment or member.extra for member in members
            ):
                raise ValueError("unexpected wheel metadata")
            if any(stat.S_ISLNK(member.external_attr >> 16) for member in members):
                raise ValueError("unexpected symbolic link in wheel")
            names = [member.filename for member in members if not member.is_dir()]
    else:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if any(not (item.isfile() or item.isdir()) for item in members):
                raise ValueError("unexpected nonregular member in source distribution")
            names = []
            for item in members:
                if (
                    item.uid != 0
                    or item.gid != 0
                    or item.uname not in ("", "root")
                    or item.gname not in ("", "root")
                    or item.pax_headers.keys() - {"mtime", "path"}
                ):
                    raise ValueError(
                        "unexpected owner or extended metadata in source distribution"
                    )
                if item.isfile():
                    parts = PurePosixPath(item.name).parts
                    if not parts or parts[0] != path.name.removesuffix(".tar.gz"):
                        raise ValueError("unexpected source archive root")
                    names.append("/".join(parts[1:]))
    unknown = [name for name in names if not allowed(name, wheel)]
    if not names or unknown or len(set(names)) != len(names):
        raise ValueError(f"unexpected or duplicate archive members: {unknown}")
    print(f"{path.name}: {len(names)} allowed files")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("supply built wheel and/or .tar.gz")
    for argument in sys.argv[1:]:
        check(Path(argument))
