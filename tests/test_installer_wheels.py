from dataclasses import replace

import pytest

from scriptkit.contracts.models import LockedPackage
from scriptkit.manager.wheels import verify_locks
from test_installer import archive, artifact, fixture


@pytest.mark.parametrize(
    "metadata",
    [
        "Name: dependency\nVersion: 1.0.0\n",
        "Name: other\nVersion: 1.0.0\n",
        "Name: dependency\nVersion: 9.0.0\n",
        "Name: dependency\nName: other\nVersion: 1.0.0\n",
    ],
)
@pytest.mark.parametrize("vendored", [False, True])
def test_dependency_wheel_identity(tmp_path, metadata, vendored):
    _, resolved = fixture(tmp_path)
    entries = {"dependency-1.0.0.dist-info/METADATA": metadata}
    if vendored:
        entries["dependency/_vendor/other-2.0.dist-info/METADATA"] = "Name: other\nVersion: 2.0\n"
    raw = archive(entries)
    item = artifact("dependency-1.0.0-py3-none-any.whl", raw)
    lock = replace(
        resolved.installation.release.locks[0],
        packages=(LockedPackage("dependency", "1.0.0", item),),
    )
    plan = replace(
        resolved.installation, release=replace(resolved.installation.release, locks=(lock,))
    )
    if metadata == "Name: dependency\nVersion: 1.0.0\n":
        verify_locks(plan, {item: raw})
    else:
        with pytest.raises(ValueError, match="identity"):
            verify_locks(plan, {item: raw})


@pytest.mark.parametrize("filename", ["dependency.tar.gz", "dependency.whl"])
def test_dependency_rejects_source_and_missing_metadata(tmp_path, filename):
    _, resolved = fixture(tmp_path)
    raw = archive(
        {"dependency/_vendor/other.dist-info/METADATA": "Name: dependency\nVersion: 1.0.0\n"}
    )
    item = artifact(filename, raw)
    lock = replace(
        resolved.installation.release.locks[0],
        packages=(LockedPackage("dependency", "1.0.0", item),),
    )
    plan = replace(
        resolved.installation, release=replace(resolved.installation.release, locks=(lock,))
    )
    with pytest.raises(ValueError):
        verify_locks(plan, {item: raw})
