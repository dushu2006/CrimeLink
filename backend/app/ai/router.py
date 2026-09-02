"""AI model router.

Model selection is centralized here: the rest of the codebase asks the router
for a client for a given task ("investigation_reasoning", "explanation",
"classification", "embedding", "extraction") and never hard-codes a model
name.  Every role can be pointed at a different provider/model via env vars.
When no API key is configured for a role the router returns ``None`` and the
gateway either falls back to the heuristic provider or returns a structured
"insufficient evidence" result.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.logging import get_logger

log = get_logger("crimelink.ai.router")


TASK_TO_ROLE: dict[str, str] = {
    "extraction": "extraction",
    "ner": "extraction",
    "investigation_reasoning": "reasoning",
    "multi_hop_reasoning": "reasoning",
    "explanation": "explanation",
    "summarization": "explanation",
    "classification": "classification",
    "prioritization": "classification",
    "anomaly_triage": "classification",
    "embedding": "embedding",
    "retrieval": "embedding",
    "similarity": "embedding",
}


@dataclass
class ModelInvocation:
    """Prepared model invocation (endpoint, credentials, model name)."""
    role: str
    provider: str
    model: str
    api_key: str | None
    base_url: str
    temperature: float
    max_tokens: int
    timeout: float

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def client(self):
        """Return an OpenAI-compatible client, or None if unavailable."""
        if not self.api_key:
            return None
        try:
            from openai import AsyncOpenAI
        except Exception:  # pragma: no cover
            return None
        return AsyncOpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)


class AIModelRouter:
    """Selects the right model for each task and exposes a uniform client."""

    def __init__(self, settings=None) -> None:
        self.settings = settings or get_settings()

    def route(self, task: str) -> ModelInvocation:
        role = TASK_TO_ROLE.get(task, task)
        cfg = self.settings.role_config(role)
        return ModelInvocation(
            role=role,
            provider=cfg["provider"],
            model=cfg["model"],
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            temperature=cfg["temperature"],
            max_tokens=cfg["max_tokens"],
            timeout=cfg["timeout"],
        )

    async def chat(self, task: str, system_prompt: str, user_prompt: str,
                   *, response_format: Any | None = None) -> dict:
        """Invoke a chat model and return the parsed JSON result.

        Returns ``{"available": False, "reason": "no_api_key"}`` when the
        requested role has no key configured — callers must detect this and
        fall back, not crash.
        """
        invocation = self.route(task)
        if not invocation.available:
            return {"available": False, "reason": f"no_api_key_for_role_{invocation.role}"}
        client = invocation.client()
        if client is None:
            return {"available": False, "reason": "openai_client_unavailable"}
        started = time.perf_counter()
        try:
            kwargs: dict[str, Any] = {
                "model": invocation.model,
                "temperature": invocation.temperature,
                "max_tokens": invocation.max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            if response_format is not None:
                kwargs["response_format"] = response_format
            response = await client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            latency_ms = int((time.perf_counter() - started) * 1000)
            return {
                "available": True,
                "content": content,
                "model": invocation.model,
                "role": invocation.role,
                "latency_ms": latency_ms,
                "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
                "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
                "output_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
            }
        except Exception as exc:
            log.warning("ai.invocation_failed", role=invocation.role, model=invocation.model,
                        error=str(exc))
            return {"available": False, "reason": f"invocation_failed: {type(exc).__name__}"}


_router: AIModelRouter | None = None


def get_router() -> AIModelRouter:
    global _router
    if _router is None:
        _router = AIModelRouter()
    return _router
