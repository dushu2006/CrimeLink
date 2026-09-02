"""Per-user request rate limiting (PRD 12.6).

Auth endpoints are limited far more tightly than the general API because they
are the credential-stuffing surface.  The implementation is an in-process
sliding window, which is correct for a single API instance; when CrimeLink is
scaled behind a load balancer, swap this adapter for a Redis-backed counter —
the call site does not change.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque

from fastapi import Request

from app.config import Settings, get_settings
from app.errors import RateLimitError

_buckets: dict[str, Deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def _consume(key: str, limit: int, window_s: float = 60.0) -> None:
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
    _consume(f"{'auth' if auth else 'api'}:{identity}", limit)
    if auth and path.endswith("/login"):
        _consume(f"login:{_client_ip(request)}", settings.rate_limit_auth_per_minute)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def reset() -> None:
    """Clear all buckets (used by tests)."""
    with _lock:
        _buckets.clear()
