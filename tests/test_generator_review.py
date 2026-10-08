"""Regressions from independent review, including Git for Windows defaults."""

import dataclasses
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from scriptkit.contracts import ToolSpec, resource_text
from scriptkit.generator import apply, preview, recover, render
from scriptkit.generator.__main__ import main
from scriptkit.generator.plan import CONTROL, MANIFEST
from scriptkit.generator.transaction import AtomicWriter, RecoveryConflict


def spec():
    value = json.loads(resource_text("ToolSpec.example.json"))
    value["entrypoint"] = "demo.cli:main"
    return ToolSpec.from_dict(value)


def test_owned_already_proposed_converges(tmp_path):
    output = render(spec())
    apply(tmp_path, preview(tmp_path, output))
    next_output = render(dataclasses.replace(spec(), description="updated"))
    for name, data in next_output.generated.items():
        (tmp_path / name).write_bytes(data)
    plan = preview(tmp_path, next_output)
    assert not plan.conflicts
    assert [c.path for c in plan.changes] == [MANIFEST]
    apply(tmp_path, plan)
    assert not preview(tmp_path, next_output).drift


def test_owned_absent_removed_converges(tmp_path):
    output = render(spec())
    apply(tmp_path, preview(tmp_path, output))
    (tmp_path / "tool.json").unlink()
    reduced = dataclasses.replace(
        output, generated={p: b for p, b in output.generated.items() if p != "tool.json"}
    )
    plan = preview(tmp_path, reduced)
    assert not plan.conflicts
    apply(tmp_path, plan)
    assert not preview(tmp_path, reduced).drift


def test_conflict_reasons_include_identical_foreign(tmp_path):
    output = render(spec())
    (tmp_path / "tool.json").write_bytes(output.generated["tool.json"])
    plan = preview(tmp_path, output)
    conflict = next(c for c in json.loads(plan.to_json())["changes"] if c["conflict"])
    assert conflict["reason"] == "foreign" and conflict["diff"] == ""
    (tmp_path / "tool.json").unlink()
    apply(tmp_path, preview(tmp_path, output))
    (tmp_path / "tool.json").unlink()
    (tmp_path / "pyproject.toml").write_bytes(b"modified\n")
    assert {c.reason for c in preview(tmp_path, output).conflicts} == {"missing", "modified"}


def test_autocrlf_clone_is_clean_and_ignores_transient_control(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git unavailable")
    root, clone = tmp_path / "root", tmp_path / "clone"
    root.mkdir()
    output = render(spec())
    apply(root, preview(root, output))
    # Simulate Windows lock content and interrupted staging (without a journal).
    (root / CONTROL / "lock").write_bytes(b"0")
    (root / CONTROL / "staging").write_bytes(b"temporary")

    def git(*args, cwd=root):
        return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.STDOUT)

    git("init")
    git("add", ".")
    tracked = git("ls-files").decode().splitlines()
    assert f"{CONTROL}/lock" not in tracked
    assert f"{CONTROL}/staging" not in tracked
    assert MANIFEST in tracked
    git(
        "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "generated"
    )
    git("-c", "core.autocrlf=true", "clone", str(root), str(clone))
    git("config", "core.autocrlf", "true", cwd=clone)
    assert not preview(clone, output).drift
    apply(clone, preview(clone, output))
    assert recover(clone) is False  # creates a native lock, still untracked
    assert not git("status", "--porcelain", cwd=clone)


def test_symlink_above_root_allowed_below_root_refused(tmp_path, monkeypatch):
    actual = tmp_path / "actual"
    actual.mkdir()
    root = actual / "project"
    root.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    spelling = alias / "project"
    output = render(spec())
    apply(spelling, preview(spelling, output))
    monkeypatch.chdir(spelling)
    assert not preview(Path("."), output).drift
    assert not preview(spelling, output).drift
    (root / "src/demo/cli.py").unlink()
    (root / "src/demo/cli.py").symlink_to(root / "tool.json")
    for path in (Path("."), spelling):
        with pytest.raises(ValueError, match="symlink"):
            preview(path, output)
    with pytest.raises(ValueError, match="root"):
        preview(alias, output)


def test_all_recovery_conflicts_explained_and_reconciled(tmp_path, capsys):
    class Crash(AtomicWriter):
        def replace(self, path, data, staging):
            super().replace(path, data, staging)
            if path.name == "journal.json":
                raise KeyboardInterrupt

    output = render(spec())
    with pytest.raises(KeyboardInterrupt):
        apply(tmp_path, preview(tmp_path, output), writer=Crash())
    for name in ("tool.json", "pyproject.toml"):
        (tmp_path / name).write_text("handwritten\n")
    journal = tmp_path / CONTROL / "journal.json"
    before = journal.read_bytes()
    with pytest.raises(RecoveryConflict) as error:
        recover(tmp_path)
    details = error.value.report["conflicts"]
    assert {c["path"] for c in details} == {"tool.json", "pyproject.toml"}
    assert all(c["before_absent"] and not c["after_absent"] for c in details)
    assert all("-handwritten" in c["diff"] and "+" in c["diff"] for c in details)
    assert journal.read_bytes() == before
    assert main(["unused.json", str(tmp_path), "--recover"]) == 2
    assert json.loads(capsys.readouterr().out)["conflicts"] == details
    # Documented reconciliation: preserve foreign work, restore absent baseline,
    # or accept exact proposed bytes, then retry. Both choices converge.
    (tmp_path / "tool.json").rename(tmp_path / "saved-handwritten.txt")
    (tmp_path / "pyproject.toml").write_bytes(output.generated["pyproject.toml"])
    assert recover(tmp_path)
    assert not preview(tmp_path, output).drift
    assert (tmp_path / "saved-handwritten.txt").read_text() == "handwritten\n"


@pytest.mark.parametrize(
    "function", ["argparse", "_handlers", "vars", "json", "int", "str", "__name__"]
)
def test_reserved_function_names_rejected(function):
    with pytest.raises(ValueError, match="reserved"):
        render(dataclasses.replace(spec(), entrypoint=f"demo.cli:{function}"))


def test_recover_rechecks_journal_after_lock(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from scriptkit.generator import transaction

    real_locked = transaction.locked

    @contextmanager
    def remove_before_acquiring(root):
        with real_locked(root) as directory:
            (directory / "journal.json").unlink()
            yield directory

    directory = tmp_path / CONTROL
    directory.mkdir()
    (directory / "journal.json").write_text("interrupted")
    monkeypatch.setattr(transaction, "locked", remove_before_acquiring)
    assert recover(tmp_path) is False
