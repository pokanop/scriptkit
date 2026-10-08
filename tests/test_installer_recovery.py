from __future__ import annotations

import errno
import os
import subprocess
import sys
import types
from dataclasses import replace
from pathlib import Path

import pytest

from scriptkit.contracts.codec import ContractError
from scriptkit.contracts.models import DependencyLock
from scriptkit.manager import launchers, storage
from scriptkit.manager import backend as adapters
from scriptkit.state import StateConflict
from test_installer import fixture, invoke, update


@pytest.mark.parametrize("phase", ["stage", "journal", "pointer"])
def test_hard_exit_recovery(tmp_path, phase):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(update(plan).canonical_json())
    code = """
import os, sys
from pathlib import Path
from scriptkit.contracts.catalog import ResolvedPlan
from scriptkit.manager import Installer
from scriptkit.registry import Resolver, RegistryStore, VerifiedCache
from scriptkit.registry.source import RegistryArtifactSource
root = Path(sys.argv[1])
source = RegistryArtifactSource(Resolver(RegistryStore(root / 'registries.json'), VerifiedCache(root / 'cache')), 'local', offline=True)
def checkpoint(phase):
    if phase == sys.argv[2]: os._exit(73)
Installer(root / 'state', root / 'bin', source, checkpoint=checkpoint).install(ResolvedPlan.from_json((root / 'plan.json').read_text()))
"""
    assert (
        subprocess.run([sys.executable, "-c", code, str(tmp_path), phase], timeout=60).returncode
        == 73
    )
    assert invoke(manager) == "working"
    manager.recover("hello")
    assert manager._active("hello") == ("first" if phase == "stage" else "second")
    assert invoke(manager) == "working"


@pytest.mark.parametrize("filename", ["receipt.json", "journal.json", "active.json"])
@pytest.mark.parametrize("number", [errno.ENOSPC, errno.EACCES])
def test_persistence_failures_preserve_generation(tmp_path, monkeypatch, filename, number):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    original = storage.publish

    def fail(path, value):
        if path.name == filename:
            raise OSError(number, "injected persistence error")
        original(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(storage, "publish", fail)
        with pytest.raises(OSError):
            manager.install(update(plan))
    assert manager._active("hello") == "first"
    assert invoke(manager) == "working"
    manager.recover("hello")
    assert invoke(manager) == "working"


def test_uninstall_interruption_and_locked_launcher(tmp_path, monkeypatch):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    original = Path.unlink
    path = manager._tool("hello") / "launch.py"

    def deny(self, *args, **kwargs):
        if self == path:
            raise PermissionError("Windows reader denies deletion")
        return original(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", deny)
        with pytest.raises(PermissionError):
            manager.uninstall("hello")
    assert manager._active("hello") is None
    manager.recover("hello")
    assert all(not p.exists() for p in manager._files("hello"))


def test_revalidation_and_doctor(tmp_path):
    manager, plan = fixture(tmp_path)
    with pytest.raises(ValueError, match="host"):
        manager.validate(
            replace(plan, installation=replace(plan.installation, python_version="3.11.1"))
        )
    with pytest.raises(ValueError, match="destination"):
        manager.validate(
            replace(plan, installation=replace(plan.installation, destination="other"))
        )
    release = plan.installation.release
    lock = release.locks[0]
    system = DependencyLock(
        1,
        lock.platform,
        lock.python,
        {"linux": "apt", "macos": "brew", "windows": "winget"}[lock.platform.os],
        (),
    )
    new_release = replace(release, locks=(*release.locks, system))
    import hashlib

    doctor = replace(
        plan,
        installation=replace(
            plan.installation, release=new_release, backends=("pip", system.backend)
        ),
        lock_sha256=(
            *plan.lock_sha256,
            hashlib.sha256(system.canonical_json().encode()).hexdigest(),
        ),
    )
    with pytest.raises(ValueError, match="doctor"):
        manager.validate(doctor)
    source = manager.source
    source.resolver.store.remove("local")
    with pytest.raises(ContractError, match="unknown"):
        manager.install(plan)
    assert manager._active("hello") is None


def test_registry_pin_rechecked(tmp_path):
    manager, plan = fixture(tmp_path)
    store = manager.source.resolver.store
    registry = store.list()[0]
    store.remove("local")
    store.add(replace(registry, expires_at=registry.expires_at - 1), consent_origin=registry.origin)
    with pytest.raises(ContractError, match="pins"):
        manager.install(plan, dry_run=True)


def test_invalid_state_refused(tmp_path):
    manager, plan = fixture(tmp_path)
    with pytest.raises(ValueError, match="name"):
        manager.recover("../foreign")
    with pytest.raises(ValueError, match="installed"):
        manager.rollback("hello")
    tool = manager._tool("hello")
    storage.publish(tool / "active.json", {"target": "../foreign"})
    with pytest.raises(ValueError, match="pointer"):
        manager.rollback("hello")
    storage.publish(tool / "active.json", {"target": "first"})
    with pytest.raises(ValueError, match="receipt"):
        manager.rollback("hello")
    storage.publish(tool / "journal.json", {"target": "../foreign"})
    with pytest.raises(ValueError, match="journal"):
        manager.recover("hello")
    with pytest.raises(ValueError, match="generation"):
        manager._verify_generation(tool, "../foreign")


def test_windows_lock_adapter(tmp_path, monkeypatch):
    calls = []

    def locking(fd, action, length):
        calls.append(action)

    fake = types.SimpleNamespace(locking=locking, LK_NBLCK=1, LK_UNLCK=2)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(storage, "os", types.SimpleNamespace(**{**vars(os), "name": "nt"}))
    with storage.locked(tmp_path):
        pass
    assert calls == [1, 2]

    def busy(*args):
        raise OSError("busy")

    fake.locking = busy
    with pytest.raises(StateConflict):
        with storage.locked(tmp_path):
            pass


def test_windows_launcher_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(launchers, "os", types.SimpleNamespace(**{**vars(os), "name": "nt"}))
    files = launchers.launchers(tmp_path / "tool", tmp_path / "bin", "hello")
    assert (tmp_path / "bin/hello.cmd") in files
    with pytest.raises(ValueError, match="expansion"):
        launchers.launchers(tmp_path / "%bad", tmp_path / "bin", "hello")


def test_uv_commands_are_offline(tmp_path, monkeypatch):
    executable = tmp_path / "uv"
    executable.touch()
    commands = []
    timeouts = []

    def capture(argv, *, timeout):
        commands.append(argv)
        timeouts.append(timeout)

    monkeypatch.setattr(adapters, "run", capture)
    adapter = adapters.UvBackend(executable, timeout=37)
    adapter.stage(tmp_path / "env", (tmp_path / "tool.whl",))
    assert "--offline" in commands[0] and "--no-python-downloads" in commands[0]
    assert "--no-index" in commands[1] and "--no-deps" in commands[1]
    assert "--link-mode=copy" in commands[1]
    assert timeouts == [37, 37, 37]
    assert commands[2][2:4] == ["pip", "check"]


@pytest.mark.skipif(os.name != "nt", reason="native Windows sharing semantics")
def test_native_windows_locked_pointer(tmp_path):
    import ctypes
    from ctypes import wintypes

    manager, plan = fixture(tmp_path)
    manager.install(plan)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    handle = create(str(manager._tool("hello") / "active.json"), 0x80000000, 1, None, 3, 0, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        with pytest.raises(PermissionError):
            manager.install(update(plan))
        assert invoke(manager) == "working"
    finally:
        close(handle)
    manager.recover("hello")
    assert manager._active("hello") == "second"
    assert invoke(manager) == "working"
