"""Local PDF source discovery and immutable identity hints."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

_PDF_NAME = re.compile(
    r"^(?P<directive_id>\d{8})-.+-v(?P<version>\d+(?:\.\d+)?)\.pdf$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SourceDocument:
    path: Path
    directive_id_hint: str
    version_hint: str
    source_hash: str
    content: bytes

    def metadata_version_matches(self, version_label: str) -> bool:
        try:
            return Decimal(self.version_hint) == Decimal(version_label)
        except InvalidOperation:
            return False

    @property
    def directive_version_id_hint(self) -> str:
        normalized = format(Decimal(self.version_hint).normalize(), "f")
        return f"{self.directive_id_hint}:v{normalized}"


def discover_pdfs(source_directory: Path) -> list[SourceDocument]:
    if not source_directory.is_dir():
        raise ValueError(
            f"Directive source directory does not exist: {source_directory}"
        )
    documents: list[SourceDocument] = []
    for path in sorted(source_directory.glob("*.pdf")):
        match = _PDF_NAME.fullmatch(path.name)
        if match is None:
            raise ValueError(
                "Directive PDF filename must start with an eight-digit ID and "
                f"end with -v<number>.pdf: {path.name}"
            )
        content = path.read_bytes()
        if not content.startswith(b"%PDF"):
            raise ValueError(f"Directive source is not a PDF: {path.name}")
        documents.append(
            SourceDocument(
                path=path,
                directive_id_hint=match.group("directive_id"),
                version_hint=match.group("version"),
                source_hash=hashlib.sha256(content).hexdigest(),
                content=content,
            )
        )
    if not documents:
        raise ValueError(
            f"No directive PDFs found under {source_directory}"
        )
    identities = [
        (item.directive_id_hint, Decimal(item.version_hint))
        for item in documents
    ]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate directive ID/version filenames found")
    return documents
