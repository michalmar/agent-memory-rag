"""Shared lifecycle mechanics for Cosmos-backed stores."""
from __future__ import annotations

from typing import Any

from .config import Settings


class CosmosContainerLifecycle:
    _unavailable_error_type: type[RuntimeError] = RuntimeError

    def __init__(self) -> None:
        self._client: Any = None
        self._container: Any = None

    async def _initialize_container(
        self,
        settings: Settings,
        container_name: str,
        *,
        database_name: str | None = None,
    ) -> None:
        from azure.cosmos.aio import CosmosClient

        if settings.cosmos_key:
            self._client = CosmosClient(
                settings.cosmos_endpoint,
                credential=settings.cosmos_key,
            )
        else:
            from .azure_clients import get_credential

            self._client = CosmosClient(
                settings.cosmos_endpoint,
                credential=get_credential(),
            )
        database = self._client.get_database_client(
            database_name or settings.cosmos_database
        )
        self._container = database.get_container_client(container_name)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._container = None

    @property
    def enabled(self) -> bool:
        return self._container is not None

    def _require_initialized_container(self, detail: str) -> Any:
        if self._container is None:
            raise self._unavailable_error_type(detail)
        return self._container
