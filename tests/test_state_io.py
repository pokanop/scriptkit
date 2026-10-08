import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from scriptkit.state import LocalStateIO, StateConflict


def test_atomic_permissions_and_conflicts(tmp_path):
    io = LocalStateIO()
    path = tmp_path / "nested/config"
    assert io.read(path) is None
    io.replace(path, b"old", expected=None)
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(StateConflict):
        io.replace(path, b"bad", expected=None)
    io.replace(path, b"new", expected=b"old")
    assert path.read_bytes() == b"new"
    assert sorted(p.name for p in path.parent.iterdir()) == ["config"]


@pytest.mark.parametrize("operation", ["replace", "fsync", "chmod"])
@pytest.mark.parametrize("error", [PermissionError, KeyboardInterrupt])
def test_failed_write_preserves_old_and_cleans_up(tmp_path, monkeypatch, operation, error):
    path = tmp_path / "config"
    path.write_bytes(b"original")

    def fail(*args):
        raise error("injected")

    monkeypatch.setattr(os, operation, fail)
    with pytest.raises(error):
        LocalStateIO().replace(path, b"replacement", expected=b"original")
    assert path.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [path]


def test_concurrent_writers_one_wins(tmp_path):
    path = tmp_path / "config"
    path.write_bytes(b"base")
    barrier = Barrier(2)

    def write(value):
        barrier.wait()
        try:
            LocalStateIO().replace(path, value, expected=b"base")
            return True
        except StateConflict:
            return False

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(write, [b"a", b"b"]))
    assert sorted(results) == [False, True]
    assert path.read_bytes() in (b"a", b"b")


def test_stale_lock_is_not_stolen(tmp_path):
    path = tmp_path / "config"
    lock = tmp_path / "config.scriptkit-lock"
    lock.write_bytes(b"foreign lock")
    with pytest.raises(StateConflict):
        LocalStateIO().replace(path, b"new", expected=None)
    assert lock.read_bytes() == b"foreign lock"
    assert not path.exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires Windows privilege")
def test_symlink_refused(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"foreign")
    path = tmp_path / "link"
    path.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        LocalStateIO().replace(path, b"bad", expected=b"foreign")
    assert target.read_bytes() == b"foreign"


def test_read_permission_error_propagates(monkeypatch, tmp_path):
    def fail(self):
        raise PermissionError

    monkeypatch.setattr(Path, "read_bytes", fail)
    with pytest.raises(PermissionError):
        LocalStateIO().read(tmp_path / "config")
