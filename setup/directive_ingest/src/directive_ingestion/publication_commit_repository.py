"""Durable, internal publication-cleanup commit markers."""

from __future__ import annotations

from dataclasses import dataclass

from directive_contracts import PublishedDirectiveVersion

from .blob_repository import BlobArtifactRepository

_MARKER_NAME = "publication-commit/current.json"


@dataclass(frozen=True)
class PublicationCommit:
    run_id: str
    stale_bundles: tuple[PublishedDirectiveVersion, ...]
    expected_state_names: frozenset[str]


class PublicationCommitRepository:
    """A marker is written before cleanup and cleared only after exact verify."""

    def __init__(self, blobs: BlobArtifactRepository) -> None:
        self._blobs = blobs

    async def load(self) -> PublicationCommit | None:
        value = await self._blobs.get_json(_MARKER_NAME)
        if value is None:
            return None
        try:
            run_id = value["run_id"]
            bundles = tuple(
                PublishedDirectiveVersion.model_validate(item)
                for item in value["stale_bundles"]
            )
            names = frozenset(value["expected_state_names"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Publication commit marker is invalid") from exc
        if not isinstance(run_id, str) or not all(
            isinstance(name, str) for name in names
        ):
            raise RuntimeError("Publication commit marker has invalid identities")
        return PublicationCommit(run_id, bundles, names)

    async def record(
        self,
        run_id: str,
        stale_bundles: list[PublishedDirectiveVersion],
        expected_state_names: set[str],
    ) -> PublicationCommit:
        marker = PublicationCommit(
            run_id,
            tuple(
                sorted(
                    stale_bundles,
                    key=lambda value: (
                        value.directive_id,
                        value.directive_version_id,
                        value.artifact_generation_id,
                    ),
                )
            ),
            frozenset(expected_state_names),
        )
        await self._blobs.replace_json(
            _MARKER_NAME,
            {
                "type": "publication_commit",
                "run_id": marker.run_id,
                "stale_bundles": [
                    bundle.model_dump(mode="json")
                    for bundle in marker.stale_bundles
                ],
                "expected_state_names": sorted(marker.expected_state_names),
            },
        )
        return marker

    async def clear(self) -> None:
        await self._blobs.delete_names({_MARKER_NAME})
