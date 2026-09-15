"""
Scale tests and realistic graph distributions
Tests: A→B, A→X→B, A→X→Y→B, A→100 irrelevant entities→B, dense graphs
Critical metric: Does CrimeLink find correct person relationship without drowning in irrelevant data?
"""
from app.domain.models import CaseGraphSnapshot, GraphNode, GraphEdge
from app.ai.person_graph_rag import person_graph_rag_retrieval

def _node(key, name, label="Person"):
    return GraphNode(provenance_key=key, label=label, properties={"name": name, "case_ids": ["c1"]})

def _edge(src, tgt, rel, doc="doc-001", **props):
    p = {"source_doc_id": doc}
    p.update(props)
    return GraphEdge(source_key=src, target_key=tgt, rel_type=rel, properties=p)

def test_direct_a_b():
    nodes = {
        "person:A": _node("person:A", "Alice", "Person"),
        "person:B": _node("person:B", "Bob", "Person"),
    }
    edges = [_edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-1")]
    snap = CaseGraphSnapshot(case_id="direct", nodes=nodes, edges=edges)
    result = person_graph_rag_retrieval(snap, "Alice Bob", max_persons=10, max_relationships=10)
    assert len(result.relationships) >= 1
    assert result.relationships[0].hop_count == 1

def test_a_x_b():
    nodes = {
        "person:A": _node("person:A", "Alice", "Person"),
        "person:B": _node("person:B", "Bob", "Person"),
        "phone:X": _node("phone:X", "Phone X", "Phone"),
    }
    edges = [
        _edge("person:A", "phone:X", "USES_PHONE", doc="doc-1"),
        _edge("phone:X", "person:B", "USES_PHONE", doc="doc-1"),
    ]
    snap = CaseGraphSnapshot(case_id="a-x-b", nodes=nodes, edges=edges)
    result = person_graph_rag_retrieval(snap, "Alice Bob", max_persons=10, max_relationships=10, max_hops=2)
    assert len(result.relationships) >= 1
    # Should be PERSON→PERSON only, not phone as final
    for r in result.relationships:
        assert "person:" in r.source_real_key
        assert "person:" in r.target_real_key

def test_a_x_y_b():
    nodes = {
        "person:A": _node("person:A", "Alice", "Person"),
        "person:B": _node("person:B", "Bob", "Person"),
        "phone:X": _node("phone:X", "Phone X", "Phone"),
        "phone:Y": _node("phone:Y", "Phone Y", "Phone"),
    }
    edges = [
        _edge("person:A", "phone:X", "USES_PHONE", doc="doc-1"),
        _edge("phone:X", "phone:Y", "CALLED", doc="doc-1"),
        _edge("phone:Y", "person:B", "USES_PHONE", doc="doc-1"),
    ]
    snap = CaseGraphSnapshot(case_id="a-x-y-b", nodes=nodes, edges=edges)
    result = person_graph_rag_retrieval(snap, "Alice Bob", max_persons=10, max_relationships=10, max_hops=3)
    assert len(result.relationships) >= 1
    assert result.relationships[0].hop_count >= 2

def test_a_100_irrelevant_b():
    nodes = {
        "person:A": _node("person:A", "Alice", "Person"),
        "person:B": _node("person:B", "Bob", "Person"),
    }
    for i in range(100):
        nodes[f"person:I{i}"] = _node(f"person:I{i}", f"Irrelevant {i}", "Person")
        nodes[f"loc:{i}"] = _node(f"loc:{i}", f"Location {i}", "Location")
    
    edges = [_edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-1")]
    for i in range(100):
        edges.append(_edge(f"person:I{i}", f"loc:{i}", "LOCATED_AT", doc=f"doc-irrelevant-{i}"))
    
    snap = CaseGraphSnapshot(case_id="100-irrelevant", nodes=nodes, edges=edges)
    result = person_graph_rag_retrieval(snap, "Alice Bob connection", max_persons=20, max_relationships=10)
    
    # Should find correct relationship without drowning in irrelevant
    assert len(result.relationships) >= 1
    # Should not include irrelevant persons in top results (adaptive 10-40)
    assert len(result.persons) <= 40
    # Relevant persons should be Alice and Bob
    person_keys = [p.provenance_key for p in result.persons]
    assert "person:A" in person_keys or "person:B" in person_keys

def test_dense_person_hundreds_connections():
    nodes = {"person:Center": _node("person:Center", "Center", "Person")}
    for i in range(200):
        nodes[f"person:{i}"] = _node(f"person:{i}", f"Person {i}", "Person")
    
    edges = []
    for i in range(200):
        edges.append(_edge("person:Center", f"person:{i}", "ASSOCIATE_OF", doc=f"doc-{i}"))
    
    snap = CaseGraphSnapshot(case_id="dense", nodes=nodes, edges=edges)
    result = person_graph_rag_retrieval(snap, "Center connections", max_persons=40, max_relationships=20)
    
    # Even though Center has 200 connections, should cap at adaptive limit
    assert len(result.persons) <= 40
    assert len(result.relationships) <= 20
    # Should still be fast
    assert result.metrics.total_ms < 2000

def test_sparse_vs_dense_retrieval_quality():
    # Sparse: few connections, should find them
    nodes_sparse = {
        "person:A": _node("person:A", "Alice", "Person"),
        "person:B": _node("person:B", "Bob", "Person"),
    }
    edges_sparse = [_edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-1")]
    snap_sparse = CaseGraphSnapshot(case_id="sparse", nodes=nodes_sparse, edges=edges_sparse)
    result_sparse = person_graph_rag_retrieval(snap_sparse, "Alice Bob", max_persons=10, max_relationships=10)
    
    # Dense: many irrelevant, should still find Alice-Bob
    nodes_dense = dict(nodes_sparse)
    for i in range(50):
        nodes_dense[f"person:D{i}"] = _node(f"person:D{i}", f"Dense {i}", "Person")
    edges_dense = list(edges_sparse)
    for i in range(50):
        edges_dense.append(_edge(f"person:D{i}", f"person:D{(i+1)%50}", "ASSOCIATE_OF", doc=f"doc-dense-{i}"))
    snap_dense = CaseGraphSnapshot(case_id="dense2", nodes=nodes_dense, edges=edges_dense)
    result_dense = person_graph_rag_retrieval(snap_dense, "Alice Bob", max_persons=10, max_relationships=10)
    
    # Both should find Alice-Bob, dense should not drown it
    assert len(result_sparse.relationships) >= 1
    assert len(result_dense.relationships) >= 1
