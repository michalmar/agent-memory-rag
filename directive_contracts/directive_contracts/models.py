"""Versioned directive ingestion contracts."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewFinding(ContractModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str


class DirectiveMetadata(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    directive_id: str = Field(pattern=r"^\d{8}$")
    directive_version_id: str
    version_label: str
    title: str
    aliases: list[str] = Field(default_factory=list)
    status: str
    is_current: bool
    effective_from: date
    effective_to: date | None = None
    language: str = "en"
    document_type: Literal["directive", "sub_directive"] = "directive"
    source_filename: str
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    processing_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DirectiveSection(ContractModel):
    section_id: str
    ordinal: int = Field(ge=0)
    number: str | None = None
    title: str
    path: list[str]
    page_from: int = Field(ge=1)
    page_to: int = Field(ge=1)
    token_count: int = Field(ge=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    blob_name: str
    chunk_ids: list[str]


class DirectiveManifest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    directive_id: str
    directive_version_id: str
    source_hash: str
    total_pages: int = Field(ge=1)
    total_tokens: int = Field(ge=0)
    canonical_blob_name: str
    source_blob_name: str
    summary_blob_name: str
    manifest_blob_name: str
    sections: list[DirectiveSection]


class DirectiveChunk(ContractModel):
    id: str
    directive_id: str
    directive_version_id: str
    version_label: str
    title: str
    aliases: list[str]
    is_current: bool
    status: str
    effective_from: date
    effective_to: date | None = None
    section_id: str
    section_number: str | None = None
    section_title: str
    section_path: list[str]
    chunk_ordinal: int = Field(ge=0)
    content_kind: Literal["prose", "table", "mixed", "document_control"]
    page_from: int = Field(ge=1)
    page_to: int = Field(ge=1)
    content: str
    content_vector: list[float]
    language: str
    source_hash: str
    processing_hash: str
    publication_state: Literal["staged", "published", "retired"] = "staged"


class DirectiveRelation(ContractModel):
    relation_id: str
    source_directive_id: str
    source_version_id: str
    target_directive_id: str
    target_version_label: str | None = None
    relation_type: Literal["parent", "sub_directive", "reference"]
    status: Literal["accepted", "needs_review"]
    evidence: str


class DirectiveSummary(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    directive_id: str
    directive_version_id: str
    source_hash: str
    summary: str
    covered_section_ids: list[str]
    total_section_count: int = Field(ge=0)
    input_token_count: int = Field(ge=0)
    strategy: Literal["full_document", "section_batches"]
    model_deployment: str


class MandateAssignment(ContractModel):
    user_id: str
    directive_id: str = Field(pattern=r"^\d{8}$")
    flag: Literal["M"] = "M"


class MandateSnapshot(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    snapshot_id: str
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    assignment_count: int = Field(ge=0)
    user_count: int = Field(ge=0)
    complete: bool
    previous_snapshot_id: str | None = None
