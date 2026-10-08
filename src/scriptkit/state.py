"""Atomic single-file persistence with fail-fast cooperative concurrency control.

Locks serialize cooperating writers; expected bytes add optimistic conflict detection.
A crash can leave a lock: recovery is explicit, never time-based lock stealing.
Parents must be trusted directories (this is not a sandbox against hostile users).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Protocol


class StateConflict(RuntimeError):
    """A writer is active, or the expected state no longer matches."""


class StateIO(Protocol):
    def read(self, path: Path) -> bytes | None: ...
    def replace(
        self, path: Path, data: bytes, *, expected: bytes | None, mode: int = 0o600
    ) -> None: ...


class LocalStateIO:
    """No symlink targets; private staging; fsync before atomic replacement.

    On Windows mode is not an ACL; configure private parent-directory ACLs.
    Atomic visibility is guaranteed, not power-loss durability of the directory.
    """

    def read(self, path: Path) -> bytes | None:
        if path.is_symlink():
            raise ValueError("state target must not be a symlink")
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def replace(
        self, path: Path, data: bytes, *, expected: bytes | None, mode: int = 0o600
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_name(path.name + ".scriptkit-lock")
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise StateConflict("state writer lock exists; explicit recovery required") from None
        temporary: Path | None = None
        try:
            os.close(fd)
            if self.read(path) != expected:
                raise StateConflict("state changed since it was read")
            fd, name = tempfile.mkstemp(prefix=".scriptkit-", dir=path.parent)
            temporary = Path(name)
            with os.fdopen(fd, "wb") as stream:
                # Apply permissions before publishing; permission errors are not swallowed.
                os.chmod(temporary, mode)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            lock.unlink()
