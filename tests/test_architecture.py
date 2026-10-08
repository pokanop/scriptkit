"""Executable ownership graph; runs on source installs AND installed wheels."""

import ast
import importlib.util
from pathlib import Path
import subprocess
import sys

import scriptkit


LAYERS = {"contracts", "registry", "manager", "generator", "ai", "conformance"}
ALLOWED = {
    "runtime": {"runtime"},
    "contracts": {"contracts"},
    "registry": {"registry", "contracts", "state"},
    "manager": {"manager", "contracts", "state", "execution"},
    "generator": {"generator", "contracts"},
    "ai": {"ai", "contracts"},
    "conformance": {"conformance", "contracts", "generator", "output", "command", "execution"},
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


# AI's project adapters compose established generator/conformance primitives.
# Provider/contracts modules still cannot access these layers (or manager/registry/runtime).
AI_PROJECT_PORTS = {
    "scriptkit.ai.context": {
        "scriptkit.generator.plan": {"MANIFEST", "Manifest", "read", "target"},
        "scriptkit.generator.scaffolds": {"existing"},
    },
    "scriptkit.ai.review": {
        "scriptkit.conformance.static": {"validate"},
        "scriptkit.generator.plan": {"Change", "Plan", "preview", "read", "target"},
        "scriptkit.generator.render": {"sha256", "canonical"},
        "scriptkit.generator.scaffolds": {"existing", "tool"},
        "scriptkit.generator.transaction": {"Writer", "apply"},
    },
}


def violations(text, module, is_package=False):
    source = layer(module)
    errors = []
    for target in imports(text, module, is_package):
        if any(
            target == port or target in {port + "." + name for name in symbols}
            for port, symbols in AI_PROJECT_PORTS.get(module, {}).items()
        ):
            continue
        # Bootstrap must remain independently downloadable, so the shared stdlib
        # cmd template lives there. Only the launchers adapter may import it;
        # importing Manager or another bootstrap symbol is still forbidden.
        if module == "scriptkit.manager.launchers" and target in {
            "scriptkit.bootstrap",
            "scriptkit.bootstrap.cmd_launcher",
        }:
            continue
        if target.startswith("scriptkit.") or target == "scriptkit":
            # Narrow reusable IO adapters, never the whole runtime or provider layer.
            target_layer = layer(target)
            for adapter in ("state", "execution", "output", "command"):
                if source in {"registry", "manager", "conformance"} and (
                    target == f"scriptkit.{adapter}" or target.startswith(f"scriptkit.{adapter}.")
                ):
                    target_layer = adapter
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
        if module in {"scriptkit.entrypoint", "scriptkit.__main__", "scriptkit.manager_cli"}:
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
        ("import scriptkit.manager", "scriptkit.conformance.static"),
        ("import scriptkit.conformance", "scriptkit.app"),
        ("import scriptkit.ai", "scriptkit.conformance.static"),
        ("import scriptkit.generator", "scriptkit.ai.provider"),
        ("import scriptkit.generator", "scriptkit.ai.contracts"),
        ("import scriptkit.manager", "scriptkit.ai.review"),
        ("import scriptkit.execution", "scriptkit.ai.review"),
        ("import scriptkit.registry", "scriptkit.ai.context"),
        ("from scriptkit.generator.scaffolds import installer", "scriptkit.ai.review"),
        ("import scriptkit.state", "scriptkit.contracts.models"),
        ("import scriptkit.registry", "scriptkit.manager.service"),
        ("import scriptkit.console", "scriptkit.manager.service"),
        ("from scriptkit.bootstrap import Manager", "scriptkit.manager.launchers"),
        ("from scriptkit.bootstrap import cmd_launcher", "scriptkit.manager.service"),
        ("import scriptkit.execution", "scriptkit.registry.cache"),
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
        if fullname.split('.')[0] in {'requests', 'openai', 'anthropic', 'pydantic', 'jsonschema'} or any(fullname == 'scriptkit.' + name or fullname.startswith('scriptkit.' + name + '.') for name in ('manager', 'registry', 'generator', 'ai', 'conformance')):
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
