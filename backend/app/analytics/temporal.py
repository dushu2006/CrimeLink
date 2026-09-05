"""Temporal pathfinding (PRD 11.4).

"Show me how suspect A connects to suspect B" must respect chronology.  If A
called B in 2024 and B paid C in 2023, then A→B→C is not a plausible
money-or-instruction *flow*: it runs backwards in time.  Traversal therefore
only follows an edge when its timestamp is at or after the previous edge's
timestamp (plus a configurable slack), producing chronologically coherent paths.

Depth is capped at 4 and results are paginated, because the number of simple
paths grows factorially and an unbounded answer helps nobody.
"""

from __future__ import annotations

from typing import Any

from app.domain.models import CaseGraphSnapshot
from app.logging import get_logger

log = get_logger("crimelink.analytics.temporal")


def _edge_ts(edge: Any) -> str | None:
    for key in ("ts", "timestamp", "first_ts", "last_ts"):
        value = edge.properties.get(key)
        if value:
            return str(value)
    return None


# Relationship types that describe a *state of the world* (ownership, usage,
# membership) rather than a dated occurrence.  They carry no timestamp of
# their own, so a temporal query keeps them only when they connect entities
# that are already active inside the window — they are structural glue, never
# the reason an entity appears.
UNDATED_ATTRIBUTE_REL_TYPES = frozenset(
    {
        "OWNS_ACCOUNT",
        "OWNS_VEHICLE",
        "USES_PHONE",
        "MEMBER_OF",
        "CONTROLS_ACCOUNT",
        "ASSOCIATE_OF",
        "RELATIVE_OF",
        "NAMED_ACCOMPLICE_OF",
        "ACCUSED_IN",
        "LINKED_ON_SOCIAL",
    }
)


def build_temporal_graph(
    snapshot: Any,
    *,
    target_key: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    depth: int = 3,
    limit: int = 400,
) -> dict[str, Any]:
    """Construct the time-constrained subgraph of a case snapshot.

    This is the *graph* counterpart to :func:`find_temporal_paths` (which
    answers "what path connects X and Y").  It answers "which entities and
    events were active — or connected to the target — inside this window".

    Construction is deliberately deterministic and evidence-preserving:

    * an edge participates when its own timestamp lies inside ``[from_ts,
      to_ts]``;
    * undated attribute edges (OWNS_ACCOUNT, USES_PHONE, …) are kept only as
      structural glue between entities that are already active in the window,
      or between the target and its immediate assets;
    * when ``target_key`` is supplied, the result is bounded to that target's
      neighbourhood (BFS up to ``depth``) over the time-filtered edges.

    Returns ``{"nodes": [...], "edges": [...], "time_range": {...},
    "events": [...], "empty_reason": str | None}`` where nodes/edges are the
    *domain* objects (:class:`GraphNode` / :class:`GraphEdge`) — the API layer
    serialises them with the same ``_node_row`` / ``_edge_row`` helpers used by
    every other graph endpoint.
    """
    if target_key is not None and target_key not in snapshot.nodes:
        return {
            "nodes": [],
            "edges": [],
            "time_range": {"from": from_ts, "to": to_ts},
            "events": [],
            "empty_reason": "target_not_in_case",
        }

    dated: list[Any] = []
    active: set[str] = set()
    for edge in snapshot.edges:
        ts = _edge_ts(edge)
        if ts is None:
            continue
        if from_ts is not None and ts < from_ts:
            continue
        if to_ts is not None and ts > to_ts:
            continue
        dated.append(edge)
        active.add(edge.source_key)
        active.add(edge.target_key)

    if not dated and target_key is None:
        return {
            "nodes": [],
            "edges": [],
            "time_range": {"from": from_ts, "to": to_ts},
            "events": [],
            "empty_reason": "no_dated_edges_in_window",
        }

    # Structural glue: undated attribute edges among entities already active in
    # the window (or attached to the target), so a person's phone/account still
    # appears even though ownership itself has no timestamp.
    glue: list[Any] = []
    for edge in snapshot.edges:
        if edge.rel_type not in UNDATED_ATTRIBUTE_REL_TYPES:
            continue
        if _edge_ts(edge) is not None:
            continue
        a, b = edge.source_key, edge.target_key
        if target_key in (a, b) or (a in active and b in active):
            glue.append(edge)

    # The target is always visible even when it has no dated edge in the window
    # (e.g. its only connection is an owned vehicle sighted in the window).
    if target_key is not None:
        active.add(target_key)
        # Pull the target's immediate (1-hop) assets so the graph is not a
        # single lonely node when it has no dated activity.
        for edge in snapshot.edges:
            if edge.source_key == target_key or edge.target_key == target_key:
                if _edge_ts(edge) is None and edge.rel_type in UNDATED_ATTRIBUTE_REL_TYPES:
                    glue.append(edge)
                    active.add(edge.source_key)
                    active.add(edge.target_key)

    candidate_edges = dated + glue
    if target_key is not None:
        candidate_edges = _bounded_neighbourhood(
            snapshot, target_key, candidate_edges, depth=depth
        )
        # Fall back to the full time-filtered view when the target's bounded
        # neighbourhood is empty (the target is connected only via longer paths).
        if not candidate_edges:
            candidate_edges = dated + glue

    kept_keys: set[str] = set()
    kept_edges: list[Any] = []
    for edge in candidate_edges:
        if edge.source_key in snapshot.nodes and edge.target_key in snapshot.nodes:
            kept_edges.append(edge)
            kept_keys.add(edge.source_key)
            kept_keys.add(edge.target_key)

    if target_key is not None and target_key not in kept_keys and kept_edges:
        kept_keys.add(target_key)

    nodes = [snapshot.nodes[k] for k in sorted(kept_keys) if k in snapshot.nodes]
    if len(nodes) > limit:
        nodes = sorted(nodes, key=lambda n: n.provenance_key)[:limit]
        keep = {n.provenance_key for n in nodes}
        kept_edges = [
            e for e in kept_edges if e.source_key in keep and e.target_key in keep
        ]

    ts_values = sorted(
        {str(t) for t in (_edge_ts(e) for e in kept_edges) if t}
    )
    event_nodes = [
        n
        for n in nodes
        if n.label in ("Event", "EVENT") and n.properties.get("timestamp")
    ]
    events = sorted(
        (
            {
                "provenance_key": n.provenance_key,
                "event_type": n.properties.get("event_type"),
                "name": n.name,
                "timestamp": n.properties.get("timestamp"),
                "description": n.properties.get("description"),
            }
            for n in event_nodes
        ),
        key=lambda item: str(item.get("timestamp") or ""),
    )

    return {
        "nodes": nodes,
        "edges": kept_edges,
        "time_range": {
            "from": from_ts,
            "to": to_ts,
            "first": ts_values[0] if ts_values else None,
            "last": ts_values[-1] if ts_values else None,
        },
        "events": events,
        "empty_reason": "no_dated_edges_in_window" if not kept_edges else None,
    }


def _bounded_neighbourhood(
    snapshot: Any,
    root_key: str,
    edges: list[Any],
    *,
    depth: int,
) -> list[Any]:
    """BFS over *edges* from *root_key* up to *depth* hops."""
    adjacency: dict[str, list[Any]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source_key, []).append(edge)
        adjacency.setdefault(edge.target_key, []).append(edge)
    seen_nodes = {root_key}
    kept: list[Any] = []
    frontier = {root_key}
    for _ in range(max(1, depth)):
        next_frontier: set[str] = set()
        for node_key in sorted(frontier):
            for edge in adjacency.get(node_key, []):
                other = (
                    edge.target_key if edge.source_key == node_key else edge.source_key
                )
                kept.append(edge)
                if other not in seen_nodes:
                    seen_nodes.add(other)
                    next_frontier.add(other)
        frontier = next_frontier
        if not frontier:
            break
    # De-duplicate while preserving order.
    unique: list[Any] = []
    seen_edges: set[int] = set()
    for edge in kept:
        marker = id(edge)
        if marker in seen_edges:
            continue
        seen_edges.add(marker)
        unique.append(edge)
    return unique


def find_temporal_paths(
    snapshot: CaseGraphSnapshot,
    source_key: str,
    target_key: str,
    *,
    max_depth: int = 4,
    slack_seconds: int = 0,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return chronologically valid paths between two entities.

    ISO-8601 UTC timestamps sort lexicographically, so string comparison is a
    correct and cheap ordering test for this data.
    """
    if source_key not in snapshot.nodes or target_key not in snapshot.nodes:
        return []

    adjacency: dict[str, list[tuple[str, Any]]] = {}
    for edge in snapshot.edges:
        ts = _edge_ts(edge)
        adjacency.setdefault(edge.source_key, []).append((edge.target_key, edge))
        adjacency.setdefault(edge.target_key, []).append((edge.source_key, edge))
        _ = ts  # timestamps are read per-edge during traversal

    results: list[dict[str, Any]] = []

    def _walk(current: str, visited: set[str], nodes: list[str], edges: list[Any], last_ts: str | None) -> None:
        if len(results) >= limit or len(nodes) > max_depth + 1:
            return
        if current == target_key and len(nodes) > 1:
            results.append(_describe(snapshot, nodes, edges))
            return
        for neighbour, edge in adjacency.get(current, []):
            if neighbour in visited:
                continue
            ts = _edge_ts(edge)
            if ts is None:
                # An undated edge cannot participate in a chronological chain;
                # including it would silently weaken the guarantee.
                continue
            if last_ts is not None and not _within_slack(ts, last_ts, slack_seconds):
                continue
            _walk(
                neighbour,
                visited | {neighbour},
                nodes + [neighbour],
                edges + [(current, neighbour, edge)],
                ts,
            )

    _walk(source_key, {source_key}, [source_key], [], None)
    results.sort(key=lambda item: len(item["path"]))
    return results[:limit]


def _within_slack(ts: str, previous_ts: str, slack_seconds: int) -> bool:
    """True when *ts* is at or after *previous_ts* (minus slack)."""
    if ts >= previous_ts:
        return True
    if slack_seconds <= 0:
        return False
    from datetime import datetime

    def _parse(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    current, previous = _parse(ts), _parse(previous_ts)
    if current is None or previous is None:
        return ts >= previous_ts
    from datetime import timedelta

    return current >= previous - timedelta(seconds=slack_seconds)


def _describe(snapshot: CaseGraphSnapshot, nodes: list[str], edges: list[Any]) -> dict[str, Any]:
    names: list[str] = []
    for key in nodes:
        node = snapshot.nodes.get(key)
        names.append(node.name if node else key[:8])
    steps: list[dict[str, Any]] = []
    evidence: list[str] = []
    for source, target, edge in edges:
        steps.append(
            {
                "from": source,
                "to": target,
                "relation": edge.rel_type,
                "ts": _edge_ts(edge),
                "confidence": edge.confidence,
                "amount": edge.properties.get("amount"),
                "call_count": edge.properties.get("call_count"),
                "source_doc_id": edge.properties.get("source_doc_id"),
            }
        )
        for doc in edge.properties.get("source_doc_ids") or [edge.properties.get("source_doc_id")]:
            if doc and doc not in evidence:
                evidence.append(doc)
    return {
        "path": names,
        "provenance_keys": nodes,
        "hops": len(edges),
        "steps": steps,
        "evidence_doc_ids": evidence,
    }
