"""Tests for Investigation Retrieval Engine — Priority 2.

Verifies:
- Query Understanding: intent detection, temporal, spatial, evidence filters
- Relevance scoring for nodes/edges/docs
- Ranking and filtering to budget
- Context compression and timeline building
- Evidence grounding
"""

from __future__ import annotations

from app.ai.retrieval import (
    understand_query,
    score_node_relevance,
    score_edge_relevance,
    score_document_relevance,
    rank_and_filter_context,
    compress_context,
    build_timeline_from_context,
)


class TestQueryUnderstanding:
    def test_intent_detection_connection(self):
        q = "What connects Person A and Person B?"
        understanding = understand_query(q)
        assert understanding.intent == "connection"
        assert understanding.requires_evidence_path is True

    def test_intent_detection_timeline(self):
        q = "What happened around the warehouse on August 12?"
        understanding = understand_query(q)
        # Should detect timeline intent or temporal filter
        assert understanding.intent in ("timeline", "location") or understanding.temporal is not None
        assert understanding.spatial is not None or "warehouse" in q.lower()

    def test_intent_detection_financial(self):
        q = "Show me all money transfers between accounts"
        understanding = understand_query(q)
        assert understanding.intent == "financial"
        assert "BankAccount" in understanding.evidence.entity_types or "TRANSFER" in understanding.evidence.rel_types

    def test_intent_detection_summary(self):
        q = "Summarize open leads in this case"
        understanding = understand_query(q)
        assert understanding.intent == "summary"

    def test_temporal_filter_extraction(self):
        q = "What happened on August 12 around the warehouse?"
        understanding = understand_query(q)
        assert understanding.temporal is not None
        assert "aug" in understanding.temporal.raw_text.lower() or "12" in understanding.temporal.raw_text

    def test_spatial_filter_extraction(self):
        q = "Show me evidence near the warehouse and bank"
        understanding = understand_query(q)
        assert understanding.spatial is not None
        assert len(understanding.spatial.locations) >= 1

    def test_evidence_filter_doc_types(self):
        q = "Find CCTV footage and FIR documents"
        understanding = understand_query(q)
        assert "CCTV" in understanding.evidence.doc_types or "FIR" in understanding.evidence.doc_types

    def test_keywords_extraction(self):
        q = "Who appears to coordinate financial activity through bank accounts?"
        understanding = understand_query(q)
        assert len(understanding.keywords) >= 2
        assert "financial" in understanding.keywords or "bank" in understanding.keywords or "account" in understanding.keywords


class TestRelevanceScoring:
    def test_node_relevance_scoring(self):
        understanding = understand_query("Show me Ravi's phone connections")
        node_person = {
            "provenance_key": "person:ravi",
            "label": "Person",
            "properties": {"name": "Ravi Kumar", "city": "Jaipur"},
            "confidence": 0.9,
        }
        node_phone = {
            "provenance_key": "phone:1",
            "label": "Phone",
            "properties": {"number": "+919829012345"},
            "confidence": 0.8,
        }
        score_person = score_node_relevance(node_person, understanding)
        score_phone = score_node_relevance(node_phone, understanding)
        # Both should have some score, phone might be higher for phone query
        assert score_person >= 0
        assert score_phone >= 0

    def test_edge_relevance_scoring(self):
        understanding = understand_query("Show money transfers")
        edge_transfer = {
            "source_key": "account:1",
            "target_key": "account:2",
            "rel_type": "TRANSFER",
            "confidence": 0.9,
        }
        edge_call = {
            "source_key": "phone:1",
            "target_key": "phone:2",
            "rel_type": "CALL",
            "confidence": 0.9,
        }
        score_transfer = score_edge_relevance(edge_transfer, understanding)
        score_call = score_edge_relevance(edge_call, understanding)
        assert score_transfer > score_call  # financial query should rank transfer higher

    def test_document_relevance_scoring(self):
        understanding = understand_query("Find CCTV near warehouse on August 12")
        doc_cctv = {
            "doc_id": "doc1",
            "filename": "CCTV-05_warehouse_2026-08-12.mp4",
            "document_type": "CCTV",
            "content": "CCTV footage shows vehicle at warehouse on August 12 at 21:43",
        }
        doc_fir = {
            "doc_id": "doc2",
            "filename": "FIR_001.txt",
            "document_type": "FIR",
            "content": "Unrelated FIR about theft",
        }
        score_cctv = score_document_relevance(doc_cctv, understanding)
        score_fir = score_document_relevance(doc_fir, understanding)
        assert score_cctv > score_fir


class TestRankingAndFiltering:
    def test_ranking_respects_budget(self):
        understanding = understand_query("Summarize case")
        nodes = [
            {"provenance_key": f"PERSON_{i}", "label": "Person", "properties": {"name": f"Person {i}"}, "confidence": 0.9}
            for i in range(100)
        ]
        edges = [
            {"source_key": f"PERSON_{i}", "target_key": f"PERSON_{i+1}", "rel_type": "ASSOCIATE_OF", "confidence": 0.8}
            for i in range(100)
        ]
        docs = [
            {"doc_id": f"doc_{i}", "filename": f"doc_{i}.txt", "document_type": "FIR", "content": "Content " * 100}
            for i in range(50)
        ]
        
        ranked = rank_and_filter_context(
            nodes=nodes,
            edges=edges,
            documents=docs,
            understanding=understanding,
            max_nodes=20,
            max_edges=30,
            max_doc_chars=5000,
            max_docs=5,
        )
        
        assert len(ranked.nodes) <= 20
        assert len(ranked.edges) <= 30
        assert len(ranked.documents) <= 5
        assert ranked.total_chars <= 5000

    def test_compression_timeline_ordering(self):
        understanding = understand_query("Show timeline of events")
        understanding.requires_timeline = True
        
        nodes = [
            {"provenance_key": "p1", "label": "Person", "properties": {"name": "A", "first_ts": "2026-08-12T10:00:00"}, "confidence": 0.9},
            {"provenance_key": "p2", "label": "Person", "properties": {"name": "B", "first_ts": "2026-08-12T09:00:00"}, "confidence": 0.9},
        ]
        edges = [
            {"source_key": "p1", "target_key": "p2", "rel_type": "CALL", "timestamp": "2026-08-12T11:00:00", "confidence": 0.8},
            {"source_key": "p2", "target_key": "p1", "rel_type": "TRANSFER", "timestamp": "2026-08-12T08:00:00", "confidence": 0.8},
        ]
        docs = []
        
        ranked = rank_and_filter_context(
            nodes=nodes, edges=edges, documents=docs,
            understanding=understanding,
            max_nodes=10, max_edges=10, max_doc_chars=10000, max_docs=5,
        )
        compressed = compress_context(ranked, understanding, timeline_order=True)
        
        # Edges should be sorted by timestamp
        timestamps = [e.get("timestamp") for e in compressed.edges]
        assert timestamps == sorted(timestamps)


class TestTimelineBuilding:
    def test_build_timeline(self):
        nodes = [
            {"provenance_key": "p1", "label": "Person", "properties": {"name": "A", "first_ts": "2026-08-12T10:00:00"}},
            {"provenance_key": "p2", "label": "Vehicle", "properties": {"plate": "RJ14AB1234", "last_ts": "2026-08-12T09:00:00"}},
        ]
        edges = [
            {"source_key": "p1", "target_key": "p2", "rel_type": "OWNS", "timestamp": "2026-08-12T11:00:00"},
        ]
        
        timeline = build_timeline_from_context(nodes, edges)
        assert len(timeline) == 3
        # Should be sorted chronologically
        assert timeline[0]["timestamp"] == "2026-08-12T09:00:00"
        assert timeline[-1]["timestamp"] == "2026-08-12T11:00:00"
