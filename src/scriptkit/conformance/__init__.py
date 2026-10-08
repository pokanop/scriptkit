"""Read-only conformance checks; never an authorization or Python safety scanner."""

from .static import Diagnostic, Report, validate

__all__ = ["Diagnostic", "Report", "validate"]
