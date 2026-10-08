"""Stable launchers read a single per-tool pointer; updates never replace executables."""

from __future__ import annotations

import hashlib
import os
import shlex
import sys
import tempfile
from pathlib import Path

from scriptkit.bootstrap import cmd_launcher


def launchers(
    tool: Path, bin_dir: Path, name: str, *, interpreter: str | None = None
) -> dict[Path, bytes]:
    interpreter = interpreter or sys.executable
    loader = tool / "launch.py"
    source = (
        "import json, os, pathlib, subprocess, sys\n"
        "try:\n"
        f"    state = json.loads(pathlib.Path({str(tool / 'active.json')!r}).read_text())\n"
        "except FileNotFoundError: sys.exit('tool is not installed')\n"
        "target = state.get('target')\n"
        "if target is None: sys.exit('tool is not installed')\n"
        f"base = pathlib.Path({str(tool)!r}) / 'generations' / target\n"
        f"python = base / 'env' / {('Scripts/python.exe' if os.name == 'nt' else 'bin/python')!r}\n"
        "argv = [str(python), '-I', '-B', str(base / 'run.py'), *sys.argv[1:]]\n"
        "if os.name != 'nt': os.execv(str(python), argv)\n"
        "sys.exit(subprocess.call(argv))\n"
    ).encode()
    if os.name == "nt":
        wrapper = cmd_launcher(interpreter, loader).encode()
        command = bin_dir / (name + ".cmd")
    else:
        wrapper = f'#!/bin/sh\nexec {shlex.quote(interpreter)} -I {shlex.quote(str(loader))} "$@"\n'.encode()
        command = bin_dir / name
    return {loader: source, command: wrapper}


def check_owned(files: dict[Path, bytes], recorded: dict[str, str] | None = None) -> None:
    recorded = recorded or {}
    for path, expected in files.items():
        if path.is_symlink():
            raise ValueError(f"refusing foreign launcher: {path.name}")
        if path.exists():
            actual = path.read_bytes()
            if actual != expected and hashlib.sha256(actual).hexdigest() != recorded.get(str(path)):
                raise ValueError(f"refusing foreign launcher: {path.name}")


def ensure(files: dict[Path, bytes], recorded: dict[str, str] | None = None) -> None:
    check_owned(files, recorded)
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
