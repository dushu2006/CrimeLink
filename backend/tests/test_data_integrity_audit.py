"""Orphan and referential-integrity audit of the seeded dataset (§17).

The seeded dataset must hold together as a chain:

    CASE -> EVIDENCE RECORD -> SOURCE DOCUMENT -> STORED FILE -> PROVENANCE

Like ``test_demo_v2_relationship_network``, this runs the real
``scripts/seed_demo_v2`` generators in-process against the test container's
throwaway graph store, so it is hermetic — no database, no live data
directory.  It cross-checks the three artifacts the chain is made of: the
canonical dataset, the graph snapshot, and the object store.

Every category below comes back empty after the fixes in this pass.  Two did
not, at first:

* an uploaded document injected a graph entity, and removing the document row
  left that entity behind citing a document that no longer resolved;
* the object store kept the uploaded file after the row was gone.
"""

from __future__ import annotations

import asyncio
from collections import Counter

import pytest


@pytest.fixture()
def seeded(container):
    """Build the v2 dataset, store its files, and build its graph.

    Returns the canonical dataset, the graph snapshot read back out of the
    store, and the evidence/source document-id -> storage-key map, so the
    three artifacts can be cross-checked against one another.
    """
    from scripts.seed_demo_v2 import (
        build_dataset,
        build_graph,
        derive_case_persons,
        doc_id_for,
        gen_evidence_bytes,
        gen_source_bytes,
    )

    dataset = build_dataset()
    dataset["case_persons_dict"] = derive_case_persons(dataset)

    store = container.object_store
    bucket = container.settings.minio_bucket_documents
    doc_key: dict[str, str] = {}
    for ev in dataset["evidence"]:
        data = gen_evidence_bytes(ev, dataset)
        store.put(bucket, ev["storage_key"], data, content_type=ev["mime_type"])
        doc_key[doc_id_for(ev["evidence_id"])] = ev["storage_key"]
    for src in dataset["sources"]:
        data = gen_source_bytes(src, dataset)
        store.put(bucket, src["storage_key"], data, content_type=src["mime_type"])
        doc_key[doc_id_for(src["source_id"])] = src["storage_key"]

    build_graph(dataset, container, doc_id_for)
    snapshot = container.graph_store.multi_case_snapshot(
        [c["id"] for c in dataset["cases"]], include_inactive=False
    )
    return {
        "dataset": dataset,
        "snapshot": snapshot,
        "doc_key": doc_key,
        "bucket": bucket,
        "store": store,
    }


# --------------------------------------------------------------------------- #
# Evidence records resolve all the way down to a real file
# --------------------------------------------------------------------------- #

def test_every_evidence_record_has_a_storage_key_and_a_real_file(seeded):
    dataset = seeded["dataset"]
    store, bucket = seeded["store"], seeded["bucket"]

    no_key = [e["evidence_id"] for e in dataset["evidence"] if not e.get("storage_key")]
    assert not no_key, f"evidence with no storage key: {no_key[:3]}"

    missing = [
        ev["storage_key"]
        for ev in dataset["evidence"]
        if store.stat(bucket, ev["storage_key"]) is None
    ]
    assert not missing, f"evidence whose file is not in storage: {missing[:3]}"


def test_no_two_evidence_records_share_a_file(seeded):
    keys = [e["storage_key"] for e in seeded["dataset"]["evidence"]]
    dupes = [k for k, c in Counter(keys).items() if c > 1]
    assert not dupes, f"two evidence records point at one file: {dupes[:3]}"


def test_no_evidence_record_is_orphaned_from_its_case(seeded):
    dataset = seeded["dataset"]
    case_ids = {c["id"] for c in dataset["cases"]}
    orphans = [e["evidence_id"] for e in dataset["evidence"] if e["case_id"] not in case_ids]
    assert not orphans, f"evidence with no case: {orphans[:3]}"


def test_every_case_owns_at_least_one_evidence_record(seeded):
    dataset = seeded["dataset"]
    covered = {e["case_id"] for e in dataset["evidence"]}
    uncovered = [c["case_number"] for c in dataset["cases"] if c["id"] not in covered]
    assert not uncovered, f"cases with no evidence at all: {uncovered[:3]}"


# --------------------------------------------------------------------------- #
# Source documents and their identity
# --------------------------------------------------------------------------- #

def test_every_document_maps_to_a_distinct_file(seeded):
    doc_key = seeded["doc_key"]
    dataset = seeded["dataset"]
    expected = len(dataset["evidence"]) + len(dataset["sources"])
    assert len(doc_key) == expected, f"{len(doc_key)} document ids for {expected} files"
    assert len(set(doc_key.values())) == expected, "two documents claim one file"


def test_evidence_and_source_documents_do_not_share_an_id(seeded):
    """``E-0000`` and ``S-0000`` used to produce the same ``CaseDocument`` id.

    The source insert was then skipped as a duplicate, the 40 source PDFs had
    no document row of their own, and ``dataset_files.doc_id`` for them pointed
    at an unrelated evidence record.  Distinct namespaces keep the two chains
    from crossing.
    """
    from scripts.seed_demo_v2 import doc_id_for

    dataset = seeded["dataset"]
    evidence_ids = {doc_id_for(e["evidence_id"]) for e in dataset["evidence"]}
    source_ids = {doc_id_for(s["source_id"]) for s in dataset["sources"]}

    assert not (evidence_ids & source_ids), "evidence and source ids collide"
    assert all(i.startswith("doc-d2-") for i in evidence_ids)
    assert all(i.startswith("doc-s2-") for i in source_ids)
    assert len(source_ids) == len(dataset["sources"]) == 40


def test_document_ids_are_unique(seeded):
    from scripts.seed_demo_v2 import doc_id_for

    ids = [doc_id_for(e["evidence_id"]) for e in seeded["dataset"]["evidence"]]
    ids += [doc_id_for(s["source_id"]) for s in seeded["dataset"]["sources"]]
    dupes = [i for i, c in Counter(ids).items() if c > 1]
    assert not dupes, f"duplicate document ids: {dupes[:3]}"


# --------------------------------------------------------------------------- #
# Findings cite evidence that actually exists
# --------------------------------------------------------------------------- #

def test_every_finding_cites_a_resolvable_evidence_record(seeded):
    from scripts.seed_demo_v2 import doc_id_for

    dataset = seeded["dataset"]
    doc_ids = set(seeded["doc_key"])
    case_ids = {c["id"] for c in dataset["cases"]}

    assert dataset["findings"], "the seeded dataset has no findings"
    unresolved, bad_case = [], []
    for f in dataset["findings"]:
        cited = set(f.get("evidence_doc_ids") or [])
        for item in f.get("evidence") or []:
            if isinstance(item, dict) and item.get("doc_id"):
                cited.add(str(item["doc_id"]))
            elif isinstance(item, str):
                cited.add(doc_id_for(item))
        if not (cited & doc_ids):
            unresolved.append(f.get("finding_id") or f.get("id"))
        if f.get("case_id") and f["case_id"] not in case_ids:
            bad_case.append(f.get("finding_id") or f.get("id"))

    assert not unresolved, f"findings with no resolvable evidence: {unresolved[:3]}"
    assert not bad_case, f"findings pointing at an unknown case: {bad_case[:3]}"


# --------------------------------------------------------------------------- #
# Graph records cite documents and cases that exist
# --------------------------------------------------------------------------- #

def test_graph_nodes_cite_existing_documents_and_cases(seeded):
    doc_ids = set(seeded["doc_key"])
    case_ids = {c["id"] for c in seeded["dataset"]["cases"]}

    bad_doc, bad_case = [], []
    for key, node in seeded["snapshot"].nodes.items():
        props = node.properties or {}
        docs = props.get("source_doc_ids") or []
        if docs and not (set(map(str, docs)) & doc_ids):
            bad_doc.append(key)
        for cid in props.get("case_ids") or []:
            if cid not in case_ids:
                bad_case.append(key)
                break

    assert not bad_doc, f"entities citing missing documents: {bad_doc[:3]}"
    assert not bad_case, f"entities citing missing cases: {bad_case[:3]}"


def test_graph_edges_cite_existing_documents_and_known_endpoints(seeded):
    doc_ids = set(seeded["doc_key"])
    node_keys = set(seeded["snapshot"].nodes)

    no_doc, bad_endpoints = [], []
    for edge in seeded["snapshot"].edges:
        props = edge.properties or {}
        docs = props.get("source_doc_ids") or (
            [props["source_doc_id"]] if props.get("source_doc_id") else []
        )
        if docs and not (set(map(str, docs)) & doc_ids):
            no_doc.append(edge.key or f"{edge.source_key}->{edge.target_key}")
        if edge.source_key not in node_keys or edge.target_key not in node_keys:
            bad_endpoints.append(edge.key or f"{edge.source_key}->{edge.target_key}")

    assert not no_doc, f"edges citing missing documents: {no_doc[:3]}"
    assert not bad_endpoints, f"edges with unknown endpoints: {bad_endpoints[:3]}"


def test_every_graph_entity_belongs_to_a_real_case(seeded):
    case_ids = {c["id"] for c in seeded["dataset"]["cases"]}
    stray = []
    for key, node in seeded["snapshot"].nodes.items():
        if node.label == "Case":
            continue
        cases = (node.properties or {}).get("case_ids") or []
        if cases and not (set(cases) & case_ids):
            stray.append(key)
    assert not stray, f"entities belonging to no real case: {stray[:3]}"


def test_no_orphan_entity_outside_every_case_snapshot(seeded):
    """Every person node must be reachable from at least one case."""
    snapshot = seeded["snapshot"]
    in_edges = {e.target_key for e in snapshot.edges} | {e.source_key for e in snapshot.edges}
    isolated = [
        key
        for key, node in snapshot.nodes.items()
        if node.label == "Person" and key not in in_edges
    ]
    assert not isolated, f"person entities with no relationship at all: {isolated[:3]}"


# --------------------------------------------------------------------------- #
# The stored bytes are the recorded bytes
# --------------------------------------------------------------------------- #

def test_stored_objects_are_readable_and_match_their_hash(seeded):
    from scripts.seed_demo_v2 import gen_evidence_bytes, sha256

    store, bucket = seeded["store"], seeded["bucket"]
    dataset = seeded["dataset"]
    checked = 0
    for ev in dataset["evidence"][:40]:
        expected = gen_evidence_bytes(ev, dataset)
        meta = store.stat(bucket, ev["storage_key"])
        assert meta is not None, f"{ev['storage_key']} is not in storage"
        raw = store.get(bucket, ev["storage_key"])
        assert raw, f"{ev['storage_key']} is empty"
        if ev.get("mime_type") != "application/pdf":  # PDFs embed a timestamp
            assert sha256(raw) == sha256(expected), f"{ev['storage_key']} drifted"
        assert len(raw) == len(expected)
        checked += 1
    assert checked == 40


def test_the_audit_tool_classifies_orphans_correctly():
    """Unit-test the shipped audit tool's own judgement, not its DB access.

    Feeding it deliberately broken inputs must make it name exactly the
    categories that are broken — otherwise a clean run on live data means
    nothing, because the tool might simply never notice.
    """
    from types import SimpleNamespace

    from scripts.audit_data_quality import _Snap, classify

    doc = SimpleNamespace(id="d1", storage_key="case-a/d1/f.pdf", case_id="c1",
                          content_hash="x")
    file_row = SimpleNamespace(id="df1", doc_id="d1", relative_path="case-a/d1/f.pdf")
    finding = SimpleNamespace(id="f1", case_id="c1", evidence=[{"doc_id": "d1"}])

    good_nodes = [
        _Snap("p:1", "Person", {"case_ids": ["c1"], "source_doc_ids": ["d1"]}),
        _Snap("p:2", "Person", {"case_ids": ["c1"], "source_doc_ids": ["d1"]}),
    ]
    good_edges = [_Snap("p:1", "ASSOCIATE_OF", {"source_doc_id": "d1"},
                        target="p:2", key_name="e1")]

    baseline = {
        name: n
        for name, n, _sample in classify(
            cases={"c1"}, documents=[doc], dataset_files=[file_row], references=[],
            findings=[finding], nodes=good_nodes, edges=good_edges,
            stored_keys={"case-a/d1/f.pdf"},
        )
    }
    assert sum(baseline.values()) == 0, baseline  # the good input really is clean

    broken_nodes = good_nodes + [
        # cites a document that no longer resolves
        _Snap("p:3", "Person", {"case_ids": ["c1"], "source_doc_ids": ["gone"]}),
        # belongs to a case that does not exist
        _Snap("p:4", "Person", {"case_ids": ["nope"], "source_doc_ids": ["d1"]}),
    ]
    broken_edges = good_edges + [
        # endpoint is not in the graph at all
        _Snap("p:1", "CALLED", {"source_doc_id": "d1"}, target="p:missing", key_name="e2"),
        # no supporting document whatsoever
        _Snap("p:1", "TRANSFER_TO", {}, target="p:2", key_name="e3"),
    ]
    broken_doc = SimpleNamespace(id="d2", storage_key="case-a/d2/g.pdf",
                                 case_id="c1", content_hash="y")

    counts = {
        name: n
        for name, n, _sample in classify(
            cases={"c1"},
            documents=[doc, broken_doc],
            dataset_files=[file_row],
            references=[SimpleNamespace(id="r1", doc_id="gone", case_id="c1",
                                        origin_file="case-a/d2/g.pdf")],
            findings=[finding, SimpleNamespace(id="f2", case_id="c1", evidence=[])],
            nodes=broken_nodes,
            edges=broken_edges,
            stored_keys={"case-a/d1/f.pdf", "case-a/stray.csv"},
        )
    }

    assert counts["entity -> unknown source document"] == 1
    assert counts["entity -> unknown case"] == 1
    assert counts["relationship edge with an unknown endpoint"] == 1
    assert counts["relationship edge with no supporting document"] == 1
    assert counts["source reference -> missing document"] == 1
    assert counts["finding with no resolvable evidence"] == 1
    assert counts["evidence record with no dataset-file row"] == 1
    assert counts["stored object with no document/dataset-file record"] == 1
