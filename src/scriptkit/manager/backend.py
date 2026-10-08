"""Offline package adapters: dependency resolution belongs to the package backend."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from scriptkit.contracts.ports import EnvironmentBackend as PackageBackend
from scriptkit.execution import BoundedRunner

__all__ = ["PackageBackend", "PipBackend", "UvBackend"]


def run(argv: list[str]) -> None:
    # No user pip configuration, implicit indexes, user site or inherited Python paths.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "PIP_", "UV_"))}
    env.update(PYTHONNOUSERSITE="1", PIP_CONFIG_FILE=os.devnull)
    BoundedRunner().run(argv, check=True, timeout=120, env=env)


def interpreter(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


class PipBackend:
    """stdlib venv + bundled pip: no external executable or bootstrap download."""

    def stage(self, environment: Path, wheels: tuple[Path, ...]) -> Path:
        run([sys.executable, "-I", "-m", "venv", "--copies", str(environment)])
        python = interpreter(environment)
        if wheels:
            run(
                [
                    str(python),
                    "-I",
                    "-m",
                    "pip",
                    "--isolated",
                    "install",
                    "--no-index",
                    "--no-deps",
                    "--only-binary=:all:",
                    "--no-compile",
                    *map(str, wheels),
                ]
            )
            run([str(python), "-I", "-m", "pip", "--isolated", "check"])
        return python


class UvBackend:
    """Optional adapter; uv must already be provisioned by the host, not downloaded."""

    def __init__(self, executable: Path):
        self.executable = executable.resolve(strict=True)

    def stage(self, environment: Path, wheels: tuple[Path, ...]) -> Path:
        run(
            [
                str(self.executable),
                "--no-config",
                "venv",
                "--offline",
                "--no-python-downloads",
                "--python",
                sys.executable,
                str(environment),
            ]
        )
        python = interpreter(environment)
        if wheels:
            run(
                [
                    str(self.executable),
                    "--no-config",
                    "pip",
                    "install",
                    "--offline",
                    "--no-index",
                    "--no-deps",
                    "--python",
                    str(python),
                    *map(str, wheels),
                ]
            )
            run([str(self.executable), "--no-config", "pip", "check", "--python", str(python)])
        return python
