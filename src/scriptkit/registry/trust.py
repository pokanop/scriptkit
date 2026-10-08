"""Explicit origin consent and pinned, expiring registry metadata."""

from __future__ import annotations

from pathlib import Path

from scriptkit.contracts.codec import ContractError, Record
from scriptkit.contracts.models import constrained, record, unique
from scriptkit.contracts.catalog import Registry as Registry, origin_url as origin_url
from scriptkit.state import LocalStateIO, StateIO


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
