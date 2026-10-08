"""Atomic single-file persistence with fail-fast cooperative concurrency control.

Locks serialize cooperating writers; expected bytes add optimistic conflict detection.
A crash can leave a lock: recovery is explicit, never time-based lock stealing.
Parents must be trusted directories (this is not a sandbox against hostile users).
"""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol


class StateConflict(RuntimeError):
    """A writer is active, or the expected state no longer matches."""


class StateIO(Protocol):
    def read(self, path: Path) -> bytes | None: ...
    def replace(
        self, path: Path, data: bytes, *, expected: bytes | None, mode: int | None = 0o600
    ) -> None: ...
    def remove(self, path: Path, *, expected: bytes) -> None: ...


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

    @contextmanager
    def _locked(self, path: Path, expected: bytes | None) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_name(path.name + ".scriptkit-lock")
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise StateConflict("state writer lock exists; explicit recovery required") from None
        try:
            os.close(fd)
            if self.read(path) != expected:
                raise StateConflict("state changed since it was read")
            yield
        finally:
            lock.unlink()

    def replace(
        self, path: Path, data: bytes, *, expected: bytes | None, mode: int | None = 0o600
    ) -> None:
        """Replace under a cooperative lock; mode=None preserves existing metadata.

        Preserve mode and POSIX uid/gid, or fail before publishing. New files are
        private (0600). Windows mode is not an ACL/owner preservation mechanism.
        """
        with self._locked(path, expected):
            previous = path.stat() if mode is None and expected is not None else None
            fd, name = tempfile.mkstemp(prefix=".scriptkit-", dir=path.parent)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    if previous is not None and os.name != "nt":
                        staged = os.fstat(stream.fileno())
                        if (staged.st_uid, staged.st_gid) != (previous.st_uid, previous.st_gid):
                            os.fchown(stream.fileno(), previous.st_uid, previous.st_gid)
                    # Ownership changes can clear set-id bits: restore mode afterwards.
                    # Metadata errors are fatal and must not publish the staged inode.
                    permissions = stat.S_IMODE(previous.st_mode) if previous else mode
                    os.chmod(temporary, 0o600 if permissions is None else permissions)
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)

    def remove(self, path: Path, *, expected: bytes) -> None:
        """Delete only the expected file, serialized with cooperating replacements."""
        with self._locked(path, expected):
            path.unlink()
