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
from app.ai.router import AIModelRouter, get_router
from app.ai.schemas import AIResponse, FindingResult
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

            # 1. Retrieve a relevant subgraph from the graph store
            await send({
                "type": "stage", "stage": "retrieving",
                "message": "Retrieving case context…",
            })
            nodes, edges = await self._retrieve_subgraph(
                case_id, depth=depth, target_key=target_key
            )
            documents = await self._retrieve_case_documents(case_id)
            timer.stage("retrieval_ms")
            context_report: dict[str, Any] = {
                "nodes": len(nodes),
                "edges": len(edges),
                "depth": depth,
                "target_key": target_key,
                "retrieved": bool(nodes or edges),
                # Isolation proof: retrieval ran against the active dataset's
                # case (and its graph), not a global id lookup.
                "dataset_id": dataset_id,
                "graph_ready": graph_ready,
                "timing": {},  # filled once below
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
            timer.stage("model_ms")
            if not result.get("available"):
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
    ) -> tuple[list[dict], list[dict]]:
        """Retrieve context for the case, bounded by the *context budget*.

        The bound is the prompt size (``ai_max_context_nodes`` /
        ``ai_max_context_edges``), not an arbitrary hop ceiling: retrieval
        walks as far as ``depth`` asks and stops early only when the budget is
        full or the subgraph is exhausted.  Clamping the hop count here used
        to silently narrow every question to two hops regardless of what the
        caller requested.
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
        keys = (
            self._neighbourhood_keys(snap, target_key, depth=depth, limit=max_nodes_eff)
            if target_key and target_key in snap.nodes
            else set(snap.nodes)
        )
        degree: dict[str, int] = {}
        for e in snap.edges:
            degree[e.source_key] = degree.get(e.source_key, 0) + 1
            degree[e.target_key] = degree.get(e.target_key, 0) + 1

        nodes: list[dict] = []
        for key in sorted(keys, key=lambda k: (-degree.get(k, 0), k)):
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
                            "filename": doc.filename,
                            "document_type": doc.document_type.value if hasattr(doc.document_type, "value") else str(doc.document_type),
                            "content": clean_text,
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
        """Render a JSON-serialized minimized subgraph into a prompt.

        This is the InvestigationReasoningContext (§21).
        """
        # trim edge/node lists to the configured limits to keep the prompt bounded
        eff_max_nodes = max_nodes or self.settings.ai_max_context_nodes
        eff_max_edges = max_edges or self.settings.ai_max_context_edges
        nodes_limited = nodes[: eff_max_nodes]
        edges_limited = edges[: eff_max_edges]

        # Deterministic graph analytics pre-computation
        entity_counts_by_type: dict[str, int] = {}
        persons: list[str] = []
        organizations: list[str] = []
        phones: list[str] = []
        vehicles: list[str] = []
        bank_accounts: list[dict[str, Any]] = []

        for n in nodes:
            lbl = n.get("label") or n.get("entity_type") or "Entity"
            entity_counts_by_type[lbl] = entity_counts_by_type.get(lbl, 0) + 1
            name = n.get("name") or n.get("id") or n.get("provenance_key")
            if not name:
                continue
            if lbl == "Person":
                if name not in persons:
                    persons.append(name)
            elif lbl == "Organization":
                if name not in organizations:
                    organizations.append(name)
            elif lbl == "Phone":
                if name not in phones:
                    phones.append(name)
            elif lbl == "Vehicle":
                if name not in vehicles:
                    vehicles.append(name)
            elif lbl == "BankAccount":
                acc_info: dict[str, Any] = {"account": name}
                if "account_holder" in n:
                    acc_info["holder"] = n["account_holder"]
                bank_accounts.append(acc_info)

        persons.sort()
        organizations.sort()

        # Financial transfer analysis
        transfers: list[dict[str, Any]] = []
        for e in edges:
            rel = str(e.get("rel_type") or "").upper()
            amt = e.get("amount")
            if "TRANSFER" in rel or "TRANSACTION" in rel or amt is not None:
                try:
                    f_amt = float(amt) if amt is not None else 0.0
                    transfers.append({
                        "from": e.get("source") or e.get("source_key"),
                        "to": e.get("target") or e.get("target_key"),
                        "amount": f_amt,
                        "rel_type": e.get("rel_type"),
                        "timestamp": e.get("timestamp"),
                    })
                except (ValueError, TypeError):
                    pass

        transfers.sort(key=lambda x: x["amount"], reverse=True)
        max_transfer = transfers[0] if transfers else None
        total_transfer_sum = sum(t["amount"] for t in transfers)

        # Communication analysis
        calls: list[dict[str, Any]] = []
        for e in edges:
            rel = str(e.get("rel_type") or "").upper()
            cnt = e.get("call_count")
            if "CALL" in rel or cnt is not None:
                try:
                    f_cnt = int(cnt) if cnt is not None else 1
                    calls.append({
                        "caller": e.get("source") or e.get("source_key"),
                        "receiver": e.get("target") or e.get("target_key"),
                        "call_count": f_cnt,
                    })
                except (ValueError, TypeError):
                    pass
        calls.sort(key=lambda x: x["call_count"], reverse=True)

        exact_analytics = {
            "total_entities": len(nodes),
            "total_relationships": len(edges),
            "entity_counts_by_type": entity_counts_by_type,
            "all_persons_count": len(persons),
            "all_persons_list": persons,
            "all_organizations_count": len(organizations),
            "all_organizations_list": organizations,
            "all_phones_count": len(phones),
            "all_vehicles_count": len(vehicles),
            "all_bank_accounts": bank_accounts,
            "financial_transfers": {
                "total_transfer_events": len(transfers),
                "total_amount_sum": total_transfer_sum,
                "maximum_transfer": max_transfer,
                "top_transfers": transfers[:10],
            },
            "communication_summary": {
                "total_call_pairs": len(calls),
                "top_call_pairs": calls[:10],
            },
        }

        payload = {
            "question": question,
            "exact_analytics": exact_analytics,
            "case_documents": documents or [],
            "nodes": nodes_limited,
            "relationships": edges_limited,
            "node_count_total": len(nodes),
            "edge_count_total": len(edges),
        }
        if self.settings.ai_allow_raw_pii:
            prompt_header = (
                "Below is the complete case evidence, including pre-computed exact analytics, uploaded case documents "
                "(FIRs, field reports, notes, witness statements), structured entities (persons, bank accounts, vehicles, phones), "
                "and financial/communication relationships. "
                "Use all of this evidence to answer the question accurately and thoroughly. "
                "CRITICAL: For quantitative questions (e.g. how many people or entities exist, complete lists of persons, "
                "maximum or minimum transfer amounts, total transfer sums, or call volumes), ALWAYS reference the exact counts, amounts, "
                "and lists in the 'exact_analytics' field. Do NOT estimate or re-count manually. "
                "In your summary and findings, ALWAYS refer to entities by their real names, bank accounts, vehicle plates, or phone numbers from the data, NOT by internal IDs. "
                "Return ONLY a single raw JSON object matching the FindingResult schema with NO introductory text, "
                "NO preamble, and NO commentary outside the JSON: "
                "{finding_type, summary, confidence, evidence_level, entities[], "
                "relationships[], evidence_refs[], reasoning_steps[], uncertainties[], "
                "recommended_review, suggested_next_actions[]}.\n"
                "Keep the summary direct, thorough, and informative. When asked to list entities, provide the complete list from exact_analytics in the summary.\n\n"
            )
        else:
            prompt_header = (
                "Below is a minimized, pseudonymized subgraph relevant to the question. "
                "Use only this evidence to answer. Return ONLY a single raw JSON object "
                "matching the FindingResult schema with NO introductory text, NO preamble, "
                "and NO commentary outside the JSON: "
                "{finding_type, summary, confidence, evidence_level, entities[], "
                "relationships[], evidence_refs[], reasoning_steps[], uncertainties[], "
                "recommended_review, suggested_next_actions[]}.\n"
                "Keep the summary concise (2-3 sentences) and limit arrays to at most 5 key items "
                "so the JSON response is complete and focused.\n\n"
            )
        return prompt_header + json.dumps(payload, default=str)

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
                return FindingResult(
                    finding_type="GENERAL",
                    summary=text,
                    confidence=0.8,
                    evidence_level="FACT",
                    recommended_review=True,
                    uncertainties=[],
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


_gateway: AIGateway | None = None


def get_ai_gateway() -> AIGateway:
    global _gateway
    if _gateway is None:
        _gateway = AIGateway()
    return _gateway
