"""Strict agent-facing directive tools over read-only backend repositories."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from agent_contracts import (
    Citation,
    MandatoryStatus,
    ToolResultEnvelope,
    directive_tool_definition,
)
from directive_contracts import DirectiveManifest, DirectiveSummary

from .agent_tools import ToolExecutionError
from .config import Settings, get_settings
from .directive_artifacts import DirectiveArtifactRepository
from .directive_catalog import DirectiveCatalogRepository
from .directive_errors import (
    DirectiveContentTooLarge,
    DirectiveDataUnavailable,
)
from .directive_mandates import DirectiveMandateRepository
from .directive_search import DirectiveSearchRepository
from .telemetry import span


@dataclass(frozen=True)
class _Outcome:
    data: dict[str, Any]
    citations: tuple[Citation, ...] = ()
    status: str = "ok"
    error_code: str | None = None


class DirectiveToolExecutor:
    def __init__(
        self,
        catalog: DirectiveCatalogRepository,
        artifacts: DirectiveArtifactRepository,
        search: DirectiveSearchRepository,
        mandates: DirectiveMandateRepository,
    ) -> None:
        self._catalog = catalog
        self._artifacts = artifacts
        self._search = search
        self._mandates = mandates

    @property
    def enabled(self) -> bool:
        return all(
            (
                self._catalog.enabled,
                self._artifacts.enabled,
                self._search.enabled,
                self._mandates.enabled,
            )
        )

    async def execute_envelope(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        user_id: str,
    ) -> ToolResultEnvelope:
        if not user_id:
            raise ToolExecutionError(
                "MISSING_USER_CONTEXT",
                "Authenticated user context is missing",
            )
        try:
            definition = directive_tool_definition(name)
            validated = definition.validate(arguments)
        except ValidationError as exc:
            raise ToolExecutionError(
                "INVALID_TOOL_ARGUMENTS",
                "Directive tool arguments are invalid",
            ) from exc
        except ValueError as exc:
            raise ToolExecutionError("UNKNOWN_TOOL", str(exc)) from exc

        settings = get_settings()
        try:
            async with asyncio.timeout(
                settings.directive_tool_timeout_seconds
            ):
                with span("directive.tool", {"agent.tool.name": name}):
                    outcome = await self._execute(
                        name,
                        validated,
                        user_id=user_id,
                        settings=settings,
                    )
        except TimeoutError as exc:
            raise ToolExecutionError(
                "TOOL_TIMEOUT",
                "Directive tool execution timed out",
            ) from exc
        except DirectiveContentTooLarge as exc:
            return ToolResultEnvelope(
                status="error",
                error_code="CONTENT_TOO_LARGE",
                data=exc.detail,
            )
        except DirectiveDataUnavailable as exc:
            raise ToolExecutionError(
                "DIRECTIVE_DATA_UNAVAILABLE",
                str(exc),
            ) from exc
        return ToolResultEnvelope(
            status=outcome.status,
            data=outcome.data,
            citations=outcome.citations,
            error_code=outcome.error_code,
        )

    async def _execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        user_id: str,
        settings: Settings,
    ) -> _Outcome:
        if name == "resolve_directive":
            return await self._resolve(arguments, settings)
        if name == "search_directives":
            return await self._search_directives(arguments, settings)
        if name == "get_directive_manifest":
            return await self._manifest(arguments)
        if name == "get_directive_content":
            return await self._content(arguments, settings)
        if name == "search_within_directive":
            return await self._search_within(arguments, settings)
        if name == "get_related_directives":
            return await self._related(arguments, settings)
        if name == "get_precomputed_summary":
            return await self._summary(arguments)
        if name == "get_user_directive_mandates":
            return await self._mandate_status(
                arguments,
                user_id=user_id,
                settings=settings,
            )
        raise ToolExecutionError("UNKNOWN_TOOL", f"unknown directive tool: {name}")

    async def _resolve(
        self,
        arguments: dict[str, Any],
        settings: Settings,
    ) -> _Outcome:
        directive_id = arguments.get("directive_id")
        if directive_id:
            record = await self._catalog.resolve_version(
                directive_id,
                directive_version_id=arguments.get("directive_version_id"),
                version_label=arguments.get("version_label"),
                as_of=arguments.get("as_of"),
            )
            if record is None:
                return _Outcome(
                    data={
                        "resolution_status": "not_found",
                        "candidates": [],
                    }
                )
            version = self._catalog.public_version(record)
            return _Outcome(
                data={
                    "resolution_status": "resolved",
                    "directive": version,
                    "candidates": [version],
                },
                citations=(_version_citation(version),),
            )

        query = arguments["query"]
        search = await self._search.retrieve(
            intents=[query],
            current_only=not (
                arguments.get("version_label") or arguments.get("as_of")
            ),
            max_results=min(10, settings.directive_max_search_results),
        )
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for reference in search["references"]:
            source = reference.get("source_data") or {}
            candidate_id = source.get("directive_id")
            if (
                not isinstance(candidate_id, str)
                or candidate_id in seen
            ):
                continue
            seen.add(candidate_id)
            record = await self._catalog.resolve_version(
                candidate_id,
                version_label=arguments.get("version_label"),
                as_of=arguments.get("as_of"),
            )
            if record is not None:
                candidates.append(self._catalog.public_version(record))
        status = (
            "resolved"
            if len(candidates) == 1
            else "ambiguous"
            if candidates
            else "not_found"
        )
        data: dict[str, Any] = {
            "resolution_status": status,
            "candidates": candidates,
        }
        if len(candidates) == 1:
            data["directive"] = candidates[0]
        return _Outcome(
            data=data,
            citations=tuple(_version_citation(item) for item in candidates),
        )

    async def _search_directives(
        self,
        arguments: dict[str, Any],
        settings: Settings,
    ) -> _Outcome:
        max_results = _bounded_results(arguments, settings)
        result = await self._search.retrieve(
            intents=arguments["intents"],
            current_only=arguments.get("current_only", True),
            max_results=max_results,
            directive_ids=arguments.get("directive_ids"),
            directive_version_id=arguments.get("directive_version_id"),
        )
        return _Outcome(
            data=result,
            citations=_search_citations(
                result["references"],
                retrieval_strategy="discovery",
            ),
        )

    async def _manifest(self, arguments: dict[str, Any]) -> _Outcome:
        record, manifest = await self._record_and_manifest(arguments)
        version = self._catalog.public_version(record)
        return _Outcome(
            data={
                "directive": version,
                "manifest": manifest.model_dump(mode="json"),
                "coverage": {
                    "total_sections": len(manifest.sections),
                    "total_pages": manifest.total_pages,
                    "total_tokens": manifest.total_tokens,
                },
            },
            citations=(
                _version_citation(
                    version,
                    page_from=1,
                    page_to=manifest.total_pages,
                    coverage={"manifest_complete": True},
                ),
            ),
        )

    async def _content(
        self,
        arguments: dict[str, Any],
        settings: Settings,
    ) -> _Outcome:
        record, manifest = await self._record_and_manifest(arguments)
        version = self._catalog.public_version(record)
        requested_tokens = arguments.get(
            "max_tokens",
            settings.directive_max_content_tokens,
        )
        if requested_tokens > settings.directive_max_content_tokens:
            raise DirectiveContentTooLarge(
                {
                    "requested_tokens": requested_tokens,
                    "max_tokens": settings.directive_max_content_tokens,
                }
            )

        sections = manifest.sections
        requested_ids = arguments.get("section_ids") or []
        if requested_ids:
            requested = set(requested_ids)
            unknown = sorted(
                requested
                - {section.section_id for section in manifest.sections}
            )
            if unknown:
                raise ToolExecutionError(
                    "UNKNOWN_SECTION",
                    "Unknown directive section identifiers: "
                    + ", ".join(unknown),
                )
            sections = [
                section
                for section in manifest.sections
                if section.section_id in requested
            ]

        cursor = arguments.get("cursor", 0)
        if cursor > len(sections):
            raise ToolExecutionError(
                "INVALID_CURSOR",
                "Directive content cursor is outside the selected section set",
            )
        selected: list[tuple[Any, str]] = []
        token_count = 0
        index = cursor
        while (
            index < len(sections)
            and len(selected) < settings.directive_max_sections_per_call
        ):
            section = sections[index]
            if token_count + section.token_count > requested_tokens:
                if not selected:
                    raise DirectiveContentTooLarge(
                        {
                            "section_id": section.section_id,
                            "section_tokens": section.token_count,
                            "max_tokens": requested_tokens,
                            "cursor": cursor,
                        }
                    )
                break
            selected.append(
                (
                    section,
                    await self._artifacts.read_text(section.blob_name),
                )
            )
            token_count += section.token_count
            index += 1

        continuation = None
        if index < len(sections):
            continuation = {
                "next_cursor": index,
                "remaining_sections": len(sections) - index,
            }
        payload_sections = [
            {
                **section.model_dump(mode="json"),
                "content": content,
            }
            for section, content in selected
        ]
        citations = tuple(
            _section_citation(
                version,
                section.model_dump(mode="json"),
                retrieval_strategy="section_batch",
            )
            for section, _ in selected
        )
        return _Outcome(
            status="partial" if continuation else "ok",
            data={
                "directive": version,
                "sections": payload_sections,
                "returned_tokens": token_count,
                "coverage": {
                    "selected_section_count": len(sections),
                    "returned_section_count": len(selected),
                    "cursor": cursor,
                    "complete": continuation is None,
                },
                "continuation": continuation,
            },
            citations=citations,
        )

    async def _search_within(
        self,
        arguments: dict[str, Any],
        settings: Settings,
    ) -> _Outcome:
        record = await self._catalog.get_version_record(
            arguments["directive_id"],
            arguments["directive_version_id"],
        )
        if record is None:
            return _not_found()
        result = await self._search.retrieve(
            intents=arguments["intents"],
            current_only=False,
            max_results=_bounded_results(arguments, settings),
            directive_ids=[arguments["directive_id"]],
            directive_version_id=arguments["directive_version_id"],
            section_ids=arguments.get("section_ids"),
        )
        return _Outcome(
            data=result,
            citations=_search_citations(
                result["references"],
                retrieval_strategy="focused",
            ),
        )

    async def _related(
        self,
        arguments: dict[str, Any],
        settings: Settings,
    ) -> _Outcome:
        depth = arguments.get("depth", 1)
        if depth > settings.directive_max_related_depth:
            raise ToolExecutionError(
                "RELATED_DEPTH_EXCEEDED",
                "Related-directive depth exceeds the configured limit",
            )
        source = await self._catalog.get_version_record(
            arguments["directive_id"],
            arguments["directive_version_id"],
        )
        if source is None:
            return _not_found()

        relation_types = set(arguments.get("relation_types") or [])
        visited = {arguments["directive_id"]}
        frontier = [(source, 0)]
        related: list[dict[str, Any]] = []
        relation_ids: set[str] = set()
        while frontier:
            record, level = frontier.pop(0)
            if level >= depth:
                continue
            relations = await self._catalog.get_relations(
                record["directive_id"],
                record["directive_version_id"],
                relation_types or None,
            )
            for relation in relations:
                if relation.relation_id in relation_ids:
                    continue
                relation_ids.add(relation.relation_id)
                target_id = relation.target_directive_id
                target = await self._catalog.resolve_version(
                    target_id,
                    version_label=relation.target_version_label,
                )
                item: dict[str, Any] = {
                    "depth": level + 1,
                    "relation": relation.model_dump(mode="json"),
                    "target": (
                        self._catalog.public_version(target)
                        if target is not None
                        else None
                    ),
                }
                related.append(item)
                if target is not None and target_id not in visited:
                    visited.add(target_id)
                    frontier.append((target, level + 1))
        citations = tuple(
            _version_citation(
                item["target"],
                retrieval_strategy="linked",
            )
            for item in related
            if item["target"] is not None
        )
        return _Outcome(
            data={
                "source": self._catalog.public_version(source),
                "related": related,
                "max_depth": depth,
            },
            citations=citations,
        )

    async def _summary(self, arguments: dict[str, Any]) -> _Outcome:
        record, manifest = await self._record_and_manifest(arguments)
        value = await self._artifacts.read_json(manifest.summary_blob_name)
        try:
            summary = DirectiveSummary.model_validate(value)
        except ValueError as exc:
            raise DirectiveDataUnavailable(
                "Directive summary artifact is invalid"
            ) from exc
        version = self._catalog.public_version(record)
        return _Outcome(
            data={
                "directive": version,
                "summary": summary.model_dump(mode="json"),
                "coverage": {
                    "covered_section_count": len(
                        summary.covered_section_ids
                    ),
                    "total_section_count": summary.total_section_count,
                    "complete": (
                        len(summary.covered_section_ids)
                        == summary.total_section_count
                    ),
                },
            },
            citations=(
                _version_citation(
                    version,
                    page_from=1,
                    page_to=manifest.total_pages,
                    retrieval_strategy="precomputed_summary",
                    coverage={
                        "covered_section_count": len(
                            summary.covered_section_ids
                        ),
                        "total_section_count": summary.total_section_count,
                    },
                ),
            ),
        )

    async def _mandate_status(
        self,
        arguments: dict[str, Any],
        *,
        user_id: str,
        settings: Settings,
    ) -> _Outcome:
        directive_ids = list(dict.fromkeys(arguments["directive_ids"]))
        if len(directive_ids) > settings.directive_max_search_results:
            raise ToolExecutionError(
                "TOO_MANY_DIRECTIVES",
                "Mandate lookup exceeds the selected-directive limit",
            )
        result = await self._mandates.lookup(user_id, directive_ids)
        return _Outcome(
            status="partial" if result["degraded"] else "ok",
            data=result,
            error_code=(
                "MANDATE_STATUS_UNKNOWN" if result["degraded"] else None
            ),
        )

    async def _record_and_manifest(
        self,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], DirectiveManifest]:
        record = await self._catalog.get_version_record(
            arguments["directive_id"],
            arguments["directive_version_id"],
        )
        if record is None:
            raise ToolExecutionError(
                "DIRECTIVE_NOT_FOUND",
                "The requested directive version was not found",
            )
        manifest = await self._catalog.get_manifest(
            arguments["directive_id"],
            arguments["directive_version_id"],
        )
        if manifest is None:
            raise DirectiveDataUnavailable(
                "Published directive manifest is missing"
            )
        return record, manifest


def _bounded_results(
    arguments: dict[str, Any],
    settings: Settings,
) -> int:
    requested = arguments.get("max_results", 10)
    if requested > settings.directive_max_search_results:
        raise ToolExecutionError(
            "TOO_MANY_RESULTS",
            "Requested Search result count exceeds the configured limit",
        )
    return requested


def _not_found() -> _Outcome:
    return _Outcome(
        status="not_found",
        data={"message": "The requested directive version was not found"},
        error_code="DIRECTIVE_NOT_FOUND",
    )


def _version_citation(
    version: dict[str, Any],
    *,
    page_from: int | None = None,
    page_to: int | None = None,
    retrieval_strategy: str = "resolved",
    coverage: dict[str, Any] | None = None,
) -> Citation:
    version_id = str(version["directive_version_id"])
    return Citation(
        ref_id=version_id,
        source_name=str(version.get("title") or version["directive_id"]),
        directive_id=str(version["directive_id"]),
        directive_version_id=version_id,
        version_label=str(version.get("version_label") or ""),
        page_from=page_from,
        page_to=page_to,
        effective_from=str(version.get("effective_from") or ""),
        mandatory_status=MandatoryStatus.UNKNOWN,
        retrieval_strategy=retrieval_strategy,
        coverage=coverage,
    )


def _section_citation(
    version: dict[str, Any],
    section: dict[str, Any],
    *,
    retrieval_strategy: str,
) -> Citation:
    return Citation(
        ref_id=(
            f"{version['directive_version_id']}:{section['section_id']}"
        ),
        source_name=str(version.get("title") or version["directive_id"]),
        directive_id=str(version["directive_id"]),
        directive_version_id=str(version["directive_version_id"]),
        version_label=str(version.get("version_label") or ""),
        section_id=str(section["section_id"]),
        section_number=(
            str(section["number"]) if section.get("number") else None
        ),
        section_title=str(section.get("title") or ""),
        page_from=_optional_int(section.get("page_from")),
        page_to=_optional_int(section.get("page_to")),
        effective_from=str(version.get("effective_from") or ""),
        mandatory_status=MandatoryStatus.UNKNOWN,
        retrieval_strategy=retrieval_strategy,
    )


def _search_citations(
    references: list[dict[str, Any]],
    *,
    retrieval_strategy: str,
) -> tuple[Citation, ...]:
    citations: list[Citation] = []
    for reference in references:
        source = reference.get("source_data") or {}
        directive_id = source.get("directive_id")
        version_id = source.get("directive_version_id")
        if not directive_id or not version_id:
            continue
        citations.append(
            Citation(
                ref_id=str(reference["ref_id"]),
                source_name=str(source.get("title") or directive_id),
                directive_id=str(directive_id),
                directive_version_id=str(version_id),
                version_label=(
                    str(source["version_label"])
                    if source.get("version_label") is not None
                    else None
                ),
                section_id=(
                    str(source["section_id"])
                    if source.get("section_id") is not None
                    else None
                ),
                section_number=(
                    str(source["section_number"])
                    if source.get("section_number") is not None
                    else None
                ),
                section_title=(
                    str(source["section_title"])
                    if source.get("section_title") is not None
                    else None
                ),
                page_from=_optional_int(source.get("page_from")),
                page_to=_optional_int(source.get("page_to")),
                effective_from=(
                    str(source["effective_from"])
                    if source.get("effective_from") is not None
                    else None
                ),
                mandatory_status=MandatoryStatus.UNKNOWN,
                retrieval_strategy=retrieval_strategy,
            )
        )
    return tuple(citations)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
