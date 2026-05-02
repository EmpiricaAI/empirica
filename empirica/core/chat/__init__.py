"""empirica chat — single-instance collaborative epistemic workspace.

See `empirica/docs/architecture/CHAT.md` for the full spec.

Phase 1 deliverables:
  - ChatSession state + jsonl persistence
  - Turn data model (user, agent_text, system) — extended in later phases
  - Source of truth: ~/.empirica/chat_sessions/{session_id}.jsonl
"""

from .session import ChatSession, Turn, TurnKind

__all__ = ["ChatSession", "Turn", "TurnKind"]
