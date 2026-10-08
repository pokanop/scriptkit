"""Private state primitives. The caller owns the root and its directory ACLs."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from scriptkit.state import StateConflict


@contextmanager
def locked(root: Path) -> Iterator[None]:
    """Kernel lock: released on process exit; never unlink a lock inode."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "installer.lock"
    if path.is_symlink():
        raise ValueError("symlink lock")
    with path.open("a+b") as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                # Windows byte-range locks also deny reads: inspect metadata,
                # never read the locked byte before attempting acquisition.
                if os.fstat(stream.fileno()).st_size == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                getattr(msvcrt, "locking")(stream.fileno(), getattr(msvcrt, "LK_NBLCK"), 1)
            except OSError as exc:
                raise StateConflict("installer is busy") from exc
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise StateConflict("installer is busy") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                getattr(msvcrt, "locking")(stream.fileno(), getattr(msvcrt, "LK_UNLCK"), 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read(path: Path) -> dict[str, Any] | None:
    if path.is_symlink():
        raise ValueError("symlink state")
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid installer state")
    return value


def publish(path: Path, value: dict[str, Any]) -> None:
    """Atomic visibility on supported local filesystems; locked Windows targets fail closed.

    File data is fsynced. Directory power-loss durability is not promised.
    No remove-then-rename fallback (especially on Windows).
    """
    if path.is_symlink():
        raise ValueError("symlink state")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
