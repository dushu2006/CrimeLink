"""Master-scoped person graph: cross-case reach and entity-type safety.

The PERSON NETWORK scope walks the active dataset (not a single case), so a
person's linked phones, bank accounts, vehicles, organisations and cases are
reachable across case boundaries. These tests pin three things:

1. The walk is active-dataset scoped and crosses case boundaries.
2. Entity types are preserved — a BANK_ACCOUNT is never rendered or treated as
   a PERSON, and a LOCATION is never a PERSON.
3. Dataset isolation holds: activating a replacement dataset evicts the
   previous projection from the person selector and network.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.datasets import registry
from app.db.base import new_uuid
from app.db.models import Case, Dataset
from app.db.session import async_session
from app.domain.enums import CaseStatus
from app.domain.models import GraphEdge, GraphNode
from app.security.deps import JurisdictionScope, Principal
from app.services.graph_service import GraphService

DOC = "doc-person-scope"


def _scope(users) -> JurisdictionScope:
    principal = Principal(users["INV-0001"])
    return JurisdictionScope(principal, granted_jurisdictions=set(), granted_case_ids=set())


async def _active_dataset(session, name: str) -> Dataset:
    ds = await registry.create_dataset(session, name=name, version="1", source_kind="folder")
    await registry.activate(session, ds)
    await session.commit()
    return ds


async def _case(session, dataset: Dataset, number: str) -> Case:
    case = Case(
        id=new_uuid(),
        case_number=number,
        title="Test case",
        jurisdiction_id="RJ-JAIPUR",
        dataset_id=dataset.id,
        status=CaseStatus.OPEN,
    )
    session.add(case)
    await session.commit()
    return case


def _node(key, name, label, cases, dataset_id, **props):
    properties = {
        "name": name,
        "case_ids": list(cases),
        "dataset_id": dataset_id,
        "source_doc_id": DOC,
        "confidence": 1.0,
    }
    properties.update(props)
    return GraphNode(provenance_key=key, label=label, properties=properties)


def _edge(a, b, rel, discriminator):
    return GraphEdge(
        source_key=a,
        target_key=b,
        rel_type=rel,
        properties={"source_doc_id": DOC, "confidence": 0.9},
        discriminator=discriminator,
    )


async def test_master_person_targets_lists_only_persons_across_dataset(
    db, users, container
):
    ds = await _active_dataset(db, "Person Targets DS")
    c1 = await _case(db, ds, "PT-1")
    c2 = await _case(db, ds, "PT-2")
    container.injector.inject_nodes(
        [
            _node("p1", "Alpha Person", "Person", [c1.id, c2.id], ds.id),
            _node("p2", "Beta Person", "Person", [c1.id], ds.id),
            _node("ph1", "+919800000001", "Phone", [c1.id], ds.id),
            _node("ba1", "ACCT-0001", "BankAccount", [c2.id], ds.id),
        ]
    )
    container.injector.inject_edges(
        [
            _edge("p1", "ph1", "USES_PHONE", "pt-a"),
            _edge("p1", "p2", "ASSOCIATE_OF", "pt-b"),
            _edge("p1", "ba1", "OWNS_ACCOUNT", "pt-c"),
        ]
    )

    payload = await GraphService(container).master_person_targets(db, _scope(users))
    keys = {item["provenance_key"] for item in payload["items"]}
    assert keys == {"p1", "p2"}, "only PERSON nodes are selectable targets"
    by_key = {item["provenance_key"]: item for item in payload["items"]}
    assert by_key["p1"]["connections"] == 3
    assert set(by_key["p1"]["case_ids"]) == {c1.id, c2.id}


async def test_master_person_network_crosses_case_boundaries(db, users, container):
    ds = await _active_dataset(db, "Cross Case DS")
    c1 = await _case(db, ds, "CC-1")
    c2 = await _case(db, ds, "CC-2")
    container.injector.inject_nodes(
        [
            _node("p1", "Ravi", "Person", [c1.id, c2.id], ds.id),
            _node("ph1", "+919811111111", "Phone", [c1.id], ds.id),
            _node("ba1", "ACCT-900", "BankAccount", [c2.id], ds.id),
            _node("veh1", "RJ14AB1234", "Vehicle", [c2.id], ds.id),
        ]
    )
    container.injector.inject_edges(
        [
            _edge("p1", "ph1", "USES_PHONE", "cc-a"),
            _edge("p1", "ba1", "OWNS_ACCOUNT", "cc-b"),
            _edge("p1", "veh1", "OWNS_VEHICLE", "cc-c"),
        ]
    )

    payload = await GraphService(container).master_person_network(
        db, _scope(users), "p1", depth=2
    )
    assert payload["mode"] == "master"
    assert payload["target"]["provenance_key"] == "p1", "the person is the central subject"
    keys = {node["provenance_key"] for node in payload["nodes"]}
    assert {"p1", "ph1", "ba1", "veh1"} <= keys, "cross-case neighbours are reachable"
    assert set(payload["person_case_ids"]) == {c1.id, c2.id}


async def test_master_person_network_preserves_entity_types(db, users, container):
    ds = await _active_dataset(db, "Type Safety DS")
    c1 = await _case(db, ds, "TS-1")
    container.injector.inject_nodes(
        [
            _node("p1", "Sana", "Person", [c1.id], ds.id),
            _node("ba1", "ACCT-777", "BankAccount", [c1.id], ds.id),
            _node("loc1", "New Delhi", "Location", [c1.id], ds.id),
            _node("ph1", "+919822222222", "Phone", [c1.id], ds.id),
        ]
    )
    container.injector.inject_edges(
        [
            _edge("p1", "ba1", "OWNS_ACCOUNT", "ts-a"),
            _edge("p1", "loc1", "LOCATED_AT", "ts-b"),
            _edge("p1", "ph1", "USES_PHONE", "ts-c"),
        ]
    )

    payload = await GraphService(container).master_person_network(
        db, _scope(users), "p1", depth=1
    )
    by_key = {node["provenance_key"]: node for node in payload["nodes"]}
    assert by_key["ba1"]["label"] == "BANK_ACCOUNT", "a bank account is never a PERSON"
    assert by_key["loc1"]["label"] == "LOCATION", "a location is never a PERSON"
    assert by_key["ph1"]["label"] == "PHONE", "a phone is never a PERSON"
    assert by_key["p1"]["label"] == "PERSON"


async def test_master_person_network_rejects_non_person(db, users, container):
    ds = await _active_dataset(db, "Non Person DS")
    c1 = await _case(db, ds, "NP-1")
    container.injector.inject_nodes([_node("ba1", "ACCT-000", "BankAccount", [c1.id], ds.id)])
    with pytest.raises(Exception):
        await GraphService(container).master_person_network(db, _scope(users), "ba1")


async def test_master_person_network_respects_dataset_isolation(db, users, container):
    ds_a = await _active_dataset(db, "Person Isolation A")
    c1 = await _case(db, ds_a, "PI-A-1")
    container.injector.inject_nodes(
        [
            _node("pa1", "Person Alpha", "Person", [c1.id], ds_a.id),
            _node("ph_a", "+919833333333", "Phone", [c1.id], ds_a.id),
        ]
    )
    container.injector.inject_edges([_edge("pa1", "ph_a", "USES_PHONE", "pi-a")])

    # Activate a replacement dataset: the previous projection must be evicted.
    ds_b = await _active_dataset(db, "Person Isolation B")
    c2 = await _case(db, ds_b, "PI-B-1")
    container.injector.inject_nodes([_node("pb1", "Person Bravo", "Person", [c2.id], ds_b.id)])

    payload = await GraphService(container).master_person_targets(db, _scope(users))
    keys = {item["provenance_key"] for item in payload["items"]}
    assert "pb1" in keys
    assert "pa1" not in keys, "a replaced dataset's persons must not resurface"

    # Cleanup
    async with async_session() as session:
        await registry.purge_dataset_data(session, ds_a.id)
        await registry.purge_dataset_data(session, ds_b.id)
