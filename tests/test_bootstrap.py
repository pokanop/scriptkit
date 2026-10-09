from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from scriptkit import bootstrap as b


def wheel_bytes(version="1.3.0", name="pokanop-scriptkit", newline="\n"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            f"pokanop_scriptkit-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".replace("\n", newline),
        )
    return stream.getvalue()


def manager(tmp_path, checkpoint=lambda _: None):
    raw = wheel_bytes()
    calls = []
    return (
        b.Manager(tmp_path, execute=calls.append, download=lambda _: raw, checkpoint=checkpoint),
        calls,
        hashlib.sha256(raw).hexdigest(),
    )


def test_generations_rollback_and_rerun(tmp_path):
    service, calls, digest = manager(tmp_path)
    first = service.install("https://example.org/latest.whl", digest, "1.3.0")
    second = service.install("https://example.org/latest.whl", digest, "1.3.0")
    assert first["generation"] != second["generation"]
    assert first["sha256"] == digest
    assert b.read(tmp_path / "manager-active.json")["previous"] == first["generation"]
    assert service.rollback() == first
    assert service.rollback() == second
    assert all("--no-index" in call and "--no-deps" in call for call in calls if "pip" in call)
    assert len(list((tmp_path / "manager-generations").iterdir())) == 2
    doctor = next(call for call in calls if call[-1] == "doctor")
    assert doctor[doctor.index("--root") + 1] == str(tmp_path.resolve())


@pytest.mark.parametrize(
    "phase",
    ["fetch", "stage", "smoke", "launcher-ledger", "launcher-file", "launchers", "activate"],
)
def test_interruption(tmp_path, phase):
    service, calls, digest = manager(tmp_path)
    first = service.install("https://example.org/tool.whl", digest, "1.3.0")

    def interrupt(current):
        if current == phase:
            raise KeyboardInterrupt

    service.checkpoint = interrupt
    with pytest.raises(KeyboardInterrupt):
        service.install("https://example.org/tool.whl", digest, "1.3.0")
    active = b.read(tmp_path / "manager-active.json")
    assert (
        active["target"] == first["generation"]
        if phase != "activate"
        else active["previous"] == first["generation"]
    )
    service.checkpoint = lambda _: None
    service.install("https://example.org/tool.whl", digest, "1.3.0")


@pytest.mark.parametrize("stage", [0, 1, 2, 3, 4])
def test_child_failure_preserves_old(tmp_path, stage):
    service, calls, digest = manager(tmp_path)
    service.install("https://example.org/tool.whl", digest, "1.3.0")
    old = (tmp_path / "manager-active.json").read_bytes()
    count = 0

    def fail(argv):
        nonlocal count
        if count == stage:
            raise subprocess.CalledProcessError(17, argv)
        count += 1

    service.execute = fail
    with pytest.raises(subprocess.CalledProcessError):
        service.install("https://example.org/tool.whl", digest, "1.3.0")
    assert (tmp_path / "manager-active.json").read_bytes() == old


def test_validation_ownership_and_missing_previous(tmp_path):
    service, calls, digest = manager(tmp_path)
    for sha, version in [("bad", "1.3.0"), (digest, "latest"), ("0" * 64, "1.3.0")]:
        with pytest.raises(ValueError):
            service.install("https://example.org/tool.whl", sha, version)
    with pytest.raises(ValueError, match="previous"):
        service.rollback()
    b.publish(tmp_path / "manager-active.json", {"target": "a" * 32, "previous": "b" * 32})
    with pytest.raises(ValueError, match="receipt"):
        service.rollback()
    command = next(p for p in b.launcher_files(tmp_path) if p.parent.name == "bin")
    command.parent.mkdir()
    command.write_text("unrelated")
    with pytest.raises(ValueError, match="unrelated"):
        service.install("https://example.org/tool.whl", digest, "1.3.0")
    assert command.read_text() == "unrelated"


def test_windows_metadata_newlines(tmp_path):
    raw = wheel_bytes(newline="\r\n")
    receipt = b.Manager(tmp_path, execute=lambda _: None, download=lambda _: raw).install(
        "https://example.org/wheel", hashlib.sha256(raw).hexdigest(), "1.3.0"
    )
    assert receipt["version"] == "1.3.0"


def test_wrong_identity(tmp_path):
    raw = wheel_bytes(name="foreign")
    with pytest.raises(ValueError, match="identity"):
        b.Manager(tmp_path, download=lambda _: raw).install(
            "https://example.org/tool.whl", hashlib.sha256(raw).hexdigest(), "1.3.0"
        )


def test_exclusive_and_atomic_errors(tmp_path, monkeypatch):
    path = tmp_path / "env"
    b.exclusive(path, b"safe")
    b.exclusive(path, b"safe")
    with pytest.raises(ValueError, match="unrelated"):
        b.exclusive(path, b"unsafe")
    b.publish(tmp_path / "state", {"old": True})

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        b.publish(tmp_path / "state", {"new": True})
    assert b.read(tmp_path / "state") == {"old": True}
    assert not list(tmp_path.glob(".publish-*"))


@pytest.mark.skipif(os.name == "nt", reason="symlink privilege and flock semantics")
def test_symlinks_and_competition(tmp_path):
    root = tmp_path / "root"
    root.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        with b.locked(root):
            pass
    root.unlink()
    root.mkdir()
    (root / "bin").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        with b.locked(root):
            pass
    (root / "bin").unlink()
    with b.locked(root):
        with pytest.raises(OSError, match="another manager operation"):
            with b.locked(root):
                pass


def test_fetch_policy(monkeypatch):
    for url in ("http://example.org/wheel", "https://user:pass@example.org/wheel"):
        with pytest.raises(ValueError):
            b.fetch(url)

    class Response:
        url = "https://example.org/wheel"
        raw = b"wheel"

        def read(self, limit):
            return self.raw

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    response = Response()
    monkeypatch.setattr(b.urllib.request, "urlopen", lambda *a, **k: response)
    assert b.fetch(response.url) == b"wheel"
    response.url = "http://example.org/wheel"
    with pytest.raises(ValueError, match="redirect"):
        b.fetch("https://example.org/wheel")
    response.url = "https://example.org/wheel"
    response.raw = b"0" * (64 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="size"):
        b.fetch(response.url)


def test_main_errors_and_path_file(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        b.main([])
    assert exc.value.code == 2
    monkeypatch.setattr(b.sys, "version_info", (3, 10))
    with pytest.raises(SystemExit):
        b.main([])
    monkeypatch.setattr(b.sys, "version_info", (3, 13))
    args = [
        "--root",
        str(tmp_path),
        "--wheel",
        "https://example.org/wheel",
        "--sha256",
        "a" * 64,
        "--version",
        "1.3.0",
    ]
    monkeypatch.setattr(b.Manager, "install", lambda *a: {"ok": True})
    assert b.main([*args, "--path-file", str(tmp_path / "path-env")]) == 0
    assert "PATH" in (tmp_path / "path-env").read_text()
    monkeypatch.setattr(b.Manager, "rollback", lambda *a: {"ok": True})
    assert b.main(["--root", str(tmp_path), "--rollback"]) == 0
    for error, expected in [
        (KeyboardInterrupt(), 130),
        (ValueError("bad"), 1),
        (subprocess.CalledProcessError(17, []), 17),
        (subprocess.CalledProcessError(-15, []), 143),
    ]:

        def fail(*args):
            raise error

        monkeypatch.setattr(b.Manager, "install", fail)
        assert b.main(args) == expected


@pytest.fixture(scope="session")
def built_wheel(tmp_path_factory):
    if "SCRIPTKIT_TEST_WHEEL" in os.environ:
        return Path(os.environ["SCRIPTKIT_TEST_WHEEL"])
    directory = tmp_path_factory.mktemp("manager-wheel")
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(directory)],
        check=True,
        capture_output=True,
    )
    return next(directory.glob("*.whl"))


def test_real_manager_install_repair_and_tool(tmp_path, built_wheel):
    from test_installer import fixture

    root = tmp_path / "custom root café"
    root.mkdir()
    fixture(root)  # populate a registered, hash-verified offline catalog/cache
    raw = built_wheel.read_bytes()
    service = b.Manager(root, download=lambda _: raw)
    first = service.install(
        "https://example.org/pinned.whl", hashlib.sha256(raw).hexdigest(), "1.5.0"
    )
    assert not (root / "tools").exists()
    command = root / "bin" / ("scriptkit.cmd" if os.name == "nt" else "scriptkit")

    def cli(*args):
        result = subprocess.run(
            [str(command), "--json", *args], capture_output=True, text=True, encoding="utf-8"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    assert json.loads(cli("doctor").stdout)["data"]["root"] == str(root)
    if os.name == "nt":
        legacy_console = subprocess.run(
            'cmd /d /s /c "chcp 437 >nul & call "%SCRIPTKIT_TEST_COMMAND%" --json doctor & chcp"',
            env={**os.environ, "SCRIPTKIT_TEST_COMMAND": str(command)},
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        lines = legacy_console.stdout.splitlines()
        assert len(lines) == 2, (legacy_console.stdout, legacy_console.stderr)
        assert json.loads(lines[0])["data"]["root"] == str(root), legacy_console.stderr
        assert "437" in lines[-1]
    assert json.loads(cli("catalog", "local", "--offline").stdout)["data"] == ["local/hello@1.0.0"]
    cli("install", "local/hello@1.0.0", "--offline")
    tool = root / "bin" / ("hello.cmd" if os.name == "nt" else "hello")
    assert subprocess.check_output([str(tool)], text=True).strip() == "working"
    cli("update", "local/hello@1.0.0", "--offline")
    cli("rollback", "hello")
    cli("recover", "hello")
    # Corrupt manager code; standalone bootstrap repairs without importing it.
    env = root / "manager-generations" / first["generation"]
    python = b.python_at(env)
    python.unlink()
    for flags in ([], ["--json"]):
        broken = subprocess.run(
            [str(command), *flags, "doctor"], capture_output=True, text=True, encoding="utf-8"
        )
        assert broken.returncode == 1
        assert "Traceback" not in broken.stderr
        assert "rerun the pinned bootstrap" in broken.stderr
        assert first["generation"] in broken.stderr
        if flags:
            assert json.loads(broken.stdout)["ok"] is False
        else:
            assert broken.stdout == ""
    assert subprocess.check_output([str(tool)], text=True).strip() == "working"
    service.install("https://example.org/pinned.whl", hashlib.sha256(raw).hexdigest(), "1.5.0")
    assert json.loads(cli("doctor").stdout)["ok"]
    cli("uninstall", "hello")
    assert not tool.exists()
