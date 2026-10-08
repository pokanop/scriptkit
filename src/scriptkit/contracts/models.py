"""Version 1 data-only contracts. No imports from orchestration or providers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from collections.abc import Hashable, Sequence
from typing import Any, TypeVar, dataclass_transform

from .codec import ContractError, Record


def constrained(**rules: Any) -> Any:
    return field(metadata=rules)


# Full-match in Python; explicit end assertion also works in JSON Schema engines.
END = r"$(?![\s\S])"
NAME = r"^(?!(?:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$))[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*" + END
COMPONENT = (
    r"(?!(?:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|/|$))[a-z0-9_](?:[a-z0-9_.-]*[a-z0-9_-])?"
)
PATH = "^" + COMPONENT + "(?:/" + COMPONENT + ")*" + END
INVENTORY_COMPONENT = r"(?!(?:[cC][oO][nN]|[pP][rR][nN]|[aA][uU][xX]|[nN][uU][lL]|[cC][oO][mM][0-9]|[lL][pP][tT][0-9])(?:\.|/|$))[A-Za-z0-9_](?:[A-Za-z0-9_.+@-]*[A-Za-z0-9_+@-])?"
INVENTORY_PATH = "^" + INVENTORY_COMPONENT + "(?:/" + INVENTORY_COMPONENT + ")*" + END
SEMVER = (
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    + END
)
PYTHON = r"^3\.(?:[1-9][0-9])\.(?:0|[1-9][0-9]*)" + END
HASH = r"^[0-9a-f]{64}" + END
MODULE = r"^[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*:[a-z_][a-z0-9_]*" + END


def name() -> Any:
    return constrained(
        pattern=NAME,
        maxLength=64,
        description="expected portable lowercase identifier (not a Windows device name)",
    )


def version() -> Any:
    return constrained(
        pattern=SEMVER, maxLength=128, description="expected SemVer 2.0 (for example 1.2.3)"
    )


def path() -> Any:
    return constrained(
        pattern=PATH,
        maxLength=240,
        description="expected portable lowercase relative POSIX path; no traversal, devices or trailing dots",
    )


def digest() -> Any:
    return constrained(pattern=HASH, description="expected lowercase SHA-256, 64 hex characters")


def unique(values: Sequence[Hashable], label: str) -> None:
    if len(values) != len(set(values)):
        raise ContractError(f"{label}: duplicate entries")


def disjoint_paths(paths: Sequence[str], label: str) -> None:
    entries = set(paths)
    for value in paths:
        parts = value.split("/")
        if any("/".join(parts[:i]) in entries for i in range(1, len(parts))):
            raise ContractError(f"{label}: a file/root cannot also be a parent directory")


T = TypeVar("T", bound=Record)


@dataclass_transform(
    frozen_default=True, field_specifiers=(constrained, name, version, path, digest)
)
def record(cls: type[T]) -> type[T]:
    return dataclass(frozen=True)(cls)


def py_tuple(value: str) -> tuple[int, ...]:
    return tuple(map(int, value.split(".")))


@record
class PythonRequirement(Record):
    minimum: str = constrained(pattern=PYTHON, description="expected Python 3.x.y release")
    maximum_exclusive: str = constrained(
        pattern=PYTHON, description="expected Python 3.x.y release"
    )

    def validate(self) -> None:
        if py_tuple(self.minimum) < (3, 11, 0):
            raise ContractError("minimum: ScriptKit requires Python >=3.11.0")
        if py_tuple(self.minimum) >= py_tuple(self.maximum_exclusive):
            raise ContractError("maximum_exclusive: must exceed minimum")

    def contains(self, value: str) -> bool:
        return py_tuple(self.minimum) <= py_tuple(value) < py_tuple(self.maximum_exclusive)


@record
class Platform(Record):
    os: str = constrained(enum=["linux", "macos", "windows"])
    arch: str = constrained(enum=["x86_64", "arm64"])


@record
class ArgumentSpec(Record):
    schema_version: int = constrained(const=1)
    name: str = name()
    kind: str = constrained(enum=["positional", "option", "flag"])
    value_type: str = constrained(enum=["string", "integer", "boolean"])
    required: bool
    default: str | int | bool | None
    choices: tuple[str, ...]
    help: str = constrained(maxLength=4096)

    def validate(self) -> None:
        expected = {"string": str, "integer": int, "boolean": bool}[self.value_type]
        if self.default is not None and type(self.default) is not expected:
            raise ContractError("default: must match value_type")
        if self.required and self.default is not None:
            raise ContractError("default: required arguments cannot have defaults")
        if self.kind == "flag" and (self.value_type != "boolean" or self.required):
            raise ContractError("kind: flags must be optional booleans")
        if self.kind != "flag" and self.value_type == "boolean":
            raise ContractError("value_type: boolean arguments must be flags")
        if self.choices and self.value_type != "string":
            raise ContractError("choices: only supported for strings")
        unique(self.choices, "choices")
        if self.choices and self.default is not None and self.default not in self.choices:
            raise ContractError("default: must be one of choices")


@record
class CommandSpec(Record):
    schema_version: int = constrained(const=1)
    name: str = name()
    help: str = constrained(maxLength=4096)
    arguments: tuple[ArgumentSpec, ...]

    def validate(self) -> None:
        unique([a.name for a in self.arguments], "arguments.name")
        optional_seen = False
        for arg in self.arguments:
            if arg.kind == "positional":
                if optional_seen and arg.required:
                    raise ContractError(
                        "arguments: required positional follows optional positional"
                    )
                optional_seen |= not arg.required


@record
class ToolSpec(Record):
    schema_version: int = constrained(const=1)
    name: str = name()
    version: str = version()
    description: str = constrained(maxLength=4096)
    entrypoint: str = constrained(
        pattern=MODULE,
        description="expected module.path:callable reference, never code or a shell command",
    )
    python: PythonRequirement
    platforms: tuple[Platform, ...] = constrained(minItems=1)
    commands: tuple[CommandSpec, ...] = constrained(minItems=1)

    def validate(self) -> None:
        unique(self.platforms, "platforms")
        unique([command.name for command in self.commands], "commands.name")


@record
class Artifact(Record):
    path: str = constrained(
        pattern=INVENTORY_PATH,
        maxLength=240,
        description="expected case-preserving relative POSIX path; no traversal, devices or trailing dots",
    )
    sha256: str = digest()
    size: int = constrained(minimum=0)


@record
class LockedPackage(Record):
    name: str = constrained(
        maxLength=128,
        pattern=r"^[a-z0-9][a-z0-9+@._-]*" + END,
        description="expected normalized package identifier, not a path or command",
    )
    version: str = constrained(
        minLength=1,
        maxLength=128,
        pattern=r"^[0-9][A-Za-z0-9.+:~_-]*" + END,
        description="expected exact numeric-leading backend version, not a range, tag or command",
    )
    artifact: Artifact


@record
class DependencyLock(Record):
    schema_version: int = constrained(const=1)
    platform: Platform
    python: PythonRequirement
    backend: str = constrained(enum=["pip", "apt", "brew", "winget"])
    packages: tuple[LockedPackage, ...]

    def validate(self) -> None:
        supported = {
            "pip": {"linux", "macos", "windows"},
            "apt": {"linux"},
            "brew": {"macos"},
            "winget": {"windows"},
        }
        if self.platform.os not in supported[self.backend]:
            raise ContractError("backend: incompatible with platform.os")
        if self.backend == "pip":
            for package in self.packages:
                if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", package.name) is None:
                    raise ContractError(
                        "packages.name: pip requires PEP 503 normalized names "
                        "(lowercase alphanumeric components separated by single hyphens)"
                    )
        unique([package.name for package in self.packages], "packages.name")
        unique([package.artifact.path for package in self.packages], "packages.artifact.path")


@record
class ToolRelease(Record):
    tool: ToolSpec
    artifact: Artifact
    locks: tuple[DependencyLock, ...] = constrained(minItems=1)

    def validate(self) -> None:
        unique([(lock.platform, lock.backend) for lock in self.locks], "locks platform/backend")
        if set(lock.platform for lock in self.locks) != set(self.tool.platforms):
            raise ContractError("locks: must cover exactly tool.platforms")
        for lock in self.locks:
            if py_tuple(lock.python.minimum) < py_tuple(self.tool.python.minimum) or py_tuple(
                lock.python.maximum_exclusive
            ) > py_tuple(self.tool.python.maximum_exclusive):
                raise ContractError("locks.python: must be a subset of tool.python")
        for platform in self.tool.platforms:
            ranges = [lock.python for lock in self.locks if lock.platform == platform]
            if max(py_tuple(r.minimum) for r in ranges) >= min(
                py_tuple(r.maximum_exclusive) for r in ranges
            ):
                raise ContractError(
                    "locks.python: platform backend requirements have no common Python version"
                )


@record
class CatalogRelease(Record):
    schema_version: int = constrained(const=1)
    name: str = name()
    version: str = version()
    releases: tuple[ToolRelease, ...] = constrained(minItems=1)

    def validate(self) -> None:
        unique(
            [(release.tool.name, release.tool.version) for release in self.releases],
            "releases name/version",
        )


@record
class InstallPlan(Record):
    schema_version: int = constrained(const=1)
    generation: str = name()
    previous_generation: str | None = name()
    catalog_sha256: str = digest()
    release: ToolRelease
    platform: Platform
    python_version: str = constrained(pattern=PYTHON, description="expected Python 3.x.y release")
    backends: tuple[str, ...] = constrained(minItems=1)
    destination: str = path()

    def validate(self) -> None:
        if self.previous_generation == self.generation:
            raise ContractError("previous_generation: must differ from generation")
        if self.platform not in self.release.tool.platforms:
            raise ContractError("platform: unsupported by release")
        if not self.release.tool.python.contains(self.python_version):
            raise ContractError("python_version: outside tool requirements")
        unique(self.backends, "backends")
        locks = [lock for lock in self.release.locks if lock.platform == self.platform]
        if set(self.backends) != {lock.backend for lock in locks}:
            raise ContractError("backends: must select all locks for the target platform")
        if any(not lock.python.contains(self.python_version) for lock in locks):
            raise ContractError("python_version: outside dependency lock requirements")


@record
class Receipt(Record):
    schema_version: int = constrained(const=1)
    plan: InstallPlan
    installed_at: int = constrained(minimum=0, description="UTC Unix timestamp in seconds")
    files: tuple[Artifact, ...] = constrained(minItems=1)

    def validate(self) -> None:
        paths = [artifact.path.casefold() for artifact in self.files]
        unique(paths, "files.path (casefold)")
        disjoint_paths(paths, "files.path (casefold)")


@record
class Generation(Record):
    schema_version: int = constrained(const=1)
    name: str = name()
    previous: str | None = constrained(
        pattern=NAME, maxLength=64, description="expected portable lowercase identifier"
    )
    receipts: tuple[Receipt, ...] = constrained(minItems=1)

    def validate(self) -> None:
        if self.name == self.previous:
            raise ContractError("previous: must differ from generation name")
        if len({r.plan.platform for r in self.receipts}) != 1:
            raise ContractError("receipts.plan.platform: generation must target one host platform")
        # Per-tool environments may deliberately use different compatible Python versions.
        unique([r.plan.release.tool.name for r in self.receipts], "receipts tool.name")
        unique([r.plan.destination for r in self.receipts], "receipts destination")
        for receipt in self.receipts:
            if (
                receipt.plan.generation != self.name
                or receipt.plan.previous_generation != self.previous
            ):
                raise ContractError("receipts.plan: generation lineage must match")
        disjoint_paths([r.plan.destination for r in self.receipts], "receipts destination")


@record
class AIProposal(Record):
    schema_version: int = constrained(const=1)
    request_id: str = name()
    provider: str = name()
    model: str = constrained(minLength=1, maxLength=128)
    base_sha256: str | None = digest()
    rationale: str = constrained(minLength=1, maxLength=8192)
    spec: ToolSpec
    # Explicitly non-executable: acceptance is a separate manager/user action.


CONTRACTS = (
    ArgumentSpec,
    CommandSpec,
    ToolSpec,
    DependencyLock,
    CatalogRelease,
    InstallPlan,
    Receipt,
    Generation,
    AIProposal,
)
