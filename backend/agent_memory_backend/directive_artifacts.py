"""Read-only access to catalog-owned directive artifacts."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

from azure.core.exceptions import AzureError

from .config import get_settings
from .directive_errors import DirectiveDataUnavailable

logger = logging.getLogger("directive_artifacts")


class DirectiveArtifactRepository:
    def __init__(self) -> None:
        self._service: Any = None
        self._container: Any = None

    @property
    def enabled(self) -> bool:
        return self._container is not None

    async def initialize(self) -> None:
        settings = get_settings()
        if not (
            settings.directive_blob_endpoint
            and settings.directive_blob_container
        ):
            logger.warning("Directive artifact storage is not configured")
            return
        from azure.storage.blob.aio import BlobServiceClient

        from .azure_clients import get_credential

        self._service = BlobServiceClient(
            account_url=settings.directive_blob_endpoint,
            credential=get_credential(),
        )
        self._container = self._service.get_container_client(
            settings.directive_blob_container
        )

    async def close(self) -> None:
        if self._service is not None:
            await self._service.close()
            self._service = None
            self._container = None

    async def health_check(self) -> None:
        container = self._require_container()
        try:
            await container.get_container_properties()
        except AzureError as exc:
            raise DirectiveDataUnavailable(
                "Directive artifact health check failed"
            ) from exc

    async def read_text(self, catalog_blob_name: str) -> str:
        blob_name = _validated_catalog_blob_name(catalog_blob_name)
        try:
            download = await self._require_container().download_blob(blob_name)
            return (await download.readall()).decode("utf-8")
        except (AzureError, UnicodeDecodeError) as exc:
            raise DirectiveDataUnavailable(
                "Directive text artifact could not be read"
            ) from exc

    async def read_json(self, catalog_blob_name: str) -> dict[str, Any]:
        try:
            value = json.loads(await self.read_text(catalog_blob_name))
        except json.JSONDecodeError as exc:
            raise DirectiveDataUnavailable(
                "Directive JSON artifact is invalid"
            ) from exc
        if not isinstance(value, dict):
            raise DirectiveDataUnavailable(
                "Directive JSON artifact must be an object"
            )
        return value

    def _require_container(self) -> Any:
        if self._container is None:
            raise DirectiveDataUnavailable(
                "Directive artifact storage is unavailable"
            )
        return self._container


def _validated_catalog_blob_name(value: str) -> str:
    parsed = urlparse(value)
    if (
        not value
        or parsed.scheme
        or parsed.netloc
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise DirectiveDataUnavailable(
            "Catalog contains an invalid directive artifact pointer"
        )
    return value
