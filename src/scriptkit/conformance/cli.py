"""CLI adapter for conformance; no manager or provider coupling."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from scriptkit.output import CommandResult, OutputContext
from .static import validate


def configure(commands: Any) -> None:
    parser = commands.add_parser(
        "validate", help="static tool conformance (never a safety certification)"
    )
    parser.add_argument("project", type=Path)
    parser.add_argument(
        "--runtime-fixtures",
        action="store_true",
        help="also run packaged reference fixtures, not project code",
    )
    parser.add_argument(
        "--allow-execution",
        action="store_true",
        help="explicit consent to execute reference fixtures",
    )


def dispatch(args: argparse.Namespace, context: OutputContext) -> CommandResult:
    if args.runtime_fixtures and not args.allow_execution:
        raise ValueError("--runtime-fixtures requires --allow-execution")
    report = validate(args.project.absolute())
    data = report.to_data()
    valid = report.valid
    if args.runtime_fixtures:
        from .runtime import check_fixtures

        runtime = check_fixtures(allow_execution=args.allow_execution)
        data["runtime"] = runtime
        valid = valid and bool(runtime["valid"])
    human = "\n".join(f"{d.path}:{d.line}: {d.code}: {d.message}" for d in report.diagnostics)
    human = (human or "Static conformance passed.") + "\n" + str(data["safety"])
    if args.runtime_fixtures:
        human += f"\nPackaged runtime fixtures: {'passed' if runtime['valid'] else 'failed'} (not project execution)."
    return CommandResult(
        data if context.policy.machine else human,
        0 if valid else 1,
        None if valid else "Conformance checks failed",
    )
