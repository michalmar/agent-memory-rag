"""Fail-closed publication gate for cross-store activation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from azure.core import MatchConditions
from azure.cosmos import exceptions
from azure.cosmos.aio import CosmosClient

PublicationGateState = Literal[
    "committed",
    "activating",
    "recovery_required",
]
_GATE_ID = "directive-publication-gate"
_GATE_PARTITION = "_control"


@dataclass(frozen=True, slots=True)
class PublicationGateSnapshot:
    state: PublicationGateState
    revision: str
    candidate_revision: str | None
    run_id: str
    etag: str


class PublicationGateRepository:
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

    async def read(self) -> PublicationGateSnapshot | None:
        try:
            item = await self._container.read_item(
                item=_GATE_ID,
                partition_key=_GATE_PARTITION,
            )
        except exceptions.CosmosResourceNotFoundError:
            return None
        return _parse_gate(item)

    async def initialize_committed(
        self,
        *,
        revision: str,
        run_id: str,
    ) -> PublicationGateSnapshot:
        body = _gate_body(
            state="committed",
            revision=revision,
            candidate_revision=None,
            run_id=run_id,
        )
        try:
            item = await self._container.create_item(body)
        except exceptions.CosmosResourceExistsError:
            existing = await self.read()
            if existing is None:
                raise RuntimeError("Publication gate disappeared during creation")
            return existing
        return _parse_gate(item)

    async def transition(
        self,
        snapshot: PublicationGateSnapshot,
        *,
        state: PublicationGateState,
        revision: str,
        candidate_revision: str | None,
        run_id: str,
    ) -> PublicationGateSnapshot:
        body = _gate_body(
            state=state,
            revision=revision,
            candidate_revision=candidate_revision,
            run_id=run_id,
        )
        item = await self._container.replace_item(
            item=_GATE_ID,
            body=body,
            etag=snapshot.etag,
            match_condition=MatchConditions.IfNotModified,
        )
        return _parse_gate(item)


def _gate_body(
    *,
    state: PublicationGateState,
    revision: str,
    candidate_revision: str | None,
    run_id: str,
) -> dict[str, object]:
    if state not in {"committed", "activating", "recovery_required"}:
        raise ValueError("Publication gate state is invalid")
    if not revision or not run_id:
        raise ValueError("Publication gate revision and run ID are required")
    if state != "committed" and not candidate_revision:
        raise ValueError(
            "Non-committed publication gate requires a candidate revision"
        )
    if state == "committed" and candidate_revision is not None:
        raise ValueError("Committed publication gate cannot retain a candidate")
    return {
        "id": _GATE_ID,
        "directive_id": _GATE_PARTITION,
        "type": "publication_gate",
        "state": state,
        "committed_revision": revision,
        "candidate_revision": candidate_revision,
        "run_id": run_id,
        "updated_at": datetime.now(UTC).isoformat(),
    }


def _parse_gate(item: Any) -> PublicationGateSnapshot:
    if not isinstance(item, dict):
        raise RuntimeError("Publication gate is not an object")
    required = {
        "id",
        "directive_id",
        "type",
        "state",
        "committed_revision",
        "candidate_revision",
        "run_id",
        "updated_at",
        "_etag",
    }
    if not required <= set(item):
        raise RuntimeError("Publication gate has an invalid schema")
    if (
        item["id"] != _GATE_ID
        or item["directive_id"] != _GATE_PARTITION
        or item["type"] != "publication_gate"
        or item["state"] not in {
            "committed",
            "activating",
            "recovery_required",
        }
        or not isinstance(item["committed_revision"], str)
        or not item["committed_revision"]
        or not isinstance(item["run_id"], str)
        or not item["run_id"]
        or not isinstance(item["_etag"], str)
        or not item["_etag"]
    ):
        raise RuntimeError("Publication gate is invalid")
    candidate = item["candidate_revision"]
    if candidate is not None and (
        not isinstance(candidate, str) or not candidate
    ):
        raise RuntimeError("Publication gate candidate revision is invalid")
    if item["state"] != "committed" and candidate is None:
        raise RuntimeError("Non-committed publication gate has no candidate")
    if item["state"] == "committed" and candidate is not None:
        raise RuntimeError("Committed publication gate retained a candidate")
    try:
        updated_at = datetime.fromisoformat(item["updated_at"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Publication gate update time is invalid") from exc
    if updated_at.tzinfo is None:
        raise RuntimeError("Publication gate update time requires a timezone")
    return PublicationGateSnapshot(
        state=item["state"],
        revision=item["committed_revision"],
        candidate_revision=candidate,
        run_id=item["run_id"],
        etag=item["_etag"],
    )
