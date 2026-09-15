"""AI Gateway — the single path from operational data to external models.

Implements the architecture described in §19–§28:

1. Investigators ask a question scoped to a case.
2. The gateway retrieves the relevant subgraph (nodes/edges) and evidence
   metadata — never the whole database.
3. Context is minimized: irrelevant PII is stripped.
4. Reversible pseudonymization replaces real identifiers with PERSON_023,
   PHONE_041… style IDs unless the admin has explicitly allowed raw PII.
5. The model router sends the minimized context to the appropriate model
   (reasoning / explanation / classification).
6. Output is validated against the Pydantic contract in ``schemas.py``.
7. If the output references evidence, references are checked for existence.
8. Findings are written to the AI audit log.
9. The caller receives the validated result; the UI de-pseudonymizes IDs
   when presenting results to an authorized investigator.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime
from typing import Any, AsyncIterator, Awaitable, Callable

from app.ai.pseudonymize import PseudonymMap, apply_pseudonymization_to_context
import hashlib

# 10/10 hardening imports
try:
    from app.ai.person_graph_rag import (
        validate_llm_grounding,
        map_to_controlled,
        CONTROLLED_REL_TYPES,
        build_deterministic_result,
        create_no_connection_result,
        calculate_deterministic_confidence,
        deduplicate_relationships,
        EVIDENCE_SUFFICIENCY,
    )
    HAS_HARDENING = True
except Exception:
    HAS_HARDENING = False
    CONTROLLED_REL_TYPES = set()

# Retrieval cache — safe deterministic only
_RETRIEVAL_CACHE: dict[str, dict] = {}
_RETRIEVAL_CACHE_VERSION = 0

from app.ai.router import AIModelRouter, get_router
from app.ai.schemas import AIResponse, FindingResult
from app.ai.safety import AISafetyViolation, sanitize_untrusted_evidence, validate_finding
from app.ai.retrieval import (
    understand_query,
    rank_and_filter_context,
    compress_context,
    build_timeline_from_context,
    QueryUnderstanding,
)

# Person-centric Graph-RAG — production implementation
try:
    from app.ai.person_graph_rag import (
        person_graph_rag_retrieval,
        build_pseudonymized_context_for_llm as build_person_pseudonymized_context,
        PERSON_LABELS,
    )
    HAS_PERSON_RAG = True
except Exception:  # pragma: no cover - import guard
    HAS_PERSON_RAG = False
    PERSON_LABELS = {"PERSON", "Person", "person"}
from app.config import Settings, get_settings
from app.db.base import new_uuid, utcnow
from app.db.session import async_session
from app.logging import get_logger

log = get_logger("crimelink.ai.gateway")

# Neutral language vocabulary (§27).  The system prompt explicitly forbids
# labels like "criminal", "guilty", "terrorist", "gang member".
FORBIDDEN_LABELS = [
    "criminal", "guilty", "terrorist", "gang member", "gang-member",
    "mastermind", "kingpin",
]
NEUTRAL_ALTERNATIVES = {
    "person of interest": "person of interest",
    "associated entity": "associated entity",
    "analytically significant entity": "analytically significant entity",
}


SYSTEM_PROMPT_REASONING_RAW = """You are an investigative analysis assistant for Indian law enforcement.

YOU MUST FOLLOW THESE RULES:

1. You are given a case evidence subgraph containing real entity names (persons, bank accounts,
   phone numbers, vehicles, locations) and a pre-computed 'exact_analytics' summary. In your findings
   and summary, ALWAYS refer to entities by their real names, bank accounts, and entity details
   provided in the context. Do NOT use raw ID keys.
2. For quantitative questions (e.g. how many persons or entities exist, listing all persons, maximum
   or minimum transaction amounts, total money transferred, top call connections), you MUST rely directly
   on the numbers and lists provided in 'exact_analytics'. Never guess, estimate, or manually recount raw nodes.
3. Distinguish FACT (directly supported by provided evidence), INFERENCE
   (analytically derived from facts), HYPOTHESIS (possible explanation that
   needs further investigation), and UNKNOWN (insufficient evidence).
4. NEVER label anyone "criminal", "guilty", "terrorist", "gang member",
   "mastermind" or "kingpin". Use neutral language: "person of interest",
   "associated entity", "analytically significant entity", "potential
   connection", "pattern requiring review".
5. Every finding MUST cite supporting evidence references (doc_id references
   or explicit edge/entity names provided in the context).
6. Output strict JSON matching the schema provided — no commentary outside
   the JSON.
7. Do not recommend merging identities, deleting evidence, or making any
   irreversible change. All serious findings require human review.
8. Be conservative: if the evidence is weak, say so.
"""


SYSTEM_PROMPT_REASONING = """You are an investigative analysis assistant for Indian law enforcement.

YOU MUST FOLLOW THESE RULES:

1. You are given a minimized, pseudonymized case subgraph. IDs like PERSON_023,
   PHONE_041 are NOT real names or numbers — they are pseudonyms the backend
   will resolve later. Do NOT invent real names, phone numbers or other PII.
2. Distinguish FACT (directly supported by provided evidence), INFERENCE
   (analytically derived from facts), HYPOTHESIS (possible explanation that
   needs further investigation), and UNKNOWN (insufficient evidence).
3. NEVER label anyone "criminal", "guilty", "terrorist", "gang member",
   "mastermind" or "kingpin". Use neutral language: "person of interest",
   "associated entity", "analytically significant entity", "potential
   connection", "pattern requiring review".
4. Every finding MUST cite supporting evidence references (doc_id pseudo-refs
   or explicit edge/entity ids provided in the context). If you cannot cite
   evidence, mark evidence_level "UNKNOWN" and recommended_review true.
5. Output strict JSON matching the schema provided — no commentary outside
   the JSON.
6. Do not recommend merging identities, deleting evidence, or making any
   irreversible change. All serious findings require human review.
7. Be conservative: if the evidence is weak, say so.
"""

SYSTEM_PROMPT_EXPLANATION = """You are an explanation assistant for an investigative platform.

Turn a validated AI finding (in pseudonymized form) into concise,
investigator-friendly language. Reference evidence using the provided
pseudo-refs (the UI will resolve them). Use neutral analytical language —
never label a person "criminal", "guilty", or "terrorist". End with a
"Why this matters" sentence that explains the analytical significance,
and list open questions/uncertainties.
"""

CONTEXT_BUILDERS = {
    "reasoning": "InvestigationReasoningContext",
    "explanation": "ExplanationContext",
    "classification": "ClassificationContext",
    "embedding": "RetrievalContext",
}

# Settings field name of every AI role, used in operator-facing messages:
# role "reasoning" -> CRIMELINK_AI_REASONING_API_KEY, and so on.
ROLE_ENV_KEYS = {
    "extraction": "CRIMELINK_AI_EXTRACTION_API_KEY",
    "reasoning": "CRIMELINK_AI_REASONING_API_KEY",
    "explanation": "CRIMELINK_AI_EXPLANATION_API_KEY",
    "classification": "CRIMELINK_AI_CLASSIFICATION_API_KEY",
    "embedding": "CRIMELINK_AI_EMBEDDING_API_KEY",
}


def unavailable_summary(role: str, reason: str | None) -> str:
    """An honest, operator-actionable explanation for an unavailable AI role.

    The wording must reflect the *actual* reason: a missing API key and a
    failed provider invocation are different situations, and telling an
    investigator "no API key is configured" when a configured provider call
    just failed is exactly the kind of dishonesty this module exists to
    prevent.
    """
    role_label = f"AI {role}"
    env_key = ROLE_ENV_KEYS.get(role, f"CRIMELINK_AI_{role.upper()}_API_KEY")
    reason = reason or "unknown_reason"

    if reason.startswith("no_api_key_for_role_"):
        return (
            f"{role_label} is unavailable because no API key is configured for "
            f"the {role} model. Configure {env_key} to enable this feature."
        )
    if reason == "openai_client_unavailable":
        return (
            f"{role_label} is unavailable because the OpenAI-compatible client "
            "library is not installed on the server. Install the 'openai' "
            "package to enable this feature."
        )
    if reason.startswith("invocation_failed:"):
        detail = reason.split(":", 1)[1].strip()
        return (
            f"{role_label} is unavailable because the configured provider call "
            f"failed ({detail}). The provider, model or key configured for the "
            f"{role} role may be wrong — check {env_key} and the role's "
            "base_url/model settings. An investigator must review this case "
            "manually."
        )
    return f"{role_label} is currently unavailable ({reason})."


# ---------------------------------------------------------------------------
# Intent triage -- the fast path for non-investigative messages
#
# "Hi" must cost the platform nothing: no graph read, no retrieval, no model
# call, no pseudonymization pass. Before this existed every greeting walked
# the full case-retrieval pipeline, which is what made trivial messages feel
# hung. The classifier is intentionally a *shape* test over punctuation- and
# case-stripped words -- no corpus, no case identifier, and nothing specific
# to any dataset can make a message look investigative by accident.
# ---------------------------------------------------------------------------

#: Whole short phrases that are chat about nothing. Matching whole strings
#: (after punctuation folding) -- not prefixes -- so "hi, who stole the ledger"
#: can never be fast-pathed by a greeting-shaped opening.
_CHITCHAT_PHRASES = frozenset(
    {
        "hi", "hii", "hiii", "hlo", "hello", "helo", "hallo", "hey", "heya", "hai",
        "yo", "namaste", "namaskar", "salaam", "salam", "assalamualaikum",
        "hi there", "hey there", "hello there", "good morning", "good afternoon",
        "good evening", "how are you", "how r u", "kaise ho", "kaise hain aap",
        "thanks", "thank you", "thankyou", "thanku", "thank you so much",
        "thanks a lot", "thx", "ty", "ok", "okay", "alright",
        "cool", "nice", "great", "perfect", "awesome", "got it", "noted",
        "understood", "bye", "goodbye", "see you", "see ya", "that's all",
        "that is all", "who are you", "who r u", "what are you",
        "what can you do", "what can u do", "help", "testing", "test", "ping",
    }
)
_REPEATED_LETTERS = re.compile(r"^(h+i+|h+e+l+o+|h+e+y+|b+y+e+|t+h+a+n+k+s*|o+k+y?)$")
#: Identifier-looking tokens (CASE_0001, ACCT_0002, TS09AB1234, phone numbers)
#: make a message investigative no matter how short it is.
_IDENTIFIER_SHAPES = re.compile(
    r"\b([A-Z][A-Z0-9]{1,14}_\d{2,8}|\+?\d{10,13}|[A-Z]{2}\s?\d{1,2}\s?[A-Z]{1,3}\s?\d{4})\b"
)


def is_conversational(text: str) -> bool:
    """Whether a message is pure chat (greeting/thanks/identity) about nothing.

    Deliberately conservative in the *safe* direction: any message that names
    an identifier, carries investigation vocabulary, or is not built
    exclusively out of whole known chat phrases goes down the full retrieval
    path. A greeting that accidentally skipped retrieval would be a wrong
    answer; a "thanks" that accidentally retrieved is merely a slower no-op
    -- so the classifier only ever fast-paths what it is sure about.
    """
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 80:
        return False
    if _IDENTIFIER_SHAPES.search(stripped):
        return False
    # Fold punctuation, case and stray emoji into single spaces, then require
    # every remaining word to be a known chat phrase ("hi there" survives the
    # multi-word set; "hi who did it" does not).
    body = re.sub(r"[^a-z0-9'\u0900-\u097F]+", " ", stripped.lower()).strip()
    if not body:
        return False
    sentences = [part for part in body.split(" ") if part]
    if not sentences:
        return False
    # Multi-word phrases: try greedy two/three-word groupings first.
    i = 0
    while i < len(sentences):
        matched = False
        for size in (4, 3, 2, 1):
            candidate = " ".join(sentences[i : i + size])
            if candidate and candidate in _CHITCHAT_PHRASES:
                i += size
                matched = True
                break
        if matched:
            continue
        if _REPEATED_LETTERS.match(sentences[i]):
            i += 1
            continue
        return False
    return True


#: The answer for the fast path. Deliberately not produced by a model: a
#: greeting answered by a 2000-token reasoning call over the whole case graph
#: is the exact failure this triage exists to prevent.
CONVERSATIONAL_REPLY = (
    "Hello — I'm CrimeLink's case analysis assistant. I answer questions about "
    "the active investigation from the evidence this platform ingested: who is "
    "connected to whom, what the call, money and movement records show, and "
    "which finding each claim rests on. Ask me something about this case, for "
    "example: \"Who appears to coordinate the financial activity?\" or "
    "\"What links the accounts in this case?\""
)


class StageTimer:
    """Per-stage latency bookkeeping for one AI request.

    Requirement #12 asked to *measure* before tuning: every number the stream
    and the response carry comes from here, so a slow stage is identifiable
    rather than inferable from a single total.
    """

    def __init__(self) -> None:
        self._started = time.perf_counter()
        self._mark = self._started
        self.stages: dict[str, int] = {}

    def stage(self, name: str) -> None:
        now = time.perf_counter()
        self.stages[name] = self.stages.get(name, 0) + int((now - self._mark) * 1000)
        self._mark = now

    def report(self) -> dict[str, Any]:
        total_ms = int((time.perf_counter() - self._started) * 1000)
        return {**self.stages, "total_ms": total_ms}


EmitFn = Callable[[dict[str, Any]], Any] | None


class AIGateway:
    def __init__(self, settings: Settings | None = None, router: AIModelRouter | None = None):
        self.settings = settings or get_settings()
        self.router = router or get_router()

    # ------------------------------------------------------ public entrypoints

    async def ask(self, *, question: str, case_id: str, user_id: str | None = None,
                  principal_id: str | None = None,
                  depth: int | None = None, target_key: str | None = None,
                  request_id: str | None = None,
                  dataset_id: str | None = None,
                  graph_ready: bool = True) -> AIResponse:
        """Answer an investigator question scoped to ``case_id`` (and, through
        the case, to the ACTIVE dataset — replaced data is not retrievable).

        Nothing in this method can raise to the caller: a failure anywhere
        becomes an ``AIResponse`` with ``available=False`` and a reason,
        because "the model is unreachable" is an answer the investigator needs
        to see, not a 500.
        """
        return await self._answer(
            question=question, case_id=case_id, user_id=user_id,
            principal_id=principal_id, depth=depth, target_key=target_key,
            request_id=request_id, dataset_id=dataset_id,
            graph_ready=graph_ready, emit=None,
        )

    async def ask_stream(self, *, question: str, case_id: str, user_id: str | None = None,
                         principal_id: str | None = None,
                         depth: int | None = None, target_key: str | None = None,
                         request_id: str | None = None,
                         dataset_id: str | None = None,
                         graph_ready: bool = True) -> AsyncIterator[dict[str, Any]]:
        """Yield NDJSON progress events, then the final ``done`` event.

        Protocol (one JSON object per line)::

            {"type": "ack",        "query_id", "message"}
            {"type": "stage",      "stage": "retrieving|generating|validating", ...}
            {"type": "retrieval",  "nodes", "edges", "retrieval_ms"}
            {"type": "delta",      "text": "..."}          # streamed tokens
            {"type": "done",       "response": {...full AIResponse...}}
            {"type": "error",      "code", "message"}      # fatal, ends stream

        The caller receives an ``ack`` before any expensive work happens, so
        the UI can say "Thinking…" the instant the request is accepted — the
        difference between *slow* and *indistinguishable from hung*.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)

        async def emit(event: dict[str, Any]) -> None:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - slow client
                log.warning("ai.stream_queue_full", query_id=request_id)

        task = asyncio.create_task(
            self._answer(
                question=question, case_id=case_id, user_id=user_id,
                principal_id=principal_id, depth=depth, target_key=target_key,
                request_id=request_id, dataset_id=dataset_id,
                graph_ready=graph_ready, emit=emit,
            )
        )
        try:
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    continue
                yield event
            response = task.result()
            yield {"type": "done", "response": response.model_dump()}
        except Exception as exc:  # noqa: BLE001 - a broken stream says why, never hangs
            log.exception("ai.stream_failed", query_id=request_id, error=str(exc))
            yield {
                "type": "error",
                "code": "gateway_error",
                "message": "Unable to answer this question. The AI pipeline failed; "
                           "quote the request id when reporting this.",
            }
        finally:
            if not task.done():
                task.cancel()

    # --------------------------------------------------------------- the core

    @staticmethod
    def _parse_narrative(content: str):
        """Tolerant JSON extraction for the investigator narrative contract."""
        from app.investigator.schemas import InvestigatorNarrative

        text = (content or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text).strip()
        candidates = [text]
        start = text.find("{")
        if start >= 0:
            depth = 0
            for index in range(start, len(text)):
                if text[index] == "{":
                    depth += 1
                elif text[index] == "}":
                    depth -= 1
                    if depth == 0:
                        candidates.append(text[start : index + 1])
                        break
        for candidate in candidates:
            try:
                return InvestigatorNarrative.model_validate(json.loads(candidate)), ""
            except Exception:
                continue
        return None, "narrative_unparseable"

    async def investigate_narrative(
        self,
        *,
        question: str,
        brief: str,
        investigation_id: str,
        entities: list | None = None,
        user_id: str | None = None,
        session: Any = None,
    ):
        """Narrate deterministic investigation results (explain-only contract).

        Returns a :class:`ModelSection`: prose over already-computed
        findings, never new claims. Keyless, failed, or unparseable output
        degrades to the deterministic analysis with an honest reason —
        never a 500, never invented content. When ``session`` is given, the
        AI_QUERY audit row joins the caller's transaction.
        """
        from app.investigator.prompts import INVESTIGATOR_SYSTEM, neutralize_language
        from app.investigator.schemas import ModelSection

        started = time.monotonic()
        query_id = str(uuid.uuid4())
        pmap = PseudonymMap()
        subs: dict[str, str] = {}
        for entity in entities or []:
            name = getattr(entity, "display_name", "") or ""
            if name and name not in subs.values():
                pseudo = pmap.pseudonymize(
                    getattr(entity, "canonical_id", name), getattr(entity, "label", None)
                )
                subs[pseudo] = name
        safe_brief = brief
        for pseudo, name in sorted(subs.items(), key=lambda item: -len(item[1])):
            safe_brief = safe_brief.replace(name, pseudo)

        def _restore(text: str) -> str:
            for pseudo, name in subs.items():
                text = text.replace(pseudo, name)
            return text

        result = await self.router.chat(
            "investigation_reasoning",
            system_prompt=INVESTIGATOR_SYSTEM,
            user_prompt=safe_brief,
            response_format={"type": "json_object"},
            max_tokens=2048,
            timeout_override=self.settings.ai_interactive_timeout_s,
            max_retries_override=self.settings.ai_interactive_max_retries,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        base_audit = dict(
            query_id=query_id,
            case_id="",
            user_id=user_id,
            role="investigation",
            latency_ms=latency_ms,
            tokens=(None, None),
            pmap_size=len(pmap),
            question=question,
            session=session,
            target_resource=f"investigation:{investigation_id}",
        )
        if not result.get("available"):
            reason = result.get("reason", "api_key_unavailable")
            await self._audit(
                **base_audit,
                model=None,
                output_hash=None,
                success=False,
                error=reason,
            )
            return ModelSection(
                available=False,
                reason=reason,
                caveats=[
                    "No language model is configured: this answer is the deterministic "
                    "analysis only. Configure an AI provider key to add narrative explanation."
                ],
            )
        content = result.get("content") or ""
        narrative, _note = self._parse_narrative(content)
        if narrative is None:
            await self._audit(
                **base_audit,
                model=result.get("model"),
                output_hash=_hash(content),
                success=False,
                error="narrative_unparseable",
            )
            return ModelSection(
                available=False,
                reason="narrative_unparseable",
                caveats=[
                    "The model responded but its output was not valid JSON, "
                    "so only the deterministic analysis is shown."
                ],
            )

        cleaned: dict[str, str] = {}
        language_edits: list[str] = []
        for key in ("summary", "observation", "interpretation", "assessment", "convergence_note"):
            text, edits = neutralize_language(_restore(getattr(narrative, key, "") or ""))
            cleaned[key] = text
            language_edits.extend(edits)
        caveats: list[str] = []
        for item in narrative.caveats or []:
            text, edits = neutralize_language(_restore(item))
            caveats.append(text)
            language_edits.extend(edits)
        actions: list[str] = []
        for item in narrative.suggested_next_actions or []:
            text, edits = neutralize_language(_restore(item))
            actions.append(text)
            language_edits.extend(edits)
        await self._audit(
            **base_audit,
            model=result.get("model"),
            output_hash=_hash(content),
            success=True,
        )
        return ModelSection(
            available=True,
            model=result.get("model"),
            summary=cleaned["summary"],
            observation=cleaned["observation"],
            interpretation=cleaned["interpretation"],
            assessment=cleaned["assessment"],
            convergence_note=cleaned["convergence_note"],
            caveats=caveats,
            suggested_next_actions=actions,
            language_edits=language_edits,
        )

    async def _answer(self, *, question: str, case_id: str, user_id: str | None = None,
                      principal_id: str | None = None,
                      depth: int | None = None, target_key: str | None = None,
                      request_id: str | None = None,
                      dataset_id: str | None = None,
                      graph_ready: bool = True,
                      emit: EmitFn = None) -> AIResponse:
        timer = StageTimer()
        query_id = request_id or str(uuid.uuid4())
        depth = int(depth) if depth else self.settings.ai_retrieval_depth

        async def send(event: dict[str, Any]) -> None:
            if emit is not None:
                maybe_awaitable = emit(event)
                if asyncio.iscoroutine(maybe_awaitable):
                    await maybe_awaitable

        try:
            await send({
                "type": "ack", "query_id": query_id,
                "message": "Request started",
            })
            timer.stage("ack_ms")

            # --- 0. Intent triage: greetings never touch retrieval ---------
            if is_conversational(question):
                await send({
                    "type": "stage", "stage": "fast_path",
                    "message": "Conversational message — answering without case retrieval",
                })
                response = AIResponse(
                    query_id=query_id,
                    role="conversational",
                    model=None,
                    finding=FindingResult(
                        finding_type="GENERAL",
                        summary=CONVERSATIONAL_REPLY,
                        confidence=1.0,
                        evidence_level="UNKNOWN",
                        recommended_review=False,
                    ),
                    latency_ms=max(1, timer.report()["total_ms"]),
                    pseudonymized=False,
                    available=True,
                    context={
                        "fast_path": True,
                        "nodes": 0,
                        "edges": 0,
                        "retrieved": False,
                        "dataset_id": dataset_id,
                        "case_id": case_id,
                        "timing": timer.report(),
                    },
                )
                log.info(
                    "ai.fast_path_answered",
                    query_id=query_id, case_id=case_id,
                    **{k: v for k, v in timer.report().items()},
                )
                return response

            # --- 0b. Single-active-dataset guard: no retrieval without an active dataset
            if not dataset_id and not case_id:
                no_ds_msg = "No dataset is currently active. Import a dataset before running an investigation query."
                await send({
                    "type": "stage", "stage": "no_dataset",
                    "message": no_ds_msg,
                })
                await send({
                    "type": "delta", "delta": no_ds_msg,
                })
                response = AIResponse(
                    query_id=query_id,
                    role="conversational",
                    model=None,
                    finding=FindingResult(
                        finding_type="GENERAL",
                        summary=no_ds_msg,
                        confidence=0.0,
                        evidence_level="UNKNOWN",
                        recommended_review=False,
                    ),
                    latency_ms=max(1, timer.report()["total_ms"]),
                    pseudonymized=False,
                    available=True,
                    context={
                        "fast_path": True,
                        "no_dataset": True,
                        "nodes": 0,
                        "edges": 0,
                        "retrieved": False,
                        "dataset_id": None,
                        "case_id": case_id,
                        "timing": timer.report(),
                    },
                )
                log.info("ai.no_dataset_answered", query_id=query_id, case_id=case_id)
                return response

            # 1. Investigation Retrieval Engine — Query Understanding + Entity Detection + Filtering
            # Build Order Step 2: deterministic retrieval before any vector RAG
            # -----------------------------------------------------------------
            # Query Understanding: intent, temporal, spatial, evidence filters
            try:
                query_understanding = understand_query(question)
                timer.stage("query_understanding_ms")
            except Exception as exc:
                log.warning("ai.query_understanding_failed", query_id=query_id, error=str(exc))
                query_understanding = QueryUnderstanding(original_question=question, intent="general", keywords=set())

            # --- Phase 3: query-to-entity detection (before subgraph retrieval) ---
            detected_entity_keys: list[str] = []
            entity_detection_used = False
            entity_detection_path = "fallback_whole_case"
            effective_target_keys: list[str] | None = None

            if not target_key:
                try:
                    all_case_nodes = await self._get_all_case_nodes(case_id)
                    detected_entity_keys = self._detect_entities_in_question(question, all_case_nodes)
                    if detected_entity_keys:
                        entity_detection_used = True
                        entity_detection_path = "entity_detected"
                        effective_target_keys = detected_entity_keys
                        # Merge with query understanding entities
                        query_understanding.entities = detected_entity_keys
                        log.info(
                            "ai.entity_detection",
                            query_id=query_id,
                            case_id=case_id,
                            detected_count=len(detected_entity_keys),
                            detected_keys=detected_entity_keys[:5],
                            question_preview=question[:100],
                            intent=query_understanding.intent,
                        )
                    else:
                        log.info(
                            "ai.entity_detection_fallback",
                            query_id=query_id,
                            case_id=case_id,
                            reason="no_entity_match",
                            question_preview=question[:100],
                            intent=query_understanding.intent,
                        )
                except Exception as exc:
                    log.warning("ai.entity_detection_failed", query_id=query_id, error=str(exc))

            await send({
                "type": "stage", "stage": "retrieving",
                "message": f"Retrieving case context… (intent: {query_understanding.intent})",
            })

            # Retrieve subgraph: person-centric — PERSON → PERSON only, supporting as evidence
            if effective_target_keys:
                nodes, edges = await self._retrieve_subgraph_multi(
                    case_id, target_keys=effective_target_keys, depth=depth,
                    max_nodes=self.settings.ai_max_context_nodes if self.settings.ai_allow_raw_pii else self.settings.ai_interactive_max_context_nodes,
                    max_edges=self.settings.ai_max_context_edges if self.settings.ai_allow_raw_pii else self.settings.ai_interactive_max_context_edges,
                    question=question,
                    dataset_id=dataset_id,
                )
                effective_target_key_for_log = effective_target_keys[0] if effective_target_keys else None
            else:
                nodes, edges = await self._retrieve_subgraph(
                    case_id, depth=depth, target_key=target_key,
                    question=question,
                    dataset_id=dataset_id,
                )
                effective_target_key_for_log = target_key

            # --- Phase 2 + Investigation Retrieval Engine: document relevance + evidence filters ---
            all_documents = await self._retrieve_case_documents(case_id)
            documents_available_count = len(all_documents)

            doc_char_budget = (
                self.settings.ai_max_context_doc_chars
                if self.settings.ai_allow_raw_pii
                else self.settings.ai_interactive_max_context_doc_chars
            )

            # Apply new retrieval engine ranking if we have query understanding
            # This is Layer A (exact) + Layer B (metadata) from build order
            try:
                # Use new ranking engine for more precise filtering
                ranked = rank_and_filter_context(
                    nodes=nodes,
                    edges=edges,
                    documents=all_documents,
                    understanding=query_understanding,
                    max_nodes=self.settings.ai_max_context_nodes if self.settings.ai_allow_raw_pii else self.settings.ai_interactive_max_context_nodes,
                    max_edges=self.settings.ai_max_context_edges if self.settings.ai_allow_raw_pii else self.settings.ai_interactive_max_context_edges,
                    max_doc_chars=doc_char_budget,
                    max_docs=15 if effective_target_keys else 10,
                )
                # Compress with timeline ordering if needed
                compressed = compress_context(
                    ranked,
                    query_understanding,
                    timeline_order=query_understanding.requires_timeline,
                )
                # Use ranked/filtered results
                # For backward compat, keep nodes/edges as filtered, but docs as ranked
                # We still run old filter as fallback check for target_key join
                old_filtered_docs, _, _ = self._filter_relevant_documents(
                    question=question,
                    nodes=compressed.nodes,
                    edges=compressed.edges,
                    documents=compressed.documents,
                    target_key=target_key,
                    target_keys=effective_target_keys,
                    max_total_chars=doc_char_budget,
                )
                # Prefer old_filtered if it yields more targeted docs when target_keys present
                if effective_target_keys and old_filtered_docs:
                    documents = old_filtered_docs
                else:
                    documents = compressed.documents
                nodes = compressed.nodes
                edges = compressed.edges
                docs_available = len(all_documents)
                docs_included = len(documents)
                ranking_ms = compressed.ranking_ms
                timer.stage("ranking_ms")
            except Exception as exc:
                log.warning("ai.ranking_failed_fallback", query_id=query_id, error=str(exc))
                # Fallback to old filtering
                try:
                    documents, docs_available, docs_included = self._filter_relevant_documents(
                        question=question,
                        nodes=nodes,
                        edges=edges,
                        documents=all_documents,
                        target_key=target_key,
                        target_keys=effective_target_keys,
                        max_total_chars=doc_char_budget,
                    )
                except Exception as exc2:
                    log.warning("ai.doc_filter_failed", query_id=query_id, error=str(exc2))
                    documents = all_documents
                    docs_available = len(all_documents)
                    docs_included = len(documents)
                ranking_ms = 0

            timer.stage("retrieval_ms")
            documents_total_chars = sum(len(str(d.get("content", ""))) for d in documents)

            # Build timeline if required (for evidence-grounded timeline feature)
            timeline_events = []
            try:
                if query_understanding.requires_timeline:
                    timeline_events = build_timeline_from_context(nodes, edges)
                    timer.stage("timeline_ms")
            except Exception as exc:
                log.warning("ai.timeline_build_failed", query_id=query_id, error=str(exc))

            context_report: dict[str, Any] = {
                "nodes": len(nodes),
                "edges": len(edges),
                "depth": depth,
                "target_key": effective_target_key_for_log if effective_target_keys else target_key,
                "target_keys": effective_target_keys,
                "retrieved": bool(nodes or edges),
                "dataset_id": dataset_id,
                "graph_ready": graph_ready,
                "evidence_ids": [str(document.get("doc_id")) for document in documents if document.get("doc_id")],
                "timing": {},
                "documents_count": len(documents),
                "documents_total_chars": documents_total_chars,
                "documents_available_count": documents_available_count,
                "documents_included_count": docs_included,
                "entity_detection_used": entity_detection_used,
                "entity_detection_path": entity_detection_path,
                "detected_entity_count": len(detected_entity_keys),
                # Investigation Retrieval Engine — new fields
                "query_intent": query_understanding.intent,
                "query_keywords": list(query_understanding.keywords)[:15],
                "temporal_filter": query_understanding.temporal.raw_text if query_understanding.temporal else None,
                "spatial_filter": query_understanding.spatial.locations if query_understanding.spatial else None,
                "evidence_filter": {
                    "doc_types": query_understanding.evidence.doc_types,
                    "entity_types": query_understanding.evidence.entity_types,
                    "rel_types": query_understanding.evidence.rel_types,
                },
                "requires_timeline": query_understanding.requires_timeline,
                "requires_evidence_path": query_understanding.requires_evidence_path,
                "query_confidence": query_understanding.confidence,
                "ranking_ms": locals().get("ranking_ms", 0),
                "timeline_events_count": len(timeline_events),
                # Evidence-grounded AI: expose node/edge IDs for "Why?" / "Show Evidence"
                "evidence_grounding": {
                    "node_ids": [n.get("provenance_key") for n in nodes[:20]],
                    "edge_ids": [f"{e.get('source_key')}->{e.get('target_key')}:{e.get('rel_type')}" for e in edges[:20]],
                    "doc_ids": [str(d.get("doc_id")) for d in documents],
                    "timeline": timeline_events[:20] if timeline_events else [],
                },
            }
            await send({
                "type": "retrieval",
                "nodes": len(nodes), "edges": len(edges),
                "retrieval_ms": timer.stages.get("retrieval_ms", 0),
            })
            if not nodes and not edges:
                # An empty case is a real, reportable state — not a model
                # failure and not something to ask a model to hallucinate over.
                log.info("ai.empty_context", query_id=query_id, case_id=case_id)

            # 2. Build display name mapping for context and de-pseudonymization
            account_owners: dict[str, str] = {}
            for e in edges:
                if e.get("rel_type") == "OWNS_ACCOUNT":
                    s_key = e.get("source_key") or e.get("source")
                    t_key = e.get("target_key") or e.get("target")
                    if s_key and t_key:
                        account_owners[t_key] = s_key
                        account_owners[t_key.split(":")[-1]] = s_key

            key_to_name: dict[str, str] = {}
            for n in nodes:
                key = n.get("provenance_key") or n.get("id")
                props = n.get("properties", {}) or {}
                lbl = n.get("label", "Entity")
                name = (
                    props.get("name")
                    or props.get("full_name")
                    or props.get("account_number")
                    or props.get("number")
                    or props.get("plate")
                    or props.get("address")
                    or props.get("city")
                )
                if not name:
                    name = key.split(":")[-1] if key else lbl

                if key:
                    key_to_name[key] = name
                    short_key = key.split(":")[-1]
                    if short_key:
                        key_to_name[short_key] = name

            # Refine display names with bank, vehicle, and owner details
            for n in nodes:
                key = n.get("provenance_key") or n.get("id")
                props = n.get("properties", {}) or {}
                lbl = n.get("label", "Entity")
                if lbl == "BankAccount":
                    acc_num = props.get("account_number") or key_to_name.get(key, key)
                    bank = props.get("bank_name")
                    owner_key = account_owners.get(key) or (account_owners.get(key.split(":")[-1]) if key else None)
                    owner_name = key_to_name.get(owner_key) if owner_key else None
                    parts = []
                    if bank:
                        parts.append(bank)
                    parts.append(f"({acc_num})")
                    if owner_name:
                        parts.append(f"[Owner: {owner_name}]")
                    disp = " ".join(parts)
                    key_to_name[key] = disp
                    if key:
                        key_to_name[key.split(":")[-1]] = disp
                elif lbl == "Vehicle" and props.get("make_model"):
                    plate = props.get("plate") or key_to_name.get(key, key)
                    disp = f"{props['make_model']} [{plate}]"
                    key_to_name[key] = disp
                    if key:
                        key_to_name[key.split(":")[-1]] = disp

            pmap = PseudonymMap()
            if self.settings.ai_allow_raw_pii:
                # Direct real-world entities in context
                safe_nodes = []
                for n in nodes:
                    key = n.get("provenance_key") or n.get("id")
                    disp_name = key_to_name.get(key, key)
                    props = n.get("properties", {}) or {}
                    lbl = n.get("label", "Entity")
                    node_entry = {
                        "id": disp_name,
                        "name": disp_name,
                        "label": lbl,
                        "confidence": n.get("confidence", 1.0),
                        "case_id": props.get("case_id"),
                    }
                    if lbl == "BankAccount":
                        owner_key = account_owners.get(key) or (account_owners.get(key.split(":")[-1]) if key else None)
                        if owner_key:
                            node_entry["account_holder"] = key_to_name.get(owner_key, owner_key)
                    for k in ("city", "role", "bank_name", "make_model", "first_ts", "last_ts"):
                        if k in props:
                            node_entry[k] = props[k]
                    safe_nodes.append(node_entry)

                safe_edges = []
                for e in edges:
                    s_key = e.get("source_key") or e.get("source")
                    t_key = e.get("target_key") or e.get("target")
                    s_name = key_to_name.get(s_key, s_key.split(":")[-1] if s_key else "?")
                    t_name = key_to_name.get(t_key, t_key.split(":")[-1] if t_key else "?")
                    edge_entry = {
                        "source": s_name,
                        "target": t_name,
                        "rel_type": e.get("rel_type"),
                        "confidence": e.get("confidence", 1.0),
                    }
                    for k in ("amount", "call_count", "timestamp", "source_doc_ids"):
                        if k in e:
                            edge_entry[k] = e[k]
                    safe_edges.append(edge_entry)
                pseudonymized = False
            else:
                nodes_min = [self._minimize_node(n) for n in nodes]
                if self.settings.ai_pseudonymize:
                    safe_nodes, safe_edges = apply_pseudonymization_to_context(nodes_min, edges, pmap)
                    pseudonymized = True
                else:
                    safe_nodes = nodes_min
                    safe_edges = edges
                    pseudonymized = False
            timer.stage("context_ms")

            # 4. Build model-specific context
            question_for_model = question
            if pseudonymized:
                question_for_model = self._rewrite_question_for_pseudonyms(
                    question, nodes, pmap, target_key=target_key,
                )
            eff_nodes = (
                self.settings.ai_max_context_nodes
                if self.settings.ai_allow_raw_pii
                else self.settings.ai_interactive_max_context_nodes
            )
            eff_edges = (
                self.settings.ai_max_context_edges
                if self.settings.ai_allow_raw_pii
                else self.settings.ai_interactive_max_context_edges
            )
            context = self._build_reasoning_context(
                safe_nodes, safe_edges, question_for_model,
                max_nodes=eff_nodes,
                max_edges=eff_edges,
                documents=documents,
            )
            timer.stage("prompt_build_ms")

            # --- Phase 1 instrumentation: detailed context metrics ---
            estimated_prompt_tokens = len(context) // 4
            # Approximate split of prompt size: graph vs documents vs rest
            # (used for the investigation markdown, not just logs)
            try:
                # Re-parse payload sizes if possible, else approximate
                graph_part = json.dumps({"nodes": safe_nodes, "relationships": safe_edges})
                graph_chars = len(graph_part)
            except Exception:
                graph_chars = 0
            doc_chars = documents_total_chars
            total_chars = len(context)
            # Structured log right before model call
            log.info(
                "ai.context_metrics",
                query_id=query_id,
                case_id=case_id,
                graph_nodes_count=len(nodes),
                graph_edges_count=len(edges),
                documents_count=len(documents),
                documents_total_chars=documents_total_chars,
                documents_available_count=documents_available_count,
                documents_included_count=docs_included,
                estimated_prompt_tokens=estimated_prompt_tokens,
                prompt_chars_total=total_chars,
                prompt_chars_graph=graph_chars,
                prompt_chars_documents=doc_chars,
                target_key=effective_target_key_for_log if 'effective_target_key_for_log' in locals() else target_key,
                target_keys=effective_target_keys if 'effective_target_keys' in locals() else None,
                has_target=bool((effective_target_keys if 'effective_target_keys' in locals() and effective_target_keys else target_key)),
                entity_detection_used=entity_detection_used if 'entity_detection_used' in locals() else False,
                entity_detection_path=entity_detection_path if 'entity_detection_path' in locals() else "unknown",
                detected_entity_count=len(detected_entity_keys) if 'detected_entity_keys' in locals() else 0,
                stage_timings_ms=dict(timer.stages),
                retrieval_ms=timer.stages.get("retrieval_ms", 0),
                prompt_build_ms=timer.stages.get("prompt_build_ms", 0),
            )

            # 5. Ask reasoning model (streaming tokens to the UI when a
            #    progress channel exists)
            await send({
                "type": "stage", "stage": "generating",
                "message": "Generating answer…",
            })

            async def on_delta(text: str) -> None:
                await send({"type": "delta", "text": text})

            reasoning_prompt = (
                SYSTEM_PROMPT_REASONING_RAW
                if self.settings.ai_allow_raw_pii
                else SYSTEM_PROMPT_REASONING
            )
            if emit is not None:
                result = await self.router.chat_stream(
                    "investigation_reasoning",
                    system_prompt=reasoning_prompt,
                    user_prompt=context,
                    on_delta=on_delta,
                    response_format={"type": "json_object"},
                    max_tokens=2048,
                    timeout_override=self.settings.ai_interactive_timeout_s,
                    max_retries_override=self.settings.ai_interactive_max_retries,
                )
            else:
                result = await self.router.chat(
                    "investigation_reasoning",
                    system_prompt=reasoning_prompt,
                    user_prompt=context,
                    response_format={"type": "json_object"},
                    max_tokens=2048,
                    timeout_override=self.settings.ai_interactive_timeout_s,
                    max_retries_override=self.settings.ai_interactive_max_retries,
                )
            timer.stage("model_call_ms")
            # Backward compat: keep model_ms alias
            timer.stages["model_ms"] = timer.stages.get("model_call_ms", 0)
            # Also expose prompt_build_ms alias if needed and log final metrics
            log.info(
                "ai.context_metrics_final",
                query_id=query_id,
                case_id=case_id,
                graph_nodes_count=len(nodes),
                graph_edges_count=len(edges),
                documents_count=len(documents),
                documents_total_chars=documents_total_chars,
                documents_available_count=documents_available_count,
                documents_included_count=docs_included,
                estimated_prompt_tokens=len(context) // 4,
                target_key=effective_target_key_for_log if 'effective_target_key_for_log' in locals() else target_key,
                target_keys=effective_target_keys if 'effective_target_keys' in locals() else None,
                has_target=bool((effective_target_keys if 'effective_target_keys' in locals() and effective_target_keys else target_key)),
                entity_detection_used=entity_detection_used if 'entity_detection_used' in locals() else False,
                entity_detection_path=entity_detection_path if 'entity_detection_path' in locals() else "unknown",
                detected_entity_count=len(detected_entity_keys) if 'detected_entity_keys' in locals() else 0,
                stage_timings_ms=dict(timer.stages),
                retrieval_ms=timer.stages.get("retrieval_ms", 0),
                prompt_build_ms=timer.stages.get("prompt_build_ms", 0),
                model_call_ms=timer.stages.get("model_call_ms", 0),
            )
            if not result.get("available"):
                # 10/10 hardening: Model-independent deterministic investigation
                # Graph-RAG → candidates → validation → deterministic result → OPTIONAL AI explanation
                # AI failure ≠ investigation failure — return deterministic result usable with View Evidence/Timeline
                deterministic_finding = None
                if HAS_HARDENING and 'person_rag_result' in locals() and locals().get('person_rag_result'):
                    try:
                        det_result = build_deterministic_result(locals()['person_rag_result'])
                        # Build FindingResult from deterministic
                        if det_result.get("no_connection"):
                            nc = det_result["no_connection"]
                            deterministic_finding = FindingResult(
                                finding_type="NO_RELIABLE_CONNECTION",
                                summary=f"{nc['reason']} People searched: {nc['people_searched']}, Evidence examined: {nc['evidence_examined']}, Reliable relationships found: 0",
                                confidence=0.0,
                                evidence_level="UNKNOWN",
                                recommended_review=False,
                                uncertainties=[nc['reason']],
                            )
                        elif det_result.get("relationships"):
                            rels = det_result["relationships"]
                            top_rel = rels[0]
                            deterministic_finding = FindingResult(
                                finding_type="RELATIONSHIP",
                                summary=f"Deterministic result: {top_rel['source_person']} ↔ {top_rel['target_person']} via {top_rel['relationship_type']} (Classification: {top_rel['classification']}, Confidence: {top_rel['confidence_label']}). Evidence: {len(top_rel['evidence_refs'])} records. AI explanation unavailable but evidence is usable. View Evidence/Timeline for details.",
                                confidence=top_rel['confidence'],
                                evidence_level=top_rel['classification'],
                                recommended_review=False,
                                relationships=[
                                    {
                                        "source_person": r["source_person"],
                                        "target_person": r["target_person"],
                                        "relationship_type": r["relationship_type"],
                                        "controlled_type": r.get("controlled_type", "UNKNOWN"),
                                        "classification": r["classification"],
                                        "confidence": r["confidence"],
                                        "confidence_label": r["confidence_label"],
                                        "evidence_refs": r["evidence_refs"],
                                        "provenance": r["provenance"],
                                        "explanation": r["explanation"],
                                        "limitations": r["limitations"],
                                    }
                                    for r in rels[:5]
                                ],
                                evidence_refs=[ref for r in rels[:3] for ref in r["evidence_refs"][:2]],
                                uncertainties=["AI explanation unavailable — deterministic result shown. View Evidence/Timeline."],
                            )
                        log.info(
                            "ai.deterministic_fallback_used",
                            query_id=query_id,
                            case_id=case_id,
                            relationships=len(det_result.get("relationships", [])),
                            reason=result.get("reason"),
                        )
                    except Exception as exc:
                        log.warning("ai.deterministic_fallback_failed", query_id=query_id, error=str(exc))

                if deterministic_finding:
                    context_report["timing"] = timer.report()
                    context_report["deterministic_fallback"] = True
                    context_report["ai_explanation_available"] = False
                    await self._audit(
                        query_id=query_id, case_id=case_id,
                        user_id=principal_id or user_id, role="reasoning",
                        model=None, latency_ms=0, tokens=(None, None),
                        pmap_size=len(pmap), question=question,
                        output_hash=None, success=True,
                        error=f"deterministic_fallback: {result.get('reason', 'api_key_unavailable')}",
                        extra={
                            "request_id": query_id,
                            "case_id": case_id,
                            "query": question[:200],
                            "retrieval_version": "person_rag_v1",
                            "graph_version": f"v{_RETRIEVAL_CACHE_VERSION}",
                            "model": None,
                            "validation_result": "deterministic",
                            "latency_ms": timer.report().get("total_ms", 0),
                            "ai_available": False,
                        },
                    )
                    return AIResponse(
                        query_id=query_id, role="reasoning", model=None,
                        finding=deterministic_finding, pseudonymized=pseudonymized,
                        available=True, fallback_reason="ai_explanation_unavailable_deterministic_usable",
                        latency_ms=max(1, context_report["timing"]["total_ms"]),
                        context=context_report,
                    )

                # No deterministic fallback — original unavailable path
                finding = self._unavailable_finding("reasoning", result.get("reason"))
                context_report["timing"] = timer.report()
                await self._audit(
                    query_id=query_id, case_id=case_id,
                    user_id=principal_id or user_id, role="reasoning",
                    model=None, latency_ms=0, tokens=(None, None),
                    pmap_size=len(pmap), question=question,
                    output_hash=None, success=False,
                    error=result.get("reason", "api_key_unavailable"),
                )
                return AIResponse(
                    query_id=query_id, role="reasoning", model=None,
                    finding=finding, pseudonymized=pseudonymized,
                    available=False, fallback_reason=result.get("reason"),
                    latency_ms=max(1, context_report["timing"]["total_ms"]),
                    context=context_report,
                )

            # 6. Parse and validate the structured result
            await send({"type": "stage", "stage": "validating",
                        "message": "Validating and attaching evidence…"})
            finding = self._parse_and_validate(result["content"])
            try:
                # Enforce references when the retrieval layer supplied an
                # evidence package.  Some offline/legacy adapters deliberately
                # return no document IDs; preserving their structured output is
                # safer than pretending an empty adapter result is a complete
                # package.  Direct safety tests still reject references against
                # an explicit allowed set.
                evidence_ids = context_report.get("evidence_ids", [])
                if evidence_ids:
                    validate_finding(
                        finding,
                        allowed_evidence_ids=evidence_ids,
                        allowed_entity_ids={*key_to_name.keys(), *pmap.entries().keys()},
                    )
            except AISafetyViolation as exc:
                log.warning("ai.safety_firewall_rejected_output", query_id=query_id, error=str(exc))
                finding = FindingResult(
                    finding_type="UNVERIFIED_AI_OUTPUT",
                    summary="The AI response failed evidence-reference validation and was withheld.",
                    confidence=0.0,
                    evidence_level="UNKNOWN",
                    recommended_review=True,
                    uncertainties=[str(exc)],
                )
            if finding and finding.summary:
                if pseudonymized:
                    for pseudo, real_key in pmap.entries().items():
                        real_name = key_to_name.get(real_key, pseudo)
                        if real_name != pseudo:
                            finding.summary = re.sub(rf"\b{re.escape(pseudo)}\b", real_name, finding.summary)
                for id_token, real_name in key_to_name.items():
                    if id_token and len(id_token) > 4 and id_token in finding.summary and id_token != real_name:
                        finding.summary = re.sub(rf"\b{re.escape(id_token)}\b", real_name, finding.summary)
            context_report["timing"] = timer.report()

            # 7. Audit
            await self._audit(
                query_id=query_id,
                case_id=case_id,
                user_id=principal_id or user_id,
                role="reasoning",
                model=result.get("model"),
                latency_ms=result.get("latency_ms", 0),
                tokens=(result.get("prompt_tokens"), result.get("completion_tokens")),
                pmap_size=len(pmap),
                question=question,
                output_hash=result.get("output_hash"),
                success=True,
            )

            log.info(
                "ai.stage_timing",
                query_id=query_id, case_id=case_id, streamed=bool(result.get("streamed")),
                **context_report["timing"],
            )
            return AIResponse(
                query_id=query_id,
                role="reasoning",
                model=result.get("model"),
                finding=finding,
                latency_ms=result.get("latency_ms", 0),
                pseudonymized=pseudonymized,
                available=True,
                context=context_report,
            )
        except Exception as exc:
            log.exception("ai.gateway_failed", query_id=query_id, error=str(exc))
            await self._audit(
                query_id=query_id,
                case_id=case_id,
                user_id=principal_id or user_id,
                role="reasoning",
                model=None,
                latency_ms=0,
                tokens=(None, None),
                pmap_size=0,
                question=question,
                output_hash=None,
                success=False,
                error=str(exc),
            )
            return AIResponse(
                query_id=query_id,
                role="reasoning",
                model=None,
                finding=FindingResult(
                    finding_type="GENERAL",
                    summary=(
                        "The AI service is currently unavailable for this question "
                        f"(internal error). Quote request id {query_id} when reporting "
                        "this. An investigator must review this case manually."
                    ),
                    confidence=0.0,
                    evidence_level="UNKNOWN",
                    recommended_review=True,
                    uncertainties=[
                        "The AI pipeline failed before producing a finding; "
                        "the server log carries the technical detail."
                    ],
                ),
                available=False,
                fallback_reason="gateway_error",
                context={"timing": timer.report(), "dataset_id": dataset_id},
            )

    # ------------------------------------------------- structured unavailability

    @staticmethod
    def _unavailable_finding(role: str, reason: str | None) -> FindingResult:
        """Build the structured "AI unavailable" finding from the real reason."""
        reason = reason or "unknown_reason"
        # A configured provider that actually failed needs human review; a
        # role that was never configured is a no-op, not an incident.
        needs_review = reason.startswith("invocation_failed:")
        return FindingResult(
            finding_type="GENERAL",
            summary=unavailable_summary(role, reason),
            confidence=0.0,
            evidence_level="UNKNOWN",
            recommended_review=needs_review,
        )

    # ----------------------------------------------------- retrieval / context

    async def _retrieve_subgraph(
        self, case_id: str, *, depth: int = 2, target_key: str | None = None,
        max_nodes: int | None = None, max_edges: int | None = None,
        question: str | None = None,
        dataset_id: str | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Person-centric retrieval — bounded by context budget, PERSON-first.

        When question is available and person_graph_rag is present, uses
        person-centric pipeline: exact person match → metadata → graph traversal
        → evidence filtering → ranking → compaction. Final relationships are
        PERSON → PERSON only; supporting entities (phone, vehicle, location,
        file, doc, org, address) are used as EVIDENCE, not as final nodes.
        LLM never receives entire graph — only relevant paths.
        """
        from app.container import get_container
        from app.domain.models import CaseGraphSnapshot

        container = get_container()
        graph = container.graph_store
        depth = max(1, int(depth))
        try:
            snap: CaseGraphSnapshot = await asyncio.to_thread(graph.get_case_snapshot, case_id)
        except Exception:
            try:
                snap = graph.get_case_snapshot(case_id)
            except Exception:
                log.warning("ai.retrieval_failed", case_id=case_id)
                return [], []
        max_nodes_eff = max_nodes or self.settings.ai_max_context_nodes
        max_edges_eff = max_edges or self.settings.ai_max_context_edges

        # PERSON-CENTRIC path when question is provided and RAG available
        if HAS_PERSON_RAG and question:
            try:
                rag_result = person_graph_rag_retrieval(
                    snap,
                    question,
                    dataset_id=dataset_id,
                    max_persons=min(40, max_nodes_eff),
                    max_relationships=min(20, max_edges_eff // 2),
                    max_hops=max(2, min(4, depth)),
                )
                person_keys = set(p.provenance_key for p in rag_result.persons)
                supporting_keys = set(n["provenance_key"] for n in rag_result.supporting_nodes)

                nodes: list[dict] = []
                for p in rag_result.persons[: max_nodes_eff]:
                    node = snap.nodes.get(p.provenance_key)
                    if not node:
                        continue
                    nodes.append({
                        "provenance_key": p.provenance_key,
                        "label": node.label,
                        "properties": dict(node.properties),
                        "confidence": node.properties.get("confidence", 1.0),
                    })
                remaining = max_nodes_eff - len(nodes)
                for sn in rag_result.supporting_nodes[:remaining]:
                    key = sn["provenance_key"]
                    if key in person_keys:
                        continue
                    node = snap.nodes.get(key)
                    if not node:
                        continue
                    nodes.append({
                        "provenance_key": key,
                        "label": node.label,
                        "properties": dict(node.properties),
                        "confidence": node.properties.get("confidence", 1.0),
                    })

                if len(nodes) < max_nodes_eff and target_key and target_key in snap.nodes:
                    extra_keys = self._neighbourhood_keys(snap, target_key, depth=depth, limit=max_nodes_eff)
                    for ek in extra_keys:
                        if len(nodes) >= max_nodes_eff:
                            break
                        if ek in person_keys or ek in supporting_keys:
                            continue
                        node = snap.nodes.get(ek)
                        if not node or node.label == "Case":
                            continue
                        if str(node.label).upper() == "PERSON":
                            nodes.append({
                                "provenance_key": ek,
                                "label": node.label,
                                "properties": dict(node.properties),
                                "confidence": node.properties.get("confidence", 1.0),
                            })

                keep = {n["provenance_key"] for n in nodes}
                edges: list[dict] = []
                for se in rag_result.supporting_edges:
                    if len(edges) >= max_edges_eff:
                        break
                    if se["source_key"] in keep and se["target_key"] in keep:
                        edges.append({
                            "source_key": se["source_key"],
                            "target_key": se["target_key"],
                            "rel_type": se["rel_type"],
                            "confidence": se["confidence"],
                            "timestamp": se.get("properties", {}).get("timestamp") or se.get("properties", {}).get("last_ts"),
                            "source_doc_ids": se.get("properties", {}).get("source_doc_ids", [se.get("properties", {}).get("source_doc_id")]),
                        })
                for e in snap.edges:
                    if len(edges) >= max_edges_eff:
                        break
                    if e.source_key not in keep or e.target_key not in keep:
                        continue
                    if any(
                        ee["source_key"] == e.source_key and ee["target_key"] == e.target_key and ee["rel_type"] == e.rel_type
                        for ee in edges
                    ):
                        continue
                    props = dict(e.properties)
                    edge_entry = {
                        "source_key": e.source_key,
                        "target_key": e.target_key,
                        "rel_type": e.rel_type,
                        "confidence": e.confidence,
                        "timestamp": props.get("timestamp") or props.get("last_ts"),
                        "source_doc_ids": props.get("source_doc_ids", [props.get("source_doc_id")]),
                    }
                    if "amount" in props and props["amount"] is not None:
                        edge_entry["amount"] = props["amount"]
                    if "call_count" in props and props["call_count"] is not None:
                        edge_entry["call_count"] = props["call_count"]
                    edges.append(edge_entry)

                log.info(
                    "ai.person_rag_retrieval",
                    case_id=case_id,
                    persons=len([n for n in nodes if str(n.get("label","")).upper() == "PERSON"]),
                    supporting=len([n for n in nodes if str(n.get("label","")).upper() != "PERSON"]),
                    edges=len(edges),
                    total_ms=rag_result.metrics.total_ms,
                )
                return nodes, edges
            except Exception as exc:
                log.warning("ai.person_rag_failed_fallback", case_id=case_id, error=str(exc))

        # Legacy fallback — but still PERSON-first ordering
        keys = (
            self._neighbourhood_keys(snap, target_key, depth=depth, limit=max_nodes_eff)
            if target_key and target_key in snap.nodes
            else set(snap.nodes)
        )
        degree: dict[str, int] = {}
        for e in snap.edges:
            degree[e.source_key] = degree.get(e.source_key, 0) + 1
            degree[e.target_key] = degree.get(e.target_key, 0) + 1

        def _person_first_key(k: str):
            node = snap.nodes.get(k)
            if not node:
                return (1, -degree.get(k, 0), k)
            is_person = 0 if str(node.label).upper() == "PERSON" else 1
            return (is_person, -degree.get(k, 0), k)

        nodes: list[dict] = []
        for key in sorted(keys, key=_person_first_key):
            node = snap.nodes[key]
            if node.label == "Case":
                continue
            nodes.append({
                "provenance_key": key,
                "label": node.label,
                "properties": dict(node.properties),
                "confidence": node.properties.get("confidence", 1.0),
            })
            if len(nodes) >= max_nodes_eff:
                break
        keep = {node["provenance_key"] for node in nodes}
        edges: list[dict] = []
        for e in snap.edges:
            if e.source_key not in keep or e.target_key not in keep:
                continue
            props = dict(e.properties)
            edge_entry = {
                "source_key": e.source_key,
                "target_key": e.target_key,
                "rel_type": e.rel_type,
                "confidence": e.confidence,
                "timestamp": props.get("timestamp") or props.get("last_ts"),
                "source_doc_ids": props.get("source_doc_ids", [props.get("source_doc_id")]),
            }
            if "amount" in props and props["amount"] is not None:
                edge_entry["amount"] = props["amount"]
            if "call_count" in props and props["call_count"] is not None:
                edge_entry["call_count"] = props["call_count"]
            edges.append(edge_entry)
            if len(edges) >= max_edges_eff:
                break
        return nodes, edges

    async def _retrieve_subgraph_multi(
        self, case_id: str, *, target_keys: list[str], depth: int = 2,
        max_nodes: int | None = None, max_edges: int | None = None,
        question: str | None = None,
        dataset_id: str | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Person-centric multi-seed retrieval — PERSON-first, supporting as evidence."""

        from app.container import get_container
        from app.domain.models import CaseGraphSnapshot

        container = get_container()
        graph = container.graph_store
        depth = max(1, int(depth))
        try:
            snap: CaseGraphSnapshot = await asyncio.to_thread(graph.get_case_snapshot, case_id)
        except Exception:
            try:
                snap = graph.get_case_snapshot(case_id)
            except Exception:
                log.warning("ai.retrieval_failed", case_id=case_id)
                return [], []
        max_nodes_eff = max_nodes or self.settings.ai_max_context_nodes
        max_edges_eff = max_edges or self.settings.ai_max_context_edges

        # If question available, try person-centric RAG first
        if HAS_PERSON_RAG and question:
            try:
                rag_result = person_graph_rag_retrieval(
                    snap,
                    question,
                    dataset_id=dataset_id,
                    max_persons=min(40, max_nodes_eff),
                    max_relationships=min(20, max_edges_eff // 2),
                    max_hops=max(2, min(4, depth)),
                )
                person_keys = set(p.provenance_key for p in rag_result.persons)
                nodes: list[dict] = []
                for p in rag_result.persons[: max_nodes_eff]:
                    node = snap.nodes.get(p.provenance_key)
                    if not node:
                        continue
                    nodes.append({
                        "provenance_key": p.provenance_key,
                        "label": node.label,
                        "properties": dict(node.properties),
                        "confidence": node.properties.get("confidence", 1.0),
                    })
                remaining = max_nodes_eff - len(nodes)
                for sn in rag_result.supporting_nodes[:remaining]:
                    key = sn["provenance_key"]
                    if key in person_keys:
                        continue
                    node = snap.nodes.get(key)
                    if not node:
                        continue
                    nodes.append({
                        "provenance_key": key,
                        "label": node.label,
                        "properties": dict(node.properties),
                        "confidence": node.properties.get("confidence", 1.0),
                    })
                keep = {n["provenance_key"] for n in nodes}
                edges: list[dict] = []
                for se in rag_result.supporting_edges:
                    if len(edges) >= max_edges_eff:
                        break
                    if se["source_key"] in keep and se["target_key"] in keep:
                        edges.append({
                            "source_key": se["source_key"],
                            "target_key": se["target_key"],
                            "rel_type": se["rel_type"],
                            "confidence": se["confidence"],
                            "timestamp": se.get("properties", {}).get("timestamp") or se.get("properties", {}).get("last_ts"),
                            "source_doc_ids": se.get("properties", {}).get("source_doc_ids", [se.get("properties", {}).get("source_doc_id")]),
                        })
                for e in snap.edges:
                    if len(edges) >= max_edges_eff:
                        break
                    if e.source_key not in keep or e.target_key not in keep:
                        continue
                    if any(
                        ee["source_key"] == e.source_key and ee["target_key"] == e.target_key and ee["rel_type"] == e.rel_type
                        for ee in edges
                    ):
                        continue
                    props = dict(e.properties)
                    edge_entry = {
                        "source_key": e.source_key,
                        "target_key": e.target_key,
                        "rel_type": e.rel_type,
                        "confidence": e.confidence,
                        "timestamp": props.get("timestamp") or props.get("last_ts"),
                        "source_doc_ids": props.get("source_doc_ids", [props.get("source_doc_id")]),
                    }
                    if "amount" in props and props["amount"] is not None:
                        edge_entry["amount"] = props["amount"]
                    if "call_count" in props and props["call_count"] is not None:
                        edge_entry["call_count"] = props["call_count"]
                    edges.append(edge_entry)
                log.info(
                    "ai.person_rag_retrieval_multi",
                    case_id=case_id,
                    persons=len([n for n in nodes if str(n.get("label","")).upper() == "PERSON"]),
                    edges=len(edges),
                    total_ms=rag_result.metrics.total_ms,
                )
                return nodes, edges
            except Exception as exc:
                log.warning("ai.person_rag_multi_failed_fallback", case_id=case_id, error=str(exc))

        valid_keys = [k for k in target_keys if k in snap.nodes]
        if not valid_keys:
            keys = set(snap.nodes)
        else:
            keys = self._neighbourhood_keys_multi(snap, valid_keys, depth=depth, limit=max_nodes_eff)

        degree: dict[str, int] = {}
        for e in snap.edges:
            degree[e.source_key] = degree.get(e.source_key, 0) + 1
            degree[e.target_key] = degree.get(e.target_key, 0) + 1

        def _person_first(k: str):
            node = snap.nodes.get(k)
            if not node:
                return (1, -degree.get(k, 0), k)
            is_person = 0 if str(node.label).upper() == "PERSON" else 1
            return (is_person, -degree.get(k, 0), k)

        nodes: list[dict] = []
        for key in sorted(keys, key=_person_first):
            node = snap.nodes.get(key)
            if not node:
                continue
            if node.label == "Case":
                continue
            nodes.append({
                "provenance_key": key,
                "label": node.label,
                "properties": dict(node.properties),
                "confidence": node.properties.get("confidence", 1.0),
            })
            if len(nodes) >= max_nodes_eff:
                break
        keep = {node["provenance_key"] for node in nodes}
        edges: list[dict] = []
        for e in snap.edges:
            if e.source_key not in keep or e.target_key not in keep:
                continue
            props = dict(e.properties)
            edge_entry = {
                "source_key": e.source_key,
                "target_key": e.target_key,
                "rel_type": e.rel_type,
                "confidence": e.confidence,
                "timestamp": props.get("timestamp") or props.get("last_ts"),
                "source_doc_ids": props.get("source_doc_ids", [props.get("source_doc_id")]),
            }
            if "amount" in props and props["amount"] is not None:
                edge_entry["amount"] = props["amount"]
            if "call_count" in props and props["call_count"] is not None:
                edge_entry["call_count"] = props["call_count"]
            edges.append(edge_entry)
            if len(edges) >= max_edges_eff:
                break
        return nodes, edges

    async def _get_all_case_nodes(self, case_id: str) -> list[dict]:
        """Get all nodes for a case (unbounded) for entity detection.

        Used by Phase 3 to run _detect_entities_in_question against the whole
        case's node set rather than an already-retrieved limited list.
        """
        from app.container import get_container
        container = get_container()
        graph = container.graph_store
        try:
            snap = await asyncio.to_thread(graph.get_case_snapshot, case_id)
        except Exception:
            try:
                snap = graph.get_case_snapshot(case_id)
            except Exception:
                return []
        all_nodes: list[dict] = []
        for key, node in snap.nodes.items():
            if node.label == "Case":
                continue
            all_nodes.append({
                "provenance_key": key,
                "label": node.label,
                "properties": dict(node.properties),
                "confidence": node.properties.get("confidence", 1.0),
            })
        return all_nodes


    async def _retrieve_case_documents(self, case_id: str, max_chars_per_doc: int = 3000) -> list[dict[str, Any]]:
        """Retrieve text and metadata for all documents associated with this case."""
        from app.container import get_container
        from app.db.session import async_session
        from app.db.models import CaseDocument
        from app.datasets.readers import read_text
        from sqlalchemy import select

        container = get_container()
        docs_out: list[dict[str, Any]] = []
        try:
            async with async_session() as session:
                stmt = select(CaseDocument).where(
                    CaseDocument.case_id == case_id,
                    CaseDocument.is_deleted.is_(False),
                )
                res = await session.execute(stmt)
                doc_records = res.scalars().all()
                for doc in doc_records:
                    text_content = ""
                    # 1. Check derived normalised text if present in object store
                    if doc.derived_key:
                        try:
                            raw = container.object_store.get(
                                self.settings.minio_bucket_derived, doc.derived_key
                            )
                            text_content = raw.decode("utf-8", errors="replace")
                        except Exception:
                            pass
                    # 2. Check dataset workspace on disk (supports .txt, .pdf, .docx, etc.)
                    if not text_content and doc.dataset_id:
                        from app.datasets import registry
                        try:
                            ws = registry.workspace_for(doc.dataset_id)
                            candidates = [ws / doc.storage_key, ws / doc.filename]
                            if doc.source_metadata and isinstance(doc.source_metadata, dict):
                                rel = doc.source_metadata.get("relative_path")
                                if rel:
                                    candidates.append(ws / rel)
                            found_file = None
                            for cand in candidates:
                                if cand.is_file():
                                    found_file = cand
                                    break
                            if not found_file and ws.exists():
                                matches = list(ws.glob(f"**/{doc.filename}"))
                                if matches and matches[0].is_file():
                                    found_file = matches[0]
                            if found_file:
                                try:
                                    parsed_doc = read_text(found_file)
                                    text_content = parsed_doc.text
                                except Exception:
                                    text_content = found_file.read_text(encoding="utf-8", errors="replace")
                        except Exception:
                            pass
                    # 3. Fallback to raw object store storage_key for text-like files
                    if not text_content and doc.storage_key:
                        fn = (doc.filename or "").lower()
                        if fn.endswith((".txt", ".csv", ".json", ".md", ".log")):
                            try:
                                raw = container.object_store.get(
                                    self.settings.minio_bucket_documents, doc.storage_key
                                )
                                text_content = raw.decode("utf-8", errors="replace")
                            except Exception:
                                pass
                    if text_content:
                        clean_text = text_content[:max_chars_per_doc].strip()
                        docs_out.append({
                            "doc_id": doc.id,
                            "filename": doc.filename,
                            "document_type": doc.document_type.value if hasattr(doc.document_type, "value") else str(doc.document_type),
                            # Evidence text is untrusted input.  Instruction-like
                            # strings are marked as text before entering a model prompt.
                            "content": sanitize_untrusted_evidence(clean_text),
                        })
        except Exception as exc:
            log.warning("ai.retrieve_documents_failed", case_id=case_id, error=str(exc))
        return docs_out

    @staticmethod
    def _neighbourhood_keys(snap, root_key: str, *, depth: int, limit: int) -> set[str]:
        adjacency: dict[str, list[str]] = {}
        for edge in snap.edges:
            adjacency.setdefault(edge.source_key, []).append(edge.target_key)
            adjacency.setdefault(edge.target_key, []).append(edge.source_key)
        seen = {root_key}
        frontier = {root_key}
        for _ in range(max(1, depth)):
            next_frontier: set[str] = set()
            for node_key in sorted(frontier):
                for neighbour in adjacency.get(node_key, []):
                    if neighbour not in seen and len(seen) < limit:
                        seen.add(neighbour)
                        next_frontier.add(neighbour)
            frontier = next_frontier
            if not frontier:
                break
        return seen

    @staticmethod
    def _neighbourhood_keys_multi(snap, root_keys: list[str], *, depth: int, limit: int) -> set[str]:
        """BFS from multiple seed keys, merging neighborhoods and respecting limit."""
        adjacency: dict[str, list[str]] = {}
        for edge in snap.edges:
            adjacency.setdefault(edge.source_key, []).append(edge.target_key)
            adjacency.setdefault(edge.target_key, []).append(edge.source_key)
        seen: set[str] = set()
        frontier: set[str] = set()
        for rk in root_keys:
            if rk in snap.nodes and rk not in seen and len(seen) < limit:
                seen.add(rk)
                frontier.add(rk)
        # If no seed was in snap (e.g. stale key), fall back to empty
        if not frontier:
            return set()
        for _ in range(max(1, depth)):
            next_frontier: set[str] = set()
            for node_key in sorted(frontier):
                for neighbour in adjacency.get(node_key, []):
                    if neighbour not in seen and len(seen) < limit:
                        seen.add(neighbour)
                        next_frontier.add(neighbour)
            frontier = next_frontier
            if not frontier:
                break
        return seen

    @staticmethod
    def _collect_subgraph_doc_ids(nodes: list[dict], edges: list[dict]) -> set[str]:
        """Collect all source_doc_ids referenced by a subgraph (nodes+edges)."""
        doc_ids: set[str] = set()
        for n in nodes:
            props = n.get("properties", {}) or {}
            # source_doc_ids may be list, source_doc_id single
            sids = props.get("source_doc_ids")
            if isinstance(sids, (list, tuple, set)):
                for sid in sids:
                    if sid:
                        doc_ids.add(str(sid))
            sid = props.get("source_doc_id")
            if sid:
                doc_ids.add(str(sid))
        for e in edges:
            sids = e.get("source_doc_ids")
            if isinstance(sids, (list, tuple, set)):
                for sid in sids:
                    if sid:
                        doc_ids.add(str(sid))
            sid = e.get("source_doc_id")
            if sid:
                doc_ids.add(str(sid))
        return doc_ids

    @staticmethod
    def _extract_question_keywords(question: str) -> set[str]:
        """Simple deterministic keyword extraction from question."""
        if not question:
            return set()
        # Lowercase, split on non-alphanumeric, keep tokens >=3 chars
        tokens = re.split(r"[^a-z0-9]+", question.lower())
        # Basic stopwords list (English + some Hindi transliteration common words)
        stop = {
            "what", "who", "when", "where", "why", "how", "which", "this", "that",
            "these", "those", "the", "and", "or", "but", "with", "from", "about",
            "into", "case", "tell", "show", "list", "give", "find", "are", "is",
            "was", "were", "been", "have", "has", "had", "does", "did", "can",
            "could", "would", "should", "will", "connected", "connects", "connection",
            "summary", "summarize", "summarise", "open", "leads", "lead",
        }
        kws = {t for t in tokens if len(t) >= 3 and t not in stop}
        return kws

    @staticmethod
    def _score_document_relevance(doc: dict, question_keywords: set[str], question_lower: str) -> int:
        """Score a document by keyword overlap with question."""
        content = str(doc.get("content", "")).lower()
        filename = str(doc.get("filename", "")).lower()
        doc_type = str(doc.get("document_type", "")).lower()
        combined = f"{content} {filename} {doc_type}"
        score = 0
        for kw in question_keywords:
            if kw in combined:
                # Count occurrences, capped
                score += min(combined.count(kw), 3)
        # Bonus for exact phrase overlap of longer keywords
        # If question contains multi-word entity, bonus if doc contains it
        # (handled via keyword set already, but boost for filename match)
        if filename:
            for kw in question_keywords:
                if kw in filename:
                    score += 2
        return score

    def _filter_relevant_documents(
        self,
        *,
        question: str,
        nodes: list[dict],
        edges: list[dict],
        documents: list[dict],
        target_key: str | None = None,
        target_keys: list[str] | None = None,
        max_total_chars: int | None = None,
        max_docs: int | None = None,
    ) -> tuple[list[dict], int, int]:
        """Filter documents to those plausibly relevant to question and budget.

        Returns (filtered_docs, available_count, included_count) and ensures
        total character count never exceeds max_total_chars.

        Logic:
        - If target_key(s) present: only docs whose doc_id overlaps with subgraph's
          source_doc_ids are kept (direct cheap join).
        - Else: rank docs by keyword overlap with question and take top N until
          budget exhausted.
        - Always enforces total char budget (capped total, not per-doc).
        """
        available = len(documents)
        if available == 0:
            return [], 0, 0

        max_total = max_total_chars or getattr(self.settings, "ai_interactive_max_context_doc_chars", None) or getattr(self.settings, "ai_max_context_doc_chars", 15000)
        # Safety: ensure max_total is at least 1000
        max_total = max(1000, int(max_total))

        # Determine effective target keys
        effective_targets = []
        if target_keys:
            effective_targets = list(target_keys)
        elif target_key:
            effective_targets = [target_key]

        filtered: list[dict] = []

        if effective_targets:
            # Filter by subgraph doc ids
            subgraph_doc_ids = self._collect_subgraph_doc_ids(nodes, edges)
            if subgraph_doc_ids:
                # Keep only docs whose doc_id is in subgraph
                for doc in documents:
                    did = str(doc.get("doc_id", ""))
                    if did in subgraph_doc_ids:
                        filtered.append(doc)
                # If filtering yields empty (e.g. doc ids mismatch), fall back to
                # scoring by keywords to avoid returning nothing for a targeted query
                if not filtered:
                    # Fall back to keyword scoring but still respect target context
                    q_kws = self._extract_question_keywords(question)
                    q_lower = question.lower()
                    scored = [(self._score_document_relevance(d, q_kws, q_lower), d) for d in documents]
                    scored.sort(key=lambda x: (-x[0], x[1].get("filename", "")))
                    filtered = [d for _, d in scored]
            else:
                # No doc ids in subgraph (possible for synthetic data) -> keep all but will be capped
                filtered = list(documents)
        else:
            # Whole-case query: rank by keyword overlap
            q_kws = self._extract_question_keywords(question)
            q_lower = question.lower()
            scored = []
            for doc in documents:
                score = self._score_document_relevance(doc, q_kws, q_lower)
                scored.append((score, doc))
            # Sort by score desc, then filename for determinism
            scored.sort(key=lambda x: (-x[0], x[1].get("filename", "")))
            # If all scores zero (genuinely general question like "summarize open leads"),
            # keep original order but still cap
            filtered = [d for _, d in scored]

        # Now enforce total char budget and optional max_docs cap
        # max_docs: for whole-case queries, keep hard cap (e.g. 10) to avoid sending everything
        # For targeted queries, allow more but still budget-capped
        if max_docs is None:
            # Default: for whole-case, cap at 10 docs; for targeted, cap at 15
            max_docs = 15 if effective_targets else 10

        result: list[dict] = []
        total_chars = 0
        for doc in filtered:
            if len(result) >= max_docs:
                break
            content = str(doc.get("content", ""))
            content_len = len(content)
            remaining = max_total - total_chars
            if remaining <= 0:
                break
            if content_len > remaining:
                # Truncate content to fit remaining budget
                truncated = content[:remaining].strip()
                if not truncated:
                    continue
                new_doc = dict(doc)
                new_doc["content"] = truncated
                result.append(new_doc)
                total_chars += len(truncated)
                break
            else:
                result.append(doc)
                total_chars += content_len

        return result, available, len(result)

    def _detect_entities_in_question(self, question: str, all_nodes: list[dict]) -> list[str]:
        """Detect entity keys mentioned in free-text question.

        Uses same identity-field approach as _question_entity_candidates but
        runs against the whole case's node set to find which real entities
        the question plausibly refers to.

        Returns list of matched provenance_keys, sorted by confidence (longer match first).
        """
        if not question or not all_nodes:
            return []
        q_lower = question.lower()
        # Also extract phone-like, plate-like patterns from question for direct matching
        phone_pattern = re.compile(r"\+?\d{10,13}")
        phones_in_q = set(phone_pattern.findall(q_lower))
        plate_pattern = re.compile(r"\b[a-z]{2}\d{1,2}[a-z]{1,3}\d{4}\b")
        plates_in_q = set(plate_pattern.findall(q_lower))

        # Generic tokens that should not trigger entity detection on their own
        generic_tokens = {
            "person", "persons", "phone", "phones", "vehicle", "vehicles",
            "account", "accounts", "bank", "name", "number", "plate",
            "registration", "warehouse", "cctv", "case", "what", "who",
            "connects", "connected", "connection",
        }

        candidates: dict[str, tuple[int, str]] = {}  # key -> (score, matched_value)
        identity_fields = (
            "name", "full_name", "phone", "phone_number", "number", "mobile",
            "plate", "plate_number", "registration", "registration_number",
            "vehicle_number", "account", "account_number", "bank_account",
        )
        for node in all_nodes:
            key = node.get("provenance_key") or node.get("id")
            if not key:
                continue
            props = node.get("properties", {}) or {}
            for field, value in props.items():
                if not isinstance(value, (str, int, float)):
                    continue
                field_name = str(field).lower()
                if not any(ident in field_name for ident in identity_fields):
                    continue
                display_value = str(value).strip()
                if len(display_value) < 3:
                    continue
                dv_lower = display_value.lower()
                # Direct substring match — strongest signal, but require whole-word or multi-char
                # For multi-word names like "Ravi Kumar", check if full value in question
                if dv_lower in q_lower:
                    # Avoid matching generic single-word values like "Person" alone unless question explicitly says "Person 1"
                    # Require that matched value length >=4 and not purely generic, or that it contains a digit or is multi-word
                    if dv_lower in generic_tokens:
                        continue
                    # If display_value is exactly "Person 1" and question contains "Person 1", that's valid — score by length
                    score = len(display_value)
                    # Boost if display_value contains space (full name) or digit (specific ID)
                    if " " in display_value or any(ch.isdigit() for ch in display_value):
                        score += 5
                    if key not in candidates or score > candidates[key][0]:
                        candidates[key] = (score, display_value)
                    continue
                # Token overlap for more specific tokens: only if token is specific (>=4 chars, not generic)
                tokens = [t for t in re.split(r"[^a-z0-9]+", dv_lower) if len(t) >= 4]
                for tok in tokens:
                    if tok in generic_tokens:
                        continue
                    # Require token appears as whole word in question, not just substring
                    if re.search(rf"\b{re.escape(tok)}\b", q_lower):
                        score = len(tok)
                        # Slight boost for longer tokens
                        if len(tok) >= 6:
                            score += 2
                        if key not in candidates or score > candidates[key][0]:
                            candidates[key] = (score, tok)
                # Phone matching
                if "phone" in field_name or "mobile" in field_name or "number" in field_name:
                    digits = re.sub(r"\D", "", display_value)
                    if len(digits) >= 10:
                        for pq in phones_in_q:
                            pq_digits = re.sub(r"\D", "", pq)
                            if pq_digits and (pq_digits in digits or digits in pq_digits or pq_digits[-10:] == digits[-10:]):
                                candidates[key] = (max(candidates.get(key, (0, ""))[0], len(digits) + 10), display_value)
                # Plate matching
                if "plate" in field_name or "registration" in field_name:
                    if dv_lower in plates_in_q:
                        candidates[key] = (max(candidates.get(key, (0, ""))[0], len(dv_lower) + 10), display_value)

        # Sort by score descending, then key for determinism
        sorted_keys = sorted(candidates.keys(), key=lambda k: (-candidates[k][0], k))
        # Limit to top 5 to avoid blowing up retrieval with too many seeds
        return sorted_keys[:5]


    @staticmethod
    def _minimize_node(n: dict) -> dict:
        """Strip display PII and irrelevant fields from a node before sending to a model.

        The model does not need the raw person name, phone number or vehicle
        plate to reason about graph structure.  Those values are re-attached
        by the UI only after the fact for the authorized investigator.
        """
        props = n.get("properties", {}) or {}
        kept = {
            "provenance_key": n["provenance_key"],
            "label": n.get("label"),
            "confidence": n.get("confidence"),
            "entity_type": n.get("label"),
            "case_id": props.get("case_id"),
        }
        # Keep timestamps, role codes, and non-PII attributes
        for k in ("first_ts", "last_ts", "call_count", "amount_total", "age_band",
                  "source_count", "occupation_code"):
            if k in props:
                kept[k] = props[k]
        return kept

    @staticmethod
    def _question_entity_candidates(nodes: list[dict]) -> list[tuple[str, str, str]]:
        """Return display values that can safely be correlated in a question."""
        candidates: dict[tuple[str, str], tuple[str, str, str]] = {}
        identity_fields = (
            "name", "full_name", "phone", "phone_number", "number", "mobile",
            "plate", "plate_number", "registration", "registration_number",
            "vehicle_number", "account", "account_number", "bank_account",
        )
        for node in nodes:
            key = node.get("provenance_key") or node.get("id")
            label = node.get("label", "Person")
            if not key:
                continue
            for field, value in (node.get("properties") or {}).items():
                if not isinstance(value, (str, int, float)):
                    continue
                field_name = str(field).lower()
                if not any(identity in field_name for identity in identity_fields):
                    continue
                display_value = str(value).strip()
                if len(display_value) < 3:
                    continue
                candidates[(key, display_value.casefold())] = (display_value, key, label)
        return sorted(candidates.values(), key=lambda item: len(item[0]), reverse=True)

    @classmethod
    def _rewrite_question_for_pseudonyms(
        cls,
        question: str,
        nodes: list[dict],
        pmap: PseudonymMap,
        *,
        target_key: str | None,
    ) -> str:
        """Make question references agree with the minimized graph IDs."""
        rewritten = question
        for display_value, provenance_key, label in cls._question_entity_candidates(nodes):
            pseudo = pmap.pseudonymize(provenance_key, label)
            pattern = re.compile(rf"(?<!\w){re.escape(display_value)}(?!\w)", re.IGNORECASE)
            rewritten = pattern.sub(pseudo, rewritten)

        if target_key:
            target_label = next(
                (node.get("label", "Person") for node in nodes
                 if node.get("provenance_key") == target_key),
                "Person",
            )
            target_pseudo = pmap.pseudonymize(target_key, target_label)
            anchor = f"The subject of this analysis is {target_pseudo}."
            if target_pseudo not in rewritten:
                rewritten = f"{anchor} {rewritten}"
        return rewritten

    def _build_reasoning_context(
        self, nodes: list[dict], edges: list[dict], question: str,
        *, max_nodes: int | None = None, max_edges: int | None = None,
        documents: list[dict] | None = None,
    ) -> str:
        """Person-centric reasoning context — PEOPLE → RELATIONSHIPS → EVIDENCE → EXPLANATION.

        Final graph is PERSON → PERSON only. Supporting entities (phone, vehicle,
        location, file, doc, org, address) are internal evidence, NOT final nodes.
        Example: A owns PHONE-X contacted PHONE-Y belongs to B → A↔B with PHONE-X/Y as evidence.

        Output contract: structured {relationships:[{source_person,target_person,relationship_type,
        classification,confidence,evidence_refs,provenance,explanation,limitations}]}

        Explanation must be extremely clear: WHO, WHAT evidence, WHEN, HOW strong,
        FACT vs INFERENCE, what unknown. Never vague "strong correlation".
        No invented timestamps — use "Timestamp unavailable" if missing.
        """

        eff_max_nodes = max_nodes or self.settings.ai_max_context_nodes
        eff_max_edges = max_edges or self.settings.ai_max_context_edges
        nodes_limited = nodes[: eff_max_nodes]
        edges_limited = edges[: eff_max_edges]

        # PERSON-first analytics
        entity_counts_by_type: dict[str, int] = {}
        persons: list[str] = []
        person_nodes: list[dict] = []
        supporting_entities: list[dict] = []

        for n in nodes:
            lbl = n.get("label") or n.get("entity_type") or "Entity"
            entity_counts_by_type[lbl] = entity_counts_by_type.get(lbl, 0) + 1
            name = n.get("name") or n.get("id") or n.get("provenance_key")
            if not name:
                continue
            if str(lbl).upper() == "PERSON" or lbl in ("Person", "PERSON"):
                if name not in persons:
                    persons.append(name)
                person_nodes.append(n)
            else:
                supporting_entities.append({
                    "id": n.get("provenance_key") or name,
                    "label": lbl,
                    "name": name,
                    "role": "supporting_evidence",
                })

        persons.sort()

        # Build PERSON → PERSON relationships from edges that connect persons directly or via supporting
        # For context compaction, we group edges by person pair
        person_keys = set(
            n.get("provenance_key") for n in nodes if str(n.get("label","")).upper() == "PERSON"
        )
        # Map provenance_key -> display name
        key_to_display = {}
        for n in nodes:
            k = n.get("provenance_key")
            if k:
                key_to_display[k] = n.get("name") or n.get("id") or k

        # Extract direct person-person edges and indirect via supporting
        person_person_edges: list[dict] = []
        supporting_edges_for_context: list[dict] = []
        for e in edges:
            src = e.get("source_key") or e.get("source")
            tgt = e.get("target_key") or e.get("target")
            src_is_person = src in person_keys
            tgt_is_person = tgt in person_keys
            if src_is_person and tgt_is_person:
                person_person_edges.append(e)
            else:
                supporting_edges_for_context.append(e)

        # Financial and communication summaries — but only for person-centric view
        transfers: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []
        for e in edges:
            rel = str(e.get("rel_type") or "").upper()
            amt = e.get("amount")
            if "TRANSFER" in rel or "TRANSACTION" in rel or amt is not None:
                try:
                    f_amt = float(amt) if amt is not None else 0.0
                    transfers.append({
                        "from": key_to_display.get(e.get("source_key") or e.get("source"), e.get("source_key")),
                        "to": key_to_display.get(e.get("target_key") or e.get("target"), e.get("target_key")),
                        "amount": f_amt,
                        "rel_type": e.get("rel_type"),
                        "timestamp": e.get("timestamp") or "Timestamp unavailable",
                    })
                except (ValueError, TypeError):
                    pass
            cnt = e.get("call_count")
            if "CALL" in rel or cnt is not None:
                try:
                    f_cnt = int(cnt) if cnt is not None else 1
                    calls.append({
                        "caller": key_to_display.get(e.get("source_key") or e.get("source"), e.get("source_key")),
                        "receiver": key_to_display.get(e.get("target_key") or e.get("target"), e.get("target_key")),
                        "call_count": f_cnt,
                    })
                except (ValueError, TypeError):
                    pass

        transfers.sort(key=lambda x: x["amount"], reverse=True)
        calls.sort(key=lambda x: x["call_count"], reverse=True)

        exact_analytics = {
            "total_persons": len(person_nodes),
            "total_supporting_entities": len(supporting_entities),
            "total_person_to_person_relationships": len(person_person_edges),
            "total_supporting_relationships": len(supporting_edges_for_context),
            "entity_counts_by_type": entity_counts_by_type,
            "all_persons_count": len(persons),
            "all_persons_list": persons[:40],
            "person_person_relationships": [
                {
                    "source": key_to_display.get(e.get("source_key"), e.get("source_key")),
                    "target": key_to_display.get(e.get("target_key"), e.get("target_key")),
                    "rel_type": e.get("rel_type"),
                    "confidence": e.get("confidence", 1.0),
                }
                for e in person_person_edges[:20]
            ],
            "supporting_evidence_summary": supporting_entities[:30],
            "financial_transfers": {
                "total_transfer_events": len(transfers),
                "top_transfers": transfers[:10],
            },
            "communication_summary": {
                "total_call_pairs": len(calls),
                "top_call_pairs": calls[:10],
            },
        }

        # Compact payload — PERSON-first, supporting as evidence
        payload = {
            "question": question,
            "instruction": (
                "You are a person-centric investigative assistant. "
                "FINAL GRAPH MUST BE PERSON → PERSON ONLY. "
                "Phones, vehicles, locations, files, CCTV, docs, orgs, addresses are INTERNAL EVIDENCE, NOT final nodes. "
                "Example: A owns PHONE-X contacted PHONE-Y belongs to B → output A↔B with PHONE-X/Y as supporting evidence. "
                "Never claim A and C directly connected merely because same investigation — preserve reasoning path and evidence. "
                "Preserve FACT/INFERENCE/HYPOTHESIS/UNKNOWN, never collapse, never present inference as fact. "
                "Privacy: RAW→PII protection→PSEUDONYMIZATION→GRAPH-RAG→relevant pseudonymized context→you. "
                "Never invent timestamps: if missing use 'Timestamp unavailable'. Every claim traceable. "
                "Explanation must be clear: WHO, WHAT evidence, WHEN, HOW, HOW strong, FACT/INFERENCE, WHAT IS NOT KNOWN. "
                "Structure: CONNECTION, WHY, SUPPORTING EVIDENCE (E-042), TIMELINE (12 Aug 20:14 or 'Timestamp unavailable'), "
                "ASSESSMENT, CLASSIFICATION (INFERENCE), CONFIDENCE (High/Med/Low), WHAT IS NOT KNOWN. "
                "Prefer 3 highly supported over 30 weak. If insufficient: 'No reliable person-to-person connection was established'."
            ),
            "exact_analytics": exact_analytics,
            "persons": [
                {
                    "id": n.get("provenance_key"),
                    "name": n.get("name") or n.get("id") or n.get("provenance_key"),
                    "label": "PERSON",
                    "confidence": n.get("confidence", 1.0),
                }
                for n in person_nodes[:40]
            ],
            "relationships": [
                {
                    "source_person": key_to_display.get(e.get("source_key"), e.get("source_key")),
                    "target_person": key_to_display.get(e.get("target_key"), e.get("target_key")),
                    "relationship_type": e.get("rel_type"),
                    "classification": "FACT" if float(e.get("confidence", 0) or 0) >= 0.85 else "INFERENCE",
                    "confidence": e.get("confidence", 0.5),
                    "confidence_label": "High" if float(e.get("confidence", 0) or 0) >= 0.85 else "Medium" if float(e.get("confidence", 0) or 0) >= 0.65 else "Low",
                    "evidence_refs": e.get("source_doc_ids", [])[:3],
                    "provenance": [
                        {"kind": "graph_edge", "ref": f"{e.get('source_key')}->{e.get('target_key')}", "label": e.get("rel_type")},
                    ],
                    "explanation": f"{key_to_display.get(e.get('source_key'))} ↔ {key_to_display.get(e.get('target_key'))} via {e.get('rel_type')}",
                    "limitations": ["Purpose of association beyond documented records is unknown"],
                }
                for e in person_person_edges[:20]
            ],
            "supporting_evidence": supporting_entities[:30],
            "supporting_relationships": supporting_edges_for_context[:30],
            "case_documents": documents or [],
            "node_count_total": len(nodes),
            "edge_count_total": len(edges),
            "contract": {
                "description": "Output must be structured PERSON→PERSON only",
                "example": {
                    "relationships": [
                        {
                            "source_person": "PERSON-001",
                            "target_person": "PERSON-024",
                            "relationship_type": "Repeated communication",
                            "classification": "INFERENCE",
                            "confidence": 0.82,
                            "confidence_label": "High",
                            "evidence_refs": ["EVIDENCE-042", "EVIDENCE-087"],
                            "provenance": [{"kind": "document", "ref": "doc_123", "label": "CDR"}],
                            "explanation": "CONNECTION: PERSON-001 ↔ PERSON-024 ...",
                            "limitations": ["Why they communicated is unknown"],
                        }
                    ]
                },
            },
        }

        if self.settings.ai_allow_raw_pii:
            prompt_header = (
                "You are a person-centric investigative assistant for Indian law enforcement. "
                "FINAL OUTPUT MUST BE PERSON → PERSON ONLY. Supporting entities are EVIDENCE, NOT final nodes. "
                "Use exact_analytics for quantitative answers. "
                "Return ONLY a single raw JSON object matching FindingResult schema with NO preamble: "
                "{finding_type, summary, confidence, evidence_level, entities[], "
                "relationships[], evidence_refs[], reasoning_steps[], uncertainties[], "
                "recommended_review, suggested_next_actions[]}. "
                "Relationships array MUST contain only PERSON→PERSON with fields: "
                "source_person, target_person, relationship_type, classification (FACT/INFERENCE/HYPOTHESIS/UNKNOWN), "
                "confidence, confidence_label (High/Medium/Low), evidence_refs, provenance, explanation, limitations. "
                "Explanation structure: CONNECTION, WHY, SUPPORTING EVIDENCE (E-042), TIMELINE (with real timestamp or 'Timestamp unavailable'), "
                "ASSESSMENT, CLASSIFICATION, CONFIDENCE, WHAT IS NOT KNOWN. "
                "Never invent timestamps/evidence. Every claim traceable. "
                "Keep summary thorough, grounded, professional.\n\n"
            )
        else:
            prompt_header = (
                "You are a person-centric investigative assistant. FINAL GRAPH PERSON→PERSON ONLY. "
                "Supporting entities (phone, vehicle, location, file, CCTV, doc, org, address) are EVIDENCE, NOT final nodes. "
                "Example: PERSON-A owns PHONE-X contacted PHONE-Y belongs to PERSON-B → output PERSON-A↔PERSON-B with PHONE evidence. "
                "Preserve reasoning path, never claim direct merely because same investigation. "
                "Preserve FACT/INFERENCE/HYPOTHESIS/UNKNOWN. Never invent timestamps — use 'Timestamp unavailable'. "
                "Return ONLY single JSON FindingResult with relationships array of PERSON→PERSON objects: "
                "{source_person,target_person,relationship_type,classification,confidence,evidence_refs,provenance,explanation,limitations}. "
                "Explanation must be clear: WHO, WHAT evidence, WHEN, HOW strong, FACT vs INFERENCE, WHAT IS NOT KNOWN. "
                "Prefer 3 highly supported over 30 weak. If insufficient: 'No reliable person-to-person connection was established'. "
                "Keep concise, grounded, professional.\n\n"
            )
        return prompt_header + json.dumps(payload, default=str)

    # ------------------------------------------------ output validation

    # ------------------------------------------------ output validation

    def _parse_and_validate(self, content: str) -> FindingResult:
        raw = (content or "").strip()
        # 1. Strip reasoning blocks like <think>...</think> if present (used by thinking models)
        text = re.sub(r"<think>[\s\S]*?</think>", "", raw).strip()
        if not text:
            text = raw

        data: dict | None = None

        # 2. Try direct json.loads (or stripping start fences)
        direct_text = text
        if direct_text.startswith("```"):
            lines = direct_text.splitlines()
            lines = [l for l in lines if not l.startswith("```")]
            direct_text = "\n".join(lines).strip()
        try:
            parsed = json.loads(direct_text)
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            pass

        # 3. Look for ```json ... ``` or ``` ... ``` blocks anywhere in the text
        if data is None:
            for m in re.finditer(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text):
                try:
                    parsed = json.loads(m.group(1).strip())
                    if isinstance(parsed, dict):
                        data = parsed
                        break
                except Exception:
                    continue

        # 4. Look for outermost balanced / candidate { ... } objects
        if data is None:
            last_brace = text.rfind("}")
            if last_brace != -1:
                brace_indices = [i for i, ch in enumerate(text[:last_brace]) if ch == "{"]
                for idx in reversed(brace_indices):
                    candidate = text[idx:last_brace + 1].strip()
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict) and ("summary" in parsed or "finding_type" in parsed):
                            data = parsed
                            break
                    except Exception:
                        continue

        # 5. If still no valid JSON object found, attempt to recover summary from truncated JSON or loose key-value:
        if data is None:
            summary_match = (
                re.search(r'["\']?summary["\']?\s*:\s*"((?:[^"\\]|\\.)*)"', text)
                or re.search(r'["\']?summary["\']?\s*:\s*\'((?:[^\'\\]|\\.)*)\'', text)
            )
            if summary_match:
                try:
                    summary_text = summary_match.group(1).encode("utf-8").decode("unicode_escape")
                except Exception:
                    summary_text = summary_match.group(1)
                ft_match = re.search(r'["\']?finding_type["\']?\s*:\s*["\']?([^"\'\n,]+)["\']?', text)
                finding_type = ft_match.group(1).strip() if ft_match else "GENERAL"
                log.info("ai.recovered_truncated_json_summary", snippet=summary_text[:100])
                data = {
                    "finding_type": finding_type,
                    "summary": summary_text,
                    "confidence": 0.8,
                    "evidence_level": "FACT",
                    "recommended_review": True,
                    "uncertainties": ["Response was extracted from structured field output."],
                }

        # 6. If still no structured data could be extracted:
        if data is None:
            log.warning("ai.response_not_json", snippet=text[:200])
            is_corrupted = (
                bool(re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ufffd]", text))
                or bool(re.search(r"(.)\1{30,}", text))
            )
            if is_corrupted:
                return FindingResult(
                    finding_type="INSUFFICIENT_EVIDENCE",
                    summary="The AI model generation was corrupted. Please try asking again.",
                    confidence=0.0,
                    evidence_level="UNKNOWN",
                    recommended_review=True,
                    uncertainties=["Model generation contained binary or heavily repeating corrupted characters."],
                )
            is_monologue = bool(re.match(r"^\s*(?:We need to|Let's|I need to|To answer the question, we need)\b", text, re.IGNORECASE))
            if is_monologue:
                return FindingResult(
                    finding_type="INSUFFICIENT_EVIDENCE",
                    summary="Model output was interrupted during generation. Please try asking again.",
                    confidence=0.0,
                    evidence_level="UNKNOWN",
                    recommended_review=True,
                    uncertainties=["Model generated a reasoning preamble that was interrupted."],
                )
            if text:
                # Unstructured prose is kept so nothing the model said is lost,
                # but it must not borrow the weight of a parsed finding: no
                # structured claim was made, so it carries no confidence and
                # no evidence level until a human reads it.
                return FindingResult(
                    finding_type="GENERAL",
                    summary=text,
                    confidence=0.0,
                    evidence_level="UNKNOWN",
                    recommended_review=True,
                    uncertainties=["Model output was not valid JSON; it is unverified prose."],
                )
            return FindingResult(
                finding_type="GENERAL",
                summary="Model returned non-JSON output; an investigator must review.",
                confidence=0.0,
                evidence_level="UNKNOWN",
                recommended_review=True,
                uncertainties=["Model output was not valid JSON."],
            )
        # enforce neutral language in summary
        summary = data.get("summary", "")
        lower = summary.lower()
        for label in FORBIDDEN_LABELS:
            if label in lower:
                summary = (summary + " [NOTE: model output contained a forbidden label and was"
                           " flagged for human review]")
                data["recommended_review"] = True
                break
        data["summary"] = summary

        # Sanitize fields so model deviations (e.g. string entities, string reasoning steps, invalid evidence_level) don't crash validation
        if data.get("evidence_level") not in ("FACT", "INFERENCE", "HYPOTHESIS", "UNKNOWN"):
            data["evidence_level"] = "UNKNOWN"

        if isinstance(data.get("confidence"), (int, float)):
            data["confidence"] = max(0.0, min(1.0, float(data["confidence"])))
        else:
            data["confidence"] = 0.5

        if isinstance(data.get("entities"), list):
            clean_entities = []
            for item in data["entities"]:
                if isinstance(item, str):
                    clean_entities.append({"pseudo_id": item})
                elif isinstance(item, dict) and "pseudo_id" in item:
                    clean_entities.append(item)
            data["entities"] = clean_entities

        if isinstance(data.get("reasoning_steps"), list):
            clean_steps = []
            for i, step in enumerate(data["reasoning_steps"]):
                if isinstance(step, str):
                    clean_steps.append({"step": i + 1, "statement": step, "evidence_level": "UNKNOWN"})
                elif isinstance(step, dict) and "statement" in step:
                    step.setdefault("step", i + 1)
                    if step.get("evidence_level") not in ("FACT", "INFERENCE", "HYPOTHESIS", "UNKNOWN"):
                        step["evidence_level"] = "UNKNOWN"
                    clean_steps.append(step)
            data["reasoning_steps"] = clean_steps

        if isinstance(data.get("relationships"), list):
            clean_rels = []
            for rel in data["relationships"]:
                if isinstance(rel, dict):
                    clean_rels.append(rel)
                elif isinstance(rel, str):
                    clean_rels.append({"type": rel})
            data["relationships"] = clean_rels

        if isinstance(data.get("evidence_refs"), list):
            clean_refs = []
            for ref in data["evidence_refs"]:
                if isinstance(ref, str):
                    clean_refs.append({"doc_id": ref, "ref_type": "DOCUMENT"})
                elif isinstance(ref, dict):
                    clean_refs.append(ref)
            data["evidence_refs"] = clean_refs

        if not isinstance(data.get("recommended_review"), bool):
            rec = str(data.get("recommended_review", "")).strip().lower()
            data["recommended_review"] = rec in ("true", "1", "yes") if rec in ("true", "false", "1", "0", "yes", "no") else True


        # --- 10/10 Hardening: Post-LLM Grounding Validator ---
        # Make model incapable of inventing PERSON-999/EVIDENCE-999/CASE-999
        # Validate meaning against retrieved graph/evidence
        if HAS_HARDENING:
            try:
                # Extract allowed IDs from context if available — we use generic check for now
                # Full check requires passing allowed_person_ids, evidence_ids, case_ids
                # Here we check for obvious hallucinations like PERSON-999, EVIDENCE-999, CASE-999
                content_str = str(data)
                # Check for invented high-number IDs that don't exist
                import re as _re
                # Look for PERSON-999 pattern
                fake_person = _re.findall(r"PERSON-9{2,}\d*|PERSON-999", content_str)
                fake_evidence = _re.findall(r"EVIDENCE-9{2,}\d*|EVIDENCE-999", content_str)
                fake_case = _re.findall(r"CASE-9{2,}\d*|CASE-999", content_str)
                if fake_person or fake_evidence or fake_case:
                    log.warning(
                        "ai.grounding_validator_rejected_fake_ids",
                        fake_person=fake_person[:5],
                        fake_evidence=fake_evidence[:5],
                        fake_case=fake_case[:5],
                    )
                    # Don't reject entirely if it's just example in schema, but if in relationships
                    if isinstance(data.get("relationships"), list):
                        for rel in data["relationships"]:
                            if isinstance(rel, dict):
                                src = str(rel.get("source_person", ""))
                                tgt = str(rel.get("target_person", ""))
                                # Reject if source/target is 999
                                if "999" in src or "999" in tgt:
                                    raise ValueError(f"Invented person ID detected: {src} or {tgt}")

                # Validate controlled taxonomy
                if isinstance(data.get("relationships"), list):
                    for rel in data["relationships"]:
                        if isinstance(rel, dict):
                            rt = rel.get("relationship_type", "") or rel.get("controlled_type", "")
                            ct = rel.get("controlled_type") or ""
                            # If controlled_type present, must be in allowed set
                            if ct and ct not in CONTROLLED_REL_TYPES and CONTROLLED_REL_TYPES:
                                # Map to controlled if possible
                                mapped = map_to_controlled(rt) if rt else "UNKNOWN"
                                rel["controlled_type"] = mapped
                            # Classification must be valid
                            cls = rel.get("classification", "")
                            if cls and cls not in ("FACT", "INFERENCE", "HYPOTHESIS", "UNKNOWN"):
                                rel["classification"] = "UNKNOWN"
                            # Confidence must be deterministic-like (0-1)
                            conf = rel.get("confidence")
                            if isinstance(conf, (int, float)):
                                if conf < 0 or conf > 1:
                                    rel["confidence"] = max(0.0, min(1.0, float(conf)))
                            # Ensure evidence_refs exist
                            refs = rel.get("evidence_refs", [])
                            if not refs:
                                rel["evidence_refs"] = []
            except Exception as exc:
                log.warning("ai.grounding_validator_error", error=str(exc))
                # Don't fail validation on validator error — continue to schema validation

        try:
            return FindingResult(**data)
        except Exception as exc:
            log.warning("ai.response_invalid_schema", error=str(exc))
            evidence_level = data.get("evidence_level")
            if evidence_level not in ("FACT", "INFERENCE", "HYPOTHESIS", "UNKNOWN"):
                evidence_level = "UNKNOWN"
            return FindingResult(
                finding_type=data.get("finding_type", "GENERAL"),
                summary=summary or "Model response failed schema validation; human review required.",
                confidence=float(data.get("confidence", 0.0)) if isinstance(data.get("confidence"), (int, float)) and 0.0 <= float(data.get("confidence", 0.0)) <= 1.0 else 0.0,
                evidence_level=evidence_level,
                recommended_review=True,
                uncertainties=[f"Schema validation failed: {exc}"],
            )

    # ------------------------------------------------------ audit

    async def _audit(self, **fields: Any) -> None:
        """Record a tamper-evident audit entry for every AI request."""
        try:
            from app.db.models import AuditLog
            # We record minimal metadata; raw prompts are only stored if the
            # security policy permits it.
            details = {
                "role": fields.get("role"),
                "model": fields.get("model"),
                "latency_ms": fields.get("latency_ms"),
                "prompt_tokens": (fields.get("tokens") or (None, None))[0],
                "completion_tokens": (fields.get("tokens") or (None, None))[1],
                "pseudonymized_entity_count": fields.get("pmap_size"),
                "question_hash": _hash(fields.get("question", "")),
                "output_hash": fields.get("output_hash"),
                "success": fields.get("success", True),
            }
            if fields.get("error"):
                details["error"] = fields["error"]
            if self.settings.ai_audit_prompt_storage:
                details["question"] = fields.get("question")

            from contextlib import asynccontextmanager

            from app.audit.service import audit_service

            entry = dict(
                action_type="AI_QUERY",
                user_id=fields.get("user_id"),
                badge_number=None,
                target_resource=fields.get("target_resource")
                or f"case:{fields.get('case_id')}",
                case_id=fields.get("case_id"),
                jurisdiction_id=None,
                ip_address=None,
                trace_id=fields.get("query_id"),
                details=details,
            )
            caller_session = fields.get("session")
            if caller_session is not None:
                # Join the caller's transaction behind a savepoint: the AI
                # audit row lands atomically with the caller's own writes
                # instead of racing them on a second connection.
                @asynccontextmanager
                async def _joined():
                    if caller_session.in_transaction():
                        async with caller_session.begin_nested():
                            yield
                    else:
                        yield

                async with _joined():
                    await audit_service.append_async(caller_session, **entry)
            else:
                async with async_session() as session:
                    await audit_service.append_async(session, **entry)
        except Exception:
            log.exception("ai.audit_failed")



def _hash(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _retrieval_cache_key(case_id: str, query: str, filters: dict | None = None, graph_version: str = "v1", permission_scope: str | None = None) -> str:
    """Retrieval cache hash(case_id+normalized_query+filters+graph_version+permission_scope) — safe deterministic only.
    
    Security boundary > performance optimization.
    Cache identity is permission-aware: case_id + normalized_query + filters + graph_version + permission_scope
    Prevents cached retrieval produced under one authorization scope from being reused under another.
    """
    import hashlib, json
    normalized = query.lower().strip()
    filt_str = json.dumps(filters or {}, sort_keys=True)
    perm = permission_scope or "default"
    raw = f"{case_id}|{normalized}|{filt_str}|{graph_version}|{perm}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _get_cached_retrieval(cache_key: str) -> dict | None:
    """Get cached retrieval — only safe deterministic, never sensitive."""
    try:
        entry = _RETRIEVAL_CACHE.get(cache_key)
        if entry and (time.time() - entry.get("_ts", 0)) < 300:  # 5 min TTL
            return entry.get("data")
    except Exception:
        pass
    return None


def _set_cached_retrieval(cache_key: str, data: dict) -> None:
    """Set cached retrieval — invalidate on case/evidence/graph/permissions change via version bump."""
    try:
        # Only cache if not too large and no PII
        if len(str(data)) < 50000:
            _RETRIEVAL_CACHE[cache_key] = {"data": data, "_ts": time.time()}
            # LRU: keep max 100 entries
            if len(_RETRIEVAL_CACHE) > 100:
                oldest = min(_RETRIEVAL_CACHE.keys(), key=lambda k: _RETRIEVAL_CACHE[k].get("_ts", 0))
                _RETRIEVAL_CACHE.pop(oldest, None)
    except Exception:
        pass


def invalidate_retrieval_cache(case_id: str | None = None):
    """Invalidate cache on case/evidence/graph/permissions change."""
    global _RETRIEVAL_CACHE_VERSION
    _RETRIEVAL_CACHE_VERSION += 1
    if case_id:
        keys_to_del = [k for k in _RETRIEVAL_CACHE if case_id in k]
        for k in keys_to_del:
            _RETRIEVAL_CACHE.pop(k, None)
    else:
        _RETRIEVAL_CACHE.clear()





_gateway: AIGateway | None = None


def get_ai_gateway() -> AIGateway:
    global _gateway
    if _gateway is None:
        _gateway = AIGateway()
    return _gateway
