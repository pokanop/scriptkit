"""Strict, dependency-free JSON codec and Draft 2020-12 schema derivation."""

from __future__ import annotations

from dataclasses import Field, fields, is_dataclass
from collections.abc import Mapping
from operator import lt, gt
from importlib.resources import files
import json
import re
from types import UnionType
from typing import Any, ClassVar, NoReturn, Self, Union, cast, get_args, get_origin, get_type_hints


class ContractError(ValueError):
    """Invalid or unsupported data; the message includes its JSON field path."""


class Record:
    """Frozen dataclass records inherit validation, parsing and canonical encoding."""

    __dataclass_fields__: ClassVar[dict[str, Field[Any]]]

    def __post_init__(self) -> None:
        hints = get_type_hints(type(self))
        for field in fields(self):
            value = getattr(self, field.name)
            _check(hints[field.name], value, field.name, field.metadata)
        self.validate()

    def validate(self) -> None:
        """Cross-field invariants, also applied during direct construction."""

    @classmethod
    def from_dict(cls, value: object) -> Self:
        return cast(Self, _decode(cls, value, cls.__name__))

    @classmethod
    def from_json(cls, text: str) -> Self:
        try:
            value = json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)
        except (ValueError, RecursionError) as exc:
            raise ContractError(f"{cls.__name__}: invalid JSON: {exc}") from exc
        return cls.from_dict(value)

    def to_dict(self) -> dict[str, Any]:
        return {field.name: _encode(getattr(self, field.name)) for field in fields(self)}

    def canonical_json(self) -> str:
        """UTF-8-ready, sorted keys, no insignificant whitespace; arrays keep order."""
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:scriptkit:contracts:{cls.__name__}:1",
            "title": cls.__name__,
            **_schema(cls),
        }


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _constant(value: str) -> NoReturn:
    raise ContractError(f"non-finite number {value}")


def _encode(value: Any) -> Any:
    if isinstance(value, Record):
        return value.to_dict()
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    return value


def _decode(kind: Any, value: Any, path: str) -> Any:
    origin, args = get_origin(kind), get_args(kind)
    if origin in (Union, UnionType):
        if value is None and type(None) in args:
            return None
        for arg in args:
            try:
                return _decode(arg, value, path)
            except ContractError:
                pass
        raise ContractError(f"{path}: value does not match any allowed type")
    if origin is tuple:
        if type(value) is not list:
            raise ContractError(f"{path}: expected array")
        return tuple(_decode(args[0], item, f"{path}[{i}]") for i, item in enumerate(value))
    if isinstance(kind, type) and issubclass(kind, Record):
        if type(value) is not dict:
            raise ContractError(f"{path}: expected object")
        names = {field.name for field in fields(kind)}
        unknown = value.keys() - names
        missing = names - value.keys()
        if unknown or missing:
            raise ContractError(
                f"{path}: unknown fields {sorted(unknown)}; missing fields {sorted(missing)}"
            )
        hints = get_type_hints(kind)
        try:
            return kind(
                **{key: _decode(hints[key], item, f"{path}.{key}") for key, item in value.items()}
            )
        except ContractError as exc:
            raise ContractError(f"{path}: {exc}") from exc
    _check(kind, value, path, {})
    return value


def _check(kind: Any, value: Any, path: str, rules: Mapping[str, Any]) -> None:
    origin, args = get_origin(kind), get_args(kind)
    if origin in (Union, UnionType):
        if value is None and type(None) in args:
            return
        for arg in args:
            try:
                _check(arg, value, path, rules)
                return
            except ContractError:
                pass
        raise ContractError(f"{path}: value does not match allowed types/constraints")
    if origin is tuple:
        if type(value) is not tuple:
            raise ContractError(f"{path}: expected immutable tuple")
        for i, item in enumerate(value):
            _check(args[0], item, f"{path}[{i}]", {})
    elif type(value) is not kind:
        raise ContractError(f"{path}: expected {kind.__name__}, got {type(value).__name__}")
    if "const" in rules and value != rules["const"]:
        raise ContractError(f"{path}: unsupported value {value!r}; expected {rules['const']!r}")
    if "enum" in rules and value not in rules["enum"]:
        raise ContractError(f"{path}: expected one of {rules['enum']}")
    if "pattern" in rules and re.fullmatch(rules["pattern"], value) is None:
        raise ContractError(f"{path}: {rules.get('description', 'invalid format')}")
    for bound, compare in (("minimum", lt), ("maximum", gt)):
        if bound in rules and compare(value, rules[bound]):
            raise ContractError(f"{path}: violates {bound} {rules[bound]}")
    for bound, compare in (("minLength", lt), ("maxLength", gt), ("minItems", lt)):
        if bound in rules and compare(len(value), rules[bound]):
            raise ContractError(f"{path}: violates {bound} {rules[bound]}")


def _schema(kind: Any) -> dict[str, Any]:
    origin, args = get_origin(kind), get_args(kind)
    if origin in (Union, UnionType):
        return {"anyOf": [_schema(arg) for arg in args]}
    if origin is tuple:
        return {"type": "array", "items": _schema(args[0])}
    if isinstance(kind, type) and is_dataclass(kind):
        hints = get_type_hints(kind)
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [f.name for f in fields(kind)],
            "properties": {
                f.name: {**_schema(hints[f.name]), **dict(f.metadata)} for f in fields(kind)
            },
        }
    return {"type": {str: "string", int: "integer", bool: "boolean", type(None): "null"}[kind]}


def resource_text(name: str) -> str:
    """Read a bundled schema/example by basename, never an arbitrary path."""
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*\.(schema|example)\.json", name) is None:
        raise ContractError("resource: expected <Contract>.schema.json or <Contract>.example.json")
    return files("scriptkit.contracts.resources").joinpath(name).read_text(encoding="utf-8")
