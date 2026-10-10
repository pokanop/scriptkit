"""One installed-artifact journey: manual + fixture AI authoring to trusted lifecycle."""

from dataclasses import replace
import json
import os
import subprocess
import sys

from scriptkit.ai import FakeProvider, apply, request, review
from scriptkit.bootstrap import Manager
from scriptkit.contracts.models import (
    CatalogRelease,
    DependencyLock,
    LockedPackage,
    ToolRelease,
    ToolSpec,
)
from scriptkit.registry import Registry, VerifiedCache
from test_ai import HANDLER, proposal, selected
from test_bootstrap import built_wheel  # noqa: F401 -- shared installed-release fixture
from test_installer import Transport, artifact, fixture


def test_authored_tool_release_journey(tmp_path, built_wheel):  # noqa: F811
    root = tmp_path / "manager café"
    project = tmp_path / "authored tool"
    root.mkdir()
    project.mkdir()
    _, fixture_plan = fixture(tmp_path / "host-fixture")
    host_release = fixture_plan.installation.release
    spec = replace(host_release.tool, name="authored-tool", entrypoint="demo.cli:main")
    spec_path = tmp_path / "spec.json"
    raw = built_wheel.read_bytes()
    Manager(root, download=lambda _: raw).install(
        "https://fixture.example/framework.whl", artifact(built_wheel.name, raw).sha256, "1.5.0"
    )
    launcher = root / "bin" / ("scriptkit.cmd" if os.name == "nt" else "scriptkit")

    def cli(*args):
        completed = subprocess.run(
            [str(launcher), "--json", *map(str, args)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=tmp_path,
        )
        value = json.loads(completed.stdout)
        assert value["ok"], value
        return value["data"]

    def generate(current):
        spec_path.write_text(current.canonical_json(), encoding="utf-8")
        cli("new-tool", project, "--spec", spec_path, "--apply")

    def build():
        cli("validate", project)
        dist = tmp_path / spec.version
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist), str(project)],
            check=True,
            capture_output=True,
            cwd=tmp_path,
        )
        return next(dist.glob("*.whl"))

    generate(spec)
    (project / HANDLER).write_text(
        "def run(command, values):\n    return {'answer': 7}\n", encoding="utf-8"
    )
    first = build()
    context = selected(project)
    proposed = request(
        FakeProvider(proposal(context).canonical_json().encode()),
        context,
        approved_context_hash=context.identity,
    )
    checked = review(project, context, proposed)
    apply(project, context, proposed, approved_review_hash=checked.approval_hash)
    spec = replace(ToolSpec.from_json((project / "tool.json").read_text()), version="1.0.1")
    generate(spec)
    second = build()
    framework = artifact(built_wheel.name, raw)
    lock = DependencyLock(
        1,
        host_release.locks[0].platform,
        spec.python,
        "pip",
        (LockedPackage("pokanop-scriptkit", "1.5.0", framework),),
    )
    releases = tuple(
        ToolRelease(
            replace(spec, version=version), artifact(wheel.name, wheel.read_bytes()), (lock,)
        )
        for version, wheel in (("1.0.0", first), ("1.0.1", second))
    )
    catalog = CatalogRelease(1, "journey", "1.0.0", releases)
    catalog_bytes = catalog.canonical_json().encode()
    origin = "https://fixture.example/"
    registry = Registry(1, "journey", origin, artifact("catalog.json", catalog_bytes), 9999999999)
    registry_file = tmp_path / "registry.json"
    registry_file.write_text(registry.canonical_json(), encoding="utf-8")
    cli("registry", "add", registry_file, "--trust-origin", origin)
    payloads = {
        framework.path: raw,
        "catalog.json": catalog_bytes,
        first.name: first.read_bytes(),
        second.name: second.read_bytes(),
    }
    cache = VerifiedCache(root / "cache", Transport({origin + k: v for k, v in payloads.items()}))
    for item in (registry.catalog, framework, *(release.artifact for release in releases)):
        cache.get(origin, item)
    assert len(cli("catalog", "journey", "--offline")) == 2
    cli("install", "journey/authored-tool@1.0.0", "--offline")
    command = root / "bin" / ("authored-tool.cmd" if os.name == "nt" else "authored-tool")

    def answer():
        result = subprocess.check_output(
            [str(command), "--json", "hello"], cwd=tmp_path, text=True, encoding="utf-8"
        )
        return json.loads(result)["data"]["answer"]

    assert answer() == 7
    cli("update", "journey/authored-tool@1.0.1", "--offline")
    assert answer() == 42
    cli("rollback", "authored-tool")
    assert answer() == 7
    cli("uninstall", "authored-tool")
    assert not command.exists()
    assert (project / HANDLER).exists()
