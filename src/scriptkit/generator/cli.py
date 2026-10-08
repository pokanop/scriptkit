"""Authoring CLI: parsing/composition only, no network or executable templates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scriptkit.contracts import ToolSpec
from scriptkit.contracts.models import CatalogRelease, CommandSpec
from . import apply, preview, recover
from .render import Rendered, canonical, sha256
from .scaffolds import VERSION, collection, existing, installer, tool

COMMANDS = {"new-tool", "add-command", "template-upgrade", "new-collection", "generate-installer"}


def configure(commands: Any) -> None:
    for verb in sorted(COMMANDS):
        parser = commands.add_parser(verb, help="offline, hash-owned authoring")
        parser.add_argument("project", type=Path, help="existing project directory")
        modes = parser.add_mutually_exclusive_group()
        modes.add_argument("--apply", action="store_true", help="publish the preview transaction")
        modes.add_argument("--check", action="store_true", help="fail on drift")
        modes.add_argument("--recover", action="store_true", help="recover interrupted publication")
        if verb == "new-tool":
            parser.add_argument("--spec", type=Path, required=True)
            parser.add_argument(
                "--layout", choices=("standalone", "repository"), default="standalone"
            )
        if verb == "add-command":
            parser.add_argument("--spec", type=Path, required=True, help="CommandSpec JSON")
        if verb == "new-collection":
            parser.add_argument(
                "--catalog", type=Path, required=True, help="validated CatalogRelease JSON"
            )
            parser.add_argument(
                "--origin", required=True, help="canonical HTTPS publication origin"
            )
            parser.add_argument(
                "--expires-at", type=int, required=True, help="registry expiry Unix timestamp"
            )
        if verb in {"new-collection", "generate-installer"}:
            parser.add_argument("--bootstrap-sha256", required=True)
            parser.add_argument("--wheel", required=True)
            parser.add_argument("--wheel-sha256", required=True)
            parser.add_argument("--manager-version", required=True)


def dispatch(args: argparse.Namespace) -> dict[str, object]:
    root = args.project
    if args.recover:
        return {"recovered": recover(root)}
    from .plan import MANIFEST, Manifest, read

    previous = read(root, MANIFEST)
    if previous:
        owned = {f.path for f in Manifest.from_json(previous.decode()).generated}
        kind = (
            "new-tool"
            if "tool.json" in owned
            else "new-collection"
            if "catalog.json" in owned
            else "generate-installer"
        )
        if (
            args.command in {"new-tool", "new-collection", "generate-installer"}
            and args.command != kind
        ):
            raise ValueError("cannot change project kind; use a separate directory")
    if args.command == "new-tool":
        rendered = tool(ToolSpec.from_json(args.spec.read_text(encoding="utf-8")), args.layout)
    elif args.command in {"add-command", "template-upgrade"}:
        spec, layout = existing(root)
        if args.command == "add-command":
            command = CommandSpec.from_json(args.spec.read_text(encoding="utf-8"))
            value = spec.to_dict()
            value["commands"] = [c.to_dict() for c in spec.commands] + [command.to_dict()]
            spec = ToolSpec.from_dict(value)
        rendered = tool(spec, layout)
    else:
        pins = installer(args.bootstrap_sha256, args.wheel, args.wheel_sha256, args.manager_version)
        if args.command == "new-collection":
            rendered = collection(
                CatalogRelease.from_json(args.catalog.read_text(encoding="utf-8")),
                pins,
                args.origin,
                args.expires_at,
            )
        else:
            rendered = Rendered(
                pins,
                {},
                VERSION,
                "text-lf-1",
                sha256(canonical({p: sha256(b) for p, b in pins.items()})),
            )
    plan = preview(root, rendered, upgrade=args.command == "template-upgrade")
    if plan.conflicts:
        raise ValueError("generation conflicts: " + plan.to_json())
    if args.check and plan.drift:
        raise ValueError("generation drift: " + plan.to_json())
    if args.apply:
        apply(root, plan)
    result: dict[str, object] = json.loads(plan.to_json())
    return result
