"""Explicit platform directories; resolving paths never creates or migrates data."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PlatformPaths:
    config: Path
    data: Path
    cache: Path
    state: Path


def resolve_paths(
    app: str,
    *,
    platform: str | None = None,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, Path] | None = None,
) -> PlatformPaths:
    """Resolve XDG/macOS/Windows defaults, then explicit absolute overrides.

    Relative environment directory values are ignored, as required by XDG.
    Application names are portable single components, not arbitrary paths.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", app):
        raise ValueError("app must be a portable lowercase identifier")
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else home
    env = os.environ if environ is None else environ
    if not home.is_absolute():
        raise ValueError("home must be absolute")

    def directory(key: str, fallback: Path) -> Path:
        value = Path(env.get(key, ""))
        return value if value.is_absolute() else fallback

    if platform == "win32":
        roaming = directory("APPDATA", home / "AppData/Roaming")
        local = directory("LOCALAPPDATA", home / "AppData/Local")
        roots = dict(
            config=roaming / app,
            data=local / app,
            cache=local / app / "Cache",
            state=local / app / "State",
        )
    elif platform == "darwin":
        support = home / "Library/Application Support" / app
        roots = dict(
            config=support,
            data=support,
            cache=home / "Library/Caches" / app,
            state=support / "State",
        )
    else:
        roots = {
            kind: directory(key, home / fallback) / app
            for kind, key, fallback in (
                ("config", "XDG_CONFIG_HOME", ".config"),
                ("data", "XDG_DATA_HOME", ".local/share"),
                ("cache", "XDG_CACHE_HOME", ".cache"),
                ("state", "XDG_STATE_HOME", ".local/state"),
            )
        }
    for kind, path in (overrides or {}).items():
        if kind not in roots or not path.is_absolute():
            raise ValueError("override must name a directory kind and be absolute")
        roots[kind] = path
    return PlatformPaths(**roots)
