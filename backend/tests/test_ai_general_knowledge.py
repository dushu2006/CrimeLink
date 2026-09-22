"""General-knowledge questions: answered directly, never case-dumped.

"What is the capital of India?" is not a case question.  It must not trigger
case retrieval, must not receive case citations, must not be padded with
evidence counts or relationships, and — when no provider is configured — must
degrade to one honest sentence rather than a case summary.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.ai.gateway import AIGateway
from app.config import Settings


def _gateway_with_model() -> tuple[AIGateway, MagicMock]:
    mock_router = MagicMock()
    mock_router.chat = AsyncMock(return_value={
        "available": True,
        "content": "The capital of India is New Delhi.",
        "model": "test-model",
        "latency_ms": 12,
    })
    return AIGateway(settings=Settings(), router=mock_router), mock_router


def _gateway_without_model() -> tuple[AIGateway, MagicMock]:
    mock_router = MagicMock()
    mock_router.chat = AsyncMock(return_value={
        "available": False, "reason": "no_api_key_for_role_classification",
    })
    return AIGateway(settings=Settings(), router=mock_router), mock_router


async def _exploding_retrieval(self, *args, **kwargs):  # noqa: ANN001, ANN202
    raise AssertionError("general-knowledge questions must not run case retrieval")


async def test_general_question_is_answered_without_case_retrieval(monkeypatch):
    gateway, mock_router = _gateway_with_model()
    monkeypatch.setattr(AIGateway, "_retrieve_subgraph", _exploding_retrieval)
    monkeypatch.setattr(AIGateway, "_retrieve_case_documents", _exploding_retrieval)

    response = await gateway.ask(
        question="What is the capital of India?",
        case_id="case-1",
        dataset_id="ds-1",
    )
    answer = response.finding.summary
    assert answer == "The capital of India is New Delhi."
    assert response.finding.finding_type == "GENERAL_KNOWLEDGE"
    assert response.context.get("answer_kind") == "general_knowledge"
    # No case evidence, no citations, no sources panel material.
    assert (response.finding.presentation or {}).get("sources") == []
    assert not response.finding.claims
    assert "FIR" not in answer and "evidence" not in answer.lower()
    assert mock_router.chat.called
    # The general-answer call carried no case context.
    _, kwargs = mock_router.chat.call_args
    assert "case" not in kwargs["user_prompt"].lower() or "capital" in kwargs["user_prompt"].lower()


async def test_general_question_without_a_model_is_one_honest_sentence(monkeypatch):
    gateway, _ = _gateway_without_model()
    monkeypatch.setattr(AIGateway, "_retrieve_subgraph", _exploding_retrieval)
    monkeypatch.setattr(AIGateway, "_retrieve_case_documents", _exploding_retrieval)

    response = await gateway.ask(
        question="What is the capital of India?",
        case_id="case-1",
        dataset_id="ds-1",
    )
    answer = response.finding.summary
    assert response.available is True
    assert (response.finding.presentation or {}).get("sources") == []
    # Honest, short, and not an obscure provider error.
    assert "narrative" not in answer.lower()
    assert "APIStatusError" not in answer
    assert "API key" not in answer
    assert "case" in answer.lower()  # it explains what it *can* do


async def test_case_questions_are_not_diverted_to_general_knowledge(monkeypatch):
    """A question with case vocabulary must stay on the case pipeline even
    though it superficially resembles a general one."""
    gateway, mock_router = _gateway_without_model()

    async def _nodes(self, case_id):  # noqa: ANN001, ANN202
        return []

    async def _empty_subgraph(self, *args, **kwargs):  # noqa: ANN001, ANN202
        return [], []

    async def _no_docs(self, *args, **kwargs):  # noqa: ANN001, ANN202
        return []

    monkeypatch.setattr(AIGateway, "_get_all_case_nodes", _nodes)
    monkeypatch.setattr(AIGateway, "_retrieve_subgraph", _empty_subgraph)
    monkeypatch.setattr(AIGateway, "_retrieve_case_documents", _no_docs)

    response = await gateway.ask(
        question="What does the FIR say?",
        case_id="case-1",
        dataset_id="ds-1",
    )
    assert response.context.get("answer_kind") != "general_knowledge"
    # The case pipeline ran: it reached the *reasoning* role, not the
    # general-knowledge classification shortcut.
    for call in mock_router.chat.call_args_list:
        assert call.args[0] == "investigation_reasoning"


async def test_followup_fragments_are_never_general_knowledge(monkeypatch):
    gateway, mock_router = _gateway_with_model()
    monkeypatch.setattr(AIGateway, "_retrieve_subgraph", _exploding_retrieval)

    called = {"hits": 0}

    async def _nodes(self, case_id):  # noqa: ANN001, ANN202
        called["hits"] += 1
        return []

    async def _empty_subgraph(self, *args, **kwargs):  # noqa: ANN001, ANN202
        return [], []

    async def _no_docs(self, *args, **kwargs):  # noqa: ANN001, ANN202
        return []

    monkeypatch.setattr(AIGateway, "_get_all_case_nodes", _nodes)
    monkeypatch.setattr(AIGateway, "_retrieve_subgraph", _empty_subgraph)
    monkeypatch.setattr(AIGateway, "_retrieve_case_documents", _no_docs)

    history = [
        {"role": "user", "content": "What is Rahul's phone number?"},
        {"role": "assistant", "content": "No case record documents it."},
    ]
    response = await gateway.ask(
        question="What about Ravi?",
        case_id="case-1",
        dataset_id="ds-1",
        history=history,
    )
    assert response.context.get("answer_kind") != "general_knowledge"
    assert response.context["followup_resolution"]["is_followup"] is True
    # The rewritten question is about Ravi's phone number, not a diversion.
    rewritten = response.context["followup_resolution"]["rewritten"] or ""
    assert "phone number" in rewritten.lower()
    assert "Ravi" in rewritten
