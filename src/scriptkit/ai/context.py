"""Explicit selection before disclosure; never crawl a home directory or credentials."""

from pathlib import Path
import re
from typing import Protocol

from scriptkit.generator.plan import MANIFEST, Manifest, read, target
from scriptkit.generator.scaffolds import existing
from .contracts import Context, ContextFile, MAX_RESPONSE, Proposal, parse_response


def extension(spec_entrypoint: str, path: str) -> bool:
    package = spec_entrypoint.split(":")[0].rsplit(".", 1)[0].replace(".", "/")
    return path == f"src/{package}/_handlers.py" or bool(
        re.fullmatch(r"(?:tests/test_ai_[a-z0-9_]+\.py|examples/ai_[a-z0-9_]+\.md)", path)
    )


def select(root: Path, paths: tuple[str, ...]) -> Context:
    """Return exact provider-visible bytes for human inspection, without sending anything.

    Only v2 generated tools and narrow user-owned extension points are supported.
    Users must inspect selected content for secrets before approving disclosure.
    """
    spec, _ = existing(root)
    raw = read(root, MANIFEST)
    if raw is None or Manifest.from_json(raw.decode()).template != "2.0.0":
        raise ValueError("AI proposals require a v2 generated tool")
    owned = {f.path for f in Manifest.from_json(raw.decode()).generated}
    selected = []
    for path in paths:
        if not extension(spec.entrypoint, path) or path in owned:
            raise ValueError(f"not an approved user extension: {path}")
        file = target(root, path)
        if file.exists() and file.stat().st_size > 65536:
            raise ValueError("context file budget exceeded")
        data = read(root, path)
        selected.append(ContextFile(path, data.decode("utf-8") if data is not None else None))
    return Context(1, spec, tuple(selected))


class Provider(Protocol):
    def propose(self, context: Context, *, max_response_bytes: int) -> bytes:
        """Return bounded JSON, treating context content as untrusted data, not instructions."""
        ...


def request(provider: Provider, context: Context, *, approved_context_hash: str) -> Proposal:
    if approved_context_hash != context.identity:
        raise ValueError("explicit approval of the exact context is required before disclosure")
    return parse_response(provider.propose(context, max_response_bytes=MAX_RESPONSE))


class FakeProvider:
    """Offline contract harness; no inference, credentials, network or execution."""

    def __init__(self, response: bytes) -> None:
        self.response = response
        self.calls: list[Context] = []

    def propose(self, context: Context, *, max_response_bytes: int) -> bytes:
        self.calls.append(context)
        if len(self.response) > max_response_bytes:
            raise ValueError("provider response budget exceeded")
        return self.response
