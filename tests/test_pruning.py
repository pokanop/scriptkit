from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scriptkit.manager import storage
from scriptkit.state import StateConflict
from test_installer import fixture, update, invoke


def generations(tmp_path):
    manager, plan = fixture(tmp_path)
    manager.install(plan)
    manager.install(update(plan))
    manager.install(
        replace(
            plan,
            installation=replace(
                plan.installation, generation="third", previous_generation="second"
            ),
        )
    )
    return manager


def test_prune_matches_preview_and_preserves_rollback(tmp_path):
    manager = generations(tmp_path)
    preview = manager.prune(dry_run=True)
    assert preview["paths"]
    assert preview["bytes"] == sum(Path(p).lstat().st_size for p in preview["paths"])
    assert manager.prune() == preview
    assert all(not Path(p).exists() for p in preview["paths"])
    manager.rollback("hello")
    assert manager.current_generation("hello") == "second"
    assert invoke(manager) == "working"
    assert not (manager.root / "hello/prune.json").exists()


def test_foreign_and_uninstalled(tmp_path):
    manager = generations(tmp_path)
    foreign = manager.root / "hello/generations/first/foreign"
    foreign.write_text("not owned")
    root_file = manager.root / "private.json"
    root_file.write_text("private")
    preview = manager.prune()
    assert str(foreign) in preview["foreign"]
    assert str(root_file) in preview["foreign"]
    assert foreign.read_text() == "not owned"
    manager.uninstall("hello")
    assert manager.prune()["paths"] == []
    manager.prune(uninstalled=True)
    assert foreign.exists() and root_file.exists()
    assert not (manager.root / "hello/generations/second").exists()


@pytest.mark.parametrize("phase", ["prune-journal", "prune-detach", "prune-file", "prune-cleanup"])
@pytest.mark.parametrize("hard_exit", [False, True])
def test_interruption(tmp_path, phase, hard_exit):
    manager = generations(tmp_path)
    if hard_exit:
        code = """
import os, sys
from pathlib import Path
from scriptkit.manager import Installer
m = Installer(Path(sys.argv[1]), Path(sys.argv[2]), None,
              checkpoint=lambda p: os._exit(71) if p == sys.argv[3] else None)
m.prune()
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(manager.root), str(manager.bin_dir), phase]
        )
        assert result.returncode == 71
    else:

        def fail(p):
            if p == phase:
                raise RuntimeError("interrupted")

        manager.checkpoint = fail
        with pytest.raises(RuntimeError):
            manager.prune()
        manager.checkpoint = lambda p: None
    assert manager.current_generation("hello") == "third"
    manager._verify_generation(manager._tool("hello"), "third")
    manager._verify_generation(manager._tool("hello"), "second")
    manager.recover("hello")
    assert not (manager.root / "hello/prune.json").exists()
    manager.rollback("hello")
    assert invoke(manager) == "working"


def test_cli(tmp_path, monkeypatch, capsys):
    from scriptkit.entrypoint import main
    from scriptkit import manager_cli

    manager = generations(tmp_path)
    monkeypatch.setattr(manager_cli, "Installer", lambda *a, **kw: manager)
    args = ["--root", str(tmp_path)]
    assert main([*args, "--json", "prune", "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)["data"]
    assert preview["paths"]
    assert main([*args, "prune"]) == 0
    assert "Reclaimed" in capsys.readouterr().out
    assert main([*args, "--json", "prune"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["bytes"] == 0


def test_keep_after_shortened_lineage(tmp_path):
    manager = generations(tmp_path)
    manager.prune()
    manager.rollback("hello")
    result = manager.prune(keep=2)
    assert result["paths"]  # The now-unreferenced third generation is reclaimed.
    assert manager.current_generation("hello") == "second"
    assert invoke(manager) == "working"
    assert manager.prune(keep=2)["paths"] == []
    manager.uninstall("hello")
    assert manager.prune(keep=2, uninstalled=True)["paths"]


@pytest.mark.parametrize("machine", [False, True])
@pytest.mark.parametrize("pending", ["journal.json", "prune.json"])
def test_doctor_pending_operation(tmp_path, monkeypatch, capsys, machine, pending):
    from scriptkit.entrypoint import main
    from scriptkit import manager_cli

    manager = generations(tmp_path)
    monkeypatch.setattr(manager_cli, "Installer", lambda *a, **kw: manager)
    storage.publish(manager._tool("hello") / pending, {"target": "third"})
    args = ["--root", str(tmp_path), *(["--json"] if machine else []), "doctor"]
    assert main(args) == 0
    output = capsys.readouterr().out
    if machine:
        health = json.loads(output)["data"]
        assert health["python"] and health["root"] == str(tmp_path)
        assert health["reclamation"]["bytes"] is None
        assert "recover hello" in health["reclamation"]["error"]
    else:
        assert "Python:" in output and "estimate unavailable" in output
        assert "recover hello" in output


def test_doctor_estimate_under_lock_without_hashing(tmp_path, monkeypatch, capsys):
    from scriptkit.entrypoint import main
    from scriptkit import manager_cli
    from scriptkit.manager import pruning

    manager = generations(tmp_path)
    expected = manager.prune(dry_run=True)["bytes"]
    monkeypatch.setattr(manager_cli, "Installer", lambda *a, **kw: manager)

    def no_hash(path):
        pytest.fail(f"doctor must not hash {path}")

    monkeypatch.setattr(pruning, "_value", no_hash)
    with storage.locked(manager.root):
        assert main(["--root", str(tmp_path), "--json", "doctor"]) == 0
    health = json.loads(capsys.readouterr().out)["data"]
    assert health["reclamation"] == {"bytes": expected, "estimated": True}


def test_unknown_state_and_pending(tmp_path):
    manager = generations(tmp_path)
    tool = manager._tool("hello")
    (tool / "unknown").write_text("foreign")
    (tool / "generations/failed").mkdir()
    (tool / "generations/unknown").write_text("foreign")
    (manager.root / "other").mkdir()
    report = manager.prune(dry_run=True)
    assert {str(tool / p) for p in ("unknown", "generations/failed", "generations/unknown")} <= set(
        report["foreign"]
    )
    storage.publish(tool / "journal.json", {"target": "third", "previous": "second"})
    with pytest.raises(StateConflict, match="pending"):
        manager.prune()
    (tool / "journal.json").unlink()
    receipt_path = tool / "generations/third/receipt.json"
    receipt = storage.read(receipt_path)
    receipt["resolved"]["installation"]["previous_generation"] = "../outside"
    storage.publish(receipt_path, receipt)
    with pytest.raises(ValueError, match="previous"):
        manager.prune()
    receipt_path.unlink()
    with pytest.raises(StateConflict, match="missing protected"):
        manager.prune()


def test_resume_refuses_foreign_changes(tmp_path):
    manager = generations(tmp_path)

    def fail(phase):
        if phase == "prune-detach":
            raise RuntimeError("stop")

    manager.checkpoint = fail
    with pytest.raises(RuntimeError):
        manager.prune()
    manager.checkpoint = lambda p: None
    foreign = manager.root / "hello/generations/.pruning-first/foreign"
    foreign.write_text("private")
    with pytest.raises(StateConflict, match="unowned"):
        manager.recover("hello")
    assert foreign.read_text() == "private"
    foreign.unlink()
    manager.recover("hello")


@pytest.mark.skipif(os.name == "nt", reason="symlink privilege")
def test_owned_links_and_foreign_links(tmp_path):
    manager = generations(tmp_path)
    first = manager.root / "hello/generations/first"
    external = tmp_path / "external"
    external.mkdir()
    (external / "private").write_text("private")
    (first / "owned-link").symlink_to(external, target_is_directory=True)
    receipt = storage.read(first / "receipt.json")
    receipt["files"]["owned-link"] = "link:" + str(external)
    storage.publish(first / "receipt.json", receipt)
    (manager.root / "alias").symlink_to(external, target_is_directory=True)
    (manager.root / "hello/generations/alias").symlink_to(external, target_is_directory=True)
    manager.prune()
    assert (external / "private").read_text() == "private"


def test_lock_and_retention(tmp_path):
    manager = generations(tmp_path)
    assert not manager.prune(keep=2)["paths"]
    with pytest.raises(ValueError):
        manager.prune(keep=0)
    with storage.locked(manager.root):
        with pytest.raises(StateConflict):
            manager.prune()


def test_locked_file_is_resumable(tmp_path, monkeypatch):
    manager = generations(tmp_path)
    unlink = Path.unlink

    def deny(path, *args, **kwargs):
        if ".pruning-first" in path.parts:
            raise PermissionError("locked file")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", deny)
        with pytest.raises(PermissionError):
            manager.prune()
    manager.recover("hello")
    manager.rollback("hello")
    assert invoke(manager) == "working"


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing violation")
def test_windows_open_file(tmp_path):
    import ctypes

    manager = generations(tmp_path)
    path = manager.root / "hello/generations/first/run.py"
    create = ctypes.windll.kernel32.CreateFileW
    create.restype = ctypes.c_void_p
    handle = create(str(path), 0x80000000, 1, None, 3, 0, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        with pytest.raises(OSError):
            manager.prune()
    finally:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
    manager.recover("hello")
    manager.rollback("hello")
    assert invoke(manager) == "working"
