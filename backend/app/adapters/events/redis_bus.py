"""Redis pub/sub event bus for production WebSocket fan-out.

The Celery worker and the FastAPI process may run in different Vercel or
worker instances.  Redis pub/sub is therefore the production implementation
of the event-bus port; the in-process implementation remains selected only by
 the embedded profile (or by the existing defensive fallback if adapter
 initialization itself is unavailable).

Publishers are synchronous pipeline/Celery code, while subscribers are
asyncio WebSocket handlers.  The two Redis clients deliberately use their
matching APIs so a worker thread never touches an asyncio event loop and a
WebSocket handler never blocks its loop on synchronous I/O.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from app.config import Settings, get_settings


class RedisEventBus:
    """Redis pub/sub implementation of the event-bus interface."""

    backend_name = "redis"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._redis: Redis | None = None

    def _publisher(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(
                self.settings.redis_url,
                decode_responses=True,
            )
        return self._redis

    def publish(self, channel: str, message: dict[str, Any]) -> None:
        """Publish one JSON event synchronously from worker/pipeline code."""
        self._publisher().publish(channel, json.dumps(message, default=str))

    async def subscribe(self, channel: str) -> AsyncIterator[dict[str, Any]]:
        """Yield JSON events from one dedicated async Redis subscription."""
        client = AsyncRedis.from_url(
            self.settings.redis_url,
            decode_responses=True,
        )
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for item in pubsub.listen():
                if item.get("type") != "message":
                    continue
                payload = item.get("data")
                if not isinstance(payload, str):
                    continue
                decoded = json.loads(payload)
                if isinstance(decoded, dict):
                    yield decoded
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
            await client.aclose()

    def close(self) -> None:
        """Close the synchronous publisher connection when the process stops."""
        if self._redis is not None:
            self._redis.close()
            self._redis = None
