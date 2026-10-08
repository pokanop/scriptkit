"""Recoverable roll-forward transactions under a process-scoped OS lock.

Trusted project directories only, not a sandbox against concurrent hostile writers.
A persisted journal is authoritative until recovery completes. Readers must not run
an interrupted project before recovery; multi-file atomic visibility is not claimed.
"""

from __future__ import annotations

import base64
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from scriptkit.contracts.codec import Record
from scriptkit.contracts.models import constrained, record, unique

from .plan import CONTROL, MANIFEST, Change, Plan, read, target
from .render import canonical


class StateConflict(RuntimeError):
    """A generated file/base changed or another generator holds the lock."""


class RecoveryConflict(StateConflict):
    """Structured reconciliation evidence for every conflicting recovery target."""

    def __init__(self, conflicts: list[dict[str, object]]) -> None:
        self.report = {"error": "recovery conflict; journal retained", "conflicts": conflicts}
        super().__init__(canonical(self.report).decode())


@record
class Operation(Record):
    path: str
    before: str | None
    after: str | None

    def validate(self) -> None:
        from .plan import OwnedFile

        if self.path != MANIFEST:
            OwnedFile(self.path, "0" * 64)
        for value in (self.before, self.after):
            if value is not None:
                base64.b64decode(value, validate=True)


@record
class Journal(Record):
    schema_version: int = constrained(const=1)
    operations: tuple[Operation, ...]

    def validate(self) -> None:
        unique([op.path for op in self.operations], "journal paths")


class Writer(Protocol):
    def replace(self, path: Path, data: bytes | None, staging: Path) -> None: ...


class AtomicWriter:
    """Single-file atomic publication; the caller holds the project lock.

    Preserve existing mode/owner, never follow symlinks. The fixed staging path
    lives inside the private control directory and is reused during recovery.
    """

    def replace(self, path: Path, data: bytes | None, staging: Path) -> None:
        if path.is_symlink() or staging.is_symlink():
            raise ValueError("symlink write refused")
        if data is None:
            path.unlink()
            return
        previous = path.stat() if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        with staging.open("wb") as stream:
            stream.write(data)
            stream.flush()
            if previous is not None and os.name != "nt":
                info = os.fstat(stream.fileno())
                if (info.st_uid, info.st_gid) != (previous.st_uid, previous.st_gid):
                    os.fchown(stream.fileno(), previous.st_uid, previous.st_gid)
            os.chmod(staging, stat.S_IMODE(previous.st_mode) if previous else 0o600)
            os.fsync(stream.fileno())
        os.replace(staging, path)


def control(root: Path) -> Path:
    # Reuse confinement checks, with a sentinel regular-file target.
    target(root, MANIFEST)
    directory = root / CONTROL
    if directory.exists() and not directory.is_dir():
        raise ValueError("control path is not a directory")
    return directory


@contextmanager
def locked(root: Path) -> Iterator[Path]:
    directory = control(root)
    directory.mkdir(mode=0o700, exist_ok=True)
    lock = directory / "lock"
    if lock.is_symlink():
        raise ValueError("symlink lock refused")
    with lock.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                getattr(msvcrt, "locking")(stream.fileno(), getattr(msvcrt, "LK_NBLCK"), 1)
            except OSError as exc:
                raise StateConflict("generator writer active") from exc
        else:
            import fcntl

            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise StateConflict("generator writer active") from exc
        try:
            yield directory
        finally:
            if os.name == "nt":
                stream.seek(0)
                getattr(msvcrt, "locking")(stream.fileno(), getattr(msvcrt, "LK_UNLCK"), 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def encode(value: bytes | None) -> str | None:
    return base64.b64encode(value).decode() if value is not None else None


def decode(value: str | None) -> bytes | None:
    return base64.b64decode(value, validate=True) if value is not None else None


def finish(root: Path, journal: Journal, directory: Path, writer: Writer) -> None:
    # Validate every target before any writes, including when recovering.
    conflicts: list[dict[str, object]] = []
    for op in journal.operations:
        current = read(root, op.path)
        before, after = decode(op.before), decode(op.after)
        if current not in (before, after):
            conflicts.append(
                {
                    "path": op.path,
                    "before_absent": before is None,
                    "after_absent": after is None,
                    "current_absent": current is None,
                    "diff": Change(op.path, current, after).diff,
                }
            )
    if conflicts:
        raise RecoveryConflict(conflicts)
    for op in journal.operations:
        after = decode(op.after)
        current = read(root, op.path)
        if current == after:
            continue
        if current != decode(op.before):
            raise StateConflict(f"concurrent change: {op.path}; journal retained")
        writer.replace(target(root, op.path), after, directory / "staging")
    (directory / "journal.json").unlink()
    (directory / "staging").unlink(missing_ok=True)


def apply(root: Path, plan: Plan, *, writer: Writer | None = None) -> None:
    if plan.conflicts:
        raise StateConflict(
            "generated files modified or foreign files exist; inspect preview diffs"
        )

    # Fail stale bases and unsafe paths before even creating the lock directory.
    def validate() -> None:
        for name, expected in plan.bases:
            if read(root, name) != expected:
                raise StateConflict(f"stale preview: {name}")
        bases = dict(plan.bases)
        unique([c.path for c in plan.changes], "plan paths")
        for change in plan.changes:
            target(root, change.path)
            if change.path not in bases or bases[change.path] != change.before:
                raise ValueError("change lacks matching base")

    validate()
    if not plan.changes:
        return
    with locked(root) as directory:
        validate()
        journal_path = directory / "journal.json"
        if journal_path.exists() or journal_path.is_symlink():
            raise StateConflict("interrupted apply; call recover first")
        journal = Journal(
            1, tuple(Operation(c.path, encode(c.before), encode(c.after)) for c in plan.changes)
        )
        backend = writer or AtomicWriter()
        backend.replace(journal_path, journal.canonical_json().encode(), directory / "staging")
        finish(root, journal, directory, backend)


def recover(root: Path, *, writer: Writer | None = None) -> bool:
    """Resume an interrupted apply; never steal an active process's lock.

    Refuse recovery when any file matches neither its before nor after bytes.
    No timestamp-based guesses, imports, network or executable journal content.
    """
    # A clean/never-generated project needs no recovery and must stay untouched.
    # Re-check after locking: another recovery may finish between these reads.
    journal_path = control(root) / "journal.json"
    if not journal_path.exists() and not journal_path.is_symlink():
        return False
    with locked(root) as directory:
        journal_path = directory / "journal.json"
        if not journal_path.exists() and not journal_path.is_symlink():
            return False
        if journal_path.is_symlink():
            raise ValueError("symlink journal refused")
        journal = Journal.from_json(journal_path.read_text(encoding="utf-8"))
        finish(root, journal, directory, writer or AtomicWriter())
    return True
