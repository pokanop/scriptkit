"""Regression coverage for the first scaffold review."""

import json
from dataclasses import replace
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

from scriptkit.contracts import ToolSpec, resource_text
from scriptkit.entrypoint import main
from scriptkit.generator import apply, preview
from scriptkit.generator.render import sha256
from scriptkit.generator.scaffolds import tool, installer
from scriptkit.scaffold_runtime import tool_main


def test_preview_presentation_and_structured_failures(tmp_path, capsys):
    spec = ToolSpec.from_json(resource_text("ToolSpec.example.json"))
    spec = replace(spec, entrypoint="demo.cli:main")
    source = tmp_path / "input.json"
    source.write_text(spec.canonical_json())
    args = ["new-tool", str(tmp_path), "--spec", str(source)]
    assert main(args) == 0
    text = capsys.readouterr().out
    assert "file change(s)" in text and "--- current/" in text
    assert "\n+++ proposed/" in text and "\\n" not in text
    assert main(["--json", *args, "--check"]) == 1
    response = json.loads(capsys.readouterr().out)
    assert response["error"] == "generation drift"
    assert response["data"]["drift"] and response["data"]["changes"]
    apply(tmp_path, preview(tmp_path, tool(spec)))
    (tmp_path / "requirements.txt").write_text("foreign edit\n")
    assert main(["--json", *args, "--apply"]) == 1
    response = json.loads(capsys.readouterr().out)
    conflicts = [c for c in response["data"]["changes"] if c["conflict"]]
    assert response["error"] == "generation conflicts"
    assert conflicts[0]["path"] == "requirements.txt"
    assert conflicts[0]["reason"] == "modified"
    assert "foreign edit" in conflicts[0]["diff"]
    assert main(args) == 1
    assert "CONFLICT (modified): requirements.txt" in capsys.readouterr().out
    assert (tmp_path / "requirements.txt").read_text() == "foreign edit\n"


@pytest.mark.parametrize("status", [0, 19])
def test_installer_executes_verified_snapshot(tmp_path, monkeypatch, status):
    original = tmp_path / "bootstrap.py"
    verified = f"raise SystemExit({status})\n".encode()
    original.write_bytes(verified)
    for name, content in installer(
        sha256(verified), "https://example.org/manager.whl", "a" * 64, "1.4.0"
    ).items():
        (tmp_path / name).write_bytes(content)
    copies = []

    def execute(argv):
        original.write_text("raise AssertionError('unverified replacement')\n")
        copy = Path(argv[2])
        copies.append(copy)
        assert copy != original and copy.read_bytes() == verified
        return subprocess.run(argv, capture_output=True).returncode

    monkeypatch.setattr(subprocess, "call", execute)
    monkeypatch.setattr(
        sys,
        "argv",
        ["install.py", "--bootstrap", str(original), "--root", str(tmp_path / "manager")],
    )
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(tmp_path / "install.py"), run_name="__main__")
    assert exc.value.code == status
    assert len(copies) == 1 and not copies[0].parent.exists()


def test_config_reaches_handler(tmp_path, capsys):
    spec = ToolSpec.from_json(resource_text("ToolSpec.example.json"))
    config = tmp_path / "config.json"
    config.write_text('{"limit": 17}')
    seen = []
    assert (
        tool_main(
            spec.to_dict(),
            lambda c, a: seen.append(a),
            ["--config", str(config), "inspect", "sample"],
        )
        == 0
    )
    assert seen == [{"target": "sample", "config": {"limit": 17}}]
    assert (
        tool_main(
            spec.to_dict(),
            lambda *a: pytest.fail("must not dispatch"),
            ["--config", str(config), "config"],
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"limit": 17}
