"""Public command boundary; lazy manager composition preserves help/version safety."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys

from . import __version__
from .command import run
from .output import OutputContext, OutputPolicy


def main(argv: Sequence[str] | None = None) -> int:
    from .manager_cli import configure, dispatch

    arguments = list(sys.argv[1:] if argv is None else argv)
    context = OutputContext(
        OutputPolicy(machine="--json" in arguments, quiet="--quiet" in arguments)
    )

    parser = argparse.ArgumentParser(
        prog="scriptkit", description="ScriptKit runtime and isolated tool manager."
    )
    parser.add_argument("--version", action="version", version=f"scriptkit {__version__}")
    configure(parser)

    def execute(args: argparse.Namespace) -> object:
        if args.command is None:
            parser.print_help()
            return None
        return dispatch(args, context)

    if context.policy.machine:
        return run(context, lambda: execute(parser.parse_args(arguments)))
    # Preserve the legacy embedding contract: argparse raises SystemExit.
    args = parser.parse_args(arguments)
    return run(context, lambda: execute(args))
