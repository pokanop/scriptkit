import io
import zipfile

import pytest

from scriptkit.contracts import ArtifactPolicy, ContractError
from scriptkit.contracts.archives import validate_archive
from scriptkit.registry.source import RegistryArtifactSource
from test_installer import fixture, invoke


def bundle(entries, compression=zipfile.ZIP_DEFLATED):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=compression) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return out.getvalue()


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_archive_bytes", 2 * 1024**3 + 1),
        ("max_expanded_bytes", 8 * 1024**3 + 1),
        ("max_entries", 100001),
        ("max_expansion_ratio", 128 * 1024**2 + 1),
        ("command_timeout", 3601),
        ("max_entries", 0),
        ("max_entries", True),
        ("command_timeout", float("inf")),
    ],
)
def test_invalid_policy(field, value):
    with pytest.raises(ContractError, match="ceiling"):
        ArtifactPolicy(**{field: value})


@pytest.mark.parametrize(
    "entries,policy,match",
    [
        ([("x.py", "a" * 100)], ArtifactPolicy(max_expanded_bytes=99), "expanded"),
        ([("x.py", "a"), ("y.py", "b")], ArtifactPolicy(max_entries=1), "entry count"),
        ([("x.py", "a" * 100000)], ArtifactPolicy(max_expansion_ratio=10), "ratio"),
        ([("../x.py", "a")], ArtifactPolicy(), "unsafe"),
        ([("X.py", "a"), ("x.py", "b")], ArtifactPolicy(), "unsafe"),
        ([("x.py", "a")], ArtifactPolicy(max_archive_bytes=1), "archive bytes"),
    ],
)
def test_rejections(entries, policy, match, tmp_path):
    path = tmp_path / "bad.scripts.zip"
    path.write_bytes(bundle(entries))
    with pytest.raises(ContractError, match=match):
        validate_archive(path.name, path, policy=policy)


def test_malformed_and_crc(tmp_path):
    with pytest.raises(ContractError, match="invalid archive"):
        validate_archive("bad.whl", b"not zip", policy=ArtifactPolicy())
    raw = bundle([("x.py", "unique payload")], zipfile.ZIP_STORED)
    with pytest.raises(ContractError, match="invalid archive"):
        validate_archive("bad.scripts.zip", raw.replace(b"unique payload", b"broken payload"))


def test_policy_timeout(tmp_path):
    manager, _ = fixture(tmp_path)
    from scriptkit.manager import Installer

    manager = Installer(
        manager.root, manager.bin_dir, manager.source, policy=ArtifactPolicy(command_timeout=900)
    )
    assert manager.backend.timeout == 900


def test_local_source_spools_and_reverifies(tmp_path):
    manager, plan = fixture(tmp_path)
    directory = tmp_path / "local"
    directory.mkdir()
    item = plan.artifacts[0]
    raw = manager.source.fetch(item)
    (directory / item.sha256).write_bytes(raw)
    manager.source = RegistryArtifactSource(
        manager.source.resolver, "local", offline=True, artifact_directory=directory
    )
    manager.source.fetch = lambda _: pytest.fail("must stream")
    manager.install(plan)
    assert invoke(manager) == "working"
    (directory / item.sha256).write_bytes(b"corrupt")
    with pytest.raises(ContractError, match="mismatch"):
        manager.source.fetch_into(item, tmp_path / "bad")


def test_source_and_resolver_bounds(tmp_path):
    manager, plan = fixture(tmp_path)
    resolver = manager.source.resolver
    item = plan.artifacts[0]
    resolver.policy = ArtifactPolicy(max_archive_bytes=1)
    with pytest.raises(ContractError, match="policy"):
        resolver._validate_artifact(plan.registry.origin, item, True)
    manager.source.policy = resolver.policy
    with pytest.raises(ContractError, match="policy"):
        manager.source.fetch_into(item, tmp_path / "output")
    resolver.policy = ArtifactPolicy()
    manager.source.policy = ArtifactPolicy()
    directory = tmp_path / "local"
    directory.mkdir()
    resolver.artifact_directory = directory
    manager.source.artifact_directory = directory
    with pytest.raises(ContractError, match="missing"):
        resolver._validate_artifact(plan.registry.origin, item, True)
    with pytest.raises(ContractError, match="missing"):
        manager.source.fetch_into(item, tmp_path / "output")
    (directory / item.sha256).write_bytes(b"x" * item.size)
    with pytest.raises(ContractError, match="SHA-256"):
        resolver._validate_artifact(plan.registry.origin, item, True)
    (directory / item.sha256).write_bytes(b"x" * (item.size + 1))
    with pytest.raises(ContractError, match="size mismatch"):
        manager.source.fetch_into(item, tmp_path / "output")


def test_legacy_byte_source_and_manager_bound(tmp_path):
    manager, plan = fixture(tmp_path)
    original = manager.source

    class Source:
        authorize = original.authorize
        fetch = original.fetch

    manager.source = Source()
    manager.install(plan)
    other, plan = fixture(tmp_path / "other")
    other.policy = ArtifactPolicy(max_archive_bytes=1)
    with pytest.raises(ValueError, match="download policy"):
        other.install(plan)


def test_large_synthetic_wheel_full_install(tmp_path):
    # Also runs against installed wheels: the shared helper travels with tests.
    from large_artifact_probe import exercise, synthetic

    wheel = synthetic(tmp_path)
    with pytest.raises(ContractError):
        validate_archive(wheel.name, wheel)
    receipt = exercise(tmp_path, [wheel], smoke_module="probe")
    assert receipt.is_file()
