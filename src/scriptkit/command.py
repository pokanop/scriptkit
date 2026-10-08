"""Opt-in command boundary, separate from legacy dispatch/default injection."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from typing import Any

from .output import CommandResult, OutputContext


def parse_args(
    parser: argparse.ArgumentParser,
    argv: Sequence[str],
    *,
    default: str | None = None,
    allow_default: bool = False,
) -> argparse.Namespace:
    """Only an explicitly allowed *bare invocation* can run a default command.

    Unknown flags/commands remain argparse errors; help/version never run work.
    Applications own parser construction and opt-in --json flag resolution.
    """
    args = list(argv)
    if not args and default is not None and allow_default:
        args.append(default)
    return parser.parse_args(args)


def run(context: OutputContext, main: Callable[[], Any]) -> int:
    """Run a main-thread callback returning JSON-serializable result data.

    Machine mode routes incidental Python stdout (including argparse help) to
    diagnostics. This process-wide redirect is not a worker-thread sandbox and
    cannot intercept native writes/subprocess inherited descriptors: callers
    must capture those explicitly. Use injected context methods in workers.
    Unexpected errors are surfaced as error 1 without a traceback. Applications
    must redact sensitive exception messages before crossing this boundary.
    """
    if context._in_command:
        raise ValueError("nested command.run is not supported")
    code = 0
    data = None
    error = None
    try:
        with (
            redirect_stdout(context.stderr) if context.policy.machine else nullcontext(),
            redirect_stderr(context.stderr),
        ):
            try:
                context._in_command = True
                try:
                    data = main()
                finally:
                    context._in_command = False
                if isinstance(data, CommandResult):
                    code, error, data = data.exit_code, data.error, data.data
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
                error = (
                    str(exc.code)
                    if not isinstance(exc.code, (int, type(None)))
                    else ("usage error" if code == 2 else f"exited with status {code}")
                    if code
                    else None
                )
            except KeyboardInterrupt:
                code, error = 130, "Interrupted"
            except Exception as exc:
                if isinstance(exc, BrokenPipeError):
                    raise
                code, error = 1, str(exc) or type(exc).__name__
        if error:
            context.emit("error", error)
        if context.policy.machine:
            context.result(data, ok=code == 0, error=error)
        elif data is not None:
            context.result(data)
        context.stdout.flush()
        context.stderr.flush()
        return code
    except BrokenPipeError:
        # Close the failed stream so interpreter shutdown cannot retry its flush.
        try:
            context.stdout.close()
        except BrokenPipeError:
            pass
        return 0
    except (TypeError, ValueError) as exc:
        context.emit("error", f"result is not JSON serializable: {exc}")
        if context.policy.machine:
            context.result(None, ok=False, error="result is not JSON serializable")
        return 1
