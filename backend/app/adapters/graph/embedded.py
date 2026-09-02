"""Embedded graph store — NetworkX-backed, dependency-free, air-gap safe.

This is the graph backend used by the ``embedded`` profile: local development,
the automated test suite, and single-host demonstrations.  It implements the
identical :class:`~app.ports.stores.GraphStore` contract as the Neo4j adapter,
so the pipeline, analytics and API layers are completely unaffected by which
one is active.

Why it exists
-------------
The PRD mandates Neo4j for production (traversals and graph maths at scale),
but a hard dependency on a running Neo4j container would make the system
impossible to test, to demo, or to run on a district workstation.  Because the
contract is narrow and every traversal in CrimeLink is bounded (depth ≤ 2 for
expansion, depth ≤ 4 for temporal paths), the same semantics can be satisfied
in-process for the graph sizes a single case realistically reaches.

Determinism note
----------------
Ties are broken by sorting on ``provenance_key`` wherever a choice between
candidate nodes is required (e.g. hard-identifier matching).  That removes
insertion-order dependence, which is what makes the "reprocess 3×" idempotency
test meaningful.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import networkx as nx

from app.config import Settings, get_settings
from app.domain.enums import AGGREGATING_REL_TYPES, UNEVIDENCED_META_REL_TYPES
from app.domain.models import GraphEdge, GraphNode, MergeResult
from app.logging import get_logger
from app.ports.stores import GraphPayload

log = get_logger("crimelink.graph.embedded")

_LABEL = "_label"
_REL = "_rel"
_KEY = "_key"
_LIST_UNION_KEYS = (
    "source_doc_ids",
    "candidate_keys",
    "aliases",
    "case_ids",
    "ipc_sections",
    "risk_flags",
    "evidence_spans",
    "source_types",
)


def _json_safe(value: Any) -> Any:
    """Coerce a value into something the JSON snapshot can round-trip."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return str(value)


def _union(existing: Any, incoming: Any) -> Any:
    """Union two list-valued properties, preserving order and uniqueness."""
    left = existing if isinstance(existing, list) else ([] if existing is None else [existing])
    right = incoming if isinstance(incoming, list) else ([] if incoming is None else [incoming])
    out: list[Any] = []
    seen: set[str] = set()
    for item in [*left, *right]:
        marker = json.dumps(item, sort_keys=True, default=str)
        if marker not in seen:
            seen.add(marker)
            out.append(item)
    return out


class EmbeddedGraphStore:
    """In-process graph with write-through JSON persistence."""

    backend_name = "embedded"

    def __init__(self, settings: Settings | None = None, persist: bool = True) -> None:
        self.settings = settings or get_settings()
        self.persist = persist
        self._lock = threading.RLock()
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self._version = 0
        if self.persist:
            self.settings.ensure_directories()
            self._load()

    # ------------------------------------------------------------------ io
    @property
    def snapshot_path(self) -> Path:
        return self.settings.graph_snapshot_path

    def _load(self) -> None:
        path = self.snapshot_path
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - corrupt snapshot
            log.error("graph.snapshot_unreadable", path=str(path), error=str(exc))
            return
        graph = nx.MultiDiGraph()
        for node in raw.get("nodes", []):
            graph.add_node(node["pk"], **node["data"])
        for edge in raw.get("edges", []):
            graph.add_edge(edge["source"], edge["target"], key=edge["key"], **edge["data"])
        self._graph = graph
        log.info("graph.snapshot_loaded", nodes=graph.number_of_nodes(), edges=graph.number_of_edges())

    def _flush(self) -> None:
        if not self.persist:
            return
        payload = {
            "nodes": [
                {"pk": pk, "data": _json_safe(data)} for pk, data in self._graph.nodes(data=True)
            ],
            "edges": [
                {"source": u, "target": v, "key": k, "data": _json_safe(data)}
                for u, v, k, data in self._graph.edges(keys=True, data=True)
            ],
        }
        tmp = self.snapshot_path.with_suffix(self.snapshot_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.snapshot_path)

    def version(self) -> int:
        return self._version

    # --------------------------------------------------------------- writes
    def upsert_nodes(self, nodes: Iterable[GraphNode]) -> int:
        count = 0
        with self._lock:
            for node in nodes:
                data = _json_safe(node.properties)
                data.setdefault("confidence", 1.0)
                data.setdefault("is_active", True)
                data.setdefault("created_at", datetime.utcnow().isoformat())
                data["case_ids"] = _union(data.get("case_ids"), node.properties.get("case_id"))
                data.setdefault("case_ids", [])
                data["source_types"] = _union(data.get("source_types"), node.properties.get("source_type"))
                if self._graph.has_node(node.provenance_key):
                    existing = self._graph.nodes[node.provenance_key]
                    for key, value in data.items():
                        if key in _LIST_UNION_KEYS:
                            existing[key] = _union(existing.get(key), value)
                        elif key == "confidence":
                            try:
                                existing[key] = max(float(existing.get(key, 0.0)), float(value))
                            except (TypeError, ValueError):
                                existing[key] = value
                        elif key in ("created_at", "first_seen"):
                            existing.setdefault(key, value)
                        elif key == "last_seen_doc":
                            existing[key] = value
                        elif key == "is_active":
                            # A merge decision (is_active=false) must never be
                            # silently undone by re-ingesting the same document.
                            existing[key] = bool(existing.get(key, True)) and bool(value)
                        else:
                            existing[key] = value
                    existing[_LABEL] = node.label
                else:
                    data[_LABEL] = node.label
                    self._graph.add_node(node.provenance_key, **data)
                count += 1
            self._version += 1
            self._flush()
        return count

    def upsert_edges(self, edges: Iterable[GraphEdge]) -> int:
        count = 0
        with self._lock:
            for edge in edges:
                if not self._graph.has_node(edge.source_key):
                    continue
                if not self._graph.has_node(edge.target_key):
                    continue
                data = _json_safe(edge.properties)
                data[_REL] = edge.rel_type
                data[_KEY] = edge.key
                docs = data.pop("source_doc_ids", None) or []
                data["source_doc_ids"] = _union(data.get("source_doc_ids"), docs)
                data.setdefault("source_doc_ids", [])
                if data.get("source_doc_id"):
                    data["source_doc_ids"] = _union(
                        data.get("source_doc_ids"), data["source_doc_id"]
                    )
                key = edge.key
                existing = (
                    self._graph.edges.get((edge.source_key, edge.target_key, key))
                    if self._graph.has_edge(edge.source_key, edge.target_key, key)
                    else None
                )
                if existing is None:
                    self._graph.add_edge(
                        edge.source_key, edge.target_key, key=key, **data
                    )
                else:
                    updated = dict(existing)
                    for prop, value in data.items():
                        if prop in _LIST_UNION_KEYS:
                            updated[prop] = _union(updated.get(prop), value)
                        elif (
                            edge.rel_type in AGGREGATING_REL_TYPES
                            and prop in ("call_count", "count")
                        ):
                            updated[prop] = _as_int(updated.get(prop, 0)) + _as_int(value)
                        elif (
                            edge.rel_type in AGGREGATING_REL_TYPES
                            and prop in ("first_ts",)
                        ):
                            updated[prop] = _min_ts(updated.get(prop), value)
                        elif edge.rel_type in AGGREGATING_REL_TYPES and prop == "last_ts":
                            updated[prop] = _max_ts(updated.get(prop), value)
                        elif (
                            edge.rel_type in AGGREGATING_REL_TYPES
                            and prop == "duration_s"
                        ):
                            updated[prop] = _as_int(updated.get(prop, 0)) + _as_int(value)
                        elif prop == "confidence":
                            try:
                                updated[prop] = max(float(updated.get(prop, 0.0)), float(value))
                            except (TypeError, ValueError):
                                updated[prop] = value
                        else:
                            updated[prop] = value
                    updated[_REL] = edge.rel_type
                    updated[_KEY] = key
                    self._graph.remove_edge(edge.source_key, edge.target_key, key=key)
                    self._graph.add_edge(
                        edge.source_key, edge.target_key, key=key, **updated
                    )
                count += 1
            self._version += 1
            self._flush()
        return count

    def ensure_case_node(self, case_id: str, case_number: str, jurisdiction_id: str) -> None:
        node = GraphNode(
            provenance_key=f"case:{case_id}",
            label="Case",
            properties={
                "case_id": case_id,
                "case_number": case_number,
                "jurisdiction_id": jurisdiction_id,
                "name": case_number,
                "confidence": 1.0,
                "is_active": True,
            },
        )
        self.upsert_nodes([node])

    # ---------------------------------------------------------------- reads
    def _to_graph_node(self, pk: str, data: dict[str, Any]) -> GraphNode:
        props = {k: v for k, v in data.items() if not k.startswith("_")}
        return GraphNode(provenance_key=pk, label=data.get(_LABEL, "Person"), properties=props)

    def get_node(self, provenance_key: str) -> GraphNode | None:
        with self._lock:
            if not self._graph.has_node(provenance_key):
                return None
            return self._to_graph_node(provenance_key, self._graph.nodes[provenance_key])

    def get_nodes(self, provenance_keys: list[str]) -> dict[str, GraphNode]:
        with self._lock:
            return {
                pk: self._to_graph_node(pk, self._graph.nodes[pk])
                for pk in provenance_keys
                if self._graph.has_node(pk)
            }

    def expand(
        self,
        root_key: str,
        rel_types: list[str] | None = None,
        depth: int = 1,
        limit: int = 300,
    ) -> GraphPayload:
        depth = max(1, min(int(depth), self.settings.graph_max_expand_depth))
        payload = GraphPayload()
        with self._lock:
            if not self._graph.has_node(root_key):
                return payload
            frontier = {root_key}
            seen_nodes: set[str] = set()
            seen_edges: set[tuple[str, str, str]] = set()
            for _ in range(depth):
                next_frontier: set[str] = set()
                for node_key in sorted(frontier):
                    if node_key in seen_nodes:
                        continue
                    seen_nodes.add(node_key)
                    if len(seen_nodes) > limit:
                        payload.truncated = True
                        break
                    for _u, v, k, data in self._graph.out_edges(node_key, keys=True, data=True):
                        if rel_types and data.get(_REL) not in rel_types:
                            continue
                        seen_edges.add((node_key, v, k))
                        if v not in seen_nodes:
                            next_frontier.add(v)
                    for u, _v, k, data in self._graph.in_edges(node_key, keys=True, data=True):
                        if rel_types and data.get(_REL) not in rel_types:
                            continue
                        seen_edges.add((u, node_key, k))
                        if u not in seen_nodes:
                            next_frontier.add(u)
                frontier = next_frontier - seen_nodes
                if not frontier or payload.truncated:
                    break
            for pk in sorted(seen_nodes):
                payload.nodes.append(self._cytoscape_node(pk, self._graph.nodes[pk]))
            for (u, v, k) in sorted(seen_edges):
                data = self._graph.edges[u, v, k]
                payload.edges.append(self._cytoscape_edge(u, v, k, data))
        return payload

    def _cytoscape_node(self, pk: str, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "data": {
                "id": pk,
                "label": data.get(_LABEL, "Person"),
                "name": (
                    data.get("name")
                    or data.get("number")
                    or data.get("plate")
                    or data.get("address")
                    or data.get("description")
                    or data.get("case_number")
                    or pk[:8]
                ),
                "confidence": float(data.get("confidence", 1.0) or 1.0),
                "case_ids": list(data.get("case_ids") or []),
                "source_doc_ids": list(data.get("source_doc_ids") or []),
                "is_active": bool(data.get("is_active", True)),
                "risk_flags": list(data.get("risk_flags") or []),
                "aliases": list(data.get("aliases") or []),
                **{
                    k: v
                    for k, v in data.items()
                    if not k.startswith("_")
                    and k
                    not in {
                        "name",
                        "confidence",
                        "case_ids",
                        "source_doc_ids",
                        "is_active",
                        "risk_flags",
                        "aliases",
                    }
                },
            }
        }

    def _cytoscape_edge(self, u: str, v: str, k: str, data: dict[str, Any]) -> dict[str, Any]:
        rel = data.get(_REL, "RELATED")
        confidence = float(data.get("confidence", 1.0) or 1.0)
        low = rel in ("LINKED_ON_SOCIAL",) or confidence < 0.6
        return {
            "data": {
                "id": k,
                "source": u,
                "target": v,
                "type": rel,
                "label": _edge_label(rel, data),
                "confidence": confidence,
                "source_doc_id": data.get("source_doc_id"),
                "source_doc_ids": list(data.get("source_doc_ids") or []),
                "call_count": data.get("call_count"),
                "amount": data.get("amount"),
                "ts": data.get("ts"),
                "low_confidence": low,
                **{
                    key: value
                    for key, value in data.items()
                    if not key.startswith("_")
                    and key
                    not in {
                        "confidence",
                        "source_doc_id",
                        "source_doc_ids",
                        "call_count",
                        "amount",
                        "ts",
                    }
                },
            },
            "classes": "low-confidence" if low else "evidenced",
        }

    def search(
        self,
        query: str,
        labels: list[str] | None = None,
        case_id: str | None = None,
        limit: int = 50,
    ) -> list[GraphNode]:
        needle = (query or "").strip().lower()
        if not needle:
            return []
        results: list[GraphNode] = []
        with self._lock:
            for pk, data in self._graph.nodes(data=True):
                label = data.get(_LABEL)
                if labels and label not in labels:
                    continue
                if case_id and case_id not in (data.get("case_ids") or []):
                    continue
                haystacks = [
                    str(data.get("name", "")),
                    str(data.get("number", "")),
                    str(data.get("plate", "")),
                    str(data.get("address", "")),
                    str(data.get("description", "")),
                    " ".join(str(a) for a in (data.get("aliases") or [])),
                ]
                if any(needle in h.lower() for h in haystacks if h):
                    results.append(self._to_graph_node(pk, data))
        results.sort(key=lambda n: (-float(n.properties.get("confidence", 0)), n.name))
        return results[:limit]

    def snapshot(
        self, case_id: str, include_inactive: bool = False, include_staging: bool = False
    ) -> Any:
        from app.domain.models import CaseGraphSnapshot

        with self._lock:
            nodes: dict[str, GraphNode] = {}
            for pk, data in self._graph.nodes(data=True):
                if data.get(_LABEL) == "Case":
                    continue
                if case_id not in (data.get("case_ids") or []):
                    continue
                if not include_inactive and not data.get("is_active", True):
                    continue
                # Anonymous-tip content lives in a case-scoped staging subgraph
                # until an investigator promotes it (PRD 7 / DPDP legal gate).
                if data.get("staging") and not include_staging:
                    continue
                nodes[pk] = self._to_graph_node(pk, data)
            edges: list[GraphEdge] = []
            for u, v, k, data in self._graph.edges(keys=True, data=True):
                if u not in nodes or v not in nodes:
                    continue
                props = {key: val for key, val in data.items() if not key.startswith("_")}
                if not props.get("source_doc_id") and data.get(_REL) not in UNEVIDENCED_META_REL_TYPES:
                    continue
                edges.append(
                    GraphEdge(
                        source_key=u,
                        target_key=v,
                        rel_type=data.get(_REL, "RELATED"),
                        properties=props,
                        key=k,
                    )
                )
            return CaseGraphSnapshot(case_id=case_id, nodes=nodes, edges=edges)

    def timeline(
        self,
        case_id: str,
        from_ts: str | None = None,
        to_ts: str | None = None,
        participant: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        from app.analytics.timeline import build_timeline

        return build_timeline(
            self.snapshot(case_id, include_inactive=True),
            from_ts=from_ts,
            to_ts=to_ts,
            participant=participant,
            limit=limit,
        )

    def stats(self) -> dict[str, Any]:
        with self._lock:
            labels: dict[str, int] = {}
            rels: dict[str, int] = {}
            for _pk, data in self._graph.nodes(data=True):
                labels[data.get(_LABEL, "?")] = labels.get(data.get(_LABEL, "?"), 0) + 1
            for _u, _v, _k, data in self._graph.edges(keys=True, data=True):
                rels[data.get(_REL, "?")] = rels.get(data.get(_REL, "?"), 0) + 1
            return {
                "backend": self.backend_name,
                "nodes": self._graph.number_of_nodes(),
                "edges": self._graph.number_of_edges(),
                "labels": labels,
                "relationships": rels,
                "version": self._version,
            }

    def get_case_snapshot(self, case_id: str):
        return self.snapshot(case_id)

    def reset(self) -> None:
        """Wipe the in-memory graph and persisted snapshot (DEV/TEST only)."""
        with self._lock:
            self._graph = nx.MultiDiGraph()
            self._version = 0
            if self.persist and self.snapshot_path.exists():
                try:
                    self.snapshot_path.unlink()
                except OSError:
                    pass

    def list_nodes(self, label: str | None = None, limit: int = 100, offset: int = 0) -> dict:
        """Paginated node listing for the admin DB-inspection UI."""
        with self._lock:
            rows = []
            total = 0
            for pk, data in self._graph.nodes(data=True):
                lbl = data.get(_LABEL, "?")
                if label and lbl != label:
                    continue
                total += 1
                rows.append((pk, data, lbl))
            rows.sort(key=lambda r: r[0])
            page = rows[offset : offset + limit]
            items = []
            for pk, data, lbl in page:
                items.append({
                    "id": pk,
                    "label": lbl,
                    "name": (
                        data.get("name") or data.get("number") or data.get("plate")
                        or data.get("address") or data.get("case_number") or pk[:8]
                    ),
                    "confidence": float(data.get("confidence", 1.0) or 1.0),
                    "case_count": len(data.get("case_ids") or []),
                    "source_doc_count": len(data.get("source_doc_ids") or []),
                    "is_active": bool(data.get("is_active", True)),
                })
            return {"items": items, "total": total, "limit": limit, "offset": offset}

    def list_edges(self, rel_type: str | None = None, limit: int = 100, offset: int = 0) -> dict:
        with self._lock:
            rows = []
            total = 0
            for u, v, k, data in self._graph.edges(keys=True, data=True):
                rt = data.get(_REL, "?")
                if rel_type and rt != rel_type:
                    continue
                total += 1
                rows.append((u, v, k, data, rt))
            rows.sort(key=lambda r: (r[0], r[1], r[2]))
            page = rows[offset : offset + limit]
            items = []
            for u, v, k, data, rt in page:
                items.append({
                    "key": k,
                    "source": u,
                    "target": v,
                    "rel_type": rt,
                    "confidence": float(data.get("confidence", 1.0) or 1.0),
                    "source_doc_id": data.get("source_doc_id"),
                    "source_doc_count": len(data.get("source_doc_ids") or []),
                })
            return {"items": items, "total": total, "limit": limit, "offset": offset}

    # ----------------------------------------------------- entity resolution
    def find_by_hard_identifier(
        self, entity_type: str, normalized_value: str, case_id: str | None = None
    ) -> str | None:
        keys = ("number", "plate", "ifsc", "account_number", "address", "name", "value")
        with self._lock:
            matches: list[str] = []
            for pk, data in self._graph.nodes(data=True):
                if data.get(_LABEL) != entity_type:
                    continue
                if any(str(data.get(k, "")) == normalized_value for k in keys):
                    if case_id and case_id not in (data.get("case_ids") or []):
                        # Hard identifiers are globally unique in CrimeLink, but
                        # respect case scoping when one is supplied.
                        continue
                    matches.append(pk)
            return sorted(matches)[0] if matches else None

    def candidate_persons(self, case_id: str, exclude_key: str | None = None) -> list[GraphNode]:
        with self._lock:
            out: list[GraphNode] = []
            for pk, data in self._graph.nodes(data=True):
                if data.get(_LABEL) != "Person":
                    continue
                if case_id not in (data.get("case_ids") or []):
                    continue
                if not data.get("is_active", True):
                    continue
                if exclude_key and pk == exclude_key:
                    continue
                out.append(self._to_graph_node(pk, data))
            return out

    def add_potential_alias(
        self, source_key: str, target_key: str, queue_id: str, similarity: float
    ) -> None:
        # A review artifact is still evidence-backed: it points at the documents
        # that mention each side, so an investigator can open them from the edge.
        doc_id, doc_ids = self._meta_evidence(source_key, target_key)
        edge = GraphEdge(
            source_key=source_key,
            target_key=target_key,
            rel_type="POTENTIAL_ALIAS",
            properties={
                "er_queue_id": queue_id,
                "similarity": similarity,
                "status": "PENDING",
                "source_doc_id": doc_id,
                "source_doc_ids": doc_ids,
            },
        )
        self.upsert_edges([edge])

    def _meta_evidence(self, *keys: str) -> tuple[str | None, list[str]]:
        """Union the evidence of the given nodes for a meta relationship."""
        docs: list[str] = []
        for key in keys:
            node = self.get_node(key)
            if node is None:
                continue
            for doc in node.properties.get("source_doc_ids") or []:
                if doc not in docs:
                    docs.append(doc)
        return (docs[0] if docs else None), docs

    def tombstone_reject(self, source_key: str, target_key: str, resolved_by: str) -> None:
        from app.db.base import utcnow


        doc_id, doc_ids = self._meta_evidence(source_key, target_key)
        edge = GraphEdge(
            source_key=source_key,
            target_key=target_key,
            rel_type="SIMILARITY_REJECTED",
            properties={
                "resolved_by": resolved_by,
                "at": utcnow().isoformat(),
                "source_doc_id": doc_id,
                "source_doc_ids": doc_ids,
            },
        )
        self.upsert_edges([edge])

    def has_tombstone(self, source_key: str, target_key: str) -> bool:
        with self._lock:
            for _u, _v, _k, data in self._graph.out_edges(source_key, keys=True, data=True):
                if data.get(_REL) == "SIMILARITY_REJECTED" and _v == target_key:
                    return True
            for _u, _v, _k, data in self._graph.out_edges(target_key, keys=True, data=True):
                if data.get(_REL) == "SIMILARITY_REJECTED" and _v == source_key:
                    return True
            return False

    def merge_persons(
        self, keep_key: str, absorb_key: str, actor_id: str, queue_id: str | None = None
    ) -> MergeResult:
        """Reversible merge: every edge of *absorb* is re-pointed at *keep*.

        The pre-merge edge list is stored on the deactivated node, so a wrongful
        merge — the worst failure mode in this system — can be undone exactly.
        """
        from app.db.base import utcnow

        with self._lock:
            if not self._graph.has_node(keep_key) or not self._graph.has_node(absorb_key):
                raise KeyError("merge endpoints must exist")
            pre_merge: list[dict[str, Any]] = []
            for u, v, k, data in list(self._graph.out_edges(absorb_key, keys=True, data=True)):
                pre_merge.append(
                    {
                        "direction": "out",
                        "other": v,
                        "key": k,
                        "rel_type": data.get(_REL),
                        "props": {p: q for p, q in data.items() if not p.startswith("_")},
                    }
                )
            for u, v, k, data in list(self._graph.in_edges(absorb_key, keys=True, data=True)):
                if u == keep_key:
                    continue
                pre_merge.append(
                    {
                        "direction": "in",
                        "other": u,
                        "key": k,
                        "rel_type": data.get(_REL),
                        "props": {p: q for p, q in data.items() if not p.startswith("_")},
                    }
                )

            post_keys: list[str] = []
            for record in pre_merge:
                rel_type = record["rel_type"]
                props = dict(record["props"])
                props["merged_from"] = absorb_key
                if record["direction"] == "out":
                    src, dst = keep_key, record["other"]
                else:
                    src, dst = record["other"], keep_key
                if src == dst:
                    continue
                edge = GraphEdge(
                    source_key=src,
                    target_key=dst,
                    rel_type=rel_type,
                    properties=props,
                    key=record["key"],
                )
                if rel_type not in UNEVIDENCED_META_REL_TYPES and not props.get("source_doc_id"):
                    continue
                self._graph.add_edge(src, dst, key=edge.key, **{**props, _REL: rel_type, _KEY: edge.key})
                post_keys.append(edge.key)

            for record in pre_merge:
                if record["direction"] == "out":
                    self._graph.remove_edge(absorb_key, record["other"], key=record["key"])
                else:
                    self._graph.remove_edge(record["other"], absorb_key, key=record["key"])

            absorbed = self._graph.nodes[absorb_key]
            kept = self._graph.nodes[keep_key]
            kept["aliases"] = _union(kept.get("aliases"), [absorbed.get("name")])
            kept["aliases"] = _union(kept.get("aliases"), absorbed.get("aliases"))
            kept["candidate_keys"] = _union(kept.get("candidate_keys"), absorbed.get("candidate_keys"))
            kept["candidate_keys"] = _union(kept.get("candidate_keys"), absorb_key)
            kept["case_ids"] = _union(kept.get("case_ids"), absorbed.get("case_ids"))
            kept["source_doc_ids"] = _union(kept.get("source_doc_ids"), absorbed.get("source_doc_ids"))
            absorbed["is_active"] = False
            absorbed["merged_into"] = keep_key
            absorbed["merged_at"] = utcnow().isoformat()
            absorbed["merged_by"] = actor_id
            absorbed["pre_merge_edges"] = _json_safe(pre_merge)
            absorbed["post_merge_edge_keys"] = post_keys

            self._graph.add_edge(
                absorb_key,
                keep_key,
                key=f"merged-into:{absorb_key}:{keep_key}",
                **{
                    _REL: "MERGED_INTO",
                    _KEY: f"merged-into:{absorb_key}:{keep_key}",
                    "resolved_by": actor_id,
                    "queue_id": queue_id,
                    "at": utcnow().isoformat(),
                    "reversible": True,
                },
            )
            self._version += 1
            self._flush()
            return MergeResult(
                kept_key=keep_key,
                absorbed_key=absorb_key,
                rerouted_edges=len(post_keys),
                merged_into_marker=f"merged-into:{absorb_key}:{keep_key}",
            )

    def unmerge_persons(self, kept_key: str, absorbed_key: str, actor_id: str) -> MergeResult:
        with self._lock:
            absorbed = self._graph.nodes.get(absorbed_key)
            if absorbed is None or not absorbed.get("pre_merge_edges"):
                raise KeyError("no reversible merge record for this pair")
            for key in list(absorbed.get("post_merge_edge_keys") or []):
                for u, v, k in list(self._graph.edges(keys=True)):
                    if k == key:
                        self._graph.remove_edge(u, v, key=k)
            for record in absorbed.get("pre_merge_edges") or []:
                props = dict(record["props"])
                rel_type = record["rel_type"]
                if record["direction"] == "out":
                    src, dst = absorbed_key, record["other"]
                else:
                    src, dst = record["other"], absorbed_key
                if not self._graph.has_node(src) or not self._graph.has_node(dst):
                    continue
                self._graph.add_edge(
                    src, dst, key=record["key"], **{**props, _REL: rel_type, _KEY: record["key"]}
                )
            absorbed["is_active"] = True
            absorbed.pop("merged_into", None)
            absorbed.pop("merged_at", None)
            absorbed["unmerged_by"] = actor_id
            absorbed.pop("pre_merge_edges", None)
            absorbed.pop("post_merge_edge_keys", None)
            marker = f"merged-into:{absorbed_key}:{kept_key}"
            if self._graph.has_edge(absorbed_key, kept_key, key=marker):
                self._graph.remove_edge(absorbed_key, kept_key, key=marker)
            self._version += 1
            self._flush()
            return MergeResult(
                kept_key=kept_key,
                absorbed_key=absorbed_key,
                rerouted_edges=0,
                merged_into_marker=marker,
            )

    def invalidate_cache(self, case_id: str) -> None:
        """Analytics caches are keyed on ``version``; nothing to invalidate here."""
        return None


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _min_ts(a: Any, b: Any) -> Any:
    if a is None:
        return b
    if b is None:
        return a
    return a if str(a) <= str(b) else b


def _max_ts(a: Any, b: Any) -> Any:
    if a is None:
        return b
    if b is None:
        return a
    return a if str(a) >= str(b) else b


def _edge_label(rel: str, data: dict[str, Any]) -> str:
    if rel == "CALLED":
        count = data.get("call_count")
        return f"called ({count}×)" if count else "called"
    if rel == "TRANSFER_TO":
        amount = data.get("amount")
        return f"transferred ₹{amount:,.0f}" if isinstance(amount, (int, float)) else "transferred"
    if rel == "POTENTIAL_ALIAS":
        return "possible alias"
    if rel == "SIMILARITY_REJECTED":
        return "match rejected"
    if rel == "MERGED_INTO":
        return "merged into"
    return rel.replace("_", " ").lower()
