"""Failure-path and executable output probes for generator adapters."""

import dataclasses
import json
import os
import subprocess
import sys
import tomllib

import pytest

from scriptkit.contracts import ToolSpec, resource_text
from scriptkit.generator import apply, preview, recover, render
from scriptkit.generator.plan import CONTROL, MANIFEST
from scriptkit.generator.transaction import AtomicWriter, StateConflict


def example():
    value = json.loads(resource_text("ToolSpec.example.json"))
    value["entrypoint"] = "demo.cli:main"
    return ToolSpec.from_dict(value)


def test_unicode_toml_and_all_argument_kinds(tmp_path):
    value = json.loads(example().canonical_json())
    value["description"] = 'Unicode 🚀, quotes ", slash \\, newline\n'

    def argument(name, kind, value_type, default=None, required=False, choices=()):
        return dict(
            schema_version=1,
            name=name,
            kind=kind,
            value_type=value_type,
            default=default,
            required=required,
            choices=list(choices),
            help="test",
        )

    value["commands"] = [
        dict(
            schema_version=1,
            name="run",
            help="run",
            arguments=[
                argument("item", "positional", "string", required=True),
                argument("extra", "positional", "string", default="fallback"),
                argument("count", "option", "integer", default=2),
                argument("mode", "option", "string", choices=("a", "b"), required=True),
                argument("help", "flag", "boolean", default=False),
                argument("enabled", "flag", "boolean", default=True),
                argument("foo-bar", "option", "string", default="dash"),
                argument("foo_bar", "option", "string", default="underscore"),
            ],
        )
    ]
    output = render(ToolSpec.from_dict(value))
    assert (
        tomllib.loads(output.generated["pyproject.toml"].decode())["project"]["description"]
        == value["description"]
    )
    apply(tmp_path, preview(tmp_path, output))
    (tmp_path / "src/demo/_handlers.py").write_text(
        "import json\ndef run(command, arguments):\n    print(json.dumps([command, arguments]))\n    return 0\n"
    )
    env = {**os.environ, "PYTHONPATH": str(tmp_path / "src")}
    result = subprocess.run(
        [sys.executable, "-m", "demo.cli", "run", "thing", "--mode", "a", "--help", "--enabled"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    command, args = json.loads(result.stdout)
    assert command == "run"
    assert args == dict(
        item="thing",
        extra="fallback",
        count=2,
        mode="a",
        help=True,
        enabled=False,
        **{"foo-bar": "dash", "foo_bar": "underscore"},
    )


def test_repeated_interruption_during_recovery(tmp_path):
    class Crash(AtomicWriter):
        def replace(self, path, data, staging):
            super().replace(path, data, staging)
            raise KeyboardInterrupt

    output = render(example())
    with pytest.raises(KeyboardInterrupt):
        apply(tmp_path, preview(tmp_path, output), writer=Crash())
    for _ in range(8):
        with pytest.raises(KeyboardInterrupt):
            recover(tmp_path, writer=Crash())
    assert recover(tmp_path)
    assert not preview(tmp_path, output).drift


def test_failed_replace_does_not_publish(tmp_path, monkeypatch):
    def fail(*args):
        raise PermissionError("injected")

    output = render(example())
    plan = preview(tmp_path, output)
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(PermissionError):
        apply(tmp_path, plan)
    assert not (tmp_path / "tool.json").exists()
    assert not (tmp_path / MANIFEST).exists()


def test_symlink_control_files_refused(tmp_path):
    output = render(example())
    plan = preview(tmp_path, output)
    directory = tmp_path / CONTROL
    directory.mkdir()
    external = tmp_path / "external"
    external.write_text("keep")
    for name in ("lock", "journal.json", "staging"):
        link = directory / name
        try:
            link.symlink_to(external)
        except OSError:
            pytest.skip("symlink privileges unavailable")
        with pytest.raises((ValueError, StateConflict)):
            apply(tmp_path, plan)
        if name == "journal.json":
            with pytest.raises(ValueError, match="symlink"):
                recover(tmp_path)
        assert external.read_text() == "keep"
        link.unlink()


def test_corrupt_manifest_and_journal_are_not_reinterpreted(tmp_path):
    output = render(example())
    apply(tmp_path, preview(tmp_path, output))
    file = tmp_path / MANIFEST
    original = file.read_bytes()
    for data in [b"{", original.replace(b'"schema_version":1', b'"schema_version":2')]:
        file.write_bytes(data)
        with pytest.raises(ValueError):
            preview(tmp_path, output)
    file.write_bytes(original)
    journal = tmp_path / CONTROL / "journal.json"
    journal.write_text(
        '{"schema_version":1,"operations":[{"path":"../escape","before":null,"after":null}]}'
    )
    with pytest.raises(ValueError):
        recover(tmp_path)
    assert journal.exists()


def test_concurrent_change_after_preflight_retains_journal(tmp_path):
    output = render(example())

    class Race(AtomicWriter):
        def replace(self, path, data, staging):
            super().replace(path, data, staging)
            if path.name == "pyproject.toml":
                (tmp_path / "tool.json").write_text("concurrent")

    with pytest.raises(StateConflict, match="concurrent"):
        apply(tmp_path, preview(tmp_path, output), writer=Race())
    assert (tmp_path / CONTROL / "journal.json").exists()
    assert (tmp_path / "tool.json").read_text() == "concurrent"


def test_generated_deletion_recovery(tmp_path):
    output = render(example())
    apply(tmp_path, preview(tmp_path, output))
    reduced = dataclasses.replace(output, generated={"tool.json": output.generated["tool.json"]})

    class Crash(AtomicWriter):
        def replace(self, path, data, staging):
            super().replace(path, data, staging)
            if data is None:
                raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        apply(tmp_path, preview(tmp_path, reduced), writer=Crash())
    assert recover(tmp_path)
    assert not preview(tmp_path, reduced).drift
