"""Evidence must resolve to real bytes, and say so truthfully.

The chain under test is

    CASE -> EVIDENCE -> SOURCE RECORD -> ORIGINAL FILE -> DURABLE OBJECT
         -> ACTUAL BYTES -> SHA-256

and the failure this pins down is the one where the chain *looks* intact: the
metadata resolves, the source reference exists, the panel says "traceable to
original" — and the object itself was never read, so availability and integrity
were being asserted rather than measured.

Every assertion here is about a fact that was computed from the object store in
front of the test, never about a row existing.
"""

from __future__ import annotations

import io

import pytest
from sqlalchemy import select

from app.datasets import registry
from app.datasets.storage import dataset_object_key, legacy_object_key
from app.db.base import new_uuid
from app.db.models import Case, CaseDocument, DatasetFile, InvestigationFinding, SourceReference
from app.db.session import async_session
from app.domain.enums import CaseStatus, DocumentType, IngestionStatus, SourceConfidence
from app.domain.provenance import content_hash
from app.errors import DependencyUnavailableError, NotFoundError
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
async def evidence(container, workspace, users):
    """One dataset, one case, one evidence object stored under a dataset key."""
    async with async_session() as session:
        ds = await registry.create_dataset(
            session, name="Evidence chain", version="1", source_kind="upload"
        )
        await registry.activate(session, ds)
        case = Case(
            id=new_uuid(),
            case_number="EVD-9001",
            title="Evidence chain",
            jurisdiction_id=JURISDICTION,
            dataset_id=ds.id,
            dataset_case_key="EVD-9001",
            status=CaseStatus.OPEN,
        )
        session.add(case)
        await session.flush()

        relative_path = "cases/EVD-9001/case_summary.txt"
        key = dataset_object_key(ds.id, relative_path)
        payload = b"Case summary for EVD-9001. Subject P001 was interviewed.\n"
        container.object_store.put(
            workspace.minio_bucket_documents, key, payload, content_type="text/plain"
        )
        manifest = DatasetFile(
            id=new_uuid(),
            dataset_id=ds.id,
            relative_path=relative_path,
            filename="case_summary.txt",
            extension=".txt",
            media_type="text/plain",
            file_kind="text",
            size_bytes=len(payload),
            sha256=content_hash(payload),
            status="INGESTED",
        )
        doc = CaseDocument(
            id="doc-evd-9001-01",
            case_id=case.id,
            dataset_id=ds.id,
            document_type=DocumentType.CASE_DIARY,
            filename="case_summary.txt",
            storage_key=key,
            content_hash=content_hash(payload),
            size_bytes=len(payload),
            mime_type="text/plain",
            source_metadata={"relative_path": relative_path},
            source_confidence=SourceConfidence.SYNTHETIC,
            ingestion_status=IngestionStatus.COMPLETE,
        )
        session.add(doc)
        session.add(manifest)
        session.add(
            SourceReference(
                id=new_uuid(),
                doc_id=doc.id,
                case_id=case.id,
                origin_file=relative_path,
                source_type="txt",
                row_number=1,
                excerpt="Case summary for EVD-9001.",
            )
        )
        await session.flush()
        manifest.doc_id = doc.id
        await session.commit()
        ctx = {
            "dataset_id": ds.id,
            "case_id": case.id,
            "doc_id": doc.id,
            "key": key,
            "relative_path": relative_path,
            "payload": payload,
        }

    yield ctx

    async with async_session() as session:
        await registry.purge_dataset_data(session, ctx["dataset_id"])
        await session.commit()


async def _payload(container, doc_id: str) -> dict:
    async with async_session() as session:
        doc = await session.get(CaseDocument, doc_id)
        return await provenance_payload(session, container, doc)


def _overwrite(store, bucket: str, key: str, payload: bytes) -> None:
    path = store._path(bucket, key)
    path.write_bytes(payload)


def _remove(store, bucket: str, key: str) -> None:
    path = store._path(bucket, key)
    if path.exists():
        path.unlink()


# --------------------------------------------------------------------------- #
# The chain resolves, end to end, to real bytes
# --------------------------------------------------------------------------- #


async def test_the_whole_chain_resolves_and_the_bytes_are_read(container, evidence):
    payload = await _payload(container, evidence["doc_id"])
    checks = payload["checks"]

    assert payload["case"]["case_number"] == "EVD-9001"
    assert payload["dataset_file"]["relative_path"] == evidence["relative_path"]
    assert payload["source_references"][0]["origin_file"] == evidence["relative_path"]

    # Availability means the bytes were read, and the digest was recomputed
    # from them -- both facts, not one.
    assert payload["file"]["storage_status"] == "available"
    assert payload["file"]["available"] is True
    assert payload["file"]["size_bytes"] == len(evidence["payload"])
    assert payload["file"]["computed_hash"] == content_hash(evidence["payload"])
    assert checks["record_available"]["ok"] is True
    assert checks["hash_matches"]["ok"] is True
    assert checks["hash_matches"]["state"] == "match"
    assert checks["traceable_to_original"]["ok"] is True

    steps = {step["step"]: step["resolved"] for step in payload["chain"]}
    assert steps["CASE"] is True
    assert steps["EVIDENCE"] is True
    assert steps["SOURCE_RECORD"] is True
    assert steps["ORIGINAL_FILE"] is True


async def test_object_keys_are_dataset_scoped(container, evidence):
    """Two datasets holding the same relative path cannot collide."""
    other = "another-dataset-id"
    assert dataset_object_key(evidence["dataset_id"], evidence["relative_path"]) == evidence["key"]
    assert evidence["key"].startswith(f"{evidence['dataset_id']}/")
    assert dataset_object_key(other, evidence["relative_path"]) != evidence["key"]
    # The unscoped legacy key is a different address, so an old reader cannot
    # accidentally resolve this dataset's object.
    assert legacy_object_key(evidence["relative_path"]) != evidence["key"]


async def test_a_manifest_row_from_another_dataset_is_not_this_provenance(
    container, evidence
):
    """Cross-dataset provenance must not resolve, even with a matching doc id."""
    async with async_session() as session:
        other = await registry.create_dataset(
            session, name="Unrelated dataset", version="1", source_kind="upload"
        )
        session.add(
            DatasetFile(
                id=new_uuid(),
                dataset_id=other.id,
                # The same doc id, in a different dataset: a coincidence the
                # chain must not treat as a link.
                doc_id=evidence["doc_id"],
                relative_path="elsewhere/forged_summary.txt",
                filename="forged_summary.txt",
                extension=".txt",
                media_type="text/plain",
                file_kind="text",
                size_bytes=10,
                sha256="0" * 64,
                status="INGESTED",
            )
        )
        await session.commit()
        other_id = other.id

    try:
        payload = await _payload(container, evidence["doc_id"])
        assert payload["dataset_file"]["relative_path"] == evidence["relative_path"]
    finally:
        async with async_session() as session:
            await registry.purge_dataset_data(session, other_id)
            await session.commit()


# --------------------------------------------------------------------------- #
# Integrity is measured, never assumed
# --------------------------------------------------------------------------- #


async def test_a_hash_mismatch_is_reported_as_a_mismatch(container, workspace, evidence):
    _overwrite(
        container.object_store,
        workspace.minio_bucket_documents,
        evidence["key"],
        b"tampered bytes that are a different length entirely\n",
    )
    payload = await _payload(container, evidence["doc_id"])

    assert payload["file"]["storage_status"] == "available"
    assert payload["file"]["available"] is True, "the bytes were read, so they are available"
    assert payload["checks"]["hash_matches"]["ok"] is False
    assert payload["checks"]["hash_matches"]["state"] == "mismatch"
    assert payload["checks"]["record_available"]["ok"] is True


async def test_an_object_that_is_gone_is_reported_as_gone(container, workspace, evidence):
    _remove(container.object_store, workspace.minio_bucket_documents, evidence["key"])
    payload = await _payload(container, evidence["doc_id"])

    assert payload["file"]["storage_status"] == "missing"
    assert payload["file"]["available"] is False
    assert payload["checks"]["record_available"]["ok"] is False
    # Provenance still identifies the original; the bytes are simply not there.
    assert payload["checks"]["traceable_to_original"]["ok"] is True
    assert payload["checks"]["hash_matches"]["ok"] is None
    assert payload["checks"]["hash_matches"]["state"] == "unproven"


async def test_an_unreachable_store_proves_nothing_about_the_record(
    container, workspace, evidence, monkeypatch
):
    class Unreachable:
        backend_name = "unreachable"

        def get(self, *_args, **_kwargs):
            raise DependencyUnavailableError("CRIMELINK_MINIO_ENDPOINT is unreachable")

    monkeypatch.setattr(type(container), "object_store", property(lambda _self: Unreachable()))
    payload = await _payload(container, evidence["doc_id"])

    assert payload["file"]["storage_status"] == "unavailable"
    assert payload["file"]["available"] is None, "unknown, not 'missing'"
    assert payload["checks"]["record_available"]["ok"] is None
    assert payload["checks"]["record_available"]["state"] == "unavailable"
    assert payload["checks"]["hash_matches"]["ok"] is None
    # The outage did not make the document untraceable.
    assert payload["checks"]["traceable_to_original"]["ok"] is True


async def test_a_document_with_no_recorded_hash_does_not_claim_a_match(
    container, workspace, evidence
):
    async with async_session() as session:
        doc = await session.get(CaseDocument, evidence["doc_id"])
        doc.content_hash = ""
        await session.commit()

    payload = await _payload(container, evidence["doc_id"])
    assert payload["file"]["storage_status"] == "available"
    assert payload["file"]["hash_matches"] is None
    assert payload["checks"]["hash_matches"]["ok"] is None
    assert payload["checks"]["hash_matches"]["state"] == "unproven"
    assert "no SHA-256 was recorded" in payload["file"]["detail"]


async def test_a_size_mismatch_is_reported_even_when_the_hash_is_absent(
    container, workspace, evidence
):
    async with async_session() as session:
        doc = await session.get(CaseDocument, evidence["doc_id"])
        doc.content_hash = ""
        await session.commit()
    _overwrite(
        container.object_store,
        workspace.minio_bucket_documents,
        evidence["key"],
        b"truncated\n",
    )

    payload = await _payload(container, evidence["doc_id"])
    assert payload["file"]["size_matches"] is False
    assert payload["file"]["expected_size_bytes"] == len(evidence["payload"])
    assert "does not match the recorded" in payload["file"]["detail"]


# --------------------------------------------------------------------------- #
# Source classification is not storage verification
# --------------------------------------------------------------------------- #


async def test_a_synthetic_dataset_stays_synthetic_while_its_object_verifies(
    container, evidence
):
    """Technical provenance and source classification are different facts."""
    payload = await _payload(container, evidence["doc_id"])

    assert payload["document"]["source_confidence"] == "SYNTHETIC"
    assert payload["document"]["ingestion_status"] == "COMPLETE"
    # The classification is not upgraded to make the panel green...
    assert payload["checks"]["source_verified"]["ok"] is False
    assert "SYNTHETIC" in payload["checks"]["source_verified"]["detail"]
    # ...and the storage verification is still computed, and still passes.
    assert payload["checks"]["record_available"]["ok"] is True
    assert payload["checks"]["hash_matches"]["ok"] is True


# --------------------------------------------------------------------------- #
# Citations are real or absent; never invented
# --------------------------------------------------------------------------- #


async def test_no_person_or_finding_citation_is_invented(container, evidence):
    payload = await _payload(container, evidence["doc_id"])
    assert payload["people"] == []
    assert payload["findings"] == []
    assert payload["chain"][-1]["step"] == "FINDING"
    assert payload["chain"][-1]["resolved"] is False


async def test_a_real_finding_citation_is_discovered(container, evidence):
    async with async_session() as session:
        session.add(
            InvestigationFinding(
                id=new_uuid(),
                case_id=evidence["case_id"],
                title="Subject identified from the case summary",
                finding_type="LINK",
                status="CONFIRMED",
                confidence=0.9,
                narrative="The case summary names the subject.",
                evidence=[{"doc_id": evidence["doc_id"]}],
            )
        )
        await session.commit()

    payload = await _payload(container, evidence["doc_id"])
    assert len(payload["findings"]) == 1
    assert payload["findings"][0]["title"] == "Subject identified from the case summary"
    assert payload["chain"][-1]["resolved"] is True
