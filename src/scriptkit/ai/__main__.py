"""Optional BYOK proposals and offline review: python -m scriptkit.ai --help."""

import argparse
import json
from pathlib import Path

from . import Context, apply, parse_response, review, select
from .contracts import MAX_CONTEXT, MAX_RESPONSE
from .providers import HTTPProvider, Limits, ProviderError


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
    propose = sub.add_parser("propose", help="preview disclosure; sends only with exact approval")
    propose.add_argument("root", type=Path)
    propose.add_argument("paths", nargs="*")
    propose.add_argument("--provider", choices=("openai", "ollama"), required=True)
    propose.add_argument("--model", required=True)
    propose.add_argument("--goal", required=True)
    propose.add_argument("--endpoint", help="Ollama literal loopback URL only")
    propose.add_argument("--keyring", action="store_true")
    propose.add_argument("--max-requests", type=int, default=1)
    propose.add_argument("--max-tokens", type=int, default=32768)
    propose.add_argument("--max-output-tokens", type=int, default=4096)
    propose.add_argument("--retries", type=int, default=0)
    propose.add_argument("--timeout", type=float, default=30)
    propose.add_argument("--approve-disclosure", help="exact preview approval_hash")
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
        elif args.command == "propose":
            context = select(args.root, tuple(args.paths))
            provider = HTTPProvider(
                args.provider,
                args.model,
                args.goal,
                args.endpoint,
                Limits(
                    args.max_requests,
                    args.max_tokens,
                    args.max_output_tokens,
                    args.retries,
                    args.timeout,
                ),
                use_keyring=args.keyring,
            )
            plan = provider.preview(context)
            if args.approve_disclosure is None:
                print(json.dumps(plan, sort_keys=True))
            else:
                if args.approve_disclosure != plan["approval_hash"]:
                    raise ProviderError("exact disclosure preview approval required")
                try:
                    proposal = parse_response(
                        provider.propose(context, max_response_bytes=MAX_RESPONSE)
                    )
                    review(args.root, context, proposal)
                except ProviderError:
                    raise
                except (ValueError, RuntimeError, OSError):
                    raise ProviderError(
                        "provider proposal failed static review; edit manually"
                    ) from None
                print(proposal.canonical_json())
        else:
            context = Context.from_json(bounded(args.context, MAX_CONTEXT).decode("utf-8"))
            proposal = parse_response(bounded(args.proposal, MAX_RESPONSE))
            if args.command == "review":
                print(review(args.root, context, proposal).to_json())
            else:
                apply(args.root, context, proposal, approved_review_hash=args.approve)
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"AI proposal refused: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
