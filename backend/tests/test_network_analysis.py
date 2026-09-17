"""Explicit network analysis over the three graph scopes.

Pins: master/case/person scoping, dataset isolation, entity-type safety
(BANK_ACCOUNT stays BANK_ACCOUNT), and criminal-status safety (high
betweenness is not criminality; only source-derived legal status produces the
criminal flag). The reasoning model is keyless in tests, so the deterministic
analysis must survive an AI_UNAVAILABLE outcome.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import select

from app.datasets import registry
from app.db.base import new_uuid
from app.db.models import Case, Dataset
from app.db.session import async_session
from app.domain.enums import CaseStatus
from app.domain.models import GraphEdge, GraphNode
from app.investigator.network_analysis import analyze_network
from app.security.deps import JurisdictionScope, Principal

DOC = "doc-network-analysis"


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


async def _world(db, container, *, name: str, people: int = 6) -> tuple[Dataset, Case, Case]:
    ds = await _active_dataset(db, name)
    c1 = await _case(db, ds, f"{name}-1")
    c2 = await _case(db, ds, f"{name}-2")
    nodes = []
    for i in range(1, people + 1):
        cases = [c1.id] if i <= people - 2 else [c1.id, c2.id]
        nodes.append(_node(f"p{i}", f"Person {i}", "Person", cases, ds.id))
    nodes.append(_node("ph1", "+919855500001", "Phone", [c1.id], ds.id))
    nodes.append(_node("ba1", "ACCT-555", "BankAccount", [c2.id], ds.id))
    container.injector.inject_nodes(nodes)
    edges = [_edge("p1", "ph1", "USES_PHONE", "w-a")]
    for i in range(2, people + 1):
        edges.append(_edge("p1", f"p{i}", "ASSOCIATE_OF", f"w-{i}"))
    edges.append(_edge(f"p{people - 1}", "ba1", "OWNS_ACCOUNT", "w-ba"))
    container.injector.inject_edges(edges)
    return ds, c1, c2


async def test_master_scope_returns_metrics_communities_and_cross_case(db, users, container):
    await _world(db, container, name="Master NA")
    result = await analyze_network(db, _scope(users), Principal(users["INV-0001"]), mode="master")
    assert result["status"] in {"COMPLETED", "AI_UNAVAILABLE", "AI_TIMEOUT", "AI_INVALID_RESPONSE"}
    response = result["response"]
    assert response["scope"]["mode"] == "master"
    assert response["scope"]["case_ids"], "master scope spans all active cases"
    analysis = result["analysis"]
    assert analysis["mode"] == "master"
    assert analysis["graph"]["nodes"] > 0 and analysis["graph"]["edges"] > 0
    for metric in ("betweenness", "degree", "weighted_degree", "pagerank"):
        assert isinstance(analysis["metrics"][metric], list)
        assert analysis["metrics"]["explanations"][metric], "every metric carries its meaning"
    assert analysis["communities"], "communities were detected"
    # Cross-case persons: the last two persons live in two cases.
    cross_keys = {item["key"] for item in analysis["cross_case"]}
    assert cross_keys, "cross-case persons were identified"


async def test_case_scope_is_isolated_to_the_selected_case(db, users, container):
    ds, c1, c2 = await _world(db, container, name="Case NA")
    result = await analyze_network(
        db, _scope(users), Principal(users["INV-0001"]), mode="case", case_id=c1.id
    )
    response = result["response"]
    assert response["scope"]["mode"] == "case"
    assert response["scope"]["case_id"] == c1.id
    assert response["scope"]["case_ids"] == [c1.id]
    # p2 is only in c1; the analysis must not cite c2-only entities.
    c2_only = [
        e
        for e in response["entities"]
        if c2.id in (e.get("case_ids") or []) and c1.id not in (e.get("case_ids") or [])
    ]
    assert not c2_only, "case-scoped analysis must not surface other cases' entities"


async def test_person_scope_is_person_centric(db, users, container):
    await _world(db, container, name="Person NA")
    result = await analyze_network(
        db, _scope(users), Principal(users["INV-0001"]), mode="person", person_key="p1"
    )
    response = result["response"]
    assert response["scope"]["mode"] == "person"
    assert result["analysis"]["person_key"] == "p1"
    # The selected person is the central subject (first entity).
    assert response["entities"] and response["entities"][0]["canonical_id"] == "p1"


async def test_entity_types_are_preserved_in_metrics(db, users, container):
    ds = await _active_dataset(db, "Type NA")
    c1 = await _case(db, ds, "TYPE-NA-1")
    # A bank account with many connections: structurally prominent, but still
    # a BANK_ACCOUNT — never re-classified as PERSON.
    container.injector.inject_nodes(
        [
            _node("ba1", "ACCT-100", "BankAccount", [c1.id], ds.id),
            _node("loc1", "New Delhi", "Location", [c1.id], ds.id),
            *[
                _node(f"p{i}", f"Person {i}", "Person", [c1.id], ds.id)
                for i in range(1, 6)
            ],
        ]
    )
    edges = [
        _edge("ba1", f"p{i}", "OWNS_ACCOUNT", f"t-{i}")
        if i % 2 == 0
        else _edge(f"p{i}", "ba1", "TRANSFER_TO", f"t-{i}")
        for i in range(1, 6)
    ]
    edges.append(_edge("p1", "loc1", "LOCATED_AT", "t-loc"))
    container.injector.inject_edges(edges)

    # MASTER is the entity/evidence deep-dive, so entity rows are reported
    # there — and they must keep their real type rather than being coerced.
    result = await analyze_network(
        db, _scope(users), Principal(users["INV-0001"]), mode="master"
    )
    analysis = result["analysis"]
    assert analysis["subject"] == "ENTITY"
    all_rows = (
        analysis["metrics"]["betweenness"]
        + analysis["metrics"]["degree"]
        + analysis["metrics"]["pagerank"]
    )
    by_key = {row["key"]: row for row in all_rows}
    assert by_key["ba1"]["label"] == "BANK_ACCOUNT", "BANK_ACCOUNT must stay BANK_ACCOUNT"
    assert by_key["loc1"]["label"] == "LOCATION", "LOCATION must stay LOCATION"

    # CASE is investigator-facing and person-centric: a hub bank account must
    # not be presented as an investigative subject at all.  It stays in the
    # evidence graph (MASTER above) and in the supporting basis of the
    # person↔person findings it explains — never as a ranked metric row here.
    case_result = await analyze_network(
        db, _scope(users), Principal(users["INV-0001"]), mode="case", case_id=c1.id
    )
    case_analysis = case_result["analysis"]
    assert case_analysis["subject"] == "PERSON"
    case_rows = (
        case_analysis["metrics"]["betweenness"]
        + case_analysis["metrics"]["degree"]
        + case_analysis["metrics"]["pagerank"]
        + case_analysis["metrics"]["weighted_degree"]
        + case_analysis["cross_case"]
    )
    assert case_rows, "person-centric metrics must still rank the people"
    non_person = [row for row in case_rows if row["label"] != "PERSON"]
    assert non_person == [], f"person-centric analysis surfaced {non_person[:3]}"


async def test_criminal_status_is_source_derived_only(db, users, container):
    ds = await _active_dataset(db, "Criminal NA")
    c1 = await _case(db, ds, "CRIM-NA-1")
    container.injector.inject_nodes(
        [
            _node("hub", "High Betweenness", "Person", [c1.id], ds.id),
            _node("conv", "Convicted Person", "Person", [c1.id], ds.id, criminal_status="convicted"),
            *[_node(f"leaf{i}", f"Leaf {i}", "Person", [c1.id], ds.id) for i in range(1, 5)],
        ]
    )
    container.injector.inject_edges(
        [_edge("hub", f"leaf{i}", "ASSOCIATE_OF", f"c-{i}") for i in range(1, 5)]
        + [_edge("conv", "hub", "ASSOCIATE_OF", "c-conv")]
    )

    result = await analyze_network(db, _scope(users), Principal(users["INV-0001"]), mode="case", case_id=c1.id)
    analysis = result["analysis"]
    all_rows = analysis["metrics"]["betweenness"] + analysis["metrics"]["degree"]
    by_key = {row["key"]: row for row in all_rows}
    # High structural position alone must never mark a person criminal.
    assert by_key["hub"]["is_criminal"] is False
    assert by_key["conv"]["is_criminal"] is True, "only source-derived status sets the flag"


async def test_network_analysis_endpoint_returns_a_pollable_job(
    client, investigator_headers, users, container
):
    from app.db.session import async_session as _as

    async with _as() as session:
        ds = await _active_dataset(session, "Endpoint NA")
        c1 = await _case(session, ds, "EP-NA-1")
    container.injector.inject_nodes(
        [
            _node("p1", "Person One", "Person", [c1.id], ds.id),
            _node("p2", "Person Two", "Person", [c1.id], ds.id),
            _node("ph1", "+919866600001", "Phone", [c1.id], ds.id),
        ]
    )
    container.injector.inject_edges(
        [_edge("p1", "p2", "ASSOCIATE_OF", "ep-a"), _edge("p1", "ph1", "USES_PHONE", "ep-b")]
    )

    response = client.post(
        "/api/v1/investigate/network-analysis",
        headers=investigator_headers,
        json={"mode": "master"},
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]

    # Poll the job over HTTP, mirroring the dataset-jobs fallback path: the
    # test loop must never reach into the app's async engine from a second loop.
    deadline = time.time() + 30
    row = None
    while time.time() < deadline:
        row = client.get(
            f"/api/v1/investigate/jobs/{job_id}", headers=investigator_headers
        ).json()
        if row.get("terminal"):
            break
        await asyncio.sleep(0.1)
    assert row is not None and row.get("terminal"), f"job never finished: {row}"
    result = row.get("result") or {}
    assert "response" in result and "analysis" in result, row
    assert result["analysis"]["graph"]["nodes"] > 0
