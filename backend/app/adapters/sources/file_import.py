"""File-import source adapter — used by the upload_document service."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from app.domain.enums import DocumentType, SourceConfidence

from .protocol import SourceAdapter, SourceRecord
from .registry import register_source_adapter

_EXT_TO_TYPE = {
    ".txt":  (DocumentType.FIR,          "text/plain"),
    ".csv":  (DocumentType.CDR,          "text/csv"),
    ".pdf":  (DocumentType.FIR,          "application/pdf"),
    ".docx": (DocumentType.FIR,          "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".xlsx": (DocumentType.FINANCIAL,    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".json": (DocumentType.FIR,          "application/json"),
}


def _guess(path: Path, declared_type: DocumentType | None = None):
    if declared_type:
        return declared_type
    ext = path.suffix.lower()
    return _EXT_TO_TYPE.get(ext, (DocumentType.FIR, "application/octet-stream"))[0]


def _guess_ct(path: Path) -> str:
    return _EXT_TO_TYPE.get(path.suffix.lower(), (DocumentType.FIR, "application/octet-stream"))[1]


@dataclass
class FileImportAdapter(SourceAdapter):
    """Adapter that yields a single SourceRecord from a file on disk.

    Used by upload_document when the file is already persisted to the object
    store or when an admin triggers a bulk filesystem import.
    """

    name: str = "file"
    source_environment: str = "user_upload"
    paths: list[Path] = field(default_factory=list)
    case_number: str | None = None
    declared_type: DocumentType | None = None
    declared_language: str = "en"

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_environment": self.source_environment,
            "paths": [str(p) for p in self.paths],
        }

    def iter_records(self, **options: Any) -> Iterator[SourceRecord]:
        case_num = options.get("case_number", self.case_number)
        if not case_num:
            raise ValueError("FileImportAdapter requires a case_number")
        paths = options.get("paths", self.paths)
        for p in paths:
            path = Path(p)
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()[:12]
            doctype = _guess(path, self.declared_type)
            is_text = path.suffix.lower() in {".txt", ".csv", ".json"}
            content: str | bytes = raw.decode("utf-8", errors="replace") if is_text else raw
            yield SourceRecord(
                external_id=f"file:{digest}",
                case_number=case_num,
                document_type=doctype,
                filename=path.name,
                content_type=_guess_ct(path),
                content=content,
                source_environment="user_upload",
                source_confidence=SourceConfidence.UNVERIFIED,
                language=options.get("language", self.declared_language),
                metadata={"sha256_prefix": digest},
            )


register_source_adapter("file", FileImportAdapter)


@dataclass
class FutureGovernmentAdapter(SourceAdapter):
    """Adapter boundary reserved for future authorised government/police feeds.

    CrimeLink does **not** ship a live CCNS/CCTNS adapter. This placeholder
    documents where an authorised integration would attach, and refuses to
    yield records unless an explicit opt-in flag is provided in a future
    release.
    """

    name: str = "future_government"
    source_environment: str = "government_db"

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_environment": self.source_environment,
            "implemented": False,
            "note": (
                "CrimeLink does not connect to live CCNS/CCTNS/police databases. "
                "This adapter is a reserved integration boundary for a future "
                "authorised government feed."
            ),
        }

    def iter_records(self, **options: Any) -> Iterator[SourceRecord]:
        raise NotImplementedError(
            "FutureGovernmentAdapter is a reserved integration boundary; no "
            "live government database feed is implemented."
        )


register_source_adapter("future_government", FutureGovernmentAdapter)


@dataclass
class DatabaseAdapter(SourceAdapter):
    """Adapter boundary for future authorised relational database feeds."""

    name: str = "database"
    source_environment: str = "external_db"

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_environment": self.source_environment,
            "implemented": False,
            "note": "Placeholder for future authorised relational database feeds.",
        }

    def iter_records(self, **options: Any) -> Iterator[SourceRecord]:
        raise NotImplementedError(
            "DatabaseAdapter is a reserved integration boundary; no external "
            "database feed is implemented."
        )


register_source_adapter("database", DatabaseAdapter)
