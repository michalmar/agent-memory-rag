"""Read-only point access to published directive section content."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from azure.cosmos import exceptions
from directive_contracts import (
    DirectiveSection,
    DirectiveSectionContent,
    PublishedDirectiveVersion,
    section_content_item_id,
)

from .config import get_settings
from .cosmos_container import CosmosContainerLifecycle
from .directive_errors import DirectiveDataUnavailable

logger = logging.getLogger("directive_content")
_MAX_CONCURRENT_READS = 8


class DirectiveContentRepository(CosmosContainerLifecycle):
    _unavailable_error_type = DirectiveDataUnavailable

    async def initialize(self) -> None:
        settings = get_settings()
        if not (
            settings.cosmos_configured
            and settings.directive_cosmos_database
            and settings.directive_content_container
        ):
            logger.warning("Directive content storage is not configured")
            return
        await self._initialize_container(
            settings,
            settings.directive_content_container,
            database_name=settings.directive_cosmos_database,
        )

    async def health_check(self) -> None:
        container = self._require_initialized_container(
            "Directive content storage is unavailable"
        )
        try:
            await container.read()
        except exceptions.CosmosHttpResponseError as exc:
            raise DirectiveDataUnavailable(
                "Directive content health check failed"
            ) from exc

    async def read_sections(
        self,
        bundle: PublishedDirectiveVersion,
        sections: list[DirectiveSection],
    ) -> list[str]:
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_READS)
        requests: list[tuple[DirectiveSection, int, int]] = []
        for section in sections:
            descriptor = bundle.section_content.get(section.section_id)
            if descriptor is None:
                raise DirectiveDataUnavailable(
                    "Published directive section descriptor is missing"
                )
            requests.extend(
                (section, part_ordinal, descriptor.part_count)
                for part_ordinal in range(descriptor.part_count)
            )

        async def read_part(
            section: DirectiveSection,
            part_ordinal: int,
            part_count: int,
        ) -> DirectiveSectionContent:
            async with semaphore:
                return await self._read_part(
                    bundle,
                    section,
                    part_ordinal,
                    part_count,
                )

        parts = await asyncio.gather(
            *(
                read_part(section, part_ordinal, part_count)
                for section, part_ordinal, part_count in requests
            )
        )
        by_section: dict[str, list[DirectiveSectionContent]] = {
            section.section_id: [] for section in sections
        }
        for part in parts:
            by_section[part.section_id].append(part)

        values: list[str] = []
        for section in sections:
            section_parts = sorted(
                by_section[section.section_id],
                key=lambda part: part.part_ordinal,
            )
            content = "".join(part.content for part in section_parts)
            if (
                len(section_parts)
                != bundle.section_content[section.section_id].part_count
                or hashlib.sha256(content.encode("utf-8")).hexdigest()
                != section.content_hash
            ):
                raise DirectiveDataUnavailable(
                    "Published directive section content is incomplete or corrupt"
                )
            values.append(content)
        return values

    async def _read_part(
        self,
        bundle: PublishedDirectiveVersion,
        section: DirectiveSection,
        part_ordinal: int,
        part_count: int,
    ) -> DirectiveSectionContent:
        container = self._require_initialized_container(
            "Directive content storage is unavailable"
        )
        item_id = section_content_item_id(
            bundle.artifact_generation_id,
            section.section_id,
            part_ordinal,
        )
        try:
            item = await container.read_item(
                item=item_id,
                partition_key=bundle.directive_version_id,
            )
        except exceptions.CosmosResourceNotFoundError as exc:
            raise DirectiveDataUnavailable(
                "Published directive section content is missing"
            ) from exc
        except exceptions.CosmosHttpResponseError as exc:
            raise DirectiveDataUnavailable(
                "Directive section content lookup failed"
            ) from exc
        try:
            value = DirectiveSectionContent.model_validate(
                {
                    key: field
                    for key, field in item.items()
                    if not key.startswith("_")
                }
            )
        except ValueError as exc:
            raise DirectiveDataUnavailable(
                "Published directive section content is invalid"
            ) from exc
        if (
            value.id != item_id
            or value.directive_id != bundle.directive_id
            or value.directive_version_id != bundle.directive_version_id
            or value.artifact_generation_id
            != bundle.artifact_generation_id
            or value.section_id != section.section_id
            or value.section_ordinal != section.ordinal
            or value.part_ordinal != part_ordinal
            or value.part_count != part_count
            or value.part_hash
            != hashlib.sha256(value.content.encode("utf-8")).hexdigest()
            or value.section_hash != section.content_hash
        ):
            raise DirectiveDataUnavailable(
                "Published directive section content identity mismatch"
            )
        return value
