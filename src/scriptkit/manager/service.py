"""Transactional per-tool installation, independent of bootstrap/self-update.

Generations are never moved after environment creation. Only a small JSON pointer
is replaced, so running Windows executables need not be renamed or removed.
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import re
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

from scriptkit.contracts.models import NAME
from scriptkit.contracts.archives import validate_archive
from scriptkit.contracts.catalog import ResolvedPlan
from scriptkit.contracts.ports import InstallationSource
from scriptkit.state import StateConflict

from . import launchers, storage
from .backend import PackageBackend, PipBackend, run
from .wheels import verify_locks


class Installer:
    def __init__(
        self,
        root: Path,
        bin_dir: Path,
        source: InstallationSource,
        *,
        backend: PackageBackend | None = None,
        checkpoint: Callable[[str], None] = lambda phase: None,
    ):
        self.root = root.absolute()
        self.bin_dir = bin_dir.absolute()
        self.source = source
        self.backend = backend or PipBackend()
        self.checkpoint = checkpoint
        for path in (self.root, self.bin_dir):
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise ValueError("installer paths must not contain symlinks")

    def _tool(self, name: str) -> Path:
        if re.fullmatch(NAME, name) is None:
            raise ValueError("invalid tool name")
        tool = self.root / name
        if tool.is_symlink() or (tool / "generations").is_symlink():
            raise ValueError("symlink tool state")
        return tool

    def _files(self, name: str) -> dict[Path, bytes]:
        return launchers.launchers(self._tool(name), self.bin_dir, name)

    def _active(self, name: str) -> str | None:
        state = storage.read(self._tool(name) / "active.json") or {"target": None}
        target = state["target"]
        if target is not None and (
            not isinstance(target, str) or re.fullmatch(NAME, target) is None
        ):
            raise ValueError("invalid generation pointer")
        return target

    def validate(self, resolved: ResolvedPlan) -> None:
        plan = resolved.installation
        # Revalidate serialized contracts even for injected callers.
        ResolvedPlan.from_json(resolved.canonical_json())
        actual_os = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}.get(
            platform.system()
        )
        actual_arch = {
            "amd64": "x86_64",
            "x86_64": "x86_64",
            "aarch64": "arm64",
            "arm64": "arm64",
        }.get(platform.machine().lower())
        if (plan.platform.os, plan.platform.arch) != (
            actual_os,
            actual_arch,
        ) or plan.python_version != platform.python_version():
            raise ValueError("plan must target this interpreter and host")
        if plan.destination != plan.release.tool.name:
            raise ValueError("destination must equal tool name in the private installer root")
        if any(b != "pip" for b in plan.backends):
            raise ValueError(
                "doctor: required system dependencies must be provisioned by the user; no system package installation"
            )
        paths = [a.path.casefold() for a in resolved.artifacts]
        if len(set(paths)) != len(paths):
            raise ValueError("artifact paths collide")
        launchers.check_owned(self._files(plan.release.tool.name))

    def install(
        self,
        resolved: ResolvedPlan,
        *,
        dry_run: bool = False,
        smoke_args: tuple[str, ...] = ("--help",),
    ) -> Path | None:
        self.checkpoint("plan")
        self.validate(resolved)
        self.checkpoint("validate")
        if dry_run:
            self.source.authorize(resolved)
            return None
        plan = resolved.installation
        name = plan.release.tool.name
        tool = self._tool(name)
        with storage.locked(self.root):
            self._recover(name)
            if self._active(name) != plan.previous_generation:
                raise StateConflict("plan lineage is stale")
            active = self._active(name)
            if active is not None:
                old = storage.read(tool / "generations" / active / "receipt.json")
                if old is None or (
                    old["resolved"]["registry"]["namespace"],
                    old["resolved"]["registry"]["origin"],
                ) != (resolved.registry.namespace, resolved.registry.origin):
                    raise ValueError("namespace cannot take ownership of another registry's tool")
            self.source.authorize(resolved)
            artifacts = [(a, self.source.fetch(a)) for a in resolved.artifacts]
            for artifact, raw in artifacts:
                if len(raw) != artifact.size or hashlib.sha256(raw).hexdigest() != artifact.sha256:
                    raise ValueError("artifact hash/size mismatch")
                validate_archive(artifact.path, raw)
            verify_locks(plan, dict(artifacts))
            self.checkpoint("fetch")
            generation = tool / "generations" / plan.generation
            generation.mkdir(parents=True, exist_ok=False)
            # A failed staging directory is retained, never recursively removed: it
            # cannot become active without a complete receipt and successful smoke.
            wheel_dir = generation / "artifacts"
            wheel_dir.mkdir()
            wheels: list[Path] = []
            for artifact, raw in artifacts:
                path = wheel_dir / artifact.path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                if artifact.path.endswith(".whl"):
                    wheels.append(path)
            python = self.backend.stage(generation / "env", tuple(wheels))
            source = generation / "source"
            source.mkdir()
            if resolved.source_kind == "legacy-scripts":
                with zipfile.ZipFile(io.BytesIO(artifacts[0][1])) as archive:
                    archive.extractall(
                        source
                    )  # every member validated above; fresh private directory
            module, function = plan.release.tool.entrypoint.split(":")
            runner = generation / "run.py"
            runner.write_text(
                "import sys\n"
                + f"sys.path.insert(0, {str(source)!r})\n"
                + f"from {module} import {function}\n"
                + f"sys.exit({function}())\n",
                encoding="utf-8",
            )
            self.checkpoint("stage")
            run([str(python), "-I", "-B", str(runner), *smoke_args])
            self.checkpoint("smoke")
            receipt = {
                "schema_version": 1,
                "resolved": resolved.to_dict(),
                "installed_at": int(time.time()),
                "files": self._inventory(generation),
                "launchers": {
                    str(p): hashlib.sha256(b).hexdigest() for p, b in self._files(name).items()
                },
            }
            storage.publish(generation / "receipt.json", receipt)
            self._activate(name, plan.generation)
            return generation / "receipt.json"

    @staticmethod
    def _inventory(root: Path) -> dict[str, str]:
        result: dict[str, str] = {}
        for path in root.rglob("*"):
            if path.is_symlink():
                # uv/venv interpreter links are owned links, not their external targets.
                value = "link:" + os.readlink(path)
            elif path.is_file() and path != root / "receipt.json":
                value = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                continue
            result[path.relative_to(root).as_posix()] = value
        return result

    def _activate(self, name: str, target: str | None) -> None:
        tool = self._tool(name)
        launchers.check_owned(self._files(name))
        if target is not None:
            self._verify_generation(tool, target)
        storage.publish(tool / "journal.json", {"target": target, "previous": self._active(name)})
        self.checkpoint("journal")
        self._recover(name)
        self.checkpoint("activate")

    def _recover(self, name: str) -> None:
        tool = self._tool(name)
        journal = storage.read(tool / "journal.json")
        if journal is None:
            return
        target = journal["target"]
        if target is not None:
            if not isinstance(target, str) or re.fullmatch(NAME, target) is None:
                raise ValueError("invalid journal target")
            self._verify_generation(tool, target)
            launchers.ensure(self._files(name))
        else:
            launchers.check_owned(self._files(name))
        storage.publish(tool / "active.json", {"target": target})
        self.checkpoint("pointer")
        if target is None:
            for path in self._files(name):
                path.unlink(missing_ok=True)
        (tool / "journal.json").unlink()

    def _verify_generation(self, tool: Path, target: str) -> None:
        if re.fullmatch(NAME, target) is None:
            raise ValueError("invalid generation")
        generation = tool / "generations" / target
        if generation.is_symlink():
            raise ValueError("symlink generation")
        receipt = storage.read(generation / "receipt.json")
        if receipt is None or receipt["files"] != self._inventory(generation):
            raise ValueError("generation changed or incomplete")

    def recover(self, name: str) -> None:
        with storage.locked(self.root):
            self._recover(name)

    def rollback(self, name: str) -> None:
        with storage.locked(self.root):
            self._recover(name)
            active = self._active(name)
            if active is None:
                raise ValueError("tool is not installed")
            receipt = storage.read(self._tool(name) / "generations" / active / "receipt.json")
            if receipt is None:
                raise ValueError("missing receipt")
            previous = receipt["resolved"]["installation"]["previous_generation"]
            if previous is None:
                raise ValueError("no previous generation")
            self._activate(name, previous)

    def uninstall(self, name: str) -> None:
        """Deactivate and remove only owned launchers; retain generations/data/config.

        Retention makes uninstall safe for running Windows processes and enables
        explicit recovery. Garbage collection is a separate administrative operation.
        """
        with storage.locked(self.root):
            self._recover(name)
            self._activate(name, None)
