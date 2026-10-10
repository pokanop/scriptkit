"""Receipt-owned, restartable reclamation. Caller holds the installer lock."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from scriptkit.contracts.models import NAME
from scriptkit.state import StateConflict

from . import storage


def _value(path: Path) -> str:
    if path.is_symlink():
        return "link:" + os.readlink(path)
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def _junction(path: Path) -> bool:
    return path.exists() and getattr(path.lstat(), "st_reparse_tag", 0) == 0xA0000003


def _paths(root: Path) -> list[Path]:
    # Never walk through directory links (including Windows junctions).
    result = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in list(dirs):
            path = parent / name
            if _junction(path):
                raise ValueError(f"junction in generation: {path}")
            if path.is_symlink():
                dirs.remove(name)
                result.append(path)
        result.extend(parent / name for name in files)
    return sorted(result)


def resume(installer: Any, name: str) -> None:
    tool = installer._tool(name)
    journal_path = tool / "prune.json"
    journal = storage.read(journal_path)
    if journal is None:
        return
    generation = journal["generation"]
    if not isinstance(generation, str) or re.fullmatch(NAME, generation) is None:
        raise ValueError("invalid prune generation")
    source = tool / "generations" / generation
    trash = tool / "generations" / (".pruning-" + generation)
    if any(p.is_symlink() or _junction(p) for p in (tool, tool / "generations", source, trash)):
        raise ValueError("symlink prune state")
    if installer._active(name) == generation or storage.read(tool / "journal.json"):
        raise StateConflict("prune conflicts with activation")
    if source.exists():
        if trash.exists():
            raise StateConflict("prune destination exists")
        source.rename(trash)
    installer.checkpoint("prune-detach")
    owned = journal["files"]
    # Validate the whole surviving tree before deleting anything. Unexpected or
    # edited files remain untouched, even after an interrupted deletion.
    paths = _paths(trash)
    for path in paths:
        key = path.relative_to(trash).as_posix()
        if key not in owned or _value(path) != owned[key]:
            raise StateConflict(f"unowned or modified prune file: {path}")
    for path in paths:
        path.unlink()
        installer.checkpoint("prune-file")
    if trash.exists():
        for directory, _, _ in os.walk(trash, topdown=False):
            Path(directory).rmdir()
    installer.checkpoint("prune-cleanup")
    journal_path.unlink()


def prune(installer: Any, *, keep: int, uninstalled: bool, dry_run: bool) -> dict[str, Any]:
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        raise ValueError("keep must be at least 1")
    removed: list[str] = []
    foreign: list[str] = []
    size = 0
    candidates = []
    for tool in sorted(installer.root.iterdir()):
        if tool.name == "installer.lock":
            continue
        if (
            tool.is_symlink()
            or _junction(tool)
            or not tool.is_dir()
            or re.fullmatch(NAME, tool.name) is None
        ):
            foreign.append(str(tool))
            continue
        name = tool.name
        if (tool / "generations").is_symlink() or _junction(tool / "generations"):
            foreign.append(str(tool / "generations"))
            continue
        installer._tool(name)
        if storage.read(tool / "journal.json") or storage.read(tool / "prune.json"):
            raise StateConflict(f"pending operation for {name}; run recover {name} first")
        active = installer._active(name)
        protected = {active}
        previous = active
        for _ in range(keep):
            receipt = (
                storage.read(tool / "generations" / previous / "receipt.json") if previous else None
            )
            if previous and receipt is None:
                raise StateConflict(f"missing protected receipt: {previous}")
            previous = (
                receipt["resolved"]["installation"]["previous_generation"] if receipt else None
            )
            if previous:
                # Validate pointer before using it in a filesystem path.
                if re.fullmatch(NAME, previous) is None:
                    raise ValueError("invalid previous generation")
                protected.add(previous)
        launcher_paths = installer._ownership(name)
        for child in sorted(tool.iterdir()):
            if str(child) in launcher_paths and child.is_file() and not child.is_symlink():
                if _value(child) == "sha256:" + launcher_paths[str(child)]:
                    continue
            if child.name not in {"active.json", "launchers.json", "generations"}:
                foreign.append(str(child))
        generations = tool / "generations"
        if not generations.exists():
            foreign.append(str(tool))
            continue
        for generation in sorted(generations.iterdir()):
            if generation.is_symlink() or _junction(generation) or not generation.is_dir():
                foreign.append(str(generation))
                continue
            receipt_path = generation / "receipt.json"
            receipt = storage.read(receipt_path)
            paths = _paths(generation)
            if receipt is None:
                foreign.append(str(generation))
                continue
            inventory = receipt["files"]
            unexpected = [
                p
                for p in paths
                if p != receipt_path
                and (
                    p.relative_to(generation).as_posix() not in inventory
                    or _value(p) != inventory[p.relative_to(generation).as_posix()]
                )
            ]
            foreign.extend(str(p) for p in unexpected)
            if unexpected or generation.name in protected or (active is None and not uninstalled):
                continue
            if re.fullmatch(NAME, generation.name) is None:
                foreign.append(str(generation))
                continue
            owned = {p.relative_to(generation).as_posix(): _value(p) for p in paths}
            candidates.append((name, generation.name, owned))
            removed.extend(str(p) for p in paths)
            size += sum(p.lstat().st_size for p in paths)
    if not dry_run:
        for name, generation, owned in candidates:
            storage.publish(
                installer._tool(name) / "prune.json",
                {
                    "generation": generation,
                    "files": owned,
                },
            )
            installer.checkpoint("prune-journal")
            resume(installer, name)
    return {"paths": removed, "bytes": size, "foreign": foreign}
