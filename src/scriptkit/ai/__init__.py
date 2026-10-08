"""Optional AI proposals. Offline generation does not import this package."""

from .context import FakeProvider, Provider, request, select
from .contracts import CommandHelp, Context, ContextFile, Patch, Proposal, SpecDelta, parse_response
from .review import Review, apply, review

__all__ = [
    "CommandHelp",
    "Context",
    "ContextFile",
    "FakeProvider",
    "Patch",
    "Proposal",
    "Provider",
    "Review",
    "SpecDelta",
    "apply",
    "parse_response",
    "request",
    "review",
    "select",
]
