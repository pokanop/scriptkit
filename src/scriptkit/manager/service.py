"""Transactional per-tool installation, independent of bootstrap/self-update.

Generations are never moved after environment creation. Only a small JSON pointer
is replaced, so running Windows executables need not be renamed or removed.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

from scriptkit.contracts.artifacts import ArtifactPolicy, DEFAULT_ARTIFACT_POLICY
from scriptkit.contracts.models import NAME
from scriptkit.contracts.archives import validate_archive
from scriptkit.contracts.catalog import ResolvedPlan
from scriptkit.contracts.ports import InstallationSource, StreamingInstallationSource
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
        policy: ArtifactPolicy | None = None,
        launcher_interpreter: str | None = None,
        checkpoint: Callable[[str], None] = lambda phase: None,
    ):
        self.root = Path(os.path.abspath(root))
        self.bin_dir = Path(os.path.abspath(bin_dir))
        self.source = source
        self.policy = policy
        self.command_timeout = (policy or DEFAULT_ARTIFACT_POLICY).command_timeout
        self.backend = backend or PipBackend(timeout=self.command_timeout)
        self.launcher_interpreter = launcher_interpreter
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
        return launchers.launchers(
            self._tool(name), self.bin_dir, name, interpreter=self.launcher_interpreter
        )

    def _ownership(self, name: str) -> dict[str, str]:
        tool = self._tool(name)
        record = storage.read(tool / "launchers.json")
        if record is None:
            # Compatibility with receipts published before the independent ledger.
            active = self._active(name)
            record = (
                storage.read(tool / "generations" / active / "receipt.json") if active else None
            )
        return {
            os.path.abspath(p): digest for p, digest in (record or {}).get("launchers", {}).items()
        }

    def _launcher_digests(self, name: str) -> dict[str, str]:
        files = self._files(name)
        launchers.check_owned(files, self._ownership(name))
        return {
            str(p): hashlib.sha256(p.read_bytes() if p.exists() else data).hexdigest()
            for p, data in files.items()
        }

    def _preflight(self, resolved: ResolvedPlan) -> None:
        plan = resolved.installation
        tool = self._tool(plan.release.tool.name)
        if storage.read(tool / "journal.json") is not None:
            raise StateConflict("pending activation; recover before dry-run")
        active = self._active(plan.release.tool.name)
        if active != plan.previous_generation:
            raise StateConflict("plan lineage is stale")
        if active is not None:
            old = storage.read(tool / "generations" / active / "receipt.json")
            if old is None or (
                old["resolved"]["registry"]["namespace"],
                old["resolved"]["registry"]["origin"],
            ) != (resolved.registry.namespace, resolved.registry.origin):
                raise ValueError("namespace cannot take ownership of another registry's tool")
        generation = tool / "generations" / plan.generation
        if generation.exists() or generation.is_symlink():
            raise FileExistsError("generation already exists; choose an unused generation")

    def _active(self, name: str) -> str | None:
        state = storage.read(self._tool(name) / "active.json") or {"target": None}
        target = state["target"]
        if target is not None and (
            not isinstance(target, str) or re.fullmatch(NAME, target) is None
        ):
            raise ValueError("invalid generation pointer")
        return target

    def current_generation(self, name: str) -> str | None:
        """Return lineage for planning; installation rechecks under its lock."""
        return self._active(name)

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
        launchers.check_owned(
            self._files(plan.release.tool.name), self._ownership(plan.release.tool.name)
        )

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
            self._preflight(resolved)
            self.source.authorize(resolved)
            return None
        plan = resolved.installation
        name = plan.release.tool.name
        tool = self._tool(name)
        with storage.locked(self.root):
            self._recover(name)
            self._preflight(resolved)
            self.source.authorize(resolved)
            generation = tool / "generations" / plan.generation
            generation.mkdir(mode=0o700, parents=True, exist_ok=False)
            # A failed staging directory is retained, never recursively removed: it
            # cannot become active without a complete receipt and successful smoke.
            wheel_dir = generation / "artifacts"
            wheel_dir.mkdir()
            wheels: list[Path] = []
            artifacts = {}
            for artifact in resolved.artifacts:
                if self.policy is not None and artifact.size > self.policy.max_archive_bytes:
                    raise ValueError("artifact exceeds download policy")
                path = wheel_dir / artifact.path
                path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(self.source, StreamingInstallationSource):
                    self.source.fetch_into(artifact, path)
                else:
                    # Compatibility for existing byte sources: only one artifact
                    # is resident, never the entire dependency set.
                    path.write_bytes(self.source.fetch(artifact))
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if path.stat().st_size != artifact.size or digest != artifact.sha256:
                    raise ValueError("artifact hash/size mismatch")
                validate_archive(artifact.path, path, policy=self.policy)
                artifacts[artifact] = path
                if artifact.path.endswith(".whl"):
                    wheels.append(path)
            verify_locks(plan, artifacts)
            self.checkpoint("fetch")
            python = self.backend.stage(generation / "env", tuple(wheels))
            source = generation / "source"
            source.mkdir()
            if resolved.source_kind == "legacy-scripts":
                with zipfile.ZipFile(artifacts[resolved.artifacts[0]]) as archive:
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
            run(
                [str(python), "-I", "-B", str(runner), *smoke_args],
                timeout=self.command_timeout,
            )
            self.checkpoint("smoke")
            receipt = {
                "schema_version": 1,
                "resolved": resolved.to_dict(),
                "installed_at": int(time.time()),
                "files": self._inventory(generation),
                "launchers": self._launcher_digests(name),
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
                with path.open("rb") as stream:
                    value = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
            else:
                continue
            result[path.relative_to(root).as_posix()] = value
        return result

    def _activate(self, name: str, target: str | None) -> None:
        tool = self._tool(name)
        launchers.check_owned(self._files(name), self._ownership(name))
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
            # Persist the actual accepted bytes (or bytes about to be published)
            # before linking: recovery can recognize partially published launchers.
            digests = self._launcher_digests(name)
            storage.publish(tool / "launchers.json", {"launchers": digests})
            launchers.ensure(self._files(name), digests)
        else:
            launchers.check_owned(self._files(name), self._ownership(name))
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
        if not self._tool(name).exists():
            return
        with storage.locked(self.root):
            self._recover(name)
            if self._active(name) is not None or any(p.exists() for p in self._files(name)):
                self._activate(name, None)
