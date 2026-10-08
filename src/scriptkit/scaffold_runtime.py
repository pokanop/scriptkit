"""Runtime support for authored tools; no generator, registry or manager imports."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
import json
from pathlib import Path
import platform
import sys
from typing import Any

from .command import run
from .output import CommandResult, OutputContext, OutputPolicy


def tool_main(
    spec: Mapping[str, Any],
    handler: Callable[[str, dict[str, Any]], object],
    argv: list[str] | None = None,
) -> int:
    """Parse declarative commands, with read-only builtins and explicit dispatch."""
    parser = argparse.ArgumentParser(prog=spec["name"], description=spec["description"])
    parser.add_argument("--version", action="version", version=spec["version"])
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--config", type=Path, help="optional JSON object; never created implicitly"
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("doctor", help="inspect Python and configuration without mutation")
    commands.add_parser("config", help="show configuration (no writes)")
    for command in spec["commands"]:
        sub = commands.add_parser(
            command["name"],
            help=command["help"],
            add_help=not any(
                a["name"] == "help" and a["kind"] != "positional" for a in command["arguments"]
            ),
        )
        for arg in command["arguments"]:
            options: dict[str, Any] = {"help": arg["help"]}
            name = arg["name"]
            if arg["kind"] == "flag":
                options["action"] = "store_false" if arg["default"] else "store_true"
            else:
                options["type"] = int if arg["value_type"] == "integer" else str
                if arg["choices"]:
                    options["choices"] = arg["choices"]
                if arg["kind"] == "positional" and not arg["required"]:
                    options["nargs"] = "?"
            if arg["kind"] != "positional":
                name = "--" + name
                options.update(dest=arg["name"], required=arg["required"])
            if arg["default"] is not None:
                options["default"] = arg["default"]
            sub.add_argument(name, **options)
    arguments = list(sys.argv[1:] if argv is None else argv)
    context = OutputContext(OutputPolicy(machine="--json" in arguments))

    def execute() -> object:
        args = parser.parse_args(arguments)
        config = {} if args.config is None else json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("configuration must be a JSON object")
        if args.command is None:
            parser.print_help()
            return None
        if args.command == "doctor":
            return {"python": platform.python_version(), "config": "valid", "tool": spec["name"]}
        if args.command == "config":
            return config
        values = vars(args).copy()
        for key in ("command", "config", "json"):
            values.pop(key)
        try:
            result = handler(args.command, values)
            if type(result) is int:
                return CommandResult(
                    exit_code=result, error=f"exited with status {result}" if result else None
                )
            return result
        except NotImplementedError as exc:
            raise ValueError(
                f"Command {args.command!r} is not implemented; edit _handlers.py"
            ) from exc

    return run(context, execute)
