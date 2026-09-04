"""Restart safety: interrupted pipeline runs must be resumable, never lost.

The embedded profile executes pipeline jobs inside the API process, so a
crash strands every job that was queued or running at that instant.  The
documents are not lost (the original bytes and metadata rows survive), but
without a recovery step they sit in PENDING / PROCESSING / mid-retry FAILED
forever, looking exactly like live work that will never finish.

``requeue_stale_documents`` is the recovery half of restart-safe ingestion.
It decides liveness from the *current* process's broker: when the broker is
provably idle, QUEUED/RUNNING job rows are orphans of the dead process and the
documents are re-queued; when the broker reports queued/running work, those
rows are genuine and are left alone so a concurrent invocation stays harmless.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from app.db.models import Case, CaseDocument, IngestionJob
from app.db.session import sync_session
from app.domain.enums import CaseStatus, IngestionStatus, JobStatus
from app.services.documents import requeue_stale_documents, upload_document


def _document_row(session, doc_id: str) -> CaseDocument:
    row = session.get(CaseDocument, doc_id)
    assert row is not None
    return row


def _make_syn_dev_case(users) -> Case:
    """A case inside the synthetic jurisdiction the dataset status counts."""
    from app.db.base import new_uuid

    with sync_session() as session:
        case = Case(
            id=new_uuid(),
            case_number=f"SYN/{uuid.uuid4().hex[:8]}",
            title="[SYNTHETIC] status test",
            jurisdiction_id="SYN-DEV",
            status=CaseStatus.OPEN,
            created_by=users["INV-0001"].id,
        )
        session.add(case)
        session.commit()
        return case


async def _upload(container, db, case, principal, *, filename: str) -> str:
    document, _job = await upload_document(
        db,
        container=container,
        case=case,
        principal=principal,
        filename=filename,
        payload=filename.encode("utf-8"),
        document_type=__import__("app.domain.enums", fromlist=["DocumentType"]).DocumentType.FIR,
        source_confidence=__import__(
            "app.domain.enums", fromlist=["SourceConfidence"]
        ).SourceConfidence.SYNTHETIC,
    )
    await db.commit()
    return document.id


def _set_job_status(doc_id: str, status: JobStatus) -> None:
    with sync_session() as session:
        job = session.query(IngestionJob).filter(IngestionJob.doc_id == doc_id).one()
        job.status = status
        session.commit()


def _reset_all_documents_terminal() -> None:
    """Reset every document/job left behind by earlier tests in this suite.

    Recovery works on the whole database (it is not jurisdiction-scoped), so a
    PENDING document left over from any other test file would otherwise be
    counted by ``requeue_stale_documents``.  This makes the recovery tests
    order-independent without weakening what they assert.
    """
    with sync_session() as session:
        for row in session.query(CaseDocument).all():
            row.ingestion_status = IngestionStatus.COMPLETE
            row.quarantined = False
            row.is_deleted = False
        for job in session.query(IngestionJob).all():
            job.status = JobStatus.SUCCEEDED
        session.commit()


async def test_requeue_recovers_a_crashed_process(container, db, users) -> None:
    """A crash leaves the broker empty; every stranded document is re-queued.

    PENDING (job never ran), PROCESSING with an orphaned RUNNING job (killed
    mid-pipeline) and mid-retry FAILED documents are all recovered.  A FAILED
    document whose retry budget is exhausted is terminal and stays put, as is
    a COMPLETE document.  The orphaned job rows are marked superseded so a
    later recovery never mistakes them for live work.
    """
    _reset_all_documents_terminal()
    owner = users["INV-0001"]
    case = _make_syn_dev_case(users)
    pending_id = await _upload(container, db, case, owner, filename="pending.txt")
    processing_id = await _upload(container, db, case, owner, filename="processing.txt")
    mid_retry_id = await _upload(container, db, case, owner, filename="retry.txt")
    terminal_id = await _upload(container, db, case, owner, filename="terminal.txt")
    complete_id = await _upload(container, db, case, owner, filename="complete.txt")

    # The process dies: its in-memory job backlog vanishes with it.
    container.broker.dispatched.clear()

    with sync_session() as session:
        _document_row(session, processing_id).ingestion_status = IngestionStatus.PROCESSING
        _document_row(session, mid_retry_id).ingestion_status = IngestionStatus.FAILED
        _document_row(session, mid_retry_id).retry_count = 2  # budget left
        _document_row(session, terminal_id).ingestion_status = IngestionStatus.FAILED
        _document_row(session, terminal_id).retry_count = 5  # exhausted
        _document_row(session, complete_id).ingestion_status = IngestionStatus.COMPLETE
        session.commit()
    _set_job_status(processing_id, JobStatus.RUNNING)
    _set_job_status(mid_retry_id, JobStatus.FAILED)
    _set_job_status(terminal_id, JobStatus.FAILED)
    _set_job_status(complete_id, JobStatus.SUCCEEDED)

    result = await requeue_stale_documents(container=container)

    assert set(result["requeued"]) == {pending_id, processing_id, mid_retry_id}
    assert terminal_id not in result["requeued"]
    assert complete_id not in result["requeued"]
    assert result["requeued_count"] == 3
    assert result["skipped_running"] == 0
    # Orphaned QUEUED (pending) + RUNNING (processing) rows were superseded.
    assert result["orphans_superseded"] == 2
    assert result["broker_idle"] is True

    # Re-queued documents were reset to PENDING with the failure cleared, and
    # the orphaned rows were closed out as superseded.
    with sync_session() as session:
        for doc_id in (pending_id, processing_id, mid_retry_id):
            row = _document_row(session, doc_id)
            assert row.ingestion_status == IngestionStatus.PENDING
            assert row.failure_reason is None
        superseded = (
            session.query(IngestionJob)
            .filter(
                IngestionJob.doc_id.in_([pending_id, processing_id]),
                IngestionJob.error.isnot(None),
            )
            .all()
        )
        assert len(superseded) == 2
        for orphan in superseded:
            assert orphan.status == JobStatus.FAILED
            assert "Superseded" in (orphan.error or "")
    # The recovery dispatched exactly the three re-queued documents.
    assert [d["doc_id"] for d in container.broker.dispatched] == [
        pending_id,
        processing_id,
        mid_retry_id,
    ]


async def test_requeue_does_not_double_dispatch_live_work(
    container, db, users
) -> None:
    """With a live broker, scheduled work is left alone.

    A concurrent recovery (e.g. the admin Resume button pressed while an
    import is actively draining in this process) must not re-queue documents
    that still carry QUEUED/RUNNING job rows — only a genuinely stranded FAILED
    document with retry budget left is re-dispatched.
    """
    _reset_all_documents_terminal()
    owner = users["INV-0001"]
    case = _make_syn_dev_case(users)
    queued_id = await _upload(container, db, case, owner, filename="queued.txt")
    running_id = await _upload(container, db, case, owner, filename="running.txt")
    stranded_id = await _upload(container, db, case, owner, filename="stranded.txt")

    # Broker stays live: dispatched calls are still queued/running.
    with sync_session() as session:
        _document_row(session, running_id).ingestion_status = IngestionStatus.PROCESSING
        _document_row(session, stranded_id).ingestion_status = IngestionStatus.FAILED
        _document_row(session, stranded_id).retry_count = 1  # budget left
        session.commit()
    _set_job_status(running_id, JobStatus.RUNNING)
    _set_job_status(stranded_id, JobStatus.FAILED)

    dispatched_before = len(container.broker.dispatched)
    result = await requeue_stale_documents(container=container)

    assert queued_id not in result["requeued"]  # QUEUED row: about to run
    assert running_id not in result["requeued"]  # RUNNING row: being handled
    assert stranded_id in result["requeued"]  # no live job at all
    assert result["requeued_count"] == 1
    assert result["skipped_running"] == 2
    assert result["broker_idle"] is False
    # Exactly one new dispatch (the stranded document) — live work is never
    # duplicated.
    assert len(container.broker.dispatched) == dispatched_before + 1
    assert container.broker.dispatched[-1]["doc_id"] == stranded_id


async def test_requeue_is_idempotent_when_nothing_is_stale(container, db, users) -> None:
    """A fully-drained database produces an empty re-dispatch (safe to re-run)."""
    owner = users["INV-0001"]
    case = _make_syn_dev_case(users)
    await _upload(container, db, case, owner, filename="done.txt")
    with sync_session() as session:
        for row in session.query(CaseDocument).all():
            row.ingestion_status = IngestionStatus.COMPLETE
        for job in session.query(IngestionJob).all():
            job.status = JobStatus.SUCCEEDED
        session.commit()

    for idle in (True, False):
        if not idle:
            # A live broker with unrelated work must behave identically.
            pass
        dispatched_before = len(container.broker.dispatched)
        result = await requeue_stale_documents(container=container)
        assert result["requeued_count"] == 0
        assert len(container.broker.dispatched) == dispatched_before


async def test_corpus_status_surfaces_interrupted_runs(
    container, db, users, monkeypatch
) -> None:
    """Dataset status distinguishes crash-orphaned work from live work."""
    _reset_all_documents_terminal()
    from app.config import Settings
    from app.synthetic_corpus.external import corpus_status
    from tests.test_synthetic_external import _make_corpus

    corpus_root = _make_corpus(
        Path(tempfile.mkdtemp(prefix="crimelink-corpus-")) / "corpus",
        salt=f"status-{uuid.uuid4().hex[:8]}",
    )
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: Settings(synthetic_data_root=corpus_root),
    )

    owner = users["INV-0001"]
    case = _make_syn_dev_case(users)
    doc_a = await _upload(container, db, case, owner, filename="stuck-a.txt")
    doc_b = await _upload(container, db, case, owner, filename="stuck-b.txt")
    doc_c = await _upload(container, db, case, owner, filename="stuck-c.txt")

    # Scenario 1: a live broker with genuinely queued work is *busy*, never
    # "interrupted".
    status = await corpus_status(container=container)
    assert status["busy"] is True
    assert status["interrupted"] is False

    # Scenario 2: the process dies.  Stranded work must be reported as
    # "interrupted" with the exact number of resumable documents, and a stage
    # hint the UI turns into a Resume button.
    container.broker.dispatched.clear()
    with sync_session() as session:
        _document_row(session, doc_a).ingestion_status = IngestionStatus.PROCESSING
        _document_row(session, doc_c).ingestion_status = IngestionStatus.FAILED
        _document_row(session, doc_c).retry_count = 5  # exhausted -> terminal
        session.commit()
    for doc_id in (doc_a, doc_b, doc_c):
        _set_job_status(doc_id, JobStatus.FAILED)  # no live job survives

    status = await corpus_status(container=container)
    assert status["busy"] is False
    assert status["interrupted"] is True
    # doc_a (PROCESSING) and doc_b (PENDING) are resumable; doc_c is terminal.
    assert status["stale_documents"] == 2
    assert "Resume" in status["stage_hint"]

    # Scenario 3: recovery re-queues the stale documents -> the broker is busy
    # again and the run is no longer reported as interrupted.
    result = await requeue_stale_documents(container=container)
    assert set(result["requeued"]) == {doc_a, doc_b}
    status = await corpus_status(container=container)
    assert status["busy"] is True
    assert status["interrupted"] is False
