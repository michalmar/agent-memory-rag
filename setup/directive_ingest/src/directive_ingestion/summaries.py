"""Coverage-aware generic summaries for each directive version."""

from __future__ import annotations

from typing import Any

import tiktoken
from directive_contracts import DirectiveSummary

from .canonical import CanonicalDirective, ParsedSection

_TOKENIZER = tiktoken.get_encoding("o200k_base")
_SYSTEM_PROMPT = """\
You summarize internal company directives for later grounded question answering.
Cover purpose, scope, eligibility or obligations, procedures, exceptions,
deadlines, approvals, and important table content. Preserve exact thresholds,
dates, and system steps. Do not invent facts or legal conclusions. Clearly say
when the source is ambiguous. Return concise Markdown without citations because
the retrieval layer attaches section and page citations separately.
"""


class SummaryGenerator:
    def __init__(
        self,
        openai_client: Any,
        deployment: str,
        *,
        full_document_tokens: int,
        batch_tokens: int,
    ) -> None:
        self._client = openai_client
        self._deployment = deployment
        self._full_document_tokens = full_document_tokens
        self._batch_tokens = batch_tokens

    async def summarize(
        self, directive: CanonicalDirective
    ) -> DirectiveSummary:
        if directive.total_tokens <= self._full_document_tokens:
            text = await self._complete(
                "Summarize this complete directive. Account for every section "
                "and table.\n\n"
                f"{directive.markdown}"
            )
            strategy = "full_document"
        else:
            batch_summaries: list[str] = []
            for number, batch in enumerate(
                _section_batches(directive.sections, self._batch_tokens), 1
            ):
                content = "\n\n".join(section.content for section in batch)
                section_ids = ", ".join(
                    section.section_id for section in batch
                )
                batch_summaries.append(
                    await self._complete(
                        f"Summarize section batch {number}. Covered section "
                        f"IDs: {section_ids}.\n\n{content}"
                    )
                )
            synthesis = "\n\n".join(
                f"## Batch {index}\n{summary}"
                for index, summary in enumerate(batch_summaries, 1)
            )
            text = await self._complete(
                "Synthesize the complete directive summary from every ordered "
                "batch below. Do not omit a batch. Resolve no conflicts by "
                f"guessing.\n\n{synthesis}"
            )
            strategy = "section_batches"
        return DirectiveSummary(
            directive_id=directive.metadata.directive_id,
            directive_version_id=directive.metadata.directive_version_id,
            source_hash=directive.metadata.source_hash,
            summary=text,
            covered_section_ids=[
                section.section_id for section in directive.sections
            ],
            total_section_count=len(directive.sections),
            input_token_count=directive.total_tokens,
            strategy=strategy,
            model_deployment=self._deployment,
        )

    async def _complete(self, prompt: str) -> str:
        response = await self._client.responses.create(
            model=self._deployment,
            input=[
                {
                    "role": "system",
                    "content": [
                        {"type": "input_text", "text": _SYSTEM_PROMPT}
                    ],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                },
            ],
            max_output_tokens=3000,
        )
        output = str(getattr(response, "output_text", "") or "").strip()
        if not output:
            raise RuntimeError("Summary model returned no text")
        return output


def _section_batches(
    sections: tuple[ParsedSection, ...], token_limit: int
) -> list[list[ParsedSection]]:
    batches: list[list[ParsedSection]] = []
    current: list[ParsedSection] = []
    current_tokens = 0
    for section in sections:
        if current and current_tokens + section.token_count > token_limit:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(section)
        current_tokens += section.token_count
    if current:
        batches.append(current)
    return batches
