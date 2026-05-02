"""Multi-line input widget for empirica chat.

Phase 1: simple TextArea-based input. Enter submits, Shift+Enter newline.
Phase 4 will add command autocomplete + paste-friendly tweaks.
"""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import TextArea


class ChatInput(TextArea):
    """TextArea that emits a Submitted message on Enter (no shift)."""

    DEFAULT_CSS = """
    ChatInput {
        height: 5;
        max-height: 10;
        border: round $accent;
    }
    """

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    def __init__(self, **kwargs) -> None:
        # Plain text mode — no syntax highlighting in the input box.
        super().__init__(language=None, theme="monokai", **kwargs)

    async def _on_key(self, event: events.Key) -> None:
        # Enter (no modifier) submits. Shift+Enter and Ctrl+Enter fall through
        # to TextArea's default which inserts a newline.
        if event.key == "enter":
            event.stop()
            text = self.text.strip()
            if text:
                self.load_text("")
                self.post_message(self.Submitted(text))
