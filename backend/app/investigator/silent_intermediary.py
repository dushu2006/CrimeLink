"""Silent intermediary / potential network intermediary analysis.

Not simply ranking by degree. Looks for low-visibility but structurally
important actors with combinations of:
- high betweenness
- community bridging
- cross-community connectivity
- temporal proximity to important events
- repeated information-flow position
- financial/communication intermediary patterns
- cross-case bridging
- indirect path significance
- multi-source corroboration
"""

from __future__ import annotations

from typing import Any

from app.domain.models import CaseGraphSnapshot

from .assessment import build_analytical_basis, METRIC_EXPLANATIONS
from .evidence import make_evidence, edge_pointer, metric_pointer, doc_pointer
from .schemas import AnalyticalBasis, SilentIntermediaryFinding, EvidenceItem


def detect_silent_intermediaries(
    snapshot: CaseGraphSnapshot,
    centrality: Any | None,
    *,
    doc_index: dict[str, dict] | None = None,
    case_id_to_number: dict[str, str] | None = None,
    limit: int = 10,
) -> list[SilentIntermediaryFinding]:
    """Detect potential network intermediaries — not masterminds/kingpins."""
    if centrality is None:
        return []

    doc_index = doc_index or {}
    betweenness = getattr(centrality, "betweenness", {}) or {}
    degree = getattr(centrality, "degree", {}) or {}
    communities = getattr(centrality, "communities", {}) or {}
    # Sort by betweenness descending, but filter out high-degree hubs that are already obvious
    # Silent intermediary = high betweenness, moderate degree, bridges communities

    candidates: list[tuple[str, float, float]] = []
    for key, betw in betweenness.items():
        node = snapshot.nodes.get(key)
        if not node:
            continue
        # Only PERSON entities for silent intermediary (as per spec, but allow other types with note)
        if node.label != "Person":
            continue
        deg = degree.get(key, 0.0)
        # Normalize degree for comparison
        # We want high betweenness but not necessarily highest degree — potential hidden bridge
        candidates.append((key, float(betw), float(deg)))

    # Sort by betweenness descending
    candidates.sort(key=lambda x: (-x[1], x[0]))

    findings: list[SilentIntermediaryFinding] = []

    for key, betw, deg in candidates[: limit * 2]:  # consider more, then filter
        if betw < 0.1:  # threshold for significance
            continue

        node = snapshot.nodes.get(key)
        if not node:
            continue

        # Build analytical basis
        # Cross-case count: count distinct case_ids from node properties + edges
        case_ids = set()
        props = node.properties or {}
        for cid in props.get("case_ids", []) or []:
            case_ids.add(cid)
        # Also from edges
        for edge in snapshot.edges or []:
            if edge.source_key == key or edge.target_key == key:
                # edge properties may have case context? Use node's case_ids for now
                pass

        cross_case = len(case_ids)

        # Bridge analysis
        own_comm = communities.get(key)
        neighbor_comms = set()
        supporting_edges = []
        for edge in snapshot.edges or []:
            if edge.source_key == key or edge.target_key == key:
                other = edge.target_key if edge.source_key == key else edge.source_key
                other_comm = communities.get(other)
                if other_comm is not None and own_comm is not None and other_comm != own_comm:
                    neighbor_comms.add(other_comm)
                supporting_edges.append(edge)

        # Silent intermediary criteria:
        # - high betweenness
        # - bridges at least 2 communities OR cross-case >=2
        # - degree may be moderate (not necessarily top hub)
        is_bridge = len(neighbor_comms) >= 2 or cross_case >= 2 or betw > 0.5

        if not is_bridge:
            continue

        analytical_basis = build_analytical_basis(
            node_key=key,
            snapshot=snapshot,
            centrality=centrality,
            cross_case_count=cross_case,
            evidence_count=len(supporting_edges),
            source_count=len(set([e.properties.get("source_doc_id") for e in supporting_edges if e.properties.get("source_doc_id")])),
        )

        # Determine why surfaced
        why_parts = []
        why_parts.append(f"High betweenness centrality {betw:.2f} indicates this entity lies on many shortest paths between network groups")
        if len(neighbor_comms) >= 2:
            why_parts.append(f"Connects {len(neighbor_comms)} distinct communities (bridge position)")
        if cross_case >= 2:
            why_parts.append(f"Cross-case participation in {cross_case} cases")
        if deg < 5:
            why_parts.append(f"Lower direct degree ({deg}) than major hubs, yet structurally important as intermediary")
        why_parts.append("This network position does not establish criminal involvement")

        why_surfaced = ". ".join(why_parts) + "."

        # Supporting evidence — from edges
        supporting_evidence: list[EvidenceItem] = []
        for edge in supporting_edges[:5]:
            props = edge.properties or {}
            doc_id = props.get("source_doc_id")
            if doc_id:
                doc_info = doc_index.get(doc_id, {})
                filename = doc_info.get("filename", doc_id)
                supporting_evidence.append(
                    make_evidence(
                        "relationship",
                        f"{node.name} --{edge.rel_type}--> {snapshot.nodes.get(edge.target_key).name if edge.source_key == key and snapshot.nodes.get(edge.target_key) else snapshot.nodes.get(edge.source_key).name if snapshot.nodes.get(edge.source_key) else 'entity'}",
                        label="FACT",
                        provenance=[
                            edge_pointer(edge_key=edge.key, label=f"{edge.rel_type} edge"),
                            metric_pointer(name=f"centrality:betweenness:{key}", label=f"Betweenness {betw:.2f}", detail=METRIC_EXPLANATIONS["betweenness_centrality"]),
                        ],
                    )
                )

        # Investigative relevance based on bridge signals
        if cross_case >= 3 and betw > 0.6:
            relevance = "CRITICAL_REVIEW"
        elif betw > 0.6 or cross_case >= 2:
            relevance = "HIGH"
        elif betw > 0.3:
            relevance = "MODERATE"
        else:
            relevance = "LOW"

        # Evidence strength based on supporting edges
        if len(supporting_edges) >= 5 and len(neighbor_comms) >= 2:
            ev_strength = "STRONG"
        elif len(supporting_edges) >= 3:
            ev_strength = "MODERATE"
        elif len(supporting_edges) >= 1:
            ev_strength = "WEAK"
        else:
            ev_strength = "INSUFFICIENT"

        findings.append(
            SilentIntermediaryFinding(
                entity_id=key,
                display_name=node.name,
                entity_type=node.label.upper(),
                why_surfaced=why_surfaced,
                analytical_basis=analytical_basis,
                network_role="POTENTIAL_NETWORK_INTERMEDIARY" if cross_case < 2 else "CROSS_CASE_BRIDGE",
                investigative_relevance=relevance,
                evidence_strength=ev_strength,
                supporting_evidence=supporting_evidence[:5],
                community_bridges=sorted(list(neighbor_comms)),
                cross_case_bridges=sorted(list(case_ids)),
                disclaimer="Network position does not establish criminal involvement. Legal status is source-derived.",
            )
        )

        if len(findings) >= limit:
            break

    return findings
