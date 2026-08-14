"""Versioned Azure AI Search index publication."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Iterable

import httpx
from directive_contracts import DirectiveChunk, DirectiveMetadata

from .canonical import CanonicalDirective
from .chunking import TextChunk
from .config import IngestionConfig


class DirectiveSearchRepository:
    def __init__(
        self,
        config: IngestionConfig,
        credential: Any,
        openai_client: Any,
    ) -> None:
        self._config = config
        self._credential = credential
        self._openai = openai_client
        self._client = httpx.AsyncClient(
            base_url=config.search_endpoint,
            timeout=httpx.Timeout(180),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def check_access(self) -> None:
        result = await self._request(
            "GET",
            "/indexes",
            api_version=self._config.search_api_version,
        )
        if not isinstance(result.get("value"), list):
            raise RuntimeError("Search index listing returned an invalid response")

    async def verification_summary(self) -> dict[str, object]:
        index = await self._request(
            "GET",
            f"/indexes/{self._config.search_index}",
            api_version=self._config.search_api_version,
        )
        fields = {field["name"]: field for field in index.get("fields", [])}
        dimensions = int(fields.get("content_vector", {}).get("dimensions", 0))
        self._validate_existing_index(index)

        published = await self._count_and_facet(
            "publication_state eq 'published'"
        )
        current = await self._count_and_facet(
            _published_current_valid_filter()
        )
        direct_query = await self._request(
            "POST",
            f"/indexes/{self._config.search_index}/docs/search",
            api_version=self._config.search_api_version,
            payload={
                "search": "directive verification",
                "filter": _published_current_valid_filter(),
                "vectorFilterMode": "preFilter",
                "vectorQueries": [
                    {
                        "kind": "text",
                        "text": "directive verification",
                        "fields": "content_vector",
                        "k": 50,
                    }
                ],
                "queryType": "semantic",
                "semanticConfiguration": "semantic_config",
                "select": "id",
                "top": 1,
            },
        )
        if not isinstance(direct_query.get("value"), list):
            raise RuntimeError(
                "Search direct hybrid query returned an invalid response"
            )
        return {
            "published_chunks": published["count"],
            "published_directives": published["directive_count"],
            "published_versions": published["version_count"],
            "current_chunks": current["count"],
            "current_directives": current["directive_count"],
            "current_versions": current["version_count"],
            "vector_dimensions": dimensions,
            "search_index": index["name"],
            "vector_profile": "directive-vector-profile",
            "vectorizer": "directive-openai-vectorizer",
            "semantic_configuration": "semantic_config",
            "direct_hybrid_query": "ok",
        }

    async def _count_and_facet(
        self, filter_expression: str
    ) -> dict[str, int]:
        result = await self._request(
            "POST",
            f"/indexes/{self._config.search_index}/docs/search",
            api_version=self._config.search_api_version,
            payload={
                "search": "*",
                "filter": filter_expression,
                "count": True,
                "top": 1,
                "select": "id",
                "facets": [
                    "directive_id,count:10000",
                    "directive_version_id,count:10000",
                ],
            },
        )
        facets = result.get("@search.facets") or {}
        return {
            "count": int(result.get("@odata.count", -1)),
            "directive_count": len(facets.get("directive_id") or []),
            "version_count": len(
                facets.get("directive_version_id") or []
            ),
        }

    async def ensure_resources(self) -> None:
        index = await self._request(
            "GET",
            f"/indexes/{self._config.search_index}",
            api_version=self._config.search_api_version,
            allow_not_found=True,
        )
        if not index:
            await self._request(
                "PUT",
                f"/indexes/{self._config.search_index}",
                api_version=self._config.search_api_version,
                payload=self._index_definition(),
            )
        else:
            self._validate_existing_index(index)

    async def build_chunks(
        self,
        directive: CanonicalDirective,
        text_chunks: list[TextChunk],
    ) -> list[DirectiveChunk]:
        vectors: list[list[float]] = []
        for batch in _batches(text_chunks, 16):
            response = await self._openai.embeddings.create(
                model=self._config.embedding_deployment,
                input=[item.content for item in batch],
                dimensions=self._config.embedding_dimensions,
            )
            batch_vectors = [item.embedding for item in response.data]
            if len(batch_vectors) != len(batch):
                raise RuntimeError(
                    "Embedding response count does not match chunk count"
                )
            vectors.extend(batch_vectors)
        sections = {
            section.section_id: section for section in directive.sections
        }
        records: list[DirectiveChunk] = []
        for chunk, vector in zip(text_chunks, vectors, strict=True):
            section = sections[chunk.section_id]
            records.append(
                DirectiveChunk(
                    id=chunk.id,
                    directive_id=directive.metadata.directive_id,
                    directive_version_id=(
                        directive.metadata.directive_version_id
                    ),
                    version_label=directive.metadata.version_label,
                    title=directive.metadata.title,
                    aliases=directive.metadata.aliases,
                    is_current=False,
                    is_valid=directive.metadata.is_valid,
                    status=directive.metadata.status,
                    effective_from=directive.metadata.effective_from,
                    effective_to=directive.metadata.effective_to,
                    section_id=chunk.section_id,
                    section_number=section.number,
                    section_title=section.title,
                    section_path=list(section.path),
                    chunk_ordinal=chunk.ordinal,
                    content_kind=chunk.content_kind,
                    page_from=chunk.page_from,
                    page_to=chunk.page_to,
                    content=chunk.content,
                    content_vector=vector,
                    language=directive.metadata.language,
                    source_hash=directive.metadata.source_hash,
                    processing_hash=directive.metadata.processing_hash,
                )
            )
        return records

    async def stage_chunks(self, chunks: list[DirectiveChunk]) -> None:
        for batch in _batches(chunks, 250):
            actions = [
                {
                    **_search_document(chunk),
                    "@search.action": "mergeOrUpload",
                }
                for chunk in batch
            ]
            await self._upload_actions(actions)

    async def publish_chunks(self, chunks: list[DirectiveChunk]) -> None:
        for batch in _batches(chunks, 500):
            actions = [
                {
                    "id": chunk.id,
                    "publication_state": "published",
                    "@search.action": "merge",
                }
                for chunk in batch
            ]
            await self._upload_actions(actions)

    async def retire_chunks(self, chunks: list[DirectiveChunk]) -> None:
        await self._merge_chunk_state(
            [chunk.id for chunk in chunks],
            publication_state="retired",
            is_current=False,
        )

    async def retire_generation(self, metadata: DirectiveMetadata) -> None:
        keys = await self._find_keys(
            _generation_filter(metadata, publication_state="published")
        )
        await self._merge_chunk_state(
            keys, publication_state="retired", is_current=False
        )

    async def validate_published(
        self, directive: CanonicalDirective, expected_count: int
    ) -> None:
        escaped_version = directive.metadata.directive_version_id.replace(
            "'", "''"
        )
        escaped_hash = directive.metadata.source_hash.replace("'", "''")
        escaped_processing = directive.metadata.processing_hash.replace(
            "'", "''"
        )
        filter_expression = (
            f"directive_version_id eq '{escaped_version}' and "
            f"source_hash eq '{escaped_hash}' and "
            f"processing_hash eq '{escaped_processing}' and "
            "publication_state eq 'published'"
        )
        await self._wait_for_count(
            filter_expression,
            expected_count,
            detail=directive.metadata.directive_version_id,
        )

    async def validate_published_chunk_ids(
        self, directive: CanonicalDirective, chunk_ids: Iterable[str]
    ) -> None:
        """Check a staged generation by its immutable document identities."""
        expected = set(chunk_ids)
        if not expected:
            return
        published = set(
            await self._find_keys(
                _generation_filter(
                    directive.metadata, publication_state="published"
                )
            )
        )
        missing = expected - published
        if missing:
            raise RuntimeError(
                "Published Search generation is missing manifest chunk IDs"
            )

    async def validate_current_generation(
        self, bundle: Any
    ) -> None:
        """Require exactly the manifest's generation-scoped IDs to be live."""
        expected_ids = {
            chunk_id
            for section in bundle.manifest.sections
            for chunk_id in section.chunk_ids
        }
        filter_expression = (
            f"directive_id eq '{_odata_string(bundle.directive_id)}' and "
            "publication_state eq 'published' and is_current eq true and "
            "is_valid eq true"
        )
        actual_ids = set(await self._find_keys(filter_expression))
        if actual_ids != expected_ids:
            raise RuntimeError(
                "Current Search generation IDs do not match manifest chunks"
            )

    async def reconcile_generation(
        self, bundle: Any
    ) -> None:
        expected_ids = {
            chunk_id
            for section in bundle.manifest.sections
            for chunk_id in section.chunk_ids
        }
        published_filter = (
            f"directive_id eq '{_odata_string(bundle.directive_id)}' and "
            "publication_state eq 'published'"
        )
        stale_keys = [
            key
            for key in await self._find_keys(published_filter)
            if key not in expected_ids
        ]
        await self._merge_chunk_state(
            stale_keys,
            publication_state="retired",
            is_current=False,
        )

    async def reconcile_current(self, bundle: Any) -> None:
        if not bundle.is_current:
            return
        expected_ids = {
            chunk_id
            for section in bundle.manifest.sections
            for chunk_id in section.chunk_ids
        }
        if not expected_ids:
            raise RuntimeError(
                "Current directive has no published Search chunks: "
                f"{bundle.directive_version_id}"
            )
        await self._merge_chunk_state(list(expected_ids), is_current=True)
        current_filter = (
            f"directive_id eq '{_odata_string(bundle.directive_id)}' and "
            "publication_state eq 'published' and is_current eq true"
        )
        stale_keys = [
            key
            for key in await self._find_keys(current_filter)
            if key not in expected_ids
        ]
        await self._merge_chunk_state(
            stale_keys,
            is_current=False,
        )
        await self.validate_current_generation(bundle)

    async def _find_keys(
        self,
        filter_expression: str,
        *,
        limit: int = 100000,
        require_complete: bool = True,
    ) -> list[str]:
        keys: list[str] = []
        page_size = 1000
        skip = 0
        while len(keys) < limit:
            result = await self._request(
                "POST",
                f"/indexes/{self._config.search_index}/docs/search",
                api_version=self._config.search_api_version,
                payload={
                    "search": "*",
                    "filter": filter_expression,
                    "select": "id",
                    "top": min(page_size, limit - len(keys)),
                    "skip": skip,
                },
            )
            page = result.get("value", [])
            page_keys = [
                item["id"]
                for item in page
                if isinstance(item.get("id"), str)
            ]
            keys.extend(page_keys)
            requested = min(page_size, limit - (len(keys) - len(page_keys)))
            if len(page) < requested:
                break
            if len(keys) >= limit:
                if require_complete:
                    raise RuntimeError(
                        "Search reconciliation exceeded its bounded key limit"
                    )
                return keys
            skip += len(page)
        return keys

    async def _merge_chunk_state(
        self,
        keys: list[str],
        *,
        publication_state: str | None = None,
        is_current: bool | None = None,
    ) -> None:
        for batch in _batches(keys, 500):
            actions: list[dict[str, Any]] = []
            for key in batch:
                action: dict[str, Any] = {
                    "id": key,
                    "@search.action": "merge",
                }
                if publication_state is not None:
                    action["publication_state"] = publication_state
                if is_current is not None:
                    action["is_current"] = is_current
                actions.append(action)
            await self._upload_actions(actions)

    async def _wait_for_count(
        self,
        filter_expression: str,
        expected_count: int,
        *,
        detail: str,
    ) -> None:
        actual = -1
        for attempt in range(12):
            result = await self._request(
                "POST",
                f"/indexes/{self._config.search_index}/docs/search",
                api_version=self._config.search_api_version,
                payload={
                    "search": "*",
                    "filter": filter_expression,
                    "count": True,
                    "top": 1,
                    "select": "id",
                },
            )
            actual = int(result.get("@odata.count", -1))
            if actual == expected_count:
                return
            await asyncio.sleep(min(2**attempt, 10))
        raise RuntimeError(
            f"Search visibility validation failed for {detail}: expected "
            f"{expected_count} chunks, found {actual}"
        )

    async def _upload_actions(self, actions: list[dict[str, Any]]) -> None:
        result = await self._request(
            "POST",
            f"/indexes/{self._config.search_index}/docs/index",
            api_version=self._config.search_api_version,
            payload={"value": actions},
        )
        failures = [
            item for item in result.get("value", []) if not item.get("status")
        ]
        if failures:
            details = ", ".join(
                f"{item.get('key')}:{item.get('errorMessage')}"
                for item in failures
            )
            raise RuntimeError(f"Search document upload failed: {details}")

    async def _headers(self) -> dict[str, str]:
        token = await self._credential.get_token(
            "https://search.azure.com/.default"
        )
        return {
            "Authorization": f"Bearer {token.token}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        api_version: str,
        payload: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any]:
        for attempt in range(5):
            response = await self._client.request(
                method,
                path,
                params={"api-version": api_version},
                headers=await self._headers(),
                json=payload,
            )
            if allow_not_found and response.status_code == 404:
                return {}
            if response.status_code not in {408, 429, 500, 502, 503, 504}:
                if response.is_error:
                    raise RuntimeError(
                        f"{method} {path} failed with HTTP "
                        f"{response.status_code}: {response.text}"
                    )
                return response.json() if response.content else {}
            if attempt == 4:
                raise RuntimeError(
                    f"{method} {path} failed after retries with HTTP "
                    f"{response.status_code}: {response.text}"
                )
            retry_after = response.headers.get("retry-after")
            delay = float(retry_after) if retry_after else 2**attempt
            await asyncio.sleep(min(delay, 30))
        raise AssertionError("unreachable")

    def _validate_existing_index(self, index: dict[str, Any]) -> None:
        fields = {field["name"]: field for field in index.get("fields", [])}
        required = {field["name"] for field in self._index_fields()}
        missing = sorted(required - fields.keys())
        if missing:
            raise RuntimeError(
                "Existing directive index is incompatible; missing fields: "
                + ", ".join(missing)
            )
        vector = fields["content_vector"]
        if int(vector.get("dimensions", 0)) != (
            self._config.embedding_dimensions
        ):
            raise RuntimeError(
                "Existing directive index has incompatible vector dimensions"
            )
        if vector.get("vectorSearchProfile") != "directive-vector-profile":
            raise RuntimeError(
                "Existing directive index has an incompatible vector profile"
            )
        if not fields["directive_id"].get("searchable"):
            raise RuntimeError(
                "Existing directive index must make directive_id searchable "
                "for semantic keyword prioritization"
            )
        for name in ("title", "content"):
            if fields[name].get("analyzer") != "cs.microsoft":
                raise RuntimeError(
                    "Existing directive index requires the Czech cs.microsoft "
                    f"analyzer for {name}"
                )
        is_valid = fields.get("is_valid") or {}
        if (
            is_valid.get("type") != "Edm.Boolean"
            or is_valid.get("filterable") is not True
            or is_valid.get("retrievable") is not True
        ):
            raise RuntimeError(
                "Existing directive index requires filterable, retrievable is_valid"
            )
        vector_search = index.get("vectorSearch") or {}
        algorithms = {
            algorithm.get("name"): algorithm
            for algorithm in vector_search.get("algorithms") or []
        }
        if algorithms.get("directive-hnsw", {}).get("kind") != "hnsw":
            raise RuntimeError(
                "Existing directive index is missing the HNSW algorithm"
            )
        profiles = {
            profile.get("name"): profile
            for profile in vector_search.get("profiles") or []
        }
        profile = profiles.get("directive-vector-profile") or {}
        if (
            profile.get("algorithm") != "directive-hnsw"
            or profile.get("vectorizer") != "directive-openai-vectorizer"
        ):
            raise RuntimeError(
                "Existing directive index is missing the direct-query "
                "vector profile"
            )
        vectorizers = {
            vectorizer.get("name"): vectorizer
            for vectorizer in vector_search.get("vectorizers") or []
        }
        vectorizer = vectorizers.get("directive-openai-vectorizer") or {}
        parameters = vectorizer.get("azureOpenAIParameters") or {}
        if (
            vectorizer.get("kind") != "azureOpenAI"
            or parameters.get("deploymentId")
            != self._config.embedding_deployment
            or parameters.get("modelName") != self._config.embedding_model
        ):
            raise RuntimeError(
                "Existing directive index is missing the configured query "
                "vectorizer"
            )
        semantic = index.get("semantic") or {}
        configurations = {
            configuration.get("name"): configuration
            for configuration in semantic.get("configurations") or []
        }
        if "semantic_config" not in configurations:
            raise RuntimeError(
                "Existing directive index is missing semantic_config"
            )

    def _index_definition(self) -> dict[str, Any]:
        return {
            "name": self._config.search_index,
            "fields": self._index_fields(),
            "vectorSearch": {
                "algorithms": [
                    {
                        "name": "directive-hnsw",
                        "kind": "hnsw",
                        "hnswParameters": {
                            "m": 4,
                            "efConstruction": 400,
                            "efSearch": 500,
                            "metric": "cosine",
                        },
                    }
                ],
                "profiles": [
                    {
                        "name": "directive-vector-profile",
                        "algorithm": "directive-hnsw",
                        "vectorizer": "directive-openai-vectorizer",
                    }
                ],
                "vectorizers": [
                    {
                        "name": "directive-openai-vectorizer",
                        "kind": "azureOpenAI",
                        "azureOpenAIParameters": {
                            "resourceUri": self._config.openai_resource_uri,
                            "deploymentId": (
                                self._config.embedding_deployment
                            ),
                            "modelName": self._config.embedding_model,
                        },
                    }
                ],
            },
            "semantic": {
                "configurations": [
                    {
                        "name": "semantic_config",
                        "prioritizedFields": {
                            "titleField": {"fieldName": "title"},
                            "prioritizedContentFields": [
                                {"fieldName": "content"}
                            ],
                            "prioritizedKeywordsFields": [
                                {"fieldName": "aliases"},
                                {"fieldName": "directive_id"},
                                {"fieldName": "section_title"},
                            ],
                        },
                    }
                ]
            },
        }

    def _index_fields(self) -> list[dict[str, Any]]:
        string_filter_fields = (
            "directive_id",
            "directive_version_id",
            "version_label",
            "status",
            "section_id",
            "section_number",
            "content_kind",
            "language",
            "source_hash",
            "processing_hash",
            "publication_state",
        )
        return [
            {
                "name": "id",
                "type": "Edm.String",
                "key": True,
                "filterable": True,
            },
            *[
                {
                    "name": name,
                    "type": "Edm.String",
                    "searchable": name == "directive_id",
                    "filterable": True,
                    "retrievable": True,
                }
                for name in string_filter_fields
            ],
            {
                "name": "title",
                "type": "Edm.String",
                "searchable": True,
                "filterable": True,
                "retrievable": True,
                "analyzer": "cs.microsoft",
            },
            {
                "name": "aliases",
                "type": "Collection(Edm.String)",
                "searchable": True,
                "filterable": True,
                "retrievable": True,
            },
            {
                "name": "is_current",
                "type": "Edm.Boolean",
                "filterable": True,
                "retrievable": True,
            },
            {
                "name": "is_valid",
                "type": "Edm.Boolean",
                "filterable": True,
                "retrievable": True,
            },
            {
                "name": "effective_from",
                "type": "Edm.DateTimeOffset",
                "filterable": True,
                "sortable": True,
                "retrievable": True,
            },
            {
                "name": "effective_to",
                "type": "Edm.DateTimeOffset",
                "filterable": True,
                "sortable": True,
                "retrievable": True,
            },
            {
                "name": "section_title",
                "type": "Edm.String",
                "searchable": True,
                "filterable": True,
                "retrievable": True,
            },
            {
                "name": "section_path",
                "type": "Collection(Edm.String)",
                "searchable": True,
                "filterable": True,
                "retrievable": True,
            },
            *[
                {
                    "name": name,
                    "type": "Edm.Int32",
                    "filterable": True,
                    "sortable": True,
                    "retrievable": True,
                }
                for name in ("chunk_ordinal", "page_from", "page_to")
            ],
            {
                "name": "content",
                "type": "Edm.String",
                "searchable": True,
                "retrievable": True,
                "analyzer": "cs.microsoft",
            },
            {
                "name": "content_vector",
                "type": "Collection(Edm.Single)",
                "searchable": True,
                "retrievable": False,
                "dimensions": self._config.embedding_dimensions,
                "vectorSearchProfile": "directive-vector-profile",
            },
        ]

def _search_document(chunk: DirectiveChunk) -> dict[str, Any]:
    value = chunk.model_dump(mode="json")
    value["effective_from"] = _search_date(chunk.effective_from)
    value["effective_to"] = (
        _search_date(chunk.effective_to) if chunk.effective_to else None
    )
    return value


def _search_date(value: date) -> str:
    return f"{value.isoformat()}T00:00:00Z"


def _odata_string(value: str) -> str:
    return value.replace("'", "''")


def _published_current_valid_filter() -> str:
    return (
        "publication_state eq 'published' and is_current eq true "
        "and is_valid eq true"
    )


def _generation_filter(
    metadata: DirectiveMetadata, *, publication_state: str
) -> str:
    return (
        f"directive_version_id eq '{_odata_string(metadata.directive_version_id)}' "
        f"and source_hash eq '{_odata_string(metadata.source_hash)}' "
        f"and processing_hash eq '{_odata_string(metadata.processing_hash)}' "
        f"and publication_state eq '{publication_state}'"
    )


def _batches(values: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
