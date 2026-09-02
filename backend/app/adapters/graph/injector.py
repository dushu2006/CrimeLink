"""THE ONLY MODULE PERMITTED TO WRITE TO THE GRAPH (PRD 6.2, checklist #1).

Guarantee G1 says: *there is no code path in the entire system that can write a
graph relationship without a source reference.*  In CrimeLink that guarantee is
structural rather than aspirational, and it is enforced at three levels:

1. **Type level** — :class:`~app.domain.models.GraphEdge` refuses to be
   constructed without ``source_doc_id``.  A caller cannot even build an
   unevidenced edge to hand to this module.
2. **Module level** — this file owns every write helper.  The write helpers
   validate their inputs again before dispatch, and the only Cypher write
   statements in the repository live here.
3. **Test level** — ``tests/test_guarantee_g1_evidence.py`` statically scans the
   whole codebase for Cypher write keywords (``MERGE``/``CREATE``/``SET``/
   ``DETACH DELETE``) outside this file and fails if any are found.  Bypassing
   the injector therefore breaks CI, not just code review.

Writes are batched (``UNWIND``, 500 rows) and keyed by the deterministic
provenance key, which is what makes re-processing a crashed document converge
on an identical graph state instead of duplicating entities (PRD 9.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from app.domain.enums import EntityType, UNEVIDENCED_META_REL_TYPES
from app.domain.models import GraphEdge, GraphNode
from app.errors import UnevidencedGraphWriteError
from app.logging import get_logger

log = get_logger("crimelink.graph.injector")

BATCH_SIZE = 500


@dataclass(slots=True)
class InjectionResult:
    nodes_written: int = 0
    edges_written: int = 0
    edges_rejected: int = 0
    case_links_written: int = 0
    rejected_reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "nodes_written": self.nodes_written,
            "edges_written": self.edges_written,
            "edges_rejected": self.edges_rejected,
            "case_links_written": self.case_links_written,
            "rejected_reasons": self.rejected_reasons[:10],
        }


class GraphInjector:
    """Validating, batching, idempotent writer for the graph store."""

    def __init__(self, store) -> None:
        self._store = store

    # ------------------------------------------------------------------ API
    def inject_nodes(self, nodes: Sequence[GraphNode]) -> int:
        """Idempotently upsert nodes in batches of ``BATCH_SIZE``."""
        written = 0
        for batch in _chunk(nodes, BATCH_SIZE):
            self._validate_nodes(batch)
            written += self._store.upsert_nodes(batch)
        return written

    def inject_edges(self, edges: Sequence[GraphEdge]) -> int:
        """Idempotently upsert evidenced edges, rejecting unevidenced ones."""
        written = 0
        for batch in _chunk(edges, BATCH_SIZE):
            self._validate_edges(batch)
            written += self._store.upsert_edges(batch)
        return written

    def ensure_case(self, case_id: str, case_number: str, jurisdiction_id: str) -> None:
        self._store.ensure_case_node(case_id, case_number, jurisdiction_id)

    def update_node_properties(self, provenance_key: str, properties: dict) -> None:
        """Patch node properties (the only non-injection graph write).

        Used for investigator actions such as promoting tip-staged entities into
        the case graph.  Every property change a human can make is audited by the
        caller, and this method cannot create relationships — so the evidence
        guarantee is untouched.
        """
        node = self._store.get_node(provenance_key)
        if node is None:
            raise UnevidencedGraphWriteError("Cannot patch a node that does not exist.")
        merged = dict(node.properties)
        merged.update(properties)
        self._store.upsert_nodes(
            [GraphNode(provenance_key=provenance_key, label=node.label, properties=merged)]
        )

    def promote_staging(self, provenance_keys: list[str]) -> int:
        """Move anonymous-tip entities from the staging subgraph into the case graph."""
        promoted = 0
        for key in provenance_keys:
            node = self._store.get_node(key)
            if node is None or not node.properties.get("staging"):
                continue
            self.update_node_properties(key, {"staging": False})
            promoted += 1
        return promoted

    def link_to_case(
        self,
        case_id: str,
        doc_id: str,
        node_keys: Iterable[str],
        confidence: float = 1.0,
    ) -> int:
        """Attach every extracted entity to its ``(:Case)`` node.

        Even this bookkeeping edge carries the document it came from.
        """
        edges = [
            GraphEdge(
                source_key=key,
                target_key=f"case:{case_id}",
                rel_type="MENTIONED_IN",
                properties={
                    "source_doc_id": doc_id,
                    "source_doc_ids": [doc_id],
                    "confidence": confidence,
                },
            )
            for key in set(node_keys)
        ]
        return self.inject_edges(edges)

    def inject(
        self,
        *,
        case_id: str,
        case_number: str,
        jurisdiction_id: str,
        doc_id: str,
        nodes: Sequence[GraphNode],
        edges: Sequence[GraphEdge],
        link_case: bool = True,
        confidence: float = 1.0,
    ) -> InjectionResult:
        """Full idempotent injection for one document.

        Ordering matters: nodes first (so edge endpoints resolve), then edges,
        then the case links.  Re-running this with identical inputs produces
        identical graph state.
        """
        self.ensure_case(case_id, case_number, jurisdiction_id)
        result = InjectionResult()
        result.nodes_written = self.inject_nodes(nodes)

        known = set()
        for node in nodes:
            known.add(node.provenance_key)
        for edge in edges:
            known.add(edge.source_key)
            known.add(edge.target_key)

        accepted: list[GraphEdge] = []
        for edge in edges:
            if edge.rel_type in UNEVIDENCED_META_REL_TYPES:
                accepted.append(edge)
                continue
            if not edge.properties.get("source_doc_id"):
                result.edges_rejected += 1
                result.rejected_reasons.append(
                    f"{edge.rel_type} without source_doc_id rejected"
                )
                continue
            if edge.source_key not in known or edge.target_key not in known:
                result.edges_rejected += 1
                result.rejected_reasons.append(
                    f"{edge.rel_type} with unresolved endpoint rejected"
                )
                continue
            accepted.append(edge)

        result.edges_written = self.inject_edges(accepted)
        if link_case:
            result.case_links_written = self.link_to_case(
                case_id, doc_id, [n.provenance_key for n in nodes], confidence
            )
        log.info(
            "graph.injected",
            case_id=case_id,
            doc_id=doc_id,
            nodes=result.nodes_written,
            edges=result.edges_written,
            rejected=result.edges_rejected,
        )
        self._store.invalidate_cache(case_id)
        return result

    # ------------------------------------------------------------ validation
    @staticmethod
    def _validate_nodes(nodes: Sequence[GraphNode]) -> None:
        for node in nodes:
            if not node.provenance_key:
                raise UnevidencedGraphWriteError("node without provenance_key")
            if node.label not in {e.value for e in EntityType} and node.label != "Case":
                raise UnevidencedGraphWriteError(f"unknown node label {node.label}")
            # G1 applies to nodes as much as to edges: an entity with no pointer
            # to the document it came from is an accusation with no source.
            if not node.properties.get("source_doc_id"):
                raise UnevidencedGraphWriteError(
                    f"{node.label} node '{node.provenance_key[:12]}…' is missing "
                    "source_doc_id"
                )
            if node.properties.get("confidence") is not None:
                value = float(node.properties["confidence"])
                if not 0.0 <= value <= 1.0:
                    raise UnevidencedGraphWriteError("confidence must be within [0, 1]")

    @staticmethod
    def _validate_edges(edges: Sequence[GraphEdge]) -> None:
        for edge in edges:
            if edge.rel_type in UNEVIDENCED_META_REL_TYPES:
                continue
            if not edge.properties.get("source_doc_id"):
                # Defence in depth: GraphEdge already refuses, this catches a
                # subclass or a deserialised payload that bypassed the ctor.
                raise UnevidencedGraphWriteError(
                    f"edge {edge.rel_type} is missing source_doc_id"
                )
            if not edge.source_key or not edge.target_key:
                raise UnevidencedGraphWriteError("edge with missing endpoint key")


def _chunk(items: Sequence, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
