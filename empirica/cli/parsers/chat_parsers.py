"""Chat parser — registers `empirica chat` subcommand.

See `empirica/docs/architecture/CHAT.md` for the design + scope per phase.
Phase 1 supports: --feed PATH (replay a pre-baked jsonl), --session-id ID
(resume a session), --feed-delay SECONDS (pacing for visual replay).
"""

from __future__ import annotations


def add_chat_parsers(subparsers) -> None:
    chat = subparsers.add_parser(
        "chat",
        help="Launch the empirica chat TUI (single-instance collaborative workspace)",
    )
    chat.add_argument(
        "--feed",
        metavar="PATH",
        help="Replay a pre-baked jsonl conversation (Phase 1 demo mode — no app-server needed)",
    )
    chat.add_argument(
        "--feed-delay",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="Delay between feed turns when replaying (default: 0, instant)",
    )
    chat.add_argument(
        "--session-id",
        metavar="UUID",
        help="Resume an existing chat session from ~/.empirica/chat_sessions/",
    )
