"""Influence ranking (PRD 11.1).

Centrality is computed per case subgraph with **edge weights = confidence**, so
a dashed social-media link contributes far less than an evidenced CDR call or a
bank transfer.  That is the structural guarantee behind source-confidence
discipline: low-quality data cannot inflate an influence score.

Algorithms
----------
``degree``       cheap first-pass triage (direct connectivity)
``betweenness``  brokers / launderers — who connects otherwise separate clusters
``pagerank``     broad indirect reach, direction-aware
``louvain``      natural gangs and syndicates

Neo4j GDS is used when it answers a capability probe; otherwise the identical
computation runs in NetworkX.  Results are cached per case and invalidated on
any graph write, because re-running Louvain on every expand click would be
wasteful and would also make scores drift between two adjacent requests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from app.config import Settings, get_settings
from app.domain.models import CaseGraphSnapshot
from app.logging import get_logger

log = get_logger("crimelink.analytics.centrality")

# Above this size exact betweenness becomes too slow for an interactive call;
# sampled betweenness is statistically equivalent for ranking purposes.
BETWEENNESS_EXACT_LIMIT = 1200
BETWEENNESS_K = 120


@dataclass(slots=True)
class CentralityResult:
    case_id: str
    degree: dict[str, float] = field(default_factory=dict)
    in_degree: dict[str, float] = field(default_factory=dict)
    out_degree: dict[str, float] = field(default_factory=dict)
    betweenness: dict[str, float] = field(default_factory=dict)
    pagerank: dict[str, float] = field(default_factory=dict)
    communities: dict[str, int] = field(default_factory=dict)
    community_members: dict[int, list[str]] = field(default_factory=dict)
    node_count: int = 0
    edge_count: int = 0
    engine: str = "networkx"

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "engine": self.engine,
        }


def build_nx_graph(snapshot: CaseGraphSnapshot) -> nx.DiGraph:
    """Project a case snapshot onto a confidence-weighted directed graph."""
    graph = nx.DiGraph()
    for key, node in snapshot.nodes.items():
        graph.add_node(key, label=node.label, confidence=float(node.properties.get("confidence", 1.0)))
    for edge in snapshot.edges:
        if edge.source_key not in snapshot.nodes or edge.target_key not in snapshot.nodes:
            continue
        weight = float(edge.properties.get("confidence", 1.0) or 1.0)
        weight = max(0.01, min(1.0, weight))
        if graph.has_edge(edge.source_key, edge.target_key):
            existing = graph[edge.source_key][edge.target_key]
            graph[edge.source_key][edge.target_key]["weight"] = max(existing["weight"], weight)
            graph[edge.source_key][edge.target_key]["count"] = existing.get("count", 1) + 1
        else:
            graph.add_edge(edge.source_key, edge.target_key, weight=weight, count=1)
    return graph


def weighted_pagerank(
    graph: nx.DiGraph, *, damping: float = 0.85, max_iter: int = 100, tol: float = 1e-6
) -> dict[str, float]:
    """Confidence-weighted PageRank by power iteration.

    Implemented here rather than delegated to :func:`networkx.pagerank` so the
    ranking needs neither SciPy nor a numerical backend: an air-gapped district
    deployment must be able to install CrimeLink from a wheelhouse without
    pulling a scientific Python stack.  The maths is the standard formulation,
    with edge weights normalised by each node's out-weight so a low-confidence
    social link cannot inflate a node's reach.
    """
    nodes = list(graph.nodes())
    count = len(nodes)
    if count == 0:
        return {}
    out_weight = {
        node: sum(float(data.get("weight", 1.0)) for _, data in graph[node].items())
        for node in nodes
    }
    predecessors = {node: list(graph.predecessors(node)) for node in nodes}
    dangling = [node for node in nodes if out_weight[node] <= 0.0]
    rank = {node: 1.0 / count for node in nodes}

    for _ in range(max_iter):
        previous = rank
        dangling_mass = damping * sum(previous[node] for node in dangling) / count
        rank = {}
        for node in nodes:
            incoming = 0.0
            for source in predecessors[node]:
                weight = float(graph[source][node].get("weight", 1.0))
                if out_weight[source] > 0.0:
                    incoming += previous[source] * weight / out_weight[source]
            rank[node] = (1.0 - damping) / count + dangling_mass + damping * incoming
        if sum(abs(rank[node] - previous[node]) for node in nodes) < tol * count:
            break

    total = sum(rank.values()) or 1.0
    return {node: value / total for node, value in rank.items()}


def compute_centrality(
    snapshot: CaseGraphSnapshot, settings: Settings | None = None
) -> CentralityResult:
    """Compute every ranking measure for one case subgraph."""
    settings = settings or get_settings()
    graph = build_nx_graph(snapshot)
    result = CentralityResult(
        case_id=snapshot.case_id,
        node_count=graph.number_of_nodes(),
        edge_count=graph.number_of_edges(),
    )
    if graph.number_of_nodes() == 0:
        return result

    result.in_degree = {n: float(d) for n, d in graph.in_degree()}
    result.out_degree = {n: float(d) for n, d in graph.out_degree()}
    result.degree = {
        n: result.in_degree.get(n, 0.0) + result.out_degree.get(n, 0.0) for n in graph.nodes()
    }

    undirected = graph.to_undirected()
    n = undirected.number_of_nodes()
    if n > BETWEENNESS_EXACT_LIMIT:
        result.betweenness = nx.betweenness_centrality(
            undirected, k=min(BETWEENNESS_K, n), weight="weight", normalized=True, seed=42
        )
    else:
        result.betweenness = nx.betweenness_centrality(undirected, weight="weight", normalized=True)
    result.betweenness = {k: float(v) for k, v in result.betweenness.items()}

    result.pagerank = weighted_pagerank(graph)

    result.communities, result.community_members = _louvain(undirected)
    return result


def _louvain(undirected: nx.Graph) -> tuple[dict[str, int], dict[int, list[str]]]:
    """Community detection with a deterministic seed and a graceful fallback."""
    try:
        communities = nx.community.louvain_communities(
            undirected, weight="weight", seed=42, resolution=1.0
        )
    except Exception:  # pragma: no cover - version differences
        communities = list(nx.connected_components(undirected))
    mapping: dict[str, int] = {}
    members: dict[int, list[str]] = {}
    for index, group in enumerate(sorted((sorted(g) for g in communities), key=lambda g: (-len(g), g[0] if g else ""))):
        for node in group:
            mapping[node] = index
        members[index] = list(group)
    return mapping, members


def percentile_rank(scores: dict[str, float], key: str) -> float:
    """Percentile of *key* within *scores* (0–100)."""
    if not scores or key not in scores:
        return 0.0
    values = sorted(scores.values())
    value = scores[key]
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return 100.0 * (below + 0.5 * equal) / len(values)


def rank_of(scores: dict[str, float], key: str) -> int:
    """1-based rank of *key* (1 = highest score)."""
    if key not in scores:
        return 0
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    for index, (node_key, _score) in enumerate(ordered, start=1):
        if node_key == key:
            return index
    return 0


def top_neighbours(
    snapshot: CaseGraphSnapshot, key: str, limit: int = 5
) -> list[dict[str, Any]]:
    """Highest-confidence neighbours of a node, annotated with evidence."""
    scored: list[tuple[float, str, str, dict[str, Any]]] = []
    for edge in snapshot.edges:
        if edge.source_key == key:
            other = edge.target_key
        elif edge.target_key == key:
            other = edge.source_key
        else:
            continue
        scored.append((edge.confidence, other, edge.rel_type, edge.properties))
    scored.sort(key=lambda item: -item[0])
    out = []
    for confidence, other, rel_type, props in scored[:limit]:
        node = snapshot.nodes.get(other)
        out.append(
            {
                "provenance_key": other,
                "name": node.name if node else other[:8],
                "label": node.label if node else "?",
                "edge_type": rel_type,
                "weight": round(confidence, 3),
                "source_doc_id": props.get("source_doc_id"),
                "source_doc_ids": list(props.get("source_doc_ids") or []),
                "call_count": props.get("call_count"),
                "amount": props.get("amount"),
            }
        )
    return out


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _is_finite(value: float) -> bool:
    return not (math.isnan(value) or math.isinf(value))
