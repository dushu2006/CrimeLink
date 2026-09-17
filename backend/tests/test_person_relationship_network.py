"""Person → Person relationship network (Investigate → Relationships).

The investigator-facing graph answers *who is connected to whom*.  Phones,
bank accounts, vehicles, locations, organisations and documents are the
**support** for an answer, never the answer itself, so they are walked
internally and collapse into one aggregated person-to-person edge.

Covers the acceptance points A–L:

A. person-to-person graph contains PERSON nodes as primary nodes
B. phone numbers are not primary nodes
C. bank statements / accounts are not primary nodes
D. documents are not primary nodes
E. vehicles are not primary nodes
F. locations are not primary nodes
G. supporting evidence can still be retrieved for a person-to-person edge
H. multiple supporting items aggregate into one person-to-person edge
I. a person gets ★ only on an authoritative criminal status
J. witnesses / associates / evidence participants do not get ★
K. the Entity Network still carries the deeper evidence/entity relationships
L. the Case/Person/Entity networks never render the same graph
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from app.datasets import registry
from app.db.base import new_uuid
from app.db.models import Case, CaseDocument
from app.db.session import async_session
from app.domain.enums import CaseStatus, DocumentType
from app.domain.models import GraphEdge, GraphNode
from app.security.deps import JurisdictionScope, Principal
from app.services.graph_service import GraphService
from app.services.person_relationships import (
    PERSON_RELATIONSHIP_LABELS,
    derive_person_relationships,
    relationship_id,
)

NON_PERSON_LABELS = {
    "PHONE",
    "BANK_ACCOUNT",
    "ACCOUNT",
    "DOCUMENT",
    "EVIDENCE",
    "VEHICLE",
    "LOCATION",
    "ORGANIZATION",
    "EVENT",
    "CASE",
    "TRANSACTION",
}


def _person(key: str, name: str, case_ids, *, criminal_status=None, role="ASSOCIATE", ds=None):
    return GraphNode(
        provenance_key=key,
        label="Person",
        properties={
            "name": name,
            "display_name": name,
            "full_name": name,
            "role": role,
            "criminal_status": criminal_status,
            "case_ids": list(case_ids),
            "dataset_id": ds,
            "source_doc_ids": ["DOC-FIR-001"],
            "origin": {"file": "FIR_2026_001.txt", "row": 3},
        },
    )


def _entity(key: str, label: str, name: str, case_ids, *, entity_type=None, ds=None):
    return GraphNode(
        provenance_key=key,
        label=label,
        properties={
            "name": name,
            "entity_type": entity_type or label.upper(),
            "case_ids": list(case_ids),
            "dataset_id": ds,
            "source_doc_ids": ["DOC-CDR-004"],
            "origin": {"file": "CDR_2026_04.csv", "row": 51},
        },
    )


def _edge(source: str, target: str, rel_type: str, *, doc="DOC-CDR-004", case_ids=None, **props):
    properties = {
        "source_doc_id": doc,
        "source_doc_ids": [doc],
        "case_ids": list(case_ids or []),
        "confidence": 0.9,
        "origin": {"file": "CDR_2026_04.csv", "row": 51},
    }
    properties.update(props)
    return GraphEdge(
        source_key=source,
        target_key=target,
        rel_type=rel_type,
        key=f"{source}-{rel_type}-{target}",
        properties=properties,
    )


@pytest.fixture()
async def relationship_dataset(container, users):
    """One active dataset holding a small, fully explicit relationship graph.

    People
      P1  Rakesh Mehta     criminal_status=CONVICTED  (case C1)   → ★
      P2  Sunita Rao       no status                  (case C1)
      P3  Arun Nair        role=WITNESS               (case C1)
      P4  Farah Qureshi    role=ASSOCIATE             (case C1)
      P5  Vikram Sethi     criminal_status=ACCUSED    (case C2)   → ★
      P6  Neha Gupta       role=INFORMANT             (case C2, isolated)

    Supporting records (all real graph rows, nothing invented)
      P1 ─ ASSOCIATE_OF ─ P2                     direct person-to-person record
      P1 ─ USES_PHONE ─ PH1 ─ USES_PHONE ─ P2    shared phone
      PH1 ─ CALLED ─ PH2, PH2 owned by P2        communication
      P1 ─ OWNS_ACCOUNT ─ BA1 ─ TRANSFER_TO ─ BA2 ─ OWNS_ACCOUNT ─ P5
                                                 financial link, cross-case
      P1 ─ OWNS_VEHICLE ─ V1 ─ OWNS_VEHICLE ─ P4 shared vehicle
      P2 ─ LOCATED_AT ─ L1 ─ LOCATED_AT ─ P3     shared address
      P3 ─ MEMBER_OF ─ O1 ─ MEMBER_OF ─ P4       shared organization
      P3 ─ RELATIVE_OF ─ P4                      family / relative
    """
    async with async_session() as session:
        ds = await registry.create_dataset(
            session, name="Relationship network test", version="1", source_kind="folder"
        )
        await registry.activate(session, ds)
        c1 = Case(
            id=new_uuid(), case_number="REL-C1", title="Armed robbery",
            jurisdiction_id="RJ-JAIPUR", dataset_id=ds.id, status=CaseStatus.OPEN,
        )
        c2 = Case(
            id=new_uuid(), case_number="REL-C2", title="Money laundering",
            jurisdiction_id="RJ-JAIPUR", dataset_id=ds.id, status=CaseStatus.OPEN,
        )
        session.add_all([c1, c2])
        await session.flush()
        doc = CaseDocument(
            id="DOC-FIR-001", case_id=c1.id, dataset_id=ds.id,
            document_type=DocumentType.FIR, filename="FIR_2026_001.txt",
            storage_key="cases/c1/fir.txt", content_hash="sha256_rel_hash_001",
        )
        session.add(doc)
        await session.commit()

        nodes = [
            _person("P1", "Rakesh Mehta", [c1.id], criminal_status="CONVICTED",
                    role="SUSPECT", ds=ds.id),
            _person("P2", "Sunita Rao", [c1.id], ds=ds.id),
            _person("P3", "Arun Nair", [c1.id], role="WITNESS", ds=ds.id),
            _person("P4", "Farah Qureshi", [c1.id], role="ASSOCIATE", ds=ds.id),
            _person("P5", "Vikram Sethi", [c2.id], criminal_status="ACCUSED",
                    role="ASSOCIATE", ds=ds.id),
            _person("P6", "Neha Gupta", [c2.id], role="INFORMANT", ds=ds.id),
            _entity("PH1", "Phone", "+919876543210", [c1.id], ds=ds.id),
            _entity("PH2", "Phone", "+919004011122", [c1.id], ds=ds.id),
            _entity("BA1", "BankAccount", "HDFC a/c 4417", [c1.id],
                    entity_type="BANK_ACCOUNT", ds=ds.id),
            _entity("BA2", "BankAccount", "ICICI a/c 8890", [c2.id],
                    entity_type="BANK_ACCOUNT", ds=ds.id),
            _entity("V1", "Vehicle", "MH02AB1234", [c1.id], ds=ds.id),
            _entity("L1", "Location", "14 Linking Road, Mumbai", [c1.id], ds=ds.id),
            _entity("O1", "Organization", "Sai Logistics Pvt Ltd", [c1.id], ds=ds.id),
            # A document must never become a relationship node.
            GraphNode(
                provenance_key="DOC-CDR-004",
                label="Document",
                properties={
                    "name": "CDR_2026_04.csv",
                    "entity_type": "DOCUMENT",
                    "case_ids": [c1.id],
                    "dataset_id": ds.id,
                },
            ),
        ]
        edges = [
            _edge("P1", "P2", "ASSOCIATE_OF", doc="DOC-FIR-001", case_ids=[c1.id]),
            _edge("P1", "PH1", "USES_PHONE", case_ids=[c1.id]),
            _edge("P2", "PH1", "USES_PHONE", case_ids=[c1.id]),
            _edge("P2", "PH2", "USES_PHONE", case_ids=[c1.id]),
            _edge("PH1", "PH2", "CALLED", case_ids=[c1.id], call_count=7,
                  first_ts="2026-04-01T09:00:00", last_ts="2026-04-03T21:15:00"),
            _edge("P1", "BA1", "OWNS_ACCOUNT", case_ids=[c1.id], doc="DOC-FIN-002"),
            _edge("P5", "BA2", "OWNS_ACCOUNT", case_ids=[c2.id], doc="DOC-FIN-009"),
            _edge("BA1", "BA2", "TRANSFER_TO", case_ids=[c1.id, c2.id], doc="DOC-FIN-002",
                  amount=49500, ts="2026-04-02T11:30:00"),
            _edge("P1", "V1", "OWNS_VEHICLE", case_ids=[c1.id], doc="DOC-ANPR-003"),
            _edge("P4", "V1", "OWNS_VEHICLE", case_ids=[c1.id], doc="DOC-ANPR-003"),
            _edge("P2", "L1", "LOCATED_AT", case_ids=[c1.id], doc="DOC-GEO-005"),
            _edge("P3", "L1", "LOCATED_AT", case_ids=[c1.id], doc="DOC-GEO-005"),
            _edge("P3", "O1", "MEMBER_OF", case_ids=[c1.id], doc="DOC-REG-006"),
            _edge("P4", "O1", "MEMBER_OF", case_ids=[c1.id], doc="DOC-REG-006"),
            _edge("P3", "P4", "RELATIVE_OF", case_ids=[c1.id], doc="DOC-FIR-001"),
        ]
        svc = GraphService()
        svc.container.graph_store.upsert_nodes(nodes)
        svc.container.graph_store.upsert_edges(edges)

        principal = Principal(users["INV-0001"])
        scope = JurisdictionScope(principal, granted_jurisdictions=set(), granted_case_ids=set())
        yield {
            "dataset": ds, "c1": c1, "c2": c2, "scope": scope, "service": svc,
            "nodes": {n.provenance_key: n for n in nodes},
        }

        await registry.purge_dataset_data(session, ds.id)
        await session.commit()
        svc.container.graph_store.purge_dataset(ds.id)


# --------------------------------------------------------------------------- #
# A–F: the primary graph is people, and only people
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_relationship_network_nodes_are_people_only(relationship_dataset):
    ctx = relationship_dataset
    async with async_session() as session:
        net = await ctx["service"].relationship_network(session, ctx["scope"])

    assert net["mode"] == "master_relationships"
    assert net["view"] == "PERSON_NETWORK"
    assert net["nodes"], "the fixture graph must produce a non-empty person network"

    # A. PERSON is the only primary node type.
    labels = {n["label"] for n in net["nodes"]}
    assert labels == {"PERSON"}
    assert net["node_types"] == ["PERSON"]

    node_keys = {n["provenance_key"] for n in net["nodes"]}
    # B. phone numbers, C. bank accounts, D. documents, E. vehicles,
    # F. locations, plus organisations and events.
    for forbidden in ("PH1", "PH2", "BA1", "BA2", "V1", "L1", "O1", "DOC-CDR-004"):
        assert forbidden not in node_keys, f"{forbidden} must not be a primary node"
    assert not (labels & NON_PERSON_LABELS)

    # Every edge endpoint is a person.
    for edge in net["edges"]:
        assert edge["source"] in node_keys
        assert edge["target"] in node_keys


@pytest.mark.asyncio
async def test_relationship_network_carries_typed_person_relationships(relationship_dataset):
    ctx = relationship_dataset
    async with async_session() as session:
        net = await ctx["service"].relationship_network(session, ctx["scope"])

    pairs = {frozenset((e["source"], e["target"])): e for e in net["edges"]}

    assert frozenset(("P1", "P2")) in pairs
    assert frozenset(("P1", "P5")) in pairs, "financial link via accounts, cross-case"
    assert frozenset(("P1", "P4")) in pairs, "shared vehicle"
    assert frozenset(("P2", "P3")) in pairs, "shared address"
    assert frozenset(("P3", "P4")) in pairs, "shared organization + relative"

    # Every relationship type used is part of the declared vocabulary and has a
    # human-readable label an investigator can read on an edge.
    for edge in net["edges"]:
        assert edge["relationship_type"] in PERSON_RELATIONSHIP_LABELS
        assert edge["label"]
        assert edge["relationship_types"]
        assert edge["evidence_count"] >= 1

    p1_p2 = pairs[frozenset(("P1", "P2"))]
    assert "COMMUNICATION" in p1_p2["relationship_types"]
    assert "SHARED_PHONE" in p1_p2["relationship_types"]
    assert "KNOWN_ASSOCIATION" in p1_p2["relationship_types"]
    # The primary label is the most specific one, not the generic fallback.
    assert p1_p2["relationship_type"] == "COMMUNICATION"
    assert p1_p2["label"] == "Communication"

    p3_p4 = pairs[frozenset(("P3", "P4"))]
    assert "FAMILY_RELATIVE" in p3_p4["relationship_types"]
    assert p3_p4["relationship_type"] == "FAMILY_RELATIVE"


# --------------------------------------------------------------------------- #
# G + H: evidence stays reachable, aggregated behind one edge
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_supporting_evidence_aggregates_into_one_edge(relationship_dataset):
    ctx = relationship_dataset
    async with async_session() as session:
        net = await ctx["service"].relationship_network(session, ctx["scope"])

    p1_p2_edges = [
        e for e in net["edges"]
        if frozenset((e["source"], e["target"])) == frozenset(("P1", "P2"))
    ]
    # H. Phone + call record + direct associate record collapse into ONE edge.
    assert len(p1_p2_edges) == 1, "duplicate edges between the same two people"
    edge = p1_p2_edges[0]
    assert edge["id"] == relationship_id("P1", "P2")
    assert edge["supporting_item_count"] >= 3
    assert edge["evidence_count"] == edge["supporting_item_count"]
    assert set(edge["supporting_kinds"]) >= {"PHONE", "COMMUNICATION", "DIRECT_RECORD"}
    assert edge["strength"] in {"STRONG", "MODERATE"}
    # The supporting items name the entity but the entity is not a node.
    phone_items = [i for i in edge["supporting_items"] if i["kind"] == "PHONE"]
    assert phone_items and phone_items[0]["ref"] == "PH1"
    assert "+919876543210" in phone_items[0]["label"]


@pytest.mark.asyncio
async def test_relationship_evidence_drill_down(relationship_dataset):
    ctx = relationship_dataset
    async with async_session() as session:
        detail = await ctx["service"].relationship_evidence(
            session, ctx["scope"], "P1", "P2"
        )

    assert detail["mode"] == "relationship_evidence"
    assert detail["empty_reason"] is None
    # G. Evidence is still retrievable for a person-to-person edge.
    assert detail["supporting_item_count"] >= 3
    assert detail["source_person"]["name"] == "Rakesh Mehta"
    assert detail["target_person"]["name"] == "Sunita Rao"

    kinds = {item["kind"] for item in detail["supporting_items"]}
    assert "COMMUNICATION" in kinds
    call = next(i for i in detail["supporting_items"] if i["kind"] == "COMMUNICATION")
    assert call["properties"]["call_count"] == 7
    assert call["source_doc_ids"] == ["DOC-CDR-004"]
    assert call["evidence"]["source_doc_id"] == "DOC-CDR-004"


@pytest.mark.asyncio
async def test_relationship_evidence_is_empty_for_an_unrelated_pair(relationship_dataset):
    """No record → no relationship.  The service says so; it never invents one."""
    ctx = relationship_dataset
    async with async_session() as session:
        net = await ctx["service"].relationship_network(session, ctx["scope"])
        detail = await ctx["service"].relationship_evidence(
            session, ctx["scope"], "P1", "P6"
        )

    assert detail["relationship"] is None
    assert detail["supporting_items"] == []
    assert detail["empty_reason"]
    assert not any(
        frozenset((e["source"], e["target"])) == frozenset(("P1", "P6"))
        for e in net["edges"]
    )


@pytest.mark.asyncio
async def test_relationship_evidence_rejects_non_person_endpoints(relationship_dataset):
    ctx = relationship_dataset
    from app.errors import NotFoundError, ValidationFailedError

    async with async_session() as session:
        with pytest.raises(NotFoundError):
            await ctx["service"].relationship_evidence(session, ctx["scope"], "P1", "PH1")
        with pytest.raises(ValidationFailedError):
            await ctx["service"].relationship_evidence(session, ctx["scope"], "P1", "P1")


# --------------------------------------------------------------------------- #
# I + J: the ★ is driven by the authoritative criminal status only
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_star_follows_authoritative_criminal_status(relationship_dataset):
    ctx = relationship_dataset
    async with async_session() as session:
        net = await ctx["service"].relationship_network(
            session, ctx["scope"], include_isolated=True
        )

    by_key = {n["provenance_key"]: n for n in net["nodes"]}

    # I. Explicit authoritative status → is_criminal True (the ★).
    assert by_key["P1"]["is_criminal"] is True
    assert by_key["P1"]["criminal_status"] == "CONVICTED"
    assert by_key["P5"]["is_criminal"] is True
    assert by_key["P5"]["criminal_status"] == "ACCUSED"

    # J. Witnesses, associates, informants and plain evidence participants
    #    never earn the star, however well connected they are.
    for key in ("P2", "P3", "P4", "P6"):
        assert by_key[key]["is_criminal"] is False, f"{key} must not be starred"
        assert by_key[key]["criminal_status"] is None

    assert net["counts"]["confirmed_criminals"] == 2


@pytest.mark.asyncio
async def test_star_is_never_derived_from_network_position(relationship_dataset):
    """The most connected person is not a criminal just for being connected."""
    ctx = relationship_dataset
    async with async_session() as session:
        net = await ctx["service"].relationship_network(
            session, ctx["scope"], include_isolated=True
        )

    most_connected = max(net["nodes"], key=lambda n: n["relationship_count"])
    if most_connected["criminal_status"] is None:
        assert most_connected["is_criminal"] is False
    for node in net["nodes"]:
        assert node["is_criminal"] == bool(node["criminal_status"]), (
            "is_criminal must be a pure function of the authoritative field"
        )


# --------------------------------------------------------------------------- #
# K + L: the entity and case networks remain separate graphs
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_entity_network_keeps_the_deeper_evidence_graph(relationship_dataset):
    ctx = relationship_dataset
    async with async_session() as session:
        person_net = await ctx["service"].relationship_network(session, ctx["scope"])
        entity_net = await ctx["service"].master_graph(session, ctx["scope"])

    # K. The Entity Network still holds phones, accounts, vehicles, locations
    #    and organisations, and the relationships between them.
    entity_labels = {n["label"] for n in entity_net["nodes"]}
    assert {"PHONE", "BANK_ACCOUNT", "VEHICLE", "LOCATION", "ORGANIZATION"} <= entity_labels
    entity_keys = {n["provenance_key"] for n in entity_net["nodes"]}
    assert {"PH1", "PH2", "BA1", "BA2", "V1", "L1", "O1"} <= entity_keys

    entity_rel_types = {e["rel_type"] for e in entity_net["edges"]}
    assert {"USES_PHONE", "CALLED", "OWNS_ACCOUNT", "TRANSFER_TO", "OWNS_VEHICLE",
            "LOCATED_AT", "MEMBER_OF"} <= entity_rel_types

    # L. The two views are genuinely different graphs, not one graph with a
    #    different title.
    assert entity_net["mode"] == "master"
    assert person_net["mode"] == "master_relationships"
    assert len(entity_net["nodes"]) > len(person_net["nodes"])
    assert not any(e["rel_type"] == "TRANSFER_TO" for e in entity_net["edges"] if
                   e["source"] not in entity_keys)
    person_edges = {e["id"] for e in person_net["edges"]}
    entity_edge_ids = {e["key"] for e in entity_net["edges"]}
    assert not (person_edges & entity_edge_ids)
    # No entity edge type leaks into the person graph vocabulary.
    for edge in person_net["edges"]:
        assert edge["relationship_type"] in PERSON_RELATIONSHIP_LABELS


@pytest.mark.asyncio
async def test_case_and_entity_networks_are_distinct_surfaces(relationship_dataset):
    ctx = relationship_dataset
    async with async_session() as session:
        case_net = await ctx["service"].master_case_network(session, ctx["scope"])
        entity_net = await ctx["service"].master_graph(session, ctx["scope"])
        person_net = await ctx["service"].relationship_network(session, ctx["scope"])

    assert case_net["mode"] == "master_case"
    assert {n["label"] for n in case_net["nodes"]} == {"CASE"}
    assert {n["label"] for n in entity_net["nodes"]} & {"PHONE", "VEHICLE"}
    assert {n["label"] for n in person_net["nodes"]} == {"PERSON"}
    assert len({case_net["mode"], entity_net["mode"], person_net["mode"]}) == 3


# --------------------------------------------------------------------------- #
# Cross-case, isolation, empty state
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_cross_case_relationship_is_one_edge_with_case_context(relationship_dataset):
    ctx = relationship_dataset
    async with async_session() as session:
        net = await ctx["service"].relationship_network(session, ctx["scope"])

    edge = next(
        e for e in net["edges"]
        if frozenset((e["source"], e["target"])) == frozenset(("P1", "P5"))
    )
    assert edge["cross_case"] is True
    assert set(edge["case_ids"]) == {ctx["c1"].id, ctx["c2"].id}
    assert edge["relationship_type"] == "FINANCIAL_LINK"
    transfer = next(i for i in edge["supporting_items"] if i["kind"] == "TRANSACTION")
    assert transfer["properties"]["amount"] == 49500


@pytest.mark.asyncio
async def test_case_scoped_relationship_network_is_case_isolated(relationship_dataset):
    ctx = relationship_dataset
    async with async_session() as session:
        master = await ctx["service"].relationship_network(session, ctx["scope"])
        scoped = await ctx["service"].relationship_network(
            session, ctx["scope"], case_id=ctx["c1"].id
        )

    assert scoped["mode"] == "case_relationships"
    assert scoped["case_id"] == ctx["c1"].id
    scoped_keys = {n["provenance_key"] for n in scoped["nodes"]}
    # P5 lives only in C2.
    assert "P5" not in scoped_keys
    assert "P1" in scoped_keys
    assert len(scoped["edges"]) < len(master["edges"])
    for edge in scoped["edges"]:
        assert edge["cross_case"] is False


@pytest.mark.asyncio
async def test_isolated_people_are_hidden_by_default_and_reported_on_request(
    relationship_dataset,
):
    ctx = relationship_dataset
    async with async_session() as session:
        default_net = await ctx["service"].relationship_network(session, ctx["scope"])
        with_isolated = await ctx["service"].relationship_network(
            session, ctx["scope"], include_isolated=True
        )

    default_keys = {n["provenance_key"] for n in default_net["nodes"]}
    assert "P6" not in default_keys
    assert "P6" in {n["provenance_key"] for n in with_isolated["nodes"]}
    assert with_isolated["counts"]["persons_total"] == 6
    for node in default_net["nodes"]:
        assert node["relationship_count"] >= 1


@pytest.mark.asyncio
async def test_empty_dataset_reports_an_honest_empty_state(container, users):
    async with async_session() as session:
        ds = await registry.create_dataset(
            session, name="No relationships", version="1", source_kind="folder"
        )
        await registry.activate(session, ds)
        case = Case(
            id=new_uuid(), case_number="REL-EMPTY", title="Empty",
            jurisdiction_id="RJ-JAIPUR", dataset_id=ds.id, status=CaseStatus.OPEN,
        )
        session.add(case)
        await session.flush()
        svc = GraphService()
        svc.container.graph_store.upsert_nodes([
            _person("PE1", "Lone Person", [case.id], ds=ds.id),
            _entity("PHE1", "Phone", "+919999999999", [case.id], ds=ds.id),
        ])
        svc.container.graph_store.upsert_edges([
            _edge("PE1", "PHE1", "USES_PHONE", case_ids=[case.id]),
        ])

        principal = Principal(users["INV-0001"])
        scope = JurisdictionScope(principal, granted_jurisdictions=set(), granted_case_ids=set())
        net = await svc.relationship_network(session, scope)

        assert net["edges"] == []
        assert net["nodes"] == []
        assert net["empty_reason"] == (
            "No verified person-to-person relationships found in the active dataset."
        )

        await registry.purge_dataset_data(session, ds.id)
        await session.commit()
        svc.container.graph_store.purge_dataset(ds.id)


# --------------------------------------------------------------------------- #
# Derivation unit behaviour: no invented relationships
# --------------------------------------------------------------------------- #

def test_derivation_never_invents_a_relationship():
    """Two people with no shared record and no bridge must not be linked."""
    from app.domain.models import CaseGraphSnapshot

    nodes = {
        "A": _person("A", "A", ["C1"]),
        "B": _person("B", "B", ["C1"]),
        "PHA": _entity("PHA", "Phone", "+911111111111", ["C1"]),
        "PHB": _entity("PHB", "Phone", "+912222222222", ["C1"]),
    }
    edges = [
        _edge("A", "PHA", "USES_PHONE", case_ids=["C1"]),
        _edge("B", "PHB", "USES_PHONE", case_ids=["C1"]),
    ]
    snapshot = CaseGraphSnapshot(case_id="C1", nodes=nodes, edges=edges)
    derived = derive_person_relationships(snapshot)
    assert derived["edges"] == []
    assert derived["persons"] == {}
    assert derived["counts"]["relationships"] == 0


def test_derivation_caps_generic_attributes_instead_of_exploding():
    """An address held by 40 people is a category, not 780 relationships."""
    from app.domain.models import CaseGraphSnapshot

    nodes = {"L": _entity("L", "Location", "Central Police Station", ["C1"])}
    edges = []
    for i in range(40):
        key = f"A{i}"
        nodes[key] = _person(key, f"Person {i}", ["C1"])
        edges.append(_edge(key, "L", "LOCATED_AT", case_ids=["C1"]))
    snapshot = CaseGraphSnapshot(case_id="C1", nodes=nodes, edges=edges)

    derived = derive_person_relationships(snapshot)
    assert derived["edges"] == []
    assert derived["suppressed_shared_entities"]["SHARED_ADDRESS"] == 1

    relaxed = derive_person_relationships(snapshot, max_fanout=50)
    assert len(relaxed["edges"]) == (40 * 39) // 2


def test_relationship_id_is_order_independent():
    assert relationship_id("P1", "P2") == relationship_id("P2", "P1")


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #

def test_relationship_network_api_endpoints(client, investigator_headers):
    resp = client.get("/api/v1/graph/master/relationships", headers=investigator_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] in {"master_relationships"}
    assert data["view"] == "PERSON_NETWORK"
    assert data["node_types"] == ["PERSON"]
    for node in data["nodes"]:
        assert node["label"] == "PERSON"

    evidence = client.get(
        "/api/v1/graph/master/relationship-evidence",
        params={"source": "P1", "target": "P2"},
        headers=investigator_headers,
    )
    # Unknown pair in an empty test database: 404, not a fabricated relationship.
    assert evidence.status_code == 404


def test_openapi_documents_the_relationship_routes(app):
    paths = app.openapi()["paths"]
    assert "/api/v1/graph/master/relationships" in paths
    assert "/api/v1/graph/master/relationship-evidence" in paths
    assert "/api/v1/graph/cases/{case_id}/relationships" in paths
