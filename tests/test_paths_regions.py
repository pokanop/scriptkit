import pytest

from scriptkit.paths import resolve_paths
from scriptkit.regions import OwnedRegion, RegionReceipt
from scriptkit.state import StateConflict


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_platform_paths_and_overrides(tmp_path, platform):
    paths = resolve_paths("tool", platform=platform, home=tmp_path, environ={})
    assert all(getattr(paths, kind).is_absolute() for kind in ("config", "data", "cache", "state"))
    assert not paths.config.exists()
    assert (
        resolve_paths(
            "tool",
            platform=platform,
            home=tmp_path,
            environ={},
            overrides={"config": tmp_path / "legacy"},
        ).config
        == tmp_path / "legacy"
    )
    if platform == "linux":
        assert paths.config == tmp_path / ".config/tool"
    elif platform == "darwin":
        assert paths.config == tmp_path / "Library/Application Support/tool"
    else:
        assert paths.config == tmp_path / "AppData/Roaming/tool"


def test_path_environment_and_invalid_inputs(tmp_path):
    from pathlib import Path

    assert (
        resolve_paths(
            "tool",
            home=tmp_path,
            platform="linux",
            environ={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        ).config
        == tmp_path / "xdg/tool"
    )
    assert (
        resolve_paths(
            "tool", home=tmp_path, platform="linux", environ={"XDG_CONFIG_HOME": "relative"}
        ).config
        == tmp_path / ".config/tool"
    )
    assert (
        resolve_paths(
            "tool", home=tmp_path, platform="win32", environ={"APPDATA": str(tmp_path / "roam")}
        ).config
        == tmp_path / "roam/tool"
    )
    for kwargs in (
        {"app": "../bad"},
        {"app": "tool", "home": Path("relative")},
        {"app": "tool", "overrides": {"oops": tmp_path}},
        {"app": "tool", "overrides": {"data": Path("relative")}},
    ):
        with pytest.raises(ValueError):
            resolve_paths(**kwargs)
    assert resolve_paths("tool").data.is_absolute()


@pytest.mark.parametrize("original", [b"", b"user", b"user\n", b"user\r\n", b"user\n\n"])
def test_owned_region_roundtrip(tmp_path, original):
    path = tmp_path / "rc"
    path.write_bytes(original)
    region = OwnedRegion("tool.path")
    receipt = region.apply(path, 'export PATH="/tools:$PATH"')
    after = path.read_bytes()
    assert region.apply(path, 'export PATH="/tools:$PATH"', receipt=receipt) == receipt
    assert path.read_bytes() == after
    receipt = region.apply(path, "updated", receipt=receipt)
    assert region.clear(path, receipt=receipt)
    assert path.read_bytes() == original
    assert not region.clear(path, receipt=receipt)


def test_region_new_file_empty_body_and_foreign_substrings(tmp_path):
    path = tmp_path / "rc"
    region = OwnedRegion("tool")
    receipt = region.apply(path, "")
    assert region.clear(path, receipt=receipt)
    foreign = b"echo '# >>> scriptkit:tool >>>'\r\nuser\r\n"
    path.write_bytes(foreign)
    receipt = region.apply(path, "body")
    # Foreign changes outside the region survive uninstall.
    path.write_bytes(b"header\r\n" + path.read_bytes() + b"tail\r\n")
    region.clear(path, receipt=receipt)
    assert path.read_bytes() == b"header\r\n" + foreign + b"tail\r\n"


def test_region_drift_and_ownership_fail_closed(tmp_path):
    path = tmp_path / "rc"
    region = OwnedRegion("tool")
    receipt = region.apply(path, "body")
    with pytest.raises(StateConflict):
        region.apply(path, "other")
    with pytest.raises(StateConflict):
        region.clear(path, receipt=RegionReceipt("foreign", receipt.block, b""))
    path.write_bytes(path.read_bytes().replace(b"body", b"user edit"))
    with pytest.raises(StateConflict):
        region.clear(path, receipt=receipt)
    path.write_bytes(b"user")
    with pytest.raises(StateConflict):
        region.apply(path, "new", receipt=receipt)
    assert path.read_bytes() == b"user"


@pytest.mark.parametrize(
    "text",
    [
        "{begin}\n",
        "{end}\n",
        "{end}\n{begin}\n",
        "{begin}\n{begin}\n{end}\n",
        "{begin}\n{end}\n{end}\n",
    ],
)
def test_malformed_regions_refused(tmp_path, text):
    region = OwnedRegion("tool")
    path = tmp_path / "rc"
    raw = text.format(begin=region.begin.decode(), end=region.end.decode()).encode()
    path.write_bytes(raw)
    with pytest.raises(StateConflict):
        region.apply(path, "body")
    assert path.read_bytes() == raw


def test_invalid_owner_and_body(tmp_path):
    with pytest.raises(ValueError):
        OwnedRegion("bad\nmarker")
    region = OwnedRegion("tool")
    with pytest.raises(ValueError):
        region.apply(tmp_path / "rc", region.begin.decode())
