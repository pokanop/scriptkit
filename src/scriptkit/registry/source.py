"""Namespace-bound ArtifactSource adapter for injected manager consumers."""

from scriptkit.contracts.codec import ContractError
from scriptkit.contracts.models import Artifact, CatalogRelease

from .resolver import Resolver


class RegistryArtifactSource:
    """Use current registered pins/expiry; never authorize arbitrary artifact URLs.

    Composition roots bind a namespace and offline policy once and inject this
    structural ArtifactSource into the manager. Pin replacement/removal takes
    effect on the next call; an old plan cannot bypass renewed trust metadata.
    """

    def __init__(self, resolver: Resolver, namespace: str, *, offline: bool = False):
        self.resolver = resolver
        self.namespace = namespace
        self.offline = offline

    def catalog(self, name: str, version: str) -> CatalogRelease:
        if name != self.namespace:
            raise ContractError("artifact source is bound to another namespace")
        _, catalog = self.resolver._catalog(self.namespace, self.offline)
        if catalog.version != version:
            raise ContractError("unknown exact catalog version; no fallback permitted")
        return catalog

    def fetch(self, artifact: Artifact) -> bytes:
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
        return self.resolver.cache.get(registry.origin, artifact, offline=self.offline)
