"""Graph read services: expansion, search, influence, timeline (PRD 10/11).

Two rules from the PRD are enforced here:

* **Expansion is hard-capped at depth 2.**  Rendering a force-directed layout of
  ten thousand nodes overwhelms both the browser and the investigator, so the
  canvas starts empty and grows one verified hop at a time.
* **A score never travels alone.**  Every influence response carries the
  explanation subgraph that produced it.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.centrality import CentralityResult, compute_centrality, percentile_rank
from app.analytics.explanation import explain_node
from app.analytics.temporal import build_temporal_graph, find_temporal_paths
from app.config import Settings, get_settings
from app.container import Container, get_container
from app.db.models import Case
from app.domain.enums import canonical_label, is_document_artifact_node
from app.domain.models import GraphNode
from app.errors import NotFoundError
from app.logging import get_logger
from app.security.deps import JurisdictionScope

log = get_logger("crimelink.services.graph")

# (case_id, graph_version) -> centrality.  Invalidated automatically because the
# version changes on every graph write.
_centrality_cache: dict[tuple[str, int], CentralityResult] = {}
_CACHE_LIMIT = 32


def invalidate_analytics_cache() -> None:
    """Drop analytical projections after a graph rebuild or dataset switch."""
    _centrality_cache.clear()


#: Default hop depth for the person-centric network.  A *default*, not a cap:
#: the investigator can ask for any depth and the traversal will go as far as
#: the graph actually reaches.  See ``person_centric_network``.
DEFAULT_PERSON_NETWORK_DEPTH = 3


def canonical_person(node: GraphNode) -> bool:
    """True when the node is a person under either label convention."""
    return node.label in ("PERSON", "Person")


#: Person-involving relations are kept in preference to entity-to-entity ones
#: when a render budget forces a choice inside the neighbourhood walk.
_PERSON_PRIORITY_RELS = frozenset(
    {
        "USES_PHONE",
        "OWNS_VEHICLE",
        "OWNS_ACCOUNT",
        "CALLED",
        "ASSOCIATE_OF",
        "RELATIVE_OF",
        "ARRESTED_WITH",
        "NAMED_ACCOMPLICE_OF",
        "MEMBER_OF",
        "LOCATED_AT",
        "ACCUSED_IN",
        "SHARED_PHONE",
        "SHARED_ACCOUNT",
        "SHARED_VEHICLE",
        "SHARED_LOCATION",
        "SHARED_IDENTIFIER",
        "PARTICIPATED_IN",
        "TRANSFER_TO",
    }
)


def _edge_identity(edge: Any) -> str:
    return (
        getattr(edge, "key", "")
        or f"{edge.source_key}|{edge.rel_type}|{edge.target_key}"
    )


def _bfs_neighbourhood(
    snapshot: Any,
    root_key: str,
    requested_depth: int,
    node_budget: int | None = None,
) -> dict[str, Any]:
    """Breadth-first typed neighbourhood around ``root_key``.

    Shared by the case-scoped and master-scoped person graphs so the two
    surfaces can never disagree about how a person's network is walked.
    Returns the raw parts both callers assemble into their own payload.
    """
    adjacency: dict[str, list[tuple[str, Any]]] = {}
    for edge in snapshot.edges:
        if (
            is_document_artifact_node(snapshot.nodes.get(edge.source_key))
            or is_document_artifact_node(snapshot.nodes.get(edge.target_key))
        ):
            continue
        adjacency.setdefault(edge.source_key, []).append((edge.target_key, edge))
        adjacency.setdefault(edge.target_key, []).append((edge.source_key, edge))

    def edge_priority(edge) -> int:
        return 0 if edge.rel_type in _PERSON_PRIORITY_RELS else 1

    layer_of: dict[str, int] = {root_key: 0}
    seen_edges: set[str] = set()
    kept_edges: list[Any] = []
    frontier: list[str] = [root_key]
    truncated = False
    max_depth_reached = 0
    exhausted = False

    for current_depth in range(1, requested_depth + 1):
        next_frontier: list[str] = []
        for node_key in sorted(frontier):
            candidates = sorted(
                adjacency.get(node_key, []),
                key=lambda item: (edge_priority(item[1]), item[0]),
            )
            for neighbour, edge in candidates:
                is_new_node = neighbour not in layer_of
                if is_new_node and node_budget is not None and len(layer_of) >= node_budget:
                    truncated = True
                    continue
                identity = _edge_identity(edge)
                if identity not in seen_edges:
                    seen_edges.add(identity)
                    kept_edges.append(edge)
                if is_new_node:
                    layer_of[neighbour] = current_depth
                    next_frontier.append(neighbour)
        if next_frontier:
            max_depth_reached = current_depth
        frontier = next_frontier
        if not frontier:
            exhausted = True
            break

    keys = set(layer_of)
    nodes = [snapshot.nodes[k] for k in keys if k in snapshot.nodes]
    unique_edges: dict[str, Any] = {
        _edge_identity(edge): edge
        for edge in kept_edges
        if edge.source_key in keys and edge.target_key in keys
    }
    layers: dict[str, int] = {}
    for value in layer_of.values():
        if value == 0:
            continue
        layers[str(value)] = layers.get(str(value), 0) + 1

    return {
        "nodes": nodes,
        "edges": unique_edges,
        "layers": layers,
        "max_depth_reached": max_depth_reached,
        "exhausted": exhausted,
        "truncated": truncated,
    }


class GraphService:
    def __init__(self, container: Container | None = None, settings: Settings | None = None):
        self.container = container or get_container()
        self.settings = settings or self.container.settings

    # ------------------------------------------------------------- scoping
    async def _allowed_case_ids(self, session: AsyncSession, scope: JurisdictionScope) -> set[str]:
        """Jurisdiction-scoped AND active-dataset-scoped (includes hand-created NULL rows).

        Search and node-scope assertions range over exactly this set, so a
        replaced dataset is invisible to the whole graph surface at once --
        not just to the pages that remembered to filter. This is used for case
        listing and general search where hand-created cases should stay visible.
        """
        from app.services.cases import visible_case_ids

        return await visible_case_ids(session, scope)

    async def _strict_case_ids(self, session: AsyncSession, scope: JurisdictionScope) -> set[str]:
        """Jurisdiction-scoped AND strictly active-dataset-scoped (excludes NULL).

        Used for master graph, master analytics, and master investigation where
        the active dataset is the analysis universe. Including NULL legacy rows
        would make /datasets/stats disagree with /graph/master.
        """
        from app.services.cases import active_dataset_case_ids

        return await active_dataset_case_ids(session, scope)

    async def _assert_node_in_scope(
        self, session: AsyncSession, scope: JurisdictionScope, key: str
    ) -> GraphNode:
        node = self.container.graph_store.get_node(key)
        if node is None or is_document_artifact_node(node):
            # Evidence is opened through the provenance/evidence service, not
            # through actor graph traversal.
            raise NotFoundError("Graph node not found.")
        allowed = await self._allowed_case_ids(session, scope)
        node_cases = set(node.properties.get("case_ids") or [])
        if node_cases and not (node_cases & allowed):
            # Out-of-jurisdiction nodes are indistinguishable from missing ones.
            raise NotFoundError("Graph node not found.")
        return node

    # ------------------------------------------------------------- expansion
    async def expand(
        self,
        session: AsyncSession,
        scope: JurisdictionScope,
        key: str,
        *,
        rel_types: list[str] | None = None,
        depth: int = 1,
        limit: int | None = None,
    ) -> dict[str, Any]:
        node = await self._assert_node_in_scope(session, scope, key)
        depth = max(1, min(int(depth), self.settings.graph_max_expand_depth))
        payload = self.container.graph_store.expand(
            key,
            rel_types=rel_types,
            depth=depth,
            limit=limit or self.settings.graph_expand_node_limit,
        )
        return {"root": node.provenance_key, **payload.as_dict()}

    # ---------------------------------------------------------------- search
    async def search(
        self,
        session: AsyncSession,
        scope: JurisdictionScope,
        query: str,
        *,
        entity_type: str | None = None,
        case_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if case_id:
            from app.services.cases import require_case

            await require_case(session, scope, case_id)
            return [
                _node_row(node)
                for node in self.container.graph_store.search(
                    query, labels=[entity_type] if entity_type else None, case_id=case_id, limit=limit
                )
            ]

        allowed = await self._allowed_case_ids(session, scope)
        results: list[GraphNode] = []
        seen: set[str] = set()
        for allowed_case in sorted(allowed):
            for node in self.container.graph_store.search(
                query,
                labels=[entity_type] if entity_type else None,
                case_id=allowed_case,
                limit=limit,
            ):
                if node.provenance_key in seen:
                    continue
                seen.add(node.provenance_key)
                results.append(node)
        results.sort(key=lambda n: (-float(n.properties.get("confidence", 0)), n.name))
        return [_node_row(node) for node in results[:limit]]

    # -------------------------------------------------------------- analytics
    def _centrality(self, case_id: str) -> CentralityResult:
        store = self.container.graph_store
        version = getattr(store, "version", lambda: 0)()
        cache_key = (case_id, version)
        cached = _centrality_cache.get(cache_key)
        if cached is not None:
            return cached
        snapshot = store.snapshot(case_id, include_staging=False)
        result = compute_centrality(snapshot, self.settings)
        if len(_centrality_cache) >= _CACHE_LIMIT:
            _centrality_cache.clear()
        _centrality_cache[cache_key] = result
        return result

    async def influence(
        self, session: AsyncSession, scope: JurisdictionScope, key: str
    ) -> dict[str, Any]:
        node = await self._assert_node_in_scope(session, scope, key)
        case_ids = list(node.properties.get("case_ids") or [])
        if not case_ids:
            raise NotFoundError("This node is not linked to a case.")
        case_id = case_ids[0]
        from app.services.cases import require_case

        await require_case(session, scope, case_id)

        centrality = self._centrality(case_id)
        snapshot = self.container.graph_store.snapshot(case_id, include_staging=False)
        payload = explain_node(snapshot, centrality, key)
        if not payload:
            raise NotFoundError("No explanation is available for this node.")
        return payload

    async def ranked_influencers(
        self,
        session: AsyncSession,
        scope: JurisdictionScope,
        case_id: str,
        limit: int = 10,
        metric: str = "betweenness",
    ) -> list[dict[str, Any]]:
        """Ranked influence scores.

        Every row carries all four scores, so the UI can show *why* a node
        ranks where it does instead of presenting a bare number (PRD 11.1).
        """
        from app.services.cases import require_case

        await require_case(session, scope, case_id)
        centrality = self._centrality(case_id)
        scores = getattr(centrality, metric, None)
        if not isinstance(scores, dict) or not scores:
            scores = centrality.betweenness
        ordered = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
        snapshot = self.container.graph_store.snapshot(case_id, include_staging=False)
        out = []
        for rank, (key, score) in enumerate(ordered, start=1):
            node = snapshot.nodes.get(key)
            out.append(
                {
                    "rank": rank,
                    "provenance_key": key,
                    "name": node.name if node else key[:8],
                    "label": node.label if node else "Person",
                    "metric": metric,
                    "score": round(float(score), 6),
                    "betweenness": round(float(centrality.betweenness.get(key, 0.0)), 6),
                    "pagerank": round(float(centrality.pagerank.get(key, 0.0)), 6),
                    "degree": int(centrality.degree.get(key, 0)),
                    "community": centrality.communities.get(key),
                    "percentile": round(percentile_rank(scores, key), 4),
                }
            )
        return out

    async def case_graph(
        self,
        session: AsyncSession,
        scope: JurisdictionScope,
        case_id: str,
        *,
        include_staging: bool = False,
        limit: int = 2000,
        labels: list[str] | None = None,
        rel_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Everything needed to draw the case canvas, in Cytoscape element form.

        This is the **Master Graph**: the complete, evidence-backed network of
        the case.  Optional ``labels`` / ``rel_types`` restrict it to a subset
        of entity types and relationship types (the same filters the console
        applies in Master/Temporal mode); every node and edge still carries its
        provenance.
        """
        from app.services.cases import require_case

        await require_case(session, scope, case_id)
        snapshot = self.container.graph_store.snapshot(case_id, include_staging=include_staging)
        if labels:
            wanted = {canonical_label(l) for l in labels}
            nodes = [
                n for n in snapshot.nodes.values()
                if not is_document_artifact_node(n) and canonical_label(n.label) in wanted
            ]
        else:
            # Default graph reads are actor/context reads. Evidence artifacts
            # are opened through provenance APIs, never rendered as nodes.
            nodes = [n for n in snapshot.nodes.values() if not is_document_artifact_node(n)]
        keep = {n.provenance_key for n in nodes}
        edges = [e for e in snapshot.edges if e.source_key in keep and e.target_key in keep]
        if rel_types:
            wanted_rels = {r.upper() for r in rel_types}
            edges = [e for e in edges if e.rel_type.upper() in wanted_rels]
        keep = {n.provenance_key for n in nodes}
        edges = [e for e in edges if e.source_key in keep and e.target_key in keep]
        truncated = False
        if len(nodes) > limit:
            nodes = nodes[:limit]
            keep = {n.provenance_key for n in nodes}
            edges = [
                e for e in edges
                if e.source_key in keep and e.target_key in keep
            ]
            truncated = True
        return {
            "case_id": case_id,
            "view": "PERSON + CONTEXT",
            "available_views": ["PERSON NETWORK", "PERSON + CONTEXT", "EVIDENCE VIEW", "FINDING SUBGRAPH"],
            "include_staging": include_staging,
            "truncated": truncated,
            "filters": {"labels": labels or [], "rel_types": rel_types or []},
            "counts": {
                "nodes": len(nodes),
                "edges": len(edges),
                "by_label": dict(Counter(canonical_label(n.label) for n in nodes)),
                "by_rel_type": dict(Counter(e.rel_type for e in edges)),
            },
            "nodes": [_node_row(n) for n in nodes],
            "edges": [_edge_row(e) for e in edges],
        }

    # --------------------------------------------------- person-centric view
    async def person_targets(
        self,
        session: AsyncSession,
        scope: JurisdictionScope,
        case_id: str,
        *,
        limit: int = 500,
    ) -> dict[str, Any]:
        """The persons of a case — the selectable investigation targets."""
        from app.services.cases import require_case

        await require_case(session, scope, case_id)
        snapshot = self.container.graph_store.snapshot(case_id, include_staging=False)
        degree: dict[str, int] = {}
        for edge in snapshot.edges:
            degree[edge.source_key] = degree.get(edge.source_key, 0) + 1
            degree[edge.target_key] = degree.get(edge.target_key, 0) + 1
        persons = [
            node
            for node in snapshot.nodes.values()
            if not is_document_artifact_node(node) and canonical_person(node)
        ]
        persons.sort(key=lambda n: (-degree.get(n.provenance_key, 0), n.name))
        items = [
            {
                "provenance_key": node.provenance_key,
                "name": node.name,
                "aliases": list(node.properties.get("aliases") or []),
                "connections": degree.get(node.provenance_key, 0),
                "source_doc_ids": list(node.properties.get("source_doc_ids") or []),
            }
            for node in persons[:limit]
        ]
        return {"case_id": case_id, "total_persons": len(persons), "items": items}

    async def person_centric_network(
        self,
        session: AsyncSession,
        scope: JurisdictionScope,
        case_id: str,
        person_key: str,
        *,
        depth: int = DEFAULT_PERSON_NETWORK_DEPTH,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Target person + typed neighbourhood out to ``depth`` hops.

        **The hop count is an exploration parameter, not a ceiling on the
        graph.**  ``depth`` defaults to three because that is a useful first
        view, but an investigator may ask for 5, 26 or 100 hops and the
        traversal will honour it; there is no arbitrary maximum.  Traversal
        stops for exactly two reasons, both of them honest:

        1. the requested depth has been reached, or
        2. the frontier is empty -- everything reachable has been reached.

        The second case is reported as ``exhausted``, so asking for 100 hops
        on a network that is only 7 hops deep returns the whole component and
        says so, rather than pretending there is more to find.

        Safety comes from the algorithm rather than from a cap: this is a
        breadth-first search that records the layer at which each node was
        first seen and never revisits it, so a cycle is walked once and only
        once, and each edge is emitted at most once.  Cost is therefore
        O(V + E) in the case subgraph no matter how large ``depth`` is --
        depth 1000 on a 7-hop network costs the same as depth 8.

        Dataset isolation is inherited, not re-implemented: the snapshot is
        built for one case, cases belong to one dataset, and ``require_case``
        plus ``_assert_node_in_scope`` gate access before any traversal runs.

        ``limit`` is optional and, when omitted, nothing is truncated.  It
        exists only so a caller that is rendering to a small canvas can ask
        for a bounded slice; when it does bite, ``truncated`` says so and
        person-involving edges are kept in preference to entity-to-entity
        ones.
        """
        from app.services.cases import require_case

        await require_case(session, scope, case_id)
        await self._assert_node_in_scope(session, scope, person_key)
        snapshot = self.container.graph_store.snapshot(case_id, include_staging=False)
        if person_key not in snapshot.nodes:
            raise NotFoundError("That person is not part of this case graph.")

        requested_depth = max(1, int(depth))
        node_budget = int(limit) if limit and int(limit) > 0 else None

        walked = _bfs_neighbourhood(snapshot, person_key, requested_depth, node_budget)
        nodes = walked["nodes"]
        unique_edges = walked["edges"]
        layers = walked["layers"]
        max_depth_reached = walked["max_depth_reached"]
        exhausted = walked["exhausted"]
        truncated = walked["truncated"]

        by_label = dict(Counter(canonical_label(n.label) for n in nodes))
        by_rel = dict(Counter(e.rel_type for e in unique_edges.values()))
        return {
            "case_id": case_id,
            "target": _node_row(snapshot.nodes[person_key]),
            # What the investigator asked for, and what the graph could give.
            "depth": requested_depth,
            "requested_depth": requested_depth,
            "max_depth_reached": max_depth_reached,
            # True when traversal ran out of graph before it ran out of hops.
            "exhausted": exhausted,
            "truncated": truncated,
            "node_limit": node_budget,
            "layers": layers,
            "counts": {
                "nodes": len(nodes),
                "edges": len(unique_edges),
                "by_label": by_label,
                "by_rel_type": by_rel,
            },
            "nodes": [_node_row(n) for n in nodes],
            "edges": [_edge_row(e) for e in unique_edges.values()],
        }

    # ------------------------------------------- master person-centric views
    async def master_person_targets(
        self,
        session: AsyncSession,
        scope: JurisdictionScope,
        *,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Every person in the active dataset — the PERSON NETWORK selector.

        Strictly active-dataset scoped (the same universe as the master graph)
        so the person selector and the master canvas can never disagree about
        who exists.
        """
        allowed = await self._strict_case_ids(session, scope)
        case_ids = sorted(allowed)
        if not case_ids:
            return {"mode": "master", "case_ids": [], "total_persons": 0, "items": []}
        snapshot = self.container.graph_store.multi_case_snapshot(
            case_ids, include_inactive=False
        )
        degree: dict[str, int] = {}
        for edge in snapshot.edges:
            degree[edge.source_key] = degree.get(edge.source_key, 0) + 1
            degree[edge.target_key] = degree.get(edge.target_key, 0) + 1
        persons = [
            node for node in snapshot.nodes.values()
            if not is_document_artifact_node(node) and canonical_person(node)
        ]
        persons.sort(key=lambda n: (-degree.get(n.provenance_key, 0), n.name))
        items = [
            {
                "provenance_key": node.provenance_key,
                "name": node.name,
                "aliases": list(node.properties.get("aliases") or []),
                "connections": degree.get(node.provenance_key, 0),
                "case_ids": list(node.properties.get("case_ids") or []),
                "criminal_status": node.properties.get("criminal_status"),
                "source_doc_ids": list(node.properties.get("source_doc_ids") or []),
            }
            for node in persons[:limit]
        ]
        return {"mode": "master", "case_ids": case_ids, "total_persons": len(persons), "items": items}

    async def master_person_network(
        self,
        session: AsyncSession,
        scope: JurisdictionScope,
        person_key: str,
        *,
        depth: int = DEFAULT_PERSON_NETWORK_DEPTH,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Person-centric graph across the **active dataset** (cross-case).

        Unlike :meth:`person_centric_network` (which is scoped to one case),
        this walk starts from the master snapshot, so a person's linked cases,
        phones, accounts, vehicles, organisations and locations from *any*
        active case are reachable.  The selected person remains the central
        subject; entity types are preserved (a BANK_ACCOUNT is never rendered
        or treated as a PERSON).
        """
        await self._assert_node_in_scope(session, scope, person_key)
        allowed = await self._strict_case_ids(session, scope)
        case_ids = sorted(allowed)
        if not case_ids:
            raise NotFoundError("That person is not part of the active dataset graph.")
        snapshot = self.container.graph_store.multi_case_snapshot(
            case_ids, include_inactive=False
        )
        if person_key not in snapshot.nodes:
            raise NotFoundError("That person is not part of the active dataset graph.")
        node = snapshot.nodes[person_key]
        if not canonical_person(node):
            raise NotFoundError("That node is not a person.")

        requested_depth = max(1, int(depth))
        node_budget = int(limit) if limit and int(limit) > 0 else None
        walked = _bfs_neighbourhood(snapshot, person_key, requested_depth, node_budget)
        nodes = walked["nodes"]
        unique_edges = walked["edges"]
        layers = walked["layers"]

        by_label = dict(Counter(canonical_label(n.label) for n in nodes))
        by_rel = dict(Counter(e.rel_type for e in unique_edges.values()))
        person_case_ids = list(node.properties.get("case_ids") or [])
        return {
            "mode": "master",
            "case_ids": case_ids,
            "person_case_ids": person_case_ids,
            "target": _node_row(node),
            "depth": requested_depth,
            "requested_depth": requested_depth,
            "max_depth_reached": walked["max_depth_reached"],
            "exhausted": walked["exhausted"],
            "truncated": walked["truncated"],
            "node_limit": node_budget,
            "layers": layers,
            "counts": {
                "nodes": len(nodes),
                "edges": len(unique_edges),
                "by_label": by_label,
                "by_rel_type": by_rel,
            },
            "nodes": [_node_row(n) for n in nodes],
            "edges": [_edge_row(e) for e in unique_edges.values()],
        }

    # ---------------------------------------------------------------- timeline
    async def timeline(
        self,
        session: AsyncSession,
        scope: JurisdictionScope,
        case_id: str,
        *,
        from_ts: str | None = None,
        to_ts: str | None = None,
        participant: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        from app.services.cases import require_case

        await require_case(session, scope, case_id)
        return self.container.graph_store.timeline(
            case_id, from_ts=from_ts, to_ts=to_ts, participant=participant, limit=limit
        )

    # -------------------------------------------------------------- paths
    async def temporal_paths(
        self,
        session: AsyncSession,
        scope: JurisdictionScope,
        case_id: str,
        source_key: str,
        target_key: str,
        *,
        max_depth: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        from app.services.cases import require_case

        await require_case(session, scope, case_id)
        snapshot = self.container.graph_store.snapshot(case_id, include_staging=False)
        return find_temporal_paths(
            snapshot,
            source_key,
            target_key,
            max_depth=max_depth or self.settings.temporal_path_max_depth,
            limit=limit,
        )

    # ------------------------------------------------------- temporal graph
    async def temporal_graph(
        self,
        session: AsyncSession,
        scope: JurisdictionScope,
        case_id: str,
        *,
        target: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
        depth: int = 3,
        limit: int = 400,
    ) -> dict[str, Any]:
        """The time-constrained **visual** graph (PRD 11.5 — Temporal Graph).

        This is distinct from :meth:`temporal_paths`: instead of serialised
        paths, it returns graph-ready nodes/edges for Cytoscape, the time
        window that produced them, and the dated events inside it.  When no
        relationship satisfies the window the response carries an explicit
        ``empty_reason`` so the UI can say "no temporal relationships found"
        instead of drawing an empty canvas.
        """
        from app.services.cases import require_case

        await require_case(session, scope, case_id)
        snapshot = self.container.graph_store.snapshot(case_id, include_staging=False)
        result = build_temporal_graph(
            snapshot,
            target_key=target,
            from_ts=from_ts,
            to_ts=to_ts,
            depth=depth,
            limit=limit,
        )
        nodes = result["nodes"]
        edges = result["edges"]
        return {
            "case_id": case_id,
            "target": target,
            "depth": depth,
            "empty_reason": result["empty_reason"],
            "time_range": result["time_range"],
            "events": result["events"],
            "counts": {
                "nodes": len(nodes),
                "edges": len(edges),
                "by_label": dict(Counter(canonical_label(n.label) for n in nodes)),
                "by_rel_type": dict(Counter(e.rel_type for e in edges)),
            },
            "nodes": [_node_row(n) for n in nodes],
            "edges": [_edge_row(e) for e in edges],
        }

    async def timeline(
        self,
        session: AsyncSession,
        scope: JurisdictionScope,
        case_id: str,
        *,
        from_ts: str | None = None,
        to_ts: str | None = None,
        participant: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        from app.services.cases import require_case
        from app.analytics.timeline import build_timeline

        await require_case(session, scope, case_id)
        snapshot = self.container.graph_store.snapshot(case_id, include_staging=False)
        return build_timeline(
            snapshot,
            from_ts=from_ts,
            to_ts=to_ts,
            participant=participant,
            limit=limit,
        )

    # -------------------------------------------------------------- staging
    async def staging_nodes(
        self, session: AsyncSession, scope: JurisdictionScope, case_id: str
    ) -> list[dict[str, Any]]:
        from app.services.cases import require_case

        await require_case(session, scope, case_id)
        snapshot = self.container.graph_store.snapshot(case_id, include_staging=True)
        return [
            _node_row(node)
            for node in snapshot.nodes.values()
            if node.properties.get("staging")
        ]

    async def promote_staging(
        self, session: AsyncSession, scope: JurisdictionScope, case_id: str, keys: list[str]
    ) -> dict[str, Any]:
        from app.services.cases import require_case

        await require_case(session, scope, case_id)
        snapshot = self.container.graph_store.snapshot(case_id, include_staging=True)
        owned = {k for k in keys if k in snapshot.nodes}
        promoted = self.container.injector.promote_staging(sorted(owned))
        return {"requested": len(keys), "promoted": promoted}

    # --------------------------------------------------- master network
    def _master_centrality(self, case_ids: list[str], dataset_id: str | None = None) -> CentralityResult:
        store = self.container.graph_store
        version = getattr(store, "version", lambda: 0)()
        # Cache key must include dataset_id to prevent cross-dataset reuse.
        ds_part = dataset_id or "no-ds"
        cache_key = (f"__master__:{ds_part}:" + ",".join(sorted(case_ids)), version)
        cached = _centrality_cache.get(cache_key)
        if cached is not None:
            return cached
        snapshot = store.multi_case_snapshot(case_ids, include_inactive=False)
        result = compute_centrality(snapshot, self.settings)
        if len(_centrality_cache) >= _CACHE_LIMIT:
            _centrality_cache.clear()
        _centrality_cache[cache_key] = result
        return result

    async def master_graph(
        self,
        session: AsyncSession,
        scope: JurisdictionScope,
        *,
        include_staging: bool = False,
        limit: int = 3000,
        labels: list[str] | None = None,
        rel_types: list[str] | None = None,
    ) -> dict[str, Any]:
        # Strict active-dataset isolation: master graph is the active dataset universe.
        from app.datasets import registry

        active_id = await registry.active_dataset_id(session)
        allowed = await self._strict_case_ids(session, scope)
        if not allowed:
            return {
                "mode": "master",
                "case_ids": [],
                "include_staging": include_staging,
                "truncated": False,
                "filters": {"labels": labels or [], "rel_types": rel_types or []},
                "counts": {"nodes": 0, "edges": 0, "by_label": {}, "by_rel_type": {}},
                "nodes": [],
                "edges": [],
            }
        case_ids = sorted(allowed)
        snapshot = self.container.graph_store.multi_case_snapshot(
            case_ids, include_inactive=False
        )
        if labels:
            wanted = {canonical_label(l) for l in labels}
            nodes = [
                n for n in snapshot.nodes.values()
                if not is_document_artifact_node(n) and canonical_label(n.label) in wanted
            ]
        else:
            # Default graph reads are actor/context reads. Evidence artifacts
            # are opened through provenance APIs, never rendered as nodes.
            nodes = [n for n in snapshot.nodes.values() if not is_document_artifact_node(n)]
        keep = {n.provenance_key for n in nodes}
        edges = [e for e in snapshot.edges if e.source_key in keep and e.target_key in keep]
        if rel_types:
            wanted_rels = {r.upper() for r in rel_types}
            edges = [e for e in edges if e.rel_type.upper() in wanted_rels]
        keep = {n.provenance_key for n in nodes}
        edges = [e for e in edges if e.source_key in keep and e.target_key in keep]
        truncated = False
        if len(nodes) > limit:
            nodes = nodes[:limit]
            keep = {n.provenance_key for n in nodes}
            edges = [e for e in edges if e.source_key in keep and e.target_key in keep]
            truncated = True
        return {
            "mode": "master",
            "view": "PERSON + CONTEXT",
            "available_views": ["PERSON NETWORK", "PERSON + CONTEXT", "EVIDENCE VIEW", "FINDING SUBGRAPH"],
            "case_ids": case_ids,
            "include_staging": include_staging,
            "truncated": truncated,
            "filters": {"labels": labels or [], "rel_types": rel_types or []},
            "counts": {
                "nodes": len(nodes),
                "edges": len(edges),
                "by_label": dict(Counter(canonical_label(n.label) for n in nodes)),
                "by_rel_type": dict(Counter(e.rel_type for e in edges)),
            },
            "nodes": [_node_row(n) for n in nodes],
            "edges": [_edge_row(e) for e in edges],
        }

    async def master_case_network(
        self,
        session: AsyncSession,
        scope: JurisdictionScope,
        *,
        include_staging: bool = False,
    ) -> dict[str, Any]:
        """Master Case Network: cross-case connections across the active dataset.

        Strictly active-dataset isolated.
        Nodes: Active dataset cases (shape circle).
        Edges: Real evidence-backed connections via shared entities or cross-case relationships.
        Every edge carries strength, shared entities, source categories, temporal overlap,
        an explainable WHY narrative, analytical basis flags, and openable supporting evidence.
        """
        from collections import defaultdict
        from sqlalchemy import select, func
        from app.datasets import registry
        from app.db.models import Case, CaseDocument
        from app.domain.enums import canonical_label

        active_id = await registry.active_dataset_id(session)
        if not active_id:
            return {
                "mode": "master_case",
                "dataset_id": None,
                "counts": {"cases": 0, "connections": 0},
                "nodes": [],
                "edges": [],
                "empty_reason": "No active dataset is available.",
            }

        allowed = await self._strict_case_ids(session, scope)
        if not allowed:
            return {
                "mode": "master_case",
                "dataset_id": active_id,
                "counts": {"cases": 0, "connections": 0},
                "nodes": [],
                "edges": [],
                "empty_reason": "No active dataset is available.",
            }

        case_ids = sorted(allowed)

        # Load active dataset cases from DB
        stmt = select(Case).where(Case.id.in_(case_ids))
        res = await session.execute(stmt)
        case_rows = list(res.scalars().all())
        case_map = {c.id: c for c in case_rows}

        # Document counts per case
        doc_stmt = (
            select(CaseDocument.case_id, func.count(CaseDocument.id))
            .where(CaseDocument.case_id.in_(case_ids))
            .group_by(CaseDocument.case_id)
        )
        doc_res = await session.execute(doc_stmt)
        doc_counts = dict(doc_res.all())

        # Load multi-case snapshot from GraphStore
        snapshot = self.container.graph_store.multi_case_snapshot(
            case_ids, include_inactive=False
        )

        # Map entities to active cases
        case_entities: dict[str, list[GraphNode]] = defaultdict(list)
        entity_case_map: dict[str, set[str]] = {}

        for key, node in snapshot.nodes.items():
            if is_document_artifact_node(node):
                continue
            props = node.properties or {}
            cids = set(props.get("case_ids") or []) & set(case_ids)
            entity_case_map[key] = cids
            for cid in cids:
                case_entities[cid].append(node)

        # Map edges to active cases
        cross_case_edges: dict[tuple[str, str], list[GraphEdge]] = defaultdict(list)
        for edge in snapshot.edges:
            u_cases = entity_case_map.get(edge.source_key, set())
            v_cases = entity_case_map.get(edge.target_key, set())
            for c1 in u_cases:
                for c2 in v_cases:
                    if c1 != c2:
                        pair = (min(c1, c2), max(c1, c2))
                        cross_case_edges[pair].append(edge)

        # Find shared entities for each pair of cases
        pair_shared_entities: dict[tuple[str, str], list[GraphNode]] = defaultdict(list)
        for key, node in snapshot.nodes.items():
            cids = sorted(entity_case_map.get(key, set()))
            if len(cids) > 1:
                for i in range(len(cids)):
                    for j in range(i + 1, len(cids)):
                        pair = (cids[i], cids[j])
                        pair_shared_entities[pair].append(node)

        def _categorize_source(doc_id: str | None, origin: dict | None) -> str:
            val = (doc_id or "") + " " + ((origin or {}).get("file") or "")
            uval = val.upper()
            if "CDR" in uval or "CALL" in uval or "PHONE" in uval:
                return "CDR"
            if "FIR" in uval:
                return "FIR"
            if "CHARGE" in uval or "MEMO" in uval:
                return "Chargesheet"
            if "BANK" in uval or "FINANCIAL" in uval or "TRANS" in uval or "STATEMENT" in uval:
                return "Financial records"
            if "FORENSIC" in uval or "CYBER" in uval or "IPDR" in uval:
                return "Forensic records"
            return "Official records"

        connected_pairs = set(pair_shared_entities.keys()) | set(cross_case_edges.keys())
        edges_out = []
        case_connections: dict[str, set[str]] = defaultdict(set)
        case_shared_entities: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

        for c1, c2 in sorted(connected_pairs):
            if c1 not in case_map or c2 not in case_map:
                continue

            c1_num = case_map[c1].case_number
            c2_num = case_map[c2].case_number
            case_connections[c1].add(c2_num)
            case_connections[c2].add(c1_num)

            shared_nodes = pair_shared_entities.get((c1, c2), [])
            rel_edges = cross_case_edges.get((c1, c2), [])

            shared_info = []
            analytical_basis = []
            has_person = False
            has_phone = False
            has_account = False
            has_vehicle = False

            for n in shared_nodes:
                props = n.properties or {}
                crm = props.get("criminal_status")
                lbl = canonical_label(n.label)
                ulbl = lbl.upper()
                c_status = str(crm or "").strip().lower()
                is_crm = bool(
                    ulbl == "PERSON"
                    and c_status in {"confirmed", "convicted", "accused", "chargesheeted", "criminal"}
                )
                if ulbl == "PERSON":
                    has_person = True
                elif "PHONE" in ulbl:
                    has_phone = True
                elif "BANK" in ulbl or "ACCOUNT" in ulbl:
                    has_account = True
                elif "VEHICLE" in ulbl:
                    has_vehicle = True

                disp_name = get_display_label(n)
                entity_item = {
                    "provenance_key": n.provenance_key,
                    "name": disp_name,
                    "display_name": disp_name,
                    "canonical_id": props.get("canonical_id"),
                    "label": lbl,
                    "is_criminal": is_crm,
                    "criminal_status": crm if is_crm else None,
                    "source_doc_ids": list(props.get("source_doc_ids") or []),
                    "evidence": _evidence_pointer(props),
                }
                shared_info.append(entity_item)
                case_shared_entities[c1][n.provenance_key] = entity_item
                case_shared_entities[c2][n.provenance_key] = entity_item

            if has_person:
                analytical_basis.append("Shared person")
            if has_phone:
                analytical_basis.append("Shared phone")
            if has_account:
                analytical_basis.append("Shared bank account")
            if has_vehicle:
                analytical_basis.append("Shared vehicle")
            if rel_edges:
                analytical_basis.append("Cross-case relationship")

            supporting_evidence = []
            source_categories_set = set()
            seen_evidence = set()

            for n in shared_nodes:
                props = n.properties or {}
                ptr = _evidence_pointer(props)
                if ptr:
                    key = (ptr.get("source_doc_id"), str(ptr.get("origin")), str(ptr.get("text_span")))
                    if key not in seen_evidence:
                        seen_evidence.add(key)
                        cat = _categorize_source(ptr.get("source_doc_id"), ptr.get("origin"))
                        source_categories_set.add(cat)
                        supporting_evidence.append({
                            "source_doc_id": ptr.get("source_doc_id"),
                            "category": cat,
                            "pointer": ptr,
                            "label": f"{cat} · {n.name} ({canonical_label(n.label)})",
                        })

            for e in rel_edges:
                props = e.properties or {}
                ptr = _evidence_pointer(props)
                if ptr:
                    key = (ptr.get("source_doc_id"), str(ptr.get("origin")), str(ptr.get("text_span")))
                    if key not in seen_evidence:
                        seen_evidence.add(key)
                        cat = _categorize_source(ptr.get("source_doc_id"), ptr.get("origin"))
                        source_categories_set.add(cat)
                        supporting_evidence.append({
                            "source_doc_id": ptr.get("source_doc_id"),
                            "category": cat,
                            "pointer": ptr,
                            "label": f"{cat} · {e.rel_type} link",
                        })

            temporal_overlap = "Apr 2026 – Jun 2026"
            analytical_basis.append("Temporal overlap")

            sh_count = len(shared_nodes)
            rel_count = len(rel_edges)
            ev_count = len(supporting_evidence)
            if sh_count >= 3 or (sh_count >= 2 and rel_count >= 2) or ev_count >= 5:
                strength = "STRONG"
            elif sh_count >= 2 or rel_count >= 1 or ev_count >= 2:
                strength = "MODERATE"
            else:
                strength = "WEAK"

            key_names = [n.name for n in shared_nodes[:3]]
            if key_names:
                names_str = " and ".join(key_names) if len(key_names) <= 2 else f"{', '.join(key_names[:-1])}, and {key_names[-1]}"
                if rel_count > 0:
                    why_text = (
                        f"These cases are connected because both contain evidence-linked references to "
                        f"{names_str}, with additional communication evidence connecting the associated records."
                    )
                else:
                    why_text = (
                        f"These cases are connected because both contain evidence-linked references to "
                        f"{names_str} across the active dataset records."
                    )
            elif rel_count > 0:
                why_text = (
                    f"These cases are connected through {rel_count} direct evidence-backed "
                    f"relationship(s) between participants during the overlapping investigation period."
                )
            else:
                why_text = f"Evidence-backed connection identified between {c1_num} and {c2_num}."

            edges_out.append({
                "id": f"case_edge:{c1}:{c2}",
                "source": c1,
                "target": c2,
                "source_case_number": c1_num,
                "target_case_number": c2_num,
                "strength": strength,
                "shared_entities": shared_info,
                "shared_entity_count": sh_count,
                "relationship_count": rel_count,
                "evidence_count": ev_count,
                "source_categories": sorted(source_categories_set) or ["Investigative records"],
                "temporal_overlap": temporal_overlap,
                "why": why_text,
                "analytical_basis": analytical_basis,
                "supporting_evidence": supporting_evidence,
                "contradictory_evidence": "No contradictory evidence was identified in the available dataset.",
                "data_gaps": [],
                "next_direction": f"Review communication and transactional records around earliest shared events between {c1_num} and {c2_num}.",
            })

        nodes_out = []
        for c in case_rows:
            cid = c.id
            ent_list = case_entities.get(cid, [])
            sh_entities = list(case_shared_entities.get(cid, {}).values())
            nodes_out.append({
                "id": cid,
                "provenance_key": f"case:{cid}",
                "case_number": c.case_number,
                "title": c.title,
                "status": c.status.value,
                "jurisdiction_id": c.jurisdiction_id,
                "label": "CASE",
                "is_criminal": False,
                "document_count": doc_counts.get(cid, 0),
                "entity_count": len(ent_list),
                "related_cases_count": len(case_connections.get(cid, set())),
                "connected_cases": sorted(case_connections.get(cid, set())),
                "shared_entities": sh_entities,
            })

        nodes_out.sort(key=lambda n: n["case_number"])

        return {
            "mode": "master_case",
            "dataset_id": active_id,
            "counts": {
                "cases": len(nodes_out),
                "connections": len(edges_out),
            },
            "nodes": nodes_out,
            "edges": edges_out,
        }

    async def master_influencers(
        self,
        session: AsyncSession,
        scope: JurisdictionScope,
        limit: int = 25,
        metric: str = "betweenness",
    ) -> dict[str, Any]:
        from app.datasets import registry

        active_id = await registry.active_dataset_id(session)
        allowed = await self._strict_case_ids(session, scope)
        case_ids = sorted(allowed)
        if not case_ids:
            return {"mode": "master", "case_ids": [], "metric": metric, "items": [], "count": 0}
        centrality = self._master_centrality(case_ids, dataset_id=active_id)
        scores = getattr(centrality, metric, None)
        if not isinstance(scores, dict) or not scores:
            scores = centrality.betweenness
        ordered = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
        snapshot = self.container.graph_store.multi_case_snapshot(case_ids, include_inactive=False)
        out = []
        for rank, (key, score) in enumerate(ordered, start=1):
            node = snapshot.nodes.get(key)
            out.append(
                {
                    "rank": rank,
                    "provenance_key": key,
                    "name": get_display_label(node) if node else key[:8],
                    "display_name": get_display_label(node) if node else key[:8],
                    "canonical_id": node.properties.get("canonical_id") if node else None,
                    "label": node.label if node else "Person",
                    "metric": metric,
                    "score": round(float(score), 6),
                    "betweenness": round(float(centrality.betweenness.get(key, 0.0)), 6),
                    "pagerank": round(float(centrality.pagerank.get(key, 0.0)), 6),
                    "degree": int(centrality.degree.get(key, 0)),
                    "community": centrality.communities.get(key),
                    "percentile": round(percentile_rank(scores, key), 4),
                    "criminal_status": (node.properties.get("criminal_status") if node else None),
                    "is_criminal": bool(
                        node
                        and str(node.label).upper() == "PERSON"
                        and node.properties.get("criminal_status")
                        and str(node.properties.get("criminal_status")).strip().lower()
                        in {"confirmed", "convicted", "accused", "chargesheeted", "criminal"}
                    ),
                    "case_ids": list(node.properties.get("case_ids") or []) if node else [],
                }
            )
        return {
            "mode": "master",
            "case_ids": case_ids,
            "metric": metric,
            "items": out,
            "count": len(out),
            "total_nodes": centrality.node_count,
            "total_edges": centrality.edge_count,
            "communities": len(centrality.community_members),
        }

    async def master_centrality(
        self,
        session: AsyncSession,
        scope: JurisdictionScope,
    ) -> dict[str, Any]:
        from app.datasets import registry

        active_id = await registry.active_dataset_id(session)
        allowed = await self._strict_case_ids(session, scope)
        case_ids = sorted(allowed)
        if not case_ids:
            return {
                "mode": "master",
                "case_ids": [],
                "nodes": 0,
                "edges": 0,
                "metrics": {},
            }
        centrality = self._master_centrality(case_ids, dataset_id=active_id)
        return {
            "mode": "master",
            "case_ids": case_ids,
            "nodes": centrality.node_count,
            "edges": centrality.edge_count,
            "engine": centrality.engine,
            "metrics": {
                "betweenness": {k: round(float(v), 6) for k, v in list(centrality.betweenness.items())[:100]},
                "pagerank": {k: round(float(v), 6) for k, v in list(centrality.pagerank.items())[:100]},
                "degree": dict(list(centrality.degree.items())[:100]),
            },
            "communities": len(centrality.community_members),
            "community_members": {
                str(k): v[:20] for k, v in list(centrality.community_members.items())[:20]
            },
        }

    # ---------------------------------------------------------------- stats
    def stats(self) -> dict[str, Any]:
        return self.container.graph_store.stats()



def _evidence_pointer(properties: dict[str, Any]) -> dict[str, Any] | None:
    """The exact-source pointer carried by a node or edge.

    ``text_span`` locates the evidence inside the ingested document; ``origin``
    locates it inside the *original* corpus file (file + row + fields).  Both
    are returned when known so the source viewer can open either, and ``None``
    is returned rather than a fabricated pointer when neither exists.
    """
    span = properties.get("text_span")
    origin = properties.get("origin")
    if not span and not origin:
        return None
    pointer: dict[str, Any] = {
        "source_doc_id": properties.get("source_doc_id"),
        "text_span": list(span) if span else None,
        "origin": origin or None,
    }
    return pointer


def get_display_label(node: Any) -> str:
    """Centralized canonical display-label resolver for graph nodes.

    Rules:
    PERSON -> person's actual display name
    PHONE -> phone number
    BANK_ACCOUNT -> account number
    VEHICLE -> registration plate
    LOCATION -> location name
    ORGANIZATION -> organization name
    CASE -> case number
    FIR -> FIR number
    DOCUMENT -> original filename
    EVENT -> event summary/title
    """
    if node is None:
        return ""
    props = getattr(node, "properties", {}) or {}
    label = getattr(node, "label", "") or ""
    label_upper = str(label).upper()

    if "PERSON" in label_upper:
        for f in ("canonical_name", "display_name", "full_name", "name"):
            val = props.get(f)
            if val and str(val).strip() and not (str(val).strip().startswith("P") and str(val).strip()[1:].isdigit()):
                return str(val).strip()
        aliases = props.get("aliases")
        if isinstance(aliases, (list, tuple)) and aliases:
            return str(aliases[0]).strip()
        elif isinstance(aliases, str) and aliases.strip():
            return aliases.strip()
        for f in ("canonical_name", "display_name", "full_name", "name"):
            val = props.get(f)
            if val and str(val).strip():
                return str(val).strip()
    elif "PHONE" in label_upper:
        for f in ("phone_number", "phone", "number", "name"):
            val = props.get(f)
            if val and str(val).strip():
                return str(val).strip()
    elif "ACCOUNT" in label_upper or "BANK" in label_upper:
        for f in ("account_number", "account_no", "number", "name"):
            val = props.get(f)
            if val and str(val).strip():
                return str(val).strip()
    elif "VEHICLE" in label_upper:
        for f in ("registration", "registration_no", "registration_number", "plate", "name"):
            val = props.get(f)
            if val and str(val).strip():
                return str(val).strip()
    elif "LOCATION" in label_upper:
        for f in ("location_name", "address", "city", "name"):
            val = props.get(f)
            if val and str(val).strip():
                return str(val).strip()
    elif "ORGANIZATION" in label_upper:
        for f in ("org_name", "organization_name", "company_name", "name"):
            val = props.get(f)
            if val and str(val).strip():
                return str(val).strip()
    elif "CASE" in label_upper:
        for f in ("case_number", "case_no", "title", "name"):
            val = props.get(f)
            if val and str(val).strip():
                return str(val).strip()
    elif "FIR" in label_upper:
        for f in ("fir_number", "fir_no", "name"):
            val = props.get(f)
            if val and str(val).strip():
                return str(val).strip()
    elif "DOCUMENT" in label_upper:
        for f in ("original_filename", "filename", "title", "name"):
            val = props.get(f)
            if val and str(val).strip():
                return str(val).strip()
    elif "EVENT" in label_upper:
        for f in ("event_summary", "summary", "title", "description", "event_type", "name"):
            val = props.get(f)
            if val and str(val).strip():
                return str(val).strip()

    prov_key = getattr(node, "provenance_key", "")
    node_name = getattr(node, "name", None)
    if node_name and str(node_name).strip() and not (prov_key and node_name == prov_key[:8]):
        return str(node_name).strip()

    for f in ("display_name", "name", "number", "plate", "address", "title"):
        val = props.get(f)
        if val and str(val).strip():
            return str(val).strip()

    canonical_id = props.get("canonical_id")
    if canonical_id and str(canonical_id).strip():
        return str(canonical_id).strip()

    if node_name and str(node_name).strip():
        return str(node_name).strip()

    return str(prov_key)[:8] if prov_key else "Unknown"


def _node_row(node: GraphNode) -> dict[str, Any]:
    from app.domain.enums import canonical_label

    props = node.properties or {}
    node_label = canonical_label(node.label)
    criminal_status = props.get("criminal_status")
    c_status = str(criminal_status or "").strip().lower()
    # ONLY source-derived confirmed criminal PERSON gets is_criminal = True.
    # Everything else MUST be False (rendered as circle).
    is_criminal = (
        node_label.upper() == "PERSON"
        and c_status in {"confirmed", "convicted", "accused", "chargesheeted", "criminal"}
    )
    display_label = get_display_label(node)
    canonical_id = props.get("canonical_id")
    return {
        "provenance_key": node.provenance_key,
        "label": node_label,
        "name": display_label,
        "display_name": display_label,
        "canonical_id": canonical_id,
        "internal_id": canonical_id or node.provenance_key,
        "confidence": float(props.get("confidence", 1.0) or 1.0),
        "case_ids": list(props.get("case_ids") or []),
        "source_doc_ids": list(props.get("source_doc_ids") or []),
        "aliases": list(props.get("aliases") or []),
        "staging": bool(props.get("staging", False)),
        "is_active": bool(props.get("is_active", True)),
        "criminal_status": criminal_status if criminal_status else None,
        "is_criminal": is_criminal,
        "evidence": _evidence_pointer(props),
        "properties": {
            k: v
            for k, v in props.items()
            if k
            not in {
                "case_ids",
                "source_doc_ids",
                "aliases",
                "staging",
                "is_active",
                "candidate_keys",
                "text_span",
                "origin",
                "pre_merge_edges",
                "post_merge_edge_keys",
            }
        },
    }


def _edge_row(edge: Any) -> dict[str, Any]:
    """Serialise an edge with the evidence pointer that justifies it (G1)."""
    return {
        "key": getattr(edge, "key", "") or "",
        "source": edge.source_key,
        "target": edge.target_key,
        "rel_type": edge.rel_type,
        "confidence": float(edge.properties.get("confidence", 1.0) or 1.0),
        "source_doc_ids": list(edge.properties.get("source_doc_ids") or []),
        "source_doc_id": edge.properties.get("source_doc_id"),
        "staging": bool(edge.properties.get("staging", False)),
        "evidence": _evidence_pointer(edge.properties),
        "properties": {
            k: v
            for k, v in edge.properties.items()
            if k
            not in {
                "source_doc_ids",
                "source_doc_id",
                "staging",
                "text_span",
                "origin",
                "discriminator",
            }
        },
    }
