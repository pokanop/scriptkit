"""Exact namespace/version resolution; no package-index fallback or install hooks."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path

from scriptkit.contracts.artifacts import ArtifactPolicy
from scriptkit.contracts.models import Artifact

from scriptkit.contracts.codec import ContractError
from scriptkit.contracts.catalog import ResolvedPlan as ResolvedPlan
from scriptkit.contracts.models import (
    CatalogRelease,
    InstallPlan,
    Platform,
)

from .archives import validate_archive
from .cache import VerifiedCache
from .trust import Registry, RegistryStore


class Resolver:
    def __init__(
        self,
        store: RegistryStore,
        cache: VerifiedCache,
        *,
        clock: Callable[[], float] = time.time,
        policy: ArtifactPolicy | None = None,
        artifact_directory: Path | None = None,
    ):
        self.store = store
        self.cache = cache
        self.clock = clock
        self.policy = policy
        self.artifact_directory = artifact_directory

    def _validate_artifact(self, origin: str, artifact: Artifact, offline: bool) -> str:
        if self.policy is not None and artifact.size > self.policy.max_archive_bytes:
            raise ContractError("artifact exceeds source policy")
        if self.artifact_directory is None:
            return validate_archive(
                artifact.path, self.cache.get(origin, artifact, offline=offline), policy=self.policy
            )
        path = self.artifact_directory / artifact.sha256
        if path.is_symlink() or not path.is_file() or path.stat().st_size != artifact.size:
            raise ContractError("local artifact missing or unsafe size")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != artifact.sha256:
            raise ContractError("artifact SHA-256 mismatch")
        return validate_archive(artifact.path, path, policy=self.policy)

    def _catalog(self, namespace: str, offline: bool) -> tuple[Registry, CatalogRelease]:
        registry = next((r for r in self.store.list() if r.namespace == namespace), None)
        if registry is None:
            raise ContractError("unknown registry namespace; no fallback permitted")
        if self.clock() >= registry.expires_at:
            raise ContractError("registry metadata expired (including offline use)")
        if registry.catalog.size > 4 * 1024 * 1024:
            raise ContractError("catalog exceeds metadata policy")
        raw = self.cache.get(registry.origin, registry.catalog, offline=offline)
        catalog = CatalogRelease.from_json(raw.decode("utf-8"))
        if catalog.name != registry.namespace:
            raise ContractError("catalog namespace shadowing")
        return registry, catalog

    def list(self, namespace: str, *, offline: bool = False) -> tuple[str, ...]:
        _, catalog = self._catalog(namespace, offline)
        return tuple(f"{namespace}/{r.tool.name}@{r.tool.version}" for r in catalog.releases)

    def resolve(
        self,
        qualified: str,
        *,
        platform: Platform,
        python_version: str,
        generation: str,
        destination: str,
        previous_generation: str | None = None,
        offline: bool = False,
    ) -> ResolvedPlan:
        try:
            namespace, target = qualified.split("/")
            name, version = target.split("@")
        except ValueError as exc:
            raise ContractError("use namespace/tool@exact-version") from exc
        registry, catalog = self._catalog(namespace, offline)
        release = next(
            (r for r in catalog.releases if (r.tool.name, r.tool.version) == (name, version)),
            None,
        )
        if release is None:
            raise ContractError("unknown exact tool version; no fallback permitted")
        locks = tuple(lock for lock in release.locks if lock.platform == platform)
        installation = InstallPlan(
            1,
            generation,
            previous_generation,
            registry.catalog.sha256,
            release,
            platform,
            python_version,
            tuple(lock.backend for lock in locks),
            destination,
        )
        artifacts = (release.artifact, *(p.artifact for lock in locks for p in lock.packages))
        # All transitive packages must already be enumerated in the trusted lock.
        # No resolver or package backend runs here; index access is never implicit.
        source_kind = self._validate_artifact(registry.origin, release.artifact, offline)
        for lock in locks:
            for package in lock.packages:
                if lock.backend == "pip":
                    if not package.artifact.path.endswith(".whl"):
                        raise ContractError("pip dependencies must be locked wheels")
                    self._validate_artifact(registry.origin, package.artifact, offline)
                else:
                    self.cache.get(registry.origin, package.artifact, offline=offline)
        return ResolvedPlan(
            1,
            registry,
            installation,
            source_kind,
            tuple(hashlib.sha256(lock.canonical_json().encode()).hexdigest() for lock in locks),
            artifacts,
        )
