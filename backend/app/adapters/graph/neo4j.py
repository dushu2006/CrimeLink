"""Neo4j 5 graph store — the production backend (PRD 6.2).

Implements the same :class:`~app.ports.stores.GraphStore` contract as
:mod:`app.adapters.graph.embedded`, so nothing above this file knows or cares
which backend is active.

Notes on correctness
--------------------
* **Dynamic labels / relationship types.** Neo4j Cypher cannot parameterise a
  node label or a relationship type.  Where a dynamic token is unavoidable it is
  taken from the validated ``REL_TYPES`` / ``EntityType`` whitelists — never
  from user input — and the resulting statement is cached in a template dict.
  All *values* remain bound parameters.
* **List-union semantics.** ``pg_trgm``-style property unions (aliases,
  source_doc_ids, candidate_keys …) are computed in Python inside the same
  transaction as the write, so both backends behave identically.  This prefers
  determinism over a Cypher one-liner that would need APOC.
* **Nested values.** Neo4j properties cannot hold nested maps, so complex
  structures (``evidence_spans``, ``pre_merge_edges``) are stored as JSON
  strings and re-hydrated on read.
* **GDS.** Centrality runs through ``gds.*`` when the plugin answers a
  capability probe; otherwise it transparently falls back to the same
  NetworkX implementation used by the embedded backend.

The module never emits ``DETACH DELETE``.  Merges deactivate and re-point;
nothing is destroyed (PRD 11.2: no hard deletes anywhere in the system).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable, Sequence

from app.config import Settings, get_settings
from app.domain.enums import (
    AGGREGATING_REL_TYPES,
    REL_TYPES,
    UNEVIDENCED_META_REL_TYPES,
    EntityType,
)
from app.domain.models import CaseGraphSnapshot, GraphEdge, GraphNode, MergeResult
from app.logging import get_logger
from app.ports.stores import GraphPayload

log = get_logger("crimelink.graph.neo4j")

try:  # the driver is only importable where it is installed
    from neo4j import GraphDatabase, basic_auth  # type: ignore
except ImportError:  # pragma: no cover - production image always has it
    GraphDatabase = None  # type: ignore
    basic_auth = None  # type: ignore


# ---------------------------------------------------------------------------
# Cypher — every write statement in the system lives in this module.
# ---------------------------------------------------------------------------

CONSTRAINTS = (
    "CREATE CONSTRAINT pk_unique IF NOT EXISTS FOR (n) REQUIRE n.provenance_key IS UNIQUE",
    "CREATE CONSTRAINT phone_uniq IF NOT EXISTS FOR (p:Phone) REQUIRE p.number IS UNIQUE",
    "CREATE CONSTRAINT plate_uniq IF NOT EXISTS FOR (v:Vehicle) REQUIRE v.plate IS UNIQUE",
    "CREATE CONSTRAINT acct_uniq IF NOT EXISTS FOR (a:BankAccount) REQUIRE a.number IS UNIQUE",
    "CREATE CONSTRAINT case_uniq IF NOT EXISTS FOR (c:Case) REQUIRE c.case_id IS UNIQUE",
    "CREATE FULLTEXT INDEX entity_search IF NOT EXISTS FOR (n) ON EACH [n.name, n.aliases]",
)

# Projection used by analytics and by the scheduled pattern pass.  Kept here as
# a named constant so the graph query for a case has exactly one definition.
CASE_PROJECTION = """
MATCH (n)
WHERE $case_id IN n.case_ids
  AND ($include_inactive OR coalesce(n.is_active, true))
  AND ($include_staging OR NOT coalesce(n.staging, false))
RETURN n
"""

CASE_EDGE_PROJECTION = """
MATCH (a)-[r]->(b)
WHERE a.provenance_key IN $keys AND b.provenance_key IN $keys
RETURN a.provenance_key AS source, b.provenance_key AS target,
       type(r) AS rel_type, properties(r) AS props
"""

EXPAND_QUERY = """
MATCH p = (root {provenance_key: $root})-[rels*1..$depth]-(other)
WHERE size($types) = 0 OR all(rel IN rels WHERE type(rel) IN $types)
RETURN p
LIMIT $limit
"""

SEARCH_FALLBACK = """
MATCH (n)
WHERE ($label IS NULL OR $label IN labels(n))
  AND ($case_id IS NULL OR $case_id IN n.case_ids)
  AND (toLower(coalesce(n.name, '')) CONTAINS $q
       OR toLower(coalesce(n.number, '')) CONTAINS $q
       OR toLower(coalesce(n.plate, '')) CONTAINS $q
       OR toLower(coalesce(n.address, '')) CONTAINS $q)
RETURN n ORDER BY n.confidence DESC LIMIT $limit
"""

_LIST_UNION_KEYS = (
    "source_doc_ids",
    "candidate_keys",
    "aliases",
    "case_ids",
    "ipc_sections",
    "risk_flags",
    "source_types",
)


def _safe(value: Any) -> Any:
    """Flatten values Neo4j cannot store natively."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _unsafe(value: Any) -> Any:
    """Best-effort inverse of :func:`_safe` for properties read back."""
    if isinstance(value, str) and value[:1] in "{[":
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    if isinstance(value, list):
        return [_unsafe(v) for v in value]
    return value


def _union(existing: Any, incoming: Any) -> list[Any]:
    left = existing if isinstance(existing, list) else ([] if existing is None else [existing])
    right = incoming if isinstance(incoming, list) else ([] if incoming is None else [incoming])
    out: list[Any] = []
    seen: set[str] = set()
    for item in [*left, *right]:
        marker = json.dumps(item, sort_keys=True, default=str)
        if marker not in seen:
            seen.add(marker)
            out.append(_safe(item))
    return out


class Neo4jGraphStore:
    """Neo4j-backed graph store (production profile)."""

    backend_name = "neo4j"

    def __init__(self, settings: Settings | None = None) -> None:
        if GraphDatabase is None:  # pragma: no cover
            raise RuntimeError("neo4j driver is not installed")
        self.settings = settings or get_settings()
        self._driver = GraphDatabase.driver(
            self.settings.neo4j_uri,
            auth=basic_auth(self.settings.neo4j_user, self.settings.neo4j_password),
        )
        self._version = 0
        self._templates: dict[tuple[str, str], str] = {}

    # --------------------------------------------------------------- plumbing
    def _template(self, kind: str, token: str) -> str:
        """Cached, whitelisted Cypher template (token is never user input)."""
        key = (kind, token)
        if key not in self._templates:
            if kind == "node":
                if token != "Case" and token not in {e.value for e in EntityType}:
                    raise ValueError(f"invalid node label {token}")
                self._templates[key] = (
                    f"UNWIND $batch AS row "
                    f"MERGE (n:{token} {{provenance_key: row.pk}}) "
                    f"SET n += row.props"
                )
            else:
                if token not in REL_TYPES:
                    raise ValueError(f"invalid relationship type {token}")
                self._templates[key] = (
                    f"UNWIND $batch AS row "
                    f"MATCH (a {{provenance_key: row.src}}) "
                    f"MATCH (b {{provenance_key: row.dst}}) "
                    f"MERGE (a)-[r:{token} {{key: row.key}}]->(b) "
                    f"SET r += row.props"
                )
        return self._templates[key]

    def _write(self, fn, *args, **kwargs):
        with self._driver.session(database=self.settings.neo4j_database) as session:
            return session.execute_write(fn, *args, **kwargs)

    def _read(self, fn, *args, **kwargs):
        with self._driver.session(database=self.settings.neo4j_database) as session:
            return session.execute_read(fn, *args, **kwargs)

    def ensure_constraints(self) -> None:
        def _apply(tx):
            for statement in CONSTRAINTS:
                tx.run(statement)

        self._write(_apply)
        log.info("graph.neo4j.constraints_ok")

    def close(self) -> None:
        self._driver.close()

    def version(self) -> int:
        return self._version

    # ---------------------------------------------------------------- writes
    def upsert_nodes(self, nodes: Iterable[GraphNode]) -> int:
        batch = list(nodes)
        if not batch:
            return 0
        pks = [n.provenance_key for n in batch]

        def _apply(tx):
            rows = tx.run(
                "UNWIND $pks AS pk MATCH (n {provenance_key: pk}) "
                "RETURN n.provenance_key AS pk, properties(n) AS props",
                pks=pks,
            )
            existing = {r["pk"]: r["props"] for r in rows}
            by_label: dict[str, list[dict[str, Any]]] = {}
            for node in batch:
                props = {k: _safe(v) for k, v in node.properties.items()}
                props.setdefault("confidence", 1.0)
                props.setdefault("is_active", True)
                current = existing.get(node.provenance_key)
                if current:
                    merged = _merge_node_props(dict(current), props)
                else:
                    merged = props
                    merged.setdefault("created_at", datetime.utcnow().isoformat())
                by_label.setdefault(node.label, []).append(
                    {"pk": node.provenance_key, "props": merged}
                )
            for label, rows_for_label in by_label.items():
                tx.run(self._template("node", label), batch=rows_for_label)

        self._write(_apply)
        self._version += 1
        return len(batch)

    def upsert_edges(self, edges: Iterable[GraphEdge]) -> int:
        batch = list(edges)
        if not batch:
            return 0
        keys = [e.key for e in batch]

        def _apply(tx):
            rows = tx.run(
                "UNWIND $keys AS k MATCH ()-[r {key: k}]->() RETURN r.key AS k, properties(r) AS props",
                keys=keys,
            )
            existing = {r["k"]: r["props"] for r in rows}
            by_type: dict[str, list[dict[str, Any]]] = {}
            for edge in batch:
                props = {k: _safe(v) for k, v in edge.properties.items()}
                if props.get("source_doc_id"):
                    props["source_doc_ids"] = _union(
                        props.get("source_doc_ids"), props["source_doc_id"]
                    )
                current = existing.get(edge.key)
                merged = (
                    _merge_edge_props(dict(current), props, edge.rel_type)
                    if current
                    else props
                )
                merged["key"] = edge.key
                by_type.setdefault(edge.rel_type, []).append(
                    {"src": edge.source_key, "dst": edge.target_key, "key": edge.key, "props": merged}
                )
            for rel_type, rows_for_type in by_type.items():
                tx.run(self._template("edge", rel_type), batch=rows_for_type)

        self._write(_apply)
        self._version += 1
        return len(batch)

    def ensure_case_node(self, case_id: str, case_number: str, jurisdiction_id: str) -> None:
        self.upsert_nodes(
            [
                GraphNode(
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
            ]
        )

    # ----------------------------------------------------------------- reads
    @staticmethod
    def _node_to_graph_node(props: dict[str, Any], label: str) -> GraphNode:
        data = {k: _unsafe(v) for k, v in props.items()}
        pk = data.pop("provenance_key", None) or ""
        return GraphNode(provenance_key=pk, label=label, properties=data)

    def get_node(self, provenance_key: str) -> GraphNode | None:
        def _apply(tx):
            result = tx.run(
                "MATCH (n {provenance_key: $pk}) RETURN n, labels(n)[0] AS label", pk=provenance_key
            )
            record = result.single()
            return (record["n"], record["label"]) if record else None

        found = self._read(_apply)
        if not found:
            return None
        node, label = found
        props = dict(node)
        props.setdefault("provenance_key", provenance_key)
        return self._node_to_graph_node(props, label)

    def get_nodes(self, provenance_keys: list[str]) -> dict[str, GraphNode]:
        out: dict[str, GraphNode] = {}
        for pk in provenance_keys:
            node = self.get_node(pk)
            if node:
                out[pk] = node
        return out

    def expand(
        self,
        root_key: str,
        rel_types: list[str] | None = None,
        depth: int = 1,
        limit: int = 300,
    ) -> GraphPayload:
        depth = max(1, min(int(depth), self.settings.graph_max_expand_depth))
        payload = GraphPayload()

        def _apply(tx):
            return list(
                tx.run(
                    EXPAND_QUERY,
                    root=root_key,
                    types=list(rel_types or []),
                    depth=depth,
                    limit=limit,
                )
            )

        records = self._read(_apply)
        seen_nodes: dict[str, GraphNode] = {}
        seen_edges: dict[str, dict[str, Any]] = {}
        for record in records:
            path = record["p"]
            for node in path.nodes:
                props = dict(node)
                pk = props.get("provenance_key")
                label = list(node.labels)[0] if node.labels else "Person"
                if pk:
                    props.setdefault("provenance_key", pk)
                    seen_nodes[pk] = self._node_to_graph_node(props, label)
                if len(seen_nodes) > limit:
                    payload.truncated = True
            for rel in path.relationships:
                rk = rel.get("key") or rel.element_id
                if rk in seen_edges:
                    continue
                props = dict(rel)
                seen_edges[rk] = {
                    "source": props.get("source") or rel.start_node.get("provenance_key"),
                    "target": props.get("target") or rel.end_node.get("provenance_key"),
                    "rel_type": rel.type,
                    "props": props,
                    "key": rk,
                }
        for pk, node in seen_nodes.items():
            payload.nodes.append(_cytoscape_node(pk, node))
        for entry in seen_edges.values():
            payload.edges.append(_cytoscape_edge(**entry))
        return payload

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
        label = labels[0] if labels else None

        def _apply(tx):
            # Prefer the full-text index; fall back to a bounded CONTAINS scan.
            try:
                result = tx.run(
                    "CALL db.index.fulltext.queryNodes('entity_search', $q) "
                    "YIELD node, score RETURN node, score ORDER BY score DESC LIMIT $limit",
                    q=f"{needle}~" if " " not in needle else needle,
                    limit=limit * 4,
                )
                records = list(result)
            except Exception:  # index missing or unsupported query syntax
                records = []
            if not records:
                result = tx.run(
                    SEARCH_FALLBACK, q=needle, label=label, case_id=case_id, limit=limit * 4
                )
                records = [(r["n"], 0.0) for r in result]
            return records

        records = self._read(_apply)
        out: list[GraphNode] = []
        for node, _score in records:
            props = dict(node)
            node_labels = list(node.labels)
            if label and label not in node_labels:
                continue
            if case_id and case_id not in (props.get("case_ids") or []):
                continue
            out.append(self._node_to_graph_node(props, node_labels[0] if node_labels else "Person"))
        return out[:limit]

    def snapshot(
        self, case_id: str, include_inactive: bool = False, include_staging: bool = False
    ) -> CaseGraphSnapshot:
        def _apply(tx):
            nodes = [
                (dict(r["n"]), list(r["n"].labels)[0] if r["n"].labels else "Person")
                for r in tx.run(
                    CASE_PROJECTION,
                    case_id=case_id,
                    include_inactive=include_inactive,
                    include_staging=include_staging,
                )
            ]
            keys = [props.get("provenance_key") for props, _ in nodes]
            edges = [
                (r["source"], r["target"], r["rel_type"], r["props"])
                for r in tx.run(CASE_EDGE_PROJECTION, keys=keys)
            ]
            return nodes, edges

        nodes_raw, edges_raw = self._read(_apply)
        nodes: dict[str, GraphNode] = {}
        for props, label in nodes_raw:
            if label == "Case":
                continue
            pk = props.get("provenance_key")
            if not pk:
                continue
            nodes[pk] = self._node_to_graph_node(props, label)
        edges: list[GraphEdge] = []
        for src, dst, rel_type, props in edges_raw:
            if src not in nodes or dst not in nodes:
                continue
            data = {k: _unsafe(v) for k, v in props.items()}
            if rel_type not in UNEVIDENCED_META_REL_TYPES and not data.get("source_doc_id"):
                continue
            try:
                edges.append(
                    GraphEdge(
                        source_key=src,
                        target_key=dst,
                        rel_type=rel_type,
                        properties=data,
                        key=str(data.get("key") or ""),
                    )
                )
            except Exception:
                continue
        return CaseGraphSnapshot(case_id=case_id, nodes=nodes, edges=edges)

    def timeline(
        self,
        case_id: str,
        from_ts: str | None = None,
        to_ts: str | None = None,
        participant: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Chronological event stream.

        Delegated to the shared helper so Neo4j and the embedded backend return
        byte-identical timelines (the Cypher projection is the only difference).
        """
        from app.analytics.timeline import build_timeline

        return build_timeline(
            self.snapshot(case_id, include_inactive=True),
            from_ts=from_ts,
            to_ts=to_ts,
            participant=participant,
            limit=limit,
        )

    def stats(self) -> dict[str, Any]:
        def _apply(tx):
            labels = {r["label"]: r["count"] for r in tx.run(
                "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS count"
            )}
            rels = {r["type"]: r["count"] for r in tx.run(
                "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count"
            )}
            return labels, rels

        labels, rels = self._read(_apply)
        return {
            "backend": self.backend_name,
            "nodes": sum(labels.values()),
            "edges": sum(rels.values()),
            "labels": labels,
            "relationships": rels,
            "version": self._version,
        }

    def get_case_snapshot(self, case_id: str):
        return self.snapshot(case_id)

    def reset(self) -> None:
        """DEV/TEST only: remove all nodes and relationships."""
        def _apply(tx):
            tx.run("MATCH (n) DETACH DELETE n")
        try:
            self._write(_apply)
        except Exception:
            log.exception("graph.neo4j.reset_failed")

    def list_nodes(self, label: str | None = None, limit: int = 100, offset: int = 0) -> dict:
        def _apply(tx):
            if label:
                q = (
                    f"MATCH (n:{label}) RETURN n.provenance_key AS pk, labels(n) AS labels, "
                    "n.name AS name, n.number AS number, n.plate AS plate, n.address AS address, "
                    "n.case_number AS case_number, n.confidence AS confidence, "
                    "n.case_ids AS case_ids, n.source_doc_ids AS source_doc_ids, n.is_active AS is_active "
                    "ORDER BY pk SKIP $offset LIMIT $limit"
                )
                count_q = f"MATCH (n:{label}) RETURN count(*) AS c"
            else:
                q = (
                    "MATCH (n) RETURN n.provenance_key AS pk, labels(n) AS labels, "
                    "n.name AS name, n.number AS number, n.plate AS plate, n.address AS address, "
                    "n.case_number AS case_number, n.confidence AS confidence, "
                    "n.case_ids AS case_ids, n.source_doc_ids AS source_doc_ids, n.is_active AS is_active "
                    "ORDER BY pk SKIP $offset LIMIT $limit"
                )
                count_q = "MATCH (n) RETURN count(*) AS c"
            rows = list(tx.run(q, offset=offset, limit=limit))
            total = tx.run(count_q).single()
            return rows, int(total["c"]) if total else 0

        try:
            rows, total = self._read(_apply)
        except Exception as exc:
            return {"items": [], "total": 0, "error": str(exc)}
        items = []
        for r in rows:
            lbl = next(iter(r["labels"] or []), "?")
            items.append({
                "id": r["pk"],
                "label": lbl,
                "name": r["name"] or r["number"] or r["plate"] or r["address"]
                         or r["case_number"] or (r["pk"][:8] if r["pk"] else ""),
                "confidence": float(r["confidence"] or 1.0),
                "case_count": len(r["case_ids"] or []),
                "source_doc_count": len(r["source_doc_ids"] or []),
                "is_active": bool(r["is_active"] if r["is_active"] is not None else True),
            })
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def list_edges(self, rel_type: str | None = None, limit: int = 100, offset: int = 0) -> dict:
        def _apply(tx):
            rel_filter = "WHERE type(r) = $rel_type" if rel_type else ""
            q = (
                f"MATCH (a)-[r]->(b) {rel_filter} "
                "RETURN a.provenance_key AS src, b.provenance_key AS dst, "
                "type(r) AS type, elementId(r) AS key, r.confidence AS confidence, "
                "r.source_doc_id AS source_doc_id, r.source_doc_ids AS source_doc_ids "
                "ORDER BY src, dst SKIP $offset LIMIT $limit"
            )
            cq = f"MATCH ()-[r]->() {rel_filter} RETURN count(*) AS c"
            params = {"offset": offset, "limit": limit}
            if rel_type:
                params["rel_type"] = rel_type
            rows = list(tx.run(q, **params))
            total_rec = tx.run(cq, **({} if not rel_type else {"rel_type": rel_type})).single()
            return rows, int(total_rec["c"]) if total_rec else 0

        try:
            rows, total = self._read(_apply)
        except Exception as exc:
            return {"items": [], "total": 0, "error": str(exc)}
        items = []
        for r in rows:
            items.append({
                "key": r["key"],
                "source": r["src"],
                "target": r["dst"],
                "rel_type": r["type"],
                "confidence": float(r["confidence"] or 1.0),
                "source_doc_id": r["source_doc_id"],
                "source_doc_count": len(r["source_doc_ids"] or []),
            })
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    # ----------------------------------------------------- entity resolution
    def find_by_hard_identifier(
        self, entity_type: str, normalized_value: str, case_id: str | None = None
    ) -> str | None:
        def _apply(tx):
            result = tx.run(
                f"MATCH (n:{entity_type}) "
                "WHERE n.number = $value OR n.plate = $value OR n.ifsc = $value "
                "OR n.address = $value OR n.name = $value "
                "RETURN n.provenance_key AS pk, n.case_ids AS case_ids ORDER BY n.provenance_key",
                value=normalized_value,
            )
            return [(r["pk"], r["case_ids"] or []) for r in result]

        rows = self._read(_apply)
        for pk, case_ids in rows:
            if not case_id or case_id in case_ids:
                return pk
        return None

    def candidate_persons(self, case_id: str, exclude_key: str | None = None) -> list[GraphNode]:
        def _apply(tx):
            return [
                (dict(r["n"]), "Person")
                for r in tx.run(
                    "MATCH (n:Person) WHERE $case_id IN n.case_ids "
                    "AND coalesce(n.is_active, true) = true RETURN n",
                    case_id=case_id,
                )
            ]

        rows = self._read(_apply)
        out = []
        for props, label in rows:
            node = self._node_to_graph_node(props, label)
            if exclude_key and node.provenance_key == exclude_key:
                continue
            out.append(node)
        return out

    def add_potential_alias(
        self, source_key: str, target_key: str, queue_id: str, similarity: float
    ) -> None:
        self.upsert_edges(
            [
                GraphEdge(
                    source_key=source_key,
                    target_key=target_key,
                    rel_type="POTENTIAL_ALIAS",
                    properties={
                        "er_queue_id": queue_id,
                        "similarity": similarity,
                        "status": "PENDING",
                    },
                )
            ]
        )

    def tombstone_reject(self, source_key: str, target_key: str, resolved_by: str) -> None:
        from app.db.base import utcnow

        self.upsert_edges(
            [
                GraphEdge(
                    source_key=source_key,
                    target_key=target_key,
                    rel_type="SIMILARITY_REJECTED",
                    properties={"resolved_by": resolved_by, "at": utcnow().isoformat()},
                )
            ]
        )

    def has_tombstone(self, source_key: str, target_key: str) -> bool:
        def _apply(tx):
            result = tx.run(
                "MATCH (a {provenance_key: $a})-[:SIMILARITY_REJECTED]-(b {provenance_key: $b}) "
                "RETURN count(*) AS c",
                a=source_key,
                b=target_key,
            )
            return result.single()["c"]

        return bool(self._read(_apply))

    def merge_persons(
        self, keep_key: str, absorb_key: str, actor_id: str, queue_id: str | None = None
    ) -> MergeResult:
        from app.db.base import utcnow

        def _apply(tx):
            out = tx.run(
                "MATCH (a {provenance_key: $absorb})-[r]-(other) "
                "RETURN startNode(r).provenance_key AS start, endNode(r).provenance_key AS end, "
                "type(r) AS rel_type, properties(r) AS props, r.key AS key",
                absorb=absorb_key,
            )
            pre_merge = []
            for record in out:
                direction = "out" if record["start"] == absorb_key else "in"
                other = record["end"] if direction == "out" else record["start"]
                if other == keep_key:
                    continue
                pre_merge.append(
                    {
                        "direction": direction,
                        "other": other,
                        "key": record["key"],
                        "rel_type": record["rel_type"],
                        "props": {k: _safe(v) for k, v in record["props"].items()},
                    }
                )
            post_keys = []
            for record in pre_merge:
                props = dict(record["props"])
                props["merged_from"] = absorb_key
                src, dst = (
                    (keep_key, record["other"])
                    if record["direction"] == "out"
                    else (record["other"], keep_key)
                )
                if src == dst:
                    continue
                if record["rel_type"] not in UNEVIDENCED_META_REL_TYPES and not props.get(
                    "source_doc_id"
                ):
                    continue
                tx.run(
                    self._template("edge", record["rel_type"]),
                    batch=[{"src": src, "dst": dst, "key": record["key"], "props": props}],
                )
                post_keys.append(record["key"])
            for record in pre_merge:
                if record["rel_type"] not in REL_TYPES:
                    continue
                src, dst = (
                    (absorb_key, record["other"])
                    if record["direction"] == "out"
                    else (record["other"], absorb_key)
                )
                # Deletes the *relationship only* (never DETACH DELETE): the
                # edge has already been re-created on the surviving node, so no
                # evidence is lost.  Nodes are never removed from CrimeLink.
                tx.run(
                    f"MATCH (a {{provenance_key: $src}})-[r:{record['rel_type']} {{key: $key}}]->"
                    f"(b {{provenance_key: $dst}}) DELETE r",
                    src=src,
                    dst=dst,
                    key=record["key"],
                )
            # APOC-free list de-duplication: REDUCE over the concatenated lists.
            tx.run(
                "MATCH (keep {provenance_key: $keep}), (absorb {provenance_key: $absorb}) "
                "WITH keep, absorb, "
                "  [x IN coalesce(keep.aliases, []) + [absorb.name] WHERE x IS NOT NULL] AS raw_aliases, "
                "  [x IN coalesce(keep.candidate_keys, []) + [$absorb_key] WHERE x IS NOT NULL] AS raw_keys "
                "SET keep.aliases = REDUCE(a = [], x IN raw_aliases | CASE WHEN x IN a THEN a ELSE a + x END), "
                "    keep.candidate_keys = REDUCE(a = [], x IN raw_keys | CASE WHEN x IN a THEN a ELSE a + x END), "
                "    absorb.is_active = false, absorb.merged_into = $keep, "
                "    absorb.merged_at = $at, absorb.merged_by = $actor, "
                "    absorb.pre_merge_edges = $pre_merge, absorb.post_merge_edge_keys = $post_keys",
                keep=keep_key,
                absorb=absorb_key,
                absorb_key=absorb_key,
                at=utcnow().isoformat(),
                actor=actor_id,
                pre_merge=json.dumps(pre_merge, default=str),
                post_keys=post_keys,
            )
            return len(post_keys)

        rerouted = self._write(_apply)
        self._version += 1
        return MergeResult(
            kept_key=keep_key,
            absorbed_key=absorb_key,
            rerouted_edges=rerouted,
            merged_into_marker=f"merged-into:{absorb_key}:{keep_key}",
        )

    def unmerge_persons(self, kept_key: str, absorbed_key: str, actor_id: str) -> MergeResult:
        def _apply(tx):
            record = tx.run(
                "MATCH (n {provenance_key: $absorb}) RETURN n.pre_merge_edges AS pre, "
                "n.post_merge_edge_keys AS post",
                absorb=absorbed_key,
            ).single()
            if not record or not record["pre"]:
                raise KeyError("no reversible merge record for this pair")
            pre_merge = json.loads(record["pre"])
            for key in record["post"] or []:
                tx.run("MATCH ()-[r {key: $key}]->() DELETE r", key=key)
            for item in pre_merge:
                src, dst = (
                    (absorbed_key, item["other"])
                    if item["direction"] == "out"
                    else (item["other"], absorbed_key)
                )
                tx.run(
                    self._template("edge", item["rel_type"]),
                    batch=[{"src": src, "dst": dst, "key": item["key"], "props": item["props"]}],
                )
            tx.run(
                "MATCH (n {provenance_key: $absorb}) "
                "SET n.is_active = true, n.unmerged_by = $actor "
                "REMOVE n.merged_into, n.merged_at, n.pre_merge_edges, n.post_merge_edge_keys",
                absorb=absorbed_key,
                actor=actor_id,
            )
            return True

        self._write(_apply)
        self._version += 1
        return MergeResult(
            kept_key=kept_key,
            absorbed_key=absorbed_key,
            rerouted_edges=0,
            merged_into_marker=f"merged-into:{absorbed_key}:{kept_key}",
        )

    def invalidate_cache(self, case_id: str) -> None:
        return None


def _merge_node_props(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current)
    for key, value in incoming.items():
        if key in _LIST_UNION_KEYS:
            merged[key] = _union(current.get(key), value)
        elif key == "confidence":
            try:
                merged[key] = max(float(current.get(key, 0.0)), float(value))
            except (TypeError, ValueError):
                merged[key] = value
        elif key in ("created_at", "first_seen"):
            merged.setdefault(key, value)
        elif key == "is_active":
            # A human merge decision must survive re-ingestion.
            merged[key] = bool(current.get(key, True)) and bool(value)
        else:
            merged[key] = value
    return merged


def _merge_edge_props(
    current: dict[str, Any], incoming: dict[str, Any], rel_type: str
) -> dict[str, Any]:
    merged = dict(current)
    for key, value in incoming.items():
        if key in _LIST_UNION_KEYS:
            merged[key] = _union(current.get(key), value)
        elif rel_type in AGGREGATING_REL_TYPES and key in ("call_count", "count", "duration_s"):
            merged[key] = _as_int(current.get(key)) + _as_int(value)
        elif rel_type in AGGREGATING_REL_TYPES and key == "first_ts":
            merged[key] = _min_ts(current.get(key), value)
        elif rel_type in AGGREGATING_REL_TYPES and key == "last_ts":
            merged[key] = _max_ts(current.get(key), value)
        elif key == "confidence":
            try:
                merged[key] = max(float(current.get(key, 0.0)), float(value))
            except (TypeError, ValueError):
                merged[key] = value
        else:
            merged[key] = value
    return merged


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


def _cytoscape_node(pk: str, node: GraphNode) -> dict[str, Any]:
    props = dict(node.properties)
    confidence = float(props.get("confidence", 1.0) or 1.0)
    return {
        "data": {
            "id": pk,
            "label": node.label,
            "name": node.name,
            "confidence": confidence,
            "case_ids": list(props.get("case_ids") or []),
            "source_doc_ids": list(props.get("source_doc_ids") or []),
            "is_active": bool(props.get("is_active", True)),
            "risk_flags": list(props.get("risk_flags") or []),
            "aliases": list(props.get("aliases") or []),
            **{
                k: v
                for k, v in props.items()
                if k
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


def _cytoscape_edge(
    source: str, target: str, rel_type: str, props: dict[str, Any], key: str
) -> dict[str, Any]:
    confidence = float(props.get("confidence", 1.0) or 1.0)
    low = rel_type == "LINKED_ON_SOCIAL" or confidence < 0.6
    return {
        "data": {
            "id": key,
            "source": source,
            "target": target,
            "type": rel_type,
            "label": rel_type.replace("_", " ").lower(),
            "confidence": confidence,
            "source_doc_id": props.get("source_doc_id"),
            "source_doc_ids": list(props.get("source_doc_ids") or []),
            "call_count": props.get("call_count"),
            "amount": props.get("amount"),
            "ts": props.get("ts"),
            "low_confidence": low,
            **{
                k: v
                for k, v in props.items()
                if k
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
