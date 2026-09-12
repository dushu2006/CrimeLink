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

from .entity_resolution import PendingAlias, extract_mentions, resolve_mentions
from .evidence import source_pointer
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
    import_report = await _load_import_report(session, dataset.id)

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
    relationships = discover_relationships(inputs.snapshot, entities, doc_index=inputs.doc_index)
    _mark("relationships_ms", stage)

    stage = time.monotonic()
    resolved = [entity for entity in entities if entity.resolved]
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
    focused = _focused_graph(inputs.snapshot, seeds)
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


__all__ = [
    "FOCUSED_MAX_NODES",
    "TIMELINE_LIMIT",
    "InvestigationInputs",
    "collect_alternatives",
    "collect_facts",
    "derive_objective",
    "detect_patterns_standalone",
    "investigate",
    "load_inputs",
    "scope_label",
]
