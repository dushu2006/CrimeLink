"""The investigator-driven case workflow (PRD 21: explicit stage execution).

The pipeline that turns source documents into a graph is *not* hidden behind
page loads.  An investigator walks a case through named stages, each of which
is a REAL backend operation with a persisted outcome:

    1  process_data         run the six-stage ingestion for pending documents
    2  extract_entities     re-run deterministic + NLP extraction, count by type
    3  resolve_entities     re-run resolution, queue low-confidence matches
    4  build_relationships  re-run the pipeline so semantic edges materialise
    5  build_graph          verify the graph backend, report what is persisted
    6  network_analysis     centrality + communities + pattern detection
    7  ai_analysis          AI reasoning over top entities (honest if keyless)
    8  generate_findings    evidence-backed findings from REAL analysis output

Rules:

* a stage refuses to run until its prerequisites are COMPLETED (409 to the API);
* every run records status, duration, and a detail payload of REAL counts —
  never a progress animation;
* failures set the stage to FAILED with the reason; dependent stages stay
  locked until the failure is fixed and the stage re-run;
* nothing here fabricates content: extraction, resolution and graph writes go
  through the same pipeline components the upload path uses, and findings come
  from :mod:`app.analytics.findings` over the persisted graph.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import asdict
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.centrality import compute_centrality
from app.analytics.findings import generate_findings
from app.audit.service import audit_service
from app.config import Settings, get_settings
from app.container import Container, get_container
from app.db.base import new_uuid, utcnow
from app.db.models import (
    Case,
    CaseDocument,
    EntityResolutionItem,
    IngestionJob,
    InvestigationFinding,
    InvestigationStageRun,
)
from app.db.session import sync_session
from app.domain.enums import (
    AuditAction,
    DocumentType,
    IngestionStatus,
    JobStatus,
    ResolutionStatus,
    SourceConfidence,
)
from app.errors import CrimeLinkError, ServiceUnavailableError, ValidationFailedError
from app.logging import bind_context, get_logger

log = get_logger("crimelink.investigation")

# The workflow contract: stage number, machine name, human label, prerequisites.
# A stage may only run when every prerequisite stage is COMPLETED for the case.
STAGES: list[dict[str, Any]] = [
    {"stage": 1, "key": "process_data", "label": "Load & Process Data", "requires": []},
    {"stage": 2, "key": "extract_entities", "label": "Extract Entities", "requires": [1]},
    {"stage": 3, "key": "resolve_entities", "label": "Resolve Entities", "requires": [2]},
    {"stage": 4, "key": "build_relationships", "label": "Build Relationships", "requires": [3]},
    {"stage": 5, "key": "build_graph", "label": "Build Investigation Graph", "requires": [4]},
    {"stage": 6, "key": "network_analysis", "label": "Run Network Analysis", "requires": [5]},
    {"stage": 7, "key": "ai_analysis", "label": "AI-Assisted Analysis", "requires": [6]},
    {"stage": 8, "key": "generate_findings", "label": "Generate Findings", "requires": [6]},
]

STAGE_BY_KEY = {s["key"]: s for s in STAGES}

# Reprocessing the whole corpus in one request is bounded: the embedded
# profile targets case-sized workloads, and the Celery path exists for
# production volumes.  A run that hits the cap reports how much work remains.
MAX_DOCS_PER_RUN = 40


class StageBlocked(CrimeLinkError):
    """The stage's prerequisites are not COMPLETED."""

    code = "stage_blocked"
    http_status = 409

    def __init__(self, stage_key: str, unmet: list[str]) -> None:
        self.unmet = unmet
        super().__init__(
            f"Stage '{stage_key}' cannot run before "
            f"{', '.join(unmet)} has completed."
        )


class UnknownStage(CrimeLinkError):
    code = "unknown_stage"
    http_status = 404


# ---------------------------------------------------------------------------
# Public surface (called from the API in a worker thread)
# ---------------------------------------------------------------------------


def workflow_state(case_id: str) -> dict[str, Any]:
    """Current state of every stage for a case (never runs anything)."""
    with sync_session() as session:
        rows = (
            session.execute(
                select(InvestigationStageRun).where(
                    InvestigationStageRun.case_id == case_id
                )
            )
            .scalars()
            .all()
        )
        by_stage = {row.stage: row for row in rows}
        doc_counts = _document_counts(session, case_id)
    stages = []
    for definition in STAGES:
        row = by_stage.get(definition["stage"])
        blocked_by = [
            p for p in definition["requires"] if _status_of(by_stage, p) != "COMPLETED"
        ]
        stages.append(
            {
                "stage": definition["stage"],
                "key": definition["key"],
                "label": definition["label"],
                "requires": definition["requires"],
                "status": row.status if row else "PENDING",
                "detail": row.detail if row else {},
                "error": row.error if row else None,
                "attempt_count": row.attempt_count if row else 0,
                "finished_at": row.finished_at.isoformat() if row and row.finished_at else None,
                "duration_ms": row.duration_ms if row else None,
                "runnable": not blocked_by,
                "blocked_by": blocked_by,
            }
        )
    return {
        "case_id": case_id,
        "stages": stages,
        "documents": doc_counts,
        "graph_backend": get_container().settings.effective_graph_backend,
    }


def key_or_none(stage_no: int) -> str:
    for s in STAGES:
        if s["stage"] == stage_no:
            return s["key"]
    return f"stage-{stage_no}"


def _status_of(by_stage: dict[int, InvestigationStageRun], stage_no: int) -> str:
    row = by_stage.get(stage_no)
    return row.status if row else "PENDING"


def run_stage(case_id: str, stage_key: str, user_id: str | None = None) -> dict[str, Any]:
    """Execute one stage for a case, enforcing prerequisites.  Synchronous."""
    definition = STAGE_BY_KEY.get(stage_key)
    if definition is None:
        raise UnknownStage(f"Unknown investigation stage '{stage_key}'.")
    handlers: dict[str, Callable[..., dict[str, Any]]] = {
        "process_data": _stage_process_data,
        "extract_entities": _stage_extract_entities,
        "resolve_entities": _stage_resolve_entities,
        "build_relationships": _stage_build_relationships,
        "build_graph": _stage_build_graph,
        "network_analysis": _stage_network_analysis,
        "ai_analysis": _stage_ai_analysis,
        "generate_findings": _stage_generate_findings,
    }

    with sync_session() as session:
        case = session.get(Case, case_id)
        if case is None:
            raise ValidationFailedError("Case not found.")
        rows = (
            session.execute(
                select(InvestigationStageRun).where(
                    InvestigationStageRun.case_id == case_id
                )
            )
            .scalars()
            .all()
        )
        by_stage = {row.stage: row for row in rows}
        unmet = [
            key_or_none(p)
            for p in definition["requires"]
            if _status_of(by_stage, p) != "COMPLETED"
        ]
    if unmet:
        raise StageBlocked(stage_key, unmet)

    bind_context(case_id=case_id, stage=stage_key)
    container = get_container()
    settings: Settings = container.settings

    with sync_session() as session:
        run = (
            session.execute(
                select(InvestigationStageRun).where(
                    InvestigationStageRun.case_id == case_id,
                    InvestigationStageRun.stage == definition["stage"],
                )
            )
            .scalars()
            .one_or_none()
        )
        if run is None:
            run = InvestigationStageRun(
                id=new_uuid(), case_id=case_id, stage=definition["stage"], status="PENDING"
            )
            session.add(run)
        run.status = "RUNNING"
        run.error = None
        run.attempt_count = int(run.attempt_count or 0) + 1
        run.started_at = utcnow()
        run.triggered_by = user_id
        session.commit()
        run_id = run.id

    started = time.perf_counter()
    try:
        detail = handlers[stage_key](container, settings, case_id, user_id)
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        log.exception("investigation.stage_failed", stage=stage_key, error=str(exc))
        with sync_session() as session:
            row = session.get(InvestigationStageRun, run_id)
            row.status = "FAILED"
            row.error = f"{type(exc).__name__}: {exc}"
            row.finished_at = utcnow()
            row.duration_ms = elapsed
            session.commit()
        raise
    elapsed = int((time.perf_counter() - started) * 1000)

    with sync_session() as session:
        row = session.get(InvestigationStageRun, run_id)
        row.status = "COMPLETED"
        row.detail = detail
        row.finished_at = utcnow()
        row.duration_ms = elapsed
        session.commit()
        audit_service.append(
            session,
            action_type=AuditAction.CONFIG_CHANGE,
            target_resource=f"case:{case_id}/investigation/{stage_key}",
            case_id=case_id,
            details={"stage": stage_key, "attempt": row.attempt_count, "duration_ms": elapsed},
        )

    log.info(
        "investigation.stage_completed", stage=stage_key, case_id=case_id, duration_ms=elapsed
    )
    return {
        "stage": definition["stage"],
        "key": stage_key,
        "status": "COMPLETED",
        "detail": detail,
        "duration_ms": elapsed,
    }


# ---------------------------------------------------------------------------
# Stage implementations — every one performs a real operation
# ---------------------------------------------------------------------------


def _document_counts(session: Session, case_id: str) -> dict[str, int]:
    rows = session.execute(
        select(CaseDocument.ingestion_status).where(CaseDocument.case_id == case_id)
    ).all()
    counts: dict[str, int] = {}
    for (status,) in rows:
        counts[status] = counts.get(status, 0) + 1
    counts["total"] = len(rows)
    return counts


def _case_documents(session: Session, case_id: str) -> list[CaseDocument]:
    return (
        session.execute(
            select(CaseDocument)
            .where(CaseDocument.case_id == case_id)
            .order_by(CaseDocument.created_at.asc())
        )
        .scalars()
        .all()
    )


def _run_pipeline_for(container: Container, doc: CaseDocument, user_id: str | None) -> str:
    """Run the REAL six-stage pipeline for one document, synchronously.

    Used for pending documents (process_data) and for idempotent re-runs of
    processed documents (build_relationships, when extraction rules have
    improved).  Provenance keys make re-runs converge instead of duplicating.
    """
    from app.pipeline.orchestrator import process_document

    job_id = new_uuid()
    with sync_session() as session:
        session.add(
            IngestionJob(
                id=job_id,
                doc_id=doc.id,
                case_id=doc.case_id,
                requested_by=user_id,
                status=JobStatus.QUEUED,
            )
        )
        session.commit()
    process_document(
        job_id=job_id,
        doc_id=doc.id,
        case_id=doc.case_id,
        trace_id=f"investigation-{uuid.uuid4().hex[:12]}",
        user_id=user_id or "",
    )
    return job_id


def _parse_document(container: Container, settings: Settings, doc: CaseDocument):
    """Re-parse a stored document exactly like pipeline stage S1 does."""
    from app.pipeline.adapters.protocol import DocumentMeta
    from app.pipeline.adapters.registry import get_adapter

    raw = container.object_store.get(settings.minio_bucket_documents, doc.storage_key)
    if not raw:
        raise ValidationFailedError(
            f"The stored file for document {doc.id} is missing from the object store."
        )
    adapter = get_adapter(
        DocumentType(doc.document_type),
        anonymous_tip=doc.source_confidence == SourceConfidence.ANONYMOUS_TIP.value,
    )
    meta = DocumentMeta(
        doc_id=doc.id,
        case_id=doc.case_id,
        filename=doc.filename,
        document_type=DocumentType(doc.document_type),
        source_confidence=SourceConfidence(doc.source_confidence),
        mime_type=doc.mime_type or "application/octet-stream",
        language_hint=doc.language,
    )
    return adapter.parse(raw, meta)


def _stage_process_data(
    container: Container, settings: Settings, case_id: str, user_id: str | None
) -> dict[str, Any]:
    """Run ingestion for every document of the case that is not yet COMPLETE."""
    with sync_session() as session:
        docs = _case_documents(session, case_id)
        pending = [
            d
            for d in docs
            if d.ingestion_status
            not in (IngestionStatus.COMPLETE.value, IngestionStatus.QUARANTINED.value)
        ]
        already = sum(1 for d in docs if d.ingestion_status == IngestionStatus.COMPLETE.value)
        quarantined = sum(
            1 for d in docs if d.ingestion_status == IngestionStatus.QUARANTINED.value
        )
        total = len(docs)

    processed, failures = 0, []
    for doc in pending[:MAX_DOCS_PER_RUN]:
        try:
            _run_pipeline_for(container, doc, user_id)
            processed += 1
        except Exception as exc:  # noqa: BLE001 - report, don't abandon the batch
            failures.append({"doc_id": doc.id, "filename": doc.filename, "error": str(exc)})

    detail: dict[str, Any] = {
        "documents_total": total,
        "already_complete": already,
        "processed_now": processed,
        "still_pending": max(0, len(pending) - processed),
        "quarantined": quarantined,
        "failures": failures[:10],
    }
    if pending and processed == 0 and failures:
        # Never report COMPLETED when nothing actually processed — the API
        # surfaces this as a failure with the backend's own message.
        raise ValidationFailedError(
            f"No document of the case could be processed ({failures[0]['error']})."
        )
    if len(pending) > MAX_DOCS_PER_RUN:
        detail["note"] = (
            f"{len(pending) - processed} document(s) remain; re-run this stage to "
            "continue processing."
        )
    return detail


def _iter_parsed(container: Container, settings: Settings, case_id: str, only_complete: bool = True):
    with sync_session() as session:
        docs = _case_documents(session, case_id)
        if only_complete:
            docs = [d for d in docs if d.ingestion_status == IngestionStatus.COMPLETE.value]
        doc_rows = list(docs)
    for doc in doc_rows:
        try:
            yield doc, _parse_document(container, settings, doc), None
        except Exception as exc:  # noqa: BLE001 - inventory reports parse failures
            yield doc, None, str(exc)


def _stage_extract_entities(
    container: Container, settings: Settings, case_id: str, user_id: str | None
) -> dict[str, Any]:
    """Re-run deterministic + NLP extraction over stored documents.

    This is a real pass over the stored files: deterministic regex/gazetteer
    extraction plus the configured NLP provider.  It reports an inventory by
    entity type; nothing is written to the graph here (that is the
    build_relationships stage) so the inventory can be compared across runs.
    """
    from app.pipeline.extraction.deterministic import extract_deterministic

    type_counts: dict[str, int] = {}
    deterministic_total = 0
    nlp_total = 0
    relations_total = 0
    docs_ok = 0
    errors: list[dict[str, str]] = []
    providers: set[str] = set()

    for doc, document, error in _iter_parsed(container, settings, case_id):
        if error is not None:
            errors.append({"doc_id": doc.id, "filename": doc.filename, "error": error})
            continue
        det_entities, det_relations = extract_deterministic(document, settings)
        nlp_entities, nlp_relations = [], []
        if any(block.kind != "record" for block in document.blocks):
            nlp_entities, nlp_relations = container.nlp.extract(document)
            providers.add(container.nlp.name)
        docs_ok += 1
        deterministic_total += len(det_entities)
        nlp_total += len(nlp_entities)
        relations_total += len(det_relations) + len(nlp_relations)
        from app.domain.enums import canonical_label

        for entity in [*det_entities, *nlp_entities]:
            label = canonical_label(entity.entity_type.value)
            type_counts[label] = type_counts.get(label, 0) + 1

    if docs_ok == 0 and errors:
        raise ValidationFailedError(
            f"No document of the case could be parsed ({errors[0]['error']})."
        )
    return {
        "documents_parsed": docs_ok,
        "documents_failed": len(errors),
        "deterministic_entities": deterministic_total,
        "nlp_entities": nlp_total,
        "nlp_provider": sorted(providers)[0] if len(providers) == 1 else sorted(providers),
        "candidate_relations": relations_total,
        "by_entity_type": dict(sorted(type_counts.items(), key=lambda kv: -kv[1])),
        "errors": errors[:10],
    }


def _stage_resolve_entities(
    container: Container, settings: Settings, case_id: str, user_id: str | None
) -> dict[str, Any]:
    """Run entity resolution per document and queue low-confidence matches.

    Hard matches (exact identifiers) merge silently because the evidence is
    deterministic; fuzzy alias proposals land in the review queue
    (entity_resolution_queue) with PENDING status — a human decides.
    """
    from app.pipeline.orchestrator import _persist_alias_proposals

    with sync_session() as session:
        case = session.get(Case, case_id)

    docs_ok = 0
    errors: list[dict[str, str]] = []
    totals: dict[str, int] = {}
    proposals_persisted = 0
    from app.pipeline.extraction.deterministic import extract_deterministic

    for doc, document, error in _iter_parsed(container, settings, case_id):
        if error is not None:
            errors.append({"doc_id": doc.id, "filename": doc.filename, "error": error})
            continue
        det_entities, det_relations = extract_deterministic(document, settings)
        nlp_entities, nlp_relations = [], []
        if any(block.kind != "record" for block in document.blocks):
            nlp_entities, nlp_relations = container.nlp.extract(document)
        outcome = container.resolver.resolve(
            document,
            [*det_entities, *nlp_entities],
            [*det_relations, *nlp_relations],
            case_number=case.case_number,
            jurisdiction_id=case.jurisdiction_id,
        )
        docs_ok += 1
        for key, value in outcome.stats.items():
            if isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0) + int(value)
        before = _resolution_queue_size(case_id)
        _persist_alias_proposals(container, case_id, doc.id, outcome.alias_proposals)
        proposals_persisted += max(0, _resolution_queue_size(case_id) - before)

    pending = _resolution_queue_size(case_id)
    return {
        "documents_resolved": docs_ok,
        "documents_failed": len(errors),
        "hard_matches": totals.get("hard_matches", 0),
        "entities_normalized": totals.get("entities", 0),
        "alias_proposals_new": proposals_persisted,
        "review_queue_pending": pending,
        "errors": errors[:10],
    }


def _resolution_queue_size(case_id: str) -> int:
    with sync_session() as session:
        return int(
            session.execute(
                select(EntityResolutionItem.id).where(
                    EntityResolutionItem.case_id == case_id,
                    EntityResolutionItem.status == ResolutionStatus.PENDING,
                )
            ).scalars().all().__len__()
        )


def _stage_build_relationships(
    container: Container, settings: Settings, case_id: str, user_id: str | None
) -> dict[str, Any]:
    """Materialise semantic relationships by re-running the real pipeline.

    The extraction vocabulary improves over time (e.g. OWNS_ACCOUNT was added
    for corpus account holders).  Re-running the six-stage pipeline for the
    case's documents is idempotent — provenance keys converge — so this stage
    reports the relationship inventory BEFORE and AFTER, and the deltas are
    the relationships this run actually constructed.
    """
    with sync_session() as session:
        docs = [
            d
            for d in _case_documents(session, case_id)
            if d.ingestion_status == IngestionStatus.COMPLETE.value
        ]
    before = _edge_inventory(container, case_id)

    rerun, failures = 0, []
    for doc in docs[:MAX_DOCS_PER_RUN]:
        try:
            _run_pipeline_for(container, doc, user_id)
            rerun += 1
        except Exception as exc:  # noqa: BLE001
            failures.append({"doc_id": doc.id, "filename": doc.filename, "error": str(exc)})

    after = _edge_inventory(container, case_id)
    deltas = {
        rel: after.get(rel, 0) - before.get(rel, 0)
        for rel in sorted(set(before) | set(after))
        if after.get(rel, 0) != before.get(rel, 0)
    }
    person_links = {
        rel: count
        for rel, count in sorted(after.items(), key=lambda kv: -kv[1])
        if rel in {
            "USES_PHONE", "OWNS_VEHICLE", "OWNS_ACCOUNT", "CALLED", "MEMBER_OF",
            "TRANSFER_TO", "LOCATED_AT", "PARTICIPATED_IN", "ASSOCIATE_OF",
            "RELATIVE_OF", "ACCUSED_IN", "ARRESTED_WITH", "NAMED_ACCOMPLICE_OF",
        }
    }
    detail: dict[str, Any] = {
        "documents_reprocessed": rerun,
        "relationships_by_type": dict(sorted(after.items(), key=lambda kv: -kv[1])),
        "person_linked_relationships": person_links,
        "new_relationships_this_run": deltas,
        "failures": failures[:10],
    }
    if len(docs) > MAX_DOCS_PER_RUN:
        detail["note"] = (
            f"{len(docs) - rerun} document(s) not reprocessed in this run; "
            "re-run to continue."
        )
    return detail


def _edge_inventory(container: Container, case_id: str) -> dict[str, int]:
    snapshot = container.graph_store.snapshot(case_id, include_staging=False)
    counts: dict[str, int] = {}
    for edge in snapshot.edges:
        counts[edge.rel_type] = counts.get(edge.rel_type, 0) + 1
    return counts


def _stage_build_graph(
    container: Container, settings: Settings, case_id: str, user_id: str | None
) -> dict[str, Any]:
    """Verify what is actually persisted in the configured graph backend.

    This stage does not *copy* anything: graph injection already went through
    the graph store port during the pipeline (S5).  Its job is to prove the
    store is reachable, that the case graph exists, and to report exactly what
    is persisted — and to FAIL honestly when the backend (e.g. Neo4j) is down.
    """
    backend = settings.effective_graph_backend
    store = container.graph_store
    try:
        snapshot = store.snapshot(case_id, include_staging=False)
    except Exception as exc:  # noqa: BLE001 - the report IS the stage result
        raise ServiceUnavailableError(
            f"Graph database unavailable ({type(exc).__name__}). Graph verification "
            "could not be completed."
        ) from exc

    from app.domain.enums import canonical_label

    labels: dict[str, int] = {}
    for node in snapshot.nodes.values():
        wire = canonical_label(node.label)
        labels[wire] = labels.get(wire, 0) + 1
    persons = labels.get("PERSON", 0)
    if persons == 0:
        raise ValidationFailedError(
            "The case graph contains no person nodes; run the relationship stage first."
        )
    return {
        "graph_backend": backend,
        "nodes_persisted": len(snapshot.nodes),
        "edges_persisted": len(snapshot.edges),
        "nodes_by_label": dict(sorted(labels.items(), key=lambda kv: -kv[1])),
        "person_nodes": persons,
        "graph_version": store.version() if callable(store.version) else store.version,
        "note": (
            "Nodes and relationships were written through the graph store port "
            "during pipeline stage 5; this stage verified the persisted state."
        ),
    }


def _stage_network_analysis(
    container: Container, settings: Settings, case_id: str, user_id: str | None
) -> dict[str, Any]:
    """Centrality + communities + whole-graph pattern detection, persisted."""
    from app.pipeline.orchestrator import _persist_patterns

    snapshot = container.graph_store.snapshot(case_id, include_staging=False)
    centrality = compute_centrality(snapshot, settings)
    findings = container.pattern_engine.detect_scheduled(snapshot, centrality=centrality)
    _persist_patterns(case_id, findings)

    top = sorted(
        centrality.betweenness.items(), key=lambda kv: -kv[1]
    )[:5]
    top_persons = [
        {
            "provenance_key": key,
            "name": snapshot.nodes[key].name if key in snapshot.nodes else key,
            "betweenness": round(score, 4),
        }
        for key, score in top
        if key in snapshot.nodes and snapshot.nodes[key].label in ("PERSON", "Person")
    ]
    return {
        "nodes_analyzed": centrality.node_count,
        "edges_analyzed": centrality.edge_count,
        "engine": centrality.engine,
        "communities": len(centrality.community_members),
        "patterns_detected": len(findings),
        "pattern_types": sorted({f.pattern_type.value for f in findings}),
        "top_persons_by_betweenness": top_persons,
    }


def _stage_ai_analysis(
    container: Container, settings: Settings, case_id: str, user_id: str | None
) -> dict[str, Any]:
    """AI-assisted reasoning over the highest-centrality persons.

    Honest by construction: with no API key configured this stage reports
    that AI was NOT used and completes, so the deterministic findings stage
    remains available.  With a key configured, each response goes through the
    gateway's validation and is stored as a reviewable AI-assisted finding
    carrying its evidence references.
    """
    from app.ai.gateway import get_ai_gateway

    if not settings.ai_role_available("reasoning"):
        return {
            "ai_available": False,
            "reason": "no_api_key_for_role_reasoning",
            "message": (
                "AI analysis could not be completed because no API key is configured "
                "for the reasoning model (CRIMELINK_AI_REASONING_API_KEY). "
                "Deterministic analysis results remain available."
            ),
            "questions_asked": 0,
        }

    snapshot = container.graph_store.snapshot(case_id, include_staging=False)
    centrality = compute_centrality(snapshot, settings)
    top_count = max(1, int(settings.ai_reasoning_target_count))
    top_persons = [
        key
        for key, _ in sorted(centrality.betweenness.items(), key=lambda kv: -kv[1])[:top_count]
        if key in snapshot.nodes and snapshot.nodes[key].label in ("PERSON", "Person")
    ]
    gateway = get_ai_gateway()

    # Independent per-person analyses run concurrently over the workflow loop —
    # three sequential reasoning calls is the dominant cost of this stage, and
    # nothing here depends on the answer to a previous person.
    async def _ask_one(key: str):
        name = snapshot.nodes[key].name
        response = await gateway.ask(
            question=(
                f"Analyse the network around {name} within this case: which "
                "connections are analytically significant and what evidence "
                "supports them?"
            ),
            case_id=case_id,
            principal_id=user_id,
            depth=2,
            target_key=key,
        )
        return key, name, response

    responses: list[tuple] = []
    if top_persons:
        responses = await_free(asyncio.gather(*(_ask_one(k) for k in top_persons)))

    results = []
    available_count = 0
    for key, name, response in responses:
        if response.available:
            available_count += 1
            _persist_ai_finding(case_id, key, name, response)
        results.append(
            {
                "person": name,
                "available": response.available,
                "fallback_reason": response.fallback_reason,
            }
        )
    return {
        "ai_available": available_count > 0,
        "questions_asked": len(top_persons),
        "answers_available": available_count,
        "per_person": results,
    }


def _persist_ai_finding(case_id: str, person_key: str, name: str, response) -> None:
    """Store a validated AI response as a reviewable, method-labelled finding."""
    finding = response.finding
    with sync_session() as session:
        session.add(
            InvestigationFinding(
                id=new_uuid(),
                case_id=case_id,
                finding_type="AI_ASSESSED",
                title=f"AI assessment of the network around {name}",
                narrative=finding.summary,
                reason=(
                    "Generated by the configured reasoning model over the "
                    "pseudonymized case subgraph; validated against the response "
                    "schema. Review against the cited evidence before relying on it."
                ),
                confidence=float(finding.confidence or 0.0),
                confidence_band=(
                    "HIGH" if (finding.confidence or 0) >= 0.75
                    else "MEDIUM" if (finding.confidence or 0) >= 0.5 else "LOW"
                ),
                method="ai_assisted",
                entity_keys=[person_key],
                evidence=[
                    {
                        "kind": "ai_response",
                        "query_id": response.query_id,
                        "model": response.model,
                        "evidence_refs": [
                            ref if isinstance(ref, dict) else str(ref)
                            for ref in (finding.evidence_refs or [])
                        ][:20],
                    }
                ],
                details={"evidence_level": finding.evidence_level},
                status="NEW",
            )
        )
        session.commit()


def _stage_generate_findings(
    container: Container, settings: Settings, case_id: str, user_id: str | None
) -> dict[str, Any]:
    """Consolidated, evidence-backed findings from REAL analysis results."""
    snapshot = container.graph_store.snapshot(case_id, include_staging=False)
    centrality = compute_centrality(snapshot, settings)
    candidates = generate_findings(snapshot, _centrality_map(centrality))

    created, skipped = 0, 0
    with sync_session() as session:
        existing = (
            session.execute(
                select(InvestigationFinding).where(InvestigationFinding.case_id == case_id)
            )
            .scalars()
            .all()
        )
        existing_keys = {
            (f.finding_type, tuple(sorted(f.entity_keys or [])))
            for f in existing
            if f.status != "DISMISSED"
        }
        dismissed_keys = {
            (f.finding_type, tuple(sorted(f.entity_keys or [])))
            for f in existing
            if f.status == "DISMISSED"
        }
        for candidate in candidates:
            key = candidate.dedupe_key()
            if key in existing_keys or key in dismissed_keys:
                skipped += 1
                continue
            session.add(
                InvestigationFinding(
                    id=new_uuid(),
                    case_id=case_id,
                    finding_type=candidate.finding_type,
                    title=candidate.title,
                    narrative=candidate.narrative,
                    reason=candidate.reason,
                    confidence=candidate.confidence,
                    confidence_band=candidate.confidence_band,
                    method="deterministic",
                    entity_keys=candidate.entity_keys,
                    evidence=candidate.evidence,
                    details=candidate.details,
                    status="NEW",
                )
            )
            created += 1
        session.commit()

    by_type: dict[str, int] = {}
    with sync_session() as session:
        rows = (
            session.execute(
                select(InvestigationFinding).where(
                    InvestigationFinding.case_id == case_id
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            by_type[row.finding_type] = by_type.get(row.finding_type, 0) + 1
    return {
        "findings_created": created,
        "findings_skipped_existing": skipped,
        "findings_total": sum(by_type.values()),
        "by_type": by_type,
    }


def _centrality_map(centrality) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for key in centrality.betweenness:
        out[key] = {
            "betweenness": float(centrality.betweenness.get(key) or 0.0),
            "pagerank": float(centrality.pagerank.get(key) or 0.0),
            "degree": float(centrality.degree.get(key) or 0.0),
        }
    return out


# Stages run on worker threads (the API submits them with to_thread), but the
# AI gateway talks to the database through the async engine, whose pooled
# connections are bound to the loop that created them.  A fresh loop per call
# would hand a pooled connection to a different loop and wedge; a single
# process-wide workflow loop keeps every async call from stage code on the
# same executor for the life of the process.
_workflow_loop: asyncio.AbstractEventLoop | None = None
_workflow_loop_lock = threading.Lock()


def _workflow_event_loop() -> asyncio.AbstractEventLoop:
    global _workflow_loop
    if _workflow_loop is None:
        with _workflow_loop_lock:
            if _workflow_loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(
                    target=loop.run_forever, name="crimelink-workflow", daemon=True
                ).start()
                _workflow_loop = loop
    return _workflow_loop


def await_free(coro):
    """Run a coroutine to completion on the dedicated workflow loop."""
    return asyncio.run_coroutine_threadsafe(coro, _workflow_event_loop()).result()


def findings_list(case_id: str) -> dict[str, Any]:
    with sync_session() as session:
        rows = (
            session.execute(
                select(InvestigationFinding)
                .where(InvestigationFinding.case_id == case_id)
                .order_by(InvestigationFinding.confidence.desc(), InvestigationFinding.created_at.desc())
            )
            .scalars()
            .all()
        )
        return {
            "items": [
                {
                    "id": row.id,
                    "finding_type": row.finding_type,
                    "title": row.title,
                    "narrative": row.narrative,
                    "reason": row.reason,
                    "confidence": row.confidence,
                    "confidence_band": row.confidence_band,
                    "method": row.method,
                    "entity_keys": row.entity_keys,
                    "evidence": row.evidence,
                    "details": row.details,
                    "status": row.status,
                    "review_note": row.review_note,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]
        }


def review_finding(
    case_id: str, finding_id: str, decision: str, note: str | None, user_id: str
) -> dict[str, Any]:
    if decision not in ("CONFIRMED", "DISMISSED"):
        raise ValidationFailedError("Decision must be CONFIRMED or DISMISSED.")
    with sync_session() as session:
        row = session.get(InvestigationFinding, finding_id)
        if row is None or row.case_id != case_id:
            raise ValidationFailedError("Finding not found for this case.")
        row.status = decision
        row.review_note = note
        row.reviewed_by = user_id
        row.reviewed_at = utcnow()
        session.commit()
        audit_service.append(
            session,
            action_type=AuditAction.REVIEW_DECISION if hasattr(AuditAction, "REVIEW_DECISION") else AuditAction.CONFIG_CHANGE,
            target_resource=f"finding:{finding_id}",
            case_id=case_id,
            details={"decision": decision, "note": note},
        )
        return {"id": row.id, "status": row.status}
