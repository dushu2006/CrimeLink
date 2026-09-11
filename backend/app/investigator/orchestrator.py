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
from app.db.models import CaseDocument, DetectedPattern
from app.domain.enums import PatternStatus
from app.domain.models import CaseGraphSnapshot
from app.errors import ValidationFailedError
from app.services.cases import require_case, visible_case_ids

from .entity_resolution import PendingAlias, extract_mentions, resolve_mentions
from .evidence import doc_pointer
from .gaps import MAX_GAPS, build_gaps
from .hypotheses import MAX_PAIRS, build_convergence, build_hypotheses
from .memory import create_session, get_session, memory_section, record_turn
from .next_steps import MAX_STEPS, build_next_steps
from .patterns import DetectorContext, detect_all_patterns, entity_signature
from .prompts import build_investigation_prompt
from .relationships import discover_relationships
from .schemas import (
    AssessmentSection,
    DataGap,
    Hypothesis,
    InvestigatorResponse,
    ModelSection,
    ResolvedEntity,
    ScopeSection,
)

#: Focused evidence graph caps (seeds plus one hop, then stop).
FOCUSED_MAX_NODES = 60
FOCUSED_MAX_EDGES = 200

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


async def load_inputs(
    session: AsyncSession, scope, case_id: str | None
) -> InvestigationInputs:
    """Scope the question to the active dataset (mandatory) and load once."""
    dataset = await registry.active_dataset(session)
    if dataset is None:
        raise ValidationFailedError(
            "No dataset is currently active. Import a dataset before investigating."
        )
    from app.services.graph_service import GraphService

    store = GraphService().container.graph_store
    if case_id:
        await require_case(session, scope, case_id)
        mode, case_ids = "case", [case_id]
        snapshot = store.snapshot(case_id)
    else:
        mode = "master"
        case_ids = sorted(await visible_case_ids(session, scope))
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

    centrality = None
    try:
        centrality = compute_centrality(snapshot)
    except Exception:
        centrality = None
    centrality_dict = None
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
    for edge in snapshot.edges or []:
        if edge.rel_type == "POTENTIAL_ALIAS":
            props = edge.properties or {}
            pending_aliases.append(
                PendingAlias(
                    source_key=edge.source_key,
                    target_key=edge.target_key,
                    note=f"similarity {props.get('similarity', '?')}",
                    extra=dict(props),
                )
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

    return InvestigationInputs(
        mode=mode,
        dataset_id=dataset.id,
        case_id=case_id,
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
    )


def _focused_graph(snapshot: CaseGraphSnapshot, seeds: list[str]) -> dict[str, Any]:
    """Seeds plus one hop: the evidence graph around the question."""
    nodes = snapshot.nodes or {}
    wanted = [seed for seed in seeds if seed in nodes]
    neighbours: list[str] = []
    for edge in snapshot.edges or []:
        if edge.source_key in wanted and edge.target_key not in wanted:
            neighbours.append(edge.target_key)
        elif edge.target_key in wanted and edge.source_key not in wanted:
            neighbours.append(edge.source_key)
    ordered = list(dict.fromkeys([*wanted, *sorted(set(neighbours))]))
    kept = ordered[:FOCUSED_MAX_NODES]
    kept_set = set(kept)
    graph_nodes = [
        {
            "key": key,
            "label": nodes[key].label,
            "name": nodes[key].name or key,
            "focus": key in set(wanted),
        }
        for key in kept
    ]
    graph_edges = [
        {"source": edge.source_key, "target": edge.target_key, "rel_type": edge.rel_type}
        for edge in (snapshot.edges or [])
        if edge.source_key in kept_set and edge.target_key in kept_set
    ][:FOCUSED_MAX_EDGES]
    return {"nodes": graph_nodes, "edges": graph_edges, "truncated": len(ordered) > len(kept)}


def _overall(hypotheses: list[Hypothesis], patterns: list) -> tuple[str, float]:
    best = "INSUFFICIENT"
    for hypothesis in hypotheses:
        if _STRENGTH_RANK.get(hypothesis.strength, 0) > _STRENGTH_RANK.get(best, 0):
            best = hypothesis.strength
    for pattern in patterns:
        if pattern.excluded:
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

    stage = time.monotonic()
    mentions = extract_mentions(question)
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
    relationships = discover_relationships(inputs.snapshot, entities, doc_index=inputs.doc_index)
    _mark("relationships_ms", stage)

    stage = time.monotonic()
    resolved = [entity for entity in entities if entity.resolved]
    pairs = [
        (resolved[index], resolved(other))
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
    convergence = build_convergence(hypotheses)
    _mark("hypotheses_ms", stage)

    stage = time.monotonic()
    gaps = build_gaps(entities, inputs.doc_types, hypotheses, relationships, inputs.case_ids)
    steps = build_next_steps(entities, gaps, hypotheses, patterns, inputs.case_ids)
    _mark("gaps_ms", stage)

    try:
        timeline = build_timeline(inputs.snapshot, limit=TIMELINE_LIMIT)
    except Exception:
        timeline = []

    seeds = [entity.canonical_id for entity in resolved]
    focused = _focused_graph(inputs.snapshot, seeds)
    provenance = [
        doc_pointer(
            doc_id=doc_id,
            label=str(info.get("filename") or doc_id),
            origin_file=str(info.get("filename")) if info.get("filename") else None,
            content_hash=info.get("content_hash"),
            detail=str(info.get("document_type")) if info.get("document_type") else None,
        )
        for doc_id, info in sorted(inputs.doc_index.items())
    ][:PROVENANCE_CAP]

    overall_strength, overall_confidence = _overall(hypotheses, patterns)
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
    assessment_text = (
        f"Overall strength {overall_strength} (confidence {overall_confidence:.0%}). "
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

    facts = [
        evidence.summary
        for relationship in relationships
        for evidence in relationship.evidence
        if evidence.inference_label == "FACT"
    ][:MAX_GAPS]
    thread.state = record_turn(
        dict(thread.state or {}),
        question=question,
        facts=facts,
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
    )
    await session.flush()
    timings["total_ms"] = int((time.monotonic() - started) * 1000)

    return InvestigatorResponse(
        question=question,
        investigation_id=thread.id,
        scope=ScopeSection(
            mode=inputs.mode,  # type: ignore[arg-type]
            dataset_id=inputs.dataset_id,
            case_id=inputs.case_id,
            case_ids=inputs.case_ids,
            nodes_considered=len(inputs.snapshot.nodes or {}),
            edges_considered=len(inputs.snapshot.edges or []),
            documents_considered=len(inputs.doc_index),
        ),
        entities=entities,
        relationships=relationships,
        patterns=patterns,
        hypotheses=hypotheses,
        assessment=AssessmentSection(
            overall_strength=overall_strength,
            overall_confidence=overall_confidence,
            convergence=convergence,
            observation=observation,
            interpretation=interpretation,
            assessment=assessment_text,
            caveats=caveats[:5],
            model=model_section,
        ),
        gaps=gaps,
        next_steps=steps,
        timeline=timeline,
        focused_graph=focused,
        provenance=provenance,
        memory=memory_section(thread),
        timing_ms=timings,
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
    thread.state = record_turn(dict(thread.state or {}), question=question)
    await session.flush()
    timings["total_ms"] = int((time.monotonic() - started) * 1000)
    return InvestigatorResponse(
        question=question,
        investigation_id=thread.id,
        scope=ScopeSection(
            mode=inputs.mode,  # type: ignore[arg-type]
            dataset_id=inputs.dataset_id,
            case_id=inputs.case_id,
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
        "mode": inputs.mode,
        "case_id": inputs.case_ids[0] if inputs.mode == "case" else None,
        "case_ids": inputs.case_ids,
        "nodes_considered": len(getattr(inputs.snapshot, "nodes", {}) or {}),
        "edges_considered": len(getattr(inputs.snapshot, "edges", []) or []),
        "patterns": [pattern.model_dump() for pattern in patterns],
        "count": len(patterns),
    }


__all__ = [
    "FOCUSED_MAX_NODES",
    "TIMELINE_LIMIT",
    "InvestigationInputs",
    "detect_patterns_standalone",
    "investigate",
    "load_inputs",
]
