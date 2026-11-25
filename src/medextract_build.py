"""Write source distributions with reproducible, host-independent archive headers."""

import gzip
import os
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory

from setuptools.command.sdist import sdist


class SourceDistribution(sdist):
    def make_archive(
        self, base_name, format, root_dir=None, base_dir=None, owner=None, group=None
    ):
        if format != "gztar":
            return super().make_archive(
                base_name, format, root_dir, base_dir, owner, group
            )

        output = Path(f"{base_name}.tar.gz")
        if root_dir is not None:
            output = output.absolute()
        if self.dry_run:
            return str(output)

        # 1980-01-01 is also representable when this sdist is unpacked to build a wheel.
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "315532800"))
        release_dir = Path(base_dir or ".")
        source = Path(os.fsdecode(root_dir or ".")) / release_dir

        def normalize(member):
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            member.mtime = epoch
            member.mode = 0o755 if member.isdir() or member.mode & 0o111 else 0o644
            # tarfile will regenerate structural PAX fields for long or Unicode paths.
            member.pax_headers.clear()
            return member

        output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=output.parent) as temp_dir:
            staged = Path(temp_dir, output.name)
            with (
                staged.open("wb") as raw,
                gzip.GzipFile(
                    filename="", mode="wb", fileobj=raw, mtime=epoch
                ) as compressed,
                tarfile.open(
                    fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
                ) as archive,
            ):
                archive.add(source, arcname=release_dir, filter=normalize)
            staged.replace(output)
        return str(output)
