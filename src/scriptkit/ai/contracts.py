"""Versioned, data-only AI boundary. Unknown fields are always rejected."""

from hashlib import sha256

from scriptkit.contracts import ToolSpec
from scriptkit.contracts.codec import Record
from scriptkit.contracts.models import constrained, digest, path, record, unique

MAX_RESPONSE = 256 * 1024
MAX_CONTEXT = 512 * 1024


@record
class ContextFile(Record):
    path: str = path()
    content: str | None = constrained(maxLength=65536)

    @property
    def base_hash(self) -> str | None:
        return sha256(self.content.encode()).hexdigest() if self.content is not None else None


@record
class Context(Record):
    schema_version: int = constrained(const=1)
    spec: ToolSpec
    files: tuple[ContextFile, ...]

    def validate(self) -> None:
        unique([f.path for f in self.files], "context paths")
        if len(self.files) > 32 or len(self.canonical_json().encode()) > MAX_CONTEXT:
            raise ValueError("context budget exceeded")

    @property
    def identity(self) -> str:
        return sha256(self.canonical_json().encode()).hexdigest()


@record
class CommandHelp(Record):
    name: str
    help: str = constrained(maxLength=4096)


@record
class SpecDelta(Record):
    description: str | None = constrained(maxLength=4096)
    command_help: tuple[CommandHelp, ...]

    def validate(self) -> None:
        unique([c.name for c in self.command_help], "command help")


@record
class Patch(Record):
    path: str = path()
    base_hash: str | None = digest()
    content: str = constrained(maxLength=65536)


@record
class Proposal(Record):
    schema_version: int = constrained(const=1)
    context_hash: str = digest()
    spec_delta: SpecDelta
    patches: tuple[Patch, ...]
    # Informational only: never passed to an installer or generation backend.
    dependency_suggestions: tuple[str, ...]

    def validate(self) -> None:
        unique([p.path for p in self.patches], "patch paths")
        if len(self.patches) > 32 or len(self.canonical_json().encode()) > MAX_RESPONSE:
            raise ValueError("proposal budget exceeded")


def parse_response(raw: bytes) -> Proposal:
    if len(raw) > MAX_RESPONSE:
        raise ValueError("provider response budget exceeded")
    return Proposal.from_json(raw.decode("utf-8"))
