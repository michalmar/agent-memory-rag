"""Immutable directive artifact publication in Azure Blob Storage."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient


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
