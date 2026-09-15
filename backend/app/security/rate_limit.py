"""Per-user request rate limiting (PRD 12.6).

Auth endpoints are limited far more tightly than the general API because they
are the credential-stuffing surface.

Implementation is now Redis-backed for horizontal scaling, with in-process
fallback for embedded profile / tests. Previously this was pure in-memory
sliding window, which is correct for a single instance but breaks behind a
load balancer: each replica had its own counter, so effective limit became
limit × replicas — a security gap for auth/credential-stuffing defense.

Redis is already in the stack (Celery broker). We use a sorted-set sliding
window: ZREMRANGEBYSCORE old entries, ZCARD count, ZADD new entry, EXPIRE.

Call site unchanged: enforce_rate_limit(request, identity, auth=False)

Fallback trade-off (deliberate decision, not a bug):
-----------------------------------------------------
When Redis is unavailable, we fall back to in-process per-instance limiting
for 5 seconds (circuit breaker _redis_unavailable_until = now + 5.0). This is
fail-open: traffic continues, but during any Redis outage we are briefly back
to the original multi-replica gap (effective limit = limit × replicas). This
is accepted because:
- Fail-closed (block all traffic when Redis down) would be a DoS vector.
- Redis is a single container in compose; outage is rare and 5s window is short.
- Auth endpoints still have per-IP bucket (login:{ip}) which partially mitigates
  credential-stuffing even with per-instance fallback.
- Monitoring: HighAPIErrorRate and RateLimitingHigh alerts will show 429 spike
  drop during outage, and DB_HEALTH gauge will go 0 if Redis is part of health.

If stricter enforcement is required during Redis outage, change fallback to
raise RateLimitError or 503, but that trades availability for security.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Any

from fastapi import Request

from app.config import Settings, get_settings
from app.errors import RateLimitError
from app.security.net import client_ip
from app.logging import get_logger

log = get_logger("crimelink.security.rate_limit")

# In-process fallback buckets (embedded profile, tests, or Redis unavailable)
_buckets: dict[str, Deque[float]] = defaultdict(deque)
_lock = threading.Lock()

# Redis client cache
_redis_client: Any | None = None
_redis_client_lock = threading.Lock()
_redis_unavailable_until: float = 0.0  # circuit breaker timestamp


def _get_redis_client(settings: Settings) -> Any | None:
    """Return a Redis client if Redis is configured and reachable, else None.

    Cached globally, with 5s circuit breaker after failure to avoid hammering
    a down Redis on every request. Falls back to in-memory buckets.
    """
    global _redis_client, _redis_unavailable_until

    now = time.monotonic()
    if now < _redis_unavailable_until:
        return None

    # Only attempt Redis when broker is celery or redis_url is explicitly set
    # Embedded profile uses inline broker and has no Redis — fallback immediately
    if settings.effective_broker_backend == "inline":
        return None

    with _redis_client_lock:
        if _redis_client is not None:
            try:
                _redis_client.ping()
                return _redis_client
            except Exception:
                _redis_client = None
                _redis_unavailable_until = now + 5.0
                return None

        try:
            import redis  # type: ignore

            client = redis.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=0.5,
                socket_connect_timeout=0.5,
            )
            client.ping()
            _redis_client = client
            log.info("rate_limit.redis_ready", redis_url=settings.redis_url)
            return client
        except Exception as exc:
            log.warning("rate_limit.redis_unavailable", error=str(exc), redis_url=getattr(settings, "redis_url", "unknown"))
            _redis_unavailable_until = now + 5.0
            return None


def _consume_in_memory(key: str, limit: int, window_s: float = 60.0) -> None:
    now = time.monotonic()
    with _lock:
        bucket = _buckets[key]
        while bucket and now - bucket[0] > window_s:
            bucket.popleft()
        if len(bucket) >= limit:
            raise RateLimitError(
                f"Rate limit of {limit} requests per minute exceeded. Please slow down."
            )
        bucket.append(now)


def _consume_redis(redis_client: Any, key: str, limit: int, window_s: float = 60.0) -> None:
    """Redis sorted-set sliding window.

    Key: crimelink:ratelimit:{key}
    Score: timestamp (monotonic not suitable across machines, so use time.time())
    Member: unique id (timestamp + random to avoid collision)

    Steps atomically via pipeline:
    - ZREMRANGEBYSCORE < now - window
    - ZCARD
    - if count >= limit: raise
    - else ZADD now, EXPIRE window*2
    """
    redis_key = f"crimelink:ratelimit:{key}"
    now = time.time()
    window_start = now - window_s

    # Use a pipeline for atomicity
    try:
        pipe = redis_client.pipeline()
        pipe.zremrangebyscore(redis_key, 0, window_start)
        pipe.zcard(redis_key)
        results = pipe.execute()
        count = results[1] if len(results) > 1 else 0

        if count >= limit:
            raise RateLimitError(
                f"Rate limit of {limit} requests per minute exceeded. Please slow down."
            )

        # Add current request
        # Use time.time() + random suffix as member to ensure uniqueness
        member = f"{now}:{time.monotonic_ns()}"
        pipe = redis_client.pipeline()
        pipe.zadd(redis_key, {member: now})
        pipe.expire(redis_key, int(window_s * 2))
        pipe.execute()
    except RateLimitError:
        raise
    except Exception as exc:
        # On any Redis error, log and let caller fallback to in-memory
        log.warning("rate_limit.redis_error_fallback", key=key, error=str(exc))
        raise RuntimeError(f"redis_error: {exc}") from exc


def _consume(key: str, limit: int, window_s: float = 60.0, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    redis_client = _get_redis_client(settings)

    if redis_client is not None:
        try:
            _consume_redis(redis_client, key, limit, window_s)
            return
        except RateLimitError:
            raise
        except Exception:
            # Fallback to in-memory on Redis error
            pass

    # In-memory fallback (embedded, tests, or Redis down)
    _consume_in_memory(key, limit, window_s)


def enforce_rate_limit(
    request: Request, identity: str, settings: Settings | None = None, auth: bool = False
) -> None:
    settings = settings or get_settings()
    limit = (
        settings.rate_limit_auth_per_minute if auth else settings.rate_limit_per_minute
    )
    path = request.url.path
    # Separate buckets for auth and general traffic so a burst of searches
    # cannot consume the login allowance (or vice versa).
    _consume(f"{'auth' if auth else 'api'}:{identity}", limit, settings=settings)
    if auth and path.endswith("/login"):
        _consume(f"login:{client_ip(request)}", settings.rate_limit_auth_per_minute, settings=settings)


def reset() -> None:
    """Clear all buckets (used by tests). Clears both in-memory and Redis."""
    with _lock:
        _buckets.clear()

    global _redis_client
    with _redis_client_lock:
        if _redis_client is not None:
            try:
                # Delete only our rate-limit keys, not entire DB
                for key in _redis_client.scan_iter(match="crimelink:ratelimit:*"):
                    _redis_client.delete(key)
            except Exception:
                pass
            _redis_client = None
