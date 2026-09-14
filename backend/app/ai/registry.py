"""Provider-neutral AI model registry abstraction.

Configuration selects a provider/model; this module supplies stable metadata and
never stores API keys.  Durable operator-managed entries live in the
``model_registry`` table and use the same fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.config import Settings, get_settings


@dataclass(frozen=True)
class ModelSpec:
    name: str
    provider: str
    version: str
    role: str
    prompt_version: str = "unknown"
    deployment_version: str = "unknown"
    evaluation_status: str = "UNTESTED"
    performance: dict[str, Any] | None = None
    registered_at: str = ""


class ModelRegistry:
    """Read-only resolved registry for the configured provider roles."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        now = datetime.now(timezone.utc).isoformat()
        self._entries = {
            role: ModelSpec(
                name=getattr(self.settings, f"ai_{role}_model"),
                provider=getattr(self.settings, f"ai_{role}_provider"),
                version=getattr(self.settings, f"ai_{role}_model").rsplit("/", 1)[-1],
                role=role.upper(),
                prompt_version="gateway-v1",
                deployment_version=self.settings.environment,
                registered_at=now,
            )
            for role in ("extraction", "reasoning", "explanation", "classification", "embedding")
        }

    def get(self, role: str) -> ModelSpec:
        try:
            return self._entries[role.lower()]
        except KeyError as exc:
            raise KeyError(f"No model is registered for role {role!r}") from exc

    def all(self) -> list[ModelSpec]:
        return list(self._entries.values())

    def as_dict(self) -> list[dict[str, Any]]:
        return [spec.__dict__.copy() for spec in self.all()]
