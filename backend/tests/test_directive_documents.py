from __future__ import annotations

import unittest
from collections.abc import AsyncIterator
from dataclasses import dataclass
from unittest.mock import patch

from directive_contracts import (
    DirectiveArtifactLocators,
    DirectiveManifest,
    DirectiveSummary,
    PublishedDirectiveVersion,
    directive_storage_key,
    directive_version_storage_key,
    published_directive_version_item_id,
)
from fastapi import HTTPException

from agent_memory_backend import server
from agent_memory_backend.auth import User, get_current_user
from agent_memory_backend.directive_artifacts import DirectiveArtifactRepository
from agent_memory_backend.directive_documents import (
    DirectiveDocumentResponse,
    DirectiveDocumentService,
    DirectiveSourceStream,
)
from agent_memory_backend.directive_errors import DirectiveDataUnavailable

_DIRECTIVE_ID = "ČD/42-A"
_VERSION_ID = "ČD/42-A:v1"
_HASH = "a" * 64


def _manifest(**overrides) -> DirectiveManifest:
    values = {
        "directive_id": _DIRECTIVE_ID,
        "directive_version_id": _VERSION_ID,
        "source_hash": _HASH,
        "artifact_generation_id": _HASH,
        "total_pages": 4,
        "total_tokens": 100,
        "sections": [],
    }
    values.update(overrides)
    return DirectiveManifest(**values)


def _bundle() -> PublishedDirectiveVersion:
    return PublishedDirectiveVersion(
        id=published_directive_version_item_id(_DIRECTIVE_ID, "1"),
        directive_id=_DIRECTIVE_ID,
        directive_version_id=_VERSION_ID,
        version_label="1.0",
        title="Company Car Driver Safety Requirements",
        status="Current",
        is_current=True,
        is_valid=True,
        effective_from="2025-01-01",
        source_filename=(
            "Řidičský předpis 42.PDF"
        ),
        source_hash=_HASH,
        processing_hash="b" * 64,
        artifact_generation_id=_HASH,
        manifest=_manifest(),
        summary=DirectiveSummary(
            directive_id=_DIRECTIVE_ID,
            directive_version_id=_VERSION_ID,
            source_hash=_HASH,
            summary="Summary",
            covered_section_ids=[],
            total_section_count=0,
            input_token_count=0,
            strategy="full_document",
            model_deployment="test",
        ),
        artifacts=DirectiveArtifactLocators(
            canonical_blob_name=(
                f"directives/{directive_storage_key(_DIRECTIVE_ID)}/"
                f"{directive_version_storage_key(_DIRECTIVE_ID, '1')}/"
                f"{_HASH}/generations/{_HASH}/document.md"
            ),
            source_blob_name=(
                f"directives/{directive_storage_key(_DIRECTIVE_ID)}/"
                f"{directive_version_storage_key(_DIRECTIVE_ID, '1')}/"
                f"{_HASH}/source.pdf"
            ),
        ),
        section_content={},
        run_id="test-run",
        published_at="2026-07-25T12:00:00Z",
    )


class _Catalog:
    def __init__(
        self,
        *,
        bundle: PublishedDirectiveVersion | None = None,
    ) -> None:
        self.bundle = bundle if bundle is not None else _bundle()
        self.bundle_requests: list[tuple[str, str]] = []

    async def get_published_version(
        self, directive_id: str, version_id: str
    ):
        self.bundle_requests.append((directive_id, version_id))
        return self.bundle

    @staticmethod
    def public_version(_version: dict) -> dict:
        return {
            "directive_id": _DIRECTIVE_ID,
            "directive_version_id": _VERSION_ID,
            "version_label": "1.0",
            "title": "Company Car Driver Safety Requirements",
            "effective_from": "2025-01-01",
            "source_filename": (
                "Řidičský předpis 42.PDF"
            ),
            "source_hash": _HASH,
        }


class _Artifacts:
    def __init__(self) -> None:
        self.text_names: list[str] = []
        self.stream_names: list[str] = []

    async def read_text(self, blob_name: str) -> str:
        self.text_names.append(blob_name)
        return "# Driver safety"

    async def stream_bytes(self, blob_name: str) -> AsyncIterator[bytes]:
        self.stream_names.append(blob_name)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"%PDF-"
            yield b"content"

        return chunks()


class DirectiveDocumentServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_exact_published_markdown(self) -> None:
        catalog = _Catalog()
        artifacts = _Artifacts()
        service = DirectiveDocumentService(catalog, artifacts)

        document = await service.get_document(_DIRECTIVE_ID, _VERSION_ID)

        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(document.markdown, "# Driver safety")
        self.assertEqual(document.total_pages, 4)
        self.assertNotIn("source_hash", document.model_dump(mode="json"))
        self.assertEqual(
            artifacts.text_names,
            [catalog.bundle.artifacts.canonical_blob_name],
        )
        self.assertEqual(
            catalog.bundle_requests,
            [(_DIRECTIVE_ID, _VERSION_ID)],
        )

    async def test_streams_manifest_selected_pdf_in_chunks(self) -> None:
        catalog = _Catalog()
        artifacts = _Artifacts()
        service = DirectiveDocumentService(catalog, artifacts)

        source = await service.get_source(_DIRECTIVE_ID, _VERSION_ID)

        self.assertIsNotNone(source)
        assert source is not None
        body = b"".join([chunk async for chunk in source.chunks])
        self.assertEqual(body, b"%PDF-content")
        self.assertEqual(
            artifacts.stream_names,
            [catalog.bundle.artifacts.source_blob_name],
        )
        self.assertEqual(
            catalog.bundle_requests,
            [(_DIRECTIVE_ID, _VERSION_ID)],
        )

    async def test_missing_version_does_not_read_artifacts(self) -> None:
        catalog = _Catalog()
        catalog.bundle = None
        artifacts = _Artifacts()
        service = DirectiveDocumentService(catalog, artifacts)

        document = await service.get_document(_DIRECTIVE_ID, _VERSION_ID)

        self.assertIsNone(document)
        self.assertEqual(
            catalog.bundle_requests,
            [(_DIRECTIVE_ID, _VERSION_ID)],
        )
        self.assertEqual(artifacts.text_names, [])

    async def test_rejects_mismatched_manifest_identity(self) -> None:
        invalid_bundle = _bundle().model_copy(
            update={
                "manifest": _manifest(directive_version_id="ČD/42-A:v2")
            }
        )
        catalog = _Catalog(bundle=invalid_bundle)
        service = DirectiveDocumentService(catalog, _Artifacts())

        with self.assertRaises(DirectiveDataUnavailable):
            await service.get_document(_DIRECTIVE_ID, _VERSION_ID)


class _Download:
    def __init__(self) -> None:
        self.started = False

    def chunks(self) -> AsyncIterator[bytes]:
        async def values() -> AsyncIterator[bytes]:
            self.started = True
            yield b"first"
            yield b"second"

        return values()


class _Container:
    def __init__(self, download: _Download) -> None:
        self.download = download
        self.names: list[str] = []

    async def download_blob(self, name: str) -> _Download:
        self.names.append(name)
        return self.download


class DirectiveArtifactStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_does_not_buffer_entire_blob(self) -> None:
        download = _Download()
        container = _Container(download)
        repository = DirectiveArtifactRepository()
        repository._container = container

        chunks = await repository.stream_bytes(
            _bundle().artifacts.source_blob_name
        )

        self.assertFalse(download.started)
        iterator = aiter(chunks)
        self.assertEqual(await anext(iterator), b"first")
        self.assertTrue(download.started)

    async def test_rejects_client_style_blob_coordinates(self) -> None:
        repository = DirectiveArtifactRepository()
        repository._container = _Container(_Download())

        with self.assertRaises(DirectiveDataUnavailable):
            await repository.stream_bytes("../private/source.pdf")


@dataclass
class _RouteService:
    document: DirectiveDocumentResponse | None = None
    source: DirectiveSourceStream | None = None
    error: Exception | None = None

    async def get_document(self, _directive_id: str, _version_id: str):
        if self.error:
            raise self.error
        return self.document

    async def get_source(self, _directive_id: str, _version_id: str):
        if self.error:
            raise self.error
        return self.source


def _user() -> User:
    return User("tenant:user", "User", "user@example.com", "U")


class DirectiveDocumentRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_document_route_returns_safe_contract(self) -> None:
        document = DirectiveDocumentResponse(
            directive_id=_DIRECTIVE_ID,
            directive_version_id=_VERSION_ID,
            title="Driver safety",
            version_label="1.0",
            effective_from="2025-01-01",
            source_filename="driver-safety.pdf",
            total_pages=4,
            markdown="# Driver safety",
        )
        with patch.object(
            server.services,
            "directive_documents",
            _RouteService(document=document),
        ):
            result = await server.get_directive_document(
                _DIRECTIVE_ID,
                _VERSION_ID,
                _user(),
            )

        self.assertEqual(result, document)

    async def test_pdf_route_sets_private_inline_headers(self) -> None:
        async def chunks() -> AsyncIterator[bytes]:
            yield b"%PDF"

        source = DirectiveSourceStream(
            source_filename="driver safety.pdf",
            source_hash=_HASH,
            chunks=chunks(),
        )
        with patch.object(
            server.services,
            "directive_documents",
            _RouteService(source=source),
        ):
            response = await server.get_directive_source(
                _DIRECTIVE_ID,
                _VERSION_ID,
                _user(),
            )

        body = b"".join([chunk async for chunk in response.body_iterator])
        self.assertEqual(body, b"%PDF")
        self.assertEqual(response.media_type, "application/pdf")
        self.assertEqual(
            response.headers["content-disposition"],
            'inline; filename="directive.pdf"; '
            "filename*=UTF-8''driver%20safety.pdf",
        )
        self.assertEqual(response.headers["etag"], f'"{_HASH}"')
        self.assertEqual(
            response.headers["cache-control"],
            "private, max-age=3600, immutable",
        )
        self.assertEqual(
            response.headers["x-content-type-options"],
            "nosniff",
        )

    async def test_routes_map_missing_and_unavailable_versions(self) -> None:
        with patch.object(
            server.services,
            "directive_documents",
            _RouteService(),
        ):
            with self.assertRaises(HTTPException) as missing:
                await server.get_directive_document(
                    _DIRECTIVE_ID,
                    _VERSION_ID,
                    _user(),
                )
        self.assertEqual(missing.exception.status_code, 404)

        with patch.object(
            server.services,
            "directive_documents",
            _RouteService(
                error=DirectiveDataUnavailable("internal storage detail")
            ),
        ):
            with self.assertRaises(HTTPException) as unavailable:
                await server.get_directive_source(
                    _DIRECTIVE_ID,
                    _VERSION_ID,
                    _user(),
                )
        self.assertEqual(unavailable.exception.status_code, 503)
        self.assertNotIn(
            "storage",
            str(unavailable.exception.detail).lower(),
        )

    async def test_routes_require_authenticated_user_dependency(self) -> None:
        paths = {
            "/directives/document",
            "/directives/source",
        }
        routes = [
            route
            for route in server.app.routes
            if getattr(route, "path", None) in paths
        ]

        self.assertEqual(len(routes), 2)
        for route in routes:
            dependencies = {
                dependency.call
                for dependency in route.dependant.dependencies
            }
            self.assertIn(get_current_user, dependencies)

    async def test_query_routes_reject_mismatched_identities(self) -> None:
        with self.assertRaises(HTTPException) as error:
            await server.get_directive_document(
                _DIRECTIVE_ID,
                "OTHER:v1",
                _user(),
            )
        self.assertEqual(error.exception.status_code, 422)
