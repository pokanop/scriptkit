"""QA regressions: human summaries and dependency-free launcher failure handling."""

from dataclasses import replace
import json
import os
import shutil
import subprocess
import sys

import pytest

from scriptkit import bootstrap as b, entrypoint, manager_cli
from scriptkit.output import OutputContext
from test_bootstrap import manager, wheel_bytes
from test_installer import FastBackend, fixture


@pytest.mark.parametrize("rich", [False, True])
def test_human_commands(tmp_path, monkeypatch, capsys, rich):
    fixture(tmp_path)
    monkeypatch.setattr(
        entrypoint, "OutputContext", lambda policy: OutputContext(replace(policy, rich=rich))
    )
    installer = manager_cli.Installer
    monkeypatch.setattr(
        manager_cli, "Installer", lambda *a, **kw: installer(*a, **kw, backend=FastBackend())
    )

    def cli(*args):
        assert entrypoint.main(["--root", str(tmp_path), *args]) == 0
        output = capsys.readouterr().out
        assert "{'" not in output and "('" not in output
        assert all(token not in output for token in (": True", ": False", ": None"))
        return output

    report = cli("doctor")
    assert "Python:" in report and "venv available: yes" in report
    assert "Manager generation: not installed" in report
    assert "Previous generation: none" in report
    assert "Commands on PATH: no" in report
    assert str(tmp_path) in report
    assert cli("registry", "list") == "local: https://fixture.example/\n"
    assert cli("catalog", "local", "--offline") == "local/hello@1.0.0\n"
    assert (
        cli("install", "local/hello@1.0.0", "--offline", "--dry-run")
        == "Dry run: would install local/hello@1.0.0; no changes made.\n"
    )
    assert cli("install", "local/hello@1.0.0", "--offline") == "Installed local/hello@1.0.0.\n"
    assert cli("update", "local/hello@1.0.0", "--offline") == "Updated local/hello@1.0.0.\n"
    assert cli("rollback", "hello") == "Rolled back hello to the previous generation.\n"
    assert cli("recover", "hello") == "Recovery complete for hello.\n"
    assert cli("uninstall", "hello") == "Uninstalled hello; user data and config preserved.\n"
    monkeypatch.setattr(manager_cli.Resolver, "list", lambda *a, **kw: ())
    assert cli("catalog", "local") == "No tools in this catalog.\n"
    assert cli("registry", "remove", "local") == "No registries registered.\n"
    monkeypatch.setattr(manager_cli.Manager, "install", lambda *a: {"version": "1.4.0"})
    monkeypatch.setattr(manager_cli.Manager, "rollback", lambda *a: {"version": "1.3.0"})
    assert (
        cli("self-update", "--wheel", "url", "--sha256", "hash", "--version", "1.4.0")
        == "Updated manager to 1.4.0.\n"
    )
    assert cli("self-rollback") == "Rolled back manager to 1.3.0.\n"


@pytest.mark.parametrize("machine", [False, True])
@pytest.mark.parametrize(
    "failure",
    ["generation", "pointer-file", "invalid-json", "invalid-pointer", "invalid-shape", "execution"],
)
def test_standalone_launcher_errors(tmp_path, machine, failure):
    if failure == "execution" and os.name == "nt":
        pytest.skip("POSIX executable permission failure")
    service, _, digest = manager(tmp_path)
    receipt = service.install("https://example.org/wheel", digest, "1.3.0")
    # Fake staging intentionally has no interpreter: exercise the actual generated
    # launcher without importing ScriptKit or requiring an installed runtime.
    pointer = tmp_path / "manager-active.json"
    expected = "is missing or incomplete"
    if failure == "generation":
        shutil.rmtree(tmp_path / "manager-generations" / receipt["generation"])
    elif failure == "pointer-file":
        pointer.unlink()
        expected = "cannot read manager pointer"
    elif failure == "invalid-json":
        pointer.write_text("{")
        expected = "cannot read manager pointer"
    elif failure in ("invalid-pointer", "invalid-shape"):
        pointer.write_text(json.dumps({"target": None} if failure == "invalid-pointer" else []))
        expected = "invalid manager pointer"
    elif failure == "execution":
        python = b.python_at(tmp_path / "manager-generations" / receipt["generation"])
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("not executable")
        python.chmod(0o600)
        expected = "cannot start"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-X",
            "utf8",
            str(tmp_path / "manager-launch.py"),
            *(["--json"] if machine else []),
            "doctor",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 1
    assert len(result.stderr.splitlines()) == 1
    assert expected in result.stderr
    assert "rerun the pinned bootstrap" in result.stderr
    assert "--rollback" in result.stderr and str(tmp_path) in result.stderr
    assert "Traceback" not in result.stderr
    if machine:
        payload = json.loads(result.stdout)
        assert payload == {
            "schema_version": 1,
            "ok": False,
            "data": None,
            "error": result.stderr.removeprefix("error: ").strip(),
        }
    else:
        assert result.stdout == ""


def test_version_mismatch_message(tmp_path):
    import hashlib

    raw = wheel_bytes()
    with pytest.raises(ValueError, match="identity/version does not match requested 1.4.0"):
        b.Manager(tmp_path, download=lambda _: raw).install(
            "https://example.org/wheel", hashlib.sha256(raw).hexdigest(), "1.4.0"
        )
