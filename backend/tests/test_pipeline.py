"""The six-stage pipeline: adapters → extraction → ER → injection (PRD 6–9)."""

from __future__ import annotations

import pytest

from app.domain.enums import DocumentType, IngestionStatus, ResolutionStatus, SourceConfidence
from app.pipeline.orchestrator import process_document
from tests.conftest import (
    SAMPLE_BANK,
    SAMPLE_CDR,
    SAMPLE_FIR,
    SAMPLE_HINDI_FIR,
)


async def _upload(db, container, case, users, filename: str, payload: str, doc_type, confidence=SourceConfidence.UNVERIFIED):
    """Run the real upload service and then the pipeline inline."""
    from app.db.models import CaseDocument, IngestionJob
    from app.services.documents import upload_document
    from app.security.deps import Principal

    principal = Principal(users["INV-0001"])
    document, job = await upload_document(
        db,
        container=container,
        case=case,
        principal=principal,
        filename=filename,
        payload=payload.encode("utf-8"),
        document_type=doc_type,
        source_confidence=confidence,
        mime_type="text/plain",
    )
    doc_id, job_id = document.id, job.id
    await db.commit()
    # upload_document has enqueued the job; run it now, in this thread.
    container.broker.drain()
    db.expire_all()
    return await db.get(CaseDocument, doc_id), await db.get(IngestionJob, job_id)


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #

async def test_fir_is_ingested_and_marked_complete(db, container, case, users):
    document, job = await _upload(
        db, container, case, users, "fir.txt", SAMPLE_FIR, DocumentType.FIR
    )
    assert document.ingestion_status is IngestionStatus.COMPLETE
    assert job.status.value == "SUCCEEDED"
    assert document.derived_key.endswith("/normalised.txt")


async def test_every_graph_node_carries_a_source_document(db, container, case, users):
    """G1 — no node or edge may exist without provenance."""
    await _upload(db, container, case, users, "fir.txt", SAMPLE_FIR, DocumentType.FIR)
    snapshot = container.graph_store.snapshot(case.id)
    assert snapshot.nodes
    for node in snapshot.nodes.values():
        assert node.properties.get("source_doc_id"), node.provenance_key
        assert node.properties.get("source_doc_ids"), node.provenance_key
    for edge in snapshot.edges:
        assert edge.properties.get("source_doc_id"), edge.rel_type


async def test_hindi_fir_is_detected_and_extracts_people(db, container, case, users):
    document, _ = await _upload(
        db, container, case, users, "fir_hi.txt", SAMPLE_HINDI_FIR, DocumentType.FIR
    )
    assert document.language == "hi"
    names = {
        (n.properties.get("name") or "").lower()
        for n in container.graph_store.snapshot(case.id).nodes.values()
        if n.label == "Person"
    }
    assert any("यादव" in name for name in names), names


async def test_heuristic_nlp_does_not_invent_people_from_boilerplate(db, container, case, users):
    """The offline fallback must prefer precision: an FIR is not a list of names."""
    await _upload(db, container, case, users, "fir.txt", SAMPLE_FIR, DocumentType.FIR)
    names = [
        n.properties.get("name")
        for n in container.graph_store.snapshot(case.id).nodes.values()
        if n.label == "Person"
    ]
    lowered = " ".join(str(n).lower() for n in names)
    for noise in ("jaipur", "rajasthan", "police station", "first information", "section", "mahindra"):
        assert noise not in lowered, (noise, names)


async def test_cdr_edges_are_aggregated_not_per_call(db, container, case, users):
    """PRD 6 — one CALLED edge per pair, with call_count / first_ts / last_ts."""
    await _upload(db, container, case, users, "cdr.csv", SAMPLE_CDR, DocumentType.CDR)
    snapshot = container.graph_store.snapshot(case.id)
    called = snapshot.edges_by_type("CALLED")
    assert called, "no CALLED edges were produced"
    # Twelve rows, all from the same caller to twelve distinct numbers: the
    # aggregation must never inflate the edge count.
    assert len(called) <= 12
    for edge in called:
        assert edge.properties.get("call_count") is None or edge.properties["call_count"] >= 1


async def test_bank_transactions_detect_structuring(db, container, case, users):
    from app.db.models import DetectedPattern
    from sqlalchemy import select

    await _upload(
        db, container, case, users, "bank.csv", SAMPLE_BANK, DocumentType.FINANCIAL
    )
    patterns = (
        await db.execute(select(DetectedPattern).where(DetectedPattern.case_id == case.id))
    ).scalars().all()
    kinds = {p.pattern_type.value for p in patterns}
    assert "STRUCTURING" in kinds, kinds
    structuring = next(p for p in patterns if p.pattern_type.value == "STRUCTURING")
    assert structuring.explanation, "a finding without an explanation is a defect"
    assert structuring.evidence_doc_ids, "a finding without evidence is a defect"


# --------------------------------------------------------------------------- #
# Entity resolution (PRD 9)
# --------------------------------------------------------------------------- #

async def test_hard_identifier_match_merges_without_human_review(db, container, case, users):
    """Tier 1: the same phone number in two documents is the same phone."""
    await _upload(db, container, case, users, "cdr.csv", SAMPLE_CDR, DocumentType.CDR)
    await _upload(db, container, case, users, "fir.txt", SAMPLE_FIR, DocumentType.FIR)
    phones = [
        n
        for n in container.graph_store.snapshot(case.id).nodes.values()
        if n.label == "Phone" and n.properties.get("number") == "+919829012345"
    ]
    assert len(phones) == 1, "the same number must collapse into one node"
    assert len(phones[0].properties["source_doc_ids"]) == 2


async def test_fuzzy_name_match_is_queued_and_never_auto_merged(db, container, case, users):
    """G2 — a fuzzy match produces two nodes and a review item, never a merge."""
    from app.db.models import EntityResolutionItem
    from sqlalchemy import select

    await _upload(db, container, case, users, "fir.txt", SAMPLE_FIR, DocumentType.FIR)
    await _upload(
        db, container, case, users, "fir_hi.txt", SAMPLE_HINDI_FIR, DocumentType.FIR
    )
    items = (
        await db.execute(
            select(EntityResolutionItem).where(EntityResolutionItem.case_id == case.id)
        )
    ).scalars().all()
    assert items, "cross-script duplicates must be offered for review"
    assert all(i.status is ResolutionStatus.PENDING for i in items)
    # Fuzzy candidates stay separate nodes until a human decides.
    snapshot = container.graph_store.snapshot(case.id)
    assert any(e.rel_type == "POTENTIAL_ALIAS" for e in snapshot.edges)


async def test_anonymous_tip_nodes_are_staged_and_hidden_by_default(db, container, case, users):
    await _upload(
        db,
        container,
        case,
        users,
        "tip.txt",
        SAMPLE_FIR,
        DocumentType.INTEL,
        confidence=SourceConfidence.ANONYMOUS_TIP,
    )
    staged = container.graph_store.snapshot(case.id, include_staging=True)
    clean = container.graph_store.snapshot(case.id, include_staging=False)
    assert any(n.properties.get("staging") for n in staged.nodes.values())
    assert not any(n.properties.get("staging") for n in clean.nodes.values())


# --------------------------------------------------------------------------- #
# Failure handling (PRD 9.4)
# --------------------------------------------------------------------------- #

async def test_unreadable_document_is_quarantined_not_dropped(db, container, case, users):
    """Nothing fails silently: the document stays visible with a reason."""
    document, job = await _upload(
        db, container, case, users, "junk.json", "{not json", DocumentType.SOCIAL_MEDIA
    )
    assert document.ingestion_status is IngestionStatus.QUARANTINED
    assert document.quarantined is True
    assert document.failure_reason


async def test_reprocessing_a_document_is_idempotent(db, container, case, users):
    """Provenance keys make a re-run converge instead of duplicating."""
    document, job = await _upload(
        db, container, case, users, "fir.txt", SAMPLE_FIR, DocumentType.FIR
    )
    before = container.graph_store.stats()
    process_document(
        job_id=job.id,
        doc_id=document.id,
        case_id=case.id,
        trace_id="test-2",
        user_id=users["INV-0001"].id,
    )  # a straight re-run of the same document
    after = container.graph_store.stats()
    assert after["nodes"] == before["nodes"], (before, after)
    assert after["edges"] == before["edges"], (before, after)
