"""
Qdrant Vector Database Integration

Provides persona registry and semantic search capabilities.

``PersonaRegistry`` is resolved on first access: importing it here eagerly loaded ``qdrant_client`` (0.76 s) for every
module in this package, including the read-only REST search that tool-router runs on each prompt (goal 2dc0b760).
"""

from typing import Any

__all__ = ["PersonaRegistry"]


def __getattr__(name: str) -> Any:
    if name == "PersonaRegistry":
        from empirica.core.qdrant.persona_registry import PersonaRegistry

        return PersonaRegistry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
