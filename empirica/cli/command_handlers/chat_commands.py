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
    # textual is in the [tui] extra; headless installs hit this surface.
    try:
        from empirica.cli.tui.chat_app import run_chat
    except ImportError as exc:
        if 'textual' in str(exc).lower():
            print('empirica chat needs the textual TUI library. Install with:')
            print('  pip install "empirica[tui]"')
            return 2
        raise

    feed_path = Path(args.feed) if getattr(args, "feed", None) else None
    if feed_path is not None and not feed_path.exists():
        print(f"empirica chat: --feed file not found: {feed_path}")
        return 2

    # Parse --provider flags (repeatable) into Provider objects
    from empirica.core.chat.providers import parse_provider_spec
    raw_providers = getattr(args, "provider", None) or []
    providers = []
    for spec in raw_providers:
        try:
            providers.append(parse_provider_spec(spec))
        except ValueError as e:
            print(f"empirica chat: --provider parse error: {e}")
            return 2

    return run_chat(
        feed_path=feed_path,
        session_id=getattr(args, "session_id", None),
        feed_delay=getattr(args, "feed_delay", 0.0) or 0.0,
        translator_url=getattr(args, "translator_url", None),
        model=getattr(args, "model", "deepseek-chat") or "deepseek-chat",
        instructions=getattr(args, "system", None),
        providers=providers,
        autonomy_mode=getattr(args, "autonomy", "assistant") or "assistant",
        enable_system_prompt=getattr(args, "enable_system_prompt", True),
    )
