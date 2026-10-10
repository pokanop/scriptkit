"""Regression tests for runtime sweep POK-638."""

import json
import sys

import pytest

from scriptkit.contracts import ToolSpec, resource_text
from scriptkit.entrypoint import main
from scriptkit.execution import BoundedRunner
from scriptkit.scaffold_runtime import tool_main


@pytest.mark.parametrize("machine", [False, True])
def test_config_error_names_file(tmp_path, capsys, machine):
    config = tmp_path / "bad.json"
    config.write_text("{bad")
    spec = ToolSpec.from_json(resource_text("ToolSpec.example.json"))
    args = (["--json"] if machine else []) + ["--config", str(config), "config"]
    assert tool_main(spec.to_dict(), lambda *a: None, args) == 1
    captured = capsys.readouterr()
    error = json.loads(captured.out)["error"] if machine else captured.err
    assert f"invalid config {config}:" in error


@pytest.mark.parametrize("machine", [False, True])
def test_handler_mapping_rendering(capsys, machine):
    spec = ToolSpec.from_json(resource_text("ToolSpec.example.json"))
    args = (["--json"] if machine else []) + ["inspect", "ok"]
    assert tool_main(spec.to_dict(), lambda *a: {"target": "ok"}, args) == 0
    output = json.loads(capsys.readouterr().out)
    assert (output["data"] if machine else output) == {"target": "ok"}


def test_packaged_example_and_layout_inference(tmp_path, capsys):
    source = tmp_path / "spec.json"
    source.write_text(resource_text("ToolSpec.example.json"))
    project = tmp_path / "project"
    project.mkdir()
    args = ["new-tool", str(project), "--spec", str(source)]
    assert main([*args, "--apply"]) == 0
    assert main([*args, "--check"]) == 0
    assert main([*args, "--layout", "repository", "--apply"]) == 0
    assert (project / "bin" / "example-tool").exists()
    assert main([*args, "--check"]) == 0
    assert main([*args, "--apply"]) == 0
    assert (project / "bin" / "example-tool").exists()
    assert main([*args, "--layout", "standalone", "--apply"]) == 0
    assert not (project / "bin" / "example-tool").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin timeout cleanup stress")
def test_darwin_repeated_timeout_cleanup():
    for _ in range(50):
        result = BoundedRunner().run(
            [sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.02
        )
        assert result.code == -1 and "timed out" in result.err
