import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scriptkit.conformance import validate
from scriptkit.conformance.runtime import check_fixtures
from scriptkit.contracts import ToolSpec, resource_text
from scriptkit.entrypoint import main
from scriptkit.generator import apply, preview
from scriptkit.generator.scaffolds import tool
from scriptkit.generator.plan import MANIFEST


@pytest.fixture
def project(tmp_path):
    value = json.loads(resource_text("ToolSpec.example.json"))
    value["entrypoint"] = "demo.cli:main"
    apply(tmp_path, preview(tmp_path, tool(ToolSpec.from_dict(value))))
    return tmp_path


def codes(root):
    return {d.code for d in validate(root).diagnostics}


def test_valid_extended_no_execution(project):
    handler = project / "src/demo/_handlers.py"
    handler.write_text(
        "raise RuntimeError('must never import')\ndef run(command, values):\n    return {'total': 42}\n"
    )
    (project / "src/demo/business.py").write_text("def total(values):\n    return sum(values)\n")
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert validate(project).valid
    assert before == {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert main(["--json", "validate", str(project)]) == 0


@pytest.mark.parametrize(
    "path,content,expected",
    [
        ("tool.json", "{}", "SKV002"),
        ("tool.json", b"\xff", "SKV002"),
        ("pyproject.toml", "", "SKV003"),
        ("pyproject.toml", "project = 42", "SKV003"),
        ("pyproject.toml", "[project]\ndependencies = 5", "SKV006"),
        ("pyproject.toml", "[project]\nscripts = 5", "SKV005"),
        ("pyproject.toml", "[project]\n[tool.scriptkit.conformance]\nimports = 5", "SKV006"),
        ("src/demo/cli.py", "def other(): pass", "SKV008"),
        ("src/demo/cli.py", "def main(): pass", "SKV011"),
        ("src/demo/cli.py", "import json\nSPEC = json.loads('{}')\ndef main(): pass", "SKV015"),
        ("src/demo/_handlers.py", "def broken(", "SKV007"),
        ("src/demo/_handlers.py", "import undeclared\nfrom another import x\nprint(x)", "SKV009"),
        ("src/demo/_handlers.py", "print(42)", "SKV010"),
        ("config.example.json", "[]", "SKV012"),
        ("config.example.json", "{", "SKV012"),
        (MANIFEST, "{}", "SKV014"),
        ("requirements.txt", "changed", "SKV013"),
    ],
)
def test_broken(project, path, content, expected):
    target = project / path
    target.write_bytes(content if isinstance(content, bytes) else content.encode())
    report = validate(project)
    assert expected in {d.code for d in report.diagnostics}
    assert not report.valid
    assert report.diagnostics == tuple(sorted(report.diagnostics))
    assert all(d.message and d.path for d in report.diagnostics)


def test_spec_drift_and_missing(project):
    value = json.loads((project / "tool.json").read_text())
    value["version"] = "9.0.0"
    (project / "tool.json").write_text(json.dumps(value))
    (project / "requirements.txt").unlink()
    assert {"SKV004", "SKV013", "SKV015"} <= codes(project)


def test_handwritten_without_manifest_aliases(project):
    (project / MANIFEST).unlink()
    metadata = project / "pyproject.toml"
    text = metadata.read_text().replace("dependencies = [", 'dependencies = ["Pillow>=1", ')
    metadata.write_text(text + '\n[tool.scriptkit.conformance.imports]\nPIL = "Pillow"\n')
    (project / "src/demo/_handlers.py").write_text("import PIL\nfrom .business import total\n")
    assert validate(project).valid


def test_unsafe_paths(project):
    handler = project / "src/demo/_handlers.py"
    handler.unlink()
    try:
        handler.symlink_to(project / "tool.json")
    except OSError:
        pytest.skip("symlinks not permitted")
    assert "SKV001" in codes(project)
    (project / "config.example.json").unlink()
    (project / "config.example.json").mkdir()
    assert "SKV001" in codes(project)
    assert "SKV001" in codes(project / "missing")


def test_missing_source(project):
    import shutil

    shutil.rmtree(project / "src")
    assert {"SKV001", "SKV008"} <= codes(project)


def test_cli_permissions_and_output(project, capsys):
    assert main(["--json", "validate", str(project), "--runtime-fixtures"]) == 1
    assert "requires --allow-execution" in capsys.readouterr().out
    assert (
        main(["--json", "validate", str(project), "--runtime-fixtures", "--allow-execution"]) == 0
    )
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["runtime"]["valid"] and data["mode"] == "static"
    assert main(["validate", str(project)]) == 0
    assert "not proof" in capsys.readouterr().out
    (project / "config.example.json").unlink()
    assert main(["validate", str(project)]) == 1
    assert "SKV012" in capsys.readouterr().out


def test_runtime_consent_and_failure():
    with pytest.raises(ValueError, match="allow-execution"):
        check_fixtures(allow_execution=False)

    class Runner:
        paths = []

        def run(self, cmd, **kwargs):
            self.paths.append(Path(cmd[2]))
            return SimpleNamespace(out="not json", err="", code=-1)

    runner = Runner()
    assert not check_fixtures(allow_execution=True, runner=runner)["valid"]
    assert all(not p.exists() for p in runner.paths)

    class Cancel(Runner):
        def run(self, cmd, **kwargs):
            self.paths.append(Path(cmd[2]))
            raise KeyboardInterrupt

    runner = Cancel()
    with pytest.raises(KeyboardInterrupt):
        check_fixtures(allow_execution=True, runner=runner)
    assert all(not p.exists() for p in runner.paths)


@pytest.mark.parametrize("flag,code", [("", 0), ("--json", 0), ("--fail", 1), ("--cancel", 130)])
def test_example_in_process(flag, code, monkeypatch, capsys):
    from importlib.resources import files
    from scriptkit.conformance.resources.example import main as example

    resources = files("scriptkit.conformance.resources")
    assert "contract v1" in resources.joinpath("AGENTS.md").read_text(encoding="utf-8")
    assert "scriptkit-command-writing" in resources.joinpath("SKILL.md").read_text(encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["example.py", flag])
    assert example() == code
    capsys.readouterr()


def test_runtime_human(project, capsys):
    assert main(["validate", str(project), "--runtime-fixtures", "--allow-execution"]) == 0
    assert "Packaged runtime fixtures: passed" in capsys.readouterr().out


def test_packaged_example_human():
    result = subprocess.run(
        [sys.executable, "-m", "scriptkit.conformance.resources.example"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0 and "Count" in result.stdout and "60" in result.stdout
