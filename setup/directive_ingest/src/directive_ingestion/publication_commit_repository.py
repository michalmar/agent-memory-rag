"""Durable, internal publication-cleanup commit markers."""

from __future__ import annotations

from dataclasses import dataclass

from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError
from directive_contracts import PublishedDirectiveVersion

from .blob_repository import BlobArtifactRepository

_MARKER_NAME = "publication-commit/current.json"
_LOCK_NAME = "publication-lock/current.json"
_LOCK_PREFIX = "publication-lock/"
_CLAIM_PREFIX = "publication-claims/"


@dataclass(frozen=True)
class PublicationCommit:
    run_id: str
    stale_bundles: tuple[PublishedDirectiveVersion, ...]
    expected_state_names: frozenset[str]
    validation_digest: str | None = None
    mandate_checksum: str | None = None


@dataclass(frozen=True)
class PublicationLock:
    """The single-writer lease retained while a publication is in flight."""

    run_id: str
    validation_digest: str
    etag: str


class PublicationResetRequiredError(RuntimeError):
    """A durable publication guard requires an explicit operator reset."""


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
            validation_digest = value.get("validation_digest")
            mandate_checksum = value.get("mandate_checksum")
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Publication commit marker is invalid") from exc
        if not isinstance(run_id, str) or not all(
            isinstance(name, str) for name in names
        ):
            raise RuntimeError("Publication commit marker has invalid identities")
        if validation_digest is not None and (
            not isinstance(validation_digest, str) or not validation_digest.strip()
        ):
            raise RuntimeError(
                "Publication commit marker has an invalid validation digest"
            )
        if (validation_digest is None) != (mandate_checksum is None):
            raise RuntimeError(
                "Publication commit marker must bind validation and mandate checksum"
            )
        if mandate_checksum is not None and not _is_checksum(mandate_checksum):
            raise RuntimeError(
                "Publication commit marker has an invalid mandate checksum"
            )
        return PublicationCommit(
            run_id, bundles, names, validation_digest, mandate_checksum
        )

    async def record(
        self,
        run_id: str,
        stale_bundles: list[PublishedDirectiveVersion],
        expected_state_names: set[str],
        *,
        validation_digest: str | None = None,
        mandate_checksum: str | None = None,
    ) -> PublicationCommit:
        if validation_digest is not None and not validation_digest.strip():
            raise ValueError("Validation digest must not be empty")
        if (validation_digest is None) != (mandate_checksum is None):
            raise ValueError(
                "Validation digest and mandate checksum must be recorded together"
            )
        if mandate_checksum is not None and not _is_checksum(mandate_checksum):
            raise ValueError("Mandate checksum must be a lowercase SHA-256 digest")
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
            validation_digest,
            mandate_checksum,
        )
        payload: dict[str, object] = {
            "type": "publication_commit",
            "run_id": marker.run_id,
            "stale_bundles": [
                bundle.model_dump(mode="json") for bundle in marker.stale_bundles
            ],
            "expected_state_names": sorted(marker.expected_state_names),
        }
        if marker.validation_digest is not None:
            payload["validation_digest"] = marker.validation_digest
            payload["mandate_checksum"] = marker.mandate_checksum
        await self._blobs.replace_json(_MARKER_NAME, payload)
        return marker

    async def clear(self) -> None:
        await self._blobs.delete_names({_MARKER_NAME})

    async def acquire_publication_lock(
        self, run_id: str, validation_digest: str
    ) -> PublicationLock:
        """Create the global lock; an existing lock is never assumed safe."""
        payload = {
            "type": "publication_lock",
            "run_id": run_id,
            "validation_digest": validation_digest,
        }
        try:
            etag = await self._blobs.replace_json(
                _LOCK_NAME, payload, require_absent=True
            )
        except RuntimeError as exc:
            raise PublicationResetRequiredError(
                "Publication lock is present; explicit reset-required"
            ) from exc
        return PublicationLock(run_id, validation_digest, etag)

    async def create_publication_claim(
        self, run_id: str, validation_digest: str
    ) -> str:
        """Durably reserve a validation identity before dispatching writes."""
        if not validation_digest:
            raise ValueError("Publication claim requires a validation digest")
        name = f"{_CLAIM_PREFIX}{validation_digest}.json"
        payload = {
            "type": "publication_claim",
            "run_id": run_id,
            "validation_digest": validation_digest,
        }
        try:
            return await self._blobs.replace_json(
                name, payload, require_absent=True
            )
        except RuntimeError as exc:
            raise PublicationResetRequiredError(
                "Publication claim already exists; explicit reset-required"
            ) from exc

    async def release_publication_lock(self, lock: PublicationLock) -> None:
        """Release only the lock instance owned by this run."""
        try:
            await self._blobs.delete_if_etag(_LOCK_NAME, lock.etag)
        except (RuntimeError, ResourceModifiedError, ResourceNotFoundError) as exc:
            raise PublicationResetRequiredError(
                "Publication lock changed; explicit reset-required"
            ) from exc

    async def discard_undispatched_claim(
        self, validation_digest: str, claim_etag: str
    ) -> None:
        """Remove a claim only when no publication dispatch has begun."""
        try:
            await self._blobs.delete_if_etag(
                f"{_CLAIM_PREFIX}{validation_digest}.json", claim_etag
            )
        except (RuntimeError, ResourceModifiedError, ResourceNotFoundError) as exc:
            raise PublicationResetRequiredError(
                "Publication claim changed; explicit reset-required"
            ) from exc

    async def reset_publication_guards(self) -> None:
        """Explicitly purge stale locks and durable claims."""
        names = await self._blobs.list_names(_LOCK_PREFIX)
        names.update(await self._blobs.list_names(_CLAIM_PREFIX))
        await self._blobs.delete_names(names)


def _is_checksum(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
