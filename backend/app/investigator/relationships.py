"""Relationship discovery between resolved entities.

Seven kinds, from strongest to weakest reading:

* ``direct`` — records link the pair outright (FACT: the edge is data).
* ``repeated`` — the pair recurs across records (LEAD).
* ``temporal`` — chronologically valid paths connect them (LEAD).
* ``cross_case`` — the link spans disjoint case sets (LEAD).
* ``indirect`` — a short path (≤3 hops) through others (LEAD).
* ``suspicious`` — communication *and* financial ties coincide (LEAD).
* ``coincidental`` — a single low-confidence link, flagged as such.

Every finding carries observation / interpretation / assessment plus its
evidence. Deterministic caps keep answers bounded: 15 pairs, 3 hops,
2 paths per pair, 40 findings.
"""

from __future__ import annotations

from collections import deque
from itertools import combinations

from app.analytics.temporal import find_temporal_paths
from app.domain.enums import LOW_CONFIDENCE_REL_TYPES
from app.domain.models import CaseGraphSnapshot

from .evidence import doc_pointer, edge_pointer, make_evidence
from .labels import COINCIDENCE, FACT, LEAD
from .patterns import META_RELS, _benign_kind, _edge_docs
from .schemas import (
    EvidenceItem,
    ObservationBlock,
    RelationshipFinding,
    RelationshipPath,
    ResolvedEntity,
)

MAX_PAIRS = 15
MAX_HOPS = 3
MAX_PATHS = 2
MAX_FINDINGS = 40

_KIND_ORDER = {
    "direct": 0,
    "repeated": 1,
    "temporal": 2,
    "cross_case": 3,
    "suspicious": 4,
    "indirect": 5,
    "coincidental": 6,
}


def _names(snapshot: CaseGraphSnapshot) -> dict[str, str]:
    return {key: (node.name or key) for key, node in (snapshot.nodes or {}).items()}


def _pair_edges(snapshot: CaseGraphSnapshot, first: str, second: str) -> list:
    return [
        edge
        for edge in (snapshot.edges or [])
        if edge.rel_type not in META_RELS
        and {edge.source_key, edge.target_key} == {first, second}
    ]


def _adjacency(snapshot: CaseGraphSnapshot) -> dict[str, list[tuple[str, str]]]:
    adjacency: dict[str, list[tuple[str, str]]] = {}
    for edge in snapshot.edges or []:
        if edge.rel_type in META_RELS:
            continue
        adjacency.setdefault(edge.source_key, []).append((edge.target_key, edge.rel_type))
        adjacency.setdefault(edge.target_key, []).append((edge.source_key, edge.rel_type))
    for key in adjacency:
        adjacency[key].sort()
    return adjacency


def _bfs_paths(
    adjacency: dict[str, list[tuple[str, str]]],
    source: str,
    target: str,
    *,
    max_hops: int = MAX_HOPS,
    max_paths: int = MAX_PATHS,
) -> list[list[str]]:
    """Shortest simple paths up to ``max_hops`` (deterministic order)."""
    if source == target:
        return []
    paths: list[list[str]] = []
    queue: deque[list[str]] = deque([[source]])
    while queue and len(paths) < max_paths:
        route = queue.popleft()
        if len(route) - 1 >= max_hops:
            continue
        for neighbour, _rel in adjacency.get(route[-1], []):
            if neighbour in route:
                continue
            extended = [*route, neighbour]
            if neighbour == target:
                if len(extended) > 2:
                    paths.append(extended)
            elif len(extended) - 1 < max_hops:
                queue.append(extended)
    return paths


def _doc_provenance(doc_index: dict[str, dict], doc_id: str):
    info = doc_index.get(doc_id, {})
    origin = info.get("origin_file") or info.get("filename")
    return doc_pointer(
        doc_id=doc_id,
        label=str(info.get("filename") or info.get("origin_file") or doc_id),
        origin_file=str(origin) if origin else None,
        content_hash=info.get("content_hash"),
    )


def _evidence_for_edges(
    edges: list,
    doc_index: dict[str, dict],
    summary: str,
    *,
    label: str = FACT,
    stance: str = "supports",
) -> list[EvidenceItem]:
    evidence = [
        make_evidence(
            "relationship",
            summary,
            label=label,
            stance=stance,
            provenance=[
                edge_pointer(
                    edge_key=str(getattr(edge, "key", "") or f"{edge.source_key}->{edge.target_key}"),
                    label=f"{edge.rel_type} edge",
                )
                for edge in edges[:5]
            ],
        )
    ]
    for doc_id in sorted(_edge_docs(edges)):
        evidence.append(
            make_evidence(
                "document",
                f"Recorded in {doc_id}.",
                label=label,
                stance=stance,
                provenance=[_doc_provenance(doc_index, doc_id)],
            )
        )
    return evidence


def _analysis(observation: str, interpretation: str, assessment: str) -> ObservationBlock:
    return ObservationBlock(
        observation=observation, interpretation=interpretation, assessment=assessment
    )


def discover_relationships(
    snapshot: CaseGraphSnapshot,
    resolved: list[ResolvedEntity],
    *,
    doc_index: dict[str, dict] | None = None,
    max_pairs: int = MAX_PAIRS,
    limit: int = MAX_FINDINGS,
) -> list[RelationshipFinding]:
    """Discover every relationship kind across the resolved entity pairs."""
    doc_index = doc_index or {}
    keys = sorted({entity.canonical_id for entity in resolved if entity.resolved})
    names = _names(snapshot)
    adjacency = _adjacency(snapshot)
    findings: list[RelationshipFinding] = []

    pairs = list(combinations(keys, 2))[: max(0, max_pairs)]
    for first, second in pairs:
        first_name, second_name = names.get(first, first), names.get(second, second)
        edges = _pair_edges(snapshot, first, second)
        docs = _edge_docs(edges)
        rels = {edge.rel_type for edge in edges}

        if edges:
            findings.append(
                RelationshipFinding(
                    kind="direct",
                    entities=[first_name, second_name],
                    title=f"{first_name} & {second_name}: directly linked ({', '.join(sorted(rels))})",
                    description=(
                        f"{len(edges)} direct record(s) across {len(docs)} document(s): "
                        f"{', '.join(sorted(rels))}."
                    ),
                    evidence=_evidence_for_edges(
                        edges, doc_index, f"{first_name} is directly linked to {second_name}."
                    ),
                    inference_label=FACT,
                    analysis=_analysis(
                        f"{len(edges)} record(s) join the pair: {', '.join(sorted(rels))}.",
                        "A direct record means documented contact, not proven coordination.",
                        f"Link established as fact; meaning still open (confidence high on existence, low on intent).",
                    ),
                )
            )
            if len(docs) >= 2:
                findings.append(
                    RelationshipFinding(
                        kind="repeated",
                        entities=[first_name, second_name],
                        title=f"{first_name} & {second_name}: recur across {len(docs)} documents",
                        description=f"The pair co-occurs in {len(docs)} distinct documents.",
                        evidence=_evidence_for_edges(
                            edges, doc_index, f"Repeated co-occurrence in {len(docs)} documents."
                        ),
                        inference_label=LEAD,
                        analysis=_analysis(
                            f"Co-occurrence spans {len(docs)} documents.",
                            "Repetition across sources is the start of corroboration.",
                            "Worth following; check the documents share no single origin.",
                        ),
                    )
                )
            if "CALLED" in rels and "TRANSFER_TO" in rels:
                findings.append(
                    RelationshipFinding(
                        kind="suspicious",
                        entities=[first_name, second_name],
                        title=f"{first_name} & {second_name}: calls coincide with transfers",
                        description="Communication and financial edges coincide on one pair.",
                        evidence=_evidence_for_edges(
                            edges, doc_index, "Calls and transfers coincide on this pair."
                        ),
                        inference_label=LEAD,
                        analysis=_analysis(
                            "Both CALLED and TRANSFER_TO edges join the pair.",
                            "Coinciding contact and money movement is the classic shape worth a look.",
                            "Suspicious as a shape; innocent pairs (family support, business) share it.",
                        ),
                    )
                )
        else:
            for path in _bfs_paths(adjacency, first, second):
                via = [names.get(key, key) for key in path[1:-1]]
                findings.append(
                    RelationshipFinding(
                        kind="indirect",
                        entities=[first_name, second_name],
                        title=f"{first_name} & {second_name}: linked via {', '.join(via)}",
                        description=f"No direct record; shortest route runs through {', '.join(via)}.",
                        evidence=[
                            make_evidence(
                                "relationship",
                                f"Path: {' → '.join(names.get(key, key) for key in path)}.",
                                label=LEAD,
                            )
                        ],
                        inference_label=LEAD,
                        path=RelationshipPath(
                            nodes=[names.get(key, key) for key in path],
                            description=f"{len(path) - 1}-hop route",
                        ),
                        analysis=_analysis(
                            f"Connected through {', '.join(via)} ({len(path) - 1} hops).",
                            "Short paths suggest a shared circle, not a shared purpose.",
                            "Weak lead: useful for direction, not for conclusions.",
                        ),
                    )
                )
                break  # one indirect reading per pair is enough; paths capped globally

        try:
            temporal = find_temporal_paths(snapshot, first, second, max_depth=4, limit=5)
        except Exception:
            temporal = []
        if temporal:
            best = temporal[0]
            nodes = [names.get(str(key), str(key)) for key in best.get("nodes", best.get("path", []))]
            findings.append(
                RelationshipFinding(
                    kind="temporal",
                    entities=[first_name, second_name],
                    title=f"{first_name} & {second_name}: chronologically linked",
                    description=f"{len(temporal)} chronologically valid path(s) connect the pair.",
                    evidence=[
                        make_evidence(
                            "relationship",
                            f"Chronological path: {' → '.join(nodes) if nodes else 'present'}.",
                            label=LEAD,
                        )
                    ],
                    inference_label=LEAD,
                    path=RelationshipPath(
                        nodes=nodes, description="chronologically valid route"
                    ),
                    analysis=_analysis(
                        "Events order cleanly along the route (no time travel).",
                        "Ordered contact fits coordination — and fits ordinary sequences too.",
                        "Supports sequencing work; not standalone proof of anything.",
                    ),
                )
            )

        first_cases = set(((snapshot.nodes or {}).get(first).properties or {}).get("case_ids", []) or [])
        second_cases = set(((snapshot.nodes or {}).get(second).properties or {}).get("case_ids", []) or [])
        if first_cases and second_cases and first_cases.isdisjoint(second_cases):
            findings.append(
                RelationshipFinding(
                    kind="cross_case",
                    entities=[first_name, second_name],
                    title=f"{first_name} & {second_name}: link spans cases",
                    description="The pair's records sit in disjoint case sets.",
                    evidence=_evidence_for_edges(
                        edges, doc_index, "Endpoints attributed to disjoint cases."
                    )
                    if edges
                    else [make_evidence("record", "Endpoints attributed to disjoint cases.", label=LEAD)],
                    inference_label=LEAD,
                    analysis=_analysis(
                        "Case attribution on the two endpoints does not overlap.",
                        "Spanning links are how separate cases share a network.",
                        "Check attribution completeness before reading further.",
                    ),
                )
            )

        if len(edges) == 1 and rels <= set(LOW_CONFIDENCE_REL_TYPES):
            findings.append(
                RelationshipFinding(
                    kind="coincidental",
                    entities=[first_name, second_name],
                    title=f"{first_name} & {second_name}: single low-confidence link",
                    description="One low-confidence social edge is the whole story.",
                    evidence=_evidence_for_edges(
                        edges, doc_index, "Single low-confidence social link.", stance="context"
                    ),
                    inference_label=COINCIDENCE,
                    analysis=_analysis(
                        "Exactly one record joins the pair, and it is low-confidence.",
                        "Online adjacency without more is coincidence until shown otherwise.",
                        "Do not pursue without corroboration.",
                    ),
                )
            )
        benign = _benign_kind(edges) if edges else None
        if benign and len(edges) <= 2 and len(docs) <= 1:
            findings.append(
                RelationshipFinding(
                    kind="coincidental",
                    entities=[first_name, second_name],
                    title=f"{first_name} & {second_name}: {benign}-only contact",
                    description=f"The only records are {benign}-only.",
                    evidence=_evidence_for_edges(
                        edges, doc_index, f"{benign.capitalize()}-only contact.", stance="context"
                    ),
                    inference_label=COINCIDENCE,
                    analysis=_analysis(
                        f"All records are {benign}-only.",
                        f"{benign.capitalize()} contact explains itself.",
                        "Set aside unless fresh evidence says otherwise.",
                    ),
                )
            )

    findings.sort(
        key=lambda item: (_KIND_ORDER.get(item.kind, 9), item.entities, item.title)
    )
    return findings[: max(0, limit)]
