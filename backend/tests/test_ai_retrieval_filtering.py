"""Tests for Phase 2 (document relevance filtering) and Phase 3 (query-to-entity detection).

Verifies:
- Document filtering respects total char budget and pulls only subgraph-connected docs when target_key present
- Entity detection triggers narrower retrieval path for specific person/phone/vehicle mentions
- General questions still return reasonable case-wide context (fallback path)
- Structured logging includes required metrics
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.gateway import AIGateway
from app.config import Settings


def _gen_nodes(n: int):
    return [
        {
            "provenance_key": f"PERSON_{i:04d}",
            "label": "Person",
            "properties": {
                "name": f"Person {i}",
                "full_name": f"Person {i} Fullname",
                "phone": f"+919800000{i:03d}",
                "source_doc_ids": [f"doc_{i % 10}"],
                "case_id": "case_test",
            },
            "confidence": 0.9,
        }
        for i in range(n)
    ]


def _gen_edges(n: int, node_count: int):
    return [
        {
            "source_key": f"PERSON_{i % node_count:04d}",
            "target_key": f"PERSON_{(i+1) % node_count:04d}",
            "rel_type": "ASSOCIATE_OF",
            "confidence": 0.85,
            "source_doc_ids": [f"doc_{i % 10}"],
        }
        for i in range(n)
    ]


def _gen_docs(num: int, chars_per_doc: int = 3000):
    base = "FIR content about investigation. Person 1 was involved. " * 60
    base = base[:chars_per_doc]
    return [
        {
            "doc_id": f"doc_{i}",
            "filename": f"doc_{i}.txt",
            "document_type": "FIR",
            "content": base + f" Doc {i} about Person {i % 5}.",
        }
        for i in range(num)
    ]


class TestDocumentRelevanceFiltering:
    """Phase 2: document retrieval relevance and budget enforcement."""

    def test_target_key_filters_to_subgraph_docs(self):
        """When target_key is set, only docs whose source_doc_id overlaps subgraph are kept."""
        settings = Settings(
            ai_max_context_doc_chars=30000,
            ai_interactive_max_context_doc_chars=15000,
        )
        gateway = AIGateway(settings=settings, router=MagicMock())

        nodes = _gen_nodes(20)
        edges = _gen_edges(30, 20)
        docs = _gen_docs(50, 3000)

        filtered, avail, included = gateway._filter_relevant_documents(
            question="What is Person 1 connected to?",
            nodes=nodes,
            edges=edges,
            documents=docs,
            target_key="PERSON_0001",
            max_total_chars=15000,
        )

        assert avail == 50
        assert included <= avail
        # Subgraph doc ids are doc_0..doc_9 (from gen)
        subgraph_ids = gateway._collect_subgraph_doc_ids(nodes, edges)
        for d in filtered:
            assert d["doc_id"] in subgraph_ids, f"{d['doc_id']} not in subgraph {subgraph_ids}"

        # Total chars must respect budget
        total_chars = sum(len(d["content"]) for d in filtered)
        assert total_chars <= 15000

    def test_whole_case_capped_by_budget(self):
        """Whole-case query with many docs must never exceed total char budget."""
        settings = Settings(
            ai_max_context_doc_chars=30000,
            ai_interactive_max_context_doc_chars=15000,
        )
        gateway = AIGateway(settings=settings, router=MagicMock())

        nodes = _gen_nodes(100)
        edges = _gen_edges(200, 100)
        docs = _gen_docs(100, 3000)  # 100 docs * 3000 = 300k chars

        filtered, avail, included = gateway._filter_relevant_documents(
            question="Summarize open leads in this case",
            nodes=nodes,
            edges=edges,
            documents=docs,
            target_key=None,
            max_total_chars=15000,
        )

        assert avail == 100
        total_chars = sum(len(d["content"]) for d in filtered)
        assert total_chars <= 15000, f"budget exceeded: {total_chars} > 15000"
        # Should include far fewer than available
        assert included < avail
        assert included <= 10  # default max_docs for whole-case

    def test_keyword_overlap_ranking(self):
        """Docs with keyword overlap should be ranked higher than unrelated docs."""
        settings = Settings()
        gateway = AIGateway(settings=settings, router=MagicMock())

        nodes = _gen_nodes(5)
        edges = _gen_edges(5, 5)
        docs = [
            {"doc_id": "doc_a", "filename": "a.txt", "document_type": "FIR", "content": "This mentions Ravi and warehouse CCTV"},
            {"doc_id": "doc_b", "filename": "b.txt", "document_type": "FIR", "content": "Completely unrelated content about weather"},
            {"doc_id": "doc_c", "filename": "c.txt", "document_type": "FIR", "content": "Ravi was seen near warehouse"},
        ]

        filtered, avail, included = gateway._filter_relevant_documents(
            question="What connects Ravi to the warehouse CCTV?",
            nodes=nodes,
            edges=edges,
            documents=docs,
            target_key=None,
            max_total_chars=10000,
        )

        # Docs mentioning Ravi/warehouse should come first
        assert filtered[0]["doc_id"] in ("doc_a", "doc_c")

    def test_new_setting_exists(self):
        """New ai_max_context_doc_chars setting must exist and be sensible."""
        settings = Settings()
        assert hasattr(settings, "ai_max_context_doc_chars")
        assert hasattr(settings, "ai_interactive_max_context_doc_chars")
        # Must be less than 50 docs * 3000 = 150k, otherwise not effective
        assert settings.ai_max_context_doc_chars <= 50000
        assert settings.ai_interactive_max_context_doc_chars <= 30000
        assert settings.ai_interactive_max_context_doc_chars <= settings.ai_max_context_doc_chars


class TestQueryToEntityDetection:
    """Phase 3: query-to-entity detection for graph retrieval."""

    def test_detect_person_mention(self):
        settings = Settings()
        gateway = AIGateway(settings=settings, router=MagicMock())
        nodes = [
            {"provenance_key": "person:ravi", "label": "Person", "properties": {"name": "Ravi Kumar"}},
            {"provenance_key": "person:asha", "label": "Person", "properties": {"name": "Asha Reddy"}},
        ]
        detected = gateway._detect_entities_in_question(
            "What connects Ravi to the warehouse CCTV on Aug 12", nodes
        )
        assert "person:ravi" in detected
        assert "person:asha" not in detected

    def test_detect_phone_mention(self):
        settings = Settings()
        gateway = AIGateway(settings=settings, router=MagicMock())
        nodes = [
            {"provenance_key": "phone:1", "label": "Phone", "properties": {"number": "+919829012345"}},
            {"provenance_key": "person:1", "label": "Person", "properties": {"name": "Ramesh"}},
        ]
        detected = gateway._detect_entities_in_question(
            "Who called +919829012345?", nodes
        )
        assert "phone:1" in detected

    def test_detect_vehicle_plate(self):
        settings = Settings()
        gateway = AIGateway(settings=settings, router=MagicMock())
        nodes = [
            {"provenance_key": "vehicle:1", "label": "Vehicle", "properties": {"plate": "RJ14AB1234"}},
        ]
        detected = gateway._detect_entities_in_question(
            "What about vehicle RJ14AB1234?", nodes
        )
        assert "vehicle:1" in detected

    def test_general_question_no_detection(self):
        settings = Settings()
        gateway = AIGateway(settings=settings, router=MagicMock())
        nodes = [
            {"provenance_key": "person:ravi", "label": "Person", "properties": {"name": "Ravi Kumar"}},
        ]
        detected = gateway._detect_entities_in_question(
            "Summarize open leads in this case", nodes
        )
        assert detected == []

    @pytest.mark.asyncio
    async def test_narrower_retrieval_path_for_entity_question(self):
        """Entity-specific question should trigger narrower retrieval (fewer nodes)."""
        settings = Settings(
            ai_interactive_max_context_nodes=100,
            ai_interactive_max_context_edges=200,
            ai_max_context_nodes=300,
            ai_max_context_edges=600,
            ai_interactive_max_context_doc_chars=15000,
        )
        mock_router = MagicMock()
        mock_router.chat = AsyncMock(return_value={
            "available": True,
            "content": json.dumps({
                "finding_type": "GENERAL",
                "summary": "Test",
                "confidence": 0.8,
                "evidence_level": "FACT",
                "evidence_refs": [{"doc_id": "doc_0"}],
            }),
            "model": "test-model",
            "latency_ms": 100,
        })

        gateway = AIGateway(settings=settings, router=mock_router)

        # Large case nodes
        large_nodes = _gen_nodes(300)
        large_edges = _gen_edges(600, 300)
        small_nodes = _gen_nodes(20)
        small_edges = _gen_edges(30, 20)
        docs = _gen_docs(20, 1000)

        # Mock methods
        with patch.object(gateway, "_retrieve_subgraph", return_value=(large_nodes, large_edges)), \
             patch.object(gateway, "_retrieve_subgraph_multi", return_value=(small_nodes, small_edges)), \
             patch.object(gateway, "_retrieve_case_documents", return_value=docs), \
             patch.object(gateway, "_get_all_case_nodes", return_value=large_nodes):

            # Entity-specific question should use multi (narrower)
            resp_entity = await gateway.ask(
                question="What connects Person 1 to the warehouse?",
                case_id="case_test",
                dataset_id="ds_test",
            )
            assert resp_entity.context["entity_detection_used"] is True
            assert resp_entity.context["detected_entity_count"] >= 1
            # Narrower path: 20 nodes vs 300
            assert resp_entity.context["nodes"] == 20
            assert resp_entity.context["nodes"] < 100  # meaningfully smaller than whole-case default

            # General question should fallback to whole-case
            # With Investigation Retrieval Engine, whole-case is now ranked and capped to
            # ai_interactive_max_context_nodes (100) for interactive path, not raw 300
            # The important invariant is: entity-specific path is narrower than whole-case
            resp_general = await gateway.ask(
                question="Summarize open leads in this case",
                case_id="case_test",
                dataset_id="ds_test",
            )
            assert resp_general.context["entity_detection_used"] is False
            assert resp_general.context["entity_detection_path"] == "fallback_whole_case"
            # Whole-case now goes through ranking engine: capped to interactive max (100)
            # not raw 300, but still larger than entity-specific 20
            assert resp_general.context["nodes"] >= 100  # capped, but at least 100
            assert resp_general.context["nodes"] > resp_entity.context["nodes"]  # narrower for entity

    @pytest.mark.asyncio
    async def test_structured_logging_includes_required_fields(self):
        """Ensure new structured logging fields are present in context_report."""
        settings = Settings(
            ai_interactive_max_context_doc_chars=15000,
        )
        mock_router = MagicMock()
        mock_router.chat = AsyncMock(return_value={
            "available": True,
            "content": json.dumps({
                "finding_type": "GENERAL",
                "summary": "Test",
                "confidence": 0.8,
                "evidence_level": "FACT",
                "evidence_refs": [{"doc_id": "doc_0"}],
            }),
            "model": "test-model",
            "latency_ms": 50,
        })
        gateway = AIGateway(settings=settings, router=mock_router)

        nodes = _gen_nodes(10)
        edges = _gen_edges(10, 10)
        docs = _gen_docs(5, 1000)

        with patch.object(gateway, "_retrieve_subgraph", return_value=(nodes, edges)), \
             patch.object(gateway, "_retrieve_subgraph_multi", return_value=(nodes[:3], edges[:3])), \
             patch.object(gateway, "_retrieve_case_documents", return_value=docs), \
             patch.object(gateway, "_get_all_case_nodes", return_value=nodes):

            resp = await gateway.ask(
                question="What connects Person 1?",
                case_id="case_test",
                dataset_id="ds_test",
            )
            ctx = resp.context
            # Required Phase 1+2+3 fields
            assert "documents_available_count" in ctx
            assert "documents_included_count" in ctx
            assert "entity_detection_used" in ctx
            assert "entity_detection_path" in ctx
            assert "detected_entity_count" in ctx
            assert "documents_total_chars" in ctx
            # Timing must include required stages
            timing = ctx.get("timing", {})
            assert "retrieval_ms" in timing
            assert "prompt_build_ms" in timing
            assert "model_call_ms" in timing or "model_ms" in timing
