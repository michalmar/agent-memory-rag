"""Versioned prompt sources used by the production agents."""

from __future__ import annotations

import hashlib
from pathlib import Path

_PROMPT_PATH = Path(__file__).with_name("customer_support.txt")
_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")
PROMPT_VERSION = hashlib.sha256(_PROMPT.encode("utf-8")).hexdigest()

_FOUNDRY_PROMPT_PATH = Path(__file__).with_name("foundry_prompt.txt")
_FOUNDRY_PROMPT = _FOUNDRY_PROMPT_PATH.read_text(encoding="utf-8")
FOUNDRY_PROMPT_VERSION = hashlib.sha256(_FOUNDRY_PROMPT.encode("utf-8")).hexdigest()

_DIRECTIVE_RAG_PATH = Path(__file__).with_name("directive_rag.txt")
_DIRECTIVE_RAG_PROMPT = _DIRECTIVE_RAG_PATH.read_text(encoding="utf-8")
DIRECTIVE_RAG_PROMPT_VERSION = hashlib.sha256(
    _DIRECTIVE_RAG_PROMPT.encode("utf-8")
).hexdigest()


def render_instructions() -> str:
    return _PROMPT


def render_foundry_prompt_instructions() -> str:
    return _FOUNDRY_PROMPT


def render_directive_rag_instructions() -> str:
    return _DIRECTIVE_RAG_PROMPT
