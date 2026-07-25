"""Explicit failures for directive data-plane access."""

from __future__ import annotations


class DirectiveDataUnavailable(RuntimeError):
    """A required directive data plane could not complete an operation."""


class DirectiveContentTooLarge(RuntimeError):
    """A requested content unit cannot fit within the configured tool budget."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("Directive content exceeds the configured tool budget")
        self.detail = detail
