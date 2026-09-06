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

import asyncio
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
                   *, response_format: Any | None = None,
                   max_tokens: int | None = None) -> dict:
        """Invoke a chat model and return the parsed result.

        Returns ``{"available": False, "reason": ...}`` when the role has no
        key configured or the provider call fails — callers must detect this
        and report it, not crash and not invent an answer.

        Transient failures (timeout, rate limit, 5xx) are retried with
        exponential backoff up to ``ai_max_retries``; a deterministic failure
        such as a bad key or an unknown model is *not* retried, because
        repeating it only delays the operator learning what is wrong.
        """
        invocation = self.route(task)
        if not invocation.available:
            return {"available": False, "reason": f"no_api_key_for_role_{invocation.role}"}
        client = invocation.client()
        if client is None:
            return {"available": False, "reason": "openai_client_unavailable"}

        attempts = max(1, int(getattr(self.settings, "ai_max_retries", 2)) + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            started = time.perf_counter()
            try:
                kwargs: dict[str, Any] = {
                    "model": invocation.model,
                    "temperature": invocation.temperature,
                    "max_tokens": max_tokens or invocation.max_tokens,
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
                log.info(
                    "ai.invocation_ok",
                    role=invocation.role,
                    provider=invocation.provider,
                    model=invocation.model,
                    latency_ms=latency_ms,
                    attempt=attempt + 1,
                )
                return {
                    "available": True,
                    "content": content,
                    "model": invocation.model,
                    "provider": invocation.provider,
                    "role": invocation.role,
                    "latency_ms": latency_ms,
                    "attempts": attempt + 1,
                    "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
                    "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
                    "output_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
                }
            except Exception as exc:
                last_error = exc
                retryable = _is_retryable(exc)
                log.warning(
                    "ai.invocation_failed",
                    role=invocation.role,
                    provider=invocation.provider,
                    model=invocation.model,
                    error=_safe_error(exc),
                    attempt=attempt + 1,
                    retryable=retryable,
                )
                if not retryable or attempt == attempts - 1:
                    break
                await asyncio.sleep(min(2 ** attempt, 8))

        return {
            "available": False,
            "reason": f"invocation_failed: {type(last_error).__name__}",
            "detail": _safe_error(last_error) if last_error else None,
            "role": invocation.role,
            "model": invocation.model,
            "provider": invocation.provider,
        }

    async def chat_stream(self, task: str, system_prompt: str, user_prompt: str,
                          *, on_delta: Any = None,
                          response_format: Any | None = None,
                          max_tokens: int | None = None) -> dict:
        """Invoke a chat model *streaming*, forwarding completion deltas to ``on_delta``.

        Same availability contract as :meth:`chat` — a missing key or failed
        provider is reported, never faked. Differences that matter:

        * as soon as anything goes wrong **before** the first token arrives,
          the call degrades to the plain :meth:`chat` (a provider without
          ``stream=True`` support, or a refused handshake, must not make the
          feature unavailable — it just means no progressive rendering);
        * once tokens have been shown, there is no silent retry: replaying
          would duplicate text in the UI. The failure is reported honestly
          with ``partial: true`` so the caller can append a clear error.
        """
        invocation = self.route(task)
        if not invocation.available:
            return {"available": False, "reason": f"no_api_key_for_role_{invocation.role}"}
        client = invocation.client()
        if client is None:
            return {"available": False, "reason": "openai_client_unavailable"}

        started = time.perf_counter()
        pieces: list[str] = []
        try:
            kwargs: dict[str, Any] = {
                "model": invocation.model,
                "temperature": invocation.temperature,
                "max_tokens": max_tokens or invocation.max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": True,
            }
            if response_format is not None:
                kwargs["response_format"] = response_format
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                text = getattr(delta, "content", None) if delta is not None else None
                if not text:
                    continue
                pieces.append(text)
                if on_delta is not None:
                    maybe_awaitable = on_delta(text)
                    if asyncio.iscoroutine(maybe_awaitable):
                        await maybe_awaitable
            content = "".join(pieces)
            if not content.strip():
                raise RuntimeError("provider returned an empty stream")
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.perf_counter() - started) * 1000)
            if pieces:
                partial_text = "".join(pieces)
                log.warning(
                    "ai.stream_interrupted",
                    role=invocation.role, error=_safe_error(exc),
                    chars=sum(len(piece) for piece in pieces),
                )
                return {
                    "available": False,
                    "partial": True,
                    "content": partial_text,
                    "reason": f"stream_interrupted: {type(exc).__name__}",
                    "role": invocation.role, "model": invocation.model,
                    "provider": invocation.provider, "latency_ms": latency_ms,
                }
            log.warning(
                "ai.stream_unsupported",
                role=invocation.role, provider=invocation.provider,
                model=invocation.model, error=_safe_error(exc),
            )
            # No token shown yet: degrade to the non-streaming path, which
            # keeps its own retry policy. Streaming is an optimisation, never
            # a dependency.
            return await self.chat(
                task, system_prompt, user_prompt,
                response_format=response_format, max_tokens=max_tokens,
            )

        latency_ms = int((time.perf_counter() - started) * 1000)
        log.info(
            "ai.invocation_ok",
            role=invocation.role, provider=invocation.provider,
            model=invocation.model, latency_ms=latency_ms, streamed=True,
        )
        return {
            "available": True,
            "content": content,
            "model": invocation.model,
            "provider": invocation.provider,
            "role": invocation.role,
            "latency_ms": latency_ms,
            "attempts": 1,
            "streamed": True,
            "prompt_tokens": None,
            "completion_tokens": None,
            "output_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
        }

    async def embed(self, task: str, inputs: list[str]) -> dict:
        """Produce embeddings through the configured embedding role.

        Same contract as :meth:`chat`: a missing key or a failed provider is
        reported, never faked with random vectors — a fabricated embedding
        would silently corrupt every similarity result downstream.
        """
        invocation = self.route(task)
        if not invocation.available:
            return {"available": False, "reason": f"no_api_key_for_role_{invocation.role}"}
        client = invocation.client()
        if client is None:
            return {"available": False, "reason": "openai_client_unavailable"}
        if not inputs:
            return {"available": False, "reason": "no_input_supplied"}

        started = time.perf_counter()
        try:
            response = await client.embeddings.create(
                model=invocation.model, input=inputs
            )
            vectors = [list(item.embedding) for item in response.data]
            latency_ms = int((time.perf_counter() - started) * 1000)
            log.info(
                "ai.embedding_ok",
                role=invocation.role,
                model=invocation.model,
                count=len(vectors),
                latency_ms=latency_ms,
            )
            return {
                "available": True,
                "embeddings": vectors,
                "dimensions": len(vectors[0]) if vectors else 0,
                "model": invocation.model,
                "provider": invocation.provider,
                "role": invocation.role,
                "latency_ms": latency_ms,
            }
        except Exception as exc:
            log.warning(
                "ai.embedding_failed",
                role=invocation.role,
                model=invocation.model,
                error=_safe_error(exc),
            )
            return {
                "available": False,
                "reason": f"invocation_failed: {type(exc).__name__}",
                "detail": _safe_error(exc),
                "role": invocation.role,
                "model": invocation.model,
            }


#: Exception *names* that indicate a transient condition worth retrying.  Names
#: rather than types so this works whether or not the openai package is
#: installed, and without importing provider-specific exception classes.
_RETRYABLE_NAMES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "InternalServerError",
        "RateLimitError",
        "ReadTimeout",
        "TimeoutError",
        "asyncio.TimeoutError",
    }
)

#: Substrings that must never reach a log line or an API response.
_SECRET_HINTS = ("api_key", "api-key", "authorization", "bearer ", "sk-", "nvapi-")


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if type(exc).__name__ in _RETRYABLE_NAMES:
        return True
    status = getattr(exc, "status_code", None)
    return status in {408, 409, 425, 429, 500, 502, 503, 504}


def _safe_error(exc: Exception | None) -> str:
    """A provider error message with any credential material removed.

    Provider SDKs sometimes echo the request — including headers — back in the
    exception text.  Logging that verbatim would put the API key in the log
    file, so anything that looks like a credential is redacted before the
    message is used anywhere.
    """
    if exc is None:
        return ""
    message = f"{type(exc).__name__}: {exc}"
    lowered = message.lower()
    if any(hint in lowered for hint in _SECRET_HINTS):
        words = []
        for word in message.split():
            stripped = word.strip("'\"{},")
            if len(stripped) > 16 and any(
                hint in stripped.lower() for hint in ("sk-", "nvapi-", "bearer")
            ):
                words.append("[redacted]")
            else:
                words.append(word)
        message = " ".join(words)
    return message[:500]


_router: AIModelRouter | None = None


def get_router() -> AIModelRouter:
    global _router
    if _router is None:
        _router = AIModelRouter()
    return _router
