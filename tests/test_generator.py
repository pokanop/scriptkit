"""Backend contract suite runs unchanged against the installed wheel in release CI."""

import dataclasses
import json
import os
import subprocess
import sys
import tomllib

import pytest

from scriptkit.contracts import ToolSpec, resource_text
from scriptkit.generator import apply, normalize, preview, recover, render
from scriptkit.generator.__main__ import main
from scriptkit.generator.plan import Change, Manifest, Plan, CONTROL, target
from scriptkit.generator.transaction import (
    AtomicWriter,
    Journal,
    Operation,
    StateConflict,
    locked,
)


@pytest.fixture
def spec():
    value = json.loads(resource_text("ToolSpec.example.json"))
    value["entrypoint"] = "demo.cli:main"
    return ToolSpec.from_dict(value)


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_deterministic_offline_backend_contract(tmp_path, spec, monkeypatch):
    import socket

    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("network"))
    first = render(spec)
    assert first == render(normalize(spec))
    from scriptkit.generator.render import canonical, sha256

    inventory = {p: sha256(b) for p, b in (first.generated | first.user).items()}
    assert sha256(canonical(inventory)) == (
        "aee0990447a1484c278f0af462a165f8790092143986bec13ce0e6ccbaf97b9e"
    )
    project = tomllib.loads(first.generated["pyproject.toml"].decode())
    assert project["project"]["scripts"][spec.name] == spec.entrypoint
    before = snapshot(tmp_path)
    plan = preview(tmp_path, first)
    assert plan.drift and not plan.conflicts
    assert json.loads(plan.to_json())["drift"]
    assert snapshot(tmp_path) == before  # check is truly read-only
    apply(tmp_path, plan)
    result = snapshot(tmp_path)
    assert not preview(tmp_path, first).drift
    apply(tmp_path, preview(tmp_path, first))
    assert snapshot(tmp_path) == result
    other = tmp_path / "another-host-path"
    other.mkdir()
    apply(other, preview(other, first))
    assert snapshot(other) == result


def test_user_code_preserved_and_never_imported(tmp_path, spec):
    output = render(spec)
    apply(tmp_path, preview(tmp_path, output))
    handler = tmp_path / "src/demo/_handlers.py"
    handler.write_text("raise RuntimeError('must not import during generation')\n")
    next_spec = dataclasses.replace(spec, description="new description")
    apply(tmp_path, preview(tmp_path, render(next_spec)))
    assert "must not import" in handler.read_text()
    assert not preview(tmp_path, render(next_spec)).drift


@pytest.mark.parametrize("replacement", [b"handwritten edit\n", None])
def test_modified_generated_conflicts_with_diff(tmp_path, spec, replacement):
    output = render(spec)
    apply(tmp_path, preview(tmp_path, output))
    file = tmp_path / "tool.json"
    if replacement is None:
        file.unlink()
    else:
        file.write_bytes(replacement)
    plan = preview(tmp_path, output)
    assert plan.conflicts and plan.conflicts[0].diff
    before = snapshot(tmp_path)
    with pytest.raises(StateConflict):
        apply(tmp_path, plan)
    assert snapshot(tmp_path) == before


def test_foreign_same_bytes_not_adopted(tmp_path, spec):
    output = render(spec)
    (tmp_path / "tool.json").write_bytes(output.generated["tool.json"])
    assert preview(tmp_path, output).conflicts


def test_stale_plan_fails_before_writes(tmp_path, spec):
    output = render(spec)
    plan = preview(tmp_path, output)
    (tmp_path / "tool.json").write_text("race")
    before = snapshot(tmp_path)
    with pytest.raises(StateConflict, match="stale"):
        apply(tmp_path, plan)
    assert snapshot(tmp_path) == before
    assert not (tmp_path / CONTROL).exists()


def test_removed_generated_and_ownership_migrations(tmp_path, spec):
    output = render(spec)
    apply(tmp_path, preview(tmp_path, output))
    reduced = dataclasses.replace(output, generated={"tool.json": output.generated["tool.json"]})
    apply(tmp_path, preview(tmp_path, reduced))
    assert not (tmp_path / "pyproject.toml").exists()
    assert (tmp_path / "src/demo/_handlers.py").exists()
    with pytest.raises(ValueError, match="ownership"):
        preview(tmp_path, dataclasses.replace(reduced, generated={"src/demo/_handlers.py": b""}))
    with pytest.raises(ValueError, match="ownership"):
        preview(tmp_path, dataclasses.replace(reduced, user={"tool.json": b""}))
    with pytest.raises(ValueError, match="migration"):
        preview(tmp_path, dataclasses.replace(reduced, template="2.0.0"))


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a/../../escape", "a\\b", "con", "a."])
def test_path_escapes_before_writes(tmp_path, spec, name):
    output = dataclasses.replace(render(spec), generated={name: b"bad"})
    with pytest.raises(ValueError):
        preview(tmp_path, output)
    assert not snapshot(tmp_path)


def test_symlinks_and_directory_collisions(tmp_path, spec):
    output = render(spec)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / "src").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(ValueError, match="symlink"):
        preview(tmp_path, output)
    (tmp_path / "src").unlink()
    (tmp_path / "src").write_text("not a directory")
    with pytest.raises(ValueError, match="parent"):
        preview(tmp_path, output)
    (tmp_path / "src").unlink()
    (tmp_path / "tool.json").mkdir()
    with pytest.raises(ValueError, match="regular"):
        preview(tmp_path, output)
    with pytest.raises(ValueError, match="root"):
        target(tmp_path / "missing", "a")


class InterruptingWriter(AtomicWriter):
    def __init__(self, interrupt_at):
        self.remaining = interrupt_at

    def replace(self, path, data, staging):
        super().replace(path, data, staging)
        self.remaining -= 1
        if self.remaining == 0:
            raise KeyboardInterrupt


@pytest.mark.parametrize("point", range(1, 10))
def test_interrupted_apply_rolls_forward(tmp_path, spec, point):
    output = render(spec)
    plan = preview(tmp_path, output)
    assert len(plan.changes) == 8
    with pytest.raises(KeyboardInterrupt):
        apply(tmp_path, plan, writer=InterruptingWriter(point))
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="interrupted"):
        preview(tmp_path, output)
    assert snapshot(tmp_path) == before
    assert recover(tmp_path)
    assert not recover(tmp_path)
    assert not preview(tmp_path, output).drift


def test_recovery_conflict_keeps_journal(tmp_path, spec):
    with pytest.raises(KeyboardInterrupt):
        apply(tmp_path, preview(tmp_path, render(spec)), writer=InterruptingWriter(2))
    (tmp_path / "tool.json").write_text("concurrent user change")
    before = snapshot(tmp_path)
    with pytest.raises(StateConflict, match="recovery conflict"):
        recover(tmp_path)
    assert snapshot(tmp_path) == before


def test_process_death_releases_lock_and_recovers(tmp_path, spec):
    spec_file = tmp_path / "input.json"
    spec_file.write_text(spec.canonical_json())
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys
from pathlib import Path
from scriptkit.contracts import ToolSpec
from scriptkit.generator import render, preview, apply
from scriptkit.generator.transaction import AtomicWriter
class Crash(AtomicWriter):
    def replace(self, path, data, staging):
        super().replace(path, data, staging)
        if path.name == 'pyproject.toml':
            os._exit(73)
root = Path(sys.argv[1])
apply(root, preview(root, render(ToolSpec.from_json((root/'input.json').read_text()))), writer=Crash())
""",
            str(tmp_path),
        ]
    )
    assert result.returncode == 73
    assert recover(tmp_path)
    assert not preview(tmp_path, render(spec)).drift


def test_lock_excludes_concurrent_writer(tmp_path):
    with locked(tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import sys
from pathlib import Path
from scriptkit.generator.transaction import locked, StateConflict
try:
    with locked(Path(sys.argv[1])):
        sys.exit(3)
except StateConflict:
    sys.exit(0)
""",
                str(tmp_path),
            ]
        )
        assert result.returncode == 0


def test_cli_check_apply_recover_errors(tmp_path, spec, capsys):
    file = tmp_path / "input.json"
    file.write_text(spec.canonical_json())
    args = [str(file), str(tmp_path)]
    before = snapshot(tmp_path)
    assert main([*args, "--check"]) == 1
    assert json.loads(capsys.readouterr().out)["drift"]
    assert snapshot(tmp_path) == before
    assert main([*args, "--apply"]) == 0
    capsys.readouterr()
    assert main([*args, "--check"]) == 0
    capsys.readouterr()
    assert main([*args, "--recover"]) == 0
    assert json.loads(capsys.readouterr().out) == {"recovered": False}
    (tmp_path / "tool.json").write_text("changed")
    assert main(args) == 2
    capsys.readouterr()
    assert main([*args, "--apply"]) == 2
    assert "error" in json.loads(capsys.readouterr().out)
    file.write_text("{")
    assert main(args) == 2
    capsys.readouterr()
    run = subprocess.run([sys.executable, "-m", "scriptkit.generator", *args], capture_output=True)
    assert run.returncode == 2 and b'"error"' in run.stdout


def test_render_rejects_versions_reserved_modules_and_hook_data(spec, monkeypatch):
    with pytest.raises(ValueError, match="version"):
        render(spec, template_version="latest")
    for entry in [
        "single:main",
        "demo.class:main",
        "demo.cli:class",
        "demo._handlers:main",
        "demo.__init__:main",
    ]:
        with pytest.raises(ValueError, match="entrypoint"):
            render(dataclasses.replace(spec, entrypoint=entry))
    value = json.loads(spec.canonical_json())
    for key in ["_tasks", "_migrations", "_jinja_extensions", "hooks"]:
        with pytest.raises(ValueError):
            ToolSpec.from_dict({**value, key: ["touch pwned"]})
    import importlib

    backend = importlib.import_module("scriptkit.generator.render")
    monkeypatch.setattr(backend, "TEMPLATE_SHA256", "bad")
    with pytest.raises(ValueError, match="integrity"):
        render(spec)


def test_manifest_and_journal_validation():
    with pytest.raises(ValueError):
        Journal(2, ())
    with pytest.raises(ValueError):
        Operation("../bad", None, None)
    with pytest.raises(ValueError):
        Operation("safe", "not base64!", None)
    with pytest.raises(ValueError):
        Journal(1, (Operation("a", None, None), Operation("a", None, None)))
    with pytest.raises(ValueError):
        Manifest(1, "1.0.0", "text-lf-1", "0" * 64, (), ("../bad",))


def test_invalid_plan_and_pending_journal(tmp_path, spec):
    with pytest.raises(ValueError, match="matching base"):
        apply(tmp_path, Plan((Change("a", None, b"a"),), ()))
    plan = preview(tmp_path, render(spec))
    with pytest.raises(KeyboardInterrupt):
        apply(tmp_path, plan, writer=InterruptingWriter(1))
    with pytest.raises(StateConflict, match="recover"):
        apply(tmp_path, plan)


def test_generated_launcher_runs_explicit_handler(tmp_path, spec):
    output = render(spec)
    apply(tmp_path, preview(tmp_path, output))
    (tmp_path / "src/demo/_handlers.py").write_text(
        "def run(command, arguments):\n    print(command, arguments)\n    return 0\n"
    )
    env = {**os.environ, "PYTHONPATH": str(tmp_path / "src")}
    result = subprocess.run(
        [sys.executable, "-m", "demo.cli", "--help"], env=env, capture_output=True
    )
    assert result.returncode == 0
    assert spec.description.encode() in result.stdout


def test_atomic_writer_preserves_mode_and_handles_deletion(tmp_path):
    file = tmp_path / "file"
    staging = tmp_path / "staging"
    writer = AtomicWriter()
    writer.replace(file, b"one", staging)
    file.chmod(0o640)
    before = file.stat().st_mode
    writer.replace(file, b"two", staging)
    assert file.stat().st_mode == before
    writer.replace(file, None, staging)
    assert not file.exists()
