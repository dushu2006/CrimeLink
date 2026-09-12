"""Regression tests for AI interactive path latency and context budget (WS 2.1 / 2.2).

Verifies:
1. Config has interactive-specific settings for timeout, retries, and context limits.
2. AIGateway._retrieve_subgraph and _build_reasoning_context respect interactive context limits.
3. Serialized context payload for a large graph (>300 nodes) is strictly bounded.
4. Interactive ask path passes timeout_override and max_retries_override to the router.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.gateway import AIGateway
from app.config import Settings


class TestInteractiveSettings:
    """Settings for the interactive AI path."""

    def test_interactive_settings_exist(self):
        settings = Settings()
        assert hasattr(settings, "ai_interactive_timeout_s")
        assert hasattr(settings, "ai_interactive_max_retries")
        assert hasattr(settings, "ai_interactive_max_context_nodes")
        assert hasattr(settings, "ai_interactive_max_context_edges")

        # Must be shorter/smaller than global batch settings
        assert settings.ai_interactive_timeout_s <= 30.0
        assert settings.ai_interactive_timeout_s < settings.ai_timeout_s
        assert settings.ai_interactive_max_retries <= 1
        assert settings.ai_interactive_max_retries < settings.ai_max_retries
        assert settings.ai_interactive_max_context_nodes <= 100
        assert settings.ai_interactive_max_context_nodes < settings.ai_max_context_nodes
        assert settings.ai_interactive_max_context_edges <= 200
        assert settings.ai_interactive_max_context_edges < settings.ai_max_context_edges


class TestInteractiveContextBudget:
    """Context budget bounding on large graphs."""

    def test_build_reasoning_context_bounds_large_graph(self):
        """A >300-node graph must be clamped to interactive limits and bounded in byte size."""
        settings = Settings(
            ai_interactive_max_context_nodes=60,
            ai_interactive_max_context_edges=120,
        )
        gateway = AIGateway(settings=settings)

        # Generate synthetic 400 nodes and 800 edges
        nodes = [
            {
                "provenance_key": f"PERSON_{i:04d}",
                "label": "Person",
                "properties": {"name": f"Person {i}", "case_id": "test_case"},
                "confidence": 0.9,
            }
            for i in range(400)
        ]
        edges = [
            {
                "source_key": f"PERSON_{i:04d}",
                "target_key": f"PERSON_{(i+1)%400:04d}",
                "rel_type": "ASSOCIATE_OF",
                "confidence": 0.85,
            }
            for i in range(800)
        ]

        context_str = gateway._build_reasoning_context(
            nodes,
            edges,
            "Who are the key people?",
            max_nodes=settings.ai_interactive_max_context_nodes,
            max_edges=settings.ai_interactive_max_context_edges,
        )

        # Extract the JSON payload from the prompt
        json_part = context_str.split("\n\n", 1)[1]
        payload = json.loads(json_part)

        assert len(payload["nodes"]) == 60
        assert len(payload["relationships"]) == 120
        assert payload["node_count_total"] == 400
        assert payload["edge_count_total"] == 800

        # Concrete byte size ceiling: 60 nodes + 120 edges should be well under 40 KB
        context_bytes = len(context_str.encode("utf-8"))
        assert context_bytes < 40_000, f"Context size {context_bytes} bytes exceeds 40KB ceiling"

    @pytest.mark.asyncio
    async def test_answer_passes_interactive_overrides_to_router(self):
        """Gateway._answer must pass interactive timeout and retries to router.chat."""
        settings = Settings(
            ai_interactive_timeout_s=22.0,
            ai_interactive_max_retries=1,
            ai_interactive_max_context_nodes=60,
            ai_interactive_max_context_edges=120,
        )
        mock_router = MagicMock()
        mock_router.chat = AsyncMock(return_value={
            "available": False,
            "reason": "no_api_key_for_role_reasoning",
        })

        gateway = AIGateway(settings=settings, router=mock_router)

        # Non-conversational question to hit the model path
        with patch.object(gateway, "_retrieve_subgraph", return_value=([], [])):
            await gateway.ask(
                question="What are the financial transfers between ACCT_001 and ACCT_002?",
                case_id="case_123",
            )

        assert mock_router.chat.called
        call_kwargs = mock_router.chat.call_args.kwargs
        assert call_kwargs.get("timeout_override") == 22.0
        assert call_kwargs.get("max_retries_override") == 1

    def test_parse_and_validate_with_preamble_and_fenced_json(self):
        """Gateway._parse_and_validate extracts JSON when model outputs reasoning preamble."""
        settings = Settings()
        gateway = AIGateway(settings=settings, router=MagicMock())
        content = (
            "We need to produce JSON output matching schema: {finding_type, summary...}\n"
            "Here is the analysis based on the case subgraph:\n"
            "```json\n"
            "{\n"
            '  "finding_type": "PATTERN",\n'
            '  "summary": "Detected layering pattern between accounts.",\n'
            '  "confidence": 0.85,\n'
            '  "evidence_level": "HIGH",\n'
            '  "entities": ["ACC_1", "ACC_2"],\n'
            '  "relationships": ["TRANSFER"],\n'
            '  "evidence_refs": [],\n'
            '  "reasoning_steps": ["Traced flow of funds"],\n'
            '  "uncertainties": [],\n'
            '  "recommended_review": false,\n'
            '  "suggested_next_actions": []\n'
            "}\n"
            "```"
        )
        result = gateway._parse_and_validate(content)
        assert result.finding_type == "PATTERN"
        assert result.summary == "Detected layering pattern between accounts."
        assert result.confidence == 0.85
        assert result.recommended_review is False

    def test_parse_and_validate_with_think_tags(self):
        """Gateway._parse_and_validate strips <think> tags from thinking models."""
        settings = Settings()
        gateway = AIGateway(settings=settings, router=MagicMock())
        content = (
            "<think>Analyzing 63 entities and 163 relationships...</think>\n"
            "{\n"
            '  "finding_type": "GENERAL",\n'
            '  "summary": "Case summary for investigator.",\n'
            '  "confidence": 0.9\n'
            "}"
        )
        result = gateway._parse_and_validate(content)
        assert result.summary == "Case summary for investigator."
        assert result.confidence == 0.9

    def test_parse_and_validate_with_unstructured_text(self):
        """Unstructured prose is kept, flagged for review, and carries no confidence.

        The text is not thrown away — losing what the model said would be worse
        than keeping it — but no structured claim was made, so nothing may be
        asserted about its weight: confidence stays at zero and the evidence
        level stays UNKNOWN until a human reads it. (This is the same contract
        ``test_ai_provider_roundtrip.py::test_non_json_output_is_flagged_for_review``
        pins from the endpoint side.)
        """
        settings = Settings()
        gateway = AIGateway(settings=settings, router=MagicMock())
        content = "This case involves money laundering coordinated between Asha Reddy and Ramesh Kumar."
        result = gateway._parse_and_validate(content)
        assert "Asha Reddy" in result.summary
        assert result.confidence == 0.0
        assert result.evidence_level == "UNKNOWN"
        assert result.recommended_review is True

