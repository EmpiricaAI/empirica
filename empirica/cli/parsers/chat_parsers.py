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
    chat.add_argument(
        "--translator-url",
        metavar="URL",
        help="ecodex translator base URL (e.g. http://127.0.0.1:18080/v1). "
              "When set, user messages are dispatched to the translator and "
              "agent responses stream back as AgentTurns. When unset, chat "
              "runs in render-only mode (Phase 1 fallback).",
    )
    chat.add_argument(
        "--model",
        metavar="MODEL",
        default="deepseek-chat",
        help="Model id passed to the translator (default: deepseek-chat)",
    )
    chat.add_argument(
        "--system",
        metavar="TEXT",
        help="System instructions injected as the leading message",
    )
    chat.add_argument(
        "--provider",
        action="append",
        metavar="NAME=URL[,model=M][,wire=W][,key_env=ENV]",
        help="Register a provider. Repeatable. Examples: "
              "ollama=http://192.168.1.68:11434/v1,model=qwen3.5:latest  · "
              "deepseek=https://api.deepseek.com/v1,model=deepseek-chat,key_env=DEEPSEEK_API_KEY  · "
              "translator=http://127.0.0.1:18080/v1,wire=responses. "
              "When omitted: builtin empirica-server defaults are loaded "
              "(ollama, qwopus, llcpp, llcpp-alt). Switch at runtime with "
              "/provider NAME and /model NAME.",
    )
    chat.add_argument(
        "--autonomy",
        choices=("assistant", "copilot", "autonomous"),
        default="assistant",
        help="Autonomy mode for the AI in this session (default: assistant). "
              "assistant = waits for confirmation; copilot = takes obvious "
              "next steps; autonomous = pursues stated objective with "
              "checkpoints at coherent boundaries.",
    )
    chat.add_argument(
        "--no-system-prompt",
        dest="enable_system_prompt",
        action="store_false",
        default=True,
        help="Disable the empirica chat system prompt. The model will not be "
              "told it's in empirica chat or made aware of slash commands. "
              "Use --system to supply your own prompt instead.",
    )
    chat.add_argument(
        "--replay",
        metavar="SESSION_ID",
        help="Open a past chat session in read-only replay mode. Loads all "
              "turns from ~/.empirica/chat_sessions/{SESSION_ID}.jsonl, "
              "renders them, and disables LLM dispatch. Use --feed-delay to "
              "pace the playback if reviewing visually. Cannot be combined "
              "with --session-id (resume) or --feed.",
    )
