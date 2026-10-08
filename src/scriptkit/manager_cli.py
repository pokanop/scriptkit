"""Public CLI composition: registry, installer and bootstrap stay separate services."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import sys
import uuid

from scriptkit.bootstrap import Manager, default_root, read
from scriptkit.contracts.catalog import Registry
from scriptkit.contracts.models import Platform
from scriptkit.output import OutputContext
from scriptkit.registry.cache import VerifiedCache
from scriptkit.registry.resolver import Resolver
from scriptkit.registry.source import RegistryArtifactSource
from scriptkit.registry.trust import RegistryStore

from .manager.service import Installer


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root", type=Path, default=Path(os.environ.get("SCRIPTKIT_ROOT", str(default_root())))
    )
    parser.add_argument("--json", action="store_true", help="versioned machine output")
    parser.add_argument("--quiet", action="store_true")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("doctor", help="inspect manager/Python/PATH without mutation")
    registry = commands.add_parser("registry").add_subparsers(dest="action", required=True)
    registry.add_parser("list")
    add = registry.add_parser("add")
    add.add_argument("file", type=Path, help="pinned Registry JSON contract")
    add.add_argument("--trust-origin", required=True)
    registry.add_parser("remove").add_argument("namespace")
    catalog = commands.add_parser("catalog")
    catalog.add_argument("namespace")
    catalog.add_argument("--offline", action="store_true")
    for verb in ("install", "update"):
        install = commands.add_parser(verb)
        install.add_argument("target", help="namespace/tool@exact-version")
        install.add_argument("--offline", action="store_true")
        install.add_argument("--dry-run", action="store_true")
    for verb in ("rollback", "recover", "uninstall"):
        commands.add_parser(verb).add_argument("name")
    update = commands.add_parser("self-update")
    update.add_argument("--wheel", required=True)
    update.add_argument("--sha256", required=True)
    update.add_argument("--version", required=True)
    commands.add_parser("self-rollback")


def dispatch(args: argparse.Namespace, context: OutputContext) -> object:
    root = Path(os.path.abspath(args.root))
    if args.command == "doctor":
        import importlib.util

        return {
            "python": platform.python_version(),
            "venv": importlib.util.find_spec("venv") is not None,
            "root": str(root),
            "manager": read(root / "manager-active.json"),
            "bin": str(root / "bin"),
            "on_path": str(root / "bin") in os.environ.get("PATH", "").split(os.pathsep),
            "guidance": "Install Python 3.11+ with venv/ensurepip. Add the bin directory to PATH explicitly; rerun the pinned bootstrap to repair.",
        }
    if args.command in ("self-update", "self-rollback"):
        manager = Manager(root, checkpoint=lambda phase: context.emit("info", phase))
        return (
            manager.rollback()
            if args.command == "self-rollback"
            else manager.install(args.wheel, args.sha256, args.version)
        )
    store = RegistryStore(root / "registries.json")
    resolver = Resolver(store, VerifiedCache(root / "cache"))
    if args.command == "registry":
        if args.action == "add":
            store.add(
                Registry.from_json(args.file.read_text(encoding="utf-8")),
                consent_origin=args.trust_origin,
            )
        elif args.action == "remove":
            store.remove(args.namespace)
        return [entry.to_dict() for entry in store.list()]
    if args.command == "catalog":
        return resolver.list(args.namespace, offline=args.offline)
    namespace = args.target.split("/")[0] if args.command in ("install", "update") else "unused"
    installer = Installer(
        root / "tools",
        root / "bin",
        RegistryArtifactSource(resolver, namespace, offline=getattr(args, "offline", False)),
        checkpoint=lambda phase: context.emit("info", phase),
        launcher_interpreter=str(Path(getattr(sys, "_base_executable", sys.executable)).resolve()),
    )
    if args.command in ("install", "update"):
        try:
            name = args.target.split("/")[1].split("@")[0]
        except IndexError as exc:
            raise ValueError("use namespace/tool@exact-version") from exc
        if name == "scriptkit":
            raise ValueError("scriptkit is reserved for the manager")
        host = Platform(
            {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}[platform.system()],
            {"amd64": "x86_64", "aarch64": "arm64"}.get(
                platform.machine().lower(), platform.machine().lower()
            ),
        )
        resolved = resolver.resolve(
            args.target,
            platform=host,
            python_version=platform.python_version(),
            generation="g-" + uuid.uuid4().hex,
            destination=name,
            previous_generation=installer.current_generation(name),
            offline=args.offline,
        )
        with context.progress("Installing tool"):
            receipt = installer.install(resolved, dry_run=args.dry_run)
        return {"plan": resolved.to_dict(), "receipt": str(receipt) if receipt else None}
    getattr(installer, args.command)(args.name)
    return {"tool": args.name, "action": args.command}
