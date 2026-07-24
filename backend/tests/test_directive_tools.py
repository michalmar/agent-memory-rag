from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError
from fastapi import HTTPException

from agent_contracts import (
    AgentType,
    MandatoryStatus,
    RuntimeDescriptor,
    RuntimeState,
    ToolResultEnvelope,
    directive_tool_definition,
)
from directive_contracts import (
    DirectiveManifest,
    DirectiveRelation,
    DirectiveSection,
    DirectiveSummary,
)

from agent_memory_backend.agent_tools import ToolExecutionError
from agent_memory_backend.agent_tool_gateway import (
    AgentToolRequest,
    dispatch_agent_tool,
)
from agent_memory_backend.auth import AgentCaller
from agent_memory_backend.directive_artifacts import (
    _validated_catalog_blob_name,
)
from agent_memory_backend.directive_catalog import DirectiveCatalogRepository
from agent_memory_backend.directive_mandates import DirectiveMandateRepository
from agent_memory_backend.directive_search import (
    DirectiveSearchRepository,
    _build_filter,
)
from agent_memory_backend.directive_tools import DirectiveToolExecutor

_HASH = "a" * 64
_PROCESSING_HASH = "b" * 64


def _version(
    directive_id: str = "10000001",
    version_id: str = "10000001-v2",
    *,
    title: str = "Travel and Vehicle Policy",
) -> dict:
    return {
        "id": f"version:{version_id}",
        "type": "version",
        "schema_version": "1.0",
        "directive_id": directive_id,
        "directive_version_id": version_id,
        "version_label": "2",
        "title": title,
        "aliases": [],
        "status": "published",
        "is_current": True,
        "effective_from": "2026-01-01",
        "effective_to": None,
        "language": "en",
        "document_type": "directive",
        "source_filename": f"{directive_id}-v2.pdf",
        "source_hash": _HASH,
        "processing_hash": _PROCESSING_HASH,
        "publication_state": "published",
    }


def _manifest(section_count: int = 3) -> DirectiveManifest:
    sections = [
        DirectiveSection(
            section_id=f"s{index}",
            ordinal=index,
            number=str(index + 1),
            title=f"Section {index + 1}",
            path=[f"Section {index + 1}"],
            page_from=index + 1,
            page_to=index + 1,
            token_count=5,
            content_hash=_HASH,
            blob_name=f"directives/sections/{index}.md",
            chunk_ids=[f"chunk-{index}"],
        )
        for index in range(section_count)
    ]
    return DirectiveManifest(
        directive_id="10000001",
        directive_version_id="10000001-v2",
        source_hash=_HASH,
        total_pages=section_count,
        total_tokens=section_count * 5,
        canonical_blob_name="directives/document.md",
        source_blob_name="directives/source.pdf",
        summary_blob_name="directives/summary.json",
        manifest_blob_name="directives/manifest.json",
        sections=sections,
    )


class _Catalog:
    enabled = True

    def __init__(self) -> None:
        self.records = {
            ("10000001", "10000001-v2"): _version(),
            ("10000002", "10000002-v1"): _version(
                "10000002",
                "10000002-v1",
                title="Vacation Policy",
            ),
        }
        self.manifest = _manifest()

    async def resolve_version(
        self,
        directive_id: str,
        *,
        directive_version_id=None,
        version_label=None,
        as_of=None,
    ):
        del version_label, as_of
        if directive_version_id:
            return self.records.get((directive_id, directive_version_id))
        return next(
            (
                value
                for (candidate_id, _), value in self.records.items()
                if candidate_id == directive_id
            ),
            None,
        )

    async def get_version_record(self, directive_id: str, version_id: str):
        return self.records.get((directive_id, version_id))

    async def get_manifest(self, directive_id: str, version_id: str):
        if (directive_id, version_id) not in self.records:
            return None
        return self.manifest

    async def get_relations(
        self,
        directive_id: str,
        version_id: str,
        relation_types=None,
    ):
        del version_id, relation_types
        if directive_id == "10000001":
            return (
                DirectiveRelation(
                    relation_id="relation-1",
                    source_directive_id="10000001",
                    source_version_id="10000001-v2",
                    target_directive_id="10000002",
                    relation_type="sub_directive",
                    status="accepted",
                    evidence="Directive 10000002",
                ),
            )
        return (
            DirectiveRelation(
                relation_id="relation-2",
                source_directive_id="10000002",
                source_version_id="10000002-v1",
                target_directive_id="10000001",
                relation_type="parent",
                status="accepted",
                evidence="Directive 10000001",
            ),
        )

    public_version = staticmethod(DirectiveCatalogRepository.public_version)


class _Artifacts:
    enabled = True

    def __init__(self) -> None:
        self.read_names: list[str] = []

    async def read_text(self, name: str) -> str:
        self.read_names.append(name)
        return f"content:{name}"

    async def read_json(self, name: str) -> dict:
        self.read_names.append(name)
        return DirectiveSummary(
            directive_id="10000001",
            directive_version_id="10000001-v2",
            source_hash=_HASH,
            summary="Summary",
            covered_section_ids=["s0", "s1", "s2"],
            total_section_count=3,
            input_token_count=15,
            strategy="full_document",
            model_deployment="gpt-5.6-sol",
        ).model_dump(mode="json")


class _Search:
    enabled = True

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "intents": kwargs["intents"],
            "filter": {"current_only": kwargs["current_only"]},
            "retrieval_output": "Grounded extract",
            "references": [
                {
                    "ref_id": "ref-1",
                    "content": "Evidence",
                    "source_data": {
                        "directive_id": "10000001",
                        "directive_version_id": "10000001-v2",
                        "version_label": "2",
                        "title": "Travel and Vehicle Policy",
                        "section_id": "s0",
                        "section_number": "1",
                        "section_title": "Eligibility",
                        "page_from": 1,
                        "page_to": 1,
                        "effective_from": "2026-01-01",
                    },
                }
            ],
        }


class _Mandates:
    enabled = True

    def __init__(self) -> None:
        self.lookup_args = None

    async def lookup(self, user_id: str, directive_ids: list[str]):
        self.lookup_args = (user_id, directive_ids)
        return {
            "snapshot_id": "mandates-current",
            "snapshot_complete": True,
            "degraded": False,
            "statuses": {
                directive_id: "mandatory" for directive_id in directive_ids
            },
        }


def _settings(**overrides):
    values = {
        "directive_tool_timeout_seconds": 5,
        "directive_max_content_tokens": 20,
        "directive_max_sections_per_call": 2,
        "directive_max_search_results": 10,
        "directive_max_related_depth": 2,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class DirectiveToolContractTests(unittest.TestCase):
    def test_search_rejects_raw_filter_and_mandates_reject_user_identity(self):
        with self.assertRaises(ValidationError):
            directive_tool_definition("search_directives").validate(
                {
                    "intents": ["vehicle eligibility"],
                    "filter": "is_current eq false",
                }
            )
        with self.assertRaises(ValidationError):
            directive_tool_definition(
                "get_user_directive_mandates"
            ).validate(
                {
                    "directive_ids": ["10000001"],
                    "user_id": "attacker",
                }
            )

    def test_filter_builder_owns_current_and_exact_version_constraints(self):
        value = _build_filter(
            current_only=True,
            directive_ids=["10000001"],
            directive_version_id="10000001-v2",
            section_ids=["section'one"],
        )
        self.assertIn("publication_state eq 'published'", value)
        self.assertIn("is_current eq true", value)
        self.assertIn("directive_id eq '10000001'", value)
        self.assertIn("directive_version_id eq '10000001-v2'", value)
        self.assertIn("section_id eq 'section''one'", value)

    def test_historical_search_requires_and_normalizes_exact_version(self):
        definition = directive_tool_definition("search_directives")
        with self.assertRaises(ValidationError):
            definition.validate(
                {
                    "intents": ["previous vehicle policy"],
                    "current_only": False,
                }
            )
        validated = definition.validate(
            {
                "intents": ["previous vehicle policy"],
                "directive_ids": ["10000001"],
                "directive_version_id": "10000001-v1",
            }
        )
        self.assertFalse(validated["current_only"])

    def test_artifact_repository_rejects_urls_and_parent_traversal(self):
        self.assertEqual(
            _validated_catalog_blob_name("directives/section.md"),
            "directives/section.md",
        )
        for value in (
            "https://storage.example/document.md",
            "../document.md",
            "/document.md",
            "folder\\document.md",
        ):
            with self.assertRaises(RuntimeError):
                _validated_catalog_blob_name(value)


class DirectiveSearchRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retrieve_uses_stable_knowledge_source_parameters(self) -> None:
        repository = DirectiveSearchRepository()
        repository._knowledge_source = "directive-source"
        repository._max_results = 25
        repository._request = AsyncMock(
            return_value={
                "response": [{"content": [{"type": "text", "text": "unbounded"}]}],
                "references": [
                    {"id": f"ref-{index}", "content": f"evidence-{index}"}
                    for index in range(5)
                ],
            }
        )

        result = await repository.retrieve(
            intents=["company car eligibility"],
            current_only=True,
            max_results=3,
        )

        payload = repository._request.await_args.args[0]
        source = payload["knowledgeSourceParams"][0]
        self.assertEqual(
            source,
            {
                "knowledgeSourceName": "directive-source",
                "kind": "searchIndex",
                "includeReferences": True,
                "includeReferenceSourceData": True,
                "filterAddOn": (
                    "publication_state eq 'published' and is_current eq true"
                ),
            },
        )
        self.assertNotIn("failOnError", source)
        self.assertNotIn("maxOutputDocuments", source)
        self.assertEqual(len(result["references"]), 3)
        self.assertEqual(
            result["retrieval_output"],
            [
                {"ref_id": "ref-0", "content": "evidence-0"},
                {"ref_id": "ref-1", "content": "evidence-1"},
                {"ref_id": "ref-2", "content": "evidence-2"},
            ],
        )

    async def test_request_uses_search_scope_access_token(self) -> None:
        response = SimpleNamespace(
            status_code=200,
            headers={},
            json=lambda: {"response": "evidence"},
        )
        client = SimpleNamespace(post=AsyncMock(return_value=response))
        credential = SimpleNamespace(
            get_token=AsyncMock(
                return_value=SimpleNamespace(token="search-access-token")
            )
        )
        repository = DirectiveSearchRepository()
        repository._client = client
        repository._endpoint = "https://search.example"
        repository._knowledge_base = "directives"
        repository._api_version = "2026-04-01"

        with patch(
            "agent_memory_backend.azure_clients.get_credential",
            return_value=credential,
        ):
            await repository._request({"intents": []})

        credential.get_token.assert_awaited_once_with(
            "https://search.azure.com/.default"
        )
        self.assertEqual(
            client.post.await_args.kwargs["headers"]["Authorization"],
            "Bearer search-access-token",
        )


class DirectiveToolExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.catalog = _Catalog()
        self.artifacts = _Artifacts()
        self.search = _Search()
        self.mandates = _Mandates()
        self.executor = DirectiveToolExecutor(
            self.catalog,
            self.artifacts,
            self.search,
            self.mandates,
        )

    async def test_search_defaults_to_current_and_emits_unknown_citation(self):
        with patch(
            "agent_memory_backend.directive_tools.get_settings",
            return_value=_settings(),
        ):
            result = await self.executor.execute_envelope(
                "search_directives",
                {"intents": ["company vehicle"]},
                user_id="tenant:user",
            )
        self.assertTrue(self.search.calls[0]["current_only"])
        self.assertEqual(result.citations[0].directive_id, "10000001")
        self.assertEqual(
            result.citations[0].mandatory_status,
            MandatoryStatus.UNKNOWN,
        )

    async def test_exact_historical_search_never_uses_current_filter(self):
        with patch(
            "agent_memory_backend.directive_tools.get_settings",
            return_value=_settings(),
        ):
            await self.executor.execute_envelope(
                "search_within_directive",
                {
                    "directive_id": "10000001",
                    "directive_version_id": "10000001-v2",
                    "intents": ["eligibility exceptions"],
                },
                user_id="tenant:user",
            )
        call = self.search.calls[0]
        self.assertFalse(call["current_only"])
        self.assertEqual(call["directive_ids"], ["10000001"])
        self.assertEqual(call["directive_version_id"], "10000001-v2")

    async def test_content_returns_explicit_continuation_without_truncation(self):
        with patch(
            "agent_memory_backend.directive_tools.get_settings",
            return_value=_settings(),
        ):
            result = await self.executor.execute_envelope(
                "get_directive_content",
                {
                    "directive_id": "10000001",
                    "directive_version_id": "10000001-v2",
                },
                user_id="tenant:user",
            )
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.data["continuation"]["next_cursor"], 2)
        self.assertEqual(len(result.data["sections"]), 2)
        self.assertEqual(
            self.artifacts.read_names,
            [
                "directives/sections/0.md",
                "directives/sections/1.md",
            ],
        )

    async def test_content_over_configured_budget_is_typed_error(self):
        with patch(
            "agent_memory_backend.directive_tools.get_settings",
            return_value=_settings(),
        ):
            result = await self.executor.execute_envelope(
                "get_directive_content",
                {
                    "directive_id": "10000001",
                    "directive_version_id": "10000001-v2",
                    "max_tokens": 21,
                },
                user_id="tenant:user",
            )
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_code, "CONTENT_TOO_LARGE")
        self.assertEqual(result.data["max_tokens"], 20)

    async def test_mandate_lookup_uses_only_injected_user(self):
        with patch(
            "agent_memory_backend.directive_tools.get_settings",
            return_value=_settings(),
        ):
            result = await self.executor.execute_envelope(
                "get_user_directive_mandates",
                {"directive_ids": ["10000001"]},
                user_id="tenant:user",
            )
        self.assertEqual(
            self.mandates.lookup_args,
            ("tenant:user", ["10000001"]),
        )
        self.assertEqual(
            result.data["statuses"]["10000001"],
            "mandatory",
        )

    async def test_related_traversal_is_bounded_and_cycle_safe(self):
        with patch(
            "agent_memory_backend.directive_tools.get_settings",
            return_value=_settings(),
        ):
            result = await self.executor.execute_envelope(
                "get_related_directives",
                {
                    "directive_id": "10000001",
                    "directive_version_id": "10000001-v2",
                    "depth": 2,
                },
                user_id="tenant:user",
            )
        self.assertEqual(len(result.data["related"]), 2)
        self.assertEqual(
            {item["depth"] for item in result.data["related"]},
            {1, 2},
        )

    async def test_unknown_tool_and_missing_user_fail_explicitly(self):
        with patch(
            "agent_memory_backend.directive_tools.get_settings",
            return_value=_settings(),
        ):
            with self.assertRaises(ToolExecutionError):
                await self.executor.execute_envelope(
                    "not_a_tool",
                    {},
                    user_id="tenant:user",
                )
            with self.assertRaises(ToolExecutionError):
                await self.executor.execute_envelope(
                    "get_directive_manifest",
                    {
                        "directive_id": "10000001",
                        "directive_version_id": "10000001-v2",
                    },
                    user_id="",
                )

    async def test_uninitialized_cosmos_repositories_use_tool_error_contract(self):
        cases = (
            (
                DirectiveToolExecutor(
                    DirectiveCatalogRepository(),
                    self.artifacts,
                    self.search,
                    self.mandates,
                ),
                "get_directive_manifest",
                {
                    "directive_id": "10000001",
                    "directive_version_id": "10000001-v2",
                },
            ),
            (
                DirectiveToolExecutor(
                    self.catalog,
                    self.artifacts,
                    self.search,
                    DirectiveMandateRepository(),
                ),
                "get_user_directive_mandates",
                {"directive_ids": ["10000001"]},
            ),
        )

        with patch(
            "agent_memory_backend.directive_tools.get_settings",
            return_value=_settings(),
        ):
            for executor, tool_name, arguments in cases:
                with self.subTest(tool_name=tool_name):
                    with self.assertRaises(ToolExecutionError) as error:
                        await executor.execute_envelope(
                            tool_name,
                            arguments,
                            user_id="tenant:user",
                        )
                    self.assertEqual(
                        error.exception.code,
                        "DIRECTIVE_DATA_UNAVAILABLE",
                    )


class DirectiveGatewayIsolationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _document(agent_type: AgentType) -> dict:
        state = RuntimeState(
            descriptor=RuntimeDescriptor(
                agent_type=agent_type,
                physical_agent_name=f"{agent_type.value}-hosted",
                release_id="test",
                prompt_version="test",
            ),
            hosted_session_id="session-1",
        )
        return {
            "id": "conversation-1",
            "user_id": "tenant:user",
            "metadata": {
                "schema_version": 3,
                "agent_type": agent_type.value,
                "physical_agent_name": state.descriptor.physical_agent_name,
                "release_id": "test",
                "prompt_version": "test",
                "runtime_state": {
                    "hosted_session_id": "session-1",
                },
            },
        }

    async def test_directive_session_routes_only_to_directive_executor(self):
        class History:
            async def get_by_hosted_session(self, user_id, session_id):
                self.lookup = (user_id, session_id)
                return DirectiveGatewayIsolationTests._document(
                    AgentType.DIRECTIVE_RAG
                )

        class Executor:
            async def execute_envelope(
                self,
                name,
                arguments,
                *,
                user_id,
            ):
                self.call = (name, arguments, user_id)
                return ToolResultEnvelope(status="ok", data={})

        history = History()
        executor = Executor()
        request = AgentToolRequest(
            user_id="tenant:user",
            session_id="session-1",
            call_id="call-1",
            arguments={"intents": ["vacation"]},
        )
        with patch(
            "agent_memory_backend.agent_tool_gateway.get_settings",
            return_value=SimpleNamespace(
                support_hosted_agent_principal_ids=("support-principal",),
                directive_hosted_agent_principal_ids=(
                    "directive-principal",
                ),
            ),
        ):
            result = await dispatch_agent_tool(
                "search_directives",
                request,
                AgentCaller(
                    principal_id="directive-principal",
                    tenant_id="tenant",
                ),
                history,
                {AgentType.DIRECTIVE_RAG: executor},
            )
        self.assertEqual(result.status, "ok")
        self.assertEqual(
            executor.call,
            (
                "search_directives",
                {"intents": ["vacation"]},
                "tenant:user",
            ),
        )

    async def test_cross_agent_tool_and_principal_are_denied(self):
        class History:
            async def get_by_hosted_session(self, user_id, session_id):
                return DirectiveGatewayIsolationTests._document(
                    AgentType.DIRECTIVE_RAG
                )

        request = AgentToolRequest(
            user_id="tenant:user",
            session_id="session-1",
            call_id="call-1",
            arguments={},
        )
        settings = SimpleNamespace(
            support_hosted_agent_principal_ids=("support-principal",),
            directive_hosted_agent_principal_ids=("directive-principal",),
        )
        with patch(
            "agent_memory_backend.agent_tool_gateway.get_settings",
            return_value=settings,
        ):
            with self.assertRaises(HTTPException) as principal_denied:
                await dispatch_agent_tool(
                    "get_directive_manifest",
                    request,
                    AgentCaller(
                        principal_id="support-principal",
                        tenant_id="tenant",
                    ),
                    History(),
                    {},
                )
            self.assertEqual(principal_denied.exception.status_code, 403)

            with self.assertRaises(HTTPException) as tool_denied:
                await dispatch_agent_tool(
                    "get_order_status",
                    request,
                    AgentCaller(
                        principal_id="directive-principal",
                        tenant_id="tenant",
                    ),
                    History(),
                    {},
                )
            self.assertEqual(tool_denied.exception.status_code, 403)
