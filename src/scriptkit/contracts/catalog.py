"""Shared registry identity and resolved-plan records, free of provider IO."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

from .codec import ContractError, Record
from .models import Artifact, InstallPlan, constrained, name, record


def origin_url(value: str) -> str:
    """Only canonical HTTPS directory URLs; no credentials, queries or redirects."""
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or not value.endswith("/")
        or re.fullmatch(r"https://[a-z0-9.-]+(?::[0-9]+)?/(?:[a-z0-9_-]+/)*", value) is None
    ):
        raise ContractError("origin must be a canonical HTTPS directory URL")
    try:
        url.port
    except ValueError as exc:
        raise ContractError("invalid origin port") from exc
    return value


@record
class Registry(Record):
    schema_version: int = constrained(const=1)
    namespace: str = name()
    origin: str
    catalog: Artifact
    expires_at: int = constrained(minimum=1)

    def validate(self) -> None:
        origin_url(self.origin)


@record
class ResolvedPlan(Record):
    schema_version: int = constrained(const=1)
    registry: Registry
    installation: InstallPlan
    source_kind: str = constrained(enum=["wheel", "legacy-scripts"])
    lock_sha256: tuple[str, ...]
    artifacts: tuple[Artifact, ...]

    def validate(self) -> None:
        plan = self.installation
        locks = tuple(lock for lock in plan.release.locks if lock.platform == plan.platform)
        expected_artifacts = (
            plan.release.artifact,
            *(p.artifact for lock in locks for p in lock.packages),
        )
        expected_hashes = tuple(
            hashlib.sha256(lock.canonical_json().encode()).hexdigest() for lock in locks
        )
        suffix = ".whl" if self.source_kind == "wheel" else ".scripts.zip"
        if (
            self.registry.catalog.sha256 != plan.catalog_sha256
            or self.lock_sha256 != expected_hashes
            or self.artifacts != expected_artifacts
            or not plan.release.artifact.path.endswith(suffix)
        ):
            raise ContractError("resolved plan provenance does not match installation")
