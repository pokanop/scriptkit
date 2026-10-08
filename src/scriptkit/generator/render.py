"""Offline, data-only rendering: no template expressions, hooks or discovery imports."""

from __future__ import annotations

import hashlib
import json
import keyword
from dataclasses import dataclass
from importlib.resources import files
from string import Template

from scriptkit.contracts import ToolSpec

TEMPLATE_VERSION = "1.0.0"
FORMATTER_VERSION = "text-lf-1"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2) + "\n").encode()


def normalize(spec: ToolSpec) -> ToolSpec:
    """Validate even directly constructed records; sort only semantically unordered lists."""
    value = json.loads(spec.canonical_json())
    value["platforms"].sort(key=lambda p: (p["os"], p["arch"]))
    value["commands"].sort(key=lambda c: c["name"])
    # Argument order is meaningful (positionals); never sort it.
    return ToolSpec.from_dict(value)


@dataclass(frozen=True)
class Rendered:
    generated: dict[str, bytes]
    user: dict[str, bytes]
    template: str
    formatter: str
    spec_hash: str


def render(spec: ToolSpec, *, template_version: str = TEMPLATE_VERSION) -> Rendered:
    if template_version != TEMPLATE_VERSION:
        raise ValueError("unsupported template version; no implicit template migration")
    spec = normalize(spec)
    module, function = spec.entrypoint.split(":")
    parts = module.split(".")
    if (
        any(keyword.iskeyword(p) for p in [*parts, function])
        or parts[-1] in {"_handlers", "__init__"}
        or function in {"argparse", "json", "_handlers", "vars", "int", "str", "any", "__name__"}
        or len(parts) < 2
    ):
        raise ValueError(
            "entrypoint must be package.module:function, not a reserved module/function"
        )
    resource = files("scriptkit.generator.resources").joinpath("template-v1.json").read_bytes()
    # Pin the exact resource, not just a mutable name.
    if sha256(resource) != TEMPLATE_SHA256:
        raise ValueError("packaged template integrity failure")
    template = json.loads(resource)
    prefix = "src/" + "/".join(parts[:-1])
    mapping = {"spec": repr(spec.canonical_json()), "function": function}
    generated = {
        ".scriptkit-generator/.gitignore": template["ignore"].encode(),
        "tool.json": spec.canonical_json().encode() + b"\n",
        "pyproject.toml": Template(template["project"])
        .substitute(
            name=json.dumps(spec.name),
            version=json.dumps(spec.version),
            description=json.dumps(spec.description, ensure_ascii=False),
            entrypoint=json.dumps(spec.entrypoint),
            python=json.dumps(f">={spec.python.minimum},<{spec.python.maximum_exclusive}"),
        )
        .encode(),
        f"{prefix}/{parts[-1]}.py": Template(template["launcher"]).substitute(mapping).encode(),
    }
    # Initializers belong to users too: adding handwritten exports is safe.
    user = {
        ".gitattributes": template["attributes"].encode(),
        f"{prefix}/_handlers.py": template["handler"].encode(),
    }
    for i in range(1, len(parts)):
        user["src/" + "/".join(parts[:i]) + "/__init__.py"] = b""
    return Rendered(
        generated, user, template_version, FORMATTER_VERSION, sha256(spec.canonical_json().encode())
    )


# Updated deliberately with a new template version/migration, never at runtime.
TEMPLATE_SHA256 = "6edb4f99439801591fba1abe5fd1b5bb1eba6115a147c5d6729fefd4efa24f5a"
