"""Stable data-only contracts, independent of manager/generator/AI packages."""

from .artifacts import ArtifactPolicy
from .codec import ContractError, resource_text
from .models import (
    AIProposal,
    ArgumentSpec,
    Artifact,
    CatalogRelease,
    CommandSpec,
    CONTRACTS,
    DependencyLock,
    Generation,
    InstallPlan,
    LockedPackage,
    Platform,
    PythonRequirement,
    Receipt,
    ToolRelease,
    ToolSpec,
)
from .ports import ArtifactSource, Clock, FileSystem, Output, PackageBackend, ProposalProvider

__all__ = [
    "AIProposal",
    "ArgumentSpec",
    "Artifact",
    "ArtifactPolicy",
    "ArtifactSource",
    "CatalogRelease",
    "Clock",
    "CommandSpec",
    "CONTRACTS",
    "ContractError",
    "DependencyLock",
    "FileSystem",
    "Generation",
    "InstallPlan",
    "LockedPackage",
    "Output",
    "PackageBackend",
    "Platform",
    "ProposalProvider",
    "PythonRequirement",
    "Receipt",
    "ToolRelease",
    "ToolSpec",
    "resource_text",
]
