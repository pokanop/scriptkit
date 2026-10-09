"""Review regressions: extensions preserve manifests and package launchers are checked."""

import json

import pytest

from test_conformance import project as project, codes
from scriptkit.conformance import validate
from scriptkit.conformance.ownership import metadata_extension
from scriptkit.contracts import ToolSpec
from scriptkit.generator import preview
from scriptkit.generator.plan import MANIFEST
from scriptkit.generator.render import render, sha256
from scriptkit.generator.scaffolds import tool


def test_add_dependency_keeps_manifest_and_generator_conflict(project):
    handler = project / "src/demo/_handlers.py"
    handler.write_text("import requests\n")
    assert "SKV009" in codes(project)
    manifest = (project / MANIFEST).read_bytes()
    metadata = project / "pyproject.toml"
    metadata.write_text(
        metadata.read_text().replace("dependencies = [", 'dependencies = ["requests>=2", ')
    )
    assert validate(project).valid
    assert (project / MANIFEST).read_bytes() == manifest
    spec = ToolSpec.from_json((project / "tool.json").read_text())
    assert any(c.path == "pyproject.toml" for c in preview(project, tool(spec)).conflicts)


@pytest.mark.parametrize(
    "before,after",
    [
        ("An offline example", "different description"),
        ("setuptools>=68", "setuptools>=70"),
        ("setuptools.build_meta", "custom.backend"),
        ("pokanop-scriptkit==1.5.0", "pokanop-scriptkit>=1.5.0"),
        ("dependencies = [", 'dependencies = ["pokanop-scriptkit>=1", '),
        ('dependencies = ["pokanop-scriptkit==1.5.0"]', "dependencies = []"),
        ('dependencies = ["pokanop-scriptkit==1.5.0"]', "dependencies = 1"),
        ('where = ["src"]', 'where = ["other"]'),
    ],
)
def test_owned_metadata_remains_protected(project, before, after):
    metadata = project / "pyproject.toml"
    original = metadata.read_text()
    assert before in original
    metadata.write_text(original.replace(before, after))
    assert "SKV013" in codes(project)


def test_unknown_baseline_fails_closed(project):
    manifest = project / MANIFEST
    value = json.loads(manifest.read_text())
    for item in value["generated"]:
        if item["path"] == "pyproject.toml":
            item["sha256"] = "0" * 64
    manifest.write_text(json.dumps(value))
    assert "SKV013" in codes(project)


@pytest.mark.parametrize("entrypoint", ["demo:main", "demo.cli:main"])
def test_package_entrypoints(project, entrypoint):
    (project / MANIFEST).unlink()  # Handwritten package entrypoint, not a scaffold migration.
    value = json.loads((project / "tool.json").read_text())
    value["entrypoint"] = entrypoint
    (project / "tool.json").write_text(json.dumps(value))
    metadata = project / "pyproject.toml"
    metadata.write_text(metadata.read_text().replace("demo.cli:main", entrypoint))
    module = entrypoint.split(":")[0]
    launcher = project / ("src/" + module.replace(".", "/") + "/__init__.py")
    launcher.parent.mkdir(exist_ok=True)
    (project / "src/demo/cli.py").unlink()
    launcher.write_text(
        "import json\nfrom scriptkit.scaffold_runtime import tool_main\n"
        + f"SPEC = json.loads({json.dumps(value)!r})\n"
        + "def main():\n    return tool_main(SPEC, lambda name, values: None)\n"
    )
    assert validate(project).valid
    original = launcher.read_text()
    launcher.write_text(original.replace("tool_main(SPEC,", "other(SPEC,"))
    assert "SKV011" in codes(project)
    launcher.write_text(original.replace("An offline example", "changed"))
    assert "SKV015" in codes(project)


def test_parse_recursion_is_diagnostic(project, monkeypatch):
    import ast

    original = ast.parse

    def parse(source, filename):
        if filename.endswith("_handlers.py"):
            raise RecursionError("too complex")
        return original(source, filename)

    monkeypatch.setattr(ast, "parse", parse)
    assert any(
        d.code == "SKV007" and d.path.endswith("_handlers.py")
        for d in validate(project).diagnostics
    )


@pytest.mark.parametrize("path,code", [("tool.json", "SKV002"), ("pyproject.toml", "SKV003")])
def test_missing_files(project, path, code):
    (project / path).unlink()
    assert any(d.code == code and "missing" in d.message for d in validate(project).diagnostics)


def test_metadata_extension_failure_modes(project):
    spec = ToolSpec.from_json((project / "tool.json").read_text())
    baseline = tool(spec).generated["pyproject.toml"]
    for invalid in (b"broken [", b"project = 1", baseline + b"\n[tool.other]\nx = 1\n"):
        assert not metadata_extension(spec, "2.0.0", sha256(baseline), invalid)
    legacy = render(spec).generated["pyproject.toml"]
    assert metadata_extension(
        spec, "1.0.0", sha256(legacy), legacy + b"\n[tool.scriptkit]\nx = 1\n"
    )
