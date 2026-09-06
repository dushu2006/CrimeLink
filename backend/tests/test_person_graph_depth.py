"""The person graph explores to any depth the data actually supports.

Three hops is a *default*, not a ceiling.  These tests build graphs whose real
connectivity is far deeper than three and assert that the traversal follows
them: a 60-hop chain is walked to hop 60, a 120-hop chain to hop 120, and a
request for more hops than exist returns the whole connected component and
says so rather than failing or silently clipping.

They also pin the properties that make unbounded depth *safe*: a cycle is
visited once, an edge is emitted once, and the cost of asking for depth 5000
on a shallow graph is the cost of the graph, not of the number.
"""

from __future__ import annotations

import time

import pytest

from app.domain.models import GraphEdge, GraphNode
from app.security.deps import JurisdictionScope, Principal
from app.services.graph_service import DEFAULT_PERSON_NETWORK_DEPTH, GraphService

DOC = "doc-depth-fixture"


def _person(index: int, case_id: str) -> GraphNode:
    return GraphNode(
        provenance_key=f"person:{index}",
        label="Person",
        properties={
            "name": f"Person {index:04d}",
            "case_ids": [case_id],
            "source_doc_id": DOC,
            "confidence": 1.0,
        },
    )


def _link(a: str, b: str, case_id: str, discriminator: str) -> GraphEdge:
    return GraphEdge(
        source_key=a,
        target_key=b,
        rel_type="ASSOCIATE_OF",
        properties={"source_doc_id": DOC, "case_ids": [case_id], "confidence": 0.9},
        discriminator=discriminator,
    )


def _chain(container, case_id: str, length: int) -> None:
    """A -> B -> C -> ... : connectivity of exactly ``length`` hops."""
    nodes = [_person(i, case_id) for i in range(length + 1)]
    edges = [
        _link(f"person:{i}", f"person:{i + 1}", case_id, f"chain-{i}")
        for i in range(length)
    ]
    container.injector.inject_nodes(nodes)
    container.injector.inject_edges(edges)


def _scope(users) -> JurisdictionScope:
    principal = Principal(users["INV-0001"])
    return JurisdictionScope(principal, granted_jurisdictions=set(), granted_case_ids=set())


async def _network(db, users, case, container, depth: int, **kwargs):
    return await GraphService(container).person_centric_network(
        db, _scope(users), case.id, "person:0", depth=depth, **kwargs
    )


# ---------------------------------------------------------------------------
# Depth is honoured, not capped
# ---------------------------------------------------------------------------


async def test_default_depth_is_three(db, users, case, container):
    """Three hops remains the default view."""
    _chain(container, case.id, 10)
    payload = await GraphService(container).person_centric_network(
        db, _scope(users), case.id, "person:0"
    )
    assert DEFAULT_PERSON_NETWORK_DEPTH == 3
    assert payload["requested_depth"] == 3
    assert payload["max_depth_reached"] == 3
    # person:0 plus one node per hop.
    assert payload["counts"]["nodes"] == 4
    assert payload["exhausted"] is False, "a 10-hop chain is not exhausted at 3"


@pytest.mark.parametrize("hops", [5, 10, 13, 20, 26, 50, 100])
async def test_requested_depth_is_reached_for_any_hop_count(
    db, users, case, container, hops
):
    """5, 10, 13, 20, 26, 50 and 100 hops all traverse the whole chain.

    These are the depths the previous implementation refused outright: the
    API rejected anything above 3 and the service clamped to 3 behind it.
    """
    _chain(container, case.id, hops)
    payload = await _network(db, users, case, container, hops)

    assert payload["requested_depth"] == hops
    assert payload["max_depth_reached"] == hops
    assert payload["counts"]["nodes"] == hops + 1
    assert payload["counts"]["edges"] == hops
    assert payload["layers"][str(hops)] == 1, "the furthest hop has exactly one node"
    assert payload["truncated"] is False


async def test_depth_beyond_one_hundred_still_traverses(db, users, case, container):
    """There is no hidden ceiling at 100 either."""
    _chain(container, case.id, 150)
    payload = await _network(db, users, case, container, 150)
    assert payload["max_depth_reached"] == 150
    assert payload["counts"]["nodes"] == 151


async def test_api_accepts_any_depth(client, investigator_headers, case, container):
    """The HTTP layer must not re-impose the cap the service dropped."""
    _chain(container, case.id, 40)
    for hops in (3, 26, 100, 5000):
        response = client.get(
            f"/api/v1/cases/{case.id}/network/person:0?depth={hops}",
            headers=investigator_headers,
        )
        assert response.status_code == 200, (hops, response.text)
        assert response.json()["requested_depth"] == hops
    # Zero and negatives are still rejected: they are not exploration values.
    assert (
        client.get(
            f"/api/v1/cases/{case.id}/network/person:0?depth=0",
            headers=investigator_headers,
        ).status_code
        == 422
    )


# ---------------------------------------------------------------------------
# Stopping conditions
# ---------------------------------------------------------------------------


async def test_traversal_stops_when_nothing_more_is_reachable(
    db, users, case, container
):
    """Asking for more hops than exist returns everything reachable."""
    _chain(container, case.id, 7)
    payload = await _network(db, users, case, container, 500)

    assert payload["requested_depth"] == 500
    assert payload["max_depth_reached"] == 7, "stops at the real edge of the graph"
    assert payload["exhausted"] is True
    assert payload["counts"]["nodes"] == 8
    assert payload["truncated"] is False, "reaching the end is not truncation"


async def test_deep_request_on_shallow_graph_is_cheap(db, users, case, container):
    """Depth 10_000 on a 7-hop graph costs what the graph costs."""
    _chain(container, case.id, 7)
    started = time.perf_counter()
    payload = await _network(db, users, case, container, 10_000)
    elapsed = time.perf_counter() - started
    assert payload["max_depth_reached"] == 7
    assert elapsed < 2.0, f"traversal scaled with the hop number, not the graph ({elapsed:.2f}s)"


# ---------------------------------------------------------------------------
# Safety: cycles, duplicates, isolation
# ---------------------------------------------------------------------------


async def test_cycles_terminate_and_are_visited_once(db, users, case, container):
    """A ring graph must not loop forever or double-count its nodes."""
    size = 12
    nodes = [_person(i, case.id) for i in range(size)]
    edges = [
        _link(f"person:{i}", f"person:{(i + 1) % size}", case.id, f"ring-{i}")
        for i in range(size)
    ]
    container.injector.inject_nodes(nodes)
    container.injector.inject_edges(edges)

    payload = await _network(db, users, case, container, 100_000)

    assert payload["counts"]["nodes"] == size, "each node appears exactly once"
    assert payload["counts"]["edges"] == size, "each edge appears exactly once"
    assert payload["exhausted"] is True
    # A ring of 12 has an eccentricity of 6 from any node.
    assert payload["max_depth_reached"] == 6
    keys = [node["provenance_key"] for node in payload["nodes"]]
    assert len(keys) == len(set(keys)), "no duplicate expansion"


async def test_edges_are_never_emitted_twice(db, users, case, container):
    """A pair reachable from both ends yields one edge, not two."""
    container.injector.inject_nodes([_person(i, case.id) for i in range(3)])
    container.injector.inject_edges(
        [
            _link("person:0", "person:1", case.id, "a"),
            _link("person:1", "person:2", case.id, "b"),
            _link("person:2", "person:0", case.id, "c"),
        ]
    )
    payload = await _network(db, users, case, container, 50)
    keys = [edge["key"] for edge in payload["edges"]]
    assert len(keys) == len(set(keys)) == 3


async def test_deep_traversal_preserves_case_isolation(
    db, users, case, other_case, container
):
    """Depth 1000 must not leak a neighbouring case's nodes into the result."""
    _chain(container, case.id, 5)
    container.injector.inject_nodes(
        [
            GraphNode(
                provenance_key="person:outsider",
                label="Person",
                properties={
                    "name": "Outsider",
                    "case_ids": [other_case.id],
                    "source_doc_id": DOC,
                },
            )
        ]
    )
    container.injector.inject_edges(
        [_link("person:5", "person:outsider", case.id, "bridge")]
    )

    payload = await _network(db, users, case, container, 1000)
    returned = {node["provenance_key"] for node in payload["nodes"]}
    assert "person:outsider" not in returned, (
        "the case snapshot is the isolation boundary and deep traversal must "
        "respect it"
    )


async def test_optional_limit_truncates_only_when_asked(db, users, case, container):
    """No limit means no truncation; an explicit limit is honoured and flagged."""
    _chain(container, case.id, 30)

    unbounded = await _network(db, users, case, container, 30)
    assert unbounded["truncated"] is False
    assert unbounded["counts"]["nodes"] == 31
    assert unbounded["node_limit"] is None

    bounded = await _network(db, users, case, container, 30, limit=10)
    assert bounded["truncated"] is True
    assert bounded["counts"]["nodes"] <= 10
