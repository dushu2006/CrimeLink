"""Case RAG receives canonical internal IDs, not display references."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.ai.schemas import AIResponse, FindingResult


def test_case_number_is_resolved_before_gateway_retrieval(
    client, investigator_headers, case
):
    """A display case reference must follow the scoped gateway path."""
    gateway = MagicMock()
    gateway.ask = AsyncMock(
        return_value=AIResponse(
            query_id="query-test",
            role="reasoning",
            finding=FindingResult(
                summary="No model call in this route-boundary regression test.",
                confidence=0.0,
            ),
            available=False,
        )
    )
    with (
        patch("app.api.v1.ai.case_service.require_case", new=AsyncMock(return_value=case)),
        patch("app.api.v1.ai.get_ai_gateway", return_value=gateway),
    ):
        response = client.post(
            "/api/v1/ai/cases/CR-DISPLAY-REF/ask",
            json={"question": "What evidence is in this case?"},
            headers=investigator_headers,
        )

    assert response.status_code == 200, response.text
    gateway.ask.assert_awaited_once()
    assert gateway.ask.await_args.kwargs["case_id"] == case.id
    assert gateway.ask.await_args.kwargs["case_id"] != "CR-DISPLAY-REF"
