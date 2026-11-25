import gzip
import os
import tarfile

from setuptools.dist import Distribution

from medextract_build import SourceDistribution


def test_source_archive_omits_host_metadata_and_rebuilds_identically(
    tmp_path, monkeypatch
):
    source = tmp_path / "example-1"
    source.mkdir()
    notice = source / "LICENSE"
    notice.write_bytes(b"Copyright example author\n")
    script = source / "example-tool"
    script.write_bytes(b"#!/bin/sh\n")
    script.chmod(0o755)
    gettarinfo = tarfile.TarFile.gettarinfo

    def add_invented_host_metadata(archive, *args, **kwargs):
        member = gettarinfo(archive, *args, **kwargs)
        member.uid, member.gid = 12345, 54321
        member.uname, member.gname = "example-builder", "example-build-group"
        member.pax_headers.update(
            {"uname": member.uname, "SCHILY.xattr.example": "example-host"}
        )
        return member

    monkeypatch.setattr(tarfile.TarFile, "gettarinfo", add_invented_host_metadata)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1000000000")
    command = SourceDistribution(Distribution())
    output = tmp_path / "output" / "example-1"
    filename = command.make_archive(
        output, "gztar", root_dir=tmp_path, base_dir=source.name
    )
    archive_path = output.with_suffix(".tar.gz")
    assert str(archive_path) == filename
    original = archive_path.read_bytes()

    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        assert [m.name for m in members] == [
            "example-1",
            "example-1/LICENSE",
            "example-1/example-tool",
        ]
        assert all((m.uid, m.gid, m.uname, m.gname) == (0, 0, "", "") for m in members)
        assert all(m.mtime == 1000000000 and m.pax_headers == {} for m in members)
        assert [m.mode for m in members] == [0o755, 0o644, 0o755]
        assert archive.extractfile(members[1]).read() == b"Copyright example author\n"
    assert original[3] == 0  # No gzip filename, comment, or extra field.
    assert int.from_bytes(original[4:8], "little") == 1000000000
    assert b"example-host" not in gzip.decompress(original)

    os.utime(notice, (1100000000, 1100000000))
    notice.chmod(0o600)
    command.make_archive(output, "gztar", root_dir=tmp_path, base_dir=source.name)
    assert archive_path.read_bytes() == original
