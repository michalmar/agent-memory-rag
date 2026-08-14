"""Immutable directive section-content publication in Cosmos DB."""

from __future__ import annotations

import hashlib
from typing import Any

from azure.cosmos import exceptions
from azure.cosmos.aio import CosmosClient
from directive_contracts import (
    DirectiveSectionContent,
    PublishedDirectiveVersion,
    section_content_item_id,
)

from .integrity import IntegrityValidationError


class DirectiveContentRepository:
    def __init__(
        self,
        endpoint: str,
        database_name: str,
        container_name: str,
        credential: Any,
    ) -> None:
        self._client = CosmosClient(endpoint, credential=credential)
        database = self._client.get_database_client(database_name)
        self._container = database.get_container_client(container_name)

    async def close(self) -> None:
        await self._client.close()

    async def check_access(self) -> None:
        await self._container.read()

    async def create_or_compare(
        self, item: DirectiveSectionContent
    ) -> None:
        payload = item.model_dump(mode="json")
        try:
            await self._container.create_item(body=payload)
            return
        except exceptions.CosmosResourceExistsError:
            pass

        existing = await self.read_item(
            item.directive_version_id,
            item.id,
        )
        expected = item.model_dump(
            mode="json", exclude={"run_id", "created_at"}
        )
        actual = existing.model_dump(
            mode="json", exclude={"run_id", "created_at"}
        )
        if actual != expected:
            raise RuntimeError(
                f"Immutable section-content collision at {item.id}"
            )

    async def read_item(
        self, directive_version_id: str, item_id: str
    ) -> DirectiveSectionContent:
        try:
            value = await self._container.read_item(
                item=item_id,
                partition_key=directive_version_id,
            )
        except exceptions.CosmosResourceNotFoundError as exc:
            raise IntegrityValidationError(
                f"Missing directive section-content item: {item_id}"
            ) from exc
        return _validate_content_record(value)

    async def validate_bundle(
        self, bundle: PublishedDirectiveVersion
    ) -> dict[str, int]:
        sections_by_id = {
            section.section_id: section
            for section in bundle.manifest.sections
        }
        part_total = 0
        split_sections = 0
        for section_id, descriptor in bundle.section_content.items():
            try:
                section = sections_by_id[section_id]
            except KeyError as exc:
                raise IntegrityValidationError(
                    "Published section-content descriptor is missing a "
                    f"manifest section: {section_id}"
                ) from exc
            parts: list[str] = []
            if descriptor.part_count > 1:
                split_sections += 1
            for part_ordinal in range(descriptor.part_count):
                item_id = section_content_item_id(
                    bundle.artifact_generation_id,
                    section_id,
                    part_ordinal,
                )
                item = await self.read_item(
                    bundle.directive_version_id, item_id
                )
                _validate_expected_part(
                    item,
                    bundle=bundle,
                    section_id=section_id,
                    section_ordinal=section.ordinal,
                    section_hash=section.content_hash,
                    part_ordinal=part_ordinal,
                    part_count=descriptor.part_count,
                )
                parts.append(item.content)
                part_total += 1
            content = "".join(parts)
            content_hash = hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest()
            if content_hash != section.content_hash:
                raise IntegrityValidationError(
                    "Reconstructed section content hash mismatch for "
                    f"{bundle.directive_version_id}/{section_id}"
                )
        return {
            "content_sections": len(bundle.section_content),
            "content_parts": part_total,
            "split_sections": split_sections,
        }

    async def delete_bundle(self, bundle: PublishedDirectiveVersion) -> None:
        for section_id, descriptor in bundle.section_content.items():
            for part_ordinal in range(descriptor.part_count):
                try:
                    await self._container.delete_item(
                        item=section_content_item_id(
                            bundle.artifact_generation_id,
                            section_id,
                            part_ordinal,
                        ),
                        partition_key=bundle.directive_version_id,
                    )
                except exceptions.CosmosResourceNotFoundError:
                    continue

    async def list_item_ids(self) -> set[str]:
        """Enumerate all content IDs so verify can reject orphaned generations."""
        values: set[str] = set()
        query = "SELECT VALUE c.id FROM c"
        async for value in self._container.query_items(query=query):
            if isinstance(value, str):
                values.add(value)
        return values

    async def list_identities(self) -> set[tuple[str, str, str, str, str]]:
        """Return partitioned identities and validated content hashes."""
        values: set[tuple[str, str, str, str, str]] = set()
        query = "SELECT * FROM c"
        async for value in self._container.query_items(query=query):
            item = _validate_content_record(value)
            values.add(
                (
                    item.directive_version_id,
                    item.id,
                    item.directive_id,
                    item.section_hash,
                    item.part_hash,
                )
            )
        return values


def _validate_content_record(
    value: dict[str, Any],
) -> DirectiveSectionContent:
    if not isinstance(value, dict):
        raise IntegrityValidationError("Invalid directive section-content item")
    application_fields = {
        key: item for key, item in value.items() if not key.startswith("_")
    }
    try:
        return DirectiveSectionContent.model_validate(application_fields)
    except ValueError as exc:
        raise IntegrityValidationError(
            "Invalid directive section-content item"
        ) from exc


def _validate_expected_part(
    item: DirectiveSectionContent,
    *,
    bundle: PublishedDirectiveVersion,
    section_id: str,
    section_ordinal: int,
    section_hash: str,
    part_ordinal: int,
    part_count: int,
) -> None:
    expected_id = section_content_item_id(
        bundle.artifact_generation_id, section_id, part_ordinal
    )
    if (
        item.id != expected_id
        or item.directive_id != bundle.directive_id
        or item.directive_version_id != bundle.directive_version_id
        or item.artifact_generation_id
        != bundle.artifact_generation_id
        or item.section_id != section_id
        or item.section_ordinal != section_ordinal
        or item.part_ordinal != part_ordinal
        or item.part_count != part_count
        or item.section_hash != section_hash
        or item.part_hash
        != hashlib.sha256(item.content.encode("utf-8")).hexdigest()
    ):
        raise IntegrityValidationError(
            "Directive section-content identity or hash mismatch for "
            f"{bundle.directive_version_id}/{section_id}/{part_ordinal}"
        )
