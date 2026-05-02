"""Slash command surface registry for empirica chat (Phase 16).

Per David's directive: real users use natural language + a small set
of /commands. Slash commands split into two surfaces:
  - user-facing — shown in `/help`, documented in system prompt
  - dev-internal — hidden by default, surfaced via `/help debug`

The table here is the single source of truth. ChatApp dispatches via
`SLASH_HANDLERS` (cmd → method-name); this module owns the table +
help-text rendering. Handlers themselves live on ChatApp because they
need access to session/registry/widget state.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCmd:
    """One slash command's surface metadata."""

    name: str
    description: str
    user_facing: bool
    takes_arg: bool = False
    arg_label: str = "ARG"


# Order = display order in /help. User-facing first, then dev/QA.
SLASH_TABLE: tuple[SlashCmd, ...] = (
    # ─── User-facing (visible in plain /help) ───────────────────────
    SlashCmd("help", "show available commands (try /help debug for the full surface)", user_facing=True),
    SlashCmd("model", "set active model on the current provider", user_facing=True, takes_arg=True, arg_label="NAME"),
    SlashCmd("plan", "show open goals + recent transactions for this project", user_facing=True),
    SlashCmd("autonomy", "switch autonomy mode (assistant | copilot | autonomous)", user_facing=True, takes_arg=True, arg_label="MODE"),

    # ─── Dev/QA (only via /help debug) ──────────────────────────────
    SlashCmd("providers", "list configured providers", user_facing=False),
    SlashCmd("provider", "switch active provider", user_facing=False, takes_arg=True, arg_label="NAME"),
    SlashCmd("models", "list models on active provider", user_facing=False),
    SlashCmd("statusline", "cycle (or set) statusline mode: basic|default|learning|full", user_facing=False, takes_arg=True, arg_label="[MODE]"),
    SlashCmd("finding", "create a finding (Phase 4 v0 demo)", user_facing=False, takes_arg=True, arg_label="TEXT"),
    SlashCmd("decision", "create a decision (Phase 4 v0 demo)", user_facing=False, takes_arg=True, arg_label="TEXT"),
    SlashCmd("unknown", "create an unknown question (Phase 4 v0 demo)", user_facing=False, takes_arg=True, arg_label="TEXT"),
)


def _format_row(cmd: SlashCmd) -> str:
    invocation = f"/{cmd.name}"
    if cmd.takes_arg:
        invocation += f" {cmd.arg_label}"
    return f"  {invocation:<28} {cmd.description}"


def render_help(*, debug: bool = False) -> str:
    """Return the body of /help as a multi-line string.

    `debug=False` (the default for `/help`): user-facing commands only.
    `debug=True` (`/help debug`): includes dev/QA commands.
    """
    rows: list[str] = ["slash commands:"]
    for cmd in SLASH_TABLE:
        if not cmd.user_facing and not debug:
            continue
        rows.append(_format_row(cmd))
    if not debug:
        rows.append("")
        rows.append("/help debug shows dev-internal commands (providers, statusline, artifact-creation demos).")
    rows.append("")
    rows.append("Anything else goes to the agent (current provider:model shown in header).")
    return "\n".join(rows)


def known_commands(*, include_dev: bool = True) -> set[str]:
    """Set of recognized command names — used by the dispatcher."""
    return {c.name for c in SLASH_TABLE if include_dev or c.user_facing}
