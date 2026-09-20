from __future__ import annotations

import json
from types import SimpleNamespace

from app.ai.gateway import AIGateway
from app.domain.models import CaseGraphSnapshot, GraphNode


def _payload(prompt: str) -> dict:
    """The JSON evidence package at the tail of the prompt.

    The prompt shape is owned by ``app.ai.response_composer``; this helper finds
    the last balanced JSON object rather than depending on a key ordering.
    """
    start = prompt.find("{")
    while start != -1:
        try:
            payload = json.loads(prompt[start:])
            if isinstance(payload, dict):
                return payload
        except ValueError:
            pass
        start = prompt.find("{", start + 1)
    raise AssertionError("the prompt did not contain a JSON evidence package")


def _question_sent_to_model(prompt: str) -> str:
    """The investigator's question as the provider receives it."""
    payload = _payload(prompt)
    if "question" in payload:
        return str(payload["question"])
    # Composer may nest it; search the serialized payload text as a fallback.
    match = json.loads(json.dumps(payload))
    if isinstance(match, dict):
        for value in match.values():
            if isinstance(value, dict) and "question" in value:
                return str(value["question"])
    raise AssertionError("no question found in the evidence package")


class RecordingRouter:
    def __init__(self) -> None:
        self.user_prompts: list[str] = []

    async def chat(self, task: str, system_prompt: str, user_prompt: str, **_: object) -> dict:
        self.user_prompts.append(user_prompt)
        return {
            "available": True,
            "content": json.dumps({
                "finding_type": "NETWORK",
                "summary": "The pseudonymous subject has a supported relationship.",
                "confidence": 0.8,
                "evidence_level": "FACT",
                "recommended_review": True,
            }),
            "model": "test-model",
        }


def _nodes() -> list[dict]:
    return [
        {
            "provenance_key": "person:chetan",
            "label": "Person",
            "properties": {"name": "Chetan Patel", "case_id": "case-1"},
            "confidence": 1.0,
        },
        {
            "provenance_key": "phone:1",
            "label": "Phone",
            "properties": {"number": "+919876543210", "case_id": "case-1"},
            "confidence": 1.0,
        },
    ]


async def _ask(settings, monkeypatch, question: str, *, target_key: str | None = None):
    monkeypatch.setattr(settings, "ai_allow_raw_pii", False)
    monkeypatch.setattr(settings, "ai_pseudonymize", True)
    router = RecordingRouter()
    gateway = AIGateway(settings=settings, router=router)

    async def retrieve(case_id: str, *, depth: int = 2, target_key: str | None = None):
        return _nodes(), [{
            "source_key": "person:chetan",
            "target_key": "phone:1",
            "rel_type": "USES_PHONE",
            "confidence": 1.0,
        }]

    monkeypatch.setattr(gateway, "_retrieve_subgraph", retrieve)
    await gateway.ask(
        question=question,
        case_id="case-1",
        target_key=target_key,
        dataset_id="dataset-1",
    )
    return router.user_prompts[0]


async def test_target_question_uses_the_same_pseudonym_as_context(settings, monkeypatch):
    prompt = await _ask(
        settings,
        monkeypatch,
        "Analyse the network around Chetan Patel within this case.",
        target_key="person:chetan",
    )

    assert "PERSON_001" in prompt
    assert "Chetan Patel" not in prompt
    # The pseudonym the question uses is the same one the evidence uses, so the
    # model can resolve the subject without ever seeing her real identity.
    assert "PERSON_001" in _question_sent_to_model(prompt)


async def test_free_form_question_rewrites_in_scope_name(settings, monkeypatch):
    prompt = await _ask(settings, monkeypatch, "What connects Chetan Patel to this case?")

    assert "PERSON_001" in prompt
    assert "Chetan Patel" not in prompt
    assert "PERSON_001" in _question_sent_to_model(prompt)


async def test_free_form_question_leaves_out_of_scope_name_unchanged(settings, monkeypatch):
    prompt = await _ask(settings, monkeypatch, "What connects Sachin Joshi to this case?")

    # A name that is not in this case cannot be pseudonymized — it must survive
    # verbatim, and no case pseudonym may be invented for it.
    assert "Sachin Joshi" in prompt
    question = _question_sent_to_model(prompt)
    assert "Sachin Joshi" in question
    assert "PERSON_001" not in question


def test_ai_analysis_stage_persists_a_present_entity_finding(settings, monkeypatch):
    from app.services import investigation

    router = RecordingRouter()
    gateway = AIGateway(
        settings=settings.model_copy(update={"ai_api_key": "test-key"}),
        router=router,
    )

    async def retrieve(case_id: str, *, depth: int = 2, target_key: str | None = None):
        return _nodes(), [{
            "source_key": "person:chetan",
            "target_key": "phone:1",
            "rel_type": "USES_PHONE",
            "confidence": 1.0,
        }]

    monkeypatch.setattr(gateway, "_retrieve_subgraph", retrieve)
    monkeypatch.setattr("app.ai.gateway.get_ai_gateway", lambda: gateway)
    monkeypatch.setattr(
        investigation,
        "compute_centrality",
        lambda snapshot, current_settings: SimpleNamespace(
            betweenness={"person:chetan": 1.0}, community_members={}
        ),
    )
    persisted: list[object] = []
    monkeypatch.setattr(
        investigation,
        "_persist_ai_finding",
        lambda case_id, person_key, name, response: persisted.append(response),
    )

    class Graph:
        def snapshot(self, case_id: str, include_staging: bool = False):
            return CaseGraphSnapshot(
                case_id=case_id,
                nodes={"person:chetan": GraphNode("person:chetan", "Person", {"name": "Chetan Patel"})},
            )

    container = SimpleNamespace(graph_store=Graph())
    result = investigation._stage_ai_analysis(
        container,
        settings.model_copy(update={"ai_api_key": "test-key"}),
        "case-1",
        "user-1",
    )

    assert result["answers_available"] == 1
    assert len(persisted) == 1
    assert "no node or reference to" not in persisted[0].finding.summary.lower()
    assert "cannot be identified" not in persisted[0].finding.summary.lower()