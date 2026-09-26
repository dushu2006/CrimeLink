"""Projection of the canonical dataset layer into the graph store.

The graph is *derived*, never authored.  Everything it contains comes from
``dataset_entities`` / ``dataset_relationships`` for one dataset, and every
node and edge is stamped with ``dataset_id``, ``canonical_id`` and
``entity_type``.  That stamp is what makes a rebuild surgical: purge by
``dataset_id``, re-project, done -- no orphans, no two datasets sharing a
canvas.

Two vocabularies meet here and must be reconciled deliberately:

* the **canonical** layer, which is rich (ACCOUNT, ADDRESS, DEVICE, EMAIL,
  PROPERTY, EVIDENCE, ...) because real datasets are rich;
* the **graph** layer, whose ``EntityType`` / ``REL_TYPES`` vocabulary is
  closed and validated at construction time.

:data:`app.datasets.schema_map.GRAPH_LABELS` and
:data:`app.datasets.normalize.GRAPH_REL_TYPES` do that mapping, and anything
unmapped degrades to a safe default rather than raising -- an unfamiliar
dataset must not be able to crash a graph build.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import Container, get_container
from app.datasets import normalize as nz
from app.datasets import schema_map as sm
from app.db.models import Case, Dataset, DatasetEntity, DatasetRelationship
from app.domain.enums import REL_TYPES, EntityType
from app.domain.models import GraphEdge, GraphNode
from app.logging import get_logger

log = get_logger("crimelink.datasets.graph_build")

BATCH = 1000

#: Graph labels that legitimately exist. Anything else is a programming error.
_VALID_LABELS = {e.value for e in EntityType} | {"Case"}


HARD_GRAPH_ID_TYPES = {
    sm.PHONE,
    sm.VEHICLE,
    sm.BANK_ACCOUNT,
}


def node_key(dataset_id: str, canonical_id: str) -> str:
    """Provenance key of a projected node for non-identifier entities."""
    return f"ds:{dataset_id}:{canonical_id}"


def entity_graph_key(dataset_id: str, entity: DatasetEntity) -> str:
    """Return the graph identity for a canonical entity.

    Hard identifiers are physically unique and therefore must collapse to one
    graph node across source files. The canonical PostgreSQL layer may contain
    multiple rows for the same identifier because each row retains its own
    provenance. The graph is the resolved view, so Phone/Vehicle/BankAccount
    identity is based on the normalized identifier within the dataset.
    """
    if entity.entity_type in HARD_GRAPH_ID_TYPES and entity.normalized_value:
        return f"ds:{dataset_id}:{entity.entity_type}:{entity.normalized_value}"
    return node_key(dataset_id, entity.canonical_id)


def _graph_label(entity_type: str) -> str | None:
    """Return an explicit graph label or ``None`` for provenance artifacts.

    Unknown types are rejected rather than guessed as ``Person``.  The old
    fallback was the direct path by which filenames, README records and parser
    artifacts entered actor analytics.
    """
    if not sm.is_graph_eligible(entity_type):
        return None
    label = sm.GRAPH_LABELS.get(entity_type)
    return label if label in _VALID_LABELS else None


def _graph_rel(rel_type: str) -> str | None:
    # Unknown source vocabulary is a data-quality review item, not a licence to
    # invent a person-to-person association.
    mapped = nz.GRAPH_REL_TYPES.get(rel_type)
    return mapped if mapped in REL_TYPES else None


def _name_property(label: str, name: str) -> dict[str, Any]:
    """Put the display value where ``GraphNode.name`` will find it.

    ``GraphNode.name`` reads name/number/plate/address/description/title in
    that order, so a Phone must carry ``number`` and a Vehicle ``plate`` or the
    UI shows a bare key.
    """
    props: dict[str, Any] = {"name": name}
    if label == EntityType.PHONE.value:
        props["number"] = name
    elif label == EntityType.VEHICLE.value:
        props["plate"] = name
    elif label == EntityType.LOCATION.value:
        props["address"] = name
    elif label == EntityType.BANK_ACCOUNT.value:
        props["account_number"] = name
    return props


#: Attribute values worth folding into the free-text search haystack are short
#: ones -- a bank name, a vehicle model, a city, a role. Long values are
#: document text, which the document search covers, and putting them here would
#: make every node match everything.
_SEARCH_VALUE_MAX = 80
_SEARCH_TEXT_MAX = 600
_SEARCH_SKIP_ATTRS = {"case_ids", "dataset_id", "canonical_id", "origin", "confidence"}


def _search_text(display: str, entity: DatasetEntity) -> str:
    """A single haystack per node, so search finds a record by any of its values.

    Investigators search for what they know: a bank's name, "Maruti Baleno", a
    locality. Those live in the row's other columns, not in the display name,
    and which columns exist differs per dataset -- so the projection flattens
    them here rather than the search code guessing field names.
    """
    parts: list[str] = [display or "", entity.normalized_value or "", entity.canonical_id]
    for key, value in (entity.attributes or {}).items():
        if key in _SEARCH_SKIP_ATTRS or value in (None, "", [], {}):
            continue
        if isinstance(value, (list, tuple)):
            candidates = [str(v) for v in value]
        elif isinstance(value, (str, int, float)):
            candidates = [str(value)]
        else:
            continue
        for text in candidates:
            if text and len(text) <= _SEARCH_VALUE_MAX:
                parts.append(text)
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        cleaned = part.strip()
        if not cleaned or cleaned.lower() in seen:
            continue
        seen.add(cleaned.lower())
        unique.append(cleaned)
    return " ".join(unique)[:_SEARCH_TEXT_MAX]


def build_node(
    dataset_id: str,
    entity: DatasetEntity,
    case_ids: Sequence[str] = (),
    *,
    fallback_doc_id: str = "",
) -> GraphNode:
    label = _graph_label(entity.entity_type)
    if label is None:
        raise ValueError(f"Entity type {entity.entity_type!r} is provenance-only and cannot enter the graph")
    display_name = getattr(entity, "display_name", "") or ""
    display = display_name or entity.name or entity.normalized_value or entity.canonical_id
    provenance = entity.provenance or {}
    properties: dict[str, Any] = {
        **_name_property(label, display),
        "dataset_id": dataset_id,
        "canonical_id": entity.canonical_id,
        "display_name": display_name or entity.name,
        "entity_type": entity.entity_type,
        "normalized_value": entity.normalized_value,
        "is_document_artifact": False,
        "confidence": float((entity.attributes or {}).get("confidence", 1.0) or 1.0),
        "resolution_confidence": (entity.attributes or {}).get("resolution_confidence"),
        "resolution_features": (entity.attributes or {}).get("resolution_features", []),
        "contradictions": (entity.attributes or {}).get("contradictions", []),
        "is_active": True,
        "case_ids": list(case_ids),
        # G1: every node names the document it came from.  For a row inside a
        # spreadsheet that document *is* the dataset file, identified by its
        # manifest id, and ``origin`` pins the exact row inside it.
        "source_doc_id": (
            provenance.get("doc_id")
            or provenance.get("dataset_file_id")
            or fallback_doc_id
            or f"dataset:{dataset_id}"
        ),
    }
    for key, value in (entity.attributes or {}).items():
        if value in (None, "") or key in properties:
            continue
        properties[key] = value
    if provenance:
        properties["origin"] = provenance
    properties["search_text"] = _search_text(display, entity)
    return GraphNode(
        provenance_key=entity_graph_key(dataset_id, entity),
        label=label,
        properties=properties,
    )


def build_edge(
    dataset_id: str,
    relationship: DatasetRelationship,
    *,
    fallback_doc_id: str,
    fallback_case_ids: Sequence[str] = (),
    graph_key_by_canonical: dict[str, str] | None = None,
) -> GraphEdge:
    rel_type = _graph_rel(relationship.rel_type)
    if rel_type is None:
        raise ValueError(f"Relationship type {relationship.rel_type!r} is not in the graph ontology")
    provenance = relationship.provenance or {}
    attrs = relationship.attributes or {}
    source_doc_id = provenance.get("doc_id") or provenance.get("dataset_file_id") or fallback_doc_id
    source_doc_ids = list(attrs.get("source_doc_ids") or provenance.get("source_doc_ids") or [])
    if source_doc_id and source_doc_id not in source_doc_ids:
        source_doc_ids.append(source_doc_id)
    # Case scope: the relationship's own explicit case list first; when the
    # source data does not state one, the edge is visible in the cases its
    # endpoints belong to (the union, so an edge is never invisible to every
    # case while both endpoints are visible — an unscoped edge in a case-less
    # dataset would make the whole graph a node cloud with no edges).
    case_scope = list(relationship.case_ids) or list(fallback_case_ids)
    properties: dict[str, Any] = {
        "dataset_id": dataset_id,
        "canonical_rel_type": relationship.rel_type,
        "confidence": float(relationship.confidence or 1.0),
        "direct_vs_derived": attrs.get("direct_vs_derived", "direct"),
        "support_level": attrs.get("support_level", "direct_source_record"),
        "contradiction_state": attrs.get("contradiction_state", "not_recorded"),
        "analytical_basis": attrs.get("analytical_basis", "source_record"),
        "case_scope": case_scope,
        # G1: an evidenced edge must name the document that justifies it.  The
        # dataset file the row came from *is* that document.
        "source_doc_id": source_doc_id,
        "source_doc_ids": source_doc_ids,
        "origin": provenance or None,
        "discriminator": relationship.edge_key,
    }
    if relationship.valid_from:
        properties["valid_from"] = relationship.valid_from
    if relationship.valid_to:
        properties["valid_to"] = relationship.valid_to
    if relationship.observed_at:
        properties["observed_at"] = relationship.observed_at
        properties.setdefault("first_ts", relationship.observed_at)
        properties.setdefault("last_ts", relationship.observed_at)
    for key, value in (relationship.attributes or {}).items():
        if value in (None, "") or key in properties:
            continue
        properties[key] = value
    if case_scope:
        properties["case_ids"] = case_scope
    graph_key_by_canonical = graph_key_by_canonical or {}
    return GraphEdge(
        source_key=graph_key_by_canonical.get(
            relationship.source_canonical_id,
            node_key(dataset_id, relationship.source_canonical_id),
        ),
        target_key=graph_key_by_canonical.get(
            relationship.target_canonical_id,
            node_key(dataset_id, relationship.target_canonical_id),
        ),
        rel_type=rel_type,
        properties=properties,
        discriminator=relationship.edge_key,
    )


async def project_dataset(
    session: AsyncSession,
    dataset: Dataset,
    *,
    container: Container | None = None,
    purge_first: bool = True,
    exclusive: bool = True,
    progress: Any = None,
) -> dict[str, Any]:
    """Rebuild the graph projection for one dataset.

    ``progress`` -- when given -- is an async callable ``(stage, pct, message)``
    used to stream the build to the Administration screen, because a rebuild
    that shows nothing for two minutes is indistinguishable from a hung one.
    """
    container = container or get_container()
    store = container.graph_store
    injector = container.injector
    dataset_id = dataset.id
    # A rebuild changes the analytical universe even when the graph backend
    # preserves its process-local version. Never serve centrality/community
    # values computed from the contaminated projection.
    from app.services.graph_service import invalidate_analytics_cache
    invalidate_analytics_cache()

    async def report(stage: str, pct: int, message: str) -> None:
        if progress is not None:
            await progress(stage, pct, message)

    if purge_first:
        await report("PURGING", 5, "Removing the previous projection of this dataset")
        purge = getattr(store, "purge_dataset", None)
        removed = purge(dataset_id) if callable(purge) else 0
        log.info("graph_build.purged", dataset_id=dataset_id, nodes=removed)

    if exclusive:
        # The graph shows the active dataset and nothing else. Leaving a
        # previous import's nodes in place is exactly how a replaced dataset
        # keeps appearing on the Graph page after a refresh.
        await report("PURGING", 8, "Evicting other datasets from the graph")
        purge_others = getattr(store, "purge_other_datasets", None)
        evicted = purge_others(dataset_id) if callable(purge_others) else 0
        if evicted:
            log.info("graph_build.evicted_other_datasets", keep=dataset_id, nodes=evicted)

    # --- case nodes -------------------------------------------------------
    await report("BUILDING_GRAPH", 10, "Creating case nodes")
    cases = (
        await session.execute(
            select(Case).where(Case.dataset_id == dataset_id).order_by(Case.case_number)
        )
    ).scalars().all()
    # The container case ("ALL") is the home for records no investigation
    # claims; it is tracked separately from the real cases below, which are
    # the only ones that get Case *nodes*.
    container_case: Case | None = None
    case_by_key: dict[str, Case] = {}
    for case in cases:
        if case.dataset_case_key == "ALL":
            container_case = case
            continue
        store.ensure_case_node(
            case.id, case.case_number, case.jurisdiction_id, dataset_id
        )
        if case.dataset_case_key:
            case_by_key[case.dataset_case_key] = case

    # --- entities ---------------------------------------------------------
    await report("BUILDING_GRAPH", 20, "Projecting entities")
    entity_rows = (
        await session.execute(
            select(DatasetEntity)
            .where(DatasetEntity.dataset_id == dataset_id)
            .order_by(DatasetEntity.canonical_id)
        )
    ).scalars().all()

    # Which cases each entity participates in, so jurisdiction scoping works
    # and case-scoped reads can find it. The dataset's container case ("ALL")
    # catches whatever no investigation claims.
    #
    # NOTE: a case-less dataset — the production "Upload of 577 files v1" —
    # has no real case to put unclaimed records in.  An earlier version
    # looked up ``case_by_key.get("ALL")`` for the container, which can never
    # match (the loop above skips it), so the default was *None* and every
    # entity was projected with an empty case list: present in the store,
    # invisible to every case-scoped read, and ``store.stats()`` the only
    # place the data still appeared.
    #
    # When real cases exist the old rules stand: a single real case absorbs
    # its unclaimed records (the container there is administrative), and with
    # several cases there is no case the data supports, so records stay
    # unscoped rather than being invented into one.
    if case_by_key:
        default_case_id = (
            next(iter(case_by_key.values())).id if len(case_by_key) == 1 else None
        )
    else:
        default_case_id = container_case.id if container_case is not None else None
    case_links = await _case_links(
        session,
        dataset_id,
        case_by_key,
        entity_ids=[
            entity.canonical_id
            for entity in entity_rows
            if entity.entity_type != sm.CASE
        ],
        default_case_id=default_case_id,
    )

    fallback_doc_id = f"dataset:{dataset_id}"
    graph_key_by_canonical = {
        entity.canonical_id: entity_graph_key(dataset_id, entity)
        for entity in entity_rows
        if entity.entity_type != sm.CASE and sm.is_graph_eligible(entity.entity_type)
    }
    nodes_written = 0
    skipped_entities = 0
    batch: list[GraphNode] = []
    for entity in entity_rows:
        if entity.entity_type == sm.CASE:
            # Cases are real ``Case`` rows, projected above.
            continue
        if not sm.is_graph_eligible(entity.entity_type):
            # DOCUMENT/EVIDENCE remain queryable provenance records in the
            # relational layer, but are never actor/network nodes.
            skipped_entities += 1
            continue
        try:
            batch.append(
                build_node(
                    dataset_id,
                    entity,
                    case_links.get(entity.canonical_id, ()),
                    fallback_doc_id=fallback_doc_id,
                )
            )
        except ValueError as exc:
            skipped_entities += 1
            log.warning(
                "graph_build.entity_skipped",
                canonical_id=entity.canonical_id,
                error=str(exc),
            )
            continue
        if len(batch) >= BATCH:
            nodes_written += injector.inject_nodes(batch)
            batch.clear()
            await report(
                "BUILDING_GRAPH",
                min(20 + int(40 * nodes_written / max(len(entity_rows), 1)), 60),
                f"Projected {nodes_written} entities",
            )
    if batch:
        nodes_written += injector.inject_nodes(batch)

    # --- relationships ----------------------------------------------------
    await report("BUILDING_RELATIONSHIPS", 65, "Projecting relationships")
    rel_rows = (
        await session.execute(
            select(DatasetRelationship).where(DatasetRelationship.dataset_id == dataset_id)
        )
    ).scalars().all()

    edges_written = 0
    skipped_edges = 0
    edge_batch: list[GraphEdge] = []
    case_edges: list[GraphEdge] = []
    for relationship in rel_rows:
        source_type = relationship.source_canonical_id.split(":", 1)[0]
        target_type = relationship.target_canonical_id.split(":", 1)[0]
        if source_type in sm.DOCUMENT_ARTIFACT_TYPES or target_type in sm.DOCUMENT_ARTIFACT_TYPES:
            # Evidence links stay in canonical/provenance storage and are
            # resolved by evidence APIs, never by the actor graph.
            skipped_edges += 1
            continue
        if target_type == sm.CASE:
            edge = _case_edge(dataset_id, relationship, case_by_key, fallback_doc_id)
            if edge is not None:
                case_edges.append(edge)
            else:
                skipped_edges += 1
            continue
        try:
            # When the source data carries no explicit case list, scope the
            # edge to the cases its endpoints belong to (see build_edge).
            derived_case_ids = sorted(
                set(case_links.get(relationship.source_canonical_id, ()))
                | set(case_links.get(relationship.target_canonical_id, ()))
            )
            edge_batch.append(
                build_edge(
                    dataset_id,
                    relationship,
                    fallback_doc_id=fallback_doc_id,
                    fallback_case_ids=derived_case_ids,
                    graph_key_by_canonical=graph_key_by_canonical,
                )
            )
        except Exception as exc:  # noqa: BLE001 - domain validation raises several types
            skipped_edges += 1
            log.warning(
                "graph_build.edge_skipped",
                rel_type=relationship.rel_type,
                error=str(exc),
            )
            continue
        if len(edge_batch) >= BATCH:
            edges_written += injector.inject_edges(edge_batch)
            edge_batch.clear()
            await report(
                "BUILDING_RELATIONSHIPS",
                min(65 + int(25 * edges_written / max(len(rel_rows), 1)), 90),
                f"Projected {edges_written} relationships",
            )
    if edge_batch:
        edges_written += injector.inject_edges(edge_batch)
    if case_edges:
        edges_written += injector.inject_edges(case_edges)

    await report("BUILDING_GRAPH", 95, "Finalising graph")
    stats = {
        "nodes_written": nodes_written,
        "edges_written": edges_written,
        "entities_considered": len(entity_rows),
        "relationships_considered": len(rel_rows),
        "entities_skipped": skipped_entities,
        "relationships_skipped": skipped_edges,
        "cases": len(cases),
        "graph": store.stats(),
    }
    log.info("graph_build.completed", dataset_id=dataset_id, **{
        k: v for k, v in stats.items() if k != "graph"
    })
    return stats


def _case_edge(
    dataset_id: str,
    relationship: DatasetRelationship,
    case_by_key: dict[str, Case],
    fallback_doc_id: str,
    graph_key_by_canonical: dict[str, str] | None = None,
) -> GraphEdge | None:
    """Link an entity to a real ``Case`` node rather than a canonical stub."""
    case_key = relationship.target_canonical_id.split(":", 1)[-1]
    case = case_by_key.get(case_key)
    if case is None:
        return None
    provenance = relationship.provenance or {}
    properties = {
        "dataset_id": dataset_id,
        "canonical_rel_type": relationship.rel_type,
        "confidence": float(relationship.confidence or 1.0),
        "source_doc_id": provenance.get("doc_id") or fallback_doc_id,
        "origin": provenance or None,
        "case_ids": [case.id],
        "discriminator": relationship.edge_key,
        **{k: v for k, v in (relationship.attributes or {}).items() if v not in (None, "")},
    }
    try:
        return GraphEdge(
            source_key=(graph_key_by_canonical or {}).get(relationship.source_canonical_id, node_key(dataset_id, relationship.source_canonical_id)),
            target_key=f"case:{case.id}",
            rel_type="PARTICIPATED_IN",
            properties=properties,
            discriminator=relationship.edge_key,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("graph_build.case_edge_skipped", error=str(exc))
        return None


#: Relationship types that carry case membership one hop out from an entity a
#: case names directly. These are *belonging* relations -- what a suspect owns,
#: uses, lives at, is a member of, is documented by. A case that names a person
#: is about that person's phone and car too, and no source file will ever spell
#: that out row by row.
#:
#: Deliberately excluded: CALLED, MESSAGED, TRANSFER_TO, TRAVELLED_TO and the
#: other transactional relations. They are the very edges an investigation
#: *discovers*; treating them as membership would put everyone who ever called
#: anyone into every case, and on a corpus with call-record hubs that means the
#: scoping stops meaning anything. Those counterparties remain fully visible --
#: they are dataset members (see the container case below) and one expand away
#: on the graph canvas.
CASE_MEMBERSHIP_RELS = {
    "USES_PHONE",
    "USES_EMAIL",
    "HAS_DEVICE",
    "OWNS_ACCOUNT",
    "OWNS_VEHICLE",
    "OWNS_PROPERTY",
    "DROVE",
    "RESIDES_AT",
    "LOCATED_AT",
    "MEMBER_OF",
    "EMPLOYED_BY",
    "WORKS_AT",
    "RELATED_TO",
    "SEEN_AT",
    "HAS_EVIDENCE",
    "INVOLVED_IN",
    "MENTIONED_IN",
}

#: How far membership spreads over those relations. One hop: the suspect's
#: phone joins the case, the phone's other callers do not.
CASE_PROPAGATION_HOPS = 1

#: Ceiling on how many entities one case may absorb by propagation. A shared
#: hub (a bank, a tower, a taxi firm) must not drag an entire dataset into one
#: case. Hitting the cap is logged, not hidden.
MAX_CASE_MEMBERS = 20_000


async def _case_links(
    session: AsyncSession,
    dataset_id: str,
    case_by_key: dict[str, Case],
    *,
    entity_ids: Iterable[str] = (),
    default_case_id: str | None = None,
) -> dict[str, list[str]]:
    """canonical_id -> the real case ids it belongs to.

    Jurisdiction scoping and every case-scoped read (search, the case graph,
    the timeline) filter graph nodes by ``case_ids``. An entity with none is
    unreachable: present in the store, invisible to the application. So every
    projected entity must land in at least one case, and the case it lands in
    has to be one the data actually supports.

    Three passes, in decreasing order of evidence:

    1. **Direct.** Rows that state the link outright -- a case-entities table
       saying PERSON_00042 is involved in CASE_0007.
    2. **Belonging.** One hop over :data:`CASE_MEMBERSHIP_RELS` from those
       entities: their phones, vehicles, accounts, addresses, employers,
       evidence. Ownership is not something a case file restates per row.
    3. **Container.** Everything else -- a phone directory, a bank's customer
       list, an address register: real dataset records that no investigation
       claims. They join the dataset's container case, which is what makes
       them searchable and jurisdiction-scoped without asserting they are
       part of an investigation they have no proven link to.
    """
    links: dict[str, list[str]] = {}

    def add(canonical_id: str, case_id: str) -> None:
        bucket = links.setdefault(canonical_id, [])
        if case_id not in bucket:
            bucket.append(case_id)

    # --- 1. links the data states directly --------------------------------
    if case_by_key:
        rows = (
            await session.execute(
                select(
                    DatasetRelationship.source_canonical_id,
                    DatasetRelationship.target_canonical_id,
                ).where(
                    DatasetRelationship.dataset_id == dataset_id,
                    DatasetRelationship.target_canonical_id.like(f"{sm.CASE}:%"),
                )
            )
        ).all()
        for source_cid, target_cid in rows:
            case = case_by_key.get(str(target_cid).split(":", 1)[-1])
            if case is not None:
                add(str(source_cid), case.id)

    direct = {cid: list(cases) for cid, cases in links.items()}

    # --- 2. one hop over belonging relations -------------------------------
    if direct:
        adjacency: dict[str, set[str]] = {}
        edge_rows = (
            await session.execute(
                select(
                    DatasetRelationship.source_canonical_id,
                    DatasetRelationship.target_canonical_id,
                    DatasetRelationship.rel_type,
                ).where(DatasetRelationship.dataset_id == dataset_id)
            )
        ).all()
        for source_cid, target_cid, rel_type in edge_rows:
            if str(rel_type or "").upper() not in CASE_MEMBERSHIP_RELS:
                continue
            source, target = str(source_cid), str(target_cid)
            if source.startswith(f"{sm.CASE}:") or target.startswith(f"{sm.CASE}:"):
                continue
            adjacency.setdefault(source, set()).add(target)
            adjacency.setdefault(target, set()).add(source)

        seeds: dict[str, set[str]] = {}
        for canonical_id, case_ids in direct.items():
            for case_id in case_ids:
                seeds.setdefault(case_id, set()).add(canonical_id)

        truncated: set[str] = set()
        for case_id, members in seeds.items():
            frontier = set(members)
            reached = set(members)
            for _hop in range(CASE_PROPAGATION_HOPS):
                nxt = {
                    neighbour
                    for canonical_id in frontier
                    for neighbour in adjacency.get(canonical_id, ())
                    if neighbour not in reached
                }
                if not nxt:
                    break
                if len(reached) + len(nxt) > MAX_CASE_MEMBERS:
                    truncated.add(case_id)
                    nxt = set(sorted(nxt)[: max(0, MAX_CASE_MEMBERS - len(reached))])
                reached |= nxt
                frontier = nxt
                if len(reached) >= MAX_CASE_MEMBERS:
                    break
            for canonical_id in reached:
                add(canonical_id, case_id)

        if truncated:
            log.warning(
                "graph_build.case_membership_capped",
                dataset_id=dataset_id,
                cases=len(truncated),
                cap=MAX_CASE_MEMBERS,
            )

    # --- 3. whatever is left belongs to the dataset itself ------------------
    if default_case_id:
        unclaimed = 0
        for canonical_id in entity_ids:
            if canonical_id not in links:
                add(canonical_id, default_case_id)
                unclaimed += 1
        if unclaimed:
            log.info(
                "graph_build.container_case_membership",
                dataset_id=dataset_id,
                entities=unclaimed,
                case_id=default_case_id,
            )

    return links


def dataset_nodes_present(container: Container, dataset_id: str) -> int:
    """How many nodes the store currently holds for a dataset (diagnostics)."""
    store = container.graph_store
    lister = getattr(store, "list_nodes", None)
    if not callable(lister):
        return 0
    page = lister(limit=1, offset=0)
    return int(page.get("total", 0) or 0)


def iter_batches(items: Iterable[Any], size: int = BATCH) -> Iterable[list[Any]]:
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
