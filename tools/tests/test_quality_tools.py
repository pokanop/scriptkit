"""Gate regression tests; deliberately separate from installed runtime tests."""

import gzip
import importlib.util
import io
from pathlib import Path
import tarfile
import zipfile

import pytest


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = load("verify_results").verify
release = load("rehearse_release")


@pytest.mark.parametrize(
    "body",
    [
        "",
        "<testcase><skipped/></testcase>",
        "<testcase><failure/></testcase>",
        "<testcase><error/></testcase>",
    ],
)
def test_reject_incomplete_suite(tmp_path, body):
    report = tmp_path / "report.xml"
    report.write_text(f"<testsuite>{body}</testsuite>")
    with pytest.raises(ValueError):
        verify(report, minimum=1)


def test_failure_not_hidden_by_enough_successes(tmp_path):
    report = tmp_path / "report.xml"
    report.write_text("<testsuite><testcase/><testcase><failure/></testcase></testsuite>")
    with pytest.raises(ValueError):
        verify(report, minimum=1)
    report.write_text("<testsuite><testcase/></testsuite>")
    verify(report, minimum=1)


def test_bad_report(tmp_path):
    with pytest.raises(FileNotFoundError):
        verify(tmp_path / "missing")


def test_normalized_sdist_and_checksum_detect_tampering(tmp_path):
    artifact = tmp_path / "package.tar.gz"
    with tarfile.open(artifact, "w:gz") as archive:
        member = tarfile.TarInfo("package/file")
        member.size = 4
        member.mtime = 12345
        member.uid = 1000
        archive.addfile(member, io.BytesIO(b"data"))
    release.normalize_sdist(artifact, 100)
    first = artifact.read_bytes()
    release.normalize_sdist(artifact, 100)
    assert artifact.read_bytes() == first
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(first))) as archive:
        member = archive.getmembers()[0]
        assert (member.mtime, member.uid, member.uname) == (100, 0, "")
        assert archive.extractfile(member).read() == b"data"
    before = release.hashes(tmp_path)
    artifact.write_bytes(first + b"tampered")
    assert release.hashes(tmp_path) != before


def test_resources_must_be_in_wheel(tmp_path):
    package = tmp_path / "src" / "scriptkit"
    package.mkdir(parents=True)
    (package / "template.txt").write_text("resource")
    wheel = tmp_path / "package.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("package.dist-info/licenses/LICENSE", "MIT")
    with pytest.raises(ValueError, match="resource"):
        release.validate_resources(wheel, tmp_path)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("scriptkit/template.txt", "resource")
    with pytest.raises(ValueError, match="license"):
        release.validate_resources(wheel, tmp_path)
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("package.dist-info/licenses/LICENSE", "MIT")
    release.validate_resources(wheel, tmp_path)
