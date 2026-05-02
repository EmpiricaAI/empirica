"""ConversationScroll widget — scrollable list of rendered turns."""

from __future__ import annotations

from textual.containers import VerticalScroll

from empirica.core.chat.session import Turn

from .turn import render_turn


class ConversationScroll(VerticalScroll):
    """Vertical scroll containing one widget per turn.

    Auto-scrolls to bottom when a new turn is appended (so the user always
    sees the latest exchange). Manually scrolling up disables auto-scroll
    until they scroll back to bottom.
    """

    DEFAULT_CSS = """
    ConversationScroll {
        height: 1fr;
        background: $background;
        padding: 1;
    }
    """

    def append_turn(self, turn: Turn) -> None:
        """Mount a new turn widget at the bottom and auto-scroll."""
        widget = render_turn(turn)
        self.mount(widget)
        # Auto-scroll to bottom — Textual handles smooth animation.
        self.call_after_refresh(self.scroll_end, animate=False)

    def render_existing(self, turns: list[Turn]) -> None:
        """Bulk-render turns at startup (e.g., from --feed or replay)."""
        for turn in turns:
            self.mount(render_turn(turn))
        self.call_after_refresh(self.scroll_end, animate=False)
