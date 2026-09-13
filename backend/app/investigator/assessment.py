"""Deterministic investigative assessments — relevance, strength, convergence.

All functions here are deterministic, auditable, and never infer criminality
from graph centrality. They produce transparent scores with explainable basis.
"""

from __future__ import annotations

from typing import Any

from app.domain.enums import EvidenceConvergence, EvidenceStrength, InvestigativeRelevance, NetworkRole
from app.domain.models import CaseGraphSnapshot

from .schemas import (
    AnalyticalBasis,
    EvidenceConvergenceAssessment,
    EvidenceStrengthAssessment,
    InvestigativeRelevanceAssessment,
    StrengthFactors,
)


# ---------------------------------------------------------------------------
# Metric explanations — honest, non-criminal language
# ---------------------------------------------------------------------------

METRIC_EXPLANATIONS = {
    "degree_centrality": "Measures how directly connected the entity is within the network. High degree means many direct relationships, not criminality.",
    "weighted_degree": "Sum of confidence-weighted connections. Higher weight means more evidence-backed relationships, not guilt.",
    "betweenness_centrality": "Measures how often the entity lies on shortest paths between other entities. High betweenness indicates a potential bridge or intermediary position, not criminal involvement.",
    "pagerank": "Measures importance based partly on connections to other important entities. High PageRank means structurally central, not legally culpable.",
    "community_id": "Identifies which community/cluster the entity belongs to via Louvain detection.",
    "cross_case_count": "Number of distinct cases the entity participates in. Cross-case presence is an investigative signal, not a criminal status.",
    "relationship_count": "Total number of relationships involving this entity.",
    "evidence_count": "Number of distinct evidence records supporting relationships.",
    "source_count": "Number of independent source categories (CDR, financial, surveillance, etc.) corroborating.",
}


def build_analytical_basis(
    *,
    node_key: str,
    snapshot: CaseGraphSnapshot,
    centrality: Any | None,
    cross_case_count: int | None = None,
    evidence_count: int | None = None,
    source_count: int | None = None,
) -> AnalyticalBasis:
    """Build analytical basis for one entity with explanations."""
    basis = AnalyticalBasis()
    metrics: dict[str, Any] = {}
    explanations: dict[str, str] = {}

    if centrality is not None:
        degree = getattr(centrality, "degree", {}).get(node_key)
        w_degree = getattr(centrality, "weighted_degree", {}).get(node_key)
        betweenness = getattr(centrality, "betweenness", {}).get(node_key)
        pagerank = getattr(centrality, "pagerank", {}).get(node_key)
        community = getattr(centrality, "communities", {}).get(node_key)
        community_members = getattr(centrality, "community_members", {}).get(community, []) if community is not None else []

        if degree is not None:
            basis.degree_centrality = round(float(degree) / max(1, len(snapshot.nodes) - 1), 3) if len(snapshot.nodes) > 1 else 0.0
            # Also keep raw degree
            metrics["raw_degree"] = float(degree)
            explanations["degree_centrality"] = METRIC_EXPLANATIONS["degree_centrality"]
        if w_degree is not None:
            basis.weighted_degree = round(float(w_degree), 3)
            explanations["weighted_degree"] = METRIC_EXPLANATIONS["weighted_degree"]
        if betweenness is not None:
            basis.betweenness_centrality = round(float(betweenness), 3)
            explanations["betweenness_centrality"] = METRIC_EXPLANATIONS["betweenness_centrality"]
        if pagerank is not None:
            basis.pagerank = round(float(pagerank), 4)
            explanations["pagerank"] = METRIC_EXPLANATIONS["pagerank"]
        if community is not None:
            basis.community_id = community
            basis.community_size = len(community_members)
            explanations["community_id"] = METRIC_EXPLANATIONS["community_id"]
            metrics["community_size"] = len(community_members)

    # Cross-case participation
    if cross_case_count is not None:
        basis.cross_case_count = cross_case_count
        explanations["cross_case_count"] = METRIC_EXPLANATIONS["cross_case_count"]

    # Relationship count
    rel_count = 0
    for edge in snapshot.edges or []:
        if edge.source_key == node_key or edge.target_key == node_key:
            rel_count += 1
    basis.relationship_count = rel_count
    explanations["relationship_count"] = METRIC_EXPLANATIONS["relationship_count"]

    if evidence_count is not None:
        basis.evidence_count = evidence_count
        explanations["evidence_count"] = METRIC_EXPLANATIONS["evidence_count"]
    if source_count is not None:
        basis.source_count = source_count
        explanations["source_count"] = METRIC_EXPLANATIONS["source_count"]

    # Bridge info
    if centrality is not None:
        communities = getattr(centrality, "communities", {}) or {}
        own_comm = communities.get(node_key)
        if own_comm is not None:
            # Find neighboring communities
            neighbor_comms = set()
            for edge in snapshot.edges or []:
                other = None
                if edge.source_key == node_key:
                    other = edge.target_key
                elif edge.target_key == node_key:
                    other = edge.source_key
                if other and communities.get(other) is not None and communities.get(other) != own_comm:
                    neighbor_comms.add(communities.get(other))
            if neighbor_comms:
                basis.bridge_info = {
                    "own_community": own_comm,
                    "bridges_to": sorted(list(neighbor_comms)),
                    "bridge_count": len(neighbor_comms),
                }

    basis.metrics = metrics
    basis.explanations = explanations
    return basis


def assess_investigative_relevance(
    *,
    analytical_basis: AnalyticalBasis,
    cross_case_count: int = 0,
    evidence_convergence_type: str = "NONE",
    has_contradictions: bool = False,
    data_completeness: float = 1.0,
) -> InvestigativeRelevanceAssessment:
    """Transparent deterministic investigative relevance — never guilt probability.

    Combines:
    - network structure (betweenness, degree, pagerank)
    - cross-case relevance
    - evidence convergence
    - contradictions
    - data completeness
    """
    components: dict[str, Any] = {}
    basis_notes: list[str] = []
    score = 0.0

    # Network structure signals (0-0.4)
    betw = analytical_basis.betweenness_centrality or 0.0
    deg = analytical_basis.degree_centrality or 0.0
    pr = analytical_basis.pagerank or 0.0

    if betw > 0.7:
        score += 0.25
        components["high_betweenness"] = betw
        basis_notes.append(f"High betweenness centrality {betw} indicates bridge position")
    elif betw > 0.4:
        score += 0.15
        components["moderate_betweenness"] = betw
        basis_notes.append(f"Moderate betweenness {betw}")

    if deg > 0.7:
        score += 0.1
        components["high_degree"] = deg
        basis_notes.append(f"High degree centrality {deg}")
    elif deg > 0.4:
        score += 0.05
        components["moderate_degree"] = deg

    if pr > 0.1:
        score += 0.05
        components["pagerank"] = pr

    # Cross-case relevance (0-0.3)
    if cross_case_count >= 3:
        score += 0.3
        components["cross_case_3plus"] = cross_case_count
        basis_notes.append(f"Cross-case participation in {cross_case_count} cases")
    elif cross_case_count == 2:
        score += 0.2
        components["cross_case_2"] = cross_case_count
        basis_notes.append(f"Cross-case participation in 2 cases")
    elif cross_case_count == 1:
        score += 0.05

    # Evidence convergence (0-0.2)
    if evidence_convergence_type == EvidenceConvergence.INDEPENDENT_SOURCE_CONVERGENCE.value:
        score += 0.2
        components["independent_convergence"] = True
        basis_notes.append("Independent source convergence")
    elif evidence_convergence_type == EvidenceConvergence.MULTI_SOURCE.value:
        score += 0.15
        components["multi_source"] = True
        basis_notes.append("Multi-source corroboration")
    elif evidence_convergence_type == EvidenceConvergence.MULTI_RECORD.value:
        score += 0.08
        components["multi_record"] = True

    # Contradictions reduce relevance clarity, but don't hide
    if has_contradictions:
        score = max(0.0, score - 0.1)
        components["has_contradictions"] = True
        basis_notes.append("Contradictory evidence exists — requires review")

    # Data completeness
    if data_completeness < 0.5:
        components["low_completeness"] = data_completeness
        basis_notes.append(f"Data completeness low ({data_completeness:.0%}) — gaps exist")
    components["data_completeness"] = data_completeness

    # Determine relevance level
    if score >= 0.7:
        relevance = InvestigativeRelevance.CRITICAL_REVIEW.value
    elif score >= 0.45:
        relevance = InvestigativeRelevance.HIGH.value
    elif score >= 0.2:
        relevance = InvestigativeRelevance.MODERATE.value
    else:
        relevance = InvestigativeRelevance.LOW.value

    explanation = (
        f"Investigative relevance {relevance} based on network structure, "
        f"cross-case signals, and evidence convergence. Score {score:.2f} from "
        f"{len(basis_notes)} signals. This describes why the investigator may want "
        f"to inspect, not probability of guilt."
    )

    return InvestigativeRelevanceAssessment(
        relevance=relevance,
        score=round(score, 3),
        basis=basis_notes,
        components=components,
        explanation=explanation,
    )


def assess_evidence_strength(
    *,
    independent_sources: int,
    corroborating_records: int,
    has_contradictions: bool = False,
    contradiction_level: str = "none",
    temporal_consistency: bool = True,
    provenance_available: bool = True,
) -> EvidenceStrengthAssessment:
    """Transparent evidence strength: WEAK, MODERATE, STRONG, INSUFFICIENT."""
    components: dict[str, Any] = {
        "independent_sources": independent_sources,
        "corroborating_records": corroborating_records,
        "has_contradictions": has_contradictions,
        "contradiction_level": contradiction_level,
        "temporal_consistency": temporal_consistency,
        "provenance_available": provenance_available,
    }
    basis: list[str] = []

    if independent_sources >= 3:
        basis.append(f"{independent_sources} independent source categories corroborate")
    elif independent_sources == 2:
        basis.append(f"{independent_sources} source categories corroborate")
    elif independent_sources == 1:
        basis.append("Single source category")
    else:
        basis.append("No independent source recorded")

    if corroborating_records >= 5:
        basis.append(f"{corroborating_records} corroborating records")
    elif corroborating_records >= 2:
        basis.append(f"{corroborating_records} corroborating records")
    elif corroborating_records == 1:
        basis.append("Single corroborating record")

    if temporal_consistency:
        basis.append("Temporal alignment is consistent")
    else:
        basis.append("Temporal inconsistency noted")

    if has_contradictions:
        if contradiction_level == "major":
            basis.append("Major contradiction exists — weakens overall strength")
        else:
            basis.append(f"Contradiction level: {contradiction_level}")

    if not provenance_available:
        basis.append("Provenance unavailable — cannot trace to source")

    # Determine strength
    if independent_sources == 0 or corroborating_records == 0:
        strength = EvidenceStrength.INSUFFICIENT.value
    elif independent_sources >= 3 and corroborating_records >= 3 and not has_contradictions:
        strength = EvidenceStrength.STRONG.value
    elif independent_sources >= 2 and corroborating_records >= 2 and contradiction_level != "major":
        strength = EvidenceStrength.MODERATE.value
    elif independent_sources >= 1:
        strength = EvidenceStrength.WEAK.value
    else:
        strength = EvidenceStrength.INSUFFICIENT.value

    explanation = f"Evidence strength {strength}: {'; '.join(basis)}."

    return EvidenceStrengthAssessment(
        strength=strength,
        basis=basis,
        components=components,
        explanation=explanation,
    )


def assess_evidence_convergence(
    *,
    source_types: list[str],
    doc_ids: list[str],
    record_count: int,
) -> EvidenceConvergenceAssessment:
    """Distinguish SINGLE_SOURCE, MULTI_RECORD, MULTI_SOURCE, INDEPENDENT."""
    unique_docs = len(set(doc_ids))
    unique_sources = len(set(source_types))

    if unique_sources >= 2 and unique_docs >= 2:
        # Check if sources are independent categories (CDR vs financial vs surveillance)
        # For simplicity, if we have 2+ distinct source types, treat as independent if >=2 docs
        if unique_sources >= 3:
            conv_type = EvidenceConvergence.INDEPENDENT_SOURCE_CONVERGENCE.value
            explanation = f"Independent source convergence: {unique_sources} source categories ({', '.join(source_types)}) across {unique_docs} documents"
        else:
            conv_type = EvidenceConvergence.MULTI_SOURCE.value
            explanation = f"Multi-source: {unique_sources} categories across {unique_docs} documents"
    elif unique_docs >= 2:
        conv_type = EvidenceConvergence.MULTI_RECORD.value
        explanation = f"Multi-record: {unique_docs} records from same source category"
    elif unique_docs == 1:
        conv_type = EvidenceConvergence.SINGLE_SOURCE.value
        explanation = "Single source: one document/record"
    else:
        conv_type = EvidenceConvergence.NONE.value
        explanation = "No evidence"

    return EvidenceConvergenceAssessment(
        convergence_type=conv_type,
        source_categories=sorted(set(source_types)),
        independent_source_count=unique_sources,
        record_count=record_count,
        explanation=explanation,
    )


def determine_network_role(
    *,
    analytical_basis: AnalyticalBasis,
    cross_case_count: int = 0,
    is_communication_intermediary: bool = False,
    is_financial_intermediary: bool = False,
) -> str:
    """Determine analytical network role — never criminal status."""
    betw = analytical_basis.betweenness_centrality or 0.0
    deg = analytical_basis.degree_centrality or 0.0
    bridge_info = analytical_basis.bridge_info

    if cross_case_count >= 2 and betw > 0.5:
        return NetworkRole.CROSS_CASE_BRIDGE.value
    if bridge_info and bridge_info.get("bridge_count", 0) >= 2:
        return NetworkRole.COMMUNITY_BRIDGE.value
    if betw > 0.7:
        return NetworkRole.BRIDGE.value
    if is_communication_intermediary:
        return NetworkRole.COMMUNICATION_INTERMEDIARY_CANDIDATE.value
    if is_financial_intermediary:
        return NetworkRole.FINANCIAL_INTERMEDIARY_CANDIDATE.value
    if deg > 0.6:
        return NetworkRole.HUB.value
    if deg > 0.3:
        return NetworkRole.CONNECTOR.value
    return NetworkRole.PERIPHERAL.value
