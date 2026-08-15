"""Versioned directive ingestion contracts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from ._identity import (
    build_directive_version_id,
    normalize_directive_id,
    normalize_directive_version,
    published_directive_version_item_id,
    validate_directive_version_id,
)
from .source_files import validate_directive_source_basename


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewFinding(ContractModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str


class DirectiveMetadata(ContractModel):
    schema_version: Literal["2.0"] = "2.0"
    directive_id: str
    directive_version_id: str
    version_label: str
    title: str
    aliases: list[str] = Field(default_factory=list)
    status: Literal["Current"]
    is_current: Literal[True]
    is_valid: Literal[True]
    effective_from: date
    effective_to: date | None = None
    language: Literal["cs"] = "cs"
    document_type: Literal["directive", "sub_directive"] = "directive"
    source_filename: str
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    processing_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("directive_id", mode="before")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        return normalize_directive_id(value)

    @field_validator("version_label")
    @classmethod
    def validate_version_label(cls, value: str) -> str:
        normalize_directive_version(value)
        return value

    @field_validator("source_filename")
    @classmethod
    def validate_source_filename(cls, value: str) -> str:
        return validate_directive_source_basename(value)

    @model_validator(mode="after")
    def validate_identity(self) -> DirectiveMetadata:
        expected = build_directive_version_id(
            self.directive_id, self.version_label
        )
        if self.directive_version_id != expected:
            raise ValueError(
                "Directive version ID does not match directive ID and version"
            )
        if self.effective_to is not None:
            raise ValueError("Current directive metadata cannot have effective_to")
        return self


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
    chunk_ids: list[str]


class DirectiveManifest(ContractModel):
    schema_version: Literal["3.0"] = "3.0"
    directive_id: str
    directive_version_id: str
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_generation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    total_pages: int = Field(ge=1)
    total_tokens: int = Field(ge=0)
    sections: list[DirectiveSection]

    @field_validator("directive_id", mode="before")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        return normalize_directive_id(value)

    @field_validator("directive_version_id")
    @classmethod
    def validate_version_id(cls, value: str) -> str:
        return validate_directive_version_id(value)

    @model_validator(mode="after")
    def validate_sections(self) -> DirectiveManifest:
        validate_directive_version_id(
            self.directive_version_id, self.directive_id
        )
        section_ids = [section.section_id for section in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("Manifest section IDs must be unique")
        ordinals = [section.ordinal for section in self.sections]
        if ordinals != sorted(ordinals) or len(ordinals) != len(set(ordinals)):
            raise ValueError(
                "Manifest sections must have unique ascending ordinals"
            )
        return self


class DirectiveArtifactLocators(ContractModel):
    canonical_blob_name: str
    source_blob_name: str

    @field_validator("canonical_blob_name", "source_blob_name")
    @classmethod
    def validate_relative_blob_name(cls, value: str) -> str:
        if (
            not value
            or value.startswith(("/", "\\"))
            or "\\" in value
            or "://" in value
            or "?" in value
            or "#" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("Blob locators must be relative catalog-owned names")
        return value


class DirectiveSectionContentDescriptor(ContractModel):
    part_count: int = Field(ge=1)


class PublishedDirectiveVersion(DirectiveMetadata):
    id: str
    type: Literal["version"] = "version"
    artifact_schema_version: Literal["3.0"] = "3.0"
    artifact_generation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    publication_state: Literal["published"] = "published"
    manifest: DirectiveManifest
    summary: DirectiveSummary
    artifacts: DirectiveArtifactLocators
    section_content: dict[str, DirectiveSectionContentDescriptor]
    run_id: str
    published_at: datetime

    @model_validator(mode="after")
    def validate_bundle(self) -> PublishedDirectiveVersion:
        if self.id != published_directive_version_item_id(
            self.directive_id, self.version_label
        ):
            raise ValueError("Published version item ID does not match version")
        if (
            self.manifest.directive_id != self.directive_id
            or self.summary.directive_id != self.directive_id
        ):
            raise ValueError("Bundle directive IDs do not agree")
        if (
            self.manifest.directive_version_id != self.directive_version_id
            or self.summary.directive_version_id != self.directive_version_id
        ):
            raise ValueError("Bundle directive version IDs do not agree")
        if (
            self.manifest.source_hash != self.source_hash
            or self.summary.source_hash != self.source_hash
        ):
            raise ValueError("Bundle source hashes do not agree")
        if (
            self.manifest.artifact_generation_id
            != self.artifact_generation_id
        ):
            raise ValueError("Bundle artifact generation IDs do not agree")
        manifest_section_ids = [
            section.section_id for section in self.manifest.sections
        ]
        if (
            self.summary.total_section_count != len(manifest_section_ids)
            or self.summary.covered_section_ids != manifest_section_ids
        ):
            raise ValueError(
                "Bundle summary coverage must match manifest sections"
            )
        if set(self.section_content) != set(manifest_section_ids):
            raise ValueError(
                "Bundle section descriptors must match manifest sections"
            )
        return self


class DirectiveSectionContent(ContractModel):
    id: str
    type: Literal["section_content"] = "section_content"
    directive_id: str
    directive_version_id: str
    artifact_generation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    section_id: str
    section_ordinal: int = Field(ge=0)
    part_ordinal: int = Field(ge=0)
    part_count: int = Field(ge=1)
    part_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    section_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: str
    run_id: str
    created_at: datetime

    @field_validator("directive_id", mode="before")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        return normalize_directive_id(value)

    @field_validator("directive_version_id")
    @classmethod
    def validate_version_id(cls, value: str) -> str:
        return validate_directive_version_id(value)

    @model_validator(mode="after")
    def validate_part_ordinal(self) -> DirectiveSectionContent:
        validate_directive_version_id(
            self.directive_version_id, self.directive_id
        )
        if self.part_ordinal >= self.part_count:
            raise ValueError("Section part ordinal must be below part count")
        return self


class DirectiveChunk(ContractModel):
    id: str
    directive_id: str
    directive_version_id: str
    version_label: str
    title: str
    aliases: list[str]
    is_current: bool
    is_valid: bool
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

    @field_validator("directive_id", mode="before")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        return normalize_directive_id(value)

    @field_validator("directive_version_id")
    @classmethod
    def validate_version_id(cls, value: str) -> str:
        return validate_directive_version_id(value)

    @model_validator(mode="after")
    def validate_identity(self) -> DirectiveChunk:
        validate_directive_version_id(
            self.directive_version_id, self.directive_id
        )
        return self


class DirectiveRelation(ContractModel):
    relation_id: str
    source_directive_id: str
    source_version_id: str
    target_directive_id: str
    target_version_label: str | None = None
    relation_type: Literal["parent", "sub_directive", "reference"]
    status: Literal["accepted", "needs_review"]
    evidence: str

    @field_validator("source_directive_id", "target_directive_id", mode="before")
    @classmethod
    def normalize_directive_ids(cls, value: str) -> str:
        return normalize_directive_id(value)

    @field_validator("source_version_id")
    @classmethod
    def validate_source_version_id(cls, value: str) -> str:
        return validate_directive_version_id(value)

    @model_validator(mode="after")
    def validate_source_identity(self) -> DirectiveRelation:
        validate_directive_version_id(
            self.source_version_id, self.source_directive_id
        )
        return self


class DirectiveSummary(ContractModel):
    schema_version: Literal["2.0"] = "2.0"
    directive_id: str
    directive_version_id: str
    source_hash: str
    summary: str
    covered_section_ids: list[str]
    total_section_count: int = Field(ge=0)
    input_token_count: int = Field(ge=0)
    strategy: Literal["full_document", "section_batches"]
    model_deployment: str

    @field_validator("directive_id", mode="before")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        return normalize_directive_id(value)

    @field_validator("directive_version_id")
    @classmethod
    def validate_version_id(cls, value: str) -> str:
        return validate_directive_version_id(value)

    @model_validator(mode="after")
    def validate_identity(self) -> DirectiveSummary:
        validate_directive_version_id(
            self.directive_version_id, self.directive_id
        )
        return self


class MandateAssignment(ContractModel):
    user_id: str
    directive_id: str
    flag: Literal["M"] = "M"

    @field_validator("directive_id", mode="before")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        return normalize_directive_id(value)


class MandateSnapshot(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    snapshot_id: str
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    assignment_count: int = Field(ge=0)
    user_count: int = Field(ge=0)
    complete: bool
    previous_snapshot_id: str | None = None


PublishedDirectiveVersion.model_rebuild()
