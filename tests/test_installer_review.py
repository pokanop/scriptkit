"""PR #8 ownership, preflight, signal and cache-isolation regressions."""

import json
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scriptkit.manager import Installer, UvBackend
from scriptkit.manager import storage
from scriptkit.state import StateConflict
from test_installer import fixture, invoke, update


@pytest.mark.parametrize("legacy_receipt", [False, True])
def test_equivalent_paths_preserve_recorded_ownership(tmp_path, monkeypatch, legacy_receipt):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    original = {p: p.read_bytes() for p in manager._files("hello")}
    if legacy_receipt:
        (manager.root / "hello/launchers.json").unlink()
    python = Path(os.path.abspath(sys.executable))
    spelling = str(python.parent / ".." / python.parent.name / python.name)
    monkeypatch.setattr(sys, "executable", spelling)
    alternate = Installer(
        tmp_path / "unused/../state",
        tmp_path / "unused/../bin",
        manager.source,
        backend=manager.backend,
    )
    assert alternate.root == manager.root
    assert alternate.install(update(plan), dry_run=True) is None
    alternate.install(update(plan))
    assert all(p.read_bytes() == data for p, data in original.items())
    alternate.rollback("hello")
    assert invoke(alternate) == "working"
    # Recovery also accepts the publication ledger after interpreter spelling changes.
    storage.publish(
        alternate.root / "hello/journal.json", {"target": "second", "previous": "first"}
    )
    alternate.recover("hello")
    assert alternate._active("hello") == "second"
    command = next(p for p in original if p.parent == manager.bin_dir)
    command.write_bytes(b"foreign")
    with pytest.raises(ValueError, match="foreign"):
        alternate.uninstall("hello")
    command.write_bytes(original[command])
    alternate.uninstall("hello")
    assert not command.exists()


def test_partial_publication_ledger_survives_interpreter_change(tmp_path, monkeypatch):
    manager, plan = fixture(tmp_path)
    real_link = os.link

    def fail(src, dst):
        if Path(dst).parent == manager.bin_dir:
            raise PermissionError("interrupted publication")
        real_link(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", fail)
        with pytest.raises(PermissionError):
            manager.install(plan)
    python = Path(os.path.abspath(sys.executable))
    monkeypatch.setattr(
        sys, "executable", str(python.parent / ".." / python.parent.name / python.name)
    )
    manager.recover("hello")
    assert invoke(manager) == "working"
    ledger = json.loads((manager.root / "hello/launchers.json").read_text())
    receipt = json.loads((manager.root / "hello/generations/first/receipt.json").read_text())
    assert (
        ledger["launchers"] != receipt["launchers"]
    )  # missing wrapper published with new spelling
    manager.uninstall("hello")


@pytest.mark.parametrize("case", ["stale", "namespace", "generation"])
def test_dry_run_preflight_matches_install_without_fetch(tmp_path, monkeypatch, case):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    proposed = update(plan)
    expected = StateConflict
    if case == "stale":
        proposed = plan
    elif case == "namespace":
        proposed = replace(
            proposed, registry=replace(plan.registry, origin="https://other.example/")
        )
        expected = ValueError
    else:
        (manager.root / "hello/generations/second").mkdir()
        expected = FileExistsError

    def no_fetch(*args):
        pytest.fail("invalid plan fetched artifacts")

    monkeypatch.setattr(manager.source, "fetch", no_fetch)
    before = {p: p.read_bytes() for p in manager.root.rglob("*") if p.is_file()}
    for dry in [True, False]:
        with pytest.raises(expected):
            manager.install(proposed, dry_run=dry)
    assert before == {p: p.read_bytes() for p in manager.root.rglob("*") if p.is_file()}


def test_dry_run_pending_journal_requires_explicit_recovery(tmp_path):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    storage.publish(manager.root / "hello/journal.json", {"target": "first", "previous": None})
    with pytest.raises(StateConflict, match="recover"):
        manager.install(update(plan), dry_run=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX exec/signal semantics")
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_launcher_exec_preserves_pid_and_signal_semantics(tmp_path, sig):
    manager, plan = fixture(
        tmp_path,
        payload="""
import os, sys, time
def main():
    if '--help' in sys.argv: return 0
    try:
        print(os.getpid(), flush=True)
        time.sleep(60)
    except KeyboardInterrupt: return 130
""",
    )
    manager.install(plan)
    process = subprocess.Popen(
        [str(manager.bin_dir / "hello")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        assert int(process.stdout.readline()) == process.pid
        process.send_signal(sig)
        _, err = process.communicate(timeout=10)
        assert process.returncode == (-signal.SIGTERM if sig == signal.SIGTERM else 130)
        assert "Traceback" not in err
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_uv_payload_is_not_hardlinked_to_cache(tmp_path):
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("optional uv not provisioned")
    manager, plan = fixture(tmp_path, wheel=True, backend=UvBackend(Path(uv)))
    manager.install(plan)
    payloads = list((manager.root / "hello/generations/first/env").rglob("hello.py"))
    assert payloads
    assert all(p.stat().st_nlink == 1 for p in payloads)


def test_missing_pointer_and_unknown_uninstall_are_quiet(tmp_path):
    manager, plan = fixture(tmp_path)
    manager.uninstall("typo")
    assert not manager.root.exists()
    manager.install(plan)
    (manager.root / "hello/active.json").unlink()
    result = subprocess.run(
        [sys.executable, str(manager.root / "hello/launch.py")], capture_output=True, text=True
    )
    assert "tool is not installed" in result.stderr
    assert "Traceback" not in result.stderr
