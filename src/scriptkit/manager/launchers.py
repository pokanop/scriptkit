"""Stable launchers read a single per-tool pointer; updates never replace executables."""

from __future__ import annotations

import os
import shlex
import sys
import tempfile
from pathlib import Path


def launchers(tool: Path, bin_dir: Path, name: str) -> dict[Path, bytes]:
    loader = tool / "launch.py"
    source = (
        "import json, pathlib, subprocess, sys\n"
        f"state = json.loads(pathlib.Path({str(tool / 'active.json')!r}).read_text())\n"
        "target = state.get('target')\n"
        "if target is None: sys.exit('tool is not installed')\n"
        f"base = pathlib.Path({str(tool)!r}) / 'generations' / target\n"
        f"python = base / 'env' / {('Scripts/python.exe' if os.name == 'nt' else 'bin/python')!r}\n"
        "sys.exit(subprocess.call([str(python), '-I', '-B', str(base / 'run.py'), *sys.argv[1:]]))\n"
    ).encode()
    if os.name == "nt":
        # cmd expands these characters even inside quoted paths.
        if any(c in str(loader) + sys.executable for c in '%!\r\n"'):
            raise ValueError("launcher paths cannot contain cmd expansion characters")
        wrapper = f'@echo off\r\n"{sys.executable}" -I "{loader}" %*\r\n'.encode()
        command = bin_dir / (name + ".cmd")
    else:
        wrapper = f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -I {shlex.quote(str(loader))} "$@"\n'.encode()
        command = bin_dir / name
    return {loader: source, command: wrapper}


def check_owned(files: dict[Path, bytes]) -> None:
    for path, expected in files.items():
        if path.is_symlink() or (path.exists() and path.read_bytes() != expected):
            raise ValueError(f"refusing foreign launcher: {path.name}")


def ensure(files: dict[Path, bytes]) -> None:
    check_owned(files)
    for path, content in files.items():
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        # Link publication is exclusive on NTFS and POSIX; no overwrite fallback.
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".launcher-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o755)
            os.link(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
