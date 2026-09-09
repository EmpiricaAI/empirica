"""Undo what `setup-claude-code` wrote — plan first, and never clobber.

Setup writes to eleven locations, SIX of them inside files Claude Code owns and
writes continuously. That asymmetry is the whole design here:

    ours to delete      the plugin dir, our prompt file, our marketplace entry
    theirs to edit      settings.json, ~/.claude.json, the plugin registries —
                        we remove OUR keys and touch nothing else
    theirs to keep      the `@include` line in the user's own CLAUDE.md, which is
                        REPORTED and never removed

**A delete has no merge to soften it.** Install could merge its entry into
whatever it found; uninstall removes, and a read-modify-write that loses a
concurrent write while deleting has no recoverable half. So every write goes
through the stamped path shipped in 1.13.40: read with an (mtime_ns, size) stamp,
re-stat before renaming, refuse on change. Non-owner yields.

Plan is the default. `--apply` writes. The plan names every location rather than
counting them, because an operator who cannot read what would be removed cannot
disagree with it — and this is the one command where disagreeing matters most.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from empirica.cli.command_handlers.setup_claude_code import (
    PLUGIN_NAME,
    ConcurrentlyModified,
    _read_json_with_stamp,
    _write_json_file,
)

#: The line setup appends to the user's own CLAUDE.md. Matched to REPORT, never
#: to remove — see `plan_uninstall`.
INCLUDE_LINE = "@~/.claude/empirica-system-prompt.md"


def _claude_paths(home: Path) -> dict[str, Path]:
    claude = home / ".claude"
    return {
        "plugin_dir": claude / "plugins" / "local" / PLUGIN_NAME,
        "settings": claude / "settings.json",
        "claude_json": home / ".claude.json",
        "legacy_mcp": claude / "mcp.json",
        "installed_plugins": claude / "plugins" / "installed_plugins.json",
        "known_marketplaces": claude / "plugins" / "known_marketplaces.json",
        "marketplace": claude / "plugins" / ".claude-plugin" / "marketplace.json",
        "system_prompt": claude / "empirica-system-prompt.md",
        "claude_md": claude / "CLAUDE.md",
        "active_work": home / ".empirica" / "active_work.json",
    }


def _json_keys_we_own(path: Path) -> list[str] | None:
    """Which of OUR keys are in a shared JSON file, or None if it cannot be read.

    None is not an empty list. Returning `[]` for an unparseable file would
    classify it as "nothing of ours here" and drop it from the plan silently —
    the absent-vs-corrupt conflation that destroyed ~/.claude.json in 1.13.39,
    reappearing in the code written to clean up after it. Caught by its own test.
    """
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    found = []
    if isinstance(data.get("mcpServers"), dict) and PLUGIN_NAME in data["mcpServers"]:
        found.append(f"mcpServers.{PLUGIN_NAME}")
    if isinstance(data.get("enabledPlugins"), dict):
        found += [f"enabledPlugins.{k}" for k in data["enabledPlugins"] if PLUGIN_NAME in k]
    if isinstance(data.get("plugins"), dict) and PLUGIN_NAME in data["plugins"]:
        found.append(f"plugins.{PLUGIN_NAME}")
    if isinstance(data.get("statusLine"), dict) and PLUGIN_NAME in json.dumps(data["statusLine"]):
        found.append("statusLine")
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for event, entries in hooks.items():
            if isinstance(entries, list) and any(PLUGIN_NAME in json.dumps(e) for e in entries):
                found.append(f"hooks.{event}")
    for k in list(data.keys()):
        if PLUGIN_NAME in k.lower() and k not in ("mcpServers", "enabledPlugins", "plugins", "hooks"):
            found.append(k)
    return sorted(set(found))


def plan_uninstall(home: Path | None = None) -> dict:
    """What uninstall WOULD do. Pure read — nothing is removed.

    Three categories, deliberately named differently, because the right action
    differs and collapsing them is how an uninstaller eats someone's config.
    """
    home = home or Path.home()
    p = _claude_paths(home)
    out: dict = {"remove_ours": [], "edit_shared": [], "report_only": [], "absent": [], "unreadable": []}

    for label in ("plugin_dir", "system_prompt", "active_work"):
        path = p[label]
        (out["remove_ours"] if path.exists() else out["absent"]).append(str(path))

    for label in ("settings", "claude_json", "legacy_mcp", "installed_plugins", "known_marketplaces", "marketplace"):
        path = p[label]
        if not path.exists():
            out["absent"].append(str(path))
            continue
        keys = _json_keys_we_own(path)
        if keys is None:
            # Exists and will not parse. NOT "no keys of ours" — we do not know
            # what is in it, and the next step would rewrite it minus keys we
            # never saw.
            out["unreadable"].append(str(path))
        elif keys:
            out["edit_shared"].append({"path": str(path), "remove_keys": keys})
        else:
            out["absent"].append(f"{path} (no empirica keys)")

    # The user's OWN file. We appended a line to it; we do not take it back.
    #
    # If they have reorganised around it, pattern-matching "our" line and cutting
    # it is a judgement we are not positioned to make — and unlike every other
    # entry here, getting it wrong damages something they wrote. Report the
    # location and let a human delete it.
    cmd = p["claude_md"]
    if cmd.exists():
        try:
            lines = cmd.read_text().splitlines()
            hits = [i + 1 for i, ln in enumerate(lines) if INCLUDE_LINE in ln]
        except OSError:
            hits = []
        if hits:
            out["report_only"].append(
                {
                    "path": str(cmd),
                    "lines": hits,
                    "content": INCLUDE_LINE,
                    "why": (
                        "this is YOUR file — setup appended one line to it. Uninstall will not edit "
                        "it, because if you have reorganised around that line we cannot tell which "
                        "edit is safe. Delete it yourself if you want it gone."
                    ),
                }
            )

    out["totals"] = {
        "remove": len(out["remove_ours"]),
        "edit": len(out["edit_shared"]),
        "report_only": len(out["report_only"]),
        "unreadable": len(out["unreadable"]),
    }
    return out


def _strip_keys(data: dict, keys: list[str]) -> dict:
    """Remove exactly what is ours. Nothing else, at any depth.

    `hooks.<Event>` is the case that needs care and did not get it first time.
    An event key holds a LIST, and a user can register their own hooks under the
    same event as ours — so popping `hooks.PostToolUse` wholesale deletes theirs
    along with ours. Measured on a real home: 12 hook events, any of which a user
    may share.

    So hook events are FILTERED, not popped: entries mentioning empirica go, the
    rest stay, and the event key itself survives if anything is left. Same rule as
    everywhere else here — strip ours, touch nothing else — applied one level
    deeper than the dotted-key form expresses.
    """
    for key in keys:
        if key.startswith("hooks."):
            event = key.split(".", 1)[1]
            hooks = data.get("hooks")
            if not isinstance(hooks, dict) or not isinstance(hooks.get(event), list):
                continue
            kept = [e for e in hooks[event] if PLUGIN_NAME not in json.dumps(e)]
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event, None)
            if not hooks:
                data.pop("hooks", None)
        elif "." in key:
            parent, child = key.split(".", 1)
            block = data.get(parent)
            if isinstance(block, dict):
                block.pop(child, None)
                if not block:
                    data.pop(parent, None)
        else:
            data.pop(key, None)
    return data


def apply_uninstall(home: Path | None = None, the_plan: dict | None = None) -> dict:
    """Execute a plan. Reports what LANDED and what was refused, separately."""
    home = home or Path.home()
    the_plan = the_plan or plan_uninstall(home)
    receipt: dict = {"removed": [], "edited": [], "refused": [], "left_for_you": the_plan["report_only"]}

    for path_str in the_plan.get("unreadable", []):
        receipt["refused"].append(f"{path_str}: unreadable — NOT modified, contents unknown")

    for path_str in the_plan["remove_ours"]:
        path = Path(path_str)
        try:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
            receipt["removed"].append(path_str)
        except OSError as e:
            receipt["refused"].append(f"{path_str}: {type(e).__name__}: {e}")

    for entry in the_plan["edit_shared"]:
        path = Path(entry["path"])
        try:
            data, stamp = _read_json_with_stamp(path, {})
        except (OSError, ValueError) as e:
            # Unparseable and shared. Refuse — the alternative is writing a file
            # whose contents we never read, while deleting.
            receipt["refused"].append(f"{entry['path']}: unreadable ({type(e).__name__}) — NOT modified")
            continue
        _strip_keys(data, entry["remove_keys"])
        try:
            _write_json_file(path, data, expect_stamp=stamp)
            receipt["edited"].append({"path": entry["path"], "removed_keys": entry["remove_keys"]})
        except ConcurrentlyModified as e:
            receipt["refused"].append(f"{entry['path']}: {e}")
    return receipt
