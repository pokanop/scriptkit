"""Read-only previews and hash-based ownership. Plans carry optimistic base bytes."""

from __future__ import annotations

import difflib
import stat
from dataclasses import dataclass
from pathlib import Path

from scriptkit.contracts.codec import Record
from scriptkit.contracts.models import constrained, digest, path, record, unique, disjoint_paths

from .render import Rendered, canonical, sha256

CONTROL = ".scriptkit-generator"
MANIFEST = CONTROL + "/manifest.json"


@record
class OwnedFile(Record):
    path: str = path()
    sha256: str = digest()


@record
class Manifest(Record):
    schema_version: int = constrained(const=1)
    template: str = constrained(enum=["1.0.0"])
    formatter: str = constrained(enum=["text-lf-1"])
    spec_hash: str = digest()
    generated: tuple[OwnedFile, ...]
    user: tuple[str, ...]

    def validate(self) -> None:
        for value in self.user:
            OwnedFile(value, "0" * 64)
        paths = [f.path for f in self.generated] + list(self.user)
        unique(paths, "ownership paths")
        disjoint_paths(paths, "ownership paths")


def target(root: Path, name: str) -> Path:
    if name != MANIFEST:
        OwnedFile(name, "0" * 64)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("project root must be an existing non-symlink directory")
    # Check every ancestor, including ancestors of the supplied root.
    result = root / name
    for part in [result, *result.parents]:
        if part.is_symlink():
            raise ValueError(f"symlink path refused: {name}")
        if part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & (
            stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError(f"reparse point refused: {name}")
        if part != result and part.exists() and not part.is_dir():
            raise ValueError(f"parent is not a directory: {name}")
    return result


def read(root: Path, name: str) -> bytes | None:
    file = target(root, name)
    if file.exists() and not file.is_file():
        raise ValueError(f"not a regular file: {name}")
    return file.read_bytes() if file.exists() else None


@dataclass(frozen=True)
class Change:
    path: str
    before: bytes | None
    after: bytes | None
    conflict: bool = False

    @property
    def diff(self) -> str:
        return "".join(
            difflib.unified_diff(
                (self.before or b"").decode("utf-8", errors="replace").splitlines(True),
                (self.after or b"").decode("utf-8", errors="replace").splitlines(True),
                fromfile="current/" + self.path,
                tofile="proposed/" + self.path,
            )
        )


@dataclass(frozen=True)
class Plan:
    changes: tuple[Change, ...]
    # Include unchanged generated files so edits after preview are rejected too.
    bases: tuple[tuple[str, bytes | None], ...]

    @property
    def conflicts(self) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.conflict)

    @property
    def drift(self) -> bool:
        return bool(self.changes)

    def to_json(self) -> str:
        return canonical(
            {
                "schema_version": 1,
                "drift": self.drift,
                "conflicts": bool(self.conflicts),
                "changes": [
                    {
                        "path": c.path,
                        "conflict": c.conflict,
                        "diff": c.diff,
                        "before_sha256": sha256(c.before) if c.before is not None else None,
                        "after_sha256": sha256(c.after) if c.after is not None else None,
                    }
                    for c in self.changes
                ],
            }
        ).decode()


def preview(root: Path, rendered: Rendered) -> Plan:
    """Never writes, locks, recovers, executes formatters or imports user modules."""
    if (root / CONTROL / "journal.json").exists():
        raise ValueError("interrupted apply: recover before preview/check")
    previous = read(root, MANIFEST)
    manifest = Manifest.from_json(previous.decode()) if previous is not None else None
    if manifest and (manifest.template, manifest.formatter) != (
        rendered.template,
        rendered.formatter,
    ):
        raise ValueError("template/formatter migration requires an explicit supported migration")
    old = {f.path: f.sha256 for f in manifest.generated} if manifest else {}
    old_user = set(manifest.user) if manifest else set()
    generated = dict(rendered.generated)
    user = dict(rendered.user)
    # Ownership cannot silently change even when contents happen to match.
    if old_user & generated.keys() or old.keys() & user.keys():
        raise ValueError("ownership migration refused")
    next_manifest = Manifest(
        1,
        rendered.template,
        rendered.formatter,
        rendered.spec_hash,
        tuple(OwnedFile(p, sha256(b)) for p, b in sorted(generated.items())),
        tuple(sorted(old_user | user.keys())),
    )
    # Validate overlap with removed user paths before reading/writing anything.
    desired: dict[str, bytes | None] = {p: None for p in old.keys() - generated.keys()}
    desired.update(generated)
    bases: dict[str, bytes | None] = {}
    changes: list[Change] = []
    for name in sorted(desired.keys() | user.keys()):
        current = read(root, name)
        bases[name] = current
        if name in user:
            if current is None:
                changes.append(Change(name, None, user[name]))
            continue
        proposed = desired[name]
        conflict = (sha256(current) if current is not None else None) != old.get(name)
        if name not in old:
            conflict = current is not None  # Foreign files are never adopted implicitly.
        if current != proposed or conflict:
            changes.append(Change(name, current, proposed, conflict))
    after = next_manifest.canonical_json().encode() + b"\n"
    bases[MANIFEST] = previous
    if previous != after:
        changes.append(Change(MANIFEST, previous, after))
    return Plan(tuple(changes), tuple(sorted(bases.items())))
