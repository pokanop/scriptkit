"""Opt-in trusted registry APIs; importing this module performs no IO."""

from .cache import HTTPTransport, Transport, VerifiedCache
from .resolver import ResolvedPlan, Resolver
from .trust import Registry, RegistryStore
from .source import RegistryArtifactSource

__all__ = [
    "HTTPTransport",
    "Transport",
    "VerifiedCache",
    "ResolvedPlan",
    "Resolver",
    "Registry",
    "RegistryStore",
    "RegistryArtifactSource",
]
