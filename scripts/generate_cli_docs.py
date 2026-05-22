#!/usr/bin/env python3
"""Regenerate docs/human/developers/CLI_COMMANDS_UNIFIED.md from live parsers.

Introspects the empirica CLI's argparse tree (via `create_argument_parser()`)
and emits a categorized markdown reference. Uses `_HELP_CATEGORIES` from
`cli_core` to group commands into the same sections `empirica help` shows.

This replaces the brittle AST-extraction approach in
`_archive/dev_scripts/doc_regeneration/` — live introspection picks up
dynamic argument additions, nested subparsers, and aliases without parsing
Python source.

Usage:
    python3 scripts/generate_cli_docs.py
        Write docs/human/developers/CLI_COMMANDS_UNIFIED.md

    python3 scripts/generate_cli_docs.py --dry-run
        Print to stdout, don't touch the file

    python3 scripts/generate_cli_docs.py --output /tmp/cli.md
        Write to a custom path

Designed to run from the repo root or anywhere on the system; it inserts the
repo root onto sys.path so `empirica.cli.cli_core` imports cleanly.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _get_version() -> str:
    try:
        from empirica import __version__
        return __version__
    except Exception:
        return "unknown"


def _format_choices(choices: list[Any] | None) -> str:
    if not choices:
        return ""
    return "{" + ", ".join(str(c) for c in choices) + "}"


def _format_arg(action: argparse.Action) -> dict[str, Any]:
    """Extract a single argparse Action into a doc-friendly dict."""
    is_flag = isinstance(
        action,
        (argparse._StoreTrueAction, argparse._StoreFalseAction),
    )
    is_positional = not action.option_strings

    names = action.option_strings or [action.dest]
    primary = names[0]
    aliases = names[1:] if len(names) > 1 else []

    required = bool(getattr(action, "required", False)) or is_positional

    arg_type: str
    if is_flag:
        arg_type = "flag"
    elif action.nargs in ("*", "+"):
        arg_type = "list"
    elif action.choices:
        arg_type = "choice"
    else:
        type_obj = getattr(action, "type", None)
        if type_obj is None:
            arg_type = "string"
        elif hasattr(type_obj, "__name__"):
            arg_type = type_obj.__name__
        else:
            arg_type = str(type_obj)

    default = action.default
    if default is argparse.SUPPRESS:
        default = None

    return {
        "name": primary,
        "aliases": aliases,
        "is_positional": is_positional,
        "required": required,
        "type": arg_type,
        "default": default,
        "choices": list(action.choices) if action.choices else [],
        "help": (action.help or "").replace("%(prog)s", "empirica").replace("%%", "%"),
        "metavar": action.metavar,
    }


def _collect_args(parser: argparse.ArgumentParser) -> list[dict[str, Any]]:
    """Return every argument on `parser`, skipping the help action."""
    out: list[dict[str, Any]] = []
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if isinstance(action, argparse._SubParsersAction):
            continue
        out.append(_format_arg(action))
    return out


def _collect_subcommands(parser: argparse.ArgumentParser) -> list[dict[str, Any]]:
    """Return nested subcommands of `parser`, if any (e.g. `loop register`)."""
    subs: list[dict[str, Any]] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub_parser in action.choices.items():
                # argparse aliases share the parser object; dedupe by id
                if any(s["_parser_id"] == id(sub_parser) for s in subs):
                    # add the alias to the matching entry
                    for s in subs:
                        if s["_parser_id"] == id(sub_parser):
                            s["aliases"].append(name)
                    continue
                # Help text for nested subcommands: try the SubParsersAction's
                # _choices_actions first (carries the `help=` from add_parser),
                # then fall back to the parser's own description.
                help_text = ""
                for ca in action._choices_actions:
                    if ca.dest == name:
                        help_text = (ca.help or "").strip()
                        break
                if not help_text:
                    help_text = (sub_parser.description or "").strip()
                subs.append({
                    "name": name,
                    "aliases": [],
                    "help": help_text,
                    "args": _collect_args(sub_parser),
                    "subcommands": _collect_subcommands(sub_parser),
                    "_parser_id": id(sub_parser),
                })
    return subs


def collect_commands() -> dict[str, dict[str, Any]]:
    """Walk the main parser and return a {command_name: command_info} map."""
    from empirica.cli.cli_core import create_argument_parser
    parser = create_argument_parser()

    commands: dict[str, dict[str, Any]] = {}
    sub_action: argparse._SubParsersAction | None = None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            sub_action = action
            break
    if sub_action is None:
        return commands

    # Build a reverse map for argparse aliases (multiple names → one parser)
    seen_parsers: dict[int, str] = {}
    for name, sub_parser in sub_action.choices.items():
        pid = id(sub_parser)
        if pid in seen_parsers:
            # this `name` is an alias for the canonical name
            canonical = seen_parsers[pid]
            commands[canonical]["aliases"].append(name)
            continue
        seen_parsers[pid] = name

        help_text = ""
        # Subparser help shows up via _choices_actions on the SubParsersAction
        for ca in sub_action._choices_actions:
            if ca.dest == name:
                help_text = (ca.help or "").strip()
                break

        commands[name] = {
            "name": name,
            "aliases": [],
            "help": help_text or (sub_parser.description or "").strip(),
            "args": _collect_args(sub_parser),
            "subcommands": _collect_subcommands(sub_parser),
        }
    return commands


def _format_arg_line(arg: dict[str, Any]) -> str:
    name = arg["name"]
    aliases = arg.get("aliases") or []
    name_str = f"`{name}`"
    if aliases:
        name_str += " / " + " / ".join(f"`{a}`" for a in aliases)

    parts: list[str] = []
    if arg["required"]:
        parts.append("**required**")
    else:
        parts.append("optional")
    if arg["type"] == "flag":
        parts.append("flag")
    elif arg["type"] != "string":
        parts.append(f"type=`{arg['type']}`")
    if arg["choices"]:
        parts.append(f"choices={_format_choices(arg['choices'])}")
    if arg["default"] not in (None, False):
        parts.append(f"default=`{arg['default']}`")

    meta = " · ".join(parts)
    line = f"- {name_str} — {meta}"
    if arg["help"]:
        line += f"\n  {arg['help']}"
    return line


def _format_command_block(cmd: dict[str, Any], level: int = 4) -> str:
    """Emit a markdown block for one command (no leading blank line)."""
    out: list[str] = []
    hashes = "#" * level
    title = f"`empirica {cmd['name']}`"
    if cmd["aliases"]:
        alias_str = ", ".join(f"`{a}`" for a in cmd["aliases"])
        title += f"  _(aliases: {alias_str})_"
    out.append(f"{hashes} {title}")
    if cmd["help"]:
        out.append("")
        out.append(cmd["help"])

    if cmd["args"]:
        out.append("")
        out.append("**Arguments:**")
        out.append("")
        for arg in cmd["args"]:
            out.append(_format_arg_line(arg))

    if cmd["subcommands"]:
        out.append("")
        out.append("**Subcommands:**")
        for sub in cmd["subcommands"]:
            out.append("")
            out.append(_format_command_block(
                {**sub, "name": f"{cmd['name']} {sub['name']}"},
                level=level + 1,
            ))

    out.append("")
    return "\n".join(out)


_PROLOGUE = """# Empirica CLI Commands — Unified Reference

> **Auto-generated.** This document is regenerated from the live argparse
> tree by `scripts/generate_cli_docs.py`. Do not edit by hand — your edits
> will be overwritten on the next regen. Add new commands by registering
> their parser via `add_*_parsers(subparsers)` in
> `empirica/cli/parsers/__init__.py`; the generator picks them up
> automatically.
>
> Categories below follow `_HELP_CATEGORIES` in
> `empirica/cli/cli_core.py` — adding a new category means editing that
> dictionary, then running this script.

**Framework version:** {version}
**Generated:** {timestamp} UTC
**Total commands:** {total} (across {category_count} categories)

For the most up-to-date detail on any single command, prefer
`empirica <command> --help` — the generator extracts the same `help`
strings argparse uses at runtime, but argparse can render dynamic context
(env-resolved defaults, conditional choices) that a static document
cannot.

---

## Transaction-First Pattern

Most commands auto-derive `--session-id` from the active transaction.
When you're inside an epistemic transaction workflow (after PREFLIGHT),
you don't need to specify `--session-id` explicitly.

The CLI uses `get_active_empirica_session_id()` with this priority chain:

1. **Active transaction** (`active_transaction_*.json`) — highest priority
2. **Active work context** (`active_work_*.json`) — from project-switch
3. **Instance projects** (`instance_projects/*.json`) — tmux/terminal aware

Commands that auto-derive `session_id` include all `*-log` artifacts,
`goals-*`, `epistemics-*`, and most read paths. The few that still
require `--session-id` (`project-bootstrap`, `sessions-show`,
`sessions-export`) document it explicitly.

---

## Category Index

"""


def render(commands: dict[str, dict[str, Any]]) -> str:
    """Top-level render — emits the full markdown document."""
    from empirica.cli.cli_core import _HELP_CATEGORIES

    out: list[str] = []
    out.append(_PROLOGUE.format(
        version=_get_version(),
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        total=len(commands),
        category_count=len(_HELP_CATEGORIES),
    ))

    # Category index table
    out.append("| Category | Count | Commands |")
    out.append("|---|---|---|")
    for cat, cmds in _HELP_CATEGORIES.items():
        sample = ", ".join(f"`{c}`" for c in cmds[:3])
        if len(cmds) > 3:
            sample += ", …"
        out.append(f"| [{cat}](#{cat}) | {len(cmds)} | {sample} |")
    out.append("")
    out.append("---")
    out.append("")

    # Per-category sections
    rendered: set[str] = set()
    for cat, cmds in _HELP_CATEGORIES.items():
        out.append(f"## {cat}")
        out.append("")
        for cmd_name in cmds:
            cmd = commands.get(cmd_name)
            if cmd is None:
                out.append(f"### `empirica {cmd_name}`")
                out.append("")
                out.append("_Not currently wired through the main parser. "
                           "Either a planned command or a stale entry in "
                           "`_HELP_CATEGORIES`._")
                out.append("")
                continue
            rendered.add(cmd_name)
            out.append(_format_command_block(cmd))

        out.append("---")
        out.append("")

    # Uncategorized — commands the parser knows about but _HELP_CATEGORIES
    # doesn't list. Surface them so they're discoverable + flag for triage.
    uncategorized = sorted(set(commands.keys()) - rendered - {"help"})
    if uncategorized:
        out.append("## uncategorized")
        out.append("")
        out.append(
            "_These commands are registered in the parser but not yet listed in_ "
            "`_HELP_CATEGORIES` _in `empirica/cli/cli_core.py`. Add them to a_ "
            "_category to make them discoverable via_ `empirica help`."
        )
        out.append("")
        for cmd_name in uncategorized:
            out.append(_format_command_block(commands[cmd_name]))
        out.append("---")
        out.append("")

    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate CLI_COMMANDS_UNIFIED.md from live parsers",
    )
    parser.add_argument(
        "--output", "-o",
        default=str(_REPO_ROOT / "docs" / "human" / "developers"
                    / "CLI_COMMANDS_UNIFIED.md"),
        help="Output path (default: docs/human/developers/CLI_COMMANDS_UNIFIED.md)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print to stdout, don't write the file",
    )
    args = parser.parse_args()

    commands = collect_commands()
    text = render(commands)

    if args.dry_run:
        sys.stdout.write(text)
        return 0

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    print(f"Wrote {len(text)} bytes ({text.count(chr(10))} lines) to {out_path}")
    print(f"  Commands rendered: {len(commands)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
