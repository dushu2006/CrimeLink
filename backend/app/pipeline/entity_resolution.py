"""Stage 4 — entity resolution (PRD 9).

This is where CrimeLink decides whether "Ramesh Yadav" in an FIR and
"Ramesh K. Yadav" in a CDR are the same human being.  Getting it wrong links an
innocent person into a criminal graph; getting it too conservative misses real
connections.  The design principle is:

    **Deterministic where possible, human-decided where not, and never silently
    re-decided.**

Tier 1 — Hard match (automatic)
    Exact match on a physically unique identifier: phone number, bank account,
    vehicle plate, IFSC, or canonical place name.  These cannot conflate two
    people, so merging is safe and automatic.

Tier 2 — Fuzzy match (always a human decision)
    Names above the similarity threshold (default 0.85) with no hard identifier
    produce **two separate nodes**, a ``POTENTIAL_ALIAS`` edge, and a row in the
    entity-resolution queue.  An investigator merges or rejects with a mandatory
    written rationale.  Rejections are tombstoned so the pair is never
    re-proposed — without that tombstone, rejected pairs resurface on every
    re-ingest, which is the classic alert-fatigue death spiral.

Devanagari note (PRD 9.2)
    ``pg_trgm`` and plain trigram similarity behave poorly on Devanagari because
    vowels are combining marks and the virama deletes the inherent 'a'.  All name
    comparison therefore goes through ISO-15919 transliteration first, so
    ``रमेश`` and ``Ramesh`` are comparable.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from app.config import Settings, get_settings
from app.domain.enums import EntityType, MatchBasis
from app.domain.models import (
    ExtractionCandidate,
    GraphEdge,
    GraphNode,
    NormalizedDocument,
    RelationCandidate,
)
from app.domain.normalize import combined_similarity, normalize_name
from app.domain.provenance import candidate_key
from app.logging import get_logger

log = get_logger("crimelink.pipeline.er")

# Entity types whose canonical identity is a hard identifier.
HARD_IDENTIFIER_TYPES: frozenset[EntityType] = frozenset(
    {EntityType.PHONE, EntityType.VEHICLE, EntityType.BANK_ACCOUNT}
)
# Entity types that are canonicalised across documents but are not unique
# identifiers of a person (so a miss is recoverable, not catastrophic).
CANONICAL_VALUE_TYPES: frozenset[EntityType] = frozenset(
    {EntityType.LOCATION, EntityType.ORGANIZATION}
)
EVENT_TYPES: frozenset[EntityType] = frozenset({EntityType.EVENT})


@dataclass(slots=True)
class AggregatedCandidate:
    """One deduplicated entity mention within a document."""

    entity_type: EntityType
    normalized_value: str
    display_value: str
    attributes: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    text_span: tuple[int, int] = (0, 0)
    staging: bool = False
    extractor: str = "deterministic"
    mentions: int = 0

    @property
    def label(self) -> str:
        return self.entity_type.value


@dataclass(slots=True)
class ResolutionOutcome:
    """Everything stage 5 needs to write, plus what stage 4 decided."""

    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    alias_proposals: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "alias_proposals": len(self.alias_proposals),
            **self.stats,
        }


class EntityResolver:
    """Resolves extraction candidates to canonical graph nodes."""

    def __init__(self, graph_store, settings: Settings | None = None) -> None:
        self.graph = graph_store
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ API
    def resolve(
        self,
        doc: NormalizedDocument,
        entities: Sequence[ExtractionCandidate],
        relations: Sequence[RelationCandidate],
        *,
        case_number: str = "",
        jurisdiction_id: str = "",
    ) -> ResolutionOutcome:
        aggregated = self._aggregate(entities)
        key_map: dict[tuple[str, str], str] = {}
        nodes: list[GraphNode] = []
        stats = {
            "candidates": len(entities),
            "deduplicated": len(aggregated),
            "hard_matches": 0,
            "fuzzy_proposals": 0,
        }

        # Tier 1 + canonical resolution.
        for candidate in aggregated:
            resolved = self._resolve_key(key_map, candidate, doc, stats)
            node = self._build_node(candidate, resolved, doc)
            nodes.append(node)

        # Tier 2: fuzzy person matching (only for genuinely new person nodes).
        alias_proposals: list[dict[str, Any]] = []
        for candidate in aggregated:
            if candidate.entity_type is not EntityType.PERSON:
                continue
            own_key = key_map[(candidate.entity_type.value, candidate.normalized_value)]
            if self.graph.get_node(own_key) is not None:
                # Already in the graph: the pair was proposed on a previous run.
                continue
            alias_proposals.extend(
                self._propose_aliases(candidate, own_key, doc, stats)
            )

        edges = self._build_edges(relations, key_map, doc, stats)

        outcome = ResolutionOutcome(
            nodes=nodes, edges=edges, alias_proposals=alias_proposals, stats=stats
        )
        log.info("pipeline.er.done", doc_id=doc.doc_id, **outcome.as_dict())
        return outcome

    # ------------------------------------------------------------- internals
    def _aggregate(
        self, entities: Sequence[ExtractionCandidate]
    ) -> list[AggregatedCandidate]:
        """Collapse repeated mentions of the same entity within one document."""
        buckets: dict[tuple[str, str], AggregatedCandidate] = {}
        for entity in entities:
            if not entity.normalized_value:
                continue
            key = (entity.entity_type.value, entity.normalized_value)
            existing = buckets.get(key)
            if existing is None:
                buckets[key] = AggregatedCandidate(
                    entity_type=entity.entity_type,
                    normalized_value=entity.normalized_value,
                    display_value=entity.display_value,
                    attributes=dict(entity.attributes),
                    confidence=entity.confidence,
                    text_span=entity.text_span,
                    staging=entity.staging,
                    extractor=entity.extractor,
                    mentions=1,
                )
                continue
            existing.mentions += 1
            existing.confidence = max(existing.confidence, entity.confidence)
            existing.staging = existing.staging or entity.staging
            if len(entity.display_value) > len(existing.display_value):
                existing.display_value = entity.display_value
            if existing.text_span == (0, 0):
                existing.text_span = entity.text_span
            self._merge_attributes(existing.attributes, entity.attributes)
            if entity.extractor == "deterministic":
                existing.extractor = "deterministic"
        return list(buckets.values())

    @staticmethod
    def _merge_attributes(target: dict[str, Any], incoming: dict[str, Any]) -> None:
        for key, value in incoming.items():
            if value in (None, "", []):
                continue
            current = target.get(key)
            if isinstance(current, list) and isinstance(value, list):
                for item in value:
                    if item not in current:
                        current.append(item)
            elif current in (None, "", []):
                target[key] = value
            elif key == "confidence":
                target[key] = max(float(current or 0), float(value or 0))

    def _resolve_key(
        self,
        key_map: dict[tuple[str, str], str],
        candidate: AggregatedCandidate,
        doc: NormalizedDocument,
        stats: dict[str, Any],
    ) -> str:
        """Return the canonical provenance key for one aggregated candidate."""
        raw_key = candidate_key(
            doc.case_id, doc.doc_id, candidate.entity_type.value, candidate.normalized_value
        )

        if candidate.entity_type in HARD_IDENTIFIER_TYPES or candidate.entity_type in CANONICAL_VALUE_TYPES:
            match_value = candidate.normalized_value
            existing = self.graph.find_by_hard_identifier(
                candidate.entity_type.value, match_value, doc.case_id
            )
            if existing:
                # Tier 1: a physically unique identifier cannot be two things.
                key_map[(candidate.entity_type.value, candidate.normalized_value)] = existing
                stats["hard_matches"] += 1
                return existing

        existing_same_key = self.graph.get_node(raw_key)
        if candidate.entity_type in HARD_IDENTIFIER_TYPES and existing_same_key is None:
            # Fall back to a case-wide lookup: the same number in another
            # document must still collapse to one node.
            existing = self.graph.find_by_hard_identifier(
                candidate.entity_type.value, candidate.normalized_value, None
            )
            if existing:
                key_map[(candidate.entity_type.value, candidate.normalized_value)] = existing
                stats["hard_matches"] += 1
                return existing

        key_map[(candidate.entity_type.value, candidate.normalized_value)] = raw_key
        return raw_key

    def _build_node(
        self, candidate: AggregatedCandidate, key: str, doc: NormalizedDocument
    ) -> GraphNode:
        properties: dict[str, Any] = {
            "confidence": round(float(candidate.confidence), 3),
            "source_doc_id": doc.doc_id,
            "source_doc_ids": [doc.doc_id],
            "case_id": doc.case_id,
            "case_ids": [doc.case_id],
            "candidate_keys": [
                candidate_key(
                    doc.case_id, doc.doc_id, candidate.entity_type.value, candidate.normalized_value
                )
            ],
            "source_types": [doc.doc_type.lower()],
            "language": doc.language,
            "text_span": list(candidate.text_span),
            "extractor": candidate.extractor,
            "mentions": candidate.mentions,
            "staging": bool(candidate.staging),
        }
        properties.update(candidate.attributes)

        if candidate.entity_type is EntityType.PERSON:
            properties.setdefault("name", candidate.display_value)
            properties["aliases"] = list(candidate.attributes.get("aliases") or [])
        elif candidate.entity_type is EntityType.PHONE:
            properties["number"] = candidate.normalized_value
            properties.setdefault("provider", _guess_provider(candidate.normalized_value))
        elif candidate.entity_type is EntityType.VEHICLE:
            properties["plate"] = candidate.normalized_value
        elif candidate.entity_type is EntityType.BANK_ACCOUNT:
            if candidate.normalized_value.isalpha() or not candidate.normalized_value.isdigit():
                properties["ifsc"] = candidate.normalized_value
            else:
                properties["number"] = candidate.normalized_value
        elif candidate.entity_type is EntityType.LOCATION:
            properties.setdefault("address", candidate.display_value)
            properties.setdefault("location_type", "MENTIONED")
        elif candidate.entity_type is EntityType.ORGANIZATION:
            properties.setdefault("name", candidate.display_value)
        elif candidate.entity_type is EntityType.EVENT:
            properties.setdefault("description", candidate.display_value)
            properties.setdefault("event_type", "MENTIONED")
        properties["is_active"] = True
        return GraphNode(provenance_key=key, label=candidate.entity_type.value, properties=properties)

    def _propose_aliases(
        self,
        candidate: AggregatedCandidate,
        own_key: str,
        doc: NormalizedDocument,
        stats: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Compare a new person against every existing person in the case."""
        threshold = float(self.settings.er_fuzzy_threshold)
        proposals: list[dict[str, Any]] = []
        seen = 0
        for other in self.graph.candidate_persons(doc.case_id, exclude_key=own_key):
            if seen >= self.settings.er_max_pairs_per_document:
                break
            seen += 1
            other_name = str(other.properties.get("name") or "")
            if not other_name:
                continue
            if self.graph.has_tombstone(own_key, other.provenance_key):
                # A previously rejected pair is never re-proposed (PRD 6.2 #3).
                continue

            score = combined_similarity(candidate.display_value, other_name)
            basis = MatchBasis.NAME_FUZZY
            aliases = other.properties.get("aliases") or []
            for alias in aliases:
                alias_score = combined_similarity(candidate.display_value, str(alias))
                if alias_score > score:
                    score, basis = alias_score, MatchBasis.ALIAS_CO_MENTION
            if candidate.attributes.get("aliases"):
                for alias in candidate.attributes["aliases"]:
                    alias_score = combined_similarity(str(alias), other_name)
                    if alias_score > score:
                        score, basis = alias_score, MatchBasis.ALIAS_CO_MENTION
            if score < threshold:
                continue

            proposals.append(
                {
                    "source_node_key": own_key,
                    "target_node_key": other.provenance_key,
                    "similarity_score": round(float(score), 4),
                    "match_basis": basis.value,
                    "evidence_doc_ids": sorted(
                        {
                            doc.doc_id,
                            *[d for d in (other.properties.get("source_doc_ids") or [])],
                        }
                    ),
                }
            )
        # Queue hygiene: one proposal per new person, the best one.
        #
        # Seven documents that all mention "Suresh Mehta" produce seven nodes
        # (the provenance key is per document by definition), and comparing each
        # against every existing person would put dozens of near-identical
        # decisions in front of the reviewer.  A queue nobody can work through
        # is a queue that gets rubber-stamped, which is the failure mode G2
        # exists to prevent.  Merges are transitive, so the single best proposal
        # per new mention still lets the cluster form — one decision at a time.
        if len(proposals) > 1:
            proposals.sort(key=lambda p: (-float(p["similarity_score"]), p["target_node_key"]))
            proposals = proposals[:1]
        stats["fuzzy_proposals"] += len(proposals)
        return proposals

    def _build_edges(
        self,
        relations: Sequence[RelationCandidate],
        key_map: dict[tuple[str, str], str],
        doc: NormalizedDocument,
        stats: dict[str, Any],
    ) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        rejected = 0
        seen: set[str] = set()
        for relation in relations:
            source = self._lookup(key_map, relation.source_type, relation.source_value)
            target = self._lookup(key_map, relation.target_type, relation.target_value)
            if source is None or target is None or source == target:
                rejected += 1
                continue
            properties: dict[str, Any] = {
                "source_doc_id": doc.doc_id,
                "source_doc_ids": [doc.doc_id],
                "confidence": round(float(relation.confidence), 3),
                "text_span": list(relation.text_span),
                "extractor": relation.extractor,
                "staging": bool(relation.staging),
            }
            properties.update(
                {k: v for k, v in relation.attributes.items() if v not in (None, "", [])}
            )
            key = f"{relation.rel_type}|{source}|{target}|{relation.discriminator}"
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                GraphEdge(
                    source_key=source,
                    target_key=target,
                    rel_type=relation.rel_type,
                    properties=properties,
                    # Aggregated edge types (CALLED) carry an empty discriminator
                    # so every call between the same pair collapses into one edge.
                    discriminator=relation.discriminator,
                )
            )
        stats["relations_rejected"] = rejected
        return edges

    @staticmethod
    def _lookup(
        key_map: dict[tuple[str, str], str], entity_type: EntityType, value: str
    ) -> str | None:
        if not value:
            return None
        direct = key_map.get((entity_type.value, value))
        if direct:
            return direct
        # Tolerate a normalisation difference between the entity and the
        # relation stage (e.g. a raw name vs its normalised form).
        normalized = normalize_name(value) if entity_type is EntityType.PERSON else value
        return key_map.get((entity_type.value, normalized))


def _guess_provider(number: str) -> str:
    """Coarse Indian mobile-series hint; informational only, never evidential."""
    digits = re.sub(r"\D", "", number)[-10:]
    if not digits:
        return "UNKNOWN"
    prefix = digits[:2]
    mapping = {
        "98": "UNKNOWN", "99": "UNKNOWN", "91": "UNKNOWN", "90": "UNKNOWN",
        "70": "UNKNOWN", "80": "UNKNOWN", "81": "UNKNOWN", "82": "UNKNOWN",
    }
    return mapping.get(prefix, "UNKNOWN")
