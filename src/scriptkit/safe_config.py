"""Opt-in strict config; legacy Config remains unchanged.

Secret fields are declared dot paths and accept references only. Resolution belongs
at the consuming provider boundary; this layer never fetches or logs secret values.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .config import deep_merge, get_nested, set_nested
from .state import LocalStateIO, StateIO


class ConfigError(ValueError):
    """Invalid config (messages deliberately omit config values)."""


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("duplicate config key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ConfigError("non-finite JSON number")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ConfigError("non-finite JSON number")
    return number


class SafeConfig:
    """Strict JSON object + optional schema callback, with compare-and-swap saves.

    Precedence/coercion match Config. Call load before save, even for new files.
    The callback validates the merged result on load and supplied data on save;
    it must raise on unknown keys/types as appropriate for the application schema.
    Saved data is explicit: environment overrides are never persisted implicitly.
    """

    def __init__(
        self,
        path: Path,
        *,
        defaults: dict[str, Any] | None = None,
        env_prefix: str | None = None,
        coerce_env: bool = False,
        environ: Mapping[str, str] | None = None,
        validator: Callable[[dict[str, Any]], None] | None = None,
        secret_fields: tuple[str, ...] = (),
        io: StateIO | None = None,
    ) -> None:
        self.path = path
        self.defaults = copy.deepcopy(defaults or {})
        self.env_prefix = env_prefix
        self.coerce_env = coerce_env
        self.environ = os.environ if environ is None else environ
        self.validator = validator
        self.secret_fields = secret_fields
        self.io = LocalStateIO() if io is None else io
        self._loaded = False
        self._snapshot: bytes | None = None

    def _validate(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise ConfigError("config must be an object")
        for path in self.secret_fields:
            ref = get_nested(data, path)
            if ref is not None and (
                not isinstance(ref, dict)
                or set(ref) != {"source", "name"}
                or ref["source"] not in ("env", "keyring")
                or not isinstance(ref["name"], str)
                or not re.fullmatch(r"[A-Za-z0-9_.:/-]+", ref["name"])
            ):
                raise ConfigError("secret fields require env/keyring references")
        if self.validator:
            try:
                self.validator(data)
            except Exception:
                raise ConfigError("config schema validation failed") from None

    def load(self) -> dict[str, Any]:
        self._loaded = False
        raw = self.io.read(self.path)
        saved: dict[str, Any] = {}
        if raw is not None:
            try:
                saved = json.loads(
                    raw, object_pairs_hook=_object, parse_constant=_constant, parse_float=_float
                )
            except (ValueError, UnicodeError):
                raise ConfigError("invalid config JSON") from None
            if not isinstance(saved, dict):
                raise ConfigError("config must be an object")
        data = deep_merge(copy.deepcopy(self.defaults), saved)
        # Validate stored values before overrides can hide an invalid secret/schema.
        self._validate(data)
        if self.env_prefix:
            prefix = self.env_prefix.upper().rstrip("_") + "_"
            for key, value in self.environ.items():
                if key.upper().startswith(prefix):
                    path = key[len(prefix) :].lower().replace("__", ".")
                    if path:
                        set_nested(data, path, value, coerce=self.coerce_env)
        self._validate(data)
        self._snapshot, self._loaded = raw, True
        return data

    def save(self, data: dict[str, Any], *, mode: int = 0o600) -> None:
        if not self._loaded:
            raise ConfigError("load config before saving")
        self._validate(data)
        try:
            raw = (json.dumps(data, indent=2, allow_nan=False) + "\n").encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise ConfigError("config is not JSON serializable") from None
        self.io.replace(self.path, raw, expected=self._snapshot, mode=mode)
        self._snapshot = raw
