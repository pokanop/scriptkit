"""Offline authoring layouts composed over the hash-owned transaction engine."""

from __future__ import annotations

import json
from importlib.metadata import version as distribution_version
from pathlib import Path
from urllib.parse import urlsplit

from scriptkit.contracts import ToolSpec
from scriptkit.contracts.models import CatalogRelease
from .render import Rendered, canonical, render, sha256

VERSION = "2.0.0"


def tool(spec: ToolSpec, layout: str = "standalone") -> Rendered:
    if layout not in {"standalone", "repository"}:
        raise ValueError("unknown layout")
    base = render(spec)
    spec = ToolSpec.from_json(base.generated["tool.json"].decode())
    if any(c.name in {"doctor", "config"} for c in spec.commands):
        raise ValueError("doctor and config are reserved commands")
    if any(a.name in {"command", "config", "json"} for c in spec.commands for a in c.arguments):
        raise ValueError("command, config and json are reserved argument names")
    module, function = spec.entrypoint.split(":")
    if function == "tool_main":
        raise ValueError("tool_main is a reserved entrypoint function")
    generated = dict(base.generated)
    generated[f"src/{module.replace('.', '/')}.py"] = (
        '"""Generated launcher; put behavior in _handlers.py."""\n'
        "import json\nfrom scriptkit.scaffold_runtime import tool_main\nfrom . import _handlers\n\n"
        f"SPEC = json.loads({spec.canonical_json()!r})\n\n"
        f"def {function}():\n    return tool_main(SPEC, _handlers.run)\n\n"
        f'if __name__ == "__main__":\n    raise SystemExit({function}())\n'
    ).encode()
    runtime = "pokanop-scriptkit==" + distribution_version("pokanop-scriptkit")
    project = (
        generated["pyproject.toml"]
        .decode()
        .replace(
            "[project.scripts]", f"dependencies = [{json.dumps(runtime)}]\n\n[project.scripts]"
        )
    )
    generated["pyproject.toml"] = project.encode()
    generated["scaffold.json"] = canonical({"layout": layout, "template": VERSION})
    generated["requirements.txt"] = (runtime + "\n").encode()
    if layout == "repository":
        generated[f"bin/{spec.name}"] = (
            "#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n"
            'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))\n'
            f"from {module} import {function}\nraise SystemExit({function}())\n"
        ).encode()
    user = dict(base.user)
    user[".gitattributes"] += (
        b"/scaffold.json text eol=lf\n/requirements.txt text eol=lf\n/bin/* text eol=lf\n"
    )
    user["config.example.json"] = b"{}\n"
    user["README.md"] = (
        f"# {spec.name}\n\n{spec.description}\n\n"
        "Install: `python -m pip install .` (or build a wheel with `python -m build`).\n"
        f"Run `{spec.name} --help`, `{spec.name} --version`, `{spec.name} doctor`.\n"
        "Global `--json` selects the ScriptKit envelope; `--config config.example.json` loads JSON.\n"
        "No command runs by default. Handlers start unimplemented and fail explicitly.\n"
        + (
            f"Repository launcher: `python bin/{spec.name}`; chmod +x explicitly for direct execution.\n"
            if layout == "repository"
            else ""
        )
    ).encode()
    user["AUTHORING.md"] = (
        "# Authoring rules\n\nEdit src/**/_handlers.py, not generated launchers.\n"
        "Dispatch by explicit command name; return data or an integer exit status.\n"
        "Raise an exception on failure. Require explicit confirmation for destructive work.\n"
        "Keep secrets out of output/config examples. No import-time IO or AI dependencies.\n"
        "Use scriptkit add-command and template-upgrade; inspect previews before --apply.\n"
        "Generated files are hash-owned; user modules are never overwritten.\n"
    ).encode()
    user["tests/test_smoke.py"] = (
        "import subprocess\nimport sys\n\n\ndef test_cli():\n"
        f"    for flag in ('--help', '--version', 'doctor'):\n"
        f"        subprocess.run([sys.executable, '-m', {module!r}, flag], check=True)\n"
    ).encode()
    user[".github/workflows/test.yml"] = (
        "name: Tool\non: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - uses: actions/checkout@v4\n      - uses: actions/setup-python@v5\n"
        "        with:\n          python-version: '3.11'\n"
        "      - run: python -m pip install . pytest build\n"
        "      - run: python -m pytest\n      - run: python -m build\n"
    ).encode()
    # Dot-directories are validated by the same path contract as ordinary generated paths.
    return Rendered(generated, user, VERSION, base.formatter, base.spec_hash)


def installer(bootstrap_hash: str, wheel: str, wheel_hash: str, version: str) -> dict[str, bytes]:
    from scriptkit.contracts.models import Artifact, LockedPackage

    Artifact("bootstrap.py", bootstrap_hash, 0)
    LockedPackage("pokanop-scriptkit", version, Artifact("manager.whl", wheel_hash, 0))
    url = urlsplit(wheel)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or any(c in wheel for c in "\r\n\x00")
    ):
        raise ValueError("wheel must be an HTTPS URL without credentials")
    pins = {
        "bootstrap_sha256": bootstrap_hash,
        "wheel": wheel,
        "sha256": wheel_hash,
        "version": version,
    }
    return {
        "manager-pins.json": canonical(pins),
        "install.py": (
            '"""Thin manager-only installer. Supply an independently obtained bootstrap.py."""\n'
            "import argparse\nimport hashlib\nimport json\nfrom pathlib import Path\nimport subprocess\nimport sys\n\n"
            "parser = argparse.ArgumentParser(description=__doc__)\n"
            "parser.add_argument('--bootstrap', type=Path, required=True)\n"
            "parser.add_argument('--root', type=Path, required=True)\n"
            "args = parser.parse_args()\n"
            "pins = json.loads(Path(__file__).with_name('manager-pins.json').read_text(encoding='utf-8'))\n"
            "if hashlib.sha256(args.bootstrap.read_bytes()).hexdigest() != pins['bootstrap_sha256']:\n"
            "    parser.error('bootstrap SHA-256 mismatch')\n"
            "raise SystemExit(subprocess.call([sys.executable, '-I', str(args.bootstrap),\n"
            "    '--root', str(args.root), '--wheel', pins['wheel'], '--sha256', pins['sha256'],\n"
            "    '--version', pins['version']]))\n"
        ).encode(),
    }


def collection(
    catalog: CatalogRelease, pins: dict[str, bytes], origin: str, expires_at: int
) -> Rendered:
    # A collection must contain actual releases, not fabricated artifacts or hashes.
    catalog = CatalogRelease.from_json(catalog.canonical_json())
    from scriptkit.contracts.catalog import Registry
    from scriptkit.contracts.models import Artifact

    raw = catalog.canonical_json().encode() + b"\n"
    registry = Registry(
        1, catalog.name, origin, Artifact("catalog.json", sha256(raw), len(raw)), expires_at
    )
    generated = {
        "catalog.json": raw,
        "registry.json": registry.canonical_json().encode() + b"\n",
        **pins,
    }
    user = {
        ".gitattributes": b"/*.json text eol=lf\n/install.py text eol=lf\n/.scriptkit-generator/manifest.json text eol=lf\n",
        "README.md": (
            f"# {catalog.name}\n\nLocal catalog: catalog.json (versioned CatalogRelease contract).\n"
            "Copy referenced artifacts beside the catalog, preserving their relative paths and hashes.\n"
            "Run `python install.py --bootstrap /verified/bootstrap.py --root /manager/root`.\n"
            "This installs ONLY the pinned manager. Publish catalog/artifacts at the pinned origin and register registry.json\n"
            "it with explicit origin consent before selecting individual tools with scriptkit install.\n"
        ).encode(),
    }
    return Rendered(generated, user, VERSION, "text-lf-1", sha256(generated["catalog.json"]))


def existing(root: Path) -> tuple[ToolSpec, str]:
    from .plan import read

    raw = read(root, "tool.json")
    if raw is None:
        raise ValueError("tool.json is missing")
    settings = read(root, "scaffold.json")
    layout = json.loads(settings)["layout"] if settings else "standalone"
    return ToolSpec.from_json(raw.decode()), layout
