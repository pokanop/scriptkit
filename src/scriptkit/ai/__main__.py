"""Offline file-based proposal review: python -m scriptkit.ai --help."""

import argparse
from pathlib import Path

from . import Context, apply, parse_response, review, select
from .contracts import MAX_CONTEXT, MAX_RESPONSE


def bounded(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("input file budget exceeded")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    selection = sub.add_parser("context", help="preview exact provider context; never sends it")
    selection.add_argument("root", type=Path)
    selection.add_argument("paths", nargs="*")
    for name in ("review", "apply"):
        command = sub.add_parser(name)
        command.add_argument("root", type=Path)
        command.add_argument("context", type=Path)
        command.add_argument("proposal", type=Path)
        if name == "apply":
            command.add_argument("--approve", required=True, help="exact review approval_hash")
    args = parser.parse_args(argv)
    try:
        if args.command == "context":
            print(select(args.root, tuple(args.paths)).canonical_json())
        else:
            context = Context.from_json(bounded(args.context, MAX_CONTEXT).decode("utf-8"))
            proposal = parse_response(bounded(args.proposal, MAX_RESPONSE))
            if args.command == "review":
                print(review(args.root, context, proposal).to_json())
            else:
                apply(args.root, context, proposal, approved_review_hash=args.approve)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"AI proposal refused: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
