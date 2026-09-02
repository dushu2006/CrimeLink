"""Registry of source adapters.

Adapters are registered at import time by name.  The synthetic corpus, file
import and future government adapters register themselves here.
"""

from __future__ import annotations

from typing import Any

from .protocol import SourceAdapter

_REGISTRY: dict[str, type[SourceAdapter]] = {}


def register_source_adapter(name: str, cls: type[SourceAdapter]) -> None:
    _REGISTRY[name] = cls


def get_source_adapter(name: str, **kwargs: Any) -> SourceAdapter:
    if name not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise KeyError(f"Unknown source adapter '{name}'. Available: {available}")
    return _REGISTRY[name](**kwargs)


def available_adapters() -> list[str]:
    return sorted(_REGISTRY)
