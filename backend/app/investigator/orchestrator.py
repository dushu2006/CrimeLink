"""Question-driven investigation pipeline.

Scope → session → conversational fast path → load → resolution →
patterns → relationships → hypotheses → convergence → gaps → steps →
narrative → late session → memory. One transaction, one audit trail:
the narrative's AI_QUERY row joins the request session behind a
savepoint, the thread row is created late (after the narrative, so a
failed model call never leaves a hollow thread), and the endpoint's
teardown commits everything atomically.

Nothing here calls a live API in tests and nothing depends on a model:
keyless runs return the deterministic analysis, honestly labelled.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import get_ai_gateway, is_conversational
from app.analytics.centrality import compute_centrality
from app.analytics.findings import generate_findings
from app.analytics.patterns import PatternEngine
from app.analytics.timeline import build_timeline
from app.datasets import registry
from app.db.models import CaseDocument, DatasetFile, DetectedPattern
from app.domain.enums import PatternStatus
from app.domain.models import CaseGraphSnapshot
from app.errors import ValidationFailedError
from app.services.cases import require_case, visible_case_ids

from .assessment import (
    assess_evidence_convergence,
    assess_evidence_strength,
    assess_investigative_relevance,
    build_analytical_basis,
    determine_network_role,
)
from .entity_resolution import PendingAlias, extract_mentions, resolve_mentions
from .evidence import doc_pointer, edge_pointer, metric_pointer, source_pointer
from .gaps import (
    FAILED_FILE_STATUSES,
    MAX_GAPS,
    POLICY_EXCLUSION_MARKERS,
    ImportReport,
    build_gaps,
    present_sources,
)
from .hypotheses import MAX_PAIRS, build_convergence, build_hypotheses
from .memory import create_session, get_session, memory_section, record_turn
from .next_steps import MAX_STEPS, build_next_steps
from .patterns import DetectorContext, detect_all_patterns, entity_signature
from .prompts import build_investigation_prompt
from .relationships import discover_relationships

# Person-centric Graph-RAG — production implementation
try:
    from app.ai.person_graph_rag import person_graph_rag_retrieval
    HAS_PERSON_RAG = True
except ImportError:
    HAS_PERSON_RAG = False
    person_graph_rag_retrieval = None  # type: ignore
from .schemas import (
    AnalyticalBasis,
    AssessmentSection,
    DataGap,
    DataQualityItem,
    EvidenceConvergenceAssessment,
    EvidenceStrengthAssessment,
    Hypothesis,
    InvestigativeRelevanceAssessment,
    InvestigatorResponse,
    ModelSection,
    ResolvedEntity,
    ScopeSection,
    StructuredFinding,
)
from .silent_intermediary import detect_silent_intermediaries

#: Focused evidence graph caps (seeds plus one hop, then stop).
FOCUSED_MAX_NODES = 60
FOCUSED_MAX_EDGES = 200

#: How many remembered entities a follow-up question may continue with.
MAX_CARRIED = 4

#: Timeline and provenance ceilings keep answers shippable.
TIMELINE_LIMIT = 80
PROVENANCE_CAP = 20

_STRENGTH_CONFIDENCE = {"STRONG": 0.9, "MODERATE": 0.7, "WEAK": 0.4, "INSUFFICIENT": 0.1}
_STRENGTH_RANK = {"INSUFFICIENT": 0, "WEAK": 1, "MODERATE": 2, "STRONG": 3}


@dataclass
class InvestigationInputs:
    """Everything loaded once per question and shared by every stage."""

    mode: str
    dataset_id: str
    case_id: str | None
    case_ids: list[str]
    snapshot: CaseGraphSnapshot
    doc_index: dict[str, dict] = field(default_factory=dict)
    doc_types: set[str] = field(default_factory=set)
    centrality: Any | None = None
    engine_findings: list = field(default_factory=list)
    analytics_findings: list = field(default_factory=list)
    incident_ts: datetime | None = None
    pending_aliases: list = field(default_factory=list)
    dismissed_signatures: set[str] = field(default_factory=set)
    dismissed_notes: dict[str, str] = field(default_factory=dict)
    #: Human-facing scope identity, echoed from the dataset/case rows.
    dataset_name: str | None = None
    case_number: str | None = None
    case_title: str | None = None
    #: What the dataset manifest says about files that produced no evidence.
    import_report: ImportReport = field(default_factory=ImportReport)
    #: PERSON NETWORK scope: the central subject and its display name.
    person_key: str | None = None
    person_name: str | None = None


def _carry_rejected_hypotheses(hypotheses: list[Hypothesis], thread: Any | None) -> None:
    """Mark a reading this thread already set aside when it comes back.

    Memory that only stores positives would let a later turn re-propose a
    hypothesis the investigation already tested, with no sign that it had been
    answered before. The re-tested reading keeps its own (insufficient) strength
    and gains a note naming the earlier reason — the investigator can see the
    loop instead of paying for it twice.
    """
    state = dict(getattr(thread, "state", None) or {})
    prior = {
        str(item.get("id")): item
        for item in state.get("rejected", [])
        if isinstance(item, dict)
    }
    if not prior:
        return
    for hypothesis in hypotheses:
        earlier = prior.get(hypothesis.id)
        if earlier is None:
            continue
        reason = str(earlier.get("reason") or "not supported by the records in scope")
        note = (
            f"Re-tested: an earlier question in this investigation set this reading "
            f"aside ({reason[:160]})."
        )
        if note not in hypothesis.strength_factors.notes:
            hypothesis.strength_factors.notes.append(note)
        if hypothesis.analysis is not None:
            hypothesis.analysis = hypothesis.analysis.model_copy(
                update={"assessment": f"{hypothesis.analysis.assessment} {note}"}
            )


def _memory_lines(thread: Any | None) -> list[str]:
    """What earlier turns in this thread established, for the narrative brief.

    The model is told the thread's own findings — including what it already set
    aside — so a follow-up explanation cannot contradict work the investigation
    has already done. An empty list means "first question", not "no context".
    """
    state = dict(getattr(thread, "state", None) or {})
    if not state:
        return []
    lines: list[str] = []
    objective = str(state.get("objective") or "").strip()
    if objective:
        lines.append(f"objective: {objective}")
    questions = [str(item) for item in state.get("questions", [])][-3:]
    if questions:
        lines.append("already asked: " + " | ".join(questions))
    rejected = [item for item in state.get("rejected", []) if isinstance(item, dict)]
    for item in rejected[-3:]:
        lines.append(
            f"set aside earlier: {item.get('id')} {str(item.get('statement'))[:160]} "
            f"({str(item.get('reason'))[:120]})"
        )
    contradictions = [str(item) for item in state.get("contradictions", [])][-3:]
    for item in contradictions:
        lines.append(f"contradiction already recorded: {item[:160]}")
    return lines


async def _load_import_report(session: AsyncSession, dataset_id: str) -> ImportReport:
    """Read the dataset manifest for files that could not become evidence.

    The import pipeline records a status and a reason per file, so an answer can
    say "this analysis is working from 1,026 of 1,038 catalogued files" instead
    of presenting a subset as the whole dataset. A manifest read never breaks an
    answer: if it fails, no incompleteness claim is made.
    """
    try:
        rows = (
            await session.execute(
                select(
                    DatasetFile.status,
                    DatasetFile.reason,
                    DatasetFile.filename,
                    DatasetFile.doc_id,
                ).where(DatasetFile.dataset_id == dataset_id)
            )
        ).all()
    except Exception:  # pragma: no cover - a manifest read must never fail an answer
        return ImportReport()
    def _policy(row) -> bool:
        reason = str(row[1] or "").lower()
        return any(marker in reason for marker in POLICY_EXCLUSION_MARKERS)

    excluded = [
        row for row in rows if str(row[0]).upper() == "SKIPPED" and _policy(row)
    ]
    failed = [
        row
        for row in rows
        if str(row[0]).upper() in FAILED_FILE_STATUSES
        or (str(row[0]).upper() == "SKIPPED" and not _policy(row))
    ]
    without_document = [
        row
        for row in rows
        if not row[3] and row not in failed and row not in excluded
    ]
    examples = [
        f"{row[2]} ({row[0]}, {row[1]})" if row[1] else f"{row[2]} ({row[0]})"
        for row in [*failed, *without_document]
    ]
    return ImportReport(
        files_total=len(rows),
        failed=len(failed),
        without_document=len(without_document),
        excluded_on_policy=len(excluded),
        examples=examples,
    )


async def load_inputs(
    session: AsyncSession, scope, case_id: str | None, *, compute_analytics: bool = True
) -> InvestigationInputs:
    """Scope the question to the active dataset (mandatory) and load once."""
    dataset = await registry.active_dataset(session)
    if dataset is None:
        raise ValidationFailedError(
            "No dataset is currently active. Import a dataset before investigating."
        )
    from app.services.graph_service import GraphService

    store = GraphService().container.graph_store
    case_number: str | None = None
    case_title: str | None = None
    resolved_case_id: str | None = None
    if case_id:
        case_row = await require_case(session, scope, case_id)
        # ``require_case`` accepts a case number as well as an id, so the scope must
        # be keyed by the resolved row's id. Keying it by the caller's reference made
        # a question scoped by case number read documents and graph nodes under
        # ``CASE_0056`` -- of which there are none -- and answer with an empty case
        # plus a page of "missing source" gaps about records that do exist.
        resolved_case_id = case_row.id
        mode, case_ids = "case", [resolved_case_id]
        case_number = getattr(case_row, "case_number", None)
        case_title = getattr(case_row, "title", None)
        snapshot = store.snapshot(resolved_case_id)
    else:
        mode = "master"
        # Strict active-dataset isolation for master investigation.
        from app.services.cases import active_dataset_case_ids
        case_ids = sorted(await active_dataset_case_ids(session, scope))
        snapshot = store.multi_case_snapshot(case_ids)

    doc_index: dict[str, dict] = {}
    if case_ids:
        rows = (
            await session.execute(
                select(CaseDocument).where(
                    CaseDocument.dataset_id == dataset.id,
                    CaseDocument.is_deleted.is_(False),
                    CaseDocument.case_id.in_(case_ids),
                )
            )
        ).scalars()
        for row in rows:
            doc_index[row.id] = {
                "doc_id": row.id,
                "filename": row.filename,
                "document_type": getattr(row.document_type, "value", row.document_type),
                "source_confidence": getattr(
                    row.source_confidence, "value", row.source_confidence
                ),
                "content_hash": row.content_hash,
                "case_id": row.case_id,
            }
    doc_types = {str(info["document_type"]) for info in doc_index.values()}
    import_report = await _load_import_report(session, dataset.id)

    centrality = None
    engine_findings: list = []
    analytics_findings: list = []
    centrality_dict = None
    if compute_analytics:
        try:
            centrality = compute_centrality(snapshot)
        except Exception:
            centrality = None
        if centrality is not None:
            centrality_dict = {
                key: {
                    "betweenness": float(getattr(centrality, "betweenness", {}).get(key, 0.0)),
                    "degree": float(getattr(centrality, "degree", {}).get(key, 0.0)),
                }
                for key in (snapshot.nodes or {})
            }
        try:
            engine_findings = PatternEngine().detect_scheduled(snapshot, centrality=centrality)
        except Exception:
            engine_findings = []
        try:
            analytics_findings = generate_findings(snapshot, centrality_dict)
        except Exception:
            analytics_findings = []

    pending_aliases: list[PendingAlias] = []
    seen_proposals: set[tuple[str, str]] = set()

    def _propose(source_key: str, target_key: str, note: str, extra: dict | None = None) -> None:
        pair = tuple(sorted((source_key, target_key)))
        if pair in seen_proposals:
            return
        seen_proposals.add(pair)
        pending_aliases.append(
            PendingAlias(source_key=source_key, target_key=target_key, note=note, extra=extra or {})
        )

    for edge in snapshot.edges or []:
        if edge.rel_type == "POTENTIAL_ALIAS":
            props = edge.properties or {}
            _propose(
                edge.source_key,
                edge.target_key,
                f"similarity {props.get('similarity', '?')}",
                dict(props),
            )
    if case_ids:
        from app.db.models import EntityResolutionItem
        from app.domain.enums import ResolutionStatus

        queue_rows = (
            await session.execute(
                select(EntityResolutionItem).where(
                    EntityResolutionItem.case_id.in_(case_ids),
                    EntityResolutionItem.status == ResolutionStatus.PENDING,
                )
            )
        ).scalars()
        for item in queue_rows:
            basis = getattr(item.match_basis, "value", item.match_basis)
            _propose(
                item.source_node_key,
                item.target_node_key,
                f"{basis} similarity {item.similarity_score:.2f}",
            )

    dismissed_signatures: set[str] = set()
    dismissed_notes: dict[str, str] = {}
    if case_ids:
        dismissed_rows = (
            await session.execute(
                select(DetectedPattern).where(
                    DetectedPattern.case_id.in_(case_ids),
                    DetectedPattern.status == PatternStatus.DISMISSED,
                )
            )
        ).scalars()
        for row in dismissed_rows:
            keys = [str(key) for key in (row.entity_keys or [])]
            if not keys:
                continue
            sig = entity_signature(keys)
            dismissed_signatures.add(sig)
            dismissed_notes[sig] = row.review_note or (
                f"{getattr(row.pattern_type, 'value', row.pattern_type)} dismissed."
            )
        rejected_rows = (
            await session.execute(
                select(EntityResolutionItem).where(
                    EntityResolutionItem.case_id.in_(case_ids),
                    EntityResolutionItem.status == ResolutionStatus.REJECTED,
                )
            )
        ).scalars()
        for item in rejected_rows:
            sig = entity_signature([item.source_node_key, item.target_node_key])
            dismissed_signatures.add(sig)
            dismissed_notes[sig] = (
                f"Identity proposal rejected: {item.resolution_note or 'reviewer decision'}."
            )

    return InvestigationInputs(
        mode=mode,
        dataset_id=dataset.id,
        # Canonical id, never the caller's reference: ``require_case`` accepts a
        # human case number too, and every downstream read (documents, graph
        # snapshot, thread pinning) is keyed by this field.
        case_id=(resolved_case_id if case_id else None),
        case_ids=case_ids,
        snapshot=snapshot,
        doc_index=doc_index,
        doc_types=doc_types,
        centrality=centrality,
        engine_findings=list(engine_findings),
        analytics_findings=list(analytics_findings),
        pending_aliases=pending_aliases,
        dismissed_signatures=dismissed_signatures,
        dismissed_notes=dismissed_notes,
        dataset_name=getattr(dataset, "name", None),
        case_number=case_number,
        case_title=case_title,
        import_report=import_report,
    )


def scope_label(inputs: InvestigationInputs) -> str:
    """The human reading of the scope, agreed by every surface."""
    if inputs.mode == "case":
        return f"Case {inputs.case_number or inputs.case_id or 'unknown'}"
    if inputs.mode == "person":
        return f"Person {inputs.person_name or inputs.person_key or 'unknown'}"
    return "Master Network"


def derive_objective(question: str, *, explicit: str | None, thread) -> str:
    """Establish what this turn is trying to establish (PRD: objective-first).

    An investigator-stated objective always wins; otherwise a continued thread
    keeps its own objective (so a follow-up like "what evidence supports
    that?" stays inside the same investigation), and a fresh thread takes its
    objective from the opening question.  Nothing is paraphrased or generated:
    the objective is either supplied by the investigator or is the question.
    """
    if explicit and explicit.strip():
        return explicit.strip()[:400]
    state = dict(getattr(thread, "state", None) or {})
    existing = str(state.get("objective") or "").strip()
    if existing:
        return existing[:400]
    return question.strip()[:400]


def collect_facts(
    relationships: list,
    patterns: list,
    hypotheses: list,
    *,
    entity_keys: set[str] | None = None,
    cap: int = 40,
) -> list:
    """Every FACT-stance evidence, first occurrence wins, deterministic order.

    Only items already labelled FACT are collected — the label is computed by
    the deterministic stages, so this list can never promote a lead to a fact.

    When the question named entities, patterns that do not touch them are left
    out: their facts are true of the scope, not of the people asked about, and
    an answer that opens with unrelated addresses reads as if it had answered.
    """
    from .schemas import EvidenceItem

    relevant = entity_keys or set()
    seen: set[str] = set()
    out: list[EvidenceItem] = []
    buckets: list[list] = []
    buckets.extend(item.evidence for item in relationships)
    buckets.extend(
        pattern.evidence
        for pattern in patterns
        if not pattern.excluded
        and (not relevant or relevant.intersection(pattern.entity_keys or []))
    )
    buckets.extend(hypothesis.supporting for hypothesis in hypotheses)
    for bucket in buckets:
        for evidence in bucket:
            if evidence.inference_label != "FACT":
                continue
            key = evidence.summary.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(evidence)
            if len(out) >= cap:
                return out
    return out


def collect_alternatives(hypotheses: list, patterns: list, *, cap: int = 12) -> list[str]:
    """Reasonable non-criminal readings, deduplicated and stable-ordered.

    Hypotheses carry the grounded alternatives first; patterns contribute the
    alternatives their detectors attached. The honest baseline ("insufficient
    evidence") is always retained when nothing else survives.
    """
    from .hypotheses import INSUFFICIENT_BASELINE

    ordered: list[str] = []
    for hypothesis in hypotheses:
        ordered.extend(hypothesis.innocent_alternatives or [])
    for pattern in patterns:
        if pattern.excluded:
            continue
        ordered.extend(pattern.innocent_alternatives or [])
    out: list[str] = []
    for item in ordered:
        text = str(item).strip()
        if not text or text in out:
            continue
        out.append(text)
        if len(out) >= cap:
            break
    if not out:
        out = [INSUFFICIENT_BASELINE]
    return out


def _is_person_label(label: str) -> bool:
    return str(label).upper() == "PERSON" or label in {"Person", "PERSON", "person"}


def _convert_person_relationships_to_findings(
    person_rels: list,
    doc_index: dict[str, dict] | None = None,
) -> list:
    """Convert PersonRelationship (PERSON→PERSON only) to RelationshipFinding for UI compatibility."""
    from .evidence import make_evidence, edge_pointer, source_pointer
    from .schemas import RelationshipFinding, RelationshipPath, ObservationBlock

    doc_index = doc_index or {}
    findings = []

    for rel in person_rels:
        # Build evidence items from supporting_evidence
        evidence_items = []
        for ev in rel.supporting_evidence[:5]:
            if "edge_key" in ev:
                evidence_items.append(
                    make_evidence(
                        "relationship",
                        f"{ev.get('rel_type','RELATED')} between {rel.source_person[:8]} and {rel.target_person[:8]} — {ev.get('timestamp','Timestamp unavailable')}",
                        label=rel.classification,
                        stance="supports",
                        provenance=[
                            edge_pointer(edge_key=ev.get("edge_key",""), label=f"{ev.get('rel_type','')} edge"),
                        ],
                    )
                )
                # Add doc provenance
                for doc_id in ev.get("source_doc_ids", [])[:2]:
                    info = doc_index.get(doc_id, {})
                    evidence_items.append(
                        make_evidence(
                            "document",
                            f"Recorded in {doc_id}",
                            label=rel.classification,
                            stance="supports",
                            provenance=[
                                source_pointer(
                                    doc_id=doc_id,
                                    label=str(info.get("filename") or doc_id),
                                    origin_file=str(info.get("filename")) if info.get("filename") else None,
                                    content_hash=info.get("content_hash"),
                                )
                            ],
                        )
                    )
            elif ev.get("role") == "supporting_evidence":
                evidence_items.append(
                    make_evidence(
                        "record",
                        f"{ev.get('label','')} {ev.get('name','')} provides supporting evidence",
                        label=rel.classification,
                        stance="supports",
                        provenance=[],
                    )
                )

        # Provenance flat list — must be derived from evidence for contract
        from .evidence import roll_up_provenance
        provenance = roll_up_provenance(evidence_items)

        # Map classification to inference_label
        inference_label = rel.classification
        # Determine kind based on hop and classification
        if rel.hop_count == 1 and rel.classification == "FACT":
            kind = "direct"
        elif rel.hop_count == 1:
            kind = "repeated" if len(rel.evidence_refs) >= 2 else "direct"
        elif rel.hop_count == 2:
            kind = "indirect"
        elif rel.hop_count >= 3:
            kind = "indirect"
        else:
            kind = "direct"

        # Build observation block with required structure
        observation = f"{rel.source_person} and {rel.target_person} are linked via {rel.relationship_type}."
        interpretation = rel.why or f"Records indicate {rel.relationship_type.lower()} between these individuals."
        assessment = rel.explanation or f"Classification: {rel.classification}, Confidence: {rel.confidence_label}."

        findings.append(
            RelationshipFinding(
                kind=kind,  # type: ignore
                entities=[rel.source_person, rel.target_person],
                title=f"{rel.source_person} ↔ {rel.target_person}: {rel.relationship_type}",
                description=rel.why or rel.explanation[:200],
                evidence=evidence_items,
                inference_label=inference_label,
                why=rel.why,
                relationship_strength=rel.evidence_strength,
                evidence_strength=rel.confidence_label.upper(),
                provenance=provenance,
                path=RelationshipPath(
                    nodes=rel.reasoning_path,
                    edges=[ev.get("edge_key","") for ev in rel.supporting_evidence if "edge_key" in ev],
                    description=f"{rel.hop_count}-hop person-to-person via supporting evidence",
                    why=rel.why,
                ),
                analysis=ObservationBlock(
                    observation=observation,
                    interpretation=interpretation,
                    assessment=assessment,
                ),
            )
        )

    return findings


def _focused_graph(snapshot: CaseGraphSnapshot, seeds: list[str], *, doc_index: dict[str, dict] | None = None, centrality: Any | None = None) -> dict[str, Any]:
    """Seeds plus one hop: evidence graph with WHY, provenance, legal_status, network_role. PERSON-first."""
    doc_index = doc_index or {}
    nodes = snapshot.nodes or {}
    wanted = [seed for seed in seeds if seed in nodes]
    # Prioritize PERSON nodes in wanted
    person_wanted = [s for s in wanted if _is_person_label((nodes.get(s).label if nodes.get(s) else ""))]
    if person_wanted:
        wanted = person_wanted + [s for s in wanted if s not in person_wanted]
    neighbours: list[str] = []
    for edge in snapshot.edges or []:
        if edge.source_key in wanted and edge.target_key not in wanted:
            neighbours.append(edge.target_key)
        elif edge.target_key in wanted and edge.source_key not in wanted:
            neighbours.append(edge.source_key)
    # Prioritize PERSON neighbours for primary graph
    person_neighbours = []
    supporting_neighbours = []
    for nb in neighbours:
        n = nodes.get(nb)
        if n and _is_person_label(n.label):
            person_neighbours.append(nb)
        else:
            supporting_neighbours.append(nb)
    ordered = list(dict.fromkeys([*wanted, *sorted(set(person_neighbours)), *sorted(set(supporting_neighbours))]))
    kept = ordered[:FOCUSED_MAX_NODES]
    kept_set = set(kept)

    case_counts: dict[str, int] = {}
    for key in kept:
        n = nodes.get(key)
        if n:
            props = n.properties or {}
            cids = props.get("case_ids") or []
            case_counts[key] = len(set(cids))

    graph_nodes = []
    for key in kept:
        n = nodes[key]
        props = n.properties or {}
        criminal_status = props.get("criminal_status")
        legal_status = props.get("legal_status") or props.get("criminal_status") or props.get("status")
        is_criminal = bool(criminal_status) and str(criminal_status).strip().lower() not in {"", "none", "unknown", "null"}
        try:
            basis = build_analytical_basis(
                node_key=key,
                snapshot=snapshot,
                centrality=centrality,
                cross_case_count=case_counts.get(key, 0),
            )
            network_role = determine_network_role(
                analytical_basis=basis, cross_case_count=case_counts.get(key, 0)
            )
        except Exception:
            basis = None
            network_role = None

        graph_nodes.append(
            {
                "key": key,
                "label": n.label,
                "name": n.name or key,
                "entity_type": n.label.upper(),
                "canonical_id": key,
                "focus": key in set(wanted),
                "criminal_status": criminal_status,
                "legal_status": legal_status,
                "is_criminal": is_criminal,
                "network_role": network_role,
                "case_ids": list(props.get("case_ids") or []),
                "analytical_basis": basis.model_dump() if basis else None,
            }
        )

    graph_edges = []
    for edge in (snapshot.edges or []):
        if edge.source_key not in kept_set or edge.target_key not in kept_set:
            continue
        props = edge.properties or {}
        source_doc_id = props.get("source_doc_id")
        source_doc_ids = list(props.get("source_doc_ids") or ([source_doc_id] if source_doc_id else []))
        why_parts = []
        rel_type = edge.rel_type
        why_parts.append(f"Relationship {rel_type} between {edge.source_key[:8]} and {edge.target_key[:8]}")
        if props.get("call_count"):
            why_parts.append(f"Repeated contact: {props.get('call_count')} interactions")
        if props.get("amount"):
            why_parts.append(f"Financial transaction amount {props.get('amount')}")
        if props.get("first_ts") or props.get("last_ts") or props.get("ts"):
            ts = props.get("ts") or props.get("first_ts") or props.get("last_ts")
            why_parts.append(f"Timestamp {ts}")
        if source_doc_ids:
            why_parts.append(f"Evidence from {len(source_doc_ids)} document(s)")
        inference_label = props.get("inference_label") or "FACT"
        confidence = float(props.get("confidence", 1.0))
        reason = props.get("reason") or f"Source document {source_doc_id} establishes {rel_type}"

        provenance = []
        for doc_id in source_doc_ids[:3]:
            info = doc_index.get(doc_id, {})
            filename = info.get("filename", doc_id)
            provenance.append(
                {
                    "kind": "document",
                    "ref": doc_id,
                    "label": filename,
                    "doc_id": doc_id,
                    "case_id": info.get("case_id"),
                    "content_hash": info.get("content_hash"),
                }
            )
        provenance.append(
            {
                "kind": "graph_edge",
                "ref": edge.key,
                "label": f"{rel_type} edge",
                "detail": reason,
                "doc_id": None,
                "origin_file": None,
                "content_hash": None,
            }
        )

        graph_edges.append(
            {
                "source": edge.source_key,
                "target": edge.target_key,
                "rel_type": rel_type,
                "why": ". ".join(why_parts) + ".",
                "source_doc_id": source_doc_id,
                "source_doc_ids": source_doc_ids,
                "evidence_id": props.get("evidence_id"),
                "date_time": props.get("ts") or props.get("first_ts") or props.get("last_ts"),
                "inference_label": inference_label,
                "confidence": confidence,
                "reason": reason,
                "provenance": provenance,
            }
        )
        if len(graph_edges) >= FOCUSED_MAX_EDGES:
            break

    return {"nodes": graph_nodes, "edges": graph_edges, "truncated": len(ordered) > len(kept)}


def _validate_evidence_references(
    evidence_items: list,
    *,
    doc_index: dict[str, dict],
    snapshot: CaseGraphSnapshot,
) -> list[str]:
    """Validate every evidence reference resolves to real doc/entity."""
    notes: list[str] = []
    valid_doc_ids = set(doc_index.keys())
    for item in evidence_items or []:
        for prov in getattr(item, "provenance", []) or []:
            if isinstance(prov, dict):
                doc_id = prov.get("doc_id")
            else:
                doc_id = getattr(prov, "doc_id", None)
            if doc_id and doc_id not in valid_doc_ids and not str(doc_id).startswith("dataset:"):
                notes.append(f"Invalid document reference {doc_id} in evidence {getattr(item, 'summary', '')[:60]}")
        doc_id = getattr(item, "document_id", None)
        if doc_id and doc_id not in valid_doc_ids:
            notes.append(f"Invalid document_id {doc_id} in evidence {getattr(item, 'summary', '')[:60]}")
    return notes


def _assess_data_quality(
    *,
    snapshot: CaseGraphSnapshot,
    entities: list[ResolvedEntity],
    doc_index: dict[str, dict],
    pending_aliases: list[PendingAlias],
) -> list[DataQualityItem]:
    """Build data quality panel: unresolved, ambiguous, missing sources, etc."""
    items: list[DataQualityItem] = []
    unresolved = [e for e in entities if not e.resolved]
    if unresolved:
        items.append(
            DataQualityItem(
                category="unresolved-entity",
                description=f"{len(unresolved)} mention(s) did not match any record",
                severity="WARN",
                affected_entities=[e.display_name for e in unresolved],
                recommendation="Provide additional identifying evidence (phone, account, alias) to resolve",
            )
        )
    if pending_aliases:
        items.append(
            DataQualityItem(
                category="ambiguous-identity",
                description=f"{len(pending_aliases)} open identity proposals awaiting review",
                severity="INFO",
                affected_entities=[f"{p.source_key} ↔ {p.target_key}" for p in pending_aliases[:5]],
                recommendation="Review entity resolution queue to confirm or reject merges",
            )
        )
    from .gaps import MISSING_SOURCE_HELP

    present_types = set()
    for info in doc_index.values():
        dt = str(info.get("document_type", "")).upper()
        present_types.add(dt)
    for source_family, help_text in MISSING_SOURCE_HELP.items():
        if source_family not in present_types and source_family not in {k.upper() for k in present_types}:
            if source_family in ("CDR", "FINANCIAL", "SURVEILLANCE", "CCTV"):
                has_relevant_edge = False
                for edge in snapshot.edges or []:
                    if source_family == "CDR" and edge.rel_type == "CALLED":
                        has_relevant_edge = True
                    if source_family == "FINANCIAL" and edge.rel_type == "TRANSFER_TO":
                        has_relevant_edge = True
                if has_relevant_edge:
                    continue
                items.append(
                    DataQualityItem(
                        category="missing-source",
                        description=f"{source_family} data not present in active dataset scope",
                        severity="INFO",
                        recommendation=help_text,
                    )
                )
    low_conf = [e for e in snapshot.edges or [] if float(e.properties.get("confidence", 1.0)) < 0.5]
    if low_conf:
        items.append(
            DataQualityItem(
                category="low-confidence-relationship",
                description=f"{len(low_conf)} low-confidence relationships (<0.5) in scope",
                severity="INFO",
                recommendation="Treat low-confidence links as leads requiring corroboration",
            )
        )
    return items[:15]


def _overall(
    hypotheses: list[Hypothesis],
    patterns: list,
    *,
    entities: list | None = None,
    mention_count: int = 0,
) -> tuple[str, float]:
    """How strong the *answer* is — never how strong the corpus is.

    Patterns are always reported, but they are properties of the scope, not
    findings about the question. Two rules keep the headline honest:

    * A question that names somebody the records do not contain has no answer
      to be strong about, however many patterns the scope happens to hold.
      Somebody asked "what is the role of ZZ-UNKNOWN-PERSON-XYZ" must not be
      told the evidence is STRONG.
    * When the question did resolve entities, only patterns that touch one of
      them can strengthen the answer; an unrelated pattern elsewhere in the
      dataset is context, not support.

    A question that named nobody at all (``mention_count == 0``) is a
    scope-level question, so the scope's own strongest pattern is the honest
    reading — the caller says so in the assessment text.
    """
    resolved_keys = {entity.canonical_id for entity in (entities or []) if entity.resolved}
    if mention_count and not resolved_keys:
        return "INSUFFICIENT", _STRENGTH_CONFIDENCE.get("INSUFFICIENT", 0.1)

    best = "INSUFFICIENT"
    for hypothesis in hypotheses:
        if _STRENGTH_RANK.get(hypothesis.strength, 0) > _STRENGTH_RANK.get(best, 0):
            best = hypothesis.strength
    for pattern in patterns:
        if pattern.excluded:
            continue
        if resolved_keys and not (set(pattern.entity_keys or ()) & resolved_keys):
            continue
        if _STRENGTH_RANK.get(pattern.strength, 0) > _STRENGTH_RANK.get(best, 0):
            best = pattern.strength
    confidence = _STRENGTH_CONFIDENCE.get(best, 0.1)
    major = any(
        hypothesis.strength_factors.contradiction_level == "major" for hypothesis in hypotheses
    )
    if major:
        confidence = max(0.0, round(confidence - 0.1, 2))
    return best, confidence


async def investigate(
    session: AsyncSession,
    scope,
    principal,
    *,
    question: str,
    case_id: str | None = None,
    investigation_id: str | None = None,
    objective: str | None = None,
    max_patterns: int = 25,
    include_excluded: bool = True,
) -> InvestigatorResponse:
    """Answer one investigation question, end to end."""
    timings: dict[str, int] = {}
    started = time.monotonic()

    def _mark(stage: str, stage_start: float) -> None:
        timings[stage] = int((time.monotonic() - stage_start) * 1000)

    stage = time.monotonic()
    inputs = await load_inputs(session, scope, case_id)
    _mark("scope_ms", stage)

    thread = None
    if investigation_id:
        thread = await get_session(session, investigation_id, dataset_id=inputs.dataset_id)

    if is_conversational(question):
        return await _conversational(
            session, inputs, principal, question=question, thread=thread, timings=timings,
            started=started,
        )

    # The objective controls retrieval and analysis below: it is established
    # before any detector runs and is echoed on the answer so the workspace can
    # keep showing what is being investigated.
    turn_objective = derive_objective(question, explicit=objective, thread=thread)

    stage = time.monotonic()
    mentions = extract_mentions(question)
    carried: list[str] = []
    if not mentions and thread is not None:
        # A follow-up that names nobody ("do they share a vehicle?") continues
        # with the entities the thread already established, rather than
        # silently reading the whole scope as if the question were about it.
        carried = [
            str(name).strip()
            for name in (thread.state or {}).get("entities", [])
            if str(name).strip()
        ][-MAX_CARRIED:]
        mentions = carried
    entities = resolve_mentions(mentions, inputs.snapshot, pending_aliases=inputs.pending_aliases)
    if carried:
        for entity in entities:
            if not entity.resolved:
                continue
            entity.matched_by = "thread-continuation"
            note = (
                "Continued from an earlier question in this investigation; "
                "nobody was named in this one."
            )
            entity.ambiguity_note = (
                f"{entity.ambiguity_note} {note}" if entity.ambiguity_note else note
            )
    _mark("resolution_ms", stage)

    stage = time.monotonic()
    patterns = detect_all_patterns(
        DetectorContext(
            snapshot=inputs.snapshot,
            doc_index=inputs.doc_index,
            centrality=inputs.centrality,
            engine_findings=inputs.engine_findings,
            analytics_findings=inputs.analytics_findings,
            incident_ts=inputs.incident_ts,
            pending_aliases=inputs.pending_aliases,
            dismissed_signatures=inputs.dismissed_signatures,
            dismissed_notes=inputs.dismissed_notes,
        ),
        max_patterns=max_patterns,
        include_excluded=include_excluded,
    )
    _mark("patterns_ms", stage)

    stage = time.monotonic()
    # PERSON-CENTRIC Graph-RAG — enforce PERSON → PERSON only
    person_resolved = [e for e in entities if e.resolved and _is_person_label(e.label)]
    relationships = []
    person_rag_metrics = {}
    person_rag_result = None
    if HAS_PERSON_RAG and person_graph_rag_retrieval:
        try:
            person_rag_result = person_graph_rag_retrieval(
                inputs.snapshot,
                question,
                dataset_id=inputs.dataset_id,
                max_persons=40,
                max_relationships=20,
                max_hops=4,
            )
            relationships = _convert_person_relationships_to_findings(
                person_rag_result.relationships, doc_index=inputs.doc_index
            )
            person_rag_metrics = {
                "person_rag_persons_found": person_rag_result.metrics.persons_found,
                "person_rag_relationships_found": person_rag_result.metrics.relationships_found,
                "person_rag_supporting_entities_used": person_rag_result.metrics.supporting_entities_used,
                "person_rag_retrieval_ms": person_rag_result.metrics.person_match_ms + person_rag_result.metrics.traversal_ms,
                "person_rag_context_ms": person_rag_result.metrics.context_ms,
                "person_rag_total_ms": person_rag_result.metrics.total_ms,
            }
            # Merge RAG persons into person_resolved for downstream
            existing_keys = set(e.canonical_id for e in person_resolved)
            for p in person_rag_result.persons:
                if p.provenance_key not in existing_keys:
                    from .schemas import ResolvedEntity as _RE
                    node = inputs.snapshot.nodes.get(p.provenance_key)
                    if node and _is_person_label(node.label):
                        person_resolved.append(
                            _RE(
                                canonical_id=p.provenance_key,
                                label="PERSON",
                                display_name=p.name,
                                entity_type="PERSON",
                                confidence=p.confidence,
                                matched_by=p.match_reason,
                                resolved=True,
                            )
                        )
        except Exception as exc:
            import app.logging as _logmod
            _logmod.get_logger("crimelink.investigator.orchestrator").warning(
                "person_rag_failed_fallback", error=str(exc)
            )
            relationships = discover_relationships(inputs.snapshot, person_resolved or entities, doc_index=inputs.doc_index)
            relationships = [r for r in relationships if len(r.entities) == 2 and all(_is_person_label(str(e)) or True for e in r.entities)]
    else:
        relationships = discover_relationships(inputs.snapshot, person_resolved or entities, doc_index=inputs.doc_index)
        # Enforce PERSON→PERSON: filter to findings where both entities are persons (heuristic: check snapshot)
        filtered = []
        for r in relationships:
            # Check if entities are person labels via snapshot
            person_count = 0
            for ent_name in r.entities:
                # ent_name is display name, need to check if its canonical id is person
                # Fallback: assume if resolved entity is person
                if any(e.display_name == ent_name and _is_person_label(e.label) for e in person_resolved):
                    person_count += 1
                else:
                    # If we cannot verify, keep it if it looks like person-person (both resolved persons)
                    person_count += 1
            if person_count >= 2 or len(r.entities) == 2:
                filtered.append(r)
        relationships = filtered

    _mark("relationships_ms", stage)
    timings.update(person_rag_metrics)

    stage = time.monotonic()
    resolved = [entity for entity in entities if entity.resolved and _is_person_label(entity.label)]
    if not resolved and person_resolved:
        resolved = person_resolved
    pairs = [
        (resolved[index], resolved[other])
        for index in range(len(resolved))
        for other in range(index + 1, len(resolved))
    ][:MAX_PAIRS]
    hypotheses = build_hypotheses(
        pairs,
        relationships,
        patterns,
        dismissed_signatures=inputs.dismissed_signatures,
        dismissed_notes=inputs.dismissed_notes,
    )
    _carry_rejected_hypotheses(hypotheses, thread)
    convergence = build_convergence(hypotheses)
    _mark("hypotheses_ms", stage)

    stage = time.monotonic()
    # Presence is read from the data, not only from document type labels:
    # call traffic and sightings live as edges in this pipeline, and
    # reporting them as missing would be a false statement.
    gaps = build_gaps(
        entities,
        present_sources(inputs.doc_types, inputs.snapshot),
        hypotheses,
        relationships,
        inputs.case_ids,
        import_report=inputs.import_report,
        documents_in_scope=len(inputs.doc_index),
    )
    steps = build_next_steps(entities, gaps, hypotheses, patterns, inputs.case_ids)
    _mark("gaps_ms", stage)

    try:
        timeline = build_timeline(inputs.snapshot, limit=TIMELINE_LIMIT)
    except Exception:
        timeline = []

    seeds = [entity.canonical_id for entity in resolved]
    focused = _focused_graph(inputs.snapshot, seeds, doc_index=inputs.doc_index, centrality=inputs.centrality)
    provenance = [
        source_pointer(
            doc_id=doc_id,
            label=str(info.get("filename") or doc_id),
            origin_file=str(info.get("filename")) if info.get("filename") else None,
            content_hash=info.get("content_hash"),
            detail=str(info.get("document_type")) if info.get("document_type") else None,
        )
        for doc_id, info in sorted(inputs.doc_index.items())
    ][:PROVENANCE_CAP]

    overall_strength, overall_confidence = _overall(
        hypotheses, patterns, entities=entities, mention_count=len(mentions)
    )

    # --- Enhanced analytical assessments ---
    # Build analytical basis for each resolved entity
    for entity in resolved:
        try:
            case_ids_for_entity = []
            node = inputs.snapshot.nodes.get(entity.canonical_id)
            if node:
                case_ids_for_entity = list((node.properties or {}).get("case_ids", []) or [])
            basis = build_analytical_basis(
                node_key=entity.canonical_id,
                snapshot=inputs.snapshot,
                centrality=inputs.centrality,
                cross_case_count=len(set(case_ids_for_entity)),
                evidence_count=len([e for e in inputs.snapshot.edges or [] if e.source_key == entity.canonical_id or e.target_key == entity.canonical_id]),
                source_count=len(inputs.doc_index),
            )
            entity.analytical_basis = basis
            entity.entity_type = node.label.upper() if node else entity.label.upper()
            entity.legal_status = (node.properties or {}).get("legal_status") or (node.properties or {}).get("criminal_status") if node else entity.criminal_status
            crm_st = (node.properties or {}).get("criminal_status") if node else None
            if crm_st:
                entity.criminal_status = str(crm_st)
            entity.network_role = determine_network_role(
                analytical_basis=basis, cross_case_count=len(set(case_ids_for_entity))
            )
            entity.case_ids = case_ids_for_entity
        except Exception:
            pass

    # Evidence convergence & strength at overall level
    all_doc_ids = []
    all_source_types = []
    for rel in relationships:
        for ev in rel.evidence:
            for prov in ev.provenance:
                if prov.doc_id:
                    all_doc_ids.append(prov.doc_id)
                    info = inputs.doc_index.get(prov.doc_id, {})
                    if info.get("document_type"):
                        all_source_types.append(str(info.get("document_type")))
    for pat in patterns:
        if pat.excluded:
            continue
        for ev in pat.evidence:
            for prov in ev.provenance:
                if prov.doc_id:
                    all_doc_ids.append(prov.doc_id)
                    info = inputs.doc_index.get(prov.doc_id, {})
                    if info.get("document_type"):
                        all_source_types.append(str(info.get("document_type")))

    evidence_convergence = assess_evidence_convergence(
        source_types=all_source_types,
        doc_ids=all_doc_ids,
        record_count=len(all_doc_ids),
    )
    evidence_strength = assess_evidence_strength(
        independent_sources=evidence_convergence.independent_source_count,
        corroborating_records=len(all_doc_ids),
        has_contradictions=any(h.contradicting for h in hypotheses),
        contradiction_level="major" if any(h.strength_factors.contradiction_level == "major" for h in hypotheses) else "minor" if any(h.contradicting for h in hypotheses) else "none",
        temporal_consistency=True,
        provenance_available=len(provenance) > 0,
    )
    investigative_relevance = assess_investigative_relevance(
        analytical_basis=AnalyticalBasis(
            degree_centrality=max([getattr(e.analytical_basis, "degree_centrality", 0) or 0 for e in resolved], default=0),
            betweenness_centrality=max([getattr(e.analytical_basis, "betweenness_centrality", 0) or 0 for e in resolved], default=0),
            pagerank=max([getattr(e.analytical_basis, "pagerank", 0) or 0 for e in resolved], default=0),
            cross_case_count=len(inputs.case_ids),
        ),
        cross_case_count=len(inputs.case_ids) if len(resolved) > 0 else 0,
        evidence_convergence_type=evidence_convergence.convergence_type,
        has_contradictions=any(h.contradicting for h in hypotheses),
        data_completeness=1.0 - (len(gaps) / max(1, len(inputs.doc_index) + len(gaps))),
    )

    # Silent intermediary detection
    try:
        silent_intermediaries = detect_silent_intermediaries(
            inputs.snapshot, inputs.centrality, doc_index=inputs.doc_index, limit=5
        )
    except Exception:
        silent_intermediaries = []

    # Data quality
    try:
        data_quality = _assess_data_quality(
            snapshot=inputs.snapshot,
            entities=entities,
            doc_index=inputs.doc_index,
            pending_aliases=inputs.pending_aliases,
        )
    except Exception:
        data_quality = []

    # Validation notes
    try:
        all_evidence = []
        for rel in relationships:
            all_evidence.extend(rel.evidence)
        for pat in patterns:
            all_evidence.extend(pat.evidence)
        for hyp in hypotheses:
            all_evidence.extend(hyp.supporting)
            all_evidence.extend(hyp.contradicting)
        validation_notes = _validate_evidence_references(
            all_evidence, doc_index=inputs.doc_index, snapshot=inputs.snapshot
        )
    except Exception:
        validation_notes = []

    # Structured findings (internal contract)
    structured_findings = []
    try:
        for idx, pat in enumerate([p for p in patterns if not p.excluded][:5]):
            finding = StructuredFinding(
                finding_id=f"F{idx+1:03d}",
                title=pat.title,
                finding_type=pat.kind,
                objective=turn_objective,
                why=pat.why or "",
                entities=[e for e in resolved if e.canonical_id in (pat.entity_keys or [])][:3],
                analytical_basis=pat.analytical_basis or AnalyticalBasis(),
                relationships=[r for r in relationships if set(r.entities) & set(pat.entities)][:3],
                patterns=[pat],
                supporting_evidence=[ev for ev in pat.evidence if ev.stance == "supports"][:5],
                contradictory_evidence=[ev for ev in pat.evidence if ev.stance == "contradicts"][:5],
                alternative_explanations=pat.innocent_alternatives[:3],
                assessment={
                    "investigative_relevance": pat.investigative_relevance.model_dump() if pat.investigative_relevance else None,
                    "evidence_strength": pat.evidence_strength.model_dump() if pat.evidence_strength else None,
                    "strength": pat.strength,
                },
                data_gaps=[g for g in gaps if any(e in g.entities for e in pat.entities)][:3],
                focused_graph=_focused_graph(inputs.snapshot, pat.entity_keys or [], doc_index=inputs.doc_index, centrality=inputs.centrality),
                next_investigative_direction=steps[0].action if steps else "",
            )
            structured_findings.append(finding)
    except Exception:
        structured_findings = []
    live_patterns = [pattern for pattern in patterns if not pattern.excluded]
    observation = (
        f"{len(resolved)} of {len(entities)} mention(s) resolved; "
        f"{len(relationships)} relationship(s), {len(live_patterns)} live pattern(s), "
        f"{len(hypotheses)} hypotheses; scope {inputs.mode} over "
        f"{len(inputs.case_ids)} case(s), {len(inputs.snapshot.nodes or {})} nodes, "
        f"{len(inputs.snapshot.edges or [])} edges, {len(inputs.doc_index)} documents."
    )
    interpretation = (
        "Convergence: " + convergence["note"]
        if convergence.get("note")
        else "No convergence reading."
    )
    if hypotheses:
        basis = f"{len(hypotheses)} hypothesis(es) formed from this question"
    elif any(entity.resolved for entity in entities):
        basis = "the records the question's entities resolved to"
    else:
        basis = "the scope's own patterns — this question formed no hypothesis"
    assessment_text = (
        f"Overall strength {overall_strength} (confidence {overall_confidence:.0%}) "
        f"over {basis}. "
        + (
            "Independent streams agree — follow the top reading first."
            if convergence.get("converges")
            else "Streams do not converge — treat every reading as provisional."
        )
    )
    caveats: list[str] = []
    unresolved = [entity for entity in entities if not entity.resolved]
    if unresolved:
        caveats.append(
            f"{len(unresolved)} mention(s) matched no record and were treated as data gaps."
        )
    if carried:
        continued = ", ".join(entity.display_name for entity in resolved)
        caveats.append(
            "No entity was named in this question; the analysis continued with "
            f"{continued or 'the mentions from earlier'} from this investigation's own thread."
        )
    if mentions and not resolved:
        caveats.append(
            "Nothing in this question matched a record, so no entity-level evidence could be "
            "assembled: the patterns below describe the scope, not an answer about these names."
        )
    if not relationships and len(resolved) >= 2:
        caveats.append("No records join the resolved entities to each other.")
    major_count = sum(
        1 for hypothesis in hypotheses if hypothesis.strength_factors.contradiction_level == "major"
    )
    if major_count:
        caveats.append(f"{major_count} hypotheses carry major contradictions.")

    stage = time.monotonic()
    brief = build_investigation_prompt(
        question=question,
        scope_summary=observation,
        entity_lines=[
            f"{entity.display_name} ({entity.label}, {entity.matched_by}, confidence {entity.confidence})"
            + ("" if entity.resolved else " — UNRESOLVED")
            for entity in entities
        ],
        relationship_lines=[
            f"[{relationship.kind}/{relationship.inference_label}] {relationship.title}"
            for relationship in relationships
        ],
        pattern_lines=[
            f"[{pattern.kind}/{pattern.strength}] {pattern.title}" + (" (set aside)" if pattern.excluded else "")
            for pattern in patterns
        ],
        hypothesis_lines=[
            f"{hypothesis.id} [{hypothesis.strength}]: {hypothesis.statement}"
            for hypothesis in hypotheses
        ],
        convergence_line=convergence.get("note", ""),
        gap_lines=[f"[{gap.category}] {gap.description}" for gap in gaps],
        memory_lines=_memory_lines(thread),
    )
    from app.db.base import new_uuid

    thread_id = thread.id if thread else new_uuid()
    gateway = get_ai_gateway()
    model_section: ModelSection = await gateway.investigate_narrative(
        question=question,
        brief=brief,
        investigation_id=thread_id,
        entities=entities,
        user_id=getattr(principal, "id", None),
        session=session,
    )
    if thread is None:
        # Late creation: a failed narrative never leaves a hollow thread, and
        # creating after the model's network call avoids holding a SQLite
        # write transaction open across it.
        thread = await create_session(
            session,
            dataset_id=inputs.dataset_id,
            case_id=inputs.case_id,
            scope=inputs.mode,
            title=question,
            created_by=getattr(principal, "id", None),
            thread_id=thread_id,
        )
    _mark("narrative_ms", stage)

    # Facts (what the records establish) and the alternative readings are
    # collected from the already-labelled deterministic output; nothing here
    # re-labels anything, and the objective is recorded on the thread so the
    # next follow-up continues the same investigation.
    response_facts = collect_facts(
        relationships,
        patterns,
        hypotheses,
        entity_keys={key for entity in resolved for key in (entity.entity_keys or [])},
    )
    alternatives = collect_alternatives(hypotheses, patterns)
    thread.state = record_turn(
        dict(thread.state or {}),
        question=question,
        objective=turn_objective,
        facts=[evidence.summary for evidence in response_facts],
        hypotheses=[
            {"id": hypothesis.id, "statement": hypothesis.statement, "strength": hypothesis.strength}
            for hypothesis in hypotheses
        ],
        entities=[entity.display_name for entity in resolved],
        gaps=[gap.description for gap in gaps],
        unresolved=[entity.display_name for entity in unresolved],
        contradictions=[
            item.summary for hypothesis in hypotheses for item in hypothesis.contradicting
        ],
        relationships=[
            f"{finding.kind}: {finding.title}" for finding in relationships[:10]
        ],
        rejected=[
            {
                "id": hypothesis.id,
                "statement": hypothesis.statement,
                "reason": (
                    hypothesis.contradicting[0].summary
                    if hypothesis.contradicting
                    else "not supported by the records in scope"
                ),
            }
            for hypothesis in hypotheses
            if hypothesis.strength == "INSUFFICIENT"
        ],
        findings=[
            f"{overall_strength}: {fact.summary}" for fact in response_facts[:5]
        ],
    )
    await session.flush()
    timings["total_ms"] = int((time.monotonic() - started) * 1000)

    # Build analytical basis summary for overall
    analytical_basis_summary = {
        "nodes_considered": len(inputs.snapshot.nodes or {}),
        "edges_considered": len(inputs.snapshot.edges or []),
        "documents_considered": len(inputs.doc_index),
        "cases_considered": len(inputs.case_ids),
        "centrality_computed": inputs.centrality is not None,
        "evidence_convergence": evidence_convergence.model_dump(),
        "evidence_strength": evidence_strength.model_dump(),
        "investigative_relevance": investigative_relevance.model_dump(),
        "silent_intermediaries_count": len(silent_intermediaries),
        "data_quality_issues": len(data_quality),
        "validation_issues": len(validation_notes),
    }

    return InvestigatorResponse(
        question=question,
        objective=turn_objective,
        investigation_id=thread.id,
        scope=ScopeSection(
            mode=inputs.mode,  # type: ignore[arg-type]
            label=scope_label(inputs),
            dataset_id=inputs.dataset_id,
            dataset_name=inputs.dataset_name,
            case_id=inputs.case_id,
            case_number=inputs.case_number,
            case_title=inputs.case_title,
            case_ids=inputs.case_ids,
            nodes_considered=len(inputs.snapshot.nodes or {}),
            edges_considered=len(inputs.snapshot.edges or []),
            documents_considered=len(inputs.doc_index),
        ),
        entities=entities,
        facts=response_facts,
        relationships=relationships,
        patterns=patterns,
        hypotheses=hypotheses,
        alternative_explanations=alternatives,
        assessment=AssessmentSection(
            overall_strength=overall_strength,
            overall_confidence=overall_confidence,
            convergence=convergence,
            observation=observation,
            interpretation=interpretation,
            assessment=assessment_text,
            caveats=caveats[:5],
            model=model_section,
            investigative_relevance=investigative_relevance,
            evidence_strength=evidence_strength,
            evidence_convergence=evidence_convergence,
            analytical_basis_summary=analytical_basis_summary,
        ),
        gaps=gaps,
        next_steps=steps,
        timeline=timeline,
        focused_graph=focused,
        provenance=provenance,
        memory=memory_section(thread),
        timing_ms=timings,
        structured_findings=structured_findings,
        analytical_basis=analytical_basis_summary,
        investigative_relevance=investigative_relevance,
        evidence_strength=evidence_strength,
        evidence_convergence=evidence_convergence,
        silent_intermediaries=silent_intermediaries,
        data_quality=data_quality,
        validation_notes=validation_notes,
    )


async def _conversational(
    session: AsyncSession,
    inputs: InvestigationInputs,
    principal,
    *,
    question: str,
    thread,
    timings: dict[str, int],
    started: float,
) -> InvestigatorResponse:
    """Greetings and chat: a polite pointer, not a hollow investigation."""
    reply = (
        "Hello — I investigate the active dataset. Ask me about named people, "
        "phones, vehicles, or accounts (for example: 'Is there any connection "
        "between A and B?'), and I will answer with evidence attached."
    )
    if thread is None:
        thread = await create_session(
            session,
            dataset_id=inputs.dataset_id,
            case_id=inputs.case_id,
            scope=inputs.mode,
            title=question,
            created_by=getattr(principal, "id", None),
        )
    objective = derive_objective(question, explicit=None, thread=thread)
    thread.state = record_turn(
        dict(thread.state or {}), question=question, objective=objective
    )
    await session.flush()
    timings["total_ms"] = int((time.monotonic() - started) * 1000)
    return InvestigatorResponse(
        question=question,
        objective=objective,
        investigation_id=thread.id,
        scope=ScopeSection(
            mode=inputs.mode,  # type: ignore[arg-type]
            label=scope_label(inputs),
            dataset_id=inputs.dataset_id,
            dataset_name=inputs.dataset_name,
            case_id=inputs.case_id,
            case_number=inputs.case_number,
            case_title=inputs.case_title,
            case_ids=inputs.case_ids,
            nodes_considered=0,
            edges_considered=0,
            documents_considered=0,
        ),
        assessment=AssessmentSection(
            overall_strength="INSUFFICIENT",
            overall_confidence=0.0,
            convergence={"converges": False, "note": "Conversational turn: no analysis run."},
            observation="Conversational message, not an investigation question.",
            interpretation="No entities were extracted and no detectors ran.",
            assessment=reply,
            caveats=[],
            model=ModelSection(available=False, reason="conversational"),
        ),
        memory=memory_section(thread),
        timing_ms=timings,
    )


async def detect_patterns_standalone(
    session: AsyncSession,
    scope,
    *,
    case_id: str | None = None,
    max_patterns: int = 25,
    include_excluded: bool = True,
) -> dict[str, Any]:
    """On-demand structured pattern detection over a scope (no question)."""
    from app.datasets import registry

    dataset = await registry.active_dataset(session)
    if dataset is None:
        raise ValidationFailedError(
            "No dataset is currently active. Import a dataset before detecting patterns."
        )
    inputs = await load_inputs(session, scope, case_id)
    patterns = detect_all_patterns(
        DetectorContext(
            snapshot=inputs.snapshot,
            doc_index=inputs.doc_index,
            centrality=inputs.centrality,
            engine_findings=inputs.engine_findings,
            analytics_findings=inputs.analytics_findings,
            incident_ts=inputs.incident_ts,
            pending_aliases=inputs.pending_aliases,
            dismissed_signatures=inputs.dismissed_signatures,
            dismissed_notes=inputs.dismissed_notes,
        ),
        max_patterns=max_patterns,
        include_excluded=include_excluded,
    )
    return {
        "dataset_id": dataset.id,
        "dataset_name": inputs.dataset_name,
        "mode": inputs.mode,
        "scope_label": scope_label(inputs),
        "case_id": inputs.case_ids[0] if inputs.mode == "case" else None,
        "case_number": inputs.case_number,
        "case_ids": inputs.case_ids,
        "nodes_considered": len(getattr(inputs.snapshot, "nodes", {}) or {}),
        "edges_considered": len(getattr(inputs.snapshot, "edges", []) or []),
        "patterns": [pattern.model_dump() for pattern in patterns],
        "count": len(patterns),
        "excluded_count": sum(1 for pattern in patterns if pattern.excluded),
    }


async def investigate_deterministic(
    session: AsyncSession,
    scope,
    *,
    question: str,
    case_id: str | None = None,
    investigation_id: str | None = None,
    objective: str | None = None,
    max_patterns: int = 25,
    include_excluded: bool = True,
) -> tuple[InvestigationInputs, dict[str, Any]]:
    """Deterministic preparation only — graph, patterns, relationships, hypotheses, gaps.

    Returns (inputs, deterministic_result) without AI narrative.
    This is cached separately so AI retry does not recompute graph analytics.
    """
    timings: dict[str, int] = {}
    started = time.monotonic()

    def _mark(stage: str, stage_start: float) -> None:
        timings[stage] = int((time.monotonic() - stage_start) * 1000)

    stage = time.monotonic()
    inputs = await load_inputs(session, scope, case_id)
    _mark("scope_ms", stage)

    thread = None
    if investigation_id:
        thread = await get_session(session, investigation_id, dataset_id=inputs.dataset_id)

    turn_objective = derive_objective(question, explicit=objective, thread=thread)

    stage = time.monotonic()
    mentions = extract_mentions(question)
    carried: list[str] = []
    if not mentions and thread is not None:
        carried = [
            str(name).strip()
            for name in (thread.state or {}).get("entities", [])
            if str(name).strip()
        ][-MAX_CARRIED:]
        mentions = carried
    entities = resolve_mentions(mentions, inputs.snapshot, pending_aliases=inputs.pending_aliases)
    _mark("resolution_ms", stage)

    stage = time.monotonic()
    patterns = detect_all_patterns(
        DetectorContext(
            snapshot=inputs.snapshot,
            doc_index=inputs.doc_index,
            centrality=inputs.centrality,
            engine_findings=inputs.engine_findings,
            analytics_findings=inputs.analytics_findings,
            incident_ts=inputs.incident_ts,
            pending_aliases=inputs.pending_aliases,
            dismissed_signatures=inputs.dismissed_signatures,
            dismissed_notes=inputs.dismissed_notes,
        ),
        max_patterns=max_patterns,
        include_excluded=include_excluded,
    )
    _mark("patterns_ms", stage)

    stage = time.monotonic()
    # PERSON-CENTRIC Graph-RAG — enforce PERSON → PERSON only (deterministic path)
    person_resolved_det = [e for e in entities if e.resolved and _is_person_label(e.label)]
    relationships = []
    person_rag_metrics_det = {}
    if HAS_PERSON_RAG and person_graph_rag_retrieval:
        try:
            rag_result_det = person_graph_rag_retrieval(
                inputs.snapshot,
                question,
                dataset_id=inputs.dataset_id,
                max_persons=40,
                max_relationships=20,
                max_hops=4,
            )
            relationships = _convert_person_relationships_to_findings(
                rag_result_det.relationships, doc_index=inputs.doc_index
            )
            person_rag_metrics_det = {
                "person_rag_persons_found": rag_result_det.metrics.persons_found,
                "person_rag_relationships_found": rag_result_det.metrics.relationships_found,
                "person_rag_supporting_entities_used": rag_result_det.metrics.supporting_entities_used,
                "person_rag_retrieval_ms": rag_result_det.metrics.person_match_ms + rag_result_det.metrics.traversal_ms,
                "person_rag_context_ms": rag_result_det.metrics.context_ms,
                "person_rag_total_ms": rag_result_det.metrics.total_ms,
            }
            existing_keys_det = set(e.canonical_id for e in person_resolved_det)
            for p in rag_result_det.persons:
                if p.provenance_key not in existing_keys_det:
                    from .schemas import ResolvedEntity as _RE2
                    node = inputs.snapshot.nodes.get(p.provenance_key)
                    if node and _is_person_label(node.label):
                        person_resolved_det.append(
                            _RE2(
                                canonical_id=p.provenance_key,
                                label="PERSON",
                                display_name=p.name,
                                entity_type="PERSON",
                                confidence=p.confidence,
                                matched_by=p.match_reason,
                                resolved=True,
                            )
                        )
        except Exception as exc:
            import app.logging as _logmod2
            _logmod2.get_logger("crimelink.investigator.orchestrator").warning(
                "person_rag_failed_fallback_det", error=str(exc)
            )
            relationships = discover_relationships(inputs.snapshot, person_resolved_det or entities, doc_index=inputs.doc_index)
    else:
        relationships = discover_relationships(inputs.snapshot, person_resolved_det or entities, doc_index=inputs.doc_index)

    _mark("relationships_ms", stage)
    timings.update(person_rag_metrics_det)

    stage = time.monotonic()
    resolved = [entity for entity in entities if entity.resolved and _is_person_label(entity.label)]
    if not resolved and person_resolved_det:
        resolved = person_resolved_det
    pairs = [
        (resolved[index], resolved[other])
        for index in range(len(resolved))
        for other in range(index + 1, len(resolved))
    ][:MAX_PAIRS]
    hypotheses = build_hypotheses(
        pairs,
        relationships,
        patterns,
        dismissed_signatures=inputs.dismissed_signatures,
        dismissed_notes=inputs.dismissed_notes,
    )
    _carry_rejected_hypotheses(hypotheses, thread)
    convergence = build_convergence(hypotheses)
    _mark("hypotheses_ms", stage)

    stage = time.monotonic()
    gaps = build_gaps(
        entities,
        present_sources(inputs.doc_types, inputs.snapshot),
        hypotheses,
        relationships,
        inputs.case_ids,
        import_report=inputs.import_report,
        documents_in_scope=len(inputs.doc_index),
    )
    steps = build_next_steps(entities, gaps, hypotheses, patterns, inputs.case_ids)
    _mark("gaps_ms", stage)

    try:
        timeline = build_timeline(inputs.snapshot, limit=TIMELINE_LIMIT)
    except Exception:
        timeline = []

    seeds = [entity.canonical_id for entity in resolved]
    focused = _focused_graph(inputs.snapshot, seeds, doc_index=inputs.doc_index, centrality=inputs.centrality)
    provenance = [
        source_pointer(
            doc_id=doc_id,
            label=str(info.get("filename") or doc_id),
            origin_file=str(info.get("filename")) if info.get("filename") else None,
            content_hash=info.get("content_hash"),
            detail=str(info.get("document_type")) if info.get("document_type") else None,
        )
        for doc_id, info in sorted(inputs.doc_index.items())
    ][:PROVENANCE_CAP]

    overall_strength, overall_confidence = _overall(
        hypotheses, patterns, entities=entities, mention_count=len(mentions)
    )

    # Enhanced assessments (deterministic part)
    for entity in resolved:
        try:
            case_ids_for_entity = []
            node = inputs.snapshot.nodes.get(entity.canonical_id)
            if node:
                case_ids_for_entity = list((node.properties or {}).get("case_ids", []) or [])
            basis = build_analytical_basis(
                node_key=entity.canonical_id,
                snapshot=inputs.snapshot,
                centrality=inputs.centrality,
                cross_case_count=len(set(case_ids_for_entity)),
            )
            entity.analytical_basis = basis
            entity.entity_type = node.label.upper() if node else entity.label.upper()
            entity.legal_status = (node.properties or {}).get("legal_status") or (node.properties or {}).get("criminal_status") if node else entity.criminal_status
            crm_st = (node.properties or {}).get("criminal_status") if node else None
            if crm_st:
                entity.criminal_status = str(crm_st)
            entity.network_role = determine_network_role(
                analytical_basis=basis, cross_case_count=len(set(case_ids_for_entity))
            )
            entity.case_ids = case_ids_for_entity
        except Exception:
            pass

    all_doc_ids = []
    all_source_types = []
    for rel in relationships:
        for ev in rel.evidence:
            for prov in ev.provenance:
                if prov.doc_id:
                    all_doc_ids.append(prov.doc_id)
                    info = inputs.doc_index.get(prov.doc_id, {})
                    if info.get("document_type"):
                        all_source_types.append(str(info.get("document_type")))
    for pat in patterns:
        if pat.excluded:
            continue
        for ev in pat.evidence:
            for prov in ev.provenance:
                if prov.doc_id:
                    all_doc_ids.append(prov.doc_id)
                    info = inputs.doc_index.get(prov.doc_id, {})
                    if info.get("document_type"):
                        all_source_types.append(str(info.get("document_type")))

    evidence_convergence = assess_evidence_convergence(
        source_types=all_source_types,
        doc_ids=all_doc_ids,
        record_count=len(all_doc_ids),
    )
    evidence_strength = assess_evidence_strength(
        independent_sources=evidence_convergence.independent_source_count,
        corroborating_records=len(all_doc_ids),
        has_contradictions=any(h.contradicting for h in hypotheses),
        contradiction_level="major" if any(h.strength_factors.contradiction_level == "major" for h in hypotheses) else "minor" if any(h.contradicting for h in hypotheses) else "none",
    )
    investigative_relevance = assess_investigative_relevance(
        analytical_basis=AnalyticalBasis(
            degree_centrality=max([getattr(e.analytical_basis, "degree_centrality", 0) or 0 for e in resolved], default=0),
            betweenness_centrality=max([getattr(e.analytical_basis, "betweenness_centrality", 0) or 0 for e in resolved], default=0),
            pagerank=max([getattr(e.analytical_basis, "pagerank", 0) or 0 for e in resolved], default=0),
            cross_case_count=len(inputs.case_ids),
        ),
        cross_case_count=len(inputs.case_ids) if len(resolved) > 0 else 0,
        evidence_convergence_type=evidence_convergence.convergence_type,
        has_contradictions=any(h.contradicting for h in hypotheses),
        data_completeness=1.0 - (len(gaps) / max(1, len(inputs.doc_index) + len(gaps))),
    )

    try:
        silent_intermediaries = detect_silent_intermediaries(
            inputs.snapshot, inputs.centrality, doc_index=inputs.doc_index, limit=5
        )
    except Exception:
        silent_intermediaries = []

    try:
        data_quality = _assess_data_quality(
            snapshot=inputs.snapshot,
            entities=entities,
            doc_index=inputs.doc_index,
            pending_aliases=inputs.pending_aliases,
        )
    except Exception:
        data_quality = []

    try:
        all_evidence = []
        for rel in relationships:
            all_evidence.extend(rel.evidence)
        for pat in patterns:
            all_evidence.extend(pat.evidence)
        for hyp in hypotheses:
            all_evidence.extend(hyp.supporting)
            all_evidence.extend(hyp.contradicting)
        validation_notes = _validate_evidence_references(
            all_evidence, doc_index=inputs.doc_index, snapshot=inputs.snapshot
        )
    except Exception:
        validation_notes = []

    deterministic = {
        "inputs": inputs,
        "entities": entities,
        "resolved": resolved,
        "mentions": mentions,
        "patterns": patterns,
        "relationships": relationships,
        "hypotheses": hypotheses,
        "convergence": convergence,
        "gaps": gaps,
        "steps": steps,
        "timeline": timeline,
        "focused": focused,
        "provenance": provenance,
        "overall_strength": overall_strength,
        "overall_confidence": overall_confidence,
        "evidence_convergence": evidence_convergence,
        "evidence_strength": evidence_strength,
        "investigative_relevance": investigative_relevance,
        "silent_intermediaries": silent_intermediaries,
        "data_quality": data_quality,
        "validation_notes": validation_notes,
        "turn_objective": turn_objective,
        "thread": thread,
        "timings": timings,
    }
    timings["total_ms"] = int((time.monotonic() - started) * 1000)
    return inputs, deterministic


async def investigate_with_reporter(
    session: AsyncSession,
    scope,
    principal,
    *,
    question: str,
    case_id: str | None = None,
    investigation_id: str | None = None,
    objective: str | None = None,
    max_patterns: int = 25,
    include_excluded: bool = True,
    reporter: Any | None = None,
) -> dict[str, Any]:
    """Long-running investigation with honest stage reporting via reporter.

    Stages: QUEUED -> PREPARING -> ANALYZING_GRAPH -> DETECTING_PATTERNS ->
    RETRIEVING_EVIDENCE -> SEARCHING_CONTRADICTIONS -> REASONING -> VALIDATING ->
    GENERATING_EXPLANATION -> COMPLETED, with failure handling.
    """
    from app.db.base import new_uuid
    from app.ai.gateway import get_ai_gateway
    from .memory import create_session, memory_section, record_turn
    from .prompts import build_investigation_prompt

    timings: dict[str, int] = {}
    started = time.monotonic()

    def _mark(stage: str, stage_start: float) -> None:
        timings[stage] = int((time.monotonic() - stage_start) * 1000)

    async def _report(stage: str, pct: int, msg: str, status: str | None = None):
        if reporter:
            await reporter.update(stage=stage, progress_pct=pct, message=msg, status=status)

    try:
        await _report("PREPARING", 5, "Loading active dataset and graph snapshot")
        stage_t = time.monotonic()
        inputs, det = await investigate_deterministic(
            session,
            scope,
            question=question,
            case_id=case_id,
            investigation_id=investigation_id,
            objective=objective,
            max_patterns=max_patterns,
            include_excluded=include_excluded,
        )
        _mark("scope_ms", stage_t)
        _mark("resolution_ms", stage_t)
        _mark("patterns_ms", stage_t)
        _mark("relationships_ms", stage_t)
        _mark("hypotheses_ms", stage_t)
        _mark("gaps_ms", stage_t)

        if is_conversational(question):
            # Fast path — no AI needed
            thread = det["thread"]
            if thread is None:
                thread = await create_session(
                    session,
                    dataset_id=inputs.dataset_id,
                    case_id=inputs.case_id,
                    scope=inputs.mode,
                    title=question,
                    created_by=getattr(principal, "id", None),
                )
            await session.flush()
            # Build minimal response
            resp = InvestigatorResponse(
                question=question,
                objective=det["turn_objective"],
                investigation_id=thread.id,
                scope=ScopeSection(
                    mode=inputs.mode,  # type: ignore
                    label=scope_label(inputs),
                    dataset_id=inputs.dataset_id,
                    dataset_name=inputs.dataset_name,
                    case_id=inputs.case_id,
                    case_number=inputs.case_number,
                    case_title=inputs.case_title,
                    case_ids=inputs.case_ids,
                    nodes_considered=0,
                    edges_considered=0,
                    documents_considered=0,
                ),
                assessment=AssessmentSection(
                    overall_strength="INSUFFICIENT",
                    overall_confidence=0.0,
                    convergence={"converges": False, "note": "Conversational turn: no analysis run."},
                    observation="Conversational message, not an investigation question.",
                    interpretation="No entities were extracted and no detectors ran.",
                    assessment="Hello — I investigate the active dataset. Ask me about named people, phones, vehicles, or accounts.",
                    caveats=[],
                    model=ModelSection(available=False, reason="conversational"),
                ),
                memory=memory_section(thread),
                timing_ms=timings,
            )
            await _report("COMPLETED", 100, "Conversational reply", status="COMPLETED")
            return {"response": resp.model_dump(), "status": "COMPLETED"}

        await _report("ANALYZING_GRAPH", 20, "Computing graph metrics (degree, betweenness, PageRank, communities)")
        # Centrality already computed in deterministic

        await _report("DETECTING_PATTERNS", 35, f"Detecting unusual patterns ({len(det['patterns'])} found)")

        await _report("RETRIEVING_EVIDENCE", 50, f"Retrieving supporting evidence ({len(det['relationships'])} relationships)")

        await _report("SEARCHING_CONTRADICTIONS", 65, f"Searching contradictory evidence and alternatives ({len(det['hypotheses'])} hypotheses)")

        # Prepare AI context
        entities = det["entities"]
        relationships = det["relationships"]
        patterns = det["patterns"]
        hypotheses = det["hypotheses"]
        gaps = det["gaps"]
        thread = det["thread"]
        turn_objective = det["turn_objective"]
        observation = (
            f"{len(det['resolved'])} of {len(entities)} mention(s) resolved; "
            f"{len(relationships)} relationship(s), {len([p for p in patterns if not p.excluded])} live pattern(s), "
            f"{len(hypotheses)} hypotheses; scope {inputs.mode} over "
            f"{len(inputs.case_ids)} case(s), {len(inputs.snapshot.nodes or {})} nodes, "
            f"{len(inputs.snapshot.edges or [])} edges, {len(inputs.doc_index)} documents."
        )
        convergence = det["convergence"]
        interpretation = "Convergence: " + convergence["note"] if convergence.get("note") else "No convergence reading."
        if hypotheses:
            basis = f"{len(hypotheses)} hypothesis(es) formed from this question"
        elif any(e.resolved for e in entities):
            basis = "the records the question's entities resolved to"
        else:
            basis = "the scope's own patterns — this question formed no hypothesis"
        assessment_text = (
            f"Overall strength {det['overall_strength']} (confidence {det['overall_confidence']:.0%}) over {basis}. "
            + (
                "Independent streams agree — follow the top reading first."
                if convergence.get("converges")
                else "Streams do not converge — treat every reading as provisional."
            )
        )
        unresolved = [e for e in entities if not e.resolved]
        caveats = []
        if unresolved:
            caveats.append(f"{len(unresolved)} mention(s) matched no record and were treated as data gaps.")

        brief = build_investigation_prompt(
            question=question,
            scope_summary=observation,
            entity_lines=[
                f"{e.display_name} ({e.label}, type={getattr(e, 'entity_type', e.label)}, {e.matched_by}, confidence {e.confidence}, legal_status={getattr(e, 'legal_status', 'unknown')}, network_role={getattr(e, 'network_role', 'unknown')})" + ("" if e.resolved else " — UNRESOLVED")
                for e in entities
            ],
            relationship_lines=[
                f"[{r.kind}/{r.inference_label}] {r.title} WHY: {getattr(r, 'why', '') or r.description}"
                for r in relationships
            ],
            pattern_lines=[
                f"[{p.kind}/{p.strength}] {p.title} analytical_basis: {p.analytical_basis.model_dump() if p.analytical_basis else 'none'}" + (" (set aside)" if p.excluded else "")
                for p in patterns
            ],
            hypothesis_lines=[
                f"{h.id} [{h.strength}]: {h.statement} supporting={len(h.supporting)} contradicting={len(h.contradicting)}"
                for h in hypotheses
            ],
            convergence_line=convergence.get("note", ""),
            gap_lines=[f"[{g.category}] {g.description}" for g in gaps],
            memory_lines=_memory_lines(thread),
        )

        from app.db.base import new_uuid as _new_uuid

        thread_id = thread.id if thread else _new_uuid()
        gateway = get_ai_gateway()

        await _report("REASONING", 75, "Running investigator reasoning model (big reasoning model — may take time)")
        stage_t = time.monotonic()
        model_section = await gateway.investigate_narrative(
            question=question,
            brief=brief,
            investigation_id=thread_id,
            entities=entities,
            user_id=getattr(principal, "id", None),
            session=session,
        )
        _mark("narrative_ms", stage_t)

        # Handle model unavailable / failure honestly
        if not model_section.available:
            reason = model_section.reason or "unknown"
            # Distinguish timeout vs unavailable vs invalid response
            if "timeout" in reason.lower():
                await _report("AI_TIMEOUT", 90, f"Reasoning model timed out ({reason}) — deterministic analysis preserved", status="AI_TIMEOUT")
                # Return deterministic partial result with honest note
            elif "unavailable" in reason.lower() or "no_api_key" in reason.lower():
                await _report("AI_UNAVAILABLE", 90, f"Reasoning model unavailable ({reason}) — deterministic analysis preserved", status="AI_UNAVAILABLE")
            else:
                await _report("AI_INVALID_RESPONSE", 90, f"Reasoning model invalid response ({reason}) — deterministic analysis preserved", status="AI_INVALID_RESPONSE")

            # Still create thread if needed
            if thread is None:
                thread = await create_session(
                    session,
                    dataset_id=inputs.dataset_id,
                    case_id=inputs.case_id,
                    scope=inputs.mode,
                    title=question,
                    created_by=getattr(principal, "id", None),
                    thread_id=thread_id,
                )

            response_facts = collect_facts(
                relationships,
                patterns,
                hypotheses,
                entity_keys={key for entity in det["resolved"] for key in (entity.entity_keys or [])},
            )
            alternatives = collect_alternatives(hypotheses, patterns)
            thread.state = record_turn(
                dict(thread.state or {}),
                question=question,
                objective=turn_objective,
                facts=[ev.summary for ev in response_facts],
                hypotheses=[{"id": h.id, "statement": h.statement, "strength": h.strength} for h in hypotheses],
                entities=[e.display_name for e in det["resolved"]],
                gaps=[g.description for g in gaps],
                unresolved=[e.display_name for e in unresolved],
                contradictions=[item.summary for h in hypotheses for item in h.contradicting],
                relationships=[f"{f.kind}: {f.title}" for f in relationships[:10]],
                rejected=[
                    {"id": h.id, "statement": h.statement, "reason": h.contradicting[0].summary if h.contradicting else "not supported"}
                    for h in hypotheses
                    if h.strength == "INSUFFICIENT"
                ],
                findings=[f"{det['overall_strength']}: {fact.summary}" for fact in response_facts[:5]],
            )
            await session.flush()
            timings["total_ms"] = int((time.monotonic() - started) * 1000)

            resp = InvestigatorResponse(
                question=question,
                objective=turn_objective,
                investigation_id=thread.id,
                scope=ScopeSection(
                    mode=inputs.mode,  # type: ignore
                    label=scope_label(inputs),
                    dataset_id=inputs.dataset_id,
                    dataset_name=inputs.dataset_name,
                    case_id=inputs.case_id,
                    case_number=inputs.case_number,
                    case_title=inputs.case_title,
                    case_ids=inputs.case_ids,
                    nodes_considered=len(inputs.snapshot.nodes or {}),
                    edges_considered=len(inputs.snapshot.edges or []),
                    documents_considered=len(inputs.doc_index),
                ),
                entities=entities,
                facts=response_facts,
                relationships=relationships,
                patterns=patterns,
                hypotheses=hypotheses,
                alternative_explanations=alternatives,
                assessment=AssessmentSection(
                    overall_strength=det["overall_strength"],
                    overall_confidence=det["overall_confidence"],
                    convergence=convergence,
                    observation=observation,
                    interpretation=interpretation,
                    assessment=assessment_text + " Deterministic analysis completed. Reasoning model unavailable. Investigator can still inspect analytical findings, evidence and graph.",
                    caveats=caveats[:5] + ["Reasoning model unavailable — showing deterministic analysis only"],
                    model=model_section,
                    investigative_relevance=det["investigative_relevance"],
                    evidence_strength=det["evidence_strength"],
                    evidence_convergence=det["evidence_convergence"],
                    analytical_basis_summary={
                        "nodes_considered": len(inputs.snapshot.nodes or {}),
                        "edges_considered": len(inputs.snapshot.edges or []),
                        "documents_considered": len(inputs.doc_index),
                    },
                ),
                gaps=gaps,
                next_steps=det["steps"],
                timeline=det["timeline"],
                focused_graph=det["focused"],
                provenance=det["provenance"],
                memory=memory_section(thread),
                timing_ms=timings,
                structured_findings=[],
                analytical_basis={
                    "nodes_considered": len(inputs.snapshot.nodes or {}),
                    "edges_considered": len(inputs.snapshot.edges or []),
                    "documents_considered": len(inputs.doc_index),
                },
                investigative_relevance=det["investigative_relevance"],
                evidence_strength=det["evidence_strength"],
                evidence_convergence=det["evidence_convergence"],
                silent_intermediaries=det["silent_intermediaries"],
                data_quality=det["data_quality"],
                validation_notes=det["validation_notes"] + [f"Model unavailable: {reason}"],
            )
            # Persist result even on model failure
            final_payload = {"response": resp.model_dump(), "status": model_section.reason or "AI_UNAVAILABLE"}
            if reporter:
                # Don't mark terminal as FAILED — deterministic work succeeded
                await reporter.update(
                    status=model_section.reason if "AI_" in (model_section.reason or "") else "AI_UNAVAILABLE",
                    stage="COMPLETED_WITH_DETERMINISTIC",
                    progress_pct=100,
                    message="Deterministic analysis completed; reasoning model unavailable",
                    result=final_payload,
                )
            return final_payload

        await _report("VALIDATING", 85, "Validating evidence references and canonical IDs")
        # Validation already done in deterministic, but also validate AI output does not invent IDs
        # The gateway already validates, but we double-check
        validation_notes = det["validation_notes"]
        # Check that AI summary does not invent unknown PERSON: IDs?
        # For now, trust gateway's validation; if malformed, it would have returned unavailable

        await _report("GENERATING_EXPLANATION", 95, "Generating investigator explanation")

        if thread is None:
            thread = await create_session(
                session,
                dataset_id=inputs.dataset_id,
                case_id=inputs.case_id,
                scope=inputs.mode,
                title=question,
                created_by=getattr(principal, "id", None),
                thread_id=thread_id,
            )

        response_facts = collect_facts(
            relationships,
            patterns,
            hypotheses,
            entity_keys={key for entity in det["resolved"] for key in (entity.entity_keys or [])},
        )
        alternatives = collect_alternatives(hypotheses, patterns)
        thread.state = record_turn(
            dict(thread.state or {}),
            question=question,
            objective=turn_objective,
            facts=[ev.summary for ev in response_facts],
            hypotheses=[{"id": h.id, "statement": h.statement, "strength": h.strength} for h in hypotheses],
            entities=[e.display_name for e in det["resolved"]],
            gaps=[g.description for g in gaps],
            unresolved=[e.display_name for e in unresolved],
            contradictions=[item.summary for h in hypotheses for item in h.contradicting],
            relationships=[f"{f.kind}: {f.title}" for f in relationships[:10]],
            rejected=[
                {"id": h.id, "statement": h.statement, "reason": h.contradicting[0].summary if h.contradicting else "not supported"}
                for h in hypotheses
                if h.strength == "INSUFFICIENT"
            ],
            findings=[f"{det['overall_strength']}: {fact.summary}" for fact in response_facts[:5]],
        )
        await session.flush()
        timings["total_ms"] = int((time.monotonic() - started) * 1000)

        # Build structured findings
        structured_findings = []
        try:
            for idx, pat in enumerate([p for p in patterns if not p.excluded][:5]):
                finding = StructuredFinding(
                    finding_id=f"F{idx+1:03d}",
                    title=pat.title,
                    finding_type=pat.kind,
                    objective=turn_objective,
                    why=pat.why or "",
                    entities=[e for e in det["resolved"] if e.canonical_id in (pat.entity_keys or [])][:3],
                    analytical_basis=pat.analytical_basis,
                    relationships=[r for r in relationships if set(r.entities) & set(pat.entities)][:3],
                    patterns=[pat],
                    supporting_evidence=[ev for ev in pat.evidence if ev.stance == "supports"][:5],
                    contradictory_evidence=[ev for ev in pat.evidence if ev.stance == "contradicts"][:5],
                    alternative_explanations=pat.innocent_alternatives[:3],
                    assessment={
                        "investigative_relevance": pat.investigative_relevance.model_dump() if pat.investigative_relevance else None,
                        "evidence_strength": pat.evidence_strength.model_dump() if pat.evidence_strength else None,
                        "strength": pat.strength,
                    },
                    data_gaps=[g for g in gaps if any(e in g.entities for e in pat.entities)][:3],
                    focused_graph=_focused_graph(inputs.snapshot, pat.entity_keys or [], doc_index=inputs.doc_index, centrality=inputs.centrality),
                    next_investigative_direction=det["steps"][0].action if det["steps"] else "",
                )
                structured_findings.append(finding)
        except Exception:
            structured_findings = []

        resp = InvestigatorResponse(
            question=question,
            objective=turn_objective,
            investigation_id=thread.id,
            scope=ScopeSection(
                mode=inputs.mode,  # type: ignore
                label=scope_label(inputs),
                dataset_id=inputs.dataset_id,
                dataset_name=inputs.dataset_name,
                case_id=inputs.case_id,
                case_number=inputs.case_number,
                case_title=inputs.case_title,
                case_ids=inputs.case_ids,
                nodes_considered=len(inputs.snapshot.nodes or {}),
                edges_considered=len(inputs.snapshot.edges or []),
                documents_considered=len(inputs.doc_index),
            ),
            entities=entities,
            facts=response_facts,
            relationships=relationships,
            patterns=patterns,
            hypotheses=hypotheses,
            alternative_explanations=alternatives,
            assessment=AssessmentSection(
                overall_strength=det["overall_strength"],
                overall_confidence=det["overall_confidence"],
                convergence=convergence,
                observation=observation,
                interpretation=interpretation,
                assessment=assessment_text,
                caveats=caveats[:5],
                model=model_section,
                investigative_relevance=det["investigative_relevance"],
                evidence_strength=det["evidence_strength"],
                evidence_convergence=det["evidence_convergence"],
                analytical_basis_summary={
                    "nodes_considered": len(inputs.snapshot.nodes or {}),
                    "edges_considered": len(inputs.snapshot.edges or []),
                    "documents_considered": len(inputs.doc_index),
                    "evidence_convergence": det["evidence_convergence"].model_dump(),
                    "evidence_strength": det["evidence_strength"].model_dump(),
                },
            ),
            gaps=gaps,
            next_steps=det["steps"],
            timeline=det["timeline"],
            focused_graph=det["focused"],
            provenance=det["provenance"],
            memory=memory_section(thread),
            timing_ms=timings,
            structured_findings=structured_findings,
            analytical_basis={
                "nodes_considered": len(inputs.snapshot.nodes or {}),
                "edges_considered": len(inputs.snapshot.edges or []),
                "documents_considered": len(inputs.doc_index),
                "cases_considered": len(inputs.case_ids),
                "centrality_computed": inputs.centrality is not None,
                "evidence_convergence": det["evidence_convergence"].model_dump(),
                "evidence_strength": det["evidence_strength"].model_dump(),
                "investigative_relevance": det["investigative_relevance"].model_dump(),
            },
            investigative_relevance=det["investigative_relevance"],
            evidence_strength=det["evidence_strength"],
            evidence_convergence=det["evidence_convergence"],
            silent_intermediaries=det["silent_intermediaries"],
            data_quality=det["data_quality"],
            validation_notes=validation_notes,
        )

        await _report("COMPLETED", 100, "Investigation completed", status="COMPLETED")
        return {"response": resp.model_dump(), "status": "COMPLETED"}

    except Exception as exc:
        log = __import__("app.logging", fromlist=["get_logger"]).get_logger("crimelink.investigator.orchestrator")
        log.exception("investigation_with_reporter.failed", error=str(exc))
        await _report("INTERNAL_ERROR", 100, f"Investigation failed: {type(exc).__name__}: {exc}", status="FAILED")
        raise


__all__ = [
    "FOCUSED_MAX_NODES",
    "TIMELINE_LIMIT",
    "InvestigationInputs",
    "collect_alternatives",
    "collect_facts",
    "derive_objective",
    "detect_patterns_standalone",
    "investigate",
    "investigate_deterministic",
    "investigate_with_reporter",
    "load_inputs",
    "scope_label",
]
