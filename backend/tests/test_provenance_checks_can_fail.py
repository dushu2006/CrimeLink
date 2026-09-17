"""Provenance checks must be capable of failing.

The demo corpus is clean, so every one of its 360 documents passes all four
checks — which is exactly the situation in which a decorative ``✓`` is
indistinguishable from a computed one.  These tests drive the same
``provenance_payload`` the API serves with documents that are genuinely broken
and assert the ticks go away.  A check that cannot fail is not a check.

Each case maps to one of the four published semantics:

    source_verified        the record is VERIFIED and COMPLETE
    record_available       the bytes were actually read from storage
    traceable_to_original  a source reference or dataset file points at it
    hash_matches           the recorded hash was re-computed from the bytes
"""

from __future__ import annotations

import io

import pytest
from sqlalchemy import select

from app.datasets import registry
from app.db.base import new_uuid
from app.db.models import Case, CaseDocument, DatasetFile
from app.db.session import async_session
from app.domain.enums import CaseStatus, DocumentType, IngestionStatus, SourceConfidence
from app.domain.provenance import content_hash
from app.services.documents import provenance_payload

JURISDICTION = "RJ-JAIPUR"


def _pdf_bytes(title: str) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    doc = canvas.Canvas(buf, pagesize=letter)
    doc.setFont("Helvetica-Bold", 14)
    doc.drawString(72, 740, title)
    doc.showPage()
    doc.save()
    return buf.getvalue()


@pytest.fixture()
async def world(container, workspace, users):
    """One dataset holding a correctly stored PDF plus its case."""
    async with async_session() as session:
        ds = await registry.create_dataset(
            session, name="Provenance negative paths", version="1", source_kind="builtin"
        )
        await registry.activate(session, ds)
        case = Case(
            id=new_uuid(),
            case_number="PRV-8001",
            title="Provenance negative paths",
            jurisdiction_id=JURISDICTION,
            dataset_id=ds.id,
            status=CaseStatus.OPEN,
        )
        session.add(case)
        await session.flush()

        key = "evidence/PRV-8001/PRV-8001_FIR_01.pdf"
        payload = _pdf_bytes("FIR 1/2026 — PRV-8001")
        container.object_store.put(
            workspace.minio_bucket_documents, key, payload, content_type="application/pdf"
        )
        doc = CaseDocument(
            id="doc-prv-8001-01",
            case_id=case.id,
            dataset_id=ds.id,
            document_type=DocumentType.FIR,
            filename="PRV-8001_FIR_01.pdf",
            storage_key=key,
            # The real hash of the real bytes: this is what makes the
            # hash_matches check meaningful in the intact case.
            content_hash=content_hash(payload),
            size_bytes=len(payload),
            mime_type="application/pdf",
            source_metadata={"relative_path": key},
            # Explicitly verified and complete: without this the document
            # defaults to UNVERIFIED/PENDING and the negative tests below
            # would pass vacuously, asserting a failure that was never caused.
            source_confidence=SourceConfidence.VERIFIED,
            ingestion_status=IngestionStatus.COMPLETE,
        )
        session.add(doc)
        await session.flush()
        await session.commit()
        ctx = {"dataset_id": ds.id, "case_id": case.id, "key": key, "doc_id": doc.id,
               "payload_len": len(payload)}

    yield ctx

    async with async_session() as session:
        await registry.purge_dataset_data(session, ctx["dataset_id"])
        await session.commit()



def _remove_object(store, bucket: str, key: str) -> None:
    """Delete an object from the store under test.

    The adapter interface has no ``delete`` — nothing in the product removes
    evidence — so the test removes the file through the store's own path
    resolution, which is what a lost or corrupted object looks like from the
    application's point of view.
    """
    path = store._path(bucket, key)
    if path.exists():
        path.unlink()


async def _payload(container, doc_id: str) -> dict:
    async with async_session() as session:
        doc = await session.get(CaseDocument, doc_id)
        assert doc is not None
        return await provenance_payload(session, container, doc)


# ---------------------------------------------------------------------------
# The intact record passes — and says why
# ---------------------------------------------------------------------------


async def test_intact_document_passes_with_real_reasons(container, world) -> None:
    payload = await _payload(container, world["doc_id"])
    checks = payload["checks"]
    assert checks["record_available"]["ok"] is True
    assert "Bytes read from object storage" in checks["record_available"]["detail"]
    assert checks["hash_matches"]["ok"] is True
    assert "re-computed from the stored bytes" in checks["hash_matches"]["detail"]
    assert payload["file"]["available"] is True
    assert payload["file"]["size_bytes"] == world["payload_len"]
    assert checks["source_verified"]["ok"] is True, (
        "the fixture document is VERIFIED/COMPLETE and must report as such"
    )


# ---------------------------------------------------------------------------
# record_available: the bytes are gone
# ---------------------------------------------------------------------------


async def test_missing_bytes_fail_record_available(container, workspace, world) -> None:
    _remove_object(container.object_store, workspace.minio_bucket_documents, world["key"])

    payload = await _payload(container, world["doc_id"])
    checks = payload["checks"]

    assert checks["record_available"]["ok"] is False, (
        "a document whose bytes cannot be read must not report 'record available'"
    )
    assert checks["hash_matches"]["ok"] is not True, (
        "the hash cannot match bytes that were never read"
    )
    assert payload["file"]["available"] is False


# ---------------------------------------------------------------------------
# hash_matches: the stored bytes no longer match the recorded hash
# ---------------------------------------------------------------------------


async def test_tampered_bytes_fail_hash_matches(container, workspace, world) -> None:
    async with async_session() as session:
        doc = await session.get(CaseDocument, world["doc_id"])
        doc.content_hash = "f" * 64  # a recorded hash that nothing can match
        await session.commit()

    payload = await _payload(container, world["doc_id"])
    checks = payload["checks"]

    assert checks["record_available"]["ok"] is True, "the bytes are still readable"
    assert checks["hash_matches"]["ok"] is False, (
        "a mismatched hash must not be reported as matching"
    )


# ---------------------------------------------------------------------------
# source_verified: an unverified source is not "source verified"
# ---------------------------------------------------------------------------


async def test_unverified_source_fails_source_verified(container, world) -> None:
    async with async_session() as session:
        doc = await session.get(CaseDocument, world["doc_id"])
        doc.source_confidence = SourceConfidence.SYNTHETIC
        await session.commit()

    payload = await _payload(container, world["doc_id"])
    checks = payload["checks"]

    assert checks["source_verified"]["ok"] is False
    assert "source_confidence=SYNTHETIC" in checks["source_verified"]["detail"]


async def test_incomplete_ingestion_fails_source_verified(container, world) -> None:
    async with async_session() as session:
        doc = await session.get(CaseDocument, world["doc_id"])
        doc.ingestion_status = IngestionStatus.PROCESSING
        await session.commit()

    payload = await _payload(container, world["doc_id"])
    assert payload["checks"]["source_verified"]["ok"] is False
    assert "ingestion_status=PROCESSING" in payload["checks"]["source_verified"]["detail"]


# ---------------------------------------------------------------------------
# traceable_to_original: no source reference and no dataset file
# ---------------------------------------------------------------------------


async def test_untraceable_document_fails_traceability(container, world) -> None:
    async with async_session() as session:
        rows = (
            await session.execute(
                select(DatasetFile).where(DatasetFile.dataset_id == world["dataset_id"])
            )
        ).scalars().all()
        for row in rows:
            await session.delete(row)
        doc = await session.get(CaseDocument, world["doc_id"])
        # Drop the metadata that recovers the relative path.
        doc.source_metadata = {}
        await session.commit()

    payload = await _payload(container, world["doc_id"])
    checks = payload["checks"]

    assert checks["traceable_to_original"]["ok"] is False, (
        "with no source reference and no dataset file row there is nothing to trace"
    )
    assert "No source reference or dataset file row" in checks["traceable_to_original"]["detail"]


# ---------------------------------------------------------------------------
# The four checks are independent: one failure must not blank the others
# ---------------------------------------------------------------------------


async def test_one_failed_check_does_not_blank_the_others(container, workspace, world) -> None:
    _remove_object(container.object_store, workspace.minio_bucket_documents, world["key"])

    payload = await _payload(container, world["doc_id"])
    checks = payload["checks"]

    assert set(checks) == {
        "source_verified",
        "record_available",
        "traceable_to_original",
        "hash_matches",
    }
    for name, check in checks.items():
        assert set(check) == {"ok", "detail"}, f"{name} must report a computed ok and a detail"
    # The record itself is still verified and still traceable — only the bytes
    # went missing, and the panel must say exactly that much.
    assert checks["source_verified"]["ok"] is True
    assert checks["record_available"]["ok"] is False
