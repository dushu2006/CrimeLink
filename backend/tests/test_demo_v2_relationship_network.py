"""End-to-end check of the person relationship graph over the **seeded v2 data**.

The other relationship tests build a small fixture graph.  This one runs the
real ``scripts/seed_demo_v2`` generators — 25 cases, 120 people, 90 phones,
35 accounts, 40 vehicles, 50 addresses, 15 organisations, 400 call records and
250 transfers — through the real graph builder, and asserts that what the
Investigate → Relationships canvas would draw is a *person* network with real
person-to-person edges, and that the ★ lands only on the people the dataset
explicitly marks.

It never touches a database: ``build_dataset`` / ``build_graph`` write only
into the test container's throwaway graph store.
"""

from __future__ import annotations

import pytest

from app.services.graph_service import _node_row
from app.services.person_relationships import (
    PERSON_RELATIONSHIP_LABELS,
    derive_person_relationships,
)

NON_CRIMINAL_ROLES = {
    "WITNESS",
    "VICTIM",
    "ASSOCIATE",
    "INFORMANT",
    "PERSON_OF_INTEREST",
    "SUSPECT",
}


@pytest.fixture()
def demo_v2_graph(container):
    from scripts.seed_demo_v2 import (
        build_dataset,
        build_graph,
        derive_case_persons,
        doc_id_for,
    )

    dataset = build_dataset()
    dataset["case_persons_dict"] = derive_case_persons(dataset)
    build_graph(dataset, container, doc_id_for)

    case_ids = [c["id"] for c in dataset["cases"]]
    snapshot = container.graph_store.multi_case_snapshot(case_ids, include_inactive=False)
    return dataset, snapshot


def test_v2_graph_yields_a_real_person_to_person_network(demo_v2_graph):
    dataset, snapshot = demo_v2_graph
    derived = derive_person_relationships(snapshot)

    counts = derived["counts"]
    # The seed creates 120 people; a few carry no case membership at all, so
    # they are outside every case-scoped snapshot.  What matters is that the
    # relationship graph accounts for every person that *is* in scope.
    in_scope_persons = sum(1 for n in snapshot.nodes.values() if n.label == "Person")
    assert len(dataset["persons"]) == 120
    assert counts["persons_total"] == in_scope_persons > 100, counts
    assert counts["relationships"] > 50, counts
    assert counts["persons_linked"] > 20, counts

    # A. Every primary node is a PERSON, and every edge endpoint is one of them.
    person_keys = set(counts and derived["persons"])
    assert person_keys <= derived["person_keys"]
    for key in person_keys:
        assert _node_row(snapshot.nodes[key])["label"] == "PERSON"
    for edge in derived["edges"]:
        assert edge["source"] in derived["person_keys"]
        assert edge["target"] in derived["person_keys"]
        assert edge["relationship_type"] in PERSON_RELATIONSHIP_LABELS
        assert edge["evidence_count"] >= 1

    # The network is not one relationship type: it carries the different ways
    # the dataset actually connects two people.
    by_type = counts["by_relationship_type"]
    assert {"KNOWN_ASSOCIATION", "COMMUNICATION", "SHARED_PHONE"} <= set(by_type), by_type


def test_v2_supporting_entities_are_evidence_not_nodes(demo_v2_graph):
    """Phones / accounts / vehicles / addresses exist, and stay out of the graph."""
    dataset, snapshot = demo_v2_graph
    derived = derive_person_relationships(snapshot)

    entity_labels = {snapshot.nodes[k].label for k in snapshot.nodes}
    # K. The entity layer is intact in the underlying graph.
    assert {"Phone", "BankAccount", "Vehicle", "Location", "Organization"} <= entity_labels

    node_keys = set(derived["persons"])
    for key in node_keys:
        assert snapshot.nodes[key].label == "Person"

    # ...but they do travel with the edge as supporting evidence.
    kinds = {item["kind"] for edge in derived["edges"] for item in edge["supporting_items"]}
    assert {"PHONE", "COMMUNICATION", "DIRECT_RECORD"} <= kinds


def test_v2_aggregates_multiple_records_into_one_edge(demo_v2_graph):
    _dataset, snapshot = demo_v2_graph
    derived = derive_person_relationships(snapshot)

    ids = [edge["id"] for edge in derived["edges"]]
    assert len(ids) == len(set(ids)), "no duplicate person-to-person edges"

    multi = [e for e in derived["edges"] if e["supporting_item_count"] >= 3]
    assert multi, "the v2 dataset has pairs supported by several independent records"
    assert max(e["supporting_item_count"] for e in derived["edges"]) >= 3
    assert any(e["cross_case"] for e in derived["edges"]), "master network must be cross-case"


def test_v2_star_lands_only_on_explicitly_confirmed_criminals(demo_v2_graph):
    dataset, snapshot = demo_v2_graph
    derived = derive_person_relationships(snapshot)

    by_person = {p["id"]: p for p in dataset["persons"]}
    starred = []
    for key in derived["persons"]:
        row = _node_row(snapshot.nodes[key])
        # is_criminal must be a pure function of the authoritative field.
        assert row["is_criminal"] == bool(row["criminal_status"])
        if row["is_criminal"]:
            starred.append((key, row))

    assert starred, "the v2 dataset explicitly confirms criminals; they must be starred"
    for key, row in starred:
        person = by_person[key]
        confirmed = (
            person["role"] == "ACCOMPLICE"
            or any(
                str(f.get("status", "")).upper() == "CONFIRMED" and key in f.get("entity_keys", [])
                for f in dataset["findings"]
            )
        )
        assert confirmed, f"{person['full_name']} is starred without explicit dataset support"

    # J. Nobody is starred for being a witness, a victim, an associate, an
    #    informant, a person of interest, or merely a suspect.
    for person in dataset["persons"]:
        if person["role"] in NON_CRIMINAL_ROLES and person["role"] != "ACCOMPLICE":
            confirmed_by_finding = any(
                str(f.get("status", "")).upper() == "CONFIRMED"
                and person["id"] in f.get("entity_keys", [])
                for f in dataset["findings"]
            )
            if not confirmed_by_finding:
                node = snapshot.nodes.get(person["id"])
                if node is None:
                    continue  # no case membership → outside the snapshot
                row = _node_row(node)
                assert row["is_criminal"] is False, person["full_name"]

    # The star is rare: it marks the people the dataset names, not the network.
    assert len(starred) < len(derived["persons"]) * 0.25, (
        f"{len(starred)} of {len(derived['persons'])} linked people starred — "
        "the star must not be handed out for being well connected"
    )


def test_v2_no_relationship_is_invented(demo_v2_graph):
    """Every edge traces back to at least one real record in the graph."""
    _dataset, snapshot = demo_v2_graph
    derived = derive_person_relationships(snapshot)

    def is_person(key: str) -> bool:
        node = snapshot.nodes.get(key)
        return node is not None and node.label == "Person"

    edge_keys = {
        (e.source_key, e.rel_type, e.target_key) for e in snapshot.edges
    } | {(e.target_key, e.rel_type, e.source_key) for e in snapshot.edges}
    entity_owners: dict[str, set[str]] = {}
    for edge in snapshot.edges:
        for person, entity in (
            (edge.source_key, edge.target_key),
            (edge.target_key, edge.source_key),
        ):
            if is_person(person) and not is_person(entity):
                entity_owners.setdefault(entity, set()).add(person)

    for relationship in derived["edges"]:
        source, target = relationship["source"], relationship["target"]
        for item in relationship["supporting_items"]:
            if item["kind"] == "DIRECT_RECORD":
                rel = item["rel_types"][0]
                assert (source, rel, target) in edge_keys
            elif item["kind"] in {"COMMUNICATION", "TRANSACTION"}:
                via = item["via"]
                assert (via[0], item["rel_types"][0], via[1]) in edge_keys
                left, right = entity_owners.get(via[0], set()), entity_owners.get(via[1], set())
                # Edge endpoints are stored in a stable sorted order, so accept
                # either orientation of the bridge.
                assert (source in left and target in right) or (
                    source in right and target in left
                ), f"{source} ↔ {target} not backed by {via}"
            else:
                owners = entity_owners.get(item["ref"], set())
                assert source in owners and target in owners, (
                    f"{source} ↔ {target} do not share {item['ref']}"
                )
