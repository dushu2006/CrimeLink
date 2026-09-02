"""Source adapter protocol.

A ``SourceAdapter`` yields ``SourceRecord`` objects — a uniform shape for
documents (FIRs, CDRs, bank statements, surveillance logs, intel, criminal
histories, social-media exports, synthetic corpus items, future authorised
government feeds).  The six-stage ingestion pipeline consumes these records
without caring which adapter produced them.

This abstraction is intentionally narrow: adapters exist only to turn external
bytes/rows into ``SourceRecord`` objects.  They do not write to the database
and they do not write to the graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol

from app.domain.enums import DocumentType, SourceConfidence


@dataclass(slots=True)
class SourceRecord:
    """A normalised document about to enter the six-stage ingestion pipeline.

    ``content`` is the raw text/bytes/csv payload that the appropriate
    document adapter (``app.pipeline.adapters.*``) knows how to parse.
    ``source_environment`` is always set so that synthetic data can never be
    confused with production data — even if a test operator loads both into
    the same instance, downstream provenance checks and UI labelling keep them
    separate.
    """

    external_id: str
    case_number: str
    document_type: DocumentType
    filename: str
    content_type: str                     # e.g. "text/plain", "text/csv"
    content: str | bytes
    source_environment: str = "unknown"   # "synthetic" | "user_upload" | "external_db" | ...
    source_confidence: SourceConfidence = SourceConfidence.UNVERIFIED
    language: str = "en"
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_synthetic(self) -> bool:
        return self.source_environment.lower() == "synthetic"


class SourceAdapter(Protocol):
    """Interface every data-source adapter implements."""

    name: str
    source_environment: str

    def iter_records(self, **options: Any) -> Iterator[SourceRecord]:
        """Yield records in a stable order suitable for ingestion."""
        ...

    def describe(self) -> dict[str, Any]:
        """Return a metadata dict for admin/diagnostic display."""
        ...
