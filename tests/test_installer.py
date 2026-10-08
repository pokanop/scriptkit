from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from scriptkit.contracts.models import (
    Artifact,
    CatalogRelease,
    CommandSpec,
    DependencyLock,
    Platform,
    PythonRequirement,
    ToolRelease,
    ToolSpec,
)
from scriptkit.manager import Installer, PipBackend, UvBackend
from scriptkit.manager import storage
from scriptkit.manager.backend import interpreter
from scriptkit.registry.source import RegistryArtifactSource
from scriptkit.registry import Registry, RegistryStore, Resolver, VerifiedCache
from scriptkit.state import StateConflict


def artifact(name, data):
    return Artifact(name, hashlib.sha256(data).hexdigest(), len(data))


def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as out:
        for name, data in entries.items():
            out.writestr(name, data)
    return stream.getvalue()


class Transport:
    def __init__(self, data):
        self.data = data

    def fetch(self, url, limit):
        return self.data[url]


class FastBackend:
    """Real isolated interpreter, without repeatedly bootstrapping pip in fault tests."""

    def stage(self, environment, wheels):
        assert not wheels
        subprocess.run(
            [sys.executable, "-I", "-m", "venv", "--copies", "--without-pip", str(environment)],
            check=True,
        )
        return interpreter(environment)


def fixture(tmp_path, *, wheel=False, backend=None, fail=False):
    host = Platform(
        {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}[platform.system()],
        {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}[
            platform.machine().lower()
        ],
    )
    python = PythonRequirement("3.11.0", "3.99.0")
    payload = 'def main():\n    print("working")\n    return ' + ("1" if fail else "0") + "\n"
    entries = {"hello.py": payload}
    if wheel:
        entries.update(
            {
                "hello-1.0.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: hello\nVersion: 1.0.0\n",
                "hello-1.0.0.dist-info/WHEEL": "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
                "hello-1.0.0.dist-info/RECORD": "",
            }
        )
    data = archive(entries)
    package = artifact("hello-1.0.0-py3-none-any.whl" if wheel else "hello.scripts.zip", data)
    tool = ToolSpec(
        1,
        "hello",
        "1.0.0",
        "fixture",
        "hello:main",
        python,
        (host,),
        (CommandSpec(1, "hello", "help", ()),),
    )
    catalog = CatalogRelease(
        1,
        "local",
        "1.0.0",
        (ToolRelease(tool, package, (DependencyLock(1, host, python, "pip", ()),)),),
    )
    raw = catalog.canonical_json().encode()
    origin = "https://fixture.example/"
    registry = Registry(1, "local", origin, artifact("catalog.json", raw), 9999999999)
    store = RegistryStore(tmp_path / "registries.json")
    store.add(registry, consent_origin=origin)
    cache = VerifiedCache(
        tmp_path / "cache", Transport({origin + package.path: data, origin + "catalog.json": raw})
    )
    resolver = Resolver(store, cache)
    plan = resolver.resolve(
        "local/hello@1.0.0",
        platform=host,
        python_version=platform.python_version(),
        generation="first",
        destination="hello",
    )
    manager = Installer(
        tmp_path / "state",
        tmp_path / "bin",
        RegistryArtifactSource(resolver, "local", offline=True),
        backend=backend or FastBackend(),
    )
    return manager, plan


def update(plan):
    return replace(
        plan,
        installation=replace(plan.installation, generation="second", previous_generation="first"),
    )


def invoke(manager):
    command = manager.bin_dir / ("hello.cmd" if os.name == "nt" else "hello")
    return subprocess.check_output([str(command), "--help"], text=True).strip()


def test_lifecycle_and_dry_run(tmp_path):
    manager, plan = fixture(tmp_path)
    assert manager.install(plan, dry_run=True) is None
    assert not manager.root.exists()
    receipt = manager.install(plan)
    record = json.loads(receipt.read_text())
    assert record["resolved"] == plan.to_dict()
    assert record["files"] and record["launchers"]
    assert invoke(manager) == "working"
    original = {p: p.stat().st_mtime_ns for p in manager._files("hello")}
    manager.install(update(plan))
    assert manager._active("hello") == "second"
    assert {p: p.stat().st_mtime_ns for p in original} == original
    manager.rollback("hello")
    assert manager._active("hello") == "first"
    assert invoke(manager) == "working"
    config = manager.root / "user.json"
    config.write_text("private")
    manager.uninstall("hello")
    assert manager._active("hello") is None
    assert all(not p.exists() for p in manager._files("hello"))
    assert config.read_text() == "private"
    assert receipt.exists()  # retained for Windows readers / audit
    manager.uninstall("hello")


@pytest.mark.parametrize(
    "phase", ["plan", "validate", "fetch", "stage", "smoke", "journal", "pointer", "activate"]
)
def test_fault_at_every_phase(tmp_path, phase):
    manager, plan = fixture(tmp_path)
    manager.install(plan)

    def fail(p):
        if p == phase:
            raise KeyboardInterrupt

    manager.checkpoint = fail
    with pytest.raises(KeyboardInterrupt):
        manager.install(update(plan))
    assert invoke(manager) == "working"
    manager.checkpoint = lambda _: None
    manager.recover("hello")
    assert manager._active("hello") == (
        "second" if phase in {"journal", "pointer", "activate"} else "first"
    )
    assert invoke(manager) == "working"


@pytest.mark.parametrize("backend", ["pip", "uv"])
def test_real_wheel_backends(tmp_path, backend):
    if backend == "uv" and not shutil.which("uv"):
        pytest.skip("optional uv not provisioned")
    adapter = PipBackend() if backend == "pip" else UvBackend(Path(shutil.which("uv")))
    manager, plan = fixture(tmp_path, wheel=True, backend=adapter)
    manager.install(plan)
    assert invoke(manager) == "working"
    manager.install(update(plan))
    manager.rollback("hello")
    assert invoke(manager) == "working"


def test_smoke_failure_keeps_previous(tmp_path):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    other = tmp_path / "other"
    other.mkdir()
    failing, bad = fixture(other, fail=True)
    manager.source = failing.source
    with pytest.raises(Exception):
        manager.install(update(bad))
    assert manager._active("hello") == "first"
    assert invoke(manager) == "working"


@pytest.mark.parametrize("error", [PermissionError("locked"), OSError(28, "disk full")])
def test_failed_pointer_replace_recoverable(tmp_path, monkeypatch, error):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    real = os.replace

    def deny(src, dst):
        if Path(dst).name == "active.json":
            raise error
        real(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", deny)
        with pytest.raises(OSError):
            manager.install(update(plan))
    assert manager._active("hello") == "first"
    assert invoke(manager) == "working"
    manager.recover("hello")
    assert manager._active("hello") == "second"


def test_foreign_launcher_never_deleted(tmp_path):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    path = next(p for p in manager._files("hello") if p.parent == manager.bin_dir)
    path.write_text("foreign")
    with pytest.raises(ValueError, match="foreign"):
        manager.uninstall("hello")
    assert path.read_text() == "foreign"
    assert manager._active("hello") == "first"


def test_competing_process_and_interruption(tmp_path):
    root = tmp_path / "root"
    code = "from pathlib import Path; from scriptkit.manager.storage import locked; import sys;\nwith locked(Path(sys.argv[1])): print('locked', flush=True); sys.stdin.read()"
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(StateConflict):
            with storage.locked(root):
                pass
        child.kill()
        child.wait(timeout=10)
        with storage.locked(root):
            pass
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_reject_stale_plan_and_tampered_rollback(tmp_path):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    with pytest.raises(StateConflict, match="stale"):
        manager.install(plan)
    with pytest.raises(ValueError, match="previous"):
        manager.rollback("hello")
    manager.install(update(plan))
    (manager.root / "hello/generations/first/run.py").write_text("foreign")
    with pytest.raises(ValueError, match="changed"):
        manager.rollback("hello")
    assert manager._active("hello") == "second"
    assert invoke(manager) == "working"


def test_storage_invalid_and_symlinks(tmp_path):
    path = tmp_path / "state"
    path.write_text("[]")
    with pytest.raises(ValueError):
        storage.read(path)
    if os.name != "nt":
        link = tmp_path / "link"
        link.symlink_to(path)
        with pytest.raises(ValueError):
            storage.read(link)
        with pytest.raises(ValueError):
            storage.publish(link, {})
        with pytest.raises(ValueError):
            Installer(link, tmp_path / "bin", VerifiedCache(tmp_path / "cache"))


def test_namespace_cannot_take_over(tmp_path):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    changed = replace(
        update(plan), registry=replace(plan.registry, origin="https://other.example/")
    )
    with pytest.raises(ValueError, match="namespace"):
        manager.install(changed)
