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
from app.analytics.temporal import find_temporal_paths
from app.config import Settings, get_settings
from app.container import Container, get_container
from app.db.models import Case
from app.domain.models import GraphNode
from app.errors import NotFoundError
from app.logging import get_logger
from app.security.deps import JurisdictionScope

log = get_logger("crimelink.services.graph")

# (case_id, graph_version) -> centrality.  Invalidated automatically because the
# version changes on every graph write.
_centrality_cache: dict[tuple[str, int], CentralityResult] = {}
_CACHE_LIMIT = 32


class GraphService:
    def __init__(self, container: Container | None = None, settings: Settings | None = None):
        self.container = container or get_container()
        self.settings = settings or self.container.settings

    # ------------------------------------------------------------- scoping
    async def _allowed_case_ids(self, session: AsyncSession, scope: JurisdictionScope) -> set[str]:
        rows = (await session.execute(select(Case.id).where(scope.case_filter()))).scalars().all()
        return set(rows)

    async def _assert_node_in_scope(
        self, session: AsyncSession, scope: JurisdictionScope, key: str
    ) -> GraphNode:
        node = self.container.graph_store.get_node(key)
        if node is None:
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
    ) -> dict[str, Any]:
        """Everything needed to draw the case canvas, in Cytoscape element form."""
        from app.services.cases import require_case

        await require_case(session, scope, case_id)
        snapshot = self.container.graph_store.snapshot(case_id, include_staging=include_staging)
        nodes = list(snapshot.nodes.values())
        edges = list(snapshot.edges)
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
            "include_staging": include_staging,
            "truncated": truncated,
            "counts": {
                "nodes": len(nodes),
                "edges": len(edges),
                "by_label": dict(Counter(n.label for n in nodes)),
                "by_rel_type": dict(Counter(e.rel_type for e in edges)),
            },
            "nodes": [_node_row(n) for n in nodes],
            "edges": [_edge_row(e) for e in edges],
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


def _node_row(node: GraphNode) -> dict[str, Any]:
    return {
        "provenance_key": node.provenance_key,
        "label": node.label,
        "name": node.name,
        "confidence": float(node.properties.get("confidence", 1.0) or 1.0),
        "case_ids": list(node.properties.get("case_ids") or []),
        "source_doc_ids": list(node.properties.get("source_doc_ids") or []),
        "aliases": list(node.properties.get("aliases") or []),
        "staging": bool(node.properties.get("staging", False)),
        "is_active": bool(node.properties.get("is_active", True)),
        "evidence": _evidence_pointer(node.properties),
        "properties": {
            k: v
            for k, v in node.properties.items()
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
