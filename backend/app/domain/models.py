"""Core data structures that flow between pipeline stages.

These are plain dataclasses on purpose: they are the *lingua franca* between
the adapters, the extraction stages, entity resolution, the injector and the
analytics layer.  Keeping them free of any database or driver dependency is
what allows the same pipeline code to run against Neo4j or the embedded graph
without a single conditional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from app.domain.enums import (
    REL_TYPES,
    UNEVIDENCED_META_REL_TYPES,
    EntityType,
    SourceConfidence,
)
from app.domain.provenance import edge_key
from app.errors import UnevidencedGraphWriteError

BlockKind = Literal["text", "table_row", "record"]


# ---------------------------------------------------------------------------
# Stage 1 output: a source document reduced to typed content units
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Block:
    """One addressable unit of content inside a normalised document.

    ``text`` is what regex/NLP extraction sees.  ``offset`` locates it inside
    the normalised plain-text rendering of the document, which is what makes
    the evidence pointer (``text_span``) resolvable later.  ``data`` carries
    the structured fields for record-like sources (a CDR row, a bank transfer).
    """

    kind: BlockKind
    text: str
    offset: int = 0
    page: int | None = None
    line: int | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def span(self) -> tuple[int, int]:
        return (self.offset, self.offset + len(self.text))


@dataclass(slots=True)
class NormalizedDocument:
    """Common intermediate representation produced by every source adapter."""

    doc_id: str
    case_id: str
    doc_type: str
    language: str = "en"
    source_confidence: SourceConfidence = SourceConfidence.UNVERIFIED
    blocks: list[Block] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)

    def iter_text_blocks(self) -> list[Block]:
        return [b for b in self.blocks if b.kind == "text"]


# ---------------------------------------------------------------------------
# Stages 2 & 3 output: extraction candidates
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ExtractionCandidate:
    """An entity mention found in a document (PRD 8.3 output contract)."""

    entity_type: EntityType
    normalized_value: str
    display_value: str
    attributes: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    source_doc_id: str = ""
    case_id: str = ""
    text_span: tuple[int, int] = (0, 0)
    language: str = "en"
    extractor: str = "deterministic"   # "deterministic" | "nlp"
    staging: bool = False              # anonymous tips stay staged (PRD 7)

    def provenance_key(self, doc_id: str | None = None) -> str:
        from app.domain.provenance import candidate_key

        return candidate_key(
            self.case_id,
            doc_id or self.source_doc_id,
            self.entity_type.value,
            self.normalized_value,
        )


@dataclass(slots=True)
class RelationCandidate:
    """A relationship between two extracted entities (PRD 8.2).

    The ``text_span`` is generated *here*, at the moment of extraction — it is
    never reconstructed after the fact.
    """

    source_type: EntityType
    source_value: str
    target_type: EntityType
    target_value: str
    rel_type: str
    confidence: float = 1.0
    attributes: dict[str, Any] = field(default_factory=dict)
    source_doc_id: str = ""
    case_id: str = ""
    text_span: tuple[int, int] = (0, 0)
    extractor: str = "deterministic"
    staging: bool = False
    # For aggregated edges (CALLED/TRANSFER_TO) this is the record identity
    discriminator: str = ""

    def provenance_pair(self, doc_id: str | None = None) -> tuple[str, str]:
        from app.domain.provenance import candidate_key

        doc = doc_id or self.source_doc_id
        return (
            candidate_key(self.case_id, doc, self.source_type.value, self.source_value),
            candidate_key(self.case_id, doc, self.target_type.value, self.target_value),
        )


# ---------------------------------------------------------------------------
# Stage 5 input: the only structures allowed to touch the graph
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GraphNode:
    """A node to be written by the injector."""

    provenance_key: str
    label: str
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.label not in EntityType._value2member_map_ and self.label != "Case":
            raise ValueError(f"Unknown node label: {self.label}")

    @property
    def name(self) -> str:
        for key in ("name", "number", "plate", "address", "description", "title"):
            value = self.properties.get(key)
            if value:
                return str(value)
        return self.provenance_key[:8]


@dataclass(slots=True)
class GraphEdge:
    """A relationship to be written by the injector.

    Guarantee G1 is enforced *structurally*: an edge cannot be constructed
    without ``source_doc_id``.  There is no ``GraphEdge`` in the system that
    lacks evidence, so no code path can smuggle one into the injector.
    """

    source_key: str
    target_key: str
    rel_type: str
    properties: dict[str, Any] = field(default_factory=dict)
    discriminator: str = ""
    key: str = ""

    def __post_init__(self) -> None:
        if self.rel_type not in REL_TYPES:
            raise ValueError(f"Unknown relationship type: {self.rel_type}")
        if self.rel_type not in UNEVIDENCED_META_REL_TYPES and not self.properties.get(
            "source_doc_id"
        ):
            # Guarantee G1, enforced here rather than in code review.
            raise UnevidencedGraphWriteError(
                "Refusing to build a graph edge without source_doc_id "
                f"({self.rel_type} {self.source_key[:8]}->{self.target_key[:8]})"
            )
        if not self.key:
            self.key = edge_key(
                self.rel_type, self.source_key, self.target_key, self.discriminator
            )

    @property
    def confidence(self) -> float:
        try:
            return float(self.properties.get("confidence", 1.0))
        except (TypeError, ValueError):
            return 1.0


# ---------------------------------------------------------------------------
# Analytics snapshot
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CaseGraphSnapshot:
    """Immutable, backend-independent view of a case subgraph.

    Analytics (centrality, communities, patterns, temporal pathfinding) runs
    against this structure for *every* backend.  On Neo4j the snapshot is
    produced by a single projection query; on the embedded backend it is the
    in-memory graph.  The rules therefore have exactly one implementation,
    which is what makes them auditable and testable.
    """

    case_id: str
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)

    def edges_by_type(self, rel_type: str) -> list[GraphEdge]:
        return [e for e in self.edges if e.rel_type == rel_type]

    def node_count(self) -> int:
        return len(self.nodes)


@dataclass(slots=True)
class MergeResult:
    """Outcome of a human-approved entity merge (reversible by design)."""

    kept_key: str
    absorbed_key: str
    rerouted_edges: int
    merged_into_marker: str


@dataclass(slots=True)
class StageProgress:
    """Progress event streamed to the UI over WebSocket (PRD 10 / 14)."""

    doc_id: str
    case_id: str
    stage: int
    stage_name: str
    status: Literal["RUNNING", "DONE", "FAILED"]
    detail: str = ""
    at: datetime | None = None
