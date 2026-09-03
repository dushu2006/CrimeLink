"""The six-stage asynchronous pipeline (PRD 6 / 7 / 8 / 9 / 11.3).

    S1  Source adapters           → normalised document (original written first)
    S2  Deterministic extraction  → regex / gazetteer facts
    S3  NLP extraction            → names, aliases, person-to-person relations
    S4  Entity resolution         → hard / fuzzy / tombstone decisions
    S5  Graph injection           → idempotent, evidence-required writes
    S6  Pattern detection         → deterministic findings into the review queue

Every stage records its progress so the UI can show "Stage 3/6 — NLP extraction"
live, and every stage has a defined failure path:

* **deterministic bad input** (encrypted PDF, unknown CDR schema) → ``FAILED``
  immediately and quarantined, because retrying cannot help;
* **transient failure** (database hiccup, broker blip) → retried with backoff,
  up to five attempts, then quarantined;
* **worker crash** → the task is re-delivered and the provenance keys make the
  re-run converge rather than duplicate.

Nothing is allowed to fail silently: a document that vanished mid-pipeline is a
hole in an investigation that nobody would know to look for (PRD principle P4).
"""

from __future__ import annotations

import time
import traceback
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import audit_service
from app.config import Settings, get_settings
from app.container import get_container
from app.db.base import utcnow
from app.db.models import (
    Case,
    CaseDocument,
    DetectedPattern,
    DocumentStageEvent,
    EntityResolutionItem,
    IngestionJob,
)
from app.db.session import sync_session
from app.domain.enums import (
    AuditAction,
    CaseStatus,
    DocumentType,
    IngestionStatus,
    JobStatus,
    MatchBasis,
    PatternStatus,
    PatternType,
    ResolutionStatus,
    SourceConfidence,
)
from app.errors import PipelineError
from app.logging import bind_context, get_logger
from app.pipeline.adapters.protocol import DocumentMeta

log = get_logger("crimelink.pipeline")

STAGE_NAMES: dict[int, str] = {
    1: "Source adapters",
    2: "Deterministic extraction",
    3: "NLP extraction",
    4: "Entity resolution",
    5: "Graph injection",
    6: "Pattern detection",
}
TOTAL_STAGES = 6
MAX_RETRIES = 5


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def process_document(
    *, job_id: str, doc_id: str, case_id: str, trace_id: str, user_id: str
) -> None:
    """Run the full pipeline for one document.  Never raises."""
    container = get_container()
    settings = container.settings
    bind_context(trace_id=trace_id, case_id=case_id, user_id=user_id)
    started = time.time()

    _mark_job(job_id, status=JobStatus.RUNNING, stage=0, stage_name="Starting")
    try:
        _run(container, settings, job_id=job_id, doc_id=doc_id, case_id=case_id, user_id=user_id)
    except PipelineError as exc:
        # Deterministic failure: retrying cannot help, so quarantine now.
        log.warning("pipeline.deterministic_failure", doc_id=doc_id, reason=str(exc))
        _fail_document(
            doc_id,
            job_id,
            case_id,
            str(exc),
            quarantine=True,
        )
    except Exception as exc:  # noqa: BLE001 - the pipeline must never lose a document
        attempts = _increment_retry(doc_id)
        log.warning(
            "pipeline.transient_failure",
            doc_id=doc_id,
            attempt=attempts,
            error=str(exc),
            traceback=traceback.format_exc(limit=3),
        )
        if attempts >= MAX_RETRIES:
            _fail_document(
                doc_id,
                job_id,
                case_id,
                f"Processing failed after {attempts} attempts: {type(exc).__name__}",
                quarantine=True,
            )
            return
        _mark_document(doc_id, status=IngestionStatus.FAILED, failure_reason=str(exc))
        _mark_job(job_id, status=JobStatus.FAILED, stage=0, error=str(exc))
        # Short, capped backoff: five attempts should cost ~20 s, not minutes.
        time.sleep(min(2 ** (attempts - 1), 5))
        container.broker.dispatch_document_pipeline(
            job_id=job_id, doc_id=doc_id, case_id=case_id, trace_id=trace_id, user_id=user_id
        )
        return
    log.info("pipeline.document_complete", doc_id=doc_id, seconds=round(time.time() - started, 2))


def run_nightly_patterns(*, trace_id: str | None = None) -> None:
    """Scheduled whole-graph pass (PRD 11.3).

    Cheap event-triggered checks already ran at injection time; this pass finds
    what only the whole graph can show — structuring across rolling windows,
    community structure and network bridges.
    """
    from app.analytics.centrality import compute_centrality

    container = get_container()
    bind_context(trace_id=trace_id)
    with sync_session() as session:
        case_ids = [
            row[0]
            for row in session.execute(
                select(Case.id).where(Case.status.in_([CaseStatus.OPEN, CaseStatus.UNDER_REVIEW]))
            ).all()
        ]
    for case_id in case_ids:
        try:
            with sync_session() as session:
                tip_docs = [
                    row[0]
                    for row in session.execute(
                        select(CaseDocument.id).where(
                            CaseDocument.case_id == case_id,
                            CaseDocument.source_confidence == SourceConfidence.ANONYMOUS_TIP,
                        )
                    ).all()
                ]
            snapshot = container.graph_store.snapshot(case_id)
            centrality = compute_centrality(snapshot, container.settings)
            findings = container.pattern_engine.detect_scheduled(
                snapshot, centrality=centrality, excluded_doc_ids=set(tip_docs)
            )
            _persist_patterns(case_id, findings)
            log.info("pipeline.nightly_case_done", case_id=case_id, findings=len(findings))
        except Exception:  # noqa: BLE001 - one bad case must not stop the run
            log.exception("pipeline.nightly_case_failed", case_id=case_id)


def run_audit_anchor(*, trace_id: str | None = None) -> None:
    """Write the audit chain head into the separately-credentialed bucket."""
    container = get_container()
    if not container.settings.audit_anchor_enabled:
        return
    bind_context(trace_id=trace_id)
    try:
        with sync_session() as session:
            result = audit_service.anchor(
                session, container.object_store, container.settings.minio_bucket_audit_anchor
            )
            audit_service.append(
                session,
                action_type=AuditAction.CONFIG_CHANGE,
                target_resource="audit_chain_head",
                details={"anchor": result, "trace_id": trace_id},
            )
    except Exception:  # noqa: BLE001
        log.exception("pipeline.audit_anchor_failed")


# ---------------------------------------------------------------------------
# The pipeline itself
# ---------------------------------------------------------------------------


def _run(
    container,
    settings: Settings,
    *,
    job_id: str,
    doc_id: str,
    case_id: str,
    user_id: str,
) -> None:
    with sync_session() as session:
        document = session.get(CaseDocument, doc_id)
        case = session.get(Case, case_id)
        if document is None or case is None:
            raise PipelineError("The document or case no longer exists.")
        filename = document.filename
        storage_key = document.storage_key
        document_type = DocumentType(document.document_type)
        source_confidence = SourceConfidence(document.source_confidence)
        mime_type = document.mime_type
        case_number = case.case_number
        jurisdiction_id = case.jurisdiction_id
        language_hint = document.language
        source_metadata = dict(document.source_metadata or {})

    _mark_document(doc_id, status=IngestionStatus.PROCESSING, ingestion_stage=1)

    # --- S1 ----------------------------------------------------------------
    _stage(doc_id, case_id, 1, "RUNNING")
    raw = container.object_store.get(settings.minio_bucket_documents, storage_key)
    if not raw:
        raise PipelineError("The uploaded file is empty.")
    from app.pipeline.adapters.registry import get_adapter

    adapter = get_adapter(
        document_type, anonymous_tip=source_confidence == SourceConfidence.ANONYMOUS_TIP
    )
    meta = DocumentMeta(
        doc_id=doc_id,
        case_id=case_id,
        filename=filename,
        document_type=document_type,
        source_confidence=source_confidence,
        mime_type=mime_type,
        language_hint=language_hint,
    )
    document_nd = adapter.parse(raw, meta)
    if not document_nd.blocks:
        raise PipelineError(
            "No content could be extracted from this file — it appears to be empty "
            "or in a format CrimeLink cannot read."
        )

    derived_key = f"{case_id}/{doc_id}/normalised.txt"
    container.object_store.put(
        settings.minio_bucket_derived,
        derived_key,
        document_nd.text.encode("utf-8"),
        content_type="text/plain",
    )
    # Provenance is captured here, at parse time, because this is the last
    # point at which a derived row still knows which corpus row produced it.
    from app.domain.models import OriginRef
    from app.services.provenance import (
        collect_references,
        persist_source_references,
    )

    raw_document_origin = source_metadata.get("document_origin")
    reference_rows = collect_references(
        document_nd,
        doc_id=doc_id,
        case_id=case_id,
        document_origin=(
            OriginRef.from_dict(raw_document_origin) if raw_document_origin else None
        ),
        line_origins=source_metadata.get("line_origins"),
    )

    with sync_session() as session:
        doc = session.get(CaseDocument, doc_id)
        doc.derived_key = derived_key
        doc.language = document_nd.language
        try:
            persist_source_references(session, reference_rows)
        except Exception as exc:  # noqa: BLE001 - provenance must not lose a document
            log.warning("pipeline.provenance_failed", doc_id=doc_id, error=str(exc))
    _stage(
        doc_id,
        case_id,
        1,
        "DONE",
        detail=(
            f"{len(document_nd.blocks)} content blocks, "
            f"{len(reference_rows)} source reference(s), "
            f"language={document_nd.language}"
        ),
    )

    # --- S2 ----------------------------------------------------------------
    _stage(doc_id, case_id, 2, "RUNNING")
    from app.pipeline.extraction.deterministic import extract_deterministic

    det_entities, det_relations = extract_deterministic(document_nd, settings)
    _stage(
        doc_id,
        case_id,
        2,
        "DONE",
        detail=f"{len(det_entities)} deterministic entities, {len(det_relations)} relations",
    )

    # --- S3 ----------------------------------------------------------------
    _stage(doc_id, case_id, 3, "RUNNING")
    nlp_entities, nlp_relations = [], []
    if any(block.kind != "record" for block in document_nd.blocks):
        nlp_entities, nlp_relations = container.nlp.extract(document_nd)
    _stage(
        doc_id,
        case_id,
        3,
        "DONE",
        detail=f"{len(nlp_entities)} NLP entities, {len(nlp_relations)} relations "
        f"(provider={container.nlp.name})",
    )

    # --- S4 ----------------------------------------------------------------
    _stage(doc_id, case_id, 4, "RUNNING")
    outcome = container.resolver.resolve(
        document_nd,
        [*det_entities, *nlp_entities],
        [*det_relations, *nlp_relations],
        case_number=case_number,
        jurisdiction_id=jurisdiction_id,
    )
    _stage(
        doc_id,
        case_id,
        4,
        "DONE",
        detail=f"{outcome.stats.get('hard_matches', 0)} hard matches, "
        f"{len(outcome.alias_proposals)} fuzzy proposals",
    )

    # --- S5 ----------------------------------------------------------------
    _stage(doc_id, case_id, 5, "RUNNING")
    result = container.injector.inject(
        case_id=case_id,
        case_number=case_number,
        jurisdiction_id=jurisdiction_id,
        doc_id=doc_id,
        nodes=outcome.nodes,
        edges=outcome.edges,
        link_case=True,
        confidence=_source_weight(source_confidence),
    )
    _persist_alias_proposals(container, case_id, doc_id, outcome.alias_proposals)
    _stage(
        doc_id,
        case_id,
        5,
        "DONE",
        detail=f"{result.nodes_written} nodes, {result.edges_written} edges",
    )

    # --- S6 ----------------------------------------------------------------
    _stage(doc_id, case_id, 6, "RUNNING")
    snapshot = container.graph_store.snapshot(case_id, include_staging=False)
    findings = container.pattern_engine.detect_event_triggered(
        snapshot, changed_doc_ids={doc_id}
    )
    _persist_patterns(case_id, findings)
    _stage(doc_id, case_id, 6, "DONE", detail=f"{len(findings)} pattern finding(s)")

    warnings = "; ".join(document_nd.parse_warnings[:3])
    _mark_document(
        doc_id,
        status=IngestionStatus.COMPLETE,
        ingestion_stage=TOTAL_STAGES,
        failure_reason=None,
    )
    _mark_job(
        job_id,
        status=JobStatus.SUCCEEDED,
        stage=TOTAL_STAGES,
        stage_name="Complete",
        progress=100,
    )
    if warnings:
        log.info("pipeline.parse_warnings", doc_id=doc_id, warnings=warnings)


def _source_weight(confidence: SourceConfidence) -> float:
    return {
        SourceConfidence.VERIFIED: 1.0,
        # Synthetic development data is weighted like an unverified upload so
        # the graph and the pattern detectors exercise the same code paths as
        # for a real upload; unlike a tip it is not staged.
        SourceConfidence.SYNTHETIC: 0.75,
        SourceConfidence.UNVERIFIED: 0.75,
        SourceConfidence.ANONYMOUS_TIP: 0.4,
    }[confidence]


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _persist_alias_proposals(
    container, case_id: str, doc_id: str, proposals: list[dict[str, Any]]
) -> None:
    """Write POTENTIAL_ALIAS edges and queue rows, skipping known pairs."""
    if not proposals:
        return
    graph = container.graph_store
    with sync_session() as session:
        for proposal in proposals:
            source = proposal["source_node_key"]
            target = proposal["target_node_key"]
            if source == target:
                continue
            existing = session.execute(
                select(EntityResolutionItem).where(
                    EntityResolutionItem.case_id == case_id,
                    EntityResolutionItem.source_node_key.in_([source, target]),
                    EntityResolutionItem.target_node_key.in_([source, target]),
                )
            ).scalars().all()
            if existing:
                continue
            if graph.has_tombstone(source, target):
                continue
            item = EntityResolutionItem(
                case_id=case_id,
                source_node_key=source,
                target_node_key=target,
                similarity_score=proposal["similarity_score"],
                match_basis=MatchBasis(proposal["match_basis"]),
                evidence_doc_ids=proposal["evidence_doc_ids"],
                status=ResolutionStatus.PENDING,
            )
            session.add(item)
            session.flush()
            graph.add_potential_alias(
                source, target, item.id, proposal["similarity_score"]
            )
            log.info(
                "pipeline.er_proposal",
                case_id=case_id,
                queue_id=item.id,
                similarity=proposal["similarity_score"],
            )


def _persist_patterns(case_id: str, findings) -> None:
    """Insert findings as NEW, de-duplicating against what is already queued."""
    if not findings:
        return
    with sync_session() as session:
        for finding in findings:
            keys = sorted(finding.entity_keys)
            existing = session.execute(
                select(DetectedPattern).where(
                    DetectedPattern.case_id == case_id,
                    DetectedPattern.pattern_type == finding.pattern_type,
                    DetectedPattern.entity_keys == keys,
                )
            ).scalars().all()
            live = [p for p in existing if p.status in (
                PatternStatus.NEW, PatternStatus.REVIEWED, PatternStatus.ESCALATED
            )]
            if live:
                continue
            dismissed = [p for p in existing if p.status == PatternStatus.DISMISSED]
            if dismissed and set(dismissed[0].evidence_doc_ids or []) >= set(finding.evidence_doc_ids):
                # Already dismissed and no new evidence has appeared since.
                continue
            pattern = DetectedPattern(
                case_id=case_id,
                pattern_type=PatternType(finding.pattern_type.value),
                confidence=finding.confidence,
                entity_keys=keys,
                evidence_doc_ids=finding.evidence_doc_ids,
                explanation=finding.explanation,
                details=finding.details,
                status=PatternStatus.NEW,
            )
            session.add(pattern)
            log.info(
                "pipeline.pattern_detected",
                case_id=case_id,
                pattern=finding.pattern_type.value,
                confidence=finding.confidence,
            )


# ---------------------------------------------------------------------------
# Status plumbing
# ---------------------------------------------------------------------------


def _stage(doc_id: str, case_id: str, stage: int, status: str, detail: str = "") -> None:
    container = get_container()
    name = STAGE_NAMES.get(stage, f"Stage {stage}")
    with sync_session() as session:
        session.add(
            DocumentStageEvent(
                doc_id=doc_id,
                case_id=case_id,
                stage=stage,
                stage_name=name,
                status=status,
                detail=detail,
            )
        )
        document = session.get(CaseDocument, doc_id)
        if document is not None and status == "RUNNING":
            document.ingestion_stage = stage
    try:
        container.event_bus.publish(
            f"case:{case_id}",
            {
                "type": "stage",
                "doc_id": doc_id,
                "case_id": case_id,
                "stage": stage,
                "stage_name": name,
                "status": status,
                "detail": detail,
                "total_stages": TOTAL_STAGES,
                "at": utcnow().isoformat(),
            },
        )
    except Exception:  # noqa: BLE001 - status push must never fail the pipeline
        log.debug("pipeline.stage_publish_failed", doc_id=doc_id)


def _mark_document(doc_id: str, **fields: Any) -> None:
    """Update document columns.

    ``status`` is accepted as a readable alias for the ``ingestion_status``
    column; every other keyword must name a real mapped attribute, and unknown
    names are reported loudly instead of being set (and silently discarded) as
    plain Python attributes.
    """
    if "status" in fields:
        fields["ingestion_status"] = fields.pop("status")
    with sync_session() as session:
        document = session.get(CaseDocument, doc_id)
        if document is None:
            return
        for key, value in fields.items():
            if not hasattr(type(document), key):
                raise PipelineError(
                    f"internal error: CaseDocument has no column '{key}'"
                )
            setattr(document, key, value)


def _mark_job(job_id: str, **fields: Any) -> None:
    with sync_session() as session:
        job = session.get(IngestionJob, job_id)
        if job is None:
            return
        for key, value in fields.items():
            if key == "stage":
                job.current_stage = value
                job.progress_pct = int(100 * value / TOTAL_STAGES)
            elif key == "progress":
                job.progress_pct = value
            elif key == "status":
                job.status = value
                if value in (JobStatus.RUNNING,):
                    job.started_at = job.started_at or utcnow()
                if value in (JobStatus.SUCCEEDED, JobStatus.FAILED):
                    job.finished_at = utcnow()
            elif value is not None:
                setattr(job, key, value)


def _increment_retry(doc_id: str) -> int:
    with sync_session() as session:
        document = session.get(CaseDocument, doc_id)
        if document is None:
            return MAX_RETRIES
        document.retry_count = int(document.retry_count or 0) + 1
        return document.retry_count


def _fail_document(
    doc_id: str, job_id: str, case_id: str, reason: str, *, quarantine: bool
) -> None:
    """Mark a document failed and, when required, place it in quarantine."""
    _mark_document(
        doc_id,
        status=IngestionStatus.QUARANTINED if quarantine else IngestionStatus.FAILED,
        failure_reason=reason,
        quarantined=quarantine,
    )
    _mark_job(job_id, status=JobStatus.QUARANTINED if quarantine else JobStatus.FAILED, error=reason)
    _stage(doc_id, case_id, 0, "FAILED", detail=reason)
    with sync_session() as session:
        audit_service.append(
            session,
            action_type=AuditAction.CONFIG_CHANGE,
            target_resource=f"document:{doc_id}",
            case_id=case_id,
            details={"reason": reason, "quarantined": quarantine},
        )
    log.warning("pipeline.document_failed", doc_id=doc_id, quarantined=quarantine, reason=reason)


def _session() -> Session:  # pragma: no cover - convenience for scripts
    from app.db.session import get_sync_sessionmaker

    return get_sync_sessionmaker()()
