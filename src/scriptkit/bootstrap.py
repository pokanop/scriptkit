"""Dependency-free recovery entry point, also executable as a downloaded file.

Never imports ScriptKit or modifies a running environment. The caller pins the
wheel hash out of band. Local state is private, trusted, user-owned storage.
"""

from __future__ import annotations

import argparse
from email.parser import Parser
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from typing import Any
import urllib.parse
import urllib.request
import uuid
import zipfile


def default_root() -> Path:
    return Path.home() / ".scriptkit"


def python_at(env: Path) -> Path:
    return env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def publish(path: Path, value: dict[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".publish-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def locked(root: Path) -> Iterator[None]:
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("manager root must not contain symlinks")
    root.mkdir(parents=True, exist_ok=True)
    for name in ("bootstrap.lock", "bin", "manager-generations", "manager-active.json"):
        if (root / name).is_symlink():
            raise ValueError("symlink manager state")
    with (root / "bootstrap.lock").open("a+b") as stream:
        stream.seek(0)
        if os.name == "nt":
            msvcrt = importlib.import_module("msvcrt")

            if not stream.read(1):
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def run(argv: list[str]) -> None:
    # Child output never pollutes machine-mode stdout.
    subprocess.run(argv, check=True, stdout=sys.stderr, stderr=sys.stderr)


def fetch(url: str) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("release URL must use HTTPS without credentials")
    with urllib.request.urlopen(url, timeout=60) as response:
        if urllib.parse.urlsplit(response.url).scheme != "https":
            raise ValueError("insecure release redirect")
        raw: bytes = response.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024:
        raise ValueError("manager wheel exceeds size limit")
    return raw


def exclusive(path: Path, content: bytes) -> None:
    if path.is_symlink() or (path.exists() and path.read_bytes() != content):
        raise ValueError(f"refusing unrelated file: {path}")
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o755)
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def launcher_files(root: Path) -> dict[Path, bytes]:
    # Stable dispatcher uses the system/base Python, not any manager generation.
    python = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
    loader = root / "manager-launch.py"
    source = (
        "import json, os, pathlib, re, subprocess, sys\n"
        f"root = pathlib.Path({str(root)!r})\n"
        "state = json.loads((root / 'manager-active.json').read_text(encoding='utf-8'))\n"
        "target = state['target']\n"
        "if not re.fullmatch('[a-f0-9]{32}', target): sys.exit('invalid manager pointer')\n"
        "base = root / 'manager-generations' / target\n"
        f"python = base / {('Scripts/python.exe' if os.name == 'nt' else 'bin/python')!r}\n"
        "argv = [str(python), '-I', '-X', 'utf8', '-m', 'scriptkit', *sys.argv[1:]]\n"
        "os.environ['SCRIPTKIT_ROOT'] = str(root)\n"
        "if os.name != 'nt': os.execv(str(python), argv)\n"
        "sys.exit(subprocess.call(argv))\n"
    ).encode()
    if os.name == "nt":
        if any(c in python + str(loader) for c in '%!\r\n"'):
            raise ValueError("Windows launcher paths cannot contain cmd expansion characters")
        wrapper = (
            "@echo off\r\nsetlocal\r\n"
            'for /f "tokens=2 delims=:" %%c in (\'chcp\') do set "_sk_cp=%%c"\r\n'
            "chcp 65001 >nul\r\n"
            f'"{python}" -I -X utf8 "{loader}" %*\r\n'
            'set "_sk_exit=%errorlevel%"\r\n'
            "chcp %_sk_cp% >nul\r\nexit /b %_sk_exit%\r\n"
        )
        name = "scriptkit.cmd"
    else:
        wrapper = (
            f'#!/bin/sh\nexec {shlex.quote(python)} -I -X utf8 {shlex.quote(str(loader))} "$@"\n'
        )
        name = "scriptkit"
    return {loader: source, root / "bin" / name: wrapper.encode()}


class Manager:
    def __init__(
        self,
        root: Path,
        *,
        execute: Callable[[list[str]], None] = run,
        download: Callable[[str], bytes] = fetch,
        checkpoint: Callable[[str], None] = lambda phase: None,
    ):
        self.root = Path(os.path.abspath(root))
        self.execute = execute
        self.download = download
        self.checkpoint = checkpoint

    def install(self, wheel: str, sha256: str, version: str) -> dict[str, Any]:
        if re.fullmatch(r"[a-f0-9]{64}", sha256) is None:
            raise ValueError("exact SHA-256 required")
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
            raise ValueError("exact stable version required")
        with locked(self.root):
            files = launcher_files(self.root)
            for path, content in files.items():
                if path.is_symlink() or (path.exists() and path.read_bytes() != content):
                    raise ValueError(f"refusing unrelated launcher: {path}")
            active = read(self.root / "manager-active.json")
            generation = uuid.uuid4().hex
            env = self.root / "manager-generations" / generation
            env.mkdir(parents=True)
            raw = self.download(wheel)
            if hashlib.sha256(raw).hexdigest() != sha256:
                raise ValueError("manager wheel hash mismatch")
            artifact = env / f"pokanop_scriptkit-{version}-py3-none-any.whl"
            artifact.write_bytes(raw)
            with zipfile.ZipFile(artifact) as archive:
                metadata = archive.read(f"pokanop_scriptkit-{version}.dist-info/METADATA").decode()
                headers = Parser().parsestr(metadata)
                if headers.get_all("Version") != [version] or headers.get_all("Name") != [
                    "pokanop-scriptkit"
                ]:
                    raise ValueError("manager wheel identity mismatch")
            self.checkpoint("fetch")
            self.execute([sys.executable, "-I", "-X", "utf8", "-m", "venv", str(env)])
            python = str(python_at(env))
            self.execute(
                [
                    python,
                    "-I",
                    "-X",
                    "utf8",
                    "-m",
                    "pip",
                    "--isolated",
                    "install",
                    "--no-index",
                    "--no-deps",
                    str(artifact),
                ]
            )
            self.checkpoint("stage")
            self.execute([python, "-I", "-X", "utf8", "-m", "scriptkit", "--version"])
            self.execute([python, "-I", "-X", "utf8", "-m", "scriptkit", "--help"])
            self.execute([python, "-I", "-X", "utf8", "-m", "scriptkit", "--json", "doctor"])
            self.checkpoint("smoke")
            receipt = {
                "schema_version": 1,
                "version": version,
                "sha256": sha256,
                "url": wheel,
                "generation": generation,
            }
            publish(env / "manager-receipt.json", receipt)
            for path, content in files.items():
                exclusive(path, content)
            self.checkpoint("launchers")
            # The only activation commit. Interrupted staging cannot affect old state.
            publish(
                self.root / "manager-active.json",
                {"target": generation, "previous": active.get("target")},
            )
            self.checkpoint("activate")
            return receipt

    def rollback(self) -> dict[str, Any]:
        with locked(self.root):
            active = read(self.root / "manager-active.json")
            previous = active.get("previous")
            if not isinstance(previous, str) or re.fullmatch("[a-f0-9]{32}", previous) is None:
                raise ValueError("no previous manager generation")
            env = self.root / "manager-generations" / previous
            receipt = read(env / "manager-receipt.json")
            if not receipt:
                raise ValueError("previous manager receipt missing")
            self.execute([str(python_at(env)), "-I", "-X", "utf8", "-m", "scriptkit", "--version"])
            publish(
                self.root / "manager-active.json",
                {"target": previous, "previous": active["target"]},
            )
            return receipt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install/repair an isolated ScriptKit manager (Python 3.11+)."
    )
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--wheel", help="HTTPS URL of a pinned manager wheel")
    parser.add_argument("--sha256")
    parser.add_argument("--version")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument(
        "--path-file", type=Path, help="opt-in new shell environment file; never edits profiles"
    )
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        parser.error("Python 3.11+ required; install Python and its venv module first")
    if not args.rollback and not all((args.wheel, args.sha256, args.version)):
        parser.error("--wheel, --sha256 and --version required (also for repair)")
    try:
        manager = Manager(args.root)
        result = (
            manager.rollback()
            if args.rollback
            else manager.install(args.wheel, args.sha256, args.version)
        )
        if args.path_file:
            bin_dir = str(manager.root / "bin")
            content = (
                (
                    "$env:PATH = '"
                    + bin_dir.replace("'", "''")
                    + "' + [IO.Path]::PathSeparator + $env:PATH\n"
                )
                if os.name == "nt"
                else f'export PATH={shlex.quote(bin_dir)}:"$PATH"\n'
            )
            exclusive(args.path_file, content.encode())
        print(json.dumps(result))
        print(
            f"Manager ready. PATH unchanged. Add {manager.root / 'bin'} to PATH or invoke its scriptkit launcher directly.",
            file=sys.stderr,
        )
        return 0
    except KeyboardInterrupt:
        return 130
    except subprocess.CalledProcessError as exc:
        print(
            "Bootstrap child failed; install Python 3.11+ with venv/ensurepip, then rerun to repair.",
            file=sys.stderr,
        )
        return exc.returncode if exc.returncode > 0 else 128 - exc.returncode
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"Bootstrap failed: {exc}. Rerun the pinned installer to repair.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
