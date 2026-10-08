"""Offline authoring, migration, and installed-project acceptance tests."""

import dataclasses
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import venv

import pytest
from test_bootstrap import built_wheel as built_wheel
from test_bootstrap_shells import release_server as release_server

from scriptkit.contracts import ToolSpec, resource_text
from scriptkit.contracts.models import CatalogRelease, CommandSpec
from scriptkit.entrypoint import main
from scriptkit.generator import apply, preview, render
from scriptkit.generator.render import canonical, sha256
from scriptkit.generator.scaffolds import collection, existing, installer, tool
from scriptkit.scaffold_runtime import tool_main


@pytest.fixture
def spec():
    value = json.loads(resource_text("ToolSpec.example.json"))
    value["entrypoint"] = "demo.cli:main"
    return ToolSpec.from_dict(value)


def invoke(root, *args):
    return main(["--json", *args, str(root)])


@pytest.mark.parametrize("layout", ["standalone", "repository"])
def test_golden(tmp_path, spec, layout, monkeypatch):
    import socket

    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("network"))
    result = tool(spec, layout)
    inventory = {p: sha256(b) for p, b in (result.generated | result.user).items()}
    golden = Path(__file__).with_name("fixtures") / "scaffolds.json"
    assert sha256(canonical(inventory)) == json.loads(golden.read_text())[layout]
    apply(tmp_path, preview(tmp_path, result))
    assert not preview(tmp_path, result).drift
    assert existing(tmp_path) == (
        ToolSpec.from_json(result.generated["tool.json"].decode()),
        layout,
    )
    assert tomllib.loads(result.generated["pyproject.toml"].decode())["project"]["dependencies"]


def test_cli_upgrade_add_preserves(tmp_path, spec, capsys):
    apply(tmp_path, preview(tmp_path, render(spec)))
    handler = tmp_path / "src/demo/_handlers.py"
    handler.write_text("def run(command, arguments):\n    return 7\n")
    original = handler.read_bytes()
    assert invoke(tmp_path, "template-upgrade", "--check") == 1
    assert invoke(tmp_path, "template-upgrade", "--apply") == 0
    command = tmp_path / "command.json"
    command.write_text(CommandSpec(1, "extra", "Additional behavior", ()).canonical_json())
    assert invoke(tmp_path, "add-command", "--spec", str(command), "--check") == 1
    assert invoke(tmp_path, "add-command", "--spec", str(command), "--apply") == 0
    assert handler.read_bytes() == original
    assert invoke(tmp_path, "template-upgrade", "--check") == 0
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    for mode in ("--check", "--apply"):
        capsys.readouterr()
        assert invoke(tmp_path, "add-command", "--spec", str(command), mode) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["data"]["changes"] == [] and result["ok"]
        assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    command.write_text(CommandSpec(1, "extra", "Different behavior", ()).canonical_json())
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert invoke(tmp_path, "add-command", "--spec", str(command), "--apply") == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"] == "command 'extra' already exists with a different definition"
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    launcher = tmp_path / "src/demo/cli.py"
    launcher.write_text("# handwritten\n")
    assert invoke(tmp_path, "template-upgrade", "--apply") == 1
    assert launcher.read_text() == "# handwritten\n"
    assert invoke(tmp_path, "template-upgrade", "--recover") == 0
    capsys.readouterr()


def test_new_tool_cli(tmp_path, spec, capsys):
    source = tmp_path / "input.json"
    source.write_text(spec.canonical_json())
    assert invoke(tmp_path, "new-tool", "--spec", str(source)) == 0
    assert not (tmp_path / "tool.json").exists()
    assert invoke(tmp_path, "new-tool", "--spec", str(source), "--apply") == 0
    capsys.readouterr()


@pytest.mark.parametrize(
    "field,value",
    [("layout", "wrong"), ("command", "doctor"), ("function", "tool_main"), ("argument", "json")],
)
def test_reserved(spec, field, value):
    if field == "command":
        spec = dataclasses.replace(spec, commands=(CommandSpec(1, value, "", ()),))
    elif field == "function":
        spec = dataclasses.replace(spec, entrypoint=f"demo.cli:{value}")
    elif field == "argument":
        from scriptkit.contracts.models import ArgumentSpec

        spec = dataclasses.replace(
            spec,
            commands=(
                CommandSpec(
                    1, "run", "", (ArgumentSpec(1, value, "flag", "boolean", False, False, (), ""),)
                ),
            ),
        )
    with pytest.raises(ValueError):
        tool(spec, value if field == "layout" else "standalone")


def test_runtime(spec, tmp_path, capsys):
    calls = []

    def handler(command, arguments):
        calls.append((command, arguments))
        return {"result": "ok"}

    value = spec.to_dict()
    for args in (
        [],
        ["--help"],
        ["--version"],
        ["doctor"],
        ["config"],
        ["--json", "--help"],
        ["--json", "doctor"],
    ):
        assert tool_main(value, handler, args) == 0
    assert not calls
    config = tmp_path / "config.json"
    config.write_text("[]")
    assert tool_main(value, handler, ["--config", str(config), "doctor"]) == 1
    assert tool_main(value, handler, ["--json", "unknown"]) == 2
    value["commands"] = [CommandSpec(1, "run", "", ()).to_dict()]
    assert tool_main(value, handler, ["run"]) == 0
    assert calls == [("run", {"config": {}})]
    assert tool_main(value, lambda *a: 7, ["run"]) == 7

    def placeholder(*args):
        raise NotImplementedError()

    assert tool_main(value, placeholder, ["--json", "run"]) == 1
    assert "not implemented" in capsys.readouterr().out

    def interrupted(*args):
        raise KeyboardInterrupt()

    assert tool_main(value, interrupted, ["run"]) == 130


def test_collection_and_installer(tmp_path, capsys):
    catalog = CatalogRelease.from_json(resource_text("CatalogRelease.example.json"))
    bootstrap = tmp_path / "bootstrap.py"
    # Delegation probe: exact argv, child failure propagation, and no tool installation.
    bootstrap.write_text("import sys\nprint(repr(sys.argv[1:]))\nraise SystemExit(19)\n")
    digest = hashlib.sha256(bootstrap.read_bytes()).hexdigest()
    pins = installer(digest, "https://example.org/manager.whl", "a" * 64, "1.4.0")
    result = collection(catalog, pins, "https://example.org/tools/", 2000000000)
    project = tmp_path / "collection"
    project.mkdir()
    apply(project, preview(project, result))
    assert CatalogRelease.from_json((project / "catalog.json").read_text()) == catalog
    command = [
        sys.executable,
        str(project / "install.py"),
        "--bootstrap",
        str(bootstrap),
        "--root",
        str(tmp_path / "manager"),
    ]
    run = subprocess.run(command, capture_output=True, text=True)
    assert run.returncode == 19
    import ast

    delegated = ast.literal_eval(run.stdout)
    assert delegated == [
        "--root",
        str(tmp_path / "manager"),
        "--wheel",
        "https://example.org/manager.whl",
        "--sha256",
        "a" * 64,
        "--version",
        "1.4.0",
    ]
    bootstrap.write_text("raise AssertionError('must not execute')")
    assert subprocess.run(command, capture_output=True).returncode == 2
    source = tmp_path / "catalog.json"
    source.write_text(catalog.canonical_json())
    flags = [
        "--bootstrap-sha256",
        digest,
        "--wheel",
        "https://example.org/manager.whl",
        "--wheel-sha256",
        "a" * 64,
        "--manager-version",
        "1.4.0",
        "--apply",
    ]
    other = tmp_path / "other"
    other.mkdir()
    assert (
        invoke(
            other,
            "new-collection",
            "--catalog",
            str(source),
            "--origin",
            "https://example.org/tools/",
            "--expires-at",
            "2000000000",
            *flags,
        )
        == 0
    )
    assert invoke(other, "generate-installer", *flags) == 1
    standalone = tmp_path / "installer"
    standalone.mkdir()
    assert invoke(standalone, "generate-installer", *flags) == 0
    with pytest.raises(ValueError):
        installer(digest, "\n", "a" * 64, "1.4.0")
    with pytest.raises(ValueError):
        existing(standalone)
    capsys.readouterr()


def test_argument_mapping(spec):
    from scriptkit.contracts.models import ArgumentSpec

    args = (
        ArgumentSpec(1, "target", "positional", "string", False, "default", (), ""),
        ArgumentSpec(1, "count", "option", "integer", False, 3, (), ""),
        ArgumentSpec(1, "format", "option", "string", False, "text", ("text", "json"), ""),
        ArgumentSpec(1, "dry-run", "flag", "boolean", False, False, (), ""),
    )
    spec = dataclasses.replace(spec, commands=(CommandSpec(1, "run", "", args),))
    seen = []
    assert (
        tool_main(spec.to_dict(), lambda c, a: seen.append(a), ["run", "--dry-run", "--count", "9"])
        == 0
    )
    assert seen == [
        {"target": "default", "count": 9, "format": "text", "dry-run": True, "config": {}}
    ]


@pytest.fixture(scope="module")
def runtime_wheel(tmp_path_factory):
    if "SCRIPTKIT_TEST_WHEEL" in os.environ:
        return Path(os.environ["SCRIPTKIT_TEST_WHEEL"])
    import scriptkit

    root = Path(scriptkit.__file__).resolve().parents[2]
    destination = tmp_path_factory.mktemp("scaffold-wheel")
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(destination), str(root)],
        check=True,
        capture_output=True,
    )
    return next(destination.glob("*.whl"))


def test_generated_installer_installs_only_manager(tmp_path, built_wheel, release_server):
    url, environment, _, bootstrap_path = release_server
    pins = installer(
        sha256(bootstrap_path.read_bytes()),
        url + built_wheel.name,
        sha256(built_wheel.read_bytes()),
        "1.3.0",
    )
    for name, content in pins.items():
        (tmp_path / name).write_bytes(content)
    root = tmp_path / "manager root"
    subprocess.run(
        [
            sys.executable,
            str(tmp_path / "install.py"),
            "--bootstrap",
            str(bootstrap_path),
            "--root",
            str(root),
        ],
        env=environment,
        check=True,
        capture_output=True,
    )
    assert (root / "manager-active.json").is_file()
    assert not (root / "tools").exists()
    launcher = root / "bin" / ("scriptkit.cmd" if os.name == "nt" else "scriptkit")
    subprocess.run([str(launcher), "--json", "doctor"], check=True, capture_output=True)


@pytest.mark.parametrize("layout", ["standalone", "repository"])
def test_installed_project(tmp_path, spec, layout, runtime_wheel):
    root = tmp_path / "project"
    root.mkdir()
    apply(root, preview(root, tool(spec, layout)))
    handler = root / "src/demo/_handlers.py"
    handler.write_text(
        "def run(command, arguments):\n    return {'handled': command, 'arguments': arguments}\n"
    )
    dist = tmp_path / "dist"
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist), str(root)],
        check=True,
        capture_output=True,
    )
    env = tmp_path / "environment"
    venv.EnvBuilder(with_pip=True).create(env)
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            str(runtime_wheel),
            str(next(dist.glob("*.whl"))),
        ],
        check=True,
        capture_output=True,
    )
    command = [str(python), "-m", "demo.cli"]
    for arg in ("--help", "--version", "doctor"):
        subprocess.run([*command, arg], cwd=tmp_path, check=True, capture_output=True)
    executable = env / (f"Scripts/{spec.name}.exe" if os.name == "nt" else f"bin/{spec.name}")
    subprocess.run([str(executable), "--help"], cwd=tmp_path, check=True, capture_output=True)
    if layout == "repository":
        subprocess.run(
            [str(python), str(root / "bin" / spec.name), "doctor"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
    handled = subprocess.run(
        [*command, "--json", "inspect", "sample"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(handled.stdout)["data"] == {
        "handled": "inspect",
        "arguments": {"target": "sample", "config": {}},
    }
    # Installed requirements resolved entirely from local wheels, no AI extras or keys.
    subprocess.run([str(python), "-m", "pip", "check"], check=True, capture_output=True)
