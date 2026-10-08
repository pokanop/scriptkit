"""Standalone command boundary; intentionally no manager/provider imports."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from . import __version__


def main(argv: Sequence[str] | None = None) -> int:
    """Show discovery help or version; argparse owns usage errors (exit 2).

    Inject argv for embedding/tests rather than mutating process-global state.
    Future commands belong to their own components, not this composition root.
    """
    parser = argparse.ArgumentParser(
        prog="scriptkit",
        description="ScriptKit runtime foundation (working name).",
        epilog="Manager, registry, generator and AI commands are not yet available.",
    )
    parser.add_argument("--version", action="version", version=f"scriptkit {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0
