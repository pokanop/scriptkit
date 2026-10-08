"""Exact namespace/version resolution; no package-index fallback or install hooks."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable

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
    ):
        self.store = store
        self.cache = cache
        self.clock = clock

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
        source_kind = validate_archive(
            release.artifact.path,
            self.cache.get(registry.origin, release.artifact, offline=offline),
        )
        for lock in locks:
            for package in lock.packages:
                raw = self.cache.get(registry.origin, package.artifact, offline=offline)
                if lock.backend == "pip":
                    if not package.artifact.path.endswith(".whl"):
                        raise ContractError("pip dependencies must be locked wheels")
                    validate_archive(package.artifact.path, raw)
        return ResolvedPlan(
            1,
            registry,
            installation,
            source_kind,
            tuple(hashlib.sha256(lock.canonical_json().encode()).hexdigest() for lock in locks),
            artifacts,
        )
