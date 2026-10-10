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
from scriptkit.output import CommandResult, OutputContext
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
    from .generator.cli import configure as configure_authoring

    configure_authoring(commands)
    from .conformance.cli import configure as configure_conformance

    configure_conformance(commands)
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
    prune = commands.add_parser("prune", help="reclaim retired receipt-owned generations")
    prune.add_argument(
        "--keep", type=int, default=1, help="previous generations to keep (minimum 1)"
    )
    prune.add_argument("--uninstalled", action="store_true", help="include uninstalled tools")
    prune.add_argument("--dry-run", action="store_true")
    update = commands.add_parser("self-update")
    update.add_argument("--wheel", required=True)
    update.add_argument("--sha256", required=True)
    update.add_argument("--version", required=True)
    commands.add_parser("self-rollback")


def dispatch(args: argparse.Namespace, context: OutputContext) -> object:
    from .generator.cli import COMMANDS, dispatch as dispatch_authoring

    if args.command == "validate":
        from .conformance.cli import dispatch as dispatch_conformance

        return dispatch_conformance(args, context)
    if args.command in COMMANDS:
        authored = dispatch_authoring(args)
        return CommandResult(
            data=authored.data if context.policy.machine else authored.human,
            exit_code=authored.exit_code,
            error=authored.error,
        )
    root = args.root.resolve()
    if args.command == "doctor":
        import importlib.util

        state = read(root / "manager-active.json")
        health = {
            "python": platform.python_version(),
            "venv": importlib.util.find_spec("venv") is not None,
            "root": str(root),
            "manager": state,
            "bin": str(root / "bin"),
            "on_path": str(root / "bin") in os.environ.get("PATH", "").split(os.pathsep),
            "guidance": "Install Python 3.11+ with venv/ensurepip. Add the bin directory to PATH explicitly; rerun the pinned bootstrap to repair.",
        }
        reclamation: dict[str, object] = {"bytes": None, "estimated": True}
        try:
            installer = Installer(root / "tools", root / "bin", None)  # type: ignore[arg-type]
            reclamation["bytes"] = installer.reclamation_estimate()
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            # A damaged or concurrently changing tool must not hide manager health.
            reclamation["error"] = str(exc)
        reclamation_summary = (
            f"Estimated reclaimable: {reclamation['bytes']} bytes (run prune --dry-run to verify)"
            if reclamation["bytes"] is not None
            else f"Reclaimable estimate unavailable: {reclamation['error']}"
        )
        health["reclamation"] = reclamation
        health["guidance"] = str(health["guidance"]) + (
            " Reclaim retired tools: scriptkit prune --dry-run, then scriptkit prune (optionally --uninstalled)."
        )
        if context.policy.machine:
            return health
        return "\n".join(
            (
                f"Python: {health['python']}",
                f"venv available: {'yes' if health['venv'] else 'no'}",
                f"Root: {root}",
                f"Manager generation: {state.get('target') or 'not installed'}",
                f"Previous generation: {state.get('previous') or 'none'}",
                f"Commands: {health['bin']}",
                f"Commands on PATH: {'yes' if health['on_path'] else 'no'}",
                reclamation_summary,
                str(health["guidance"]),
            )
        )
    if args.command in ("self-update", "self-rollback"):
        manager = Manager(root, checkpoint=lambda phase: context.emit("info", phase))
        result = (
            manager.rollback()
            if args.command == "self-rollback"
            else manager.install(args.wheel, args.sha256, args.version)
        )
        if context.policy.machine:
            return result
        verb = "Rolled back" if args.command == "self-rollback" else "Updated"
        return f"{verb} manager to {result['version']}."
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
        entries = store.list()
        if context.policy.machine:
            return [entry.to_dict() for entry in entries]
        return (
            "\n".join(f"{entry.namespace}: {entry.origin}" for entry in entries)
            or "No registries registered."
        )
    if args.command == "catalog":
        releases = resolver.list(args.namespace, offline=args.offline)
        return (
            releases
            if context.policy.machine
            else "\n".join(releases) or "No tools in this catalog."
        )
    namespace = args.target.split("/")[0] if args.command in ("install", "update") else "unused"
    installer = Installer(
        root / "tools",
        root / "bin",
        RegistryArtifactSource(resolver, namespace, offline=getattr(args, "offline", False)),
        checkpoint=lambda phase: context.emit("info", phase) if phase != "prune-file" else None,
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
        if context.policy.machine:
            return {"plan": resolved.to_dict(), "receipt": str(receipt) if receipt else None}
        if args.dry_run:
            return f"Dry run: would {args.command} {args.target}; no changes made."
        verb = "Installed" if args.command == "install" else "Updated"
        return f"{verb} {args.target}."
    if args.command == "prune":
        result = installer.prune(keep=args.keep, uninstalled=args.uninstalled, dry_run=args.dry_run)
        if context.policy.machine:
            return result
        return "\n".join(
            [
                f"{'Would reclaim' if args.dry_run else 'Reclaimed'} {result['bytes']} bytes.",
                *result["paths"],
                *(f"Preserved foreign: {path}" for path in result["foreign"]),
            ]
        )
    getattr(installer, args.command)(args.name)
    if context.policy.machine:
        return {"tool": args.name, "action": args.command}
    return {
        "rollback": f"Rolled back {args.name} to the previous generation.",
        "recover": f"Recovery complete for {args.name}.",
        "uninstall": f"Uninstalled {args.name}; user data and config preserved.",
    }[args.command]
