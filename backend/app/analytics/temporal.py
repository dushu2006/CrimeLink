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
