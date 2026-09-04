"""Persisting and resolving exact source coordinates.

Two responsibilities, deliberately kept together because they are two halves of
the same guarantee:

* :func:`persist_source_references` runs *during* ingestion, while the link
  between a derived row and its originating corpus row still exists.  Once the
  pipeline has finished, that link is gone forever — which is why provenance
  cannot be reconstructed after the fact and must be captured here.
* :func:`resolve_reference` reads it back for the source viewer.

Everything in this module addresses the corpus by *relative* path.  Absolute
paths are never stored and never accepted, so an evidence URL cannot be turned
into an arbitrary file read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import new_uuid
from app.db.models import SourceReference
from app.domain.models import NormalizedDocument, OriginRef
from app.logging import get_logger

log = get_logger("crimelink.provenance")

#: Maximum characters stored as an inline excerpt.  The excerpt exists so a
#: reference is still meaningful if the corpus file is later unavailable; it is
#: not a substitute for reading the file.
MAX_EXCERPT = 500


def source_type_for(origin_file: str) -> str:
    suffix = Path(origin_file).suffix.lower()
    return {
        ".csv": "csv",
        ".txt": "txt",
        ".md": "txt",
        ".json": "json",
        ".pdf": "pdf",
        ".docx": "docx",
    }.get(suffix, "txt")


def _row_from_origin(
    origin: OriginRef,
    *,
    doc_id: str,
    case_id: str,
    text_span: tuple[int, int] | None,
    excerpt: str | None,
) -> dict[str, Any]:
    return {
        "id": new_uuid(),
        "doc_id": doc_id,
        "case_id": case_id,
        "origin_file": origin.file,
        "source_type": source_type_for(origin.file),
        "record_id": origin.record_id,
        "row_number": origin.row,
        "field_names": list(origin.fields),
        "field_values": dict(origin.values),
        "page_number": None,
        "line_start": None,
        "line_end": None,
        "text_start": text_span[0] if text_span else None,
        "text_end": text_span[1] if text_span else None,
        "excerpt": (excerpt or "")[:MAX_EXCERPT] or None,
    }


def collect_references(
    document: NormalizedDocument,
    *,
    doc_id: str,
    case_id: str,
    document_origin: OriginRef | None = None,
    line_origins: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Derive the source references implied by a parsed document.

    Three shapes are handled, matching how the corpus actually reaches us:

    1. **Per-block origins** — structured rows (CDR, transactions, sightings,
       persons) carry an :class:`OriginRef` through the pipeline.
    2. **Line-range origins** — rendered free text (intel) maps line windows in
       the derived document back to rows of the origin CSV.
    3. **Document origin** — a verbatim file is its own origin.
    """
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    def add(row: dict[str, Any]) -> None:
        # Mirrors uq_source_references_position so a re-parse converges.
        key = (row["origin_file"], row["row_number"], row["text_start"])
        if key in seen:
            return
        seen.add(key)
        rows.append(row)

    for block in document.blocks:
        origin = getattr(block, "origin", None)
        if origin is None:
            continue
        add(
            _row_from_origin(
                origin,
                doc_id=doc_id,
                case_id=case_id,
                text_span=block.span,
                excerpt=block.text,
            )
        )

    for entry in line_origins or []:
        raw = entry.get("origin")
        if not raw:
            continue
        origin = OriginRef.from_dict(raw)
        row = _row_from_origin(
            origin, doc_id=doc_id, case_id=case_id, text_span=None, excerpt=None
        )
        row["line_start"] = entry.get("line_start")
        row["line_end"] = entry.get("line_end")
        add(row)

    if document_origin is not None:
        add(
            _row_from_origin(
                document_origin,
                doc_id=doc_id,
                case_id=case_id,
                text_span=None,
                excerpt=None,
            )
        )
    return rows


def persist_source_references(session: Session, rows: list[dict[str, Any]]) -> int:
    """Idempotently upsert source references for one document.

    Re-processing a document must converge on the same set rather than
    accumulate duplicates, so existing positions are updated in place.
    """
    if not rows:
        return 0
    doc_id = rows[0]["doc_id"]
    existing = {
        (r.origin_file, r.row_number, r.text_start): r
        for r in session.execute(
            select(SourceReference).where(SourceReference.doc_id == doc_id)
        ).scalars()
    }
    written = 0
    for row in rows:
        key = (row["origin_file"], row["row_number"], row["text_start"])
        current = existing.get(key)
        if current is None:
            session.add(SourceReference(**row))
            written += 1
            continue
        for field in (
            "record_id", "field_names", "field_values", "page_number",
            "line_start", "line_end", "text_end", "excerpt", "source_type",
        ):
            setattr(current, field, row[field])
    return written
