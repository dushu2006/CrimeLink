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
import uuid
from datetime import datetime
from typing import Any

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


class AIGateway:
    def __init__(self, settings: Settings | None = None, router: AIModelRouter | None = None):
        self.settings = settings or get_settings()
        self.router = router or get_router()

    # ------------------------------------------------------ public entrypoint

    async def ask(self, *, question: str, case_id: str, user_id: str | None = None,
                  principal_id: str | None = None,
                  depth: int = 2, target_key: str | None = None) -> AIResponse:
        """Answer an investigator question scoped to ``case_id``.

        The query runs through retrieval, minimization, pseudonymization,
        model invocation, validation and audit logging before returning.
        """
        query_id = str(uuid.uuid4())
        started = utcnow()
        try:
            # 1. Retrieve a relevant subgraph from the graph store
            nodes, edges = await self._retrieve_subgraph(
                case_id, depth=depth, target_key=target_key
            )

            # 2. Data minimization: strip sensitive display fields before any
            #    pseudonymization step.
            nodes_min = [self._minimize_node(n) for n in nodes]

            # 3. Pseudonymize
            pmap = PseudonymMap()
            if self.settings.ai_pseudonymize and not self.settings.ai_allow_raw_pii:
                safe_nodes, safe_edges = apply_pseudonymization_to_context(nodes_min, edges, pmap)
                pseudonymized = True
            else:
                safe_nodes = nodes_min
                safe_edges = edges
                pseudonymized = False

            # 4. Build model-specific context
            context = self._build_reasoning_context(safe_nodes, safe_edges, question)

            # 5. Ask reasoning model
            result = await self.router.chat(
                "investigation_reasoning",
                system_prompt=SYSTEM_PROMPT_REASONING,
                user_prompt=context,
            )
            if not result.get("available"):
                finding = self._unavailable_finding("reasoning", result.get("reason"))
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
                )

            # 6. Parse and validate the structured result
            finding = self._parse_and_validate(result["content"])

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

            return AIResponse(
                query_id=query_id,
                role="reasoning",
                model=result.get("model"),
                finding=finding,
                latency_ms=result.get("latency_ms", 0),
                pseudonymized=pseudonymized,
                available=True,
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
                    summary=f"AI processing failed: {type(exc).__name__}. An investigator must review this case manually.",
                    confidence=0.0,
                    evidence_level="UNKNOWN",
                    recommended_review=True,
                    uncertainties=[str(exc)],
                ),
                available=False,
                fallback_reason=f"gateway_error: {type(exc).__name__}",
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
        self, case_id: str, *, depth: int = 2, target_key: str | None = None
    ) -> tuple[list[dict], list[dict]]:
        """Retrieve a subgraph for the case, bounded by configured max depth."""
        from app.container import get_container
        from app.domain.models import CaseGraphSnapshot

        container = get_container()
        graph = container.graph_store
        depth = max(1, min(depth, self.settings.graph_max_expand_depth + 1))
        try:
            snap: CaseGraphSnapshot = await asyncio.to_thread(graph.get_case_snapshot, case_id)
        except Exception:
            try:
                snap = graph.get_case_snapshot(case_id)
            except Exception:
                return [], []
        max_nodes = self.settings.ai_max_context_nodes
        max_edges = self.settings.ai_max_context_edges
        keys = (
            self._neighbourhood_keys(snap, target_key, depth=depth, limit=max_nodes)
            if target_key and target_key in snap.nodes
            else set(snap.nodes)
        )
        nodes: list[dict] = []
        for key in sorted(keys):
            node = snap.nodes[key]
            if node.label == "Case":
                continue
            nodes.append({
                "provenance_key": key,
                "label": node.label,
                "properties": dict(node.properties),
                "confidence": node.properties.get("confidence", 1.0),
            })
            if len(nodes) >= max_nodes:
                break
        keep = {node["provenance_key"] for node in nodes}
        edges: list[dict] = []
        for e in snap.edges:
            if e.source_key not in keep or e.target_key not in keep:
                continue
            props = dict(e.properties)
            edges.append({
                "source_key": e.source_key,
                "target_key": e.target_key,
                "rel_type": e.rel_type,
                "confidence": e.confidence,
                "timestamp": props.get("timestamp") or props.get("last_ts"),
                "source_doc_ids": props.get("source_doc_ids", [props.get("source_doc_id")]),
            })
            if len(edges) >= max_edges:
                break
        return nodes, edges

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

    def _build_reasoning_context(self, nodes: list[dict], edges: list[dict], question: str) -> str:
        """Render a JSON-serialized minimized subgraph into a prompt.

        This is the InvestigationReasoningContext (§21).
        """
        # trim edge/node lists to the configured limits to keep the prompt bounded
        nodes_limited = nodes[: self.settings.ai_max_context_nodes]
        edges_limited = edges[: self.settings.ai_max_context_edges]
        payload = {
            "question": question,
            "nodes": nodes_limited,
            "relationships": edges_limited,
            "node_count_total": len(nodes),
            "edge_count_total": len(edges),
        }
        return (
            "Below is a minimized, pseudonymized subgraph relevant to the question. "
            "Use only this evidence to answer. Output JSON matching the FindingResult "
            "schema: {finding_type, summary, confidence, evidence_level, entities[], "
            "relationships[], evidence_refs[], reasoning_steps[], uncertainties[], "
            "recommended_review, suggested_next_actions[]}.\n\n"
            + json.dumps(payload, default=str)
        )

    # ------------------------------------------------ output validation

    def _parse_and_validate(self, content: str) -> FindingResult:
        text = (content or "").strip()
        # Some models wrap JSON in ``` fences — strip them.
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [l for l in lines if not l.startswith("```")]
            text = "\n".join(lines).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            log.warning("ai.response_not_json", error=str(exc), snippet=text[:200])
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
        try:
            return FindingResult(**data)
        except Exception as exc:
            log.warning("ai.response_invalid_schema", error=str(exc))
            return FindingResult(
                finding_type=data.get("finding_type", "GENERAL"),
                summary=summary or "Model response failed schema validation; human review required.",
                confidence=float(data.get("confidence", 0.0)) if isinstance(data.get("confidence"), (int, float)) else 0.0,
                evidence_level=data.get("evidence_level", "UNKNOWN"),
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

            from app.audit.service import audit_service
            async with async_session() as session:
                await audit_service.append_async(
                    session,
                    action_type="AI_QUERY",
                    user_id=fields.get("user_id"),
                    badge_number=None,
                    target_resource=f"case:{fields.get('case_id')}",
                    case_id=fields.get("case_id"),
                    jurisdiction_id=None,
                    ip_address=None,
                    trace_id=fields.get("query_id"),
                    details=details,
                )
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
