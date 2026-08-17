"""Coverage-aware generic summaries for each directive version."""

from __future__ import annotations

import asyncio
from typing import Any

import tiktoken
from directive_contracts import DirectiveSummary

from .canonical import CanonicalDirective, ParsedSection
from .config import RetryPolicyConfig
from .provider_retry import RetryBudget, retry_provider_call
from .run_metrics import IngestionRunMetrics

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
        max_input_tokens: int,
        max_output_tokens: int,
        concurrency: int,
        retry_policy: RetryPolicyConfig | None = None,
    ) -> None:
        if (
            full_document_tokens < 1
            or batch_tokens < 1
            or max_input_tokens < 1
            or max_output_tokens < 1
            or concurrency < 1
        ):
            raise ValueError("Summary limits must be positive")
        if full_document_tokens > max_input_tokens or batch_tokens > max_input_tokens:
            raise ValueError("Summary thresholds exceed the hard input limit")
        self._client = openai_client
        self._deployment = deployment
        self._full_document_tokens = full_document_tokens
        self._batch_tokens = batch_tokens
        self._max_input_tokens = max_input_tokens
        self._max_output_tokens = max_output_tokens
        self._semaphore = asyncio.Semaphore(concurrency)
        self._retry_policy = retry_policy or RetryPolicyConfig(
            5,
            1.0,
            30.0,
            0.2,
            12,
        )
        self._retry_budget = RetryBudget(
            self._retry_policy.stage_retry_budget
        )
        self._metrics: IngestionRunMetrics | None = None

    def attach_metrics(self, metrics: IngestionRunMetrics | None) -> None:
        self._metrics = metrics
        self._retry_budget = RetryBudget(
            self._retry_policy.stage_retry_budget
        )

    async def summarize(
        self, directive: CanonicalDirective
    ) -> DirectiveSummary:
        full_prompt = (
            "Summarize this complete directive. Account for every section "
            "and table.\n\n"
            f"{directive.markdown}"
        )
        if (
            directive.total_tokens <= self._full_document_tokens
            and self._request_tokens(full_prompt) <= self._max_input_tokens
        ):
            text = await self._complete(full_prompt)
            strategy = "full_document"
        else:
            batches = _summary_batches(
                directive.sections,
                min(self._batch_tokens, self._max_input_tokens // 2),
            )
            batch_summaries = await asyncio.gather(
                *(
                    self._complete(
                        f"Summarize section batch {number}. Covered section "
                        f"IDs: {', '.join(section_ids)}.\n\n{content}"
                    )
                    for number, (content, section_ids) in enumerate(
                        batches,
                        1,
                    )
                )
            )
            text = await self._reduce_summaries(list(batch_summaries))
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

    async def _complete(
        self,
        prompt: str,
        *,
        max_output_tokens: int | None = None,
    ) -> str:
        request_tokens = self._request_tokens(prompt)
        if request_tokens > self._max_input_tokens:
            raise ValueError("Summary request exceeds the hard input limit")
        output_limit = max_output_tokens or self._max_output_tokens

        async def operation() -> Any:
            return await self._client.responses.create(
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
                max_output_tokens=output_limit,
            )

        def record_attempt() -> None:
            if self._metrics is not None:
                self._metrics.increment("summary_requests")
                self._metrics.increment(
                    "summary_input_tokens",
                    request_tokens,
                )

        async with self._semaphore:
            response = await retry_provider_call(
                operation,
                policy=self._retry_policy,
                budget=self._retry_budget,
                on_attempt=record_attempt,
                on_retry=self._record_retry,
                on_throttle=self._record_throttle,
            )
        output = str(getattr(response, "output_text", "") or "").strip()
        if not output:
            raise RuntimeError("Summary model returned no text")
        if self._metrics is not None:
            self._metrics.increment(
                "summary_output_tokens",
                len(_TOKENIZER.encode(output)),
            )
        return output

    async def _reduce_summaries(self, summaries: list[str]) -> str:
        if not summaries:
            raise ValueError("Summary reduction requires input")
        round_number = 0
        while True:
            synthesis = "\n\n".join(
                f"## Batch {index}\n{summary}"
                for index, summary in enumerate(summaries, 1)
            )
            prompt = (
                "Synthesize the complete directive summary from every ordered "
                "batch below. Do not omit a batch. Resolve no conflicts by "
                f"guessing.\n\n{synthesis}"
            )
            if self._request_tokens(prompt) <= self._max_input_tokens:
                return await self._complete(prompt)
            groups = _text_batches(
                summaries,
                min(self._batch_tokens, self._max_input_tokens // 2),
            )
            summaries = list(
                await asyncio.gather(
                    *(
                        self._complete(
                            "Reduce this ordered summary group without omitting "
                            f"facts or resolving conflicts by guessing.\n\n{group}",
                            max_output_tokens=max(
                                32,
                                min(
                                    self._max_output_tokens,
                                    self._batch_tokens // 4,
                                ),
                            ),
                        )
                        for group in groups
                    )
                )
            )
            round_number += 1
            if self._metrics is not None:
                self._metrics.increment("summary_hierarchy_depth")
            if round_number > 32:
                raise RuntimeError("Summary hierarchy did not converge")

    def _request_tokens(self, prompt: str) -> int:
        return len(_TOKENIZER.encode(_SYSTEM_PROMPT)) + len(
            _TOKENIZER.encode(prompt)
        )

    def _record_retry(self, error: Exception) -> None:
        del error
        if self._metrics is None:
            return
        self._metrics.increment("retry_count")
        self._metrics.increment("retry_openai")

    def _record_throttle(self, error: Exception) -> None:
        del error
        if self._metrics is not None:
            self._metrics.increment("throttle_openai")


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


def _summary_batches(
    sections: tuple[ParsedSection, ...],
    token_limit: int,
) -> list[tuple[str, tuple[str, ...]]]:
    units: list[tuple[str, str]] = []
    for section in sections:
        tokens = _TOKENIZER.encode(section.content)
        if len(tokens) <= token_limit:
            units.append((section.content, section.section_id))
            continue
        start = 0
        part = 1
        while start < len(tokens):
            end = min(start + token_limit, len(tokens))
            units.append(
                (
                    f"[{section.section_id} continuation {part}]\n"
                    + _TOKENIZER.decode(tokens[start:end]),
                    section.section_id,
                )
            )
            start = end
            part += 1
    batches: list[tuple[str, tuple[str, ...]]] = []
    current: list[tuple[str, str]] = []
    current_tokens = 0
    for content, section_id in units:
        tokens = len(_TOKENIZER.encode(content))
        if current and current_tokens + tokens > token_limit:
            batches.append(
                (
                    "\n\n".join(value for value, _ in current),
                    tuple(dict.fromkeys(value for _, value in current)),
                )
            )
            current = []
            current_tokens = 0
        current.append((content, section_id))
        current_tokens += tokens
    if current:
        batches.append(
            (
                "\n\n".join(value for value, _ in current),
                tuple(dict.fromkeys(value for _, value in current)),
            )
        )
    return batches


def _text_batches(values: list[str], token_limit: int) -> list[str]:
    units: list[str] = []
    for value in values:
        tokens = _TOKENIZER.encode(value)
        units.extend(
            _TOKENIZER.decode(tokens[start : start + token_limit])
            for start in range(0, len(tokens), token_limit)
        )
    batches: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for value in units:
        tokens = len(_TOKENIZER.encode(value))
        if current and current_tokens + tokens > token_limit:
            batches.append("\n\n".join(current))
            current = []
            current_tokens = 0
        current.append(value)
        current_tokens += tokens
    if current:
        batches.append("\n\n".join(current))
    return batches
