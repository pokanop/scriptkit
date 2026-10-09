"""Narrow injected ports. Implementations live in their owning upper layers.

Methods are synchronous, propagate cancellation (KeyboardInterrupt), and must
not translate cancellation into success. No implicit global state or credentials.
All filesystem paths are relative to an adapter-owned, confined root.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from .catalog import ResolvedPlan

from .models import AIProposal, Artifact, CatalogRelease, DependencyLock, ToolSpec


class ArtifactSource(Protocol):
    def catalog(self, name: str, version: str) -> CatalogRelease:
        """Load an exact catalog, locally or remotely; never implicitly latest."""
        ...

    def fetch(self, artifact: Artifact) -> bytes:
        """Return bytes verified against artifact size and SHA-256 or raise."""
        ...


class InstallationSource(Protocol):
    def authorize(self, plan: ResolvedPlan) -> None:
        """Recheck current trust and pin policy; reject stale or unregistered plans."""
        ...

    def fetch(self, artifact: Artifact) -> bytes: ...


@runtime_checkable
class StreamingInstallationSource(InstallationSource, Protocol):
    """Optional additive extension; legacy byte-returning sources stay valid."""

    def fetch_into(self, artifact: Artifact, destination: Path) -> None:
        """Write bounded, verified bytes to a fresh caller-owned private path.

        Reject size/hash mismatches; never write more than the declared size.
        The installer independently verifies the completed private copy.
        """
        ...


class EnvironmentBackend(Protocol):
    def stage(self, environment: Path, wheels: tuple[Path, ...]) -> Path:
        """Create an offline environment at its final path; return its interpreter."""
        ...


class PackageBackend(Protocol):
    def available(self, lock: DependencyLock) -> bool:
        """Check backend/platform compatibility without modifying the host."""
        ...

    def install(self, lock: DependencyLock, destination: str) -> tuple[Artifact, ...]:
        """Install exact locked versions into a staged root; return file inventory.

        Never activate a generation. On error the manager owns staged cleanup.
        """
        ...


class FileSystem(Protocol):
    def read_bytes(self, path: str) -> bytes: ...
    def write_atomic(self, path: str, data: bytes) -> None:
        """Replace one file atomically; failure preserves its previous contents."""
        ...

    def exists(self, path: str) -> bool: ...
    def remove_tree(self, path: str) -> None:
        """Delete a confined staging root; missing paths are harmless."""
        ...

    def activate(self, staged: str, active: str) -> None:
        """Atomically replace the active pointer; no partial visible generation."""
        ...


class Clock(Protocol):
    def unix_seconds(self) -> int: ...


class Output(Protocol):
    def emit(self, level: str, message: str) -> None:
        """Present an event; adapters own formatting and secret redaction."""
        ...


class ProposalProvider(Protocol):
    def propose(self, request_id: str, prompt: str, base: ToolSpec | None) -> AIProposal:
        """Return untrusted data for validation/review, never execute or install."""
        ...
