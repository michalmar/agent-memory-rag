"""Content-safe bounded metrics for every ingestion attempt."""

from __future__ import annotations

import resource
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Iterator

_STAGES = {
    "planning",
    "source_listing",
    "download",
    "cache_lookup",
    "cache_write",
    "extraction",
    "metadata",
    "canonicalization",
    "chunking",
    "summary",
    "embedding",
    "staging",
    "blob_staging",
    "cosmos_staging",
    "search_publication",
    "catalog_publication",
    "activation",
    "verification",
    "cleanup",
}
_COUNTERS = {
    "descriptor_count",
    "source_listed_bytes",
    "source_download_count",
    "source_download_bytes",
    "cache_hit_count",
    "cache_miss_count",
    "cache_invalidation_count",
    "cache_fallback_count",
    "document_intelligence_requests",
    "document_intelligence_poll_count",
    "summary_requests",
    "summary_input_tokens",
    "summary_output_tokens",
    "summary_hierarchy_depth",
    "embedding_requests",
    "embedding_items",
    "embedding_input_tokens",
    "search_requests",
    "search_action_count",
    "search_query_count",
    "search_visibility_poll_count",
    "search_documents",
    "catalog_reads",
    "catalog_writes",
    "blob_reads",
    "blob_writes",
    "cosmos_reads",
    "cosmos_writes",
    "retry_count",
    "retry_document_intelligence",
    "retry_openai",
    "retry_search",
    "throttle_document_intelligence",
    "throttle_openai",
    "throttle_search",
    "changed_count",
    "skipped_count",
    "repaired_count",
    "quarantined_count",
    "deleted_count",
}


@dataclass(slots=True)
class IngestionRunMetrics:
    run_id: str
    operation: str
    processing_hash: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    stage_durations_ms: dict[str, int] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    status: str = "running"
    error_code: str | None = None
    publication_result: str | None = None
    _started_ns: int = field(default_factory=time.monotonic_ns, repr=False)
    _activation_started_ns: int | None = field(default=None, repr=False)
    _activation_duration_ms: int | None = field(default=None, repr=False)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if name not in _STAGES:
            raise ValueError(f"Unsupported ingestion metric stage: {name}")
        started = time.monotonic_ns()
        try:
            yield
        finally:
            elapsed = max(0, (time.monotonic_ns() - started) // 1_000_000)
            self.stage_durations_ms[name] = (
                self.stage_durations_ms.get(name, 0) + elapsed
            )

    def increment(self, name: str, value: int = 1) -> None:
        if name not in _COUNTERS:
            raise ValueError(f"Unsupported ingestion metric counter: {name}")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("Ingestion metric increments must be non-negative")
        self.counters[name] = self.counters.get(name, 0) + value

    def succeed(self) -> None:
        self.status = "succeeded"
        self.error_code = None

    def skip(self) -> None:
        self.status = "skipped"
        self.error_code = None

    def fail(self, error_code: str) -> None:
        if not error_code or len(error_code) > 128:
            raise ValueError("Ingestion metric error code is invalid")
        self.status = "failed"
        self.error_code = error_code

    def begin_activation_gate(self) -> None:
        if self._activation_started_ns is not None:
            raise RuntimeError("Publication activation gate timing already started")
        self._activation_started_ns = time.monotonic_ns()

    def end_activation_gate(self, result: str) -> None:
        if result not in {"success", "rollback", "recovery_required"}:
            raise ValueError("Publication result is invalid")
        if self._activation_started_ns is None:
            raise RuntimeError("Publication activation gate timing was not started")
        if self._activation_duration_ms is not None:
            raise RuntimeError("Publication activation gate timing already ended")
        self._activation_duration_ms = max(
            0,
            (time.monotonic_ns() - self._activation_started_ns) // 1_000_000,
        )
        self.publication_result = result

    def to_payload(
        self,
        *,
        ttl_seconds: int | None = None,
    ) -> dict[str, object]:
        if self.status not in {"succeeded", "failed", "skipped"}:
            raise ValueError("Ingestion attempt is not complete")
        if ttl_seconds is not None and ttl_seconds < 1:
            raise ValueError("Ingestion metric TTL must be positive")
        payload: dict[str, object] = {
            "type": "ingestion_run",
            "run_id": self.run_id,
            "operation": self.operation,
            "processing_hash": self.processing_hash,
            "status": self.status,
            "error_code": self.error_code,
            "publication_result": self.publication_result,
            "started_at": self.started_at.isoformat(),
            "duration_ms": max(
                0,
                (time.monotonic_ns() - self._started_ns) // 1_000_000,
            ),
            "stage_durations_ms": dict(sorted(self.stage_durations_ms.items())),
            "counters": dict(sorted(self.counters.items())),
            "peak_rss_bytes": _peak_rss_bytes(),
            "activation_gate_duration_ms": self._activation_duration_ms,
        }
        if ttl_seconds is not None:
            payload["ttl"] = ttl_seconds
        return payload


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024
