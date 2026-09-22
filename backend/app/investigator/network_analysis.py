"""Explicit network analysis over the three graph scopes.

The Investigation Analysis workspace exposes three scopes, each analysed by an
explicit trigger (never by opening the page):

* **MASTER NETWORK** — the Overall Master Graph spanning every case of the
  active dataset.
* **CASE NETWORK**    — one case's Case Master Graph.
* **PERSON NETWORK**  — one person's cross-case neighbourhood.

Every structural claim here is deterministic (graph metrics, communities,
cross-case analysis, patterns, relationships, hypotheses, supporting and
contradictory evidence, alternatives, gaps, next steps). The reasoning model
only authors narrative prose over those results; if it is unavailable, times
out, or answers with an invalid shape, the deterministic analysis is
preserved and reported honestly.

Entity types are canonical and never inferred from graph position: a
BANK_ACCOUNT stays a BANK_ACCOUNT, a LOCATION stays a LOCATION, and a PERSON
stays a PERSON. Criminal status is read only from source-derived legal/criminal
records — never from degree, betweenness, PageRank or community membership.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import get_ai_gateway
from app.analytics.centrality import CentralityResult, compute_centrality
from app.analytics.findings import generate_findings
from app.analytics.patterns import PatternEngine
from app.analytics.timeline import build_timeline
from app.db.base import new_uuid
from app.db.models import CaseDocument
from app.domain.models import CaseGraphSnapshot
from app.errors import NotFoundError, ValidationFailedError
from app.services.cases import active_dataset_case_ids, require_case
from app.services.graph_service import _bfs_neighbourhood

from .assessment import (
    assess_evidence_convergence,
    assess_evidence_strength,
    assess_investigative_relevance,
    build_analytical_basis,
    determine_network_role,
)
from .evidence import source_pointer
from .gaps import build_gaps, present_sources
from .hypotheses import build_convergence, build_hypotheses
from .memory import create_session, memory_section, record_turn
from .next_steps import build_next_steps
from .orchestrator import (
    InvestigationInputs,
    _assess_data_quality,
    _carry_rejected_hypotheses,
    _focused_graph,
    _overall,
    _validate_evidence_references,
    collect_alternatives,
    collect_facts,
    load_inputs,
    scope_label,
)
from .patterns import _is_person, detect_all_patterns
from .prompts import build_investigation_prompt
from .relationships import discover_relationships
from .schemas import (
    AnalyticalBasis,
    AssessmentSection,
    InvestigatorResponse,
    ModelSection,
    ResolvedEntity,
    ScopeSection,
    StructuredFinding,
)
from .silent_intermediary import detect_silent_intermediaries

#: Top-N entities shown per metric and per cross-case list.
METRIC_TOP = 15
CROSS_CASE_TOP = 20
COMMUNITY_TOP = 20
#: How many seeds the analysis focuses on (keeps hypotheses legible).
SEED_LIMIT = 8
PROVENANCE_CAP = 20
TIMELINE_LIMIT = 80

_METRIC_EXPLANATIONS = {
    "degree": (
        "Degree centrality counts a node's direct relationships. It describes "
        "connectivity only — never criminality."
    ),
    "weighted_degree": (
        "Weighted degree sums the confidence of a node's direct relationships, "
        "so well-evidenced links weigh more than weak ones."
    ),
    "betweenness": (
        "Betweenness centrality measures how often a node lies on the shortest "
        "paths between other nodes — a structural bridge position, not a verdict."
    ),
    "pagerank": (
        "PageRank estimates indirect reach across the directed, confidence-"
        "weighted graph. It is a network-position measure, not a suspicion score."
    ),
}

_STRENGTH_CONFIDENCE = {"STRONG": 0.9, "MODERATE": 0.7, "WEAK": 0.4, "INSUFFICIENT": 0.1}


def _canonical(node: Any) -> str:
    from app.domain.enums import canonical_label

    return canonical_label(node.label)


def is_confirmed_criminal(node: Any) -> bool:
    """Check if an entity is a confirmed criminal based strictly on source-derived records."""
    if not node:
        return False
    label = getattr(node, "label", "") or ""
    if label.lower() not in {"person", "suspect"}:
        return False
    props = getattr(node, "properties", {}) or {}
    c_status = str(props.get("criminal_status") or props.get("legal_status") or "").strip().lower()
    return c_status in {"convicted", "criminal", "confirmed", "accused", "proclaimed_offender"}


def node_shape_rule(node: Any) -> str:
    """Visual rule: star for confirmed criminal PERSON only, ellipse for all others."""
    if is_confirmed_criminal(node):
        return "star"
    return "ellipse"


def _top_metric(
    centrality: CentralityResult,
    snapshot: CaseGraphSnapshot,
    metric: str,
    limit: int,
    subject: str = "PERSON",
) -> list[dict[str, Any]]:
    """Top nodes by one centrality metric, restricted to the analytical subject.

    Ranking is taken over the whole computed graph — the numbers are real
    graph metrics either way — but a person-centric scope only *reports* the
    people.  A hub phone outranking every person on weighted degree is a true
    statement about the evidence graph and a misleading one about an
    investigation, so it stays out of the person-centric answer.
    """
    scores = getattr(centrality, metric, None)
    if not isinstance(scores, dict) or not scores:
        return []
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    out: list[dict[str, Any]] = []
    for key, value in ordered:
        node = snapshot.nodes.get(key)
        if subject.upper() != "ENTITY" and not _is_person(node):
            continue
        if len(out) >= limit:
            break
        case_ids = list((node.properties or {}).get("case_ids", []) or []) if node else []
        out.append(
            {
                "key": key,
                "name": node.name if node else key[:8],
                "label": _canonical(node) if node else "?",
                "value": round(float(value), 6),
                "case_count": len(set(case_ids)),
                "is_criminal": bool(
                    node
                    and (node.properties or {}).get("criminal_status")
                    and str((node.properties or {}).get("criminal_status")).strip().lower()
                    not in {"", "none", "unknown", "null"}
                ),
            }
        )
    return out


def _communities(
    centrality: CentralityResult, snapshot: CaseGraphSnapshot, limit: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cid in sorted(centrality.community_members):
        members = centrality.community_members[cid]
        top = []
        for key in members[:8]:
            node = snapshot.nodes.get(key)
            top.append(
                {
                    "key": key,
                    "name": node.name if node else key[:8],
                    "label": _canonical(node) if node else "?",
                }
            )
        out.append({"id": cid, "size": len(members), "top_members": top})
    out.sort(key=lambda c: -c["size"])
    return out[:limit]


def _cross_case(
    snapshot: CaseGraphSnapshot,
    centrality: CentralityResult,
    limit: int,
    subject: str = "PERSON",
) -> list[dict[str, Any]]:
    """Entities spanning more than one case, restricted to the subject.

    A person-centric scope lists the PEOPLE who span cases.  Supporting
    entities that span cases are handled by the pattern detectors, which
    translate them into the person↔person connections they carry — that is the
    form an investigator can act on.
    """
    rows: list[tuple[str, Any, int, float]] = []
    for key, node in snapshot.nodes.items():
        if subject.upper() != "ENTITY" and not _is_person(node):
            continue
        cids = set((node.properties or {}).get("case_ids", []) or [])
        if len(cids) > 1:
            rows.append((key, node, len(cids), float(centrality.betweenness.get(key, 0.0))))
    rows.sort(key=lambda item: (-item[2], -item[3]))
    out: list[dict[str, Any]] = []
    for key, node, count, btwn in rows[:limit]:
        out.append(
            {
                "key": key,
                "name": node.name,
                "label": _canonical(node),
                "case_count": count,
                "case_ids": sorted((node.properties or {}).get("case_ids", []) or []),
                "betweenness": round(btwn, 6),
                "is_criminal": bool(
                    (node.properties or {}).get("criminal_status")
                    and str((node.properties or {}).get("criminal_status")).strip().lower()
                    not in {"", "none", "unknown", "null"}
                ),
            }
        )
    return out


def _seed_entity(
    key: str,
    snapshot: CaseGraphSnapshot,
    centrality: CentralityResult,
    *,
    matched_by: str,
) -> ResolvedEntity:
    node = snapshot.nodes[key]
    props = node.properties or {}
    case_ids = list(props.get("case_ids") or [])
    try:
        basis = build_analytical_basis(
            node_key=key,
            snapshot=snapshot,
            centrality=centrality,
            cross_case_count=len(set(case_ids)),
            evidence_count=sum(
                1
                for edge in snapshot.edges or []
                if edge.source_key == key or edge.target_key == key
            ),
        )
        network_role = determine_network_role(
            analytical_basis=basis, cross_case_count=len(set(case_ids))
        )
    except Exception:
        basis = None
        network_role = None
    return ResolvedEntity(
        canonical_id=key,
        label=node.label,
        display_name=node.name or key,
        entity_type=node.label.upper(),
        aliases=list(props.get("aliases") or []),
        confidence=float(props.get("confidence", 1.0) or 1.0),
        matched_by=matched_by,
        entity_keys=[key],
        criminal_status=props.get("criminal_status"),
        legal_status=props.get("legal_status") or props.get("criminal_status"),
        network_role=network_role,
        case_ids=case_ids,
        analytical_basis=basis,
        resolved=True,
    )


def _select_seeds(
    inputs: InvestigationInputs,
    person_key: str | None,
    centrality: CentralityResult,
) -> tuple[list[ResolvedEntity], list[str]]:
    """Deterministically pick the entities the analysis will focus on.

    The ranking is structural (betweenness first, then degree) and is used only
    to decide *where to look* — it never labels anyone.
    """
    snapshot = inputs.snapshot
    nodes = snapshot.nodes or {}
    if person_key and person_key in nodes:
        # Person-centric: the selected person is the central subject, then the
        # most structurally interesting persons in their neighbourhood.
        seeds = [person_key]
        others = [
            key
            for key in nodes
            if key != person_key and nodes[key].label in ("PERSON", "Person")
        ]
        others.sort(
            key=lambda k: (
                -float(centrality.betweenness.get(k, 0.0)),
                -float(centrality.degree.get(k, 0.0)),
            )
        )
        seeds.extend(others[: SEED_LIMIT - 1])
        return (
            [_seed_entity(k, snapshot, centrality, matched_by="person-network") for k in seeds],
            seeds,
        )

    # Master / case: prefer persons, ranked by betweenness then degree.
    persons = [key for key in nodes if nodes[key].label in ("PERSON", "Person")]
    persons.sort(
        key=lambda k: (
            -float(centrality.betweenness.get(k, 0.0)),
            -float(centrality.degree.get(k, 0.0)),
        )
    )
    seeds = persons[:SEED_LIMIT]
    if len(seeds) < 2:
        # Fall back to any entity type so a small graph still yields an analysis.
        others = [key for key in nodes if key not in seeds]
        others.sort(
            key=lambda k: (
                -float(centrality.betweenness.get(k, 0.0)),
                -float(centrality.degree.get(k, 0.0)),
            )
        )
        seeds.extend(others[: SEED_LIMIT - len(seeds)])
    return (
        [_seed_entity(k, snapshot, centrality, matched_by="graph-metric") for k in seeds],
        seeds,
    )


async def _person_inputs(
    session: AsyncSession,
    scope,
    person_key: str,
    master_inputs: InvestigationInputs,
    centrality: CentralityResult,
    engine_findings: list,
    analytics_findings: list,
) -> InvestigationInputs:
    """Re-scope the master inputs to one person's neighbourhood."""
    master_snapshot = master_inputs.snapshot
    if person_key not in master_snapshot.nodes:
        raise NotFoundError("That person is not part of the active dataset graph.")
    node = master_snapshot.nodes[person_key]
    if node.label not in ("PERSON", "Person"):
        raise NotFoundError("That node is not a person.")

    walked = _bfs_neighbourhood(master_snapshot, person_key, 3, None)
    keys = {n.provenance_key for n in walked["nodes"]}
    neighbourhood = CaseGraphSnapshot(
        case_id="",
        nodes={k: master_snapshot.nodes[k] for k in keys if k in master_snapshot.nodes},
        edges=list(walked["edges"].values()),
    )

    person_case_ids = sorted((node.properties or {}).get("case_ids") or [])
    # Documents are scoped to the person's own cases so provenance can never
    # cite a record from an unrelated active case.
    doc_index: dict[str, dict] = {
        doc_id: info
        for doc_id, info in master_inputs.doc_index.items()
        if info.get("case_id") in person_case_ids
    }
    doc_types = {str(info["document_type"]) for info in doc_index.values()}

    return InvestigationInputs(
        mode="person",
        dataset_id=master_inputs.dataset_id,
        case_id=None,
        case_ids=person_case_ids,
        snapshot=neighbourhood,
        doc_index=doc_index,
        doc_types=doc_types,
        centrality=centrality,
        engine_findings=engine_findings,
        analytics_findings=analytics_findings,
        incident_ts=master_inputs.incident_ts,
        pending_aliases=master_inputs.pending_aliases,
        dismissed_signatures=master_inputs.dismissed_signatures,
        dismissed_notes=master_inputs.dismissed_notes,
        dataset_name=master_inputs.dataset_name,
        case_number=None,
        case_title=None,
        import_report=master_inputs.import_report,
        person_key=person_key,
        person_name=node.name,
    )


async def analyze_network(
    session: AsyncSession,
    scope,
    principal,
    *,
    mode: str,
    case_id: str | None = None,
    person_key: str | None = None,
    max_patterns: int = 25,
    include_excluded: bool = True,
    reporter: Any | None = None,
) -> dict[str, Any]:
    """Run the full deterministic network analysis for one scope, then explain.

    Returns ``{"status": ..., "response": {...}, "analysis": {...}}``. The
    deterministic work always survives an AI failure; ``status`` records the
    honest outcome (COMPLETED or an AI_* failure with deterministic preserved).
    """
    timings: dict[str, int] = {}
    started = time.monotonic()

    def _mark(stage: str, stage_start: float) -> None:
        timings[stage] = int((time.monotonic() - stage_start) * 1000)

    async def _report(stage: str, pct: int, msg: str, status: str | None = None):
        if reporter:
            await reporter.update(stage=stage, progress_pct=pct, message=msg, status=status)

    mode = (mode or "master").lower()
    if mode not in ("master", "case", "person"):
        raise ValidationFailedError(f"Unknown network analysis mode '{mode}'.")

    # The analytical subject this run answers for.  CASE and PERSON scopes are
    # investigator-facing and therefore person-centric: metrics, rankings and
    # cross-case lists must name people.  MASTER is the entity/evidence
    # deep-dive, where entity-level structure is genuinely the point — and is
    # labelled as such wherever it is rendered.
    subject = "ENTITY" if mode == "master" else "PERSON"

    await _report("PREPARING", 5, "Preparing scope and loading graph snapshot")
    stage_t = time.monotonic()

    # ---- scope resolution --------------------------------------------------
    if mode == "case":
        if not case_id:
            raise ValidationFailedError("CASE NETWORK analysis requires a case.")
        await require_case(session, scope, case_id)
        inputs = await load_inputs(session, scope, case_id, compute_analytics=False)
    else:
        # Master scope (and the base for person scope).
        inputs = await load_inputs(session, scope, None, compute_analytics=False)

    await _report("BUILDING_GRAPH", 15, "Building the graph snapshot for this scope")
    stage_t = time.monotonic()
    centrality = compute_centrality(inputs.snapshot)
    _mark("building_graph_ms", stage_t)

    await _report("CALCULATING_METRICS", 25, "Calculating degree, weighted degree, betweenness, PageRank")
    stage_t = time.monotonic()
    communities = centrality.communities
    _mark("calculating_metrics_ms", stage_t)

    await _report("DETECTING_COMMUNITIES", 30, f"Detecting communities ({len(centrality.community_members)} found)")
    stage_t = time.monotonic()
    engine_findings = []
    analytics_findings = []
    try:
        engine_findings = PatternEngine().detect_scheduled(inputs.snapshot, centrality=centrality)
    except Exception:
        engine_findings = []
    try:
        centrality_dict = {
            key: {
                "betweenness": float(centrality.betweenness.get(key, 0.0)),
                "degree": float(centrality.degree.get(key, 0.0)),
            }
            for key in (inputs.snapshot.nodes or {})
        }
        analytics_findings = generate_findings(inputs.snapshot, centrality_dict)
    except Exception:
        analytics_findings = []
    inputs.centrality = centrality
    inputs.engine_findings = list(engine_findings)
    inputs.analytics_findings = list(analytics_findings)
    _mark("communities_ms", stage_t)

    if mode == "person":
        if not person_key:
            raise ValidationFailedError("PERSON NETWORK analysis requires a person.")
        inputs = await _person_inputs(
            session,
            scope,
            person_key,
            inputs,
            centrality,
            list(engine_findings),
            list(analytics_findings),
        )
        # Recompute analytics over the person's own neighbourhood.
        centrality = compute_centrality(inputs.snapshot)
        communities = centrality.communities
        inputs.centrality = centrality
        inputs.engine_findings = []
        inputs.analytics_findings = []
        try:
            inputs.engine_findings = PatternEngine().detect_scheduled(
                inputs.snapshot, centrality=centrality
            )
        except Exception:
            inputs.engine_findings = []
        try:
            cdict = {
                key: {
                    "betweenness": float(centrality.betweenness.get(key, 0.0)),
                    "degree": float(centrality.degree.get(key, 0.0)),
                }
                for key in (inputs.snapshot.nodes or {})
            }
            inputs.analytics_findings = generate_findings(inputs.snapshot, cdict)
        except Exception:
            inputs.analytics_findings = []

    seeds, seed_keys = _select_seeds(inputs, person_key if mode == "person" else None, centrality)

    await _report("DETECTING_PATTERNS", 45, "Detecting unusual patterns (deterministic)")
    stage_t = time.monotonic()
    from .patterns import DetectorContext

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
            subject=subject,
        ),
        max_patterns=max_patterns,
        include_excluded=include_excluded,
    )
    _mark("patterns_ms", stage_t)

    await _report("RETRIEVING_EVIDENCE", 55, "Retrieving supporting evidence and relationships")
    stage_t = time.monotonic()
    relationships = discover_relationships(inputs.snapshot, seeds, doc_index=inputs.doc_index)
    _mark("relationships_ms", stage_t)

    await _report("SEARCHING_CONTRADICTIONS", 65, "Searching contradictory evidence and alternatives")
    stage_t = time.monotonic()
    resolved = [entity for entity in seeds if entity.resolved]
    if mode == "person" and person_key:
        pairs = [
            (resolved[0], other)
            for other in resolved[1:4]
        ]
    else:
        pairs = [
            (resolved[i], resolved[j])
            for i in range(len(resolved))
            for j in range(i + 1, len(resolved))
        ][:3]
    hypotheses = build_hypotheses(
        pairs,
        relationships,
        patterns,
        dismissed_signatures=inputs.dismissed_signatures,
        dismissed_notes=inputs.dismissed_notes,
    )
    convergence = build_convergence(hypotheses)
    _mark("hypotheses_ms", stage_t)

    gaps = build_gaps(
        seeds,
        present_sources(inputs.doc_types, inputs.snapshot),
        hypotheses,
        relationships,
        inputs.case_ids,
        import_report=inputs.import_report,
        documents_in_scope=len(inputs.doc_index),
    )
    steps = build_next_steps(seeds, gaps, hypotheses, patterns, inputs.case_ids)
    try:
        timeline = build_timeline(inputs.snapshot, limit=TIMELINE_LIMIT)
    except Exception:
        timeline = []

    focused = _focused_graph(
        inputs.snapshot, seed_keys, doc_index=inputs.doc_index, centrality=inputs.centrality
    )
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
        hypotheses, patterns, entities=seeds, mention_count=len(seed_keys)
    )

    # ---- analytical assessments -------------------------------------------
    all_doc_ids: list[str] = []
    all_source_types: list[str] = []
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
        source_types=all_source_types, doc_ids=all_doc_ids, record_count=len(all_doc_ids)
    )
    evidence_strength = assess_evidence_strength(
        independent_sources=evidence_convergence.independent_source_count,
        corroborating_records=len(all_doc_ids),
        has_contradictions=any(h.contradicting for h in hypotheses),
        contradiction_level=(
            "major"
            if any(h.strength_factors.contradiction_level == "major" for h in hypotheses)
            else "minor"
            if any(h.contradicting for h in hypotheses)
            else "none"
        ),
    )
    investigative_relevance = assess_investigative_relevance(
        analytical_basis=AnalyticalBasis(
            degree_centrality=max(
                [getattr(e.analytical_basis, "degree_centrality", 0) or 0 for e in resolved],
                default=0,
            ),
            betweenness_centrality=max(
                [getattr(e.analytical_basis, "betweenness_centrality", 0) or 0 for e in resolved],
                default=0,
            ),
            pagerank=max(
                [getattr(e.analytical_basis, "pagerank", 0) or 0 for e in resolved], default=0
            ),
            cross_case_count=len(inputs.case_ids),
        ),
        cross_case_count=len(inputs.case_ids) if resolved else 0,
        evidence_convergence_type=evidence_convergence.convergence_type,
        has_contradictions=any(h.contradicting for h in hypotheses),
        data_completeness=1.0
        - (len(gaps) / max(1, len(inputs.doc_index) + len(gaps))),
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
            entities=seeds,
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

    # ---- structured findings ----------------------------------------------
    structured_findings: list[StructuredFinding] = []
    try:
        for idx, pat in enumerate([p for p in patterns if not p.excluded][:5]):
            structured_findings.append(
                StructuredFinding(
                    finding_id=f"F{idx + 1:03d}",
                    title=pat.title,
                    finding_type=pat.kind,
                    objective=scope_label(inputs),
                    why=pat.why or "",
                    entities=[e for e in resolved if e.canonical_id in (pat.entity_keys or [])][:3],
                    analytical_basis=pat.analytical_basis or AnalyticalBasis(),
                    relationships=[r for r in relationships if set(r.entities) & set(pat.entities)][:3],
                    patterns=[pat],
                    supporting_evidence=[ev for ev in pat.evidence if ev.stance == "supports"][:5],
                    contradictory_evidence=[ev for ev in pat.evidence if ev.stance == "contradicts"][:5],
                    alternative_explanations=pat.innocent_alternatives[:3],
                    assessment={
                        "investigative_relevance": pat.investigative_relevance.model_dump()
                        if pat.investigative_relevance
                        else None,
                        "evidence_strength": pat.evidence_strength.model_dump()
                        if pat.evidence_strength
                        else None,
                        "strength": pat.strength,
                    },
                    data_gaps=[g for g in gaps if any(e in g.entities for e in pat.entities)][:3],
                    focused_graph=_focused_graph(
                        inputs.snapshot,
                        pat.entity_keys or [],
                        doc_index=inputs.doc_index,
                        centrality=inputs.centrality,
                    ),
                    next_investigative_direction=steps[0].action if steps else "",
                )
            )
    except Exception:
        structured_findings = []

    # ---- deterministic explanation ----------------------------------------
    live_patterns = [p for p in patterns if not p.excluded]
    observation = (
        f"{len(resolved)} entity/entities in focus; {len(relationships)} relationship(s), "
        f"{len(live_patterns)} live pattern(s), {len(hypotheses)} hypothesis/hypotheses; "
        f"scope {inputs.mode} over {len(inputs.case_ids)} case(s), "
        f"{len(inputs.snapshot.nodes or {})} nodes, {len(inputs.snapshot.edges or [])} edges, "
        f"{len(inputs.doc_index)} documents."
    )
    interpretation = (
        "Convergence: " + convergence["note"]
        if convergence.get("note")
        else "No convergence reading."
    )
    basis_text = (
        f"{len(hypotheses)} hypothesis(es) formed from the highest-betweenness entities"
        if hypotheses
        else "the scope's own patterns — this analysis formed no hypothesis"
    )
    assessment_text = (
        f"Overall strength {overall_strength} (confidence {overall_confidence:.0%}) over {basis_text}. "
        + (
            "Independent streams agree — follow the top reading first."
            if convergence.get("converges")
            else "Streams do not converge — treat every reading as provisional."
        )
    )
    caveats: list[str] = []
    if not resolved:
        caveats.append("No entity could be placed in focus for this scope.")
    major_count = sum(
        1 for h in hypotheses if h.strength_factors.contradiction_level == "major"
    )
    if major_count:
        caveats.append(f"{major_count} hypothesis(es) carry major contradictions.")

    # ---- AI narrative (optional, validated) --------------------------------
    question = (
        f"Analyze the {scope_label(inputs).lower()}."
        if mode != "person"
        else f"Analyze the network around {getattr(inputs, 'person_name', person_key)}."
    )
    objective = (
        f"Network analysis of {scope_label(inputs)} — metrics, communities, cross-case "
        "links, patterns, findings and next direction."
    )
    brief = build_investigation_prompt(
        question=question,
        scope_summary=observation,
        entity_lines=[
            f"{e.display_name} ({e.label}, type={e.entity_type}, {e.matched_by}, confidence {e.confidence}, "
            f"legal_status={e.legal_status or 'unknown'}, network_role={e.network_role or 'unknown'})"
            for e in seeds
        ],
        relationship_lines=[
            f"[{r.kind}/{r.inference_label}] {r.title}" for r in relationships
        ],
        pattern_lines=[
            f"[{p.kind}/{p.strength}] {p.title}" + (" (set aside)" if p.excluded else "")
            for p in patterns
        ],
        hypothesis_lines=[
            f"{h.id} [{h.strength}]: {h.statement}" for h in hypotheses
        ],
        convergence_line=convergence.get("note", ""),
        gap_lines=[f"[{g.category}] {g.description}" for g in gaps],
        memory_lines=[],
    )

    await _report("REASONING", 75, "Running investigator reasoning model (may take time)")
    stage_t = time.monotonic()
    gateway = get_ai_gateway()
    model_section: ModelSection = await gateway.investigate_narrative(
        question=question,
        brief=brief,
        investigation_id=new_uuid(),
        entities=seeds,
        user_id=getattr(principal, "id", None),
        session=session,
        timeout_override=max(getattr(gateway.settings, "ai_timeout_s", 180.0), 180.0),
    )
    _mark("narrative_ms", stage_t)
    # The reasoning gateway appends its AI_QUERY audit row to *this* session.
    # Commit now so the reporter (which persists progress on a separate session)
    # is never blocked on an uncommitted write transaction while the job runs.
    await session.commit()

    ai_status = "COMPLETED"
    if not model_section.available:
        reason = model_section.reason or "unknown"
        r_lower = reason.lower()
        if "timeout" in r_lower:
            ai_status = "AI_TIMEOUT"
            await _report("AI_TIMEOUT", 95, "AI reasoning timed out — deterministic analysis preserved")
        elif (
            "unparseable" in r_lower
            or "invalid_json" in r_lower
            or "invalid_response" in r_lower
            or (("json" in r_lower or "schema" in r_lower) and "invocation_failed" not in r_lower)
        ):
            ai_status = "AI_INVALID_RESPONSE"
            await _report("AI_INVALID_RESPONSE", 95, "AI reasoning response could not be parsed — deterministic analysis preserved")
        elif "rate" in r_lower or "429" in r_lower:
            ai_status = "AI_RATE_LIMITED"
            await _report("AI_RATE_LIMITED", 95, "AI reasoning temporarily unavailable — deterministic analysis preserved")
        elif "auth" in r_lower or "401" in r_lower or "403" in r_lower:
            ai_status = "AI_AUTH_FAILED"
            await _report("AI_AUTH_FAILED", 95, "AI reasoning authentication failed — deterministic analysis preserved")
        else:
            ai_status = "AI_UNAVAILABLE"
            await _report("AI_UNAVAILABLE", 95, "Reasoning model offline/unavailable — deterministic analysis preserved")

        # Synthesize honest, complete deterministic findings so the narrative is not empty
        top_comm = (
            f"{len(centrality.community_members)} community/communities detected."
            if centrality.community_members
            else "Single unified cluster."
        )
        model_section = ModelSection(
            available=False,
            role="investigation_reasoning",
            reason=model_section.reason,
            summary=(
                f"Deterministic network analysis complete for {scope_label(inputs)}. "
                f"Topology: {len(inputs.snapshot.nodes or {})} nodes, {len(inputs.snapshot.edges or [])} edges, "
                f"{top_comm} "
                f"Identified {len(live_patterns)} active pattern(s) and {len(hypotheses)} working hypothesis/hypotheses."
            ),
            observation=observation,
            interpretation=interpretation,
            assessment=assessment_text,
            convergence_note=convergence.get("note", ""),
            caveats=list(model_section.caveats) if model_section.caveats else [
                "AI reasoning model offline or unavailable; graph metrics, centrality, and community detections are 100% mathematically preserved."
            ],
            suggested_next_actions=[s.action for s in steps[:3]],
        )
    else:
        await _report("VALIDATING", 90, "Validating evidence references and canonical IDs")

    await _report("GENERATING_EXPLANATION", 95, "Assembling the network analysis")

    # ---- assembly ----------------------------------------------------------
    analysis = {
        "mode": mode,
        "scope_label": scope_label(inputs),
        "case_ids": inputs.case_ids,
        "case_number": inputs.case_number,
        "case_title": inputs.case_title,
        "person_key": person_key if mode == "person" else None,
        "person_name": getattr(inputs, "person_name", None),
        "graph": {
            "nodes": len(inputs.snapshot.nodes or {}),
            "edges": len(inputs.snapshot.edges or []),
            "communities": len(centrality.community_members),
        },
        "subject": subject,
        "metrics": {
            "betweenness": _top_metric(centrality, inputs.snapshot, "betweenness", METRIC_TOP, subject),
            "degree": _top_metric(centrality, inputs.snapshot, "degree", METRIC_TOP, subject),
            "weighted_degree": _top_metric(centrality, inputs.snapshot, "weighted_degree", METRIC_TOP, subject),
            "pagerank": _top_metric(centrality, inputs.snapshot, "pagerank", METRIC_TOP, subject),
            "explanations": _METRIC_EXPLANATIONS,
        },
        "communities": _communities(centrality, inputs.snapshot, COMMUNITY_TOP),
        "cross_case": _cross_case(inputs.snapshot, centrality, CROSS_CASE_TOP, subject),
    }

    thread = await create_session(
        session,
        dataset_id=inputs.dataset_id,
        case_id=inputs.case_id,
        scope=inputs.mode,
        title=question,
        created_by=getattr(principal, "id", None),
    )
    response_facts = collect_facts(
        relationships,
        patterns,
        hypotheses,
        entity_keys={key for e in resolved for key in (e.entity_keys or [])},
    )
    alternatives = collect_alternatives(hypotheses, patterns)
    thread.state = record_turn(
        dict(thread.state or {}),
        question=question,
        objective=objective,
        facts=[ev.summary for ev in response_facts],
        hypotheses=[
            {"id": h.id, "statement": h.statement, "strength": h.strength}
            for h in hypotheses
        ],
        entities=[e.display_name for e in resolved],
        gaps=[g.description for g in gaps],
        unresolved=[],
        contradictions=[item.summary for h in hypotheses for item in h.contradicting],
        relationships=[f"{f.kind}: {f.title}" for f in relationships[:10]],
        rejected=[
            {"id": h.id, "statement": h.statement, "reason": h.contradicting[0].summary if h.contradicting else "not supported"}
            for h in hypotheses
            if h.strength == "INSUFFICIENT"
        ],
        findings=[f"{overall_strength}: {fact.summary}" for fact in response_facts[:5]],
    )
    await session.flush()
    # Commit the memory/thread writes so the terminal job update that follows
    # runs against a clean session and cannot deadlock on this transaction.
    await session.commit()

    response = InvestigatorResponse(
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
            nodes_considered=len(inputs.snapshot.nodes or {}),
            edges_considered=len(inputs.snapshot.edges or []),
            documents_considered=len(inputs.doc_index),
        ),
        entities=seeds,
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
            analytical_basis_summary={
                "nodes_considered": len(inputs.snapshot.nodes or {}),
                "edges_considered": len(inputs.snapshot.edges or []),
                "documents_considered": len(inputs.doc_index),
            },
        ),
        gaps=gaps,
        next_steps=steps,
        timeline=timeline,
        focused_graph=focused,
        provenance=provenance,
        memory=memory_section(thread),
        timing_ms=timings,
        structured_findings=structured_findings,
        analytical_basis={
            "nodes_considered": len(inputs.snapshot.nodes or {}),
            "edges_considered": len(inputs.snapshot.edges or []),
            "documents_considered": len(inputs.doc_index),
            "cases_considered": len(inputs.case_ids),
            "communities_detected": len(centrality.community_members),
            "evidence_convergence": evidence_convergence.model_dump(),
            "evidence_strength": evidence_strength.model_dump(),
            "investigative_relevance": investigative_relevance.model_dump(),
        },
        investigative_relevance=investigative_relevance,
        evidence_strength=evidence_strength,
        evidence_convergence=evidence_convergence,
        silent_intermediaries=silent_intermediaries,
        data_quality=data_quality,
        validation_notes=validation_notes,
    )

    return {
        "status": ai_status,
        "response": response.model_dump(),
        "analysis": analysis,
    }


__all__ = ["analyze_network"]
