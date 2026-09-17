"""Person → Person relationship derivation (the investigator-facing network).

The investigator's question is *who is connected to whom*.  A phone number, a
bank account, a vehicle, an address or an organisation is **support** for an
answer to that question — it is never the answer itself.  This module walks
those supporting entities internally and collapses them into a single,
aggregated person-to-person edge that carries its evidence with it:

    Rajesh Kumar ──[Communication · 4 supporting items]── Amit Sharma
                   ├─ Phone  +91 98765 43210   (USES_PHONE + CALLED)
                   ├─ Phone  +91 90040 11122   (USES_PHONE + CALLED)
                   ├─ Account  a/c 4417        (TRANSFER_TO)
                   └─ Vehicle  MH02AB1234      (OWNS_VEHICLE)

Nothing here invents a relationship.  An edge exists only when the graph
already contains a record that supports it — either a first-class
person-to-person relation (``ASSOCIATE_OF``, ``RELATIVE_OF``,
``NAMED_ACCOMPLICE_OF``, ``ARRESTED_WITH`` …) or a shared/bridged supporting
entity that both people are independently attached to.  If no such record
exists, the pair simply has no edge and the caller reports an empty network
rather than manufacturing one.

The full entity graph is untouched: :mod:`app.services.graph_service` still
serves it verbatim through ``master_graph`` for the ENTITY NETWORK view.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from app.domain.enums import canonical_label, is_document_artifact_node

__all__ = [
    "PERSON_RELATIONSHIP_LABELS",
    "PERSON_RELATIONSHIP_PRIORITY",
    "MAX_SUPPORTING_ITEMS",
    "DEFAULT_SHARED_ENTITY_FANOUT",
    "derive_person_relationships",
    "relationship_id",
]

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: First-class person-to-person relations already present in the graph.
#: ``rel_type -> (relationship_type, human label)``.
DIRECT_PERSON_RELATIONSHIPS: dict[str, tuple[str, str]] = {
    "NAMED_ACCOMPLICE_OF": ("NAMED_ACCOMPLICE", "Named accomplice"),
    "ARRESTED_WITH": ("ARRESTED_WITH", "Arrested together"),
    "RELATIVE_OF": ("FAMILY_RELATIVE", "Family / relative"),
    "ASSOCIATE_OF": ("KNOWN_ASSOCIATION", "Known association"),
    "LINKED_ON_SOCIAL": ("SOCIAL_LINK", "Social media link"),
    "SHARED_IDENTIFIER": ("SHARED_IDENTIFIER", "Shared identifier"),
    "ACCUSED_IN": ("EVIDENCE_SUPPORTED", "Evidence-supported association"),
}

#: Person → supporting-entity relations used to establish a *shared* attribute.
#: ``rel_type -> (relationship_type, human label, supporting kind)``.
SHARED_ATTRIBUTE_RELATIONSHIPS: dict[str, tuple[str, str, str]] = {
    "USES_PHONE": ("SHARED_PHONE", "Shared phone number", "PHONE"),
    "SHARED_PHONE": ("SHARED_PHONE", "Shared phone number", "PHONE"),
    "OWNS_ACCOUNT": ("SHARED_ACCOUNT", "Shared bank account", "BANK_ACCOUNT"),
    "CONTROLS_ACCOUNT": ("SHARED_ACCOUNT", "Shared bank account", "BANK_ACCOUNT"),
    "SHARED_ACCOUNT": ("SHARED_ACCOUNT", "Shared bank account", "BANK_ACCOUNT"),
    "OWNS_VEHICLE": ("SHARED_VEHICLE", "Shared vehicle", "VEHICLE"),
    "SHARED_VEHICLE": ("SHARED_VEHICLE", "Shared vehicle", "VEHICLE"),
    "LOCATED_AT": ("SHARED_ADDRESS", "Shared address", "LOCATION"),
    "SHARED_LOCATION": ("SHARED_ADDRESS", "Shared address", "LOCATION"),
    "MEMBER_OF": ("SHARED_ORGANIZATION", "Shared organization", "ORGANIZATION"),
}

#: Entity → entity relations bridged to people through their ownership edges.
#: ``rel_type -> (relationship_type, human label, supporting kind)``.
BRIDGED_RELATIONSHIPS: dict[str, tuple[str, str, str]] = {
    "CALLED": ("COMMUNICATION", "Communication", "COMMUNICATION"),
    "TRANSFER_TO": ("FINANCIAL_LINK", "Financial link", "TRANSACTION"),
}

#: Display label + relative weight for every relationship type this module can
#: emit.  Weight decides which type becomes the *primary* label on an edge that
#: several kinds of evidence support: "Communication" beats the generic
#: "Known association", which beats nothing at all.
PERSON_RELATIONSHIP_LABELS: dict[str, tuple[str, int]] = {
    "NAMED_ACCOMPLICE": ("Named accomplice", 100),
    "ARRESTED_WITH": ("Arrested together", 95),
    "COMMUNICATION": ("Communication", 90),
    "FINANCIAL_LINK": ("Financial link", 85),
    "FAMILY_RELATIVE": ("Family / relative", 80),
    "SHARED_VEHICLE": ("Shared vehicle", 70),
    "SHARED_ACCOUNT": ("Shared bank account", 65),
    "SHARED_PHONE": ("Shared phone", 60),
    "SHARED_ADDRESS": ("Shared address", 55),
    "SHARED_ORGANIZATION": ("Shared organization", 45),
    "SHARED_IDENTIFIER": ("Shared identifier", 40),
    "SOCIAL_LINK": ("Social link", 25),
    "KNOWN_ASSOCIATION": ("Known association", 20),
    "EVIDENCE_SUPPORTED": ("Evidence-supported association", 10),
}

#: Deterministic ordering used to pick the primary relationship type.
PERSON_RELATIONSHIP_PRIORITY: list[str] = sorted(
    PERSON_RELATIONSHIP_LABELS, key=lambda rt: -PERSON_RELATIONSHIP_LABELS[rt][1]
)

#: How many supporting items travel on the wire per edge before the payload
#: starts to summarise instead of listing.  The count is always exact.
MAX_SUPPORTING_ITEMS = 25

#: An attribute held by more people than this is a *category*, not a
#: relationship (a police station address, a call-centre SIM block).  Deriving
#: pairwise edges from it would bury the real network under thousands of
#: meaningless links, so those entities are reported as suppressed instead.
DEFAULT_SHARED_ENTITY_FANOUT = 12


def relationship_id(source_key: str, target_key: str) -> str:
    """Stable id for the aggregated edge between two people (order-free)."""
    a, b = sorted([source_key, target_key])
    return f"p2p:{a}|{b}"


def _label_for(relationship_type: str) -> str:
    return PERSON_RELATIONSHIP_LABELS.get(relationship_type, (relationship_type,))[0]


def _docs(props: dict[str, Any]) -> list[str]:
    docs: list[str] = []
    for value in (props.get("source_doc_ids") or []):
        if value and value not in docs:
            docs.append(str(value))
    single = props.get("source_doc_id")
    if single and single not in docs:
        docs.append(str(single))
    return docs


def _cases(props: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for value in (props.get("case_ids") or []):
        if value and value not in out:
            out.append(str(value))
    single = props.get("case_id")
    if single and single not in out:
        out.append(str(single))
    return out


def _pointer(props: dict[str, Any]) -> dict[str, Any] | None:
    origin = props.get("origin") or {}
    doc = props.get("source_doc_id") or next(iter(_docs(props)), None)
    if not doc and not origin:
        return None
    return {
        "source_doc_id": doc,
        "text_span": props.get("text_span"),
        "origin": origin or None,
    }


def _display(node: Any) -> str:
    props = node.properties or {}
    for key in ("display_name", "full_name", "name", "number", "account_number",
                "registration", "plate", "address", "org_name"):
        value = props.get(key)
        if value and str(value).strip():
            return str(value).strip()
    return str(node.provenance_key)


def _strength(item_count: int, type_count: int) -> str:
    if item_count >= 4 or type_count >= 3:
        return "STRONG"
    if item_count >= 2 or type_count >= 2:
        return "MODERATE"
    return "WEAK"


def derive_person_relationships(
    snapshot: Any,
    *,
    pair_filter: Iterable[frozenset] | None = None,
    max_fanout: int = DEFAULT_SHARED_ENTITY_FANOUT,
) -> dict[str, Any]:
    """Collapse the entity graph into aggregated person-to-person edges.

    Args:
        snapshot: a ``CaseGraphSnapshot`` (single- or multi-case).
        pair_filter: when given, only these unordered person pairs are emitted
            (used by the edge-evidence drill-down so it stays O(E)).
        max_fanout: skip pairwise derivation for supporting entities attached to
            more people than this.

    Returns a dict with ``edges`` (aggregated, already ordered for display),
    ``persons`` (keys with an incident relationship), ``stats`` and
    ``suppressed`` diagnostics.
    """
    nodes: dict[str, Any] = snapshot.nodes
    wanted_pairs: set[frozenset] | None = (
        {frozenset(p) for p in pair_filter} if pair_filter is not None else None
    )

    person_keys: set[str] = set()
    for key, node in nodes.items():
        if is_document_artifact_node(node):
            continue
        if canonical_label(node.label) == "PERSON":
            person_keys.add(key)

    # entity_key -> people attached to it (used to bridge CALLED / TRANSFER_TO)
    entity_owners: dict[str, set[str]] = defaultdict(set)
    # (relationship_type, entity_key) -> {people, sample props}
    shared: dict[tuple[str, str], dict[str, Any]] = {}
    direct: list[tuple[str, str, Any]] = []
    bridges: list[tuple[str, str, Any]] = []
    suppressed: dict[str, int] = defaultdict(int)

    for edge in snapshot.edges:
        src, tgt = edge.source_key, edge.target_key
        rel = str(edge.rel_type or "").upper()
        src_is_person = src in person_keys
        tgt_is_person = tgt in person_keys

        if src_is_person and tgt_is_person:
            if rel in DIRECT_PERSON_RELATIONSHIPS:
                direct.append((src, tgt, edge))
            continue

        if src_is_person != tgt_is_person:
            person_key = src if src_is_person else tgt
            entity_key = tgt if src_is_person else src
            entity_owners[entity_key].add(person_key)
            mapping = SHARED_ATTRIBUTE_RELATIONSHIPS.get(rel)
            if not mapping:
                continue
            relationship_type, _label, kind = mapping
            bucket = shared.setdefault(
                (relationship_type, entity_key),
                {"people": set(), "kind": kind, "props": edge.properties or {},
                 "rel_types": set(), "label": _label},
            )
            bucket["people"].add(person_key)
            bucket["rel_types"].add(rel)
            continue

        if not src_is_person and not tgt_is_person and rel in BRIDGED_RELATIONSHIPS:
            bridges.append((src, tgt, edge))

    # ---- aggregate into one record per unordered person pair -----------------
    pairs: dict[frozenset, dict[str, Any]] = {}

    def _bucket(a: str, b: str) -> dict[str, Any] | None:
        if a == b:
            return None
        pair = frozenset((a, b))
        if wanted_pairs is not None and pair not in wanted_pairs:
            return None
        return pairs.setdefault(
            pair,
            {
                "source": min(a, b),
                "target": max(a, b),
                "types": {},
                "items": [],
                "docs": [],
                "cases": [],
                "rel_types": set(),
            },
        )

    def _add_item(bucket: dict[str, Any], relationship_type: str, item: dict[str, Any]) -> None:
        bucket["types"][relationship_type] = bucket["types"].get(relationship_type, 0) + 1
        bucket["items"].append(item)
        bucket["rel_types"].update(item.get("rel_types") or [])
        for doc in item.get("source_doc_ids") or []:
            if doc not in bucket["docs"]:
                bucket["docs"].append(doc)
        for case in item.get("case_ids") or []:
            if case not in bucket["cases"]:
                bucket["cases"].append(case)

    # 1. First-class person-to-person records.
    for src, tgt, edge in direct:
        bucket = _bucket(src, tgt)
        if bucket is None:
            continue
        props = edge.properties or {}
        relationship_type, label = DIRECT_PERSON_RELATIONSHIPS[str(edge.rel_type).upper()]
        _add_item(
            bucket,
            relationship_type,
            {
                "kind": "DIRECT_RECORD",
                "relationship_type": relationship_type,
                "label": label,
                "ref": getattr(edge, "key", "") or f"{src}|{edge.rel_type}|{tgt}",
                "detail": f"Recorded {str(edge.rel_type).upper()} relationship",
                "rel_types": [str(edge.rel_type).upper()],
                "source_doc_ids": _docs(props),
                "case_ids": _cases(props),
                "confidence": float(props.get("confidence", 1.0) or 1.0),
                "evidence": _pointer(props),
                "properties": {
                    k: v for k, v in props.items()
                    if k in {"call_count", "first_ts", "last_ts", "amount", "ts"}
                },
            },
        )

    # 2. Shared supporting attribute (same phone / account / vehicle / address / org).
    for (relationship_type, entity_key), bucket in shared.items():
        people = sorted(bucket["people"])
        if len(people) < 2:
            continue
        if len(people) > max_fanout:
            suppressed[relationship_type] += 1
            continue
        entity = nodes.get(entity_key)
        if entity is None:
            continue
        props = bucket["props"]
        for i in range(len(people)):
            for j in range(i + 1, len(people)):
                target = _bucket(people[i], people[j])
                if target is None:
                    continue
                _add_item(
                    target,
                    relationship_type,
                    {
                        "kind": bucket["kind"],
                        "relationship_type": relationship_type,
                        "label": f"{bucket['label']}: {_display(entity)}",
                        "ref": entity_key,
                        "detail": _display(entity),
                        "entity_label": canonical_label(entity.label),
                        "rel_types": sorted(bucket["rel_types"]),
                        "source_doc_ids": _docs(props),
                        "case_ids": _cases(props),
                        "confidence": float(props.get("confidence", 1.0) or 1.0),
                        "evidence": _pointer(props),
                        "properties": {},
                    },
                )

    # 3. Bridged records: A → phone → CALLED → phone → B, A → account →
    #    TRANSFER_TO → account → B.  The intermediary never becomes a node.
    for src, tgt, edge in bridges:
        props = edge.properties or {}
        rel = str(edge.rel_type).upper()
        relationship_type, label, kind = BRIDGED_RELATIONSHIPS[rel]
        left = sorted(entity_owners.get(src, ()))
        right = sorted(entity_owners.get(tgt, ()))
        if not left or not right:
            continue
        if len(left) > max_fanout or len(right) > max_fanout:
            suppressed[relationship_type] += 1
            continue
        src_node, tgt_node = nodes.get(src), nodes.get(tgt)
        detail_bits = [label]
        if rel == "CALLED":
            if props.get("call_count"):
                detail_bits.append(f"{props['call_count']} call(s)")
            if props.get("first_ts"):
                detail_bits.append(f"first {props['first_ts']}")
        elif props.get("amount") is not None:
            detail_bits.append(f"amount {props['amount']}")
        item = {
            "kind": kind,
            "relationship_type": relationship_type,
            "label": (
                f"{label}: {_display(src_node) if src_node else src} → "
                f"{_display(tgt_node) if tgt_node else tgt}"
            ),
            "ref": getattr(edge, "key", "") or f"{src}|{rel}|{tgt}",
            "detail": " · ".join(str(b) for b in detail_bits),
            "via": [src, tgt],
            "via_labels": [
                canonical_label(src_node.label) if src_node else "UNKNOWN",
                canonical_label(tgt_node.label) if tgt_node else "UNKNOWN",
            ],
            "rel_types": [rel],
            "source_doc_ids": _docs(props),
            "case_ids": _cases(props),
            "confidence": float(props.get("confidence", 1.0) or 1.0),
            "evidence": _pointer(props),
            "properties": {
                k: v for k, v in props.items()
                if k in {"call_count", "first_ts", "last_ts", "amount", "ts"}
            },
        }
        for a in left:
            for b in right:
                bucket = _bucket(a, b)
                if bucket is None:
                    continue
                _add_item(bucket, relationship_type, dict(item))

    # ---- order + finalise ----------------------------------------------------
    edges_out: list[dict[str, Any]] = []
    person_stats: dict[str, dict[str, int]] = {}
    for pair, bucket in pairs.items():
        items: list[dict[str, Any]] = bucket["items"]
        # Collapse supporting records that state the same fact.  Two people
        # recorded as associates in three different cases is *one* relationship
        # fact with three case attributions — not three identical rows that
        # make the evidence list look padded.
        seen: dict[tuple, dict[str, Any]] = {}
        unique_items: list[dict[str, Any]] = []
        for item in items:
            if item.get("kind") == "DIRECT_RECORD":
                sig: tuple = ("DIRECT_RECORD", tuple(item.get("rel_types") or []))
                existing = seen.get(sig)
                if existing is None:
                    seen[sig] = item
                    unique_items.append(item)
                else:
                    for case in item.get("case_ids") or []:
                        if case not in existing["case_ids"]:
                            existing["case_ids"].append(case)
                    for doc in item.get("source_doc_ids") or []:
                        if doc not in existing["source_doc_ids"]:
                            existing["source_doc_ids"].append(doc)
                continue
            sig = (str(item.get("kind")), str(item.get("ref")))
            if sig in seen:
                continue
            seen[sig] = item
            unique_items.append(item)
        for item in unique_items:
            item["case_count"] = len(item.get("case_ids") or [])
            if item.get("kind") == "DIRECT_RECORD" and item["case_count"] > 1:
                rel = (item.get("rel_types") or ["RELATED"])[0]
                item["detail"] = f"Recorded {rel} relationship in {item['case_count']} cases"
        type_counts = dict(bucket["types"])
        ordered_types = [
            rt for rt in PERSON_RELATIONSHIP_PRIORITY if rt in type_counts
        ]
        primary = ordered_types[0] if ordered_types else "EVIDENCE_SUPPORTED"
        edges_out.append(
            {
                "id": relationship_id(bucket["source"], bucket["target"]),
                "source": bucket["source"],
                "target": bucket["target"],
                "relationship_type": primary,
                "label": _label_for(primary),
                "relationship_types": ordered_types,
                "relationship_type_counts": type_counts,
                "supporting_items": unique_items[:MAX_SUPPORTING_ITEMS],
                "supporting_item_count": len(unique_items),
                "supporting_kinds": sorted({str(i.get("kind")) for i in unique_items}),
                "rel_types": sorted(bucket["rel_types"]),
                "evidence_count": len(unique_items),
                "source_doc_ids": bucket["docs"],
                "case_ids": sorted(bucket["cases"]),
                "cross_case": len(set(bucket["cases"])) > 1,
                "strength": _strength(len(unique_items), len(ordered_types)),
                "confidence": (
                    round(sum(i.get("confidence", 1.0) for i in unique_items) / len(unique_items), 4)
                    if unique_items
                    else 0.0
                ),
            }
        )
        for key in (bucket["source"], bucket["target"]):
            stat = person_stats.setdefault(key, {"relationships": 0, "evidence": 0})
            stat["relationships"] += 1
            stat["evidence"] += len(unique_items)

    # Most-supported relationships first: that is what an investigator reads.
    edges_out.sort(
        key=lambda e: (-e["supporting_item_count"], -len(e["relationship_types"]), e["id"])
    )

    by_type: dict[str, int] = defaultdict(int)
    for edge in edges_out:
        by_type[edge["relationship_type"]] += 1

    return {
        "edges": edges_out,
        "persons": person_stats,
        "person_keys": person_keys,
        "counts": {
            "persons_total": len(person_keys),
            "persons_linked": len(person_stats),
            "relationships": len(edges_out),
            "by_relationship_type": dict(by_type),
            "supporting_items": sum(e["supporting_item_count"] for e in edges_out),
        },
        "suppressed_shared_entities": dict(suppressed),
    }
