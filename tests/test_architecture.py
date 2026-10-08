"""Executable ownership graph; runs on source installs AND installed wheels."""

import ast
import importlib.util
from pathlib import Path
import subprocess
import sys

import scriptkit


LAYERS = {"contracts", "registry", "manager", "generator", "ai"}
ALLOWED = {
    "runtime": {"runtime"},
    "contracts": {"contracts"},
    "registry": {"registry", "contracts", "state"},
    "manager": {"manager", "contracts"},
    "generator": {"generator", "contracts"},
    "ai": {"ai", "contracts"},
}


def layer(module):
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[1] in LAYERS else "runtime"


def imports(text, module, is_package=False):
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = (
                importlib.util.resolve_name("." * node.level + (node.module or ""), package)
                if node.level
                else node.module
            )
            yield base
            yield from (base + "." + alias.name for alias in node.names)
        elif isinstance(node, ast.Call) and (
            isinstance(node.func, ast.Name)
            and node.func.id in {"__import__", "import_module"}
            or isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
        ):
            # No hidden/computed dynamic edges in protected layers.
            if (
                not node.args
                or not isinstance(node.args[0], ast.Constant)
                or not isinstance(node.args[0].value, str)
            ):
                yield "FORBIDDEN_COMPUTED_IMPORT"
            else:
                yield importlib.util.resolve_name(node.args[0].value, package)


def violations(text, module, is_package=False):
    source = layer(module)
    errors = []
    for target in imports(text, module, is_package):
        if target.startswith("scriptkit.") or target == "scriptkit":
            # The sole runtime exception is explicit in ALLOWED: registry state IO.
            target_layer = layer(target)
            if source == "registry" and (
                target == "scriptkit.state" or target.startswith("scriptkit.state.")
            ):
                target_layer = "state"
            if target_layer not in ALLOWED[source]:
                errors.append(f"{module} -> {target}")
        elif source == "contracts" and target.split(".")[0] not in sys.stdlib_module_names | {
            "__future__"
        }:
            errors.append(f"{module} -> external {target}")
        elif target == "FORBIDDEN_COMPUTED_IMPORT":
            errors.append(f"{module} -> computed import")
    return errors


def test_ownership_graph():
    root = Path(scriptkit.__file__).parent
    errors = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).with_suffix("")
        parts = list(relative.parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts.pop()
        module = ".".join(["scriptkit", *parts])
        if module in {"scriptkit.entrypoint", "scriptkit.__main__"}:
            continue  # composition roots alone may wire upper layers
        errors.extend(violations(path.read_text(encoding="utf-8"), module, is_package))
    assert not errors, "\n".join(errors)


def test_checker_detects_regressions():
    for code, module in [
        ("from .manager import install", "scriptkit.app"),
        ("from . import ai", "scriptkit.app"),
        ("import scriptkit.generator", "scriptkit.contracts.models"),
        ("from .. import manager", "scriptkit.contracts.models"),
        ("import requests", "scriptkit.contracts.models"),
        ("import scriptkit.console", "scriptkit.registry.cache"),
        ("import scriptkit.state", "scriptkit.contracts.models"),
        ("importlib.import_module('scriptkit.ai')", "scriptkit.app"),
        ("__import__(computed)", "scriptkit.app"),
        ("from importlib import import_module; import_module('scriptkit.ai')", "scriptkit.app"),
    ]:
        assert violations(code, module), code


def test_core_imports_without_upper_layers():
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
import sys
class BlockUpperLayers:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'requests', 'openai', 'anthropic', 'pydantic', 'jsonschema'} or any(fullname == 'scriptkit.' + name or fullname.startswith('scriptkit.' + name + '.') for name in ('manager', 'registry', 'generator', 'ai')):
            raise AssertionError('Forbidden core dependency: ' + fullname)
sys.meta_path.insert(0, BlockUpperLayers())
import scriptkit
import scriptkit.contracts
import scriptkit.safe_config
import scriptkit.state
import scriptkit.paths
import scriptkit.regions
import scriptkit.execution
from scriptkit.contracts import ToolSpec, resource_text
ToolSpec.from_json(resource_text('ToolSpec.example.json'))
""",
        ],
        check=True,
    )
