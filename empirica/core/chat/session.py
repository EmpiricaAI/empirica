"""ChatSession state — turns + jsonl persistence.

Source of truth lives at `~/.empirica/chat_sessions/{session_id}.jsonl`.
Append-only — one JSON object per line. The file alone is enough to
reconstruct the session (replay mode in Phase 7 reads from it).

Phase 1 supports: user, agent_text, system turn kinds.
Phase 2 adds: tool_call, tool_result (from app-server stream).
Phase 3 adds: agent_reasoning (from translator event tap).
Phase 4 adds: epistemic_action (artifact cards).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path


class TurnKind(str, Enum):
    USER = "user"
    AGENT_TEXT = "agent_text"
    AGENT_REASONING = "agent_reasoning"     # Phase 3
    TOOL_CALL = "tool_call"                 # Phase 2
    TOOL_RESULT = "tool_result"             # Phase 2
    EPISTEMIC_ACTION = "epistemic_action"   # Phase 4
    SYSTEM = "system"


@dataclass
class Turn:
    """One observable event in the conversation timeline."""

    turn_id: str
    ts_ms: int
    kind: TurnKind
    text: str = ""
    # Reserved for later phases. Phase 1 ignores.
    metadata: dict = field(default_factory=dict)

    def to_jsonl(self) -> str:
        """Serialize as a single JSON line for persistence."""
        d = asdict(self)
        d["kind"] = self.kind.value
        return json.dumps(d, ensure_ascii=False)

    @classmethod
    def from_jsonl(cls, line: str) -> Turn:
        """Parse one JSONL line back to a Turn."""
        raw = json.loads(line)
        raw["kind"] = TurnKind(raw["kind"])
        # Tolerate forward-compat fields by ignoring unknowns
        known = {"turn_id", "ts_ms", "kind", "text", "metadata"}
        return cls(**{k: v for k, v in raw.items() if k in known})

    @classmethod
    def new(cls, kind: TurnKind, text: str = "", metadata: dict | None = None) -> Turn:
        return cls(
            turn_id=str(uuid.uuid4()),
            ts_ms=int(time.time() * 1000),
            kind=kind,
            text=text,
            metadata=metadata or {},
        )


def _default_chat_root() -> Path:
    return Path.home() / ".empirica" / "chat_sessions"


@dataclass
class ChatSession:
    """In-memory state + on-disk jsonl persistence.

    Use `ChatSession.create(root=...)` for a fresh session, or
    `ChatSession.load(session_id, root=...)` to resume one.
    """

    session_id: str
    jsonl_path: Path
    turns: list[Turn] = field(default_factory=list)

    @classmethod
    def create(cls, root: Path | None = None) -> ChatSession:
        root = root or _default_chat_root()
        root.mkdir(parents=True, exist_ok=True)
        session_id = str(uuid.uuid4())
        jsonl_path = root / f"{session_id}.jsonl"
        # Touch the file so subsequent appends work even if no turns yet.
        jsonl_path.touch()
        return cls(session_id=session_id, jsonl_path=jsonl_path, turns=[])

    @classmethod
    def load(cls, session_id: str, root: Path | None = None) -> ChatSession:
        root = root or _default_chat_root()
        jsonl_path = root / f"{session_id}.jsonl"
        if not jsonl_path.exists():
            raise FileNotFoundError(f"chat session {session_id} not found at {jsonl_path}")
        turns = list(load_turns(jsonl_path))
        return cls(session_id=session_id, jsonl_path=jsonl_path, turns=turns)

    def append(self, turn: Turn) -> None:
        """Append a turn to memory + flush to jsonl."""
        self.turns.append(turn)
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(turn.to_jsonl() + "\n")
            f.flush()


def load_turns(path: Path) -> Iterator[Turn]:
    """Yield Turn objects from a jsonl file (e.g., for --feed mode)."""
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            yield Turn.from_jsonl(line)
