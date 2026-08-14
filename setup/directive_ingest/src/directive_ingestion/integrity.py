"""Typed failures for invalid persisted publication data."""

from __future__ import annotations


class IntegrityValidationError(RuntimeError):
    """A stored publication record does not match its expected identity."""


class CatalogResetRequiredError(IntegrityValidationError):
    """A corrupt live catalog slot cannot be safely repaired in place."""
