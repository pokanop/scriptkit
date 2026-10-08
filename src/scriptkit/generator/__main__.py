"""python -m scriptkit.generator SPEC ROOT [--check | --apply | --recover]."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scriptkit.contracts import ToolSpec
from .transaction import StateConflict

from . import apply, preview, recover, render


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("root", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--recover", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.recover:
            print(json.dumps({"recovered": recover(args.root)}, sort_keys=True))
            return 0
        spec = ToolSpec.from_json(args.spec.read_text(encoding="utf-8"))
        plan = preview(args.root, render(spec))
        if args.apply:
            apply(args.root, plan)
        print(plan.to_json(), end="")
        return 2 if plan.conflicts else int(args.check and plan.drift)
    except (ValueError, OSError, StateConflict) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
