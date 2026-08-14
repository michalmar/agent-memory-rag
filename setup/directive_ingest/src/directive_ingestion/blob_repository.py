"""Immutable directive artifact publication in Azure Blob Storage."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient

from .integrity import IntegrityValidationError


class BlobArtifactRepository:
    def __init__(
        self,
        account_url: str,
        container_name: str,
        credential: Any,
    ) -> None:
        self._service = BlobServiceClient(
            account_url=account_url, credential=credential
        )
        self._container = self._service.get_container_client(container_name)

    async def close(self) -> None:
        await self._service.close()

    async def check_access(self) -> None:
        await self._container.get_container_properties()

    async def list_names(self, prefix: str) -> set[str]:
        names: set[str] = set()
        async for blob in self._container.list_blobs(name_starts_with=prefix):
            names.add(blob.name)
        return names

    async def put_immutable(
        self,
        blob_name: str,
        content: bytes,
        content_type: str,
    ) -> None:
        content_hash = hashlib.sha256(content).hexdigest()
        blob = self._container.get_blob_client(blob_name)
        try:
            await blob.upload_blob(
                content,
                overwrite=False,
                metadata={"content_sha256": content_hash},
                content_settings=ContentSettings(content_type=content_type),
            )
        except ResourceExistsError:
            properties = await blob.get_blob_properties()
            existing_hash = properties.metadata.get("content_sha256")
            if existing_hash != content_hash:
                raise RuntimeError(
                    f"Immutable artifact collision at {blob_name}"
                ) from None

    async def put_json(self, blob_name: str, value: object) -> None:
        content = json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            default=str,
        ).encode()
        await self.put_immutable(blob_name, content, "application/json")

    async def replace_json(
        self,
        blob_name: str,
        value: object,
        *,
        expected_etag: str | None = None,
        require_absent: bool = False,
    ) -> str:
        """Replace mutable internal state with an optimistic-concurrency guard."""
        content = json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            default=str,
        ).encode()
        blob = self._container.get_blob_client(blob_name)
        if require_absent:
            try:
                response = await blob.upload_blob(
                    content,
                    overwrite=False,
                    metadata={"content_sha256": hashlib.sha256(content).hexdigest()},
                    content_settings=ContentSettings(content_type="application/json"),
                )
            except ResourceExistsError as exc:
                raise RuntimeError(
                    f"Concurrent source-state replacement prevented at {blob_name}"
                ) from exc
            return _response_etag(response, blob_name)
        try:
            properties = await blob.get_blob_properties()
        except ResourceNotFoundError:
            try:
                response = await blob.upload_blob(
                    content,
                    overwrite=False,
                    metadata={"content_sha256": hashlib.sha256(content).hexdigest()},
                    content_settings=ContentSettings(content_type="application/json"),
                )
            except ResourceExistsError as exc:
                raise RuntimeError(
                    f"Concurrent source-state replacement prevented at {blob_name}"
                ) from exc
            return _response_etag(response, blob_name)
        etag = expected_etag or getattr(properties, "etag", None)
        if not isinstance(etag, str) or not etag:
            raise RuntimeError(f"State artifact is missing an ETag: {blob_name}")
        try:
            response = await blob.upload_blob(
                content,
                overwrite=True,
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
                metadata={"content_sha256": hashlib.sha256(content).hexdigest()},
                content_settings=ContentSettings(content_type="application/json"),
            )
        except ResourceExistsError as exc:
            raise RuntimeError(
                f"Concurrent source-state replacement prevented at {blob_name}"
            ) from exc
        return _response_etag(response, blob_name)

    async def get_json(self, blob_name: str) -> dict[str, Any] | None:
        """Point-read an internal JSON artifact without enumerating its prefix."""
        blob = self._container.get_blob_client(blob_name)
        try:
            stream = await blob.download_blob()
            value = json.loads((await stream.readall()).decode("utf-8"))
        except ResourceNotFoundError:
            return None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrityValidationError(
                f"Invalid JSON artifact: {blob_name}"
            ) from exc
        if not isinstance(value, dict):
            raise IntegrityValidationError(
                f"JSON artifact must be an object: {blob_name}"
            )
        return value

    async def read_bytes_with_etag(
        self, blob_name: str
    ) -> tuple[bytes, str] | None:
        blob = self._container.get_blob_client(blob_name)
        try:
            stream = await blob.download_blob()
            etag = getattr(getattr(stream, "properties", None), "etag", None)
            if not isinstance(etag, str) or not etag:
                raise RuntimeError(f"State artifact is missing an ETag: {blob_name}")
            return await stream.readall(), etag
        except ResourceNotFoundError:
            return None

    async def restore_bytes(
        self, blob_name: str, content: bytes, candidate_etag: str
    ) -> None:
        blob = self._container.get_blob_client(blob_name)
        await blob.upload_blob(
            content,
            overwrite=True,
            etag=candidate_etag,
            match_condition=MatchConditions.IfNotModified,
            metadata={"content_sha256": hashlib.sha256(content).hexdigest()},
            content_settings=ContentSettings(content_type="application/json"),
        )

    async def delete_if_etag(self, blob_name: str, candidate_etag: str) -> None:
        await self._container.delete_blob(
            blob_name,
            delete_snapshots="include",
            etag=candidate_etag,
            match_condition=MatchConditions.IfNotModified,
        )

    async def content_hash(self, blob_name: str) -> str:
        blob = self._container.get_blob_client(blob_name)
        try:
            properties = await blob.get_blob_properties()
            content = await (await blob.download_blob()).readall()
        except ResourceNotFoundError as exc:
            raise IntegrityValidationError(
                f"Expected artifact is missing: {blob_name}"
            ) from exc
        metadata = getattr(properties, "metadata", None)
        value = metadata.get("content_sha256") if isinstance(metadata, dict) else None
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise IntegrityValidationError(
                f"Artifact is missing a valid content hash: {blob_name}"
            )
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != value:
            raise IntegrityValidationError(
                f"Artifact payload hash does not match metadata: {blob_name}"
            )
        return actual_hash

    async def read_text(self, blob_name: str) -> str:
        try:
            stream = await self._container.get_blob_client(
                blob_name
            ).download_blob()
            content = await stream.readall()
        except ResourceNotFoundError as exc:
            raise IntegrityValidationError(
                f"Expected artifact is missing: {blob_name}"
            ) from exc
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IntegrityValidationError(
                f"Artifact is not valid UTF-8 text: {blob_name}"
            ) from exc

    async def validate_hash(
        self, blob_name: str, expected_hash: str
    ) -> None:
        actual_hash = await self.content_hash(blob_name)
        if actual_hash != expected_hash:
            raise IntegrityValidationError(
                f"Artifact hash mismatch at {blob_name}"
            )

    async def quarantine(
        self,
        run_id: str,
        filename: str,
        source: bytes,
        errors: list[str],
    ) -> None:
        base = f"quarantine/{run_id}/{filename}"
        await self.put_immutable(base, source, "application/pdf")
        await self.put_json(
            f"{base}.json",
            {
                "filename": filename,
                "source_hash": hashlib.sha256(source).hexdigest(),
                "errors": errors,
            },
        )

    async def exists(self, blob_name: str) -> bool:
        return await self._container.get_blob_client(blob_name).exists()

    async def delete_names(self, names: set[str]) -> None:
        for name in sorted(names):
            try:
                await self._container.delete_blob(
                    name, delete_snapshots="include"
                )
            except ResourceNotFoundError:
                continue


def _response_etag(response: object, blob_name: str) -> str:
    etag = (
        response.get("etag")
        if isinstance(response, dict)
        else getattr(response, "etag", None)
    )
    if not isinstance(etag, str) or not etag:
        raise RuntimeError(f"State artifact is missing an ETag: {blob_name}")
    return etag
