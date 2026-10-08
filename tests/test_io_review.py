"""POK-619 round-one review regressions: reversible metadata and secret refs."""

import os
import stat
from types import SimpleNamespace

import pytest

from scriptkit.regions import OwnedRegion
from scriptkit.safe_config import SafeConfig
from scriptkit.state import LocalStateIO, StateConflict


@pytest.mark.parametrize("mode", [0o600, 0o644, 0o640, 0o755])
def test_existing_region_preserves_metadata_on_update_and_clear(tmp_path, mode):
    path = tmp_path / "rc"
    path.write_bytes(b"user config\r\n")
    path.chmod(mode)
    before = path.stat()
    region = OwnedRegion("tool")
    receipt = region.apply(path, "first")
    assert not receipt.created

    def unchanged():
        after = path.stat()
        assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
        if os.name != "nt":
            assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)

    unchanged()
    receipt = region.apply(path, "second", receipt=receipt)
    unchanged()
    assert region.clear(path, receipt=receipt)
    unchanged()
    assert path.read_bytes() == b"user config\r\n"


@pytest.mark.skipif(
    os.name == "nt" or os.geteuid() != 0, reason="requires POSIX root for foreign uid"
)
def test_root_installer_preserves_foreign_owner(tmp_path):
    path = tmp_path / ".bashrc"
    path.write_bytes(b"user config\n")
    os.chown(path, 65534, 65534)
    path.chmod(0o644)
    region = OwnedRegion("tool")
    receipt = region.apply(path, "body")
    assert (path.stat().st_uid, path.stat().st_gid) == (65534, 65534)
    region.clear(path, receipt=receipt)
    assert (path.stat().st_uid, path.stat().st_gid) == (65534, 65534)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert path.read_bytes() == b"user config\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership adapter")
@pytest.mark.parametrize("error", [PermissionError, KeyboardInterrupt])
def test_failed_owner_preservation_never_replaces_original(tmp_path, monkeypatch, error):
    path = tmp_path / "rc"
    path.write_bytes(b"original")
    before = path.stat()
    monkeypatch.setattr(os, "fstat", lambda fd: SimpleNamespace(st_uid=-1, st_gid=-1))

    def fail(*args):
        raise error("cannot preserve ownership")

    monkeypatch.setattr(os, "fchown", fail)
    with pytest.raises(error):
        LocalStateIO().replace(path, b"new", expected=b"original", mode=None)
    assert path.read_bytes() == b"original"
    after = path.stat()
    assert (after.st_ino, after.st_mode, after.st_uid, after.st_gid, after.st_mtime_ns) == (
        before.st_ino,
        before.st_mode,
        before.st_uid,
        before.st_gid,
        before.st_mtime_ns,
    )
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("foreign", [b"", b"user added config\n"])
def test_created_region_file_removal_preserves_foreign_additions(tmp_path, foreign):
    path = tmp_path / ".bash_profile"
    region = OwnedRegion("tool")
    receipt = region.apply(path, "first")
    assert receipt.created
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    receipt = region.apply(path, "updated", receipt=receipt)
    assert receipt.created
    path.write_bytes(path.read_bytes() + foreign)
    assert region.clear(path, receipt=receipt)
    if foreign:
        assert path.read_bytes() == foreign
    else:
        assert not path.exists()
    assert not region.clear(path, receipt=receipt)


def test_existing_empty_region_file_is_not_removed(tmp_path):
    path = tmp_path / "rc"
    path.touch()
    region = OwnedRegion("tool")
    receipt = region.apply(path, "body")
    assert not receipt.created
    region.clear(path, receipt=receipt)
    assert path.read_bytes() == b""


@pytest.mark.parametrize("failure", ["conflict", "lock", "permission", "interrupt"])
def test_remove_failure_preserves_file_and_lock_ownership(tmp_path, monkeypatch, failure):
    path = tmp_path / "rc"
    path.write_bytes(b"user")
    lock = tmp_path / "rc.scriptkit-lock"
    expected = b"stale" if failure == "conflict" else b"user"
    error = StateConflict
    if failure == "lock":
        lock.write_bytes(b"foreign lock")
    elif failure in ("permission", "interrupt"):
        original = type(path).unlink
        error = PermissionError if failure == "permission" else KeyboardInterrupt

        def fail(self, *args, **kwargs):
            if self == path:
                raise error
            return original(self, *args, **kwargs)

        monkeypatch.setattr(type(path), "unlink", fail)
    with pytest.raises(error):
        LocalStateIO().remove(path, expected=expected)
    assert path.read_bytes() == b"user"
    assert lock.exists() == (failure == "lock")


def test_region_clear_passes_snapshot_to_delete(tmp_path):
    class RacingIO(LocalStateIO):
        def remove(self, path, *, expected):
            path.write_bytes(b"concurrent foreign edit")
            super().remove(path, expected=expected)

    path = tmp_path / "rc"
    region = OwnedRegion("tool", io=RacingIO())
    receipt = region.apply(path, "body")
    with pytest.raises(StateConflict):
        region.clear(path, receipt=receipt)
    assert path.read_bytes() == b"concurrent foreign edit"


def test_documented_secret_reference_with_secret_set(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_TOKEN", "credential-do-not-persist")
    monkeypatch.setenv("TOOL_PORT", "9090")

    def validate(data):
        if set(data) - {"port", "token"} or type(data.get("port")) is not int:
            raise ValueError("invalid schema")

    cfg = SafeConfig(
        tmp_path / "config.json",
        defaults={"port": 8080},
        env_prefix="TOOL",
        coerce_env=True,
        validator=validate,
        secret_fields=("token",),
    )
    assert cfg.load() == {"port": 9090}
    ref = {"source": "env", "name": "TOOL_TOKEN"}
    cfg.save({"port": 9000, "token": ref})
    assert cfg.load() == {"port": 9090, "token": ref}
    assert b"credential" not in cfg.path.read_bytes()


@pytest.mark.parametrize(
    "key", ["TOOL_AUTH__TOKEN", "TOOL_AUTH__TOKEN__NAME", "TOOL_AUTH", "tool_actual_secret"]
)
def test_environment_cannot_mutate_or_flatten_secret_reference(tmp_path, key):
    ref = {"source": "env", "name": "TOOL_ACTUAL_SECRET"}
    config = SafeConfig(
        tmp_path / "config",
        defaults={"auth": {"token": ref}},
        env_prefix="TOOL",
        secret_fields=("auth.token",),
        environ={key: "credential", "TOOL_AUTH__HOST": "example"},
    )
    assert config.load() == {"auth": {"token": ref, "host": "example"}}
