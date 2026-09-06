"""End-to-end AI verification against a real OpenAI-compatible HTTP server.

There is no way to assert that "the AI works" by mocking the gateway -- that
just asserts the mock works.  So these tests start an **actual HTTP server**
that speaks the OpenAI wire protocol and point the configured base URL at it.
Everything on the CrimeLink side then runs for real:

    Settings -> role_config -> AIModelRouter -> AsyncOpenAI client
      -> HTTP request over a socket -> response parsing
      -> FindingResult validation -> audit -> API response body

Only the model's weights are substituted.  The request the provider receives
is captured and asserted on, so we can prove which model name was sent, that
the Authorization header carried the configured key, and that the case
subgraph reached the prompt.

To run the same checks against the real provider instead, set
``CRIMELINK_AI_API_KEY`` (or a per-role key) and
``CRIMELINK_AI_REASONING_MODEL`` in the environment and use
``POST /api/v1/ai/health/test``.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.ai.router import AIModelRouter

pytest.importorskip("openai", reason="the OpenAI-compatible client is required")

#: Every request the fake provider received, for assertions.
RECEIVED: list[dict] = []

VALID_FINDING = {
    "finding_type": "GENERAL",
    "summary": "Two persons of interest share a phone number in this case.",
    "confidence": 0.62,
    "evidence_level": "INFERENCE",
    "entities": [{"pseudo_id": "PERSON_001", "label": "Person"}],
    "reasoning_steps": [
        {"step": 1, "statement": "PERSON_001 and PERSON_002 both use PHONE_001.",
         "evidence_level": "FACT", "evidence_refs": ["doc-1"]}
    ],
    "uncertainties": ["Subscriber records were not available."],
    "recommended_review": True,
}


class _Provider(BaseHTTPRequestHandler):
    """A minimal but genuine OpenAI-compatible endpoint."""

    #: Set per-test to change how the provider behaves.
    mode = "ok"

    def log_message(self, *args):  # silence the default stderr logging
        return

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        RECEIVED.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": body,
            }
        )
        mode = type(self).mode

        if mode == "http_500":
            self._send(500, {"error": {"message": "upstream exploded"}})
            return
        if mode == "unauthorized":
            self._send(401, {"error": {"message": "Invalid API key"}})
            return

        if self.path.endswith("/embeddings"):
            count = len(body.get("input") or [])
            self._send(
                200,
                {
                    "object": "list",
                    "model": body.get("model"),
                    "data": [
                        {"object": "embedding", "index": i, "embedding": [0.1, 0.2, 0.3, 0.4]}
                        for i in range(count)
                    ],
                    "usage": {"prompt_tokens": 4, "total_tokens": 4},
                },
            )
            return

        content = {
            "ok": json.dumps(VALID_FINDING),
            "fenced": "```json\n" + json.dumps(VALID_FINDING) + "\n```",
            "not_json": "I think they are probably connected somehow.",
            "forbidden_label": json.dumps(
                {**VALID_FINDING, "summary": "PERSON_001 is a criminal kingpin."}
            ),
            "bad_schema": json.dumps({"summary": "x", "confidence": 42}),
        }[mode]

        self._send(
            200,
            {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": body.get("model"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180},
            },
        )

    def _send(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture()
def provider():
    """A live OpenAI-compatible server on a real TCP port."""
    RECEIVED.clear()
    _Provider.mode = "ok"
    server = HTTPServer(("127.0.0.1", 0), _Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def wired(settings, provider, monkeypatch):
    """Settings pointed at the live provider with a key configured."""
    monkeypatch.setattr(settings, "ai_api_key", "test-key-abcdef123456", raising=False)
    monkeypatch.setattr(settings, "ai_base_url", provider, raising=False)
    monkeypatch.setattr(settings, "ai_timeout_s", 10.0, raising=False)
    monkeypatch.setattr(settings, "ai_max_retries", 1, raising=False)
    for role in settings.AI_ROLES:
        monkeypatch.setattr(settings, f"ai_{role}_api_key", None, raising=False)
        monkeypatch.setattr(settings, f"ai_{role}_base_url", None, raising=False)
    return settings


# ---------------------------------------------------------------------------
# The router really talks to a provider
# ---------------------------------------------------------------------------


async def test_chat_round_trip_reaches_the_provider(wired):
    result = await AIModelRouter(wired).chat(
        "investigation_reasoning", system_prompt="sys", user_prompt="user"
    )
    assert result["available"] is True, result
    assert result["model"] == wired.ai_reasoning_model
    assert result["provider"] == wired.ai_provider
    assert result["prompt_tokens"] == 120
    assert result["latency_ms"] >= 0

    sent = RECEIVED[-1]
    assert sent["path"].endswith("/chat/completions")
    assert sent["authorization"] == "Bearer test-key-abcdef123456", (
        "the configured key must be sent to the provider"
    )
    assert sent["body"]["model"] == wired.ai_reasoning_model
    assert sent["body"]["messages"][0]["role"] == "system"


@pytest.mark.parametrize(
    "task,expected_role",
    [
        ("extraction", "extraction"),
        ("investigation_reasoning", "reasoning"),
        ("explanation", "explanation"),
        ("classification", "classification"),
    ],
)
async def test_every_declared_chat_role_routes_to_its_own_model(
    wired, task, expected_role
):
    """Each declared role must reach the provider with *its* configured model."""
    result = await AIModelRouter(wired).chat(task, system_prompt="s", user_prompt="u")
    assert result["available"] is True, result
    assert result["role"] == expected_role
    assert result["model"] == getattr(wired, f"ai_{expected_role}_model")
    assert RECEIVED[-1]["body"]["model"] == getattr(wired, f"ai_{expected_role}_model")


async def test_embedding_role_returns_real_vectors(wired):
    result = await AIModelRouter(wired).embed("embedding", ["alpha", "beta"])
    assert result["available"] is True, result
    assert result["model"] == wired.ai_embedding_model
    assert len(result["embeddings"]) == 2
    assert result["dimensions"] == 4
    assert RECEIVED[-1]["path"].endswith("/embeddings")


async def test_per_role_key_overrides_the_shared_key(wired, monkeypatch):
    monkeypatch.setattr(wired, "ai_reasoning_api_key", "role-specific-key", raising=False)
    await AIModelRouter(wired).chat(
        "investigation_reasoning", system_prompt="s", user_prompt="u"
    )
    assert RECEIVED[-1]["authorization"] == "Bearer role-specific-key"


# ---------------------------------------------------------------------------
# Failure handling is real, not swallowed
# ---------------------------------------------------------------------------


async def test_server_error_is_retried_then_reported(wired, monkeypatch):
    monkeypatch.setattr(wired, "ai_max_retries", 2, raising=False)
    _Provider.mode = "http_500"
    result = await AIModelRouter(wired).chat("investigation_reasoning", "s", "u")
    assert result["available"] is False
    assert result["reason"].startswith("invocation_failed:")
    assert len(RECEIVED) >= 2, "a 5xx must be retried"


async def test_bad_key_is_not_retried(wired, monkeypatch):
    monkeypatch.setattr(wired, "ai_max_retries", 3, raising=False)
    _Provider.mode = "unauthorized"
    result = await AIModelRouter(wired).chat("investigation_reasoning", "s", "u")
    assert result["available"] is False
    assert len(RECEIVED) == 1, "a 401 is deterministic; retrying only wastes time"


async def test_provider_error_text_never_carries_the_key(wired):
    _Provider.mode = "unauthorized"
    result = await AIModelRouter(wired).chat("investigation_reasoning", "s", "u")
    assert "test-key-abcdef123456" not in json.dumps(result)


# ---------------------------------------------------------------------------
# Full stack: HTTP endpoint -> gateway -> provider -> validated finding
# ---------------------------------------------------------------------------


def _seed_case_graph(container, case_id: str) -> None:
    from app.domain.models import GraphEdge, GraphNode

    container.injector.inject_nodes(
        [
            GraphNode(
                provenance_key=f"person:{i}",
                label="Person",
                properties={
                    "name": f"Person {i}",
                    "case_ids": [case_id],
                    "source_doc_id": "doc-ai",
                },
            )
            for i in (1, 2)
        ]
    )
    container.injector.inject_edges(
        [
            GraphEdge(
                source_key="person:1",
                target_key="person:2",
                rel_type="ASSOCIATE_OF",
                properties={"source_doc_id": "doc-ai", "case_ids": [case_id]},
                discriminator="ai-fixture",
            )
        ]
    )


async def test_case_ask_returns_a_real_model_answer(
    client, investigator_headers, case, container, wired
):
    """The headline check: a question produces a model-authored finding."""
    _seed_case_graph(container, case.id)

    response = client.post(
        f"/api/v1/ai/cases/{case.id}/ask",
        json={"question": "How are these people connected?"},
        headers=investigator_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["available"] is True, body
    assert body["model"] == wired.ai_reasoning_model
    assert body["provider"] == wired.ai_provider
    assert body["request_id"]
    assert body["finding"]["summary"] == VALID_FINDING["summary"]
    assert body["finding"]["confidence"] == pytest.approx(0.62)
    assert body["finding"]["reasoning_steps"][0]["step"] == 1
    assert body["context"]["nodes"] >= 2, "the case subgraph was retrieved"

    prompt = RECEIVED[-1]["body"]["messages"][1]["content"]
    assert "How are these people connected?" in prompt
    assert "PERSON_" in prompt, "context reached the model, pseudonymized"
    assert "Person 1" not in prompt, "raw names must not leave the building"


async def test_fenced_json_is_parsed(client, investigator_headers, case, container, wired):
    """Models that wrap JSON in ``` fences are handled, not rejected."""
    _seed_case_graph(container, case.id)
    _Provider.mode = "fenced"
    body = client.post(
        f"/api/v1/ai/cases/{case.id}/ask",
        json={"question": "Summarise"},
        headers=investigator_headers,
    ).json()
    assert body["available"] is True
    assert body["finding"]["summary"] == VALID_FINDING["summary"]


async def test_non_json_output_is_flagged_for_review(
    client, investigator_headers, case, container, wired
):
    _seed_case_graph(container, case.id)
    _Provider.mode = "not_json"
    body = client.post(
        f"/api/v1/ai/cases/{case.id}/ask",
        json={"question": "Summarise"},
        headers=investigator_headers,
    ).json()
    assert body["finding"]["recommended_review"] is True
    assert body["finding"]["confidence"] == 0.0


async def test_forbidden_labels_are_flagged(
    client, investigator_headers, case, container, wired
):
    """Neutral-language policy is enforced on real model output."""
    _seed_case_graph(container, case.id)
    _Provider.mode = "forbidden_label"
    body = client.post(
        f"/api/v1/ai/cases/{case.id}/ask",
        json={"question": "Who leads this group?"},
        headers=investigator_headers,
    ).json()
    assert body["finding"]["recommended_review"] is True
    assert "flagged for human review" in body["finding"]["summary"]


async def test_provider_outage_surfaces_as_unavailable_not_a_lie(
    client, investigator_headers, case, container, wired
):
    _seed_case_graph(container, case.id)
    _Provider.mode = "http_500"
    body = client.post(
        f"/api/v1/ai/cases/{case.id}/ask",
        json={"question": "Summarise"},
        headers=investigator_headers,
    ).json()
    assert body["available"] is False
    assert body["fallback_reason"].startswith("invocation_failed:")
    assert "CRIMELINK_AI_REASONING_API_KEY" in body["finding"]["summary"], (
        "the message must tell the operator what to check"
    )
    assert body["request_id"]


async def test_health_endpoint_reports_roles_without_leaking_keys(
    client, investigator_headers, wired
):
    response = client.get("/api/v1/ai/health", headers=investigator_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configured"] is True
    roles = {role["role"]: role for role in body["roles"]}
    assert set(roles) == set(wired.AI_ROLES)
    for role in roles.values():
        assert role["key_present"] is True
        assert role["key_source"] == "CRIMELINK_AI_API_KEY"
        assert role["model"]
    assert "test-key-abcdef123456" not in response.text


async def test_admin_connectivity_test_calls_the_provider(client, admin_headers, wired):
    for role in ("reasoning", "classification", "embedding"):
        response = client.post(
            "/api/v1/ai/health/test", json={"role": role}, headers=admin_headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True, body
        assert "test-key-abcdef123456" not in response.text


async def test_connectivity_test_requires_admin(client, investigator_headers, wired):
    response = client.post(
        "/api/v1/ai/health/test", json={"role": "reasoning"}, headers=investigator_headers
    )
    assert response.status_code == 403
