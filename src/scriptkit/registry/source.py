"""Namespace-bound ArtifactSource adapter for injected manager consumers."""

import hashlib
from pathlib import Path

from scriptkit.contracts.artifacts import ArtifactPolicy
from scriptkit.contracts.codec import ContractError
from scriptkit.contracts.catalog import ResolvedPlan
from scriptkit.contracts.models import Artifact, CatalogRelease

from .resolver import Resolver


class RegistryArtifactSource:
    """Use current registered pins/expiry; never authorize arbitrary artifact URLs.

    Composition roots bind a namespace and offline policy once and inject this
    structural ArtifactSource into the manager. Pin replacement/removal takes
    effect on the next call; an old plan cannot bypass renewed trust metadata.
    """

    def __init__(
        self,
        resolver: Resolver,
        namespace: str,
        *,
        offline: bool = False,
        policy: ArtifactPolicy | None = None,
        artifact_directory: Path | None = None,
    ):
        self.resolver = resolver
        self.namespace = namespace
        self.offline = offline
        self.policy = policy
        self.artifact_directory = artifact_directory

    def catalog(self, name: str, version: str) -> CatalogRelease:
        if name != self.namespace:
            raise ContractError("artifact source is bound to another namespace")
        _, catalog = self.resolver._catalog(self.namespace, self.offline)
        if catalog.version != version:
            raise ContractError("unknown exact catalog version; no fallback permitted")
        return catalog

    def authorize(self, plan: ResolvedPlan) -> None:
        registry, catalog = self.resolver._catalog(self.namespace, self.offline)
        if registry != plan.registry or plan.installation.release not in catalog.releases:
            raise ContractError("installation plan no longer matches registered trust/pins")

    def _authorize_artifact(self, artifact: Artifact) -> str:
        registry, catalog = self.resolver._catalog(self.namespace, self.offline)
        authorized = {
            entry
            for release in catalog.releases
            for entry in (
                release.artifact,
                *(package.artifact for lock in release.locks for package in lock.packages),
            )
        }
        if artifact not in authorized:
            raise ContractError("artifact is not authorized by the pinned catalog")
        if self.policy is not None and artifact.size > self.policy.max_archive_bytes:
            raise ContractError("artifact exceeds source policy")
        return registry.origin

    def fetch(self, artifact: Artifact) -> bytes:
        origin = self._authorize_artifact(artifact)
        return self.resolver.cache.get(origin, artifact, offline=self.offline)

    def fetch_into(self, artifact: Artifact, destination: Path) -> None:
        """Copy pinned local artifacts in chunks; registry HTTP remains small-only.

        The caller owns the private destination. Local artifacts are addressed by
        SHA-256, not untrusted catalog paths, and reverified on every read.
        """
        origin = self._authorize_artifact(artifact)
        if self.artifact_directory is None:
            destination.write_bytes(self.resolver.cache.get(origin, artifact, offline=self.offline))
            return
        path = self.artifact_directory / artifact.sha256
        if path.is_symlink() or not path.is_file():
            raise ContractError("local artifact missing or unsafe")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source, destination.open("xb") as target:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > artifact.size:
                    raise ContractError("artifact size mismatch")
                digest.update(chunk)
                target.write(chunk)
        if size != artifact.size or digest.hexdigest() != artifact.sha256:
            raise ContractError("artifact size or SHA-256 mismatch")
