"""Static authoring contract checks. Project files are data, never imported."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys
import tomllib
from typing import Any

from scriptkit.contracts import ToolSpec
from scriptkit.generator.plan import MANIFEST, Manifest, read
from scriptkit.generator.render import sha256


@dataclass(frozen=True, order=True)
class Diagnostic:
    code: str
    path: str
    message: str
    line: int = 0


@dataclass(frozen=True)
class Report:
    diagnostics: tuple[Diagnostic, ...]

    @property
    def valid(self) -> bool:
        return not self.diagnostics

    def to_data(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "mode": "static",
            "valid": self.valid,
            "safety": "Static conformance is not proof that arbitrary Python is safe.",
            "diagnostics": [asdict(d) for d in self.diagnostics],
        }


def validate(root: Path) -> Report:
    """Check src-layout tools, including handwritten extensions, without execution.

    Distribution/import name differences are declared in
    [tool.scriptkit.conformance.imports], e.g. PIL = "Pillow".
    Dynamic imports/behavior require independent review and runtime tests.
    """
    diagnostics: list[Diagnostic] = []

    def issue(code: str, path: str, message: str, line: int = 0) -> None:
        diagnostics.append(Diagnostic(code, path, message, line))

    def load(path: str) -> bytes | None:
        try:
            return read(root, path)
        except (OSError, ValueError) as exc:
            issue("SKV001", path, f"Cannot read a regular project file: {exc}")
            return None

    raw = load("tool.json")
    try:
        spec = ToolSpec.from_json((raw or b"").decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        issue("SKV002", "tool.json", f"Provide a valid ToolSpec: {exc}")
        return Report(tuple(sorted(diagnostics)))
    try:
        project = tomllib.loads((load("pyproject.toml") or b"").decode("utf-8"))
        metadata = project["project"]
        if not isinstance(metadata, dict):
            raise ValueError("project must be a table")
    except (ValueError, KeyError, UnicodeError) as exc:
        issue("SKV003", "pyproject.toml", f"Provide project metadata: {exc}")
        return Report(tuple(sorted(diagnostics)))
    for key, expected in (
        ("name", spec.name),
        ("version", spec.version),
        ("requires-python", f">={spec.python.minimum},<{spec.python.maximum_exclusive}"),
    ):
        if metadata.get(key) != expected:
            issue("SKV004", "pyproject.toml", f"Set project.{key} to {expected!r} from tool.json")
    scripts = metadata.get("scripts", {})
    if not isinstance(scripts, dict) or scripts.get(spec.name) != spec.entrypoint:
        issue(
            "SKV005", "pyproject.toml", f"Declare console script {spec.name} = {spec.entrypoint!r}"
        )

    dependencies = metadata.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(isinstance(d, str) for d in dependencies):
        issue("SKV006", "pyproject.toml", "project.dependencies must be an array of requirements")
        dependencies = []

    def normalized(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name).lower()

    declared = {normalized(re.split(r"[\s\[<>=!~;@]", d)[0]) for d in dependencies}
    if "pokanop-scriptkit" not in declared:
        issue("SKV006", "pyproject.toml", "Declare pokanop-scriptkit as a runtime dependency")
    settings: Any = project
    for section in ("tool", "scriptkit", "conformance"):
        settings = settings.get(section, {}) if isinstance(settings, dict) else {}
    aliases = settings.get("imports", {}) if isinstance(settings, dict) else {}
    if not isinstance(aliases, dict) or not all(isinstance(v, str) for v in aliases.values()):
        issue(
            "SKV006", "pyproject.toml", "conformance.imports must map import names to distributions"
        )
        aliases = {}
    aliases = {"scriptkit": "pokanop-scriptkit", **aliases}
    source = root / "src"
    trees: dict[str, ast.Module] = {}

    # Do not follow directory links, even on Python versions where rglob changes behavior.
    def walk(directory: Path) -> None:
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                issue(
                    "SKV001", relative, "Replace symlink/reparse point with a regular source file"
                )
            elif path.is_dir():
                walk(path)
            elif path.suffix == ".py":
                data = load(relative)
                if data is not None:
                    try:
                        trees[relative] = ast.parse(data, filename=relative)
                    except (SyntaxError, ValueError) as exc:
                        issue("SKV007", relative, f"Fix Python syntax: {exc}")

    try:
        if (
            source.is_symlink()
            or not source.is_dir()
            or getattr(source.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("src must be a non-symlink directory")
        walk(source)
    except (OSError, ValueError) as exc:
        issue("SKV001", "src", str(exc))
    local = {p.split("/")[1].removesuffix(".py") for p in trees}
    module, function = spec.entrypoint.split(":")
    launcher = "src/" + module.replace(".", "/") + ".py"
    tree = trees.get(launcher)
    if tree is None or not any(
        isinstance(n, ast.FunctionDef) and n.name == function for n in tree.body
    ):
        issue(
            "SKV008", launcher, f"Define synchronous entrypoint function {function} at module scope"
        )
    for path, parsed in trees.items():
        for node in ast.walk(parsed):
            imports: list[str] = []
            if isinstance(node, ast.Import):
                imports = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports = [node.module.split(".")[0]]
            for name in imports:
                if (
                    name not in local
                    and name not in sys.stdlib_module_names
                    and normalized(aliases.get(name, name)) not in declared
                ):
                    issue(
                        "SKV009",
                        path,
                        f"Declare runtime dependency for import {name!r}; map differing distribution names in tool.scriptkit.conformance.imports",
                        getattr(node, "lineno", 0),
                    )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                issue(
                    "SKV010",
                    path,
                    "Use OutputContext or return data, not print(), to preserve machine output",
                    node.lineno,
                )
    # Recognize sanctioned runtime wiring, not string occurrences or arbitrary same-named calls.
    if tree is not None:
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SPEC" for t in statement.targets
            ):
                value = statement.value
                if (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and isinstance(value.func.value, ast.Name)
                    and value.func.value.id == "json"
                    and value.func.attr == "loads"
                    and len(value.args) == 1
                ):
                    try:
                        embedded = ToolSpec.from_json(ast.literal_eval(value.args[0]))
                        if embedded != spec:
                            issue(
                                "SKV015",
                                launcher,
                                "Embedded SPEC differs from tool.json; regenerate launcher",
                            )
                    except (ValueError, TypeError, SyntaxError):
                        issue(
                            "SKV015", launcher, "Embedded SPEC must be valid literal ToolSpec JSON"
                        )
        runtime_names = {
            a.asname or a.name
            for n in tree.body
            if isinstance(n, ast.ImportFrom) and n.module == "scriptkit.scaffold_runtime"
            for a in n.names
            if a.name == "tool_main"
        }
        wired = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in runtime_names
            for n in ast.walk(tree)
        )
        if not wired:
            issue(
                "SKV011",
                launcher,
                "Use scaffold_runtime.tool_main for doctor/config/output conventions; custom runtimes require independent conformance review",
            )
    config = load("config.example.json")
    try:
        if not isinstance(json.loads(config or b"null"), dict):
            raise ValueError("expected JSON object")
    except (ValueError, UnicodeError) as exc:
        issue("SKV012", "config.example.json", f"Provide a non-secret config example object: {exc}")
    manifest = load(MANIFEST)
    if manifest is not None:
        try:
            owned = Manifest.from_json(manifest.decode())
            if owned.spec_hash != sha256(spec.canonical_json().encode()):
                issue("SKV013", "tool.json", "Spec changed; preview regeneration before applying")
            for item in owned.generated:
                current = load(item.path)
                if current is None or sha256(current) != item.sha256:
                    issue(
                        "SKV013",
                        item.path,
                        "Generated file drift; restore or preview regeneration (never overwrite user code)",
                    )
        except (ValueError, UnicodeError) as exc:
            issue("SKV014", MANIFEST, f"Repair invalid ownership manifest: {exc}")
    return Report(tuple(sorted(set(diagnostics))))
