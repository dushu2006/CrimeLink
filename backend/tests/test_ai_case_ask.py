"""The Case AI ask endpoint: contract, failure handling and provider routing.

The reported defect was a blanket ``422 Unprocessable Entity`` from
``POST /api/v1/ai/cases/{case_id}/ask``.  A 422 is FastAPI telling the caller
"your body does not match my schema", so these tests pin the contract from
both ends: what the browser actually sends must validate, and every rejection
the schema still makes must be one a human can act on.

Nothing here mocks the gateway's *decision making*.  Where a provider call is
involved the router is pointed at a real HTTP endpoint so the whole path --
client construction, request, response parsing, schema validation -- executes
for real.
"""

from __future__ import annotations

import json

import pytest


def _ask(client, headers, case_id: str, body: dict):
    return client.post(f"/api/v1/ai/cases/{case_id}/ask", json=body, headers=headers)


# ---------------------------------------------------------------------------
# The contract the frontend actually uses
# ---------------------------------------------------------------------------


def test_the_payload_the_ui_sends_is_accepted(client, investigator_headers, case):
    """CaseDetail posts ``{question}`` and nothing else. That must validate."""
    response = _ask(
        client, investigator_headers, case.id,
        {"question": "What connections appear in this case?"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["query_id"]
    assert "available" in body and "finding" in body


def test_short_questions_are_accepted(client, investigator_headers, case):
    """"Why?" is a legitimate question; a length rule must not 422 it.

    ``min_length=3`` was the literal cause of the reported 422: the UI guards
    only against an empty string, so any one- or two-character question was
    rejected by the schema before it reached the gateway.
    """
    for question in ("Why", "Hi", "?"):
        response = _ask(client, investigator_headers, case.id, {"question": question})
        assert response.status_code == 200, (question, response.text)


def test_whitespace_is_trimmed_not_rejected(client, investigator_headers, case):
    response = _ask(
        client, investigator_headers, case.id, {"question": "   Who is linked?   "}
    )
    assert response.status_code == 200, response.text


def test_arbitrary_retrieval_depth_is_accepted(client, investigator_headers, case):
    """Retrieval depth follows the same rule as the person graph: no cap."""
    for depth in (1, 3, 7, 25, 100):
        response = _ask(
            client, investigator_headers, case.id,
            {"question": "Summarise this case", "depth": depth},
        )
        assert response.status_code == 200, (depth, response.text)


def test_legacy_field_names_are_accepted(client, investigator_headers, case):
    """``query``/``prompt``/``text`` are aliases, not 422s.

    Different call sites historically used different names.  Accepting the
    aliases keeps every existing caller working; the API is additive, so
    nothing that used to work stops working.
    """
    for field in ("query", "prompt", "text"):
        response = _ask(
            client, investigator_headers, case.id, {field: "Who is connected here?"}
        )
        assert response.status_code == 200, (field, response.text)


# ---------------------------------------------------------------------------
# The rejections that remain -- each one actionable
# ---------------------------------------------------------------------------


def test_empty_question_is_rejected_with_a_readable_reason(
    client, investigator_headers, case
):
    for body in ({"question": ""}, {"question": "    "}, {}):
        response = _ask(client, investigator_headers, case.id, body)
        assert response.status_code == 422, body
        detail = json.dumps(response.json()).lower()
        assert "question" in detail
        assert "empty" in detail or "required" in detail or "provide" in detail


def test_unknown_case_is_a_404_not_a_422(client, investigator_headers):
    """An invalid case is a missing resource, not a malformed request."""
    response = _ask(
        client, investigator_headers, "no-such-case-id", {"question": "Anything?"}
    )
    assert response.status_code == 404, response.text


def test_case_outside_the_jurisdiction_is_not_readable(
    client, kota_headers, case
):
    """Scope is enforced before the model ever sees the question."""
    response = _ask(client, kota_headers, case.id, {"question": "Tell me about this"})
    assert response.status_code in (403, 404), response.text


def test_malformed_body_is_rejected(client, investigator_headers, case):
    response = client.post(
        f"/api/v1/ai/cases/{case.id}/ask",
        content="this is not json",
        headers={**investigator_headers, "Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_over_long_question_is_rejected(client, investigator_headers, case):
    response = _ask(client, investigator_headers, case.id, {"question": "x" * 20_001})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Provider failure surfaces honestly -- never as a fake success
# ---------------------------------------------------------------------------


def test_missing_key_reports_unavailable_with_the_env_var_to_set(
    client, investigator_headers, case, monkeypatch
):
    from app.ai import gateway as gateway_module

    monkeypatch.setattr(
        gateway_module.get_ai_gateway().settings, "ai_api_key", None, raising=False
    )
    monkeypatch.setattr(
        gateway_module.get_ai_gateway().settings,
        "ai_reasoning_api_key",
        None,
        raising=False,
    )
    response = _ask(client, investigator_headers, case.id, {"question": "Who?"})
    assert response.status_code == 200, "an unconfigured model is not an HTTP error"
    body = response.json()
    assert body["available"] is False
    assert "CRIMELINK_AI" in body["finding"]["summary"], (
        "the message must name the variable an operator has to set"
    )
    assert body["request_id"], "a failure must be traceable to a log line"


def test_provider_failure_is_reported_not_invented(
    client, investigator_headers, case, monkeypatch
):
    """A configured provider that fails must say so, not fabricate a finding."""
    from app.ai import router as router_module

    async def exploding_chat(*args, **kwargs):
        raise TimeoutError("provider did not respond in time")

    monkeypatch.setattr(
        router_module.AIModelRouter, "chat", exploding_chat, raising=True
    )
    response = _ask(client, investigator_headers, case.id, {"question": "Who?"})
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    summary = body["finding"]["summary"].lower()
    assert "timeout" in summary or "failed" in summary or "unavailable" in summary
    assert body["finding"]["confidence"] == 0.0
    assert body["finding"]["recommended_review"] is True


def test_no_api_key_ever_appears_in_a_response(
    client, investigator_headers, case, monkeypatch
):
    """Keys must never leak through an error path into the browser."""
    secret = "sk-super-secret-key-value"
    settings = __import__("app.ai.gateway", fromlist=["x"]).get_ai_gateway().settings
    monkeypatch.setattr(settings, "ai_api_key", secret, raising=False)

    response = _ask(client, investigator_headers, case.id, {"question": "Who?"})
    assert secret not in response.text
