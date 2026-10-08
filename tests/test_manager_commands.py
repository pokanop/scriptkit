import json

import pytest

from scriptkit.entrypoint import main
from scriptkit import manager_cli as commands
from test_installer import FastBackend, fixture


def test_public_lifecycle(tmp_path, monkeypatch, capsys):
    installer, plan, *_ = fixture(tmp_path)
    real_installer = commands.Installer
    monkeypatch.setattr(
        commands,
        "Installer",
        lambda *args, **kwargs: real_installer(*args, **kwargs, backend=FastBackend()),
    )

    def cli(*args):
        code = main(["--root", str(tmp_path), "--json", *args])
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert code == 0, payload
        return payload["data"]

    assert cli("doctor")["root"] == str(tmp_path)
    registry = cli("registry", "list")[0]
    cli("registry", "remove", "local")
    registry_file = tmp_path / "registry.json"
    registry_file.write_text(json.dumps(registry))
    cli("registry", "add", str(registry_file), "--trust-origin", registry["origin"])
    assert cli("catalog", "local", "--offline") == ["local/hello@1.0.0"]
    assert cli("install", "local/hello@1.0.0", "--offline", "--dry-run")["receipt"] is None
    cli("install", "local/hello@1.0.0", "--offline")
    cli("update", "local/hello@1.0.0", "--offline")
    cli("rollback", "hello")
    cli("recover", "hello")
    cli("uninstall", "hello")
    for target in ("bad", "local/scriptkit@1.3.0"):
        assert main(["--root", str(tmp_path), "--json", "install", target]) == 1
        assert not json.loads(capsys.readouterr().out)["ok"]


def test_self_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(commands.Manager, "install", lambda *a: {"version": "1.3.0"})
    monkeypatch.setattr(commands.Manager, "rollback", lambda *a: {"version": "1.2.0"})
    assert (
        main(
            [
                "--json",
                "--root",
                str(tmp_path),
                "self-update",
                "--wheel",
                "https://example.org/wheel",
                "--sha256",
                "a" * 64,
                "--version",
                "1.3.0",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["data"]["version"] == "1.3.0"
    assert main(["--json", "self-rollback"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["version"] == "1.2.0"


@pytest.mark.parametrize(
    "args, code", [(["--help"], 0), (["--version"], 0), (["--unknown"], 2), ([], 0)]
)
def test_machine_boundary(args, code, capsys):
    assert main(["--json", *args]) == code
    assert json.loads(capsys.readouterr().out)["ok"] == (code == 0)


def test_default_no_mutation(tmp_path, monkeypatch, capsys):
    root = tmp_path / "absent"
    monkeypatch.setenv("SCRIPTKIT_ROOT", str(root))
    assert main([]) == 0
    assert not root.exists()
    assert main(["doctor"]) == 0
    assert not root.exists()
