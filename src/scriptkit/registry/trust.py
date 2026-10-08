"""Explicit origin consent and pinned, expiring registry metadata."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from scriptkit.contracts.codec import ContractError, Record
from scriptkit.contracts.models import Artifact, constrained, name, record, unique
from scriptkit.state import LocalStateIO, StateIO


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
class RegistryList(Record):
    schema_version: int = constrained(const=1)
    registries: tuple[Registry, ...]

    def validate(self) -> None:
        unique([item.namespace for item in self.registries], "registry namespace")


class RegistryStore:
    """Caller supplies explicit consent (e.g. prompt or --trust-origin).

    An existing namespace cannot be repointed; remove then add explicitly. Config
    and cache parents must be private trusted directories, as with LocalStateIO.
    """

    def __init__(self, path: Path, io: StateIO | None = None):
        self.path = path
        self.io = io or LocalStateIO()

    def _read(self) -> tuple[bytes | None, RegistryList]:
        raw = self.io.read(self.path)
        return raw, RegistryList.from_json(raw.decode()) if raw else RegistryList(1, ())

    def list(self) -> tuple[Registry, ...]:
        return self._read()[1].registries

    def add(self, registry: Registry, *, consent_origin: str) -> None:
        if consent_origin != registry.origin:
            raise ContractError("explicit consent to exact registry origin required")
        raw, state = self._read()
        updated = RegistryList(1, (*state.registries, registry))
        self.io.replace(self.path, updated.canonical_json().encode(), expected=raw)

    def remove(self, namespace: str) -> None:
        raw, state = self._read()
        if namespace not in {item.namespace for item in state.registries}:
            raise ContractError("unknown registry namespace")
        updated = RegistryList(1, tuple(r for r in state.registries if r.namespace != namespace))
        self.io.replace(self.path, updated.canonical_json().encode(), expected=raw)
