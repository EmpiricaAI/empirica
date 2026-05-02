"""Chat command handler — `empirica chat`.

Thin wrapper around the Textual app. Phase 1 just spawns the TUI with
optional --feed / --session-id / --feed-delay flags.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def handle_chat_command(args: Any) -> int:
    """Launch the empirica chat TUI."""
    # Lazy import — avoid loading Textual unless this command actually runs.
    from empirica.cli.tui.chat_app import run_chat

    feed_path = Path(args.feed) if getattr(args, "feed", None) else None
    if feed_path is not None and not feed_path.exists():
        print(f"empirica chat: --feed file not found: {feed_path}")
        return 2

    return run_chat(
        feed_path=feed_path,
        session_id=getattr(args, "session_id", None),
        feed_delay=getattr(args, "feed_delay", 0.0) or 0.0,
    )
