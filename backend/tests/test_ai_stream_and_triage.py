"""AI latency contract: intent triage, streamed progress, honest timing.

Three reported problems, each pinned here:

1. A greeting ("Hi") used to run the *entire* pipeline — case subgraph
   retrieval, pseudonymisation, a model call — so a two-word message hung for
   tens of seconds.  Conversational messages must answer instantly with zero
   retrieval, zero embedding, zero model calls.
2. Investigative questions must not look frozen: the stream endpoint emits an
   ``ack`` before any expensive work, stage events while it runs, answer
   tokens as they arrive, and a final ``done`` carrying the same payload the
   plain POST would have returned.
3. "The AI is slow" has to be diagnosable: every response carries per-stage
   timing, and a failure says *why* in actionable words — never a stack trace.

No provider API key exists in this environment; where a provider call is
exercised it is driven through the real router code paths with a scripted
client object, so routing, fallback and streaming semantics all execute.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from app.ai import router as router_module
from app.ai.gateway import AIGateway, is_conversational

FAKE_FINDING = {
    "finding_type": "FINANCIAL",
    "summary": "Two accounts share the phone 9812345672 across transfer rows.",
    "confidence": 0.6,
    "evidence_level": "INFERENCE",
    "recommended_review": True,
}


# ---------------------------------------------------------------------------
# 1. Intent triage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hi", True),
        ("hi", True),
        ("hii", True),
        ("hello there", True),
        ("thank you", True),
        ("thank you so much!", True),
        ("good morning", True),
        ("who are you?", True),
        ("what can you do", True),
        ("hi there, thanks", True),
        # These MUST go through full retrieval — a greeting-shaped prefix on a
        # real question is the trap a naive prefix matcher falls into:
        ("Hi, who is Arjun Reddy?", False),
        ("Hello, show transactions for ACCT_0001", False),
        ("Why", False),
        ("What connects the phone numbers?", False),
        ("CASE_0001 who benefited?", False),
        ("help me investigate TS09AB1234", False),
        ("", False),
    ],
)
def test_triage_truth_table(text: str, expected: bool):
    assert is_conversational(text) is expected


def test_greeting_never_touches_retrieval_or_model(
    client, investigator_headers, case, monkeypatch
):
    """The latency defect itself: an instant answer for a non-question."""

    async def exploding_retrieve(self, *args, **kwargs):  # noqa: ANN001
        raise AssertionError("greetings must not trigger case retrieval")

    async def exploding_chat(self, *args, **kwargs):  # noqa: ANN001
        raise AssertionError("greetings must not trigger a model call")

    monkeypatch.setattr(AIGateway, "_retrieve_subgraph", exploding_retrieve)
    monkeypatch.setattr(router_module.AIModelRouter, "chat", exploding_chat)

    started = time.perf_counter()
    response = client.post(
        f"/api/v1/ai/cases/{case.id}/ask",
        json={"question": "Hi"},
        headers=investigator_headers,
    )
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["role"] == "conversational"
    assert body["context"]["fast_path"] is True
    assert body["context"]["retrieved"] is False
    assert body["finding"]["summary"]
    assert elapsed < 2.0, elapsed
    # Timing proves the work skipped: no retrieval/model stages were recorded.
    timing = body["timing"]
    assert "ack_ms" in timing and "total_ms" in timing
    assert "model_ms" not in timing


def test_greeting_is_still_scoped_to_the_case_and_dataset(
    client, investigator_headers, case
):
    """Fast path must not leak an answer computed outside case visibility:
    asking a greeting on an unknown case is still a 404."""
    response = client.post(
        "/api/v1/ai/cases/nonexistent-case/ask",
        json={"question": "Hi"},
        headers=investigator_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 2. The streamed protocol (NDJSON) over the real HTTP endpoint
# ---------------------------------------------------------------------------


def _stream(client, headers, case_id: str, question: str):
    with client.stream(
        "POST",
        f"/api/v1/ai/cases/{case_id}/ask/stream",
        json={"question": question},
        headers=headers,
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in response.iter_lines() if line]
    return events


def test_stream_acknowledges_before_any_work_and_ends_with_done(
    client, investigator_headers, case
):
    events = _stream(client, investigator_headers, case.id, "hello")
    assert events[0]["type"] == "ack"
    assert events[0]["query_id"]
    kinds = [event["type"] for event in events]
    assert "stage" in kinds
    assert kinds[-1] == "done"
    done = events[-1]["response"]
    assert done["role"] == "conversational"
    assert done["available"] is True
    # A fast-path answer must not announce retrieval stages at all.
    assert not any(e.get("stage") in {"retrieving", "generating"} for e in events if e["type"] == "stage")


def test_investigation_stream_reports_every_stage_and_deltas(
    client, investigator_headers, case, monkeypatch
):
    chunks = []
    payload = json.dumps(FAKE_FINDING)
    parts = [payload[: 40], payload[40: 90], payload[90:]]

    async def scripted_stream(self, task, system_prompt=None, user_prompt=None, *, on_delta=None, **kwargs):  # noqa: ANN001
        assert task == "investigation_reasoning"
        for part in parts:
            chunks.append(part)
            if on_delta is not None:
                await on_delta(part)
        return {
            "available": True,
            "content": "".join(chunks),
            "model": "scripted-model",
            "provider": "scripted",
            "role": "REASONING",
            "latency_ms": 3,
            "streamed": True,
        }

    monkeypatch.setattr(router_module.AIModelRouter, "chat_stream", scripted_stream)

    events = _stream(
        client, investigator_headers, case.id, "Which accounts are connected through shared phones?"
    )
    types = [event["type"] for event in events]
    stages = [event.get("stage") for event in events if event["type"] == "stage"]

    assert types[0] == "ack"
    assert "retrieving" in stages
    assert "retrieval" in types          # counted nodes/edges event
    assert "generating" in stages
    assert "validating" in stages
    assert types[-1] == "done"

    deltas = [event["text"] for event in events if event["type"] == "delta"]
    assert "".join(deltas) == payload    # the UI got exactly what the model produced

    retrieval_event = next(event for event in events if event["type"] == "retrieval")
    assert retrieval_event["nodes"] >= 0
    assert "retrieval_ms" in retrieval_event

    done = events[-1]["response"]
    assert done["available"] is True
    assert done["finding"]["summary"] == FAKE_FINDING["summary"]
    timing = done["context"]["timing"]
    for key in ("ack_ms", "retrieval_ms", "context_ms", "model_ms", "total_ms"):
        assert key in timing, timing


def test_investigation_stream_survives_a_dead_provider(
    client, investigator_headers, case
):
    """No key configured: the stream must *complete* with an honest
    unavailable answer — not hang, not emit a 500 mid-stream."""
    events = _stream(
        client, investigator_headers, case.id, "Trace the money from ACCT_0001"
    )
    assert events[0]["type"] == "ack"
    done = events[-1]
    assert done["type"] == "done"
    response = done["response"]
    assert response["available"] is False
    assert response["fallback_reason"]
    assert "Traceback" not in json.dumps(response)


def test_stream_validation_failures_stay_plain_http_before_streaming(
    client, investigator_headers, case
):
    unknown = client.post(
        "/api/v1/ai/cases/does-not-exist/ask/stream",
        json={"question": "anything at all"},
        headers=investigator_headers,
    )
    assert unknown.status_code == 404
    empty = client.post(
        f"/api/v1/ai/cases/{case.id}/ask/stream",
        json={"question": "   "},
        headers=investigator_headers,
    )
    assert empty.status_code == 422


def test_retrieval_failure_becomes_an_error_event_not_a_500(
    client, investigator_headers, case, monkeypatch
):
    async def broken_retrieve(self, *args, **kwargs):  # noqa: ANN001
        raise RuntimeError("graph store exploded")

    monkeypatch.setattr(AIGateway, "_retrieve_subgraph", broken_retrieve)
    with client.stream(
        "POST",
        f"/api/v1/ai/cases/{case.id}/ask/stream",
        json={"question": "Who shares the phone 9812345672?"},
        headers=investigator_headers,
    ) as response:
        assert response.status_code == 200  # the stream opened, then explains itself
        events = [json.loads(line) for line in response.iter_lines() if line]
    final = events[-1]
    assert final["type"] in {"done", "error"}
    payload = json.dumps(final)
    assert "RuntimeError" not in payload  # no internal exception text to the UI


# ---------------------------------------------------------------------------
# 3. Router stream semantics: degrade before tokens, honest after
# ---------------------------------------------------------------------------


class _Completions:
    def __init__(self, chunks, error_after=None):
        self._chunks = chunks
        self._error_after = error_after

    async def create(self, **kwargs):
        if self._error_after is None:
            raise RuntimeError("provider rejected stream=True")

        async def generate():
            for chunk in self._chunks:
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=chunk))]
                )
            raise self._error_after

        return generate()


def _invocation(client_obj):
    return SimpleNamespace(
        available=True,
        role="REASONING",
        provider="scripted",
        model="scripted-model",
        temperature=0.1,
        max_tokens=900,
        client=lambda: client_obj,
    )


async def test_pre_token_stream_failure_degrades_to_plain_chat(monkeypatch):
    client_obj = SimpleNamespace(chat=SimpleNamespace(completions=_Completions([], error_after=ValueError("rejected"))))
    calls = {}

    async def fake_chat(self, task, system_prompt=None, user_prompt=None, **kwargs):  # noqa: ANN001
        calls["task"] = task
        return {"available": True, "content": json.dumps(FAKE_FINDING), "model": "scripted-model",
                "provider": "scripted", "role": "REASONING", "latency_ms": 2}

    router = router_module.AIModelRouter.__new__(router_module.AIModelRouter)
    monkeypatch.setattr(router_module.AIModelRouter, "route", lambda self, task: _invocation(client_obj))
    monkeypatch.setattr(router_module.AIModelRouter, "chat", fake_chat)

    seen = []
    result = await router.chat_stream(
        "investigation_reasoning", "sys", "user", on_delta=seen.append
    )
    assert result["available"] is True
    assert calls["task"] == "investigation_reasoning"
    assert seen == []  # no partial text was shown, so a retry is safe


async def test_mid_stream_failure_reports_partial_without_retry(monkeypatch):
    client_obj = SimpleNamespace(
        chat=SimpleNamespace(completions=_Completions(["Here is ", "part of the answer"], error_after=ConnectionError("socket reset")))
    )
    shown = []

    async def on_delta(text):
        shown.append(text)

    async def must_not_chat(self, *args, **kwargs):  # noqa: ANN001
        raise AssertionError("after tokens were shown the router must not silently re-ask")

    router = router_module.AIModelRouter.__new__(router_module.AIModelRouter)
    monkeypatch.setattr(router_module.AIModelRouter, "route", lambda self, task: _invocation(client_obj))
    monkeypatch.setattr(router_module.AIModelRouter, "chat", must_not_chat)

    result = await router.chat_stream("investigation_reasoning", "sys", "user", on_delta=on_delta)
    assert result["available"] is False
    assert result["partial"] is True
    assert result["content"] == "Here is part of the answer"
    assert result["reason"].startswith("stream_interrupted")
    assert shown == ["Here is ", "part of the answer"]
