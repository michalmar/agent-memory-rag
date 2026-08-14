"""Typed failures for invalid persisted publication data."""

from __future__ import annotations


class IntegrityValidationError(RuntimeError):
    """A stored publication record does not match its expected identity."""
