"""Per-kind turn widgets — one Static subclass per Turn kind.

Phase 1 covers user / agent_text / system. Future phases add
agent_reasoning (collapsed thinking block), tool_call (collapsed strip),
epistemic_action (rich artifact card).
"""

from __future__ import annotations

from textual.widgets import Static

from empirica.core.chat.session import Turn, TurnKind


def render_turn(turn: Turn) -> Static:
    """Factory: pick the right widget class for a turn kind."""
    if turn.kind == TurnKind.USER:
        return UserTurn(turn)
    if turn.kind == TurnKind.AGENT_TEXT:
        return AgentTurn(turn)
    if turn.kind == TurnKind.SYSTEM:
        return SystemTurn(turn)
    # Phase 2+ kinds — render as plain text for now with kind label
    return UnknownTurn(turn)


class _BaseTurn(Static):
    """Common base — every turn carries its turn_id for later message routing."""

    DEFAULT_CSS = """
    _BaseTurn {
        margin: 0 1 1 1;
        padding: 0 1;
        height: auto;
    }
    """

    def __init__(self, turn: Turn) -> None:
        self.turn = turn
        super().__init__(self._format_body(), id=f"turn-{turn.turn_id[:8]}")

    def _format_body(self) -> str:
        return self.turn.text


class UserTurn(_BaseTurn):
    """User input — right-aligned style."""

    DEFAULT_CSS = """
    UserTurn {
        background: $primary 20%;
        border-left: thick $primary;
    }
    """

    def _format_body(self) -> str:
        return f"[b]you:[/b] {self.turn.text}"


class AgentTurn(_BaseTurn):
    """Agent text response — left-aligned style."""

    DEFAULT_CSS = """
    AgentTurn {
        background: $surface;
        border-left: thick $accent;
    }
    """

    def _format_body(self) -> str:
        return f"[b]agent:[/b] {self.turn.text}"


class SystemTurn(_BaseTurn):
    """System message — italic muted (compaction markers, mode transitions)."""

    DEFAULT_CSS = """
    SystemTurn {
        color: $text-muted;
        text-style: italic;
    }
    """

    def _format_body(self) -> str:
        return f"— {self.turn.text} —"


class UnknownTurn(_BaseTurn):
    """Fallback for Phase 2+ kinds while we incrementally build them."""

    DEFAULT_CSS = """
    UnknownTurn {
        background: $warning 20%;
        border-left: thick $warning;
    }
    """

    def _format_body(self) -> str:
        return f"[dim]({self.turn.kind.value})[/dim] {self.turn.text}"
