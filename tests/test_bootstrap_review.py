"""Round-1 regressions: shell quoting, ownership repair and canonical roots."""

import errno
import hashlib
import json
import os
from pathlib import Path

import pytest

from scriptkit import bootstrap as b
from scriptkit.entrypoint import main
from test_bootstrap import manager


@pytest.mark.parametrize("phase", [None, "launcher-ledger", "launcher-file"])
def test_moved_python_owned_repair(tmp_path, monkeypatch, phase):
    service, calls, digest = manager(tmp_path)
    first = service.install("https://example.org/wheel", digest, "1.3.0")
    before = b.launcher_files(service.root)
    monkeypatch.setattr(b.sys, "_base_executable", str(tmp_path / "new python"))
    if phase:

        def interrupt(current):
            if current == phase:
                raise KeyboardInterrupt

        service.checkpoint = interrupt
        with pytest.raises(KeyboardInterrupt):
            service.install("https://example.org/wheel", digest, "1.3.0")
        assert b.read(tmp_path / "manager-active.json")["target"] == first["generation"]
        service.checkpoint = lambda _: None
    service.install("https://example.org/wheel", digest, "1.3.0")
    after = b.launcher_files(service.root)
    assert before != after
    assert all(path.read_bytes() == content for path, content in after.items())
    # The receipt is proof of ownership, not permission to clobber edited files.
    command = next(path for path in after if path.parent.name == "bin")
    command.write_bytes(b"foreign edit")
    with pytest.raises(ValueError, match="inspect it and move it aside"):
        service.install("https://example.org/wheel", digest, "1.3.0")
    assert command.read_bytes() == b"foreign edit"


def test_legacy_no_ledger_moved_python_gives_repair_instructions(tmp_path, monkeypatch):
    service, _, digest = manager(tmp_path)
    service.install("https://example.org/wheel", digest, "1.3.0")
    (tmp_path / "manager-launchers.json").unlink()
    monkeypatch.setattr(b.sys, "_base_executable", str(tmp_path / "new python"))
    with pytest.raises(ValueError, match="obsolete ScriptKit launcher, then rerun"):
        service.install("https://example.org/wheel", digest, "1.3.0")


def test_failed_owned_replace_keeps_old_bytes(tmp_path, monkeypatch):
    path = tmp_path / "launcher"
    path.write_bytes(b"old")

    def fail(*args):
        raise PermissionError("locked Windows launcher")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(PermissionError):
        b.exclusive(path, b"new", [hashlib.sha256(b"old").hexdigest()])
    assert path.read_bytes() == b"old"


@pytest.mark.skipif(os.name == "nt", reason="symlink privileges not universal on Windows")
def test_alias_home_and_cli_root(tmp_path, capsys):
    home = tmp_path / "real-home"
    home.mkdir()
    alias = tmp_path / "home"
    alias.symlink_to(home, target_is_directory=True)
    service, _, digest = manager(alias / ".scriptkit")
    service.install("https://example.org/wheel", digest, "1.3.0")
    assert service.root == home / ".scriptkit"
    assert main(["--root", str(alias / ".scriptkit"), "--json", "doctor"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["root"] == str(service.root)
    # Trust the resolved entry path, never redirected state inside it.
    (service.root / "manager-launchers.json").unlink()
    (service.root / "manager-launchers.json").symlink_to(tmp_path / "foreign")
    with pytest.raises(ValueError, match="symlink manager state"):
        service.install("https://example.org/wheel", digest, "1.3.0")


def test_shared_cmd_template():
    source = b.cmd_launcher("python.exe", Path("loader.py"), utf8=True)
    assert "tokens=2 delims=:." in source
    assert "-I -X utf8" in source
    assert "-X utf8" not in b.cmd_launcher("python.exe", Path("loader.py"))
    for path in ("bad%path", "bad!path", 'bad"path', "bad\npath"):
        with pytest.raises(ValueError, match="cmd expansion"):
            b.cmd_launcher(path, Path("loader.py"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX lock injection")
def test_lock_other_error_is_not_misreported(tmp_path, monkeypatch):
    import fcntl

    def fail(*args):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(fcntl, "flock", fail)
    with pytest.raises(OSError, match="disk full"):
        with b.locked(tmp_path):
            pass


def test_powershell_embedded_python_has_no_double_quotes():
    source = Path(__file__).resolve().parents[1] / "install.ps1"
    code = source.read_text().split("$code = @'\n", 1)[1].split("\n'@", 1)[0]
    assert '"' not in code  # Windows PowerShell 5.1's legacy native argv binder
    compile(code, "install.ps1 embedded Python", "exec")
