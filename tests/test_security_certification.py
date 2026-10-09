"""Composed adversarial boundaries; fixtures are synthetic and never need live keys.

These complement the per-component negative tests, exercising the real installer
and the same namespace-bound source used by the scripts consumer.
"""

from dataclasses import replace
import hashlib
import io
import json
import stat
import sys
import zipfile

import pytest

from scriptkit import CliError
from scriptkit.contracts.codec import ContractError
from scriptkit.contracts.models import LockedPackage
from scriptkit.manager import storage
from scriptkit.manager.backend import run
from scriptkit.registry import RegistryStore
from scriptkit.state import StateConflict
from test_installer import archive, artifact, fixture, invoke, update


def tree(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("mutation", ["expiry", "remove", "repoint", "rollback"])
def test_trust_changes_between_resolution_and_execution(tmp_path, mutation):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    before = tree(manager.root)
    source = manager.source
    store = source.resolver.store
    registry = plan.registry
    if mutation == "expiry":
        source.resolver.clock = lambda: registry.expires_at
    else:
        store.remove(registry.namespace)
        if mutation != "remove":
            # Even an explicitly consented registration cannot authorize a saved
            # plan from a previous origin or catalog pin.
            changed = replace(registry, origin="https://other.example/")
            if mutation == "rollback":
                cache = source.resolver.cache
                raw = cache.get(registry.origin, registry.catalog, offline=True)
                data = json.loads(raw)
                data["version"] = "0.9.0"
                raw = json.dumps(data).encode()
                pin = artifact("old.json", raw)
                (cache.root / pin.sha256).write_bytes(raw)
                changed = replace(registry, catalog=pin)
            store.add(changed, consent_origin=changed.origin)
    with pytest.raises(ContractError, match="expired|unknown registry|no longer matches"):
        manager.install(update(plan))
    assert tree(manager.root) == before
    assert invoke(manager) == "working"


@pytest.mark.parametrize("mutation", ["content", "size", "link"])
def test_poisoned_cache_never_reaches_execution(tmp_path, mutation):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    package = plan.artifacts[0]
    cached = manager.source.resolver.cache.root / package.sha256
    if mutation == "link":
        outside = tmp_path / "outside"
        outside.write_bytes(cached.read_bytes())
        cached.unlink()
        try:
            cached.symlink_to(outside)
        except OSError:
            pytest.skip("host does not permit symlink creation")
    else:
        data = cached.read_bytes()
        cached.write_bytes(data + b"x" if mutation == "size" else b"x" * len(data))
    with pytest.raises(ValueError, match="SHA-256 mismatch|symlink"):
        manager.install(update(plan))
    assert manager.current_generation("hello") == "first"
    assert invoke(manager) == "working"
    assert not (manager.root / "hello/generations/second/receipt.json").exists()


@pytest.mark.parametrize("member", ["../outside.py", "/outside.py", "link.py", "pipe.py"])
def test_hash_valid_hostile_archive_is_still_rejected(tmp_path, member):
    manager, plan = fixture(tmp_path)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        entry = zipfile.ZipInfo(member)
        entry.create_system = 3
        if member == "link.py":
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        elif member == "pipe.py":
            entry.external_attr = (stat.S_IFIFO | 0o600) << 16
        archive.writestr(entry, b"../outside")
    raw = output.getvalue()
    package = artifact("hostile.scripts.zip", raw)
    release = replace(plan.installation.release, artifact=package)
    installation = replace(plan.installation, release=release)
    hostile = replace(plan, installation=installation, artifacts=(package,))

    class Source:
        def authorize(self, resolved):
            assert resolved == hostile

        def fetch(self, requested):
            assert requested == package
            return raw

    class NoExecution:
        def stage(self, *args):
            pytest.fail("hostile bytes reached package execution")

    manager.source = Source()
    manager.backend = NoExecution()
    with pytest.raises(ContractError, match="unsafe archive member"):
        manager.install(hostile)
    assert manager.current_generation("hello") is None
    assert not manager.bin_dir.exists()
    assert not (manager.root / "hello/generations/first/source").exists()


def test_replayed_plan_and_concurrent_install_preserve_generation(tmp_path):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    before = tree(manager.root)
    with pytest.raises(StateConflict, match="lineage"):
        manager.install(plan)
    with storage.locked(manager.root):
        with pytest.raises(StateConflict, match="busy"):
            manager.install(update(plan))
    assert tree(manager.root) == before
    manager.install(update(plan))
    assert invoke(manager) == "working"


@pytest.mark.parametrize("mutation", ["payload", "receipt", "launcher"])
def test_tampered_rollback_and_uninstall_fail_without_destroying_state(tmp_path, mutation):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    manager.install(update(plan))
    old = manager.root / "hello/generations/first"
    if mutation == "payload":
        (old / "run.py").write_text("raise RuntimeError('tampered')")
    elif mutation == "receipt":
        receipt = old / "receipt.json"
        data = json.loads(receipt.read_text())
        data["files"]["run.py"] = "sha256:" + "0" * 64
        receipt.write_text(json.dumps(data))
    else:
        launcher = next(iter(manager._files("hello")))
        launcher.write_bytes(b"user-owned replacement")
    before = tree(manager.root)
    with pytest.raises(ValueError, match="generation changed|foreign launcher"):
        manager.rollback("hello")
    assert manager.current_generation("hello") == "second"
    assert tree(manager.root) == before
    if mutation == "launcher":
        with pytest.raises(ValueError, match="foreign launcher"):
            manager.uninstall("hello")
        assert launcher.read_bytes() == b"user-owned replacement"
    else:
        assert invoke(manager) == "working"


def test_namespace_confusion_cannot_adopt_an_installed_tool(tmp_path):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    changed = replace(plan.registry, namespace="imposter", origin="https://other.example/")
    other = replace(update(plan), registry=changed)
    with pytest.raises(ValueError, match="namespace"):
        manager.install(other)
    assert invoke(manager) == "working"


def test_consent_requires_exact_origin(tmp_path):
    manager, plan = fixture(tmp_path)
    store = RegistryStore(tmp_path / "untrusted.json")
    for consent in ("", "https://fixture.example", "https://other.example/"):
        with pytest.raises(ContractError, match="consent"):
            store.add(plan.registry, consent_origin=consent)
    assert not store.path.exists()
    store.add(plan.registry, consent_origin=plan.registry.origin)
    assert store.list() == (plan.registry,)


def test_saved_plan_cannot_substitute_release_or_lock(tmp_path):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    before = tree(manager.root)
    planned = update(plan)
    release = planned.installation.release
    # Internally consistent forgery: provenance hashes recomputed, registry unchanged.
    lock = replace(release.locks[0], python=replace(release.locks[0].python, minimum="3.11.1"))
    forged = replace(
        planned,
        installation=replace(
            planned.installation,
            release=replace(release, tool=replace(release.tool, version="9.9.9"), locks=(lock,)),
        ),
        lock_sha256=(hashlib.sha256(lock.canonical_json().encode()).hexdigest(),),
    )
    with pytest.raises(ContractError, match="no longer matches"):
        manager.install(forged)
    assert tree(manager.root) == before
    assert invoke(manager) == "working"


def test_locked_wheel_identity_is_enforced_before_backend(tmp_path):
    manager, plan = fixture(tmp_path)
    wheel = archive(
        {
            "other-9.9.dist-info/METADATA": "Name: other\nVersion: 9.9\n",
            "other-9.9.dist-info/WHEEL": "Wheel-Version: 1.0\n",
        }
    )
    item = artifact("dependency-1.0.0-py3-none-any.whl", wheel)
    release = plan.installation.release
    lock = replace(release.locks[0], packages=(LockedPackage("dependency", "1.0.0", item),))
    release = replace(release, locks=(lock,))
    substituted = replace(
        plan,
        installation=replace(plan.installation, release=release),
        lock_sha256=(hashlib.sha256(lock.canonical_json().encode()).hexdigest(),),
        artifacts=(release.artifact, item),
    )
    blobs = {release.artifact: manager.source.fetch(release.artifact), item: wheel}

    class Source:
        def authorize(self, resolved):
            assert resolved == substituted

        def fetch(self, requested):
            return blobs[requested]

    class NoExecution:
        def stage(self, *args):
            pytest.fail("mislabelled wheel reached package execution")

    manager.source, manager.backend = Source(), NoExecution()
    with pytest.raises(ValueError, match="identity does not match"):
        manager.install(substituted)
    assert manager.current_generation("hello") is None
    assert not manager.bin_dir.exists()


def test_installer_rechecks_bytes_from_injected_source(tmp_path):
    manager, plan = fixture(tmp_path)
    unpinned = archive({"hello.py": "def main():\n    return 0\n"})

    class Source:
        def authorize(self, resolved):
            assert resolved == plan

        def fetch(self, requested):
            return unpinned

    class NoExecution:
        def stage(self, *args):
            pytest.fail("unpinned bytes reached package execution")

    manager.source, manager.backend = Source(), NoExecution()
    with pytest.raises(ValueError, match="hash/size mismatch"):
        manager.install(plan)
    assert manager.current_generation("hello") is None
    assert not manager.bin_dir.exists()


def test_package_failure_output_does_not_leak_secrets(capsys, caplog):
    sentinel = "synthetic-private-output-630"
    with pytest.raises(CliError) as caught:
        run([sys.executable, "-I", "-c", f"import sys; print({sentinel!r}); sys.exit(1)"])
    assert sentinel not in str(caught.value)
    captured = capsys.readouterr()
    assert sentinel not in captured.out + captured.err + caplog.text


def test_cache_hash_names_do_not_make_content_trusted(tmp_path):
    manager, plan = fixture(tmp_path)
    cached = manager.source.resolver.cache.root / plan.registry.catalog.sha256
    original = cached.read_bytes()
    assert hashlib.sha256(original).hexdigest() == cached.name
    cached.write_bytes(original.replace(b"hello", b"evil!"))
    with pytest.raises(ContractError, match="mismatch"):
        manager.install(plan, dry_run=True)
    assert not manager.root.exists()
