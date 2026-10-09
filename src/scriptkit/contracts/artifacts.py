"""Explicit finite resource budgets for untrusted installation artifacts."""

from dataclasses import dataclass, fields

from .codec import ContractError


@dataclass(frozen=True)
class ArtifactPolicy:
    """Per-artifact bounds; increasing these requires explicit caller consent.

    The default ratio equals the expanded-byte bound, preserving acceptance of
    existing archives (even a one-byte compressed member cannot exceed it).
    """

    max_archive_bytes: int = 64 * 1024 * 1024
    max_expanded_bytes: int = 128 * 1024 * 1024
    max_entries: int = 10000
    max_expansion_ratio: int = 128 * 1024 * 1024
    command_timeout: int = 120
    allow_member_spaces: bool = False

    def __post_init__(self) -> None:
        if type(self.allow_member_spaces) is not bool:
            raise ContractError("allow_member_spaces must be boolean")
        ceilings = (2 * 1024**3, 8 * 1024**3, 100000, 128 * 1024 * 1024, 3600)
        for field, ceiling in zip(fields(self), ceilings):
            value = getattr(self, field.name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ContractError(f"{field.name} outside finite artifact policy ceiling")


DEFAULT_ARTIFACT_POLICY = ArtifactPolicy()
