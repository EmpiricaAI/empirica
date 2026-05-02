"""empirica chat — Textual TUI app entry (Phase 1).

Standalone-usable skeleton. Phase 1 capabilities:
  - Header with mode badge + model + clock (placeholders for Phase 6)
  - Conversation scroll with rendered turns (user / agent_text / system)
  - Multi-line input (Enter submits, Shift+Enter newline)
  - Footer with key bindings
  - --feed sample.jsonl loads pre-baked conversation (no app-server dep)
  - --session-id RESUME loads an existing session from disk
  - All turns auto-persist to ~/.empirica/chat_sessions/{session_id}.jsonl

Phase 2 wires app-server WebSocket. Phase 3 wires translator event tap.
Phase 4 adds artifact cards. See CHAT.md for the full roadmap.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header

from empirica.core.chat.session import ChatSession, Turn, TurnKind, load_turns

from .chat.conversation import ConversationScroll
from .chat.input import ChatInput

REFRESH_SECONDS = 2.0


class ChatApp(App):
    """empirica chat — single-instance collaborative epistemic workspace."""

    CSS = """
    Screen { layout: vertical; }
    #chat-conversation { height: 1fr; }
    #chat-input { dock: bottom; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+l", "clear_input", "Clear input"),
    ]

    TITLE = "empirica chat"
    SUB_TITLE = "🤖 conversational"  # Phase 6: dynamic from autonomy mode

    def __init__(
        self,
        feed_path: Path | None = None,
        session_id: str | None = None,
        feed_delay: float = 0.0,
    ) -> None:
        super().__init__()
        self.feed_path = feed_path
        self.session_id_to_resume = session_id
        self.feed_delay = feed_delay
        self._session: ChatSession | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield ConversationScroll(id="chat-conversation")
            yield ChatInput(id="chat-input")
        yield Footer()

    def on_mount(self) -> None:
        # Establish the chat session — resume if requested, else create new.
        if self.session_id_to_resume:
            self._session = ChatSession.load(self.session_id_to_resume)
            self._convo().render_existing(self._session.turns)
        else:
            self._session = ChatSession.create()

        # Optional: replay a sample feed (no app-server dep — useful for
        # reviewing the rendering UX before wiring upstream).
        if self.feed_path:
            self.run_worker(self._replay_feed(), thread=False)

        # Focus the input so the user can start typing immediately.
        self.query_one(ChatInput).focus()

    def _convo(self) -> ConversationScroll:
        return self.query_one("#chat-conversation", ConversationScroll)

    async def _replay_feed(self) -> None:
        """Stream turns from a feed file into the conversation."""
        assert self.feed_path is not None  # noqa: S101 — type narrowing
        assert self._session is not None  # noqa: S101 — type narrowing
        for turn in load_turns(self.feed_path):
            self._session.append(turn)
            self._convo().append_turn(turn)
            if self.feed_delay > 0:
                await asyncio.sleep(self.feed_delay)

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        """User pressed Enter on a non-empty input."""
        assert self._session is not None  # noqa: S101 — type narrowing
        turn = Turn.new(TurnKind.USER, event.text)
        self._session.append(turn)
        self._convo().append_turn(turn)

        # Phase 1: no app-server, so we just echo a placeholder agent
        # response so the round-trip is visible. Phase 2 replaces this with
        # a real WebSocket send.
        echo = Turn.new(
            TurnKind.SYSTEM,
            "(no agent connected — wire app-server in Phase 2 to get real responses)",
        )
        self._session.append(echo)
        self._convo().append_turn(echo)

    def action_clear_input(self) -> None:
        try:
            self.query_one(ChatInput).load_text("")
        except Exception:  # noqa: S110 — clear is best-effort UI op
            pass


def run_chat(
    feed_path: Path | None = None,
    session_id: str | None = None,
    feed_delay: float = 0.0,
) -> int:
    app = ChatApp(feed_path=feed_path, session_id=session_id, feed_delay=feed_delay)
    app.run()
    return 0
