"""Explanation subgraphs — the mandatory companion to every score (PRD 11.2).

A raw centrality number is not actionable intelligence.  An investigator, and
ultimately a prosecutor, needs to know *why* a person was flagged.  CrimeLink
therefore never returns a bare score: every influence response carries the
bridging paths that produced it, the top weighted edges, and the documents that
evidence each one.

"A score without an explanation is a bug, not a feature" — that rule is enforced
by a test which fails when ``GET /graph/nodes/{pk}/influence`` returns a score
with no explanation payload.
"""

from __future__ import annotations

from typing import Any

import networkx as nx

from app.analytics.centrality import (
    CentralityResult,
    build_nx_graph,
    percentile_rank,
    rank_of,
    safe_float,
    top_neighbours,
)
from app.domain.models import CaseGraphSnapshot
from app.logging import get_logger

log = get_logger("crimelink.analytics.explanation")

MAX_BRIDGING_PATHS = 3


def explain_node(
    snapshot: CaseGraphSnapshot, centrality: CentralityResult, key: str
) -> dict[str, Any]:
    """Build the checkable justification for one node's influence score."""
    node = snapshot.nodes.get(key)
    if node is None:
        return {}

    betweenness = float(centrality.betweenness.get(key, 0.0))
    pagerank = float(centrality.pagerank.get(key, 0.0))
    degree = float(centrality.degree.get(key, 0.0))
    community = centrality.communities.get(key)
    neighbours = top_neighbours(snapshot, key, limit=6)

    bridging_paths = _bridging_paths(snapshot, centrality, key)
    summary = _summary(node.name, centrality, key, community, bridging_paths)

    evidence_doc_ids = sorted(
        {
            doc
            for path in bridging_paths
            for doc in path.get("evidence_doc_ids", [])
            if doc
        }
        | {
            doc
            for item in neighbours
            for doc in (item.get("source_doc_ids") or [item.get("source_doc_id")])
            if doc
        }
    )

    return {
        "node": node.name,
        "provenance_key": key,
        "label": node.label,
        "betweenness": round(betweenness, 4),
        "pagerank": round(pagerank, 4),
        "degree": degree,
        "betweenness_percentile": round(percentile_rank(centrality.betweenness, key), 1),
        "rank_in_case": rank_of(centrality.betweenness, key),
        "rank_total": len(centrality.betweenness),
        "community": community,
        "community_size": len(centrality.community_members.get(community, [])) if community is not None else 0,
        "explanation": {
            "summary": summary,
            "bridging_paths": bridging_paths,
            "top_weighted_edges": neighbours,
            "community_members": centrality.community_members.get(community, [])[:25],
            "evidence_doc_ids": evidence_doc_ids,
            "method": (
                "Betweenness, PageRank and degree computed on the case subgraph with "
                "edge weights equal to each relationship's confidence; communities "
                "detected with Louvain modularity."
            ),
        },
    }


def _summary(
    name: str,
    centrality: CentralityResult,
    key: str,
    community: int | None,
    bridging_paths: list[dict[str, Any]],
) -> str:
    rank = rank_of(centrality.betweenness, key)
    total = len(centrality.betweenness)
    percentile = percentile_rank(centrality.betweenness, key)
    if bridging_paths and community is not None:
        touched = {
            path.get("target_community") for path in bridging_paths if path.get("target_community") is not None
        }
        sizes = [
            len(centrality.community_members.get(c, [])) for c in touched
        ]
        if sizes:
            return (
                f"{name} bridges community '{_community_name(community)}' "
                f"({len(centrality.community_members.get(community, []))} members) and "
                f"{len(touched)} other group(s) of {', '.join(str(s) for s in sizes)} members, "
                f"ranking {rank} of {total} in this case by betweenness "
                f"({percentile:.0f}th percentile)."
            )
    if total:
        return (
            f"{name} ranks {rank} of {total} in this case by betweenness "
            f"({percentile:.0f}th percentile) with "
            f"{int(centrality.degree.get(key, 0))} direct connections."
        )
    return f"No centrality data available for {name}."


def _community_name(community: int) -> str:
    return f"Cluster {chr(ord('A') + (community % 26))}"


def _bridging_paths(
    snapshot: CaseGraphSnapshot, centrality: CentralityResult, key: str
) -> list[dict[str, Any]]:
    """Find the shortest paths from this node into *other* communities."""
    if not centrality.communities:
        return []
    graph = build_nx_graph(snapshot)
    if key not in graph:
        return []
    undirected = graph.to_undirected()
    own = centrality.communities.get(key)

    # Distance must reward high-confidence edges.
    for u, v, data in undirected.edges(data=True):
        data["distance"] = 1.0 / max(0.01, float(data.get("weight", 1.0)))

    targets: list[str] = [
        other
        for other in graph.nodes()
        if other != key and centrality.communities.get(other) != own
    ]
    try:
        lengths = nx.single_source_dijkstra_path_length(undirected, key, weight="distance")
    except nx.NetworkXNoPath:  # pragma: no cover
        return []
    targets.sort(key=lambda t: lengths.get(t, float("inf")))

    paths: list[dict[str, Any]] = []
    seen_communities: set[int] = set()
    for target in targets:
        if len(paths) >= MAX_BRIDGING_PATHS:
            break
        target_community = centrality.communities.get(target)
        if target_community is None or target_community == own:
            continue
        if target_community in seen_communities:
            continue
        try:
            path = nx.shortest_path(undirected, key, target, weight="distance")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
        seen_communities.add(target_community)
        paths.append(_describe_path(snapshot, path, target_community))
    return paths


def _describe_path(
    snapshot: CaseGraphSnapshot, path: list[str], target_community: int
) -> dict[str, Any]:
    edge_types: list[str] = []
    evidence: list[str] = []
    names: list[str] = []
    for index, key in enumerate(path):
        node = snapshot.nodes.get(key)
        names.append(node.name if node else key[:8])
        if index + 1 >= len(path):
            continue
        nxt = path[index + 1]
        best = None
        for edge in snapshot.edges:
            forward = edge.source_key == key and edge.target_key == nxt
            backward = edge.source_key == nxt and edge.target_key == key
            if not (forward or backward):
                continue
            if best is None or edge.confidence > best.confidence:
                best = edge
        if best is None:
            edge_types.append("?")
            continue
        label = best.rel_type
        if best.rel_type == "CALLED" and best.properties.get("call_count"):
            label = f"CALLED(count={best.properties['call_count']})"
        edge_types.append(label)
        for doc in best.properties.get("source_doc_ids") or [best.properties.get("source_doc_id")]:
            if doc and doc not in evidence:
                evidence.append(doc)
    return {
        "path": names,
        "provenance_keys": path,
        "edges": edge_types,
        "target_community": target_community,
        "evidence_doc_ids": evidence,
    }
