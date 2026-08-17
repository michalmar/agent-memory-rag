"""Strict descriptor inventory committed after verified publication."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from azure.core.exceptions import ResourceNotFoundError

from .blob_repository import BlobArtifactRepository
from .source import SourceDescriptor, SourceIdentity

SOURCE_INVENTORY_BLOB = "source-inventory/current.json"
SOURCE_INVENTORY_SCHEMA = "1.0"


@dataclass(frozen=True, slots=True)
class SourceInventoryEntry:
    source_name: str
    etag: str | None
    version_id: str | None
    size: int
    last_modified: datetime | None
    source_hash: str
    source_state_blob: str

    @classmethod
    def create(
        cls,
        descriptor: SourceDescriptor,
        identity: SourceIdentity,
        source_state_blob: str,
    ) -> "SourceInventoryEntry":
        if descriptor.source_name != identity.source_name:
            raise ValueError("Source descriptor and identity names disagree")
        return cls(
            source_name=identity.source_name,
            etag=descriptor.etag,
            version_id=descriptor.version_id,
            size=descriptor.size,
            last_modified=descriptor.last_modified,
            source_hash=identity.source_hash,
            source_state_blob=source_state_blob,
        )

    def matches(self, descriptor: SourceDescriptor) -> bool:
        if self.source_name != descriptor.source_name or self.size != descriptor.size:
            return False
        if self.version_id is not None or descriptor.version_id is not None:
            return (
                self.version_id is not None
                and descriptor.version_id is not None
                and self.version_id == descriptor.version_id
            )
        return (
            self.etag is not None
            and descriptor.etag is not None
            and self.etag == descriptor.etag
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "etag": self.etag,
            "version_id": self.version_id,
            "size": self.size,
            "last_modified": (
                self.last_modified.astimezone(UTC).isoformat()
                if self.last_modified is not None
                else None
            ),
            "source_hash": self.source_hash,
            "source_state_blob": self.source_state_blob,
        }


@dataclass(frozen=True, slots=True)
class SourceInventory:
    run_id: str
    entries: tuple[SourceInventoryEntry, ...]
    inventory_hash: str
    schema_version: str = SOURCE_INVENTORY_SCHEMA

    @classmethod
    def create(
        cls,
        run_id: str,
        entries: list[SourceInventoryEntry] | tuple[SourceInventoryEntry, ...],
    ) -> "SourceInventory":
        ordered = tuple(sorted(entries, key=lambda entry: entry.source_name))
        if not run_id:
            raise ValueError("Source inventory run ID is required")
        if not ordered:
            raise ValueError("Source inventory must contain entries")
        names = [entry.source_name for entry in ordered]
        hashes = [entry.source_hash for entry in ordered]
        if len(names) != len(set(names)):
            raise ValueError("Source inventory contains duplicate names")
        if len(hashes) != len(set(hashes)):
            raise ValueError("Source inventory contains duplicate hashes")
        projection = {
            "schema_version": SOURCE_INVENTORY_SCHEMA,
            "run_id": run_id,
            "entries": [entry.to_payload() for entry in ordered],
        }
        return cls(
            run_id=run_id,
            entries=ordered,
            inventory_hash=_payload_hash(projection),
        )

    def entry_by_name(self) -> dict[str, SourceInventoryEntry]:
        return {entry.source_name: entry for entry in self.entries}

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "entries": [entry.to_payload() for entry in self.entries],
            "inventory_hash": self.inventory_hash,
        }


@dataclass(frozen=True, slots=True)
class SourceInventorySnapshot:
    inventory: SourceInventory | None
    etag: str | None
    valid: bool


class SourceInventoryRepository:
    def __init__(self, artifacts: BlobArtifactRepository) -> None:
        self._artifacts = artifacts

    async def load_snapshot(self) -> SourceInventorySnapshot:
        try:
            stored = await self._artifacts.read_bytes_with_metadata_and_etag(
                SOURCE_INVENTORY_BLOB
            )
        except ResourceNotFoundError:
            return SourceInventorySnapshot(
                inventory=None,
                etag=None,
                valid=True,
            )
        if stored is None:
            return SourceInventorySnapshot(
                inventory=None,
                etag=None,
                valid=True,
            )
        content, metadata, etag = stored
        try:
            expected_hash = metadata.get("content_sha256")
            actual_hash = hashlib.sha256(content).hexdigest()
            if expected_hash != actual_hash:
                raise ValueError("Source inventory content hash mismatch")
            payload = json.loads(content)
            inventory = _parse_inventory(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            return SourceInventorySnapshot(
                inventory=None,
                etag=etag,
                valid=False,
            )
        return SourceInventorySnapshot(
            inventory=inventory,
            etag=etag,
            valid=True,
        )

    async def commit(
        self,
        inventory: SourceInventory,
        *,
        expected_etag: str | None,
    ) -> None:
        await self._artifacts.replace_json(
            SOURCE_INVENTORY_BLOB,
            inventory.to_payload(),
            expected_etag=expected_etag,
            require_absent=expected_etag is None,
        )


def _parse_inventory(payload: Any) -> SourceInventory:
    _require_keys(
        payload,
        {"schema_version", "run_id", "entries", "inventory_hash"},
        "source inventory",
    )
    if payload["schema_version"] != SOURCE_INVENTORY_SCHEMA:
        raise ValueError("Unsupported source inventory schema")
    run_id = _text(payload["run_id"], "source inventory run ID")
    raw_entries = payload["entries"]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError("Source inventory entries must be a non-empty array")
    entries = tuple(_parse_entry(value) for value in raw_entries)
    parsed = SourceInventory.create(run_id, entries)
    if payload["inventory_hash"] != parsed.inventory_hash:
        raise ValueError("Source inventory hash mismatch")
    return parsed


def _parse_entry(payload: Any) -> SourceInventoryEntry:
    _require_keys(
        payload,
        {
            "source_name",
            "etag",
            "version_id",
            "size",
            "last_modified",
            "source_hash",
            "source_state_blob",
        },
        "source inventory entry",
    )
    source_name = _text(payload["source_name"], "source name")
    etag = _optional_text(payload["etag"], "source ETag")
    version_id = _optional_text(payload["version_id"], "source version ID")
    size = payload["size"]
    if not isinstance(size, int) or isinstance(size, bool) or size < 1:
        raise ValueError("Source inventory size must be positive")
    last_modified = _optional_datetime(payload["last_modified"])
    identity = SourceIdentity(
        source_name=source_name,
        source_hash=_text(payload["source_hash"], "source hash"),
    )
    return SourceInventoryEntry(
        source_name=identity.source_name,
        etag=etag,
        version_id=version_id,
        size=size,
        last_modified=last_modified,
        source_hash=identity.source_hash,
        source_state_blob=_text(
            payload["source_state_blob"],
            "source state blob",
        ),
    )


def _payload_hash(payload: dict[str, Any]) -> str:
    content = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(content).hexdigest()


def _require_keys(payload: Any, keys: set[str], name: str) -> None:
    if not isinstance(payload, dict) or set(payload) != keys:
        raise ValueError(f"{name} has an invalid schema")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value


def _optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _text(value, name)


def _optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Source inventory timestamp must be text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Source inventory timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("Source inventory timestamp requires a timezone")
    return parsed
