"""What Empirica gets used for, measured: the census behind /capability-review and `empirica efficiency-report` (goal cfc94d52).

David (2026-11-20): even the people who build Empirica forget what it provides, so a refresh needs evidence of what is used,
what to make more prominent, and what to retire. Read-only: Claude Code's session logs (``~/.claude/projects/**/*.jsonl``)
for the ``empirica <command>`` calls and Skill loads the AI made, the practice's store for artifacts logged, and the package
itself for what points at a command nobody ran.

Not a verdict. A command the AI never ran here may be called by a hook or a script, by another harness, on another machine,
or by a person in a terminal; ``limits`` says so with every result, and ``references`` lists the static callers of each unused
command so an assessment can tell "never used here" from "dead".
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

_SLASH = re.compile(r"<command-name>/([^<\s]+)</command-name>")
# After any boundary: start, a separator, whitespace (an env prefix or `timeout 30`), or an install path ending in bin/
# (`~/.local/bin/empirica`). Any other path is a directory: `git -C ~/repos/empirica status` is not an empirica call.
_COMMAND = re.compile(r"(?:^|[\s;&|(]|\$\(|/bin/)empirica\s+([a-z][a-z0-9-]+)")

#: Artifact type -> its table; counted by created_timestamp inside the window.
_ARTIFACT_TABLES = {
    "finding": "project_findings",
    "unknown": "project_unknowns",
    "dead_end": "project_dead_ends",
    "mistake": "mistakes_made",
    "assumption": "assumptions",
    "decision": "decisions",
    "goal": "goals",
}

LIMITS = (
    "Counts the empirica commands the AI ran and the skills loaded (by the AI, or typed as /name) in Claude Code on this machine. It does not see hooks or "
    "scripts calling Empirica directly, other harnesses (Codex, Desktop, the extension), other machines, or a person typing "
    "in a terminal, and it also counts a command named inside a heredoc or a commit message: an unused command is a "
    "candidate to look at, not proof that nothing needs it."
)


def cli_commands() -> dict[str, str]:
    """Every command name the CLI accepts -> the command it is (an alias maps to its command)."""
    from empirica.cli.cli_core import create_argument_parser

    parser = create_argument_parser()
    names: dict[str, str] = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            first_name: dict[int, str] = {}
            for name, sub in action.choices.items():  # the command is registered first, then its aliases
                names[name] = first_name.setdefault(id(sub), name)
    return names


def plugin_skills(root: Path | None = None) -> list[str]:
    """The skills the Claude Code plugin ships (a directory with a SKILL.md)."""
    root = root or Path(__file__).resolve().parent.parent / "plugins" / "claude-code-integration" / "skills"
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "SKILL.md").exists()) if root.exists() else []


def transcript_usage(projects_root: Path, since: float) -> dict[str, Any]:
    """Commands (aliases folded into their command) and skills the AI used in transcripts modified since ``since``."""
    names = cli_commands()
    commands: Counter = Counter()
    aliases: Counter = Counter()
    skills: Counter = Counter()
    transcripts = 0
    for path in Path(projects_root).rglob("*.jsonl"):
        try:
            if path.stat().st_mtime < since:
                continue
            handle = path.open(errors="ignore")
        except OSError:
            continue
        transcripts += 1
        with handle:
            for line in handle:
                if "<command-name>" in line and ('"type": "user"' in line or '"type":"user"' in line):
                    _count_slash_commands(line, skills)
                    continue
                if '"tool_use"' not in line:
                    continue
                try:
                    content = (json.loads(line).get("message") or {}).get("content") or []
                except (ValueError, AttributeError):
                    continue
                for block in content if isinstance(content, list) else []:
                    _count_tool_use(block, names, commands, aliases, skills)
    return {"transcripts": transcripts, "commands": commands, "aliases_used": aliases, "skills": skills}


def _count_tool_use(block, names: dict[str, str], commands: Counter, aliases: Counter, skills: Counter) -> None:
    """One tool_use block: a Skill load, or the empirica commands inside a Bash call (aliases folded into their command)."""
    if not isinstance(block, dict) or block.get("type") != "tool_use":
        return
    tool_input = block.get("input") or {}
    if block.get("name") == "Skill":
        skill = _empirica_skill(str(tool_input.get("skill", "")))
        if skill:
            skills[skill] += 1
    elif block.get("name") == "Bash":
        for match in _COMMAND.finditer(str(tool_input.get("command", ""))):
            name = match.group(1)
            if name in names:
                commands[names[name]] += 1
                if names[name] != name:
                    aliases[name] += 1


def _empirica_skill(name: str) -> str | None:
    """'empirica:forecast' or 'forecast' -> 'forecast'; another plugin's same-named skill ('anthropic-skills:x') -> None."""
    plugin, _, skill = name.rpartition(":")
    return skill if plugin in ("", "empirica") and skill else None


def _count_slash_commands(line: str, skills: Counter) -> None:
    """A person typing /name records it in a user message as <command-name>/name</command-name>: a skill load the Skill tool
    never sees (1.17.14 sweep). Built-in commands (/compact, /model) are counted too and dropped later: only shipped skill
    names are reported."""
    try:
        record = json.loads(line)
    except ValueError:
        return
    if record.get("type") != "user":
        return
    content = (record.get("message") or {}).get("content")
    texts = [content] if isinstance(content, str) else [b.get("text", "") for b in content or [] if isinstance(b, dict)]
    for text in texts:
        for name in _SLASH.findall(str(text)):
            skill = _empirica_skill(name)
            if skill:
                skills[skill] += 1


def artifact_counts(conn, since: float) -> dict[str, int]:
    """Artifacts logged inside the window, per type; a table this store lacks is left out."""
    out: dict[str, int] = {}
    for kind, table in _ARTIFACT_TABLES.items():
        try:
            out[kind] = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE created_timestamp >= ?", (since,)).fetchone()[
                0
            ]
        except Exception:
            continue
    return out


def static_references(commands: list[str], package_root: Path | None = None) -> dict[str, list[str]]:
    """For each command, the files in the installed package that tell someone or something to run ``empirica <command>``:
    hooks, skills, prompts, scripts. One pass over the package. The parsers and the dispatcher only register commands, so
    they are not callers."""
    root = package_root or Path(__file__).resolve().parent.parent
    # Not followed by a word character or a hyphen: `empirica check-submit` is not a reference to `check`.
    patterns = {c: re.compile(rf"empirica\s+{re.escape(c)}(?![\w-])") for c in commands}
    hits: dict[str, list[str]] = {c: [] for c in commands}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".md", ".sh", ".yaml", ".yml", ".json"):
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(("cli/parsers/", "cli/cli_core.py")) or "__pycache__" in rel:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if "empirica" not in text:
            continue
        for command, pattern in patterns.items():
            if pattern.search(text):
                hits[command].append(rel)
    return hits


def census(*, days: int = 30, projects_root: Path | None = None, conn=None) -> dict[str, Any]:
    """The whole census for a window of ``days``."""
    since = time.time() - days * 86400
    usage = transcript_usage(projects_root or Path.home() / ".claude" / "projects", since)
    names = cli_commands()
    primary = sorted(set(names.values()))
    alias_names = sorted(n for n, p in names.items() if n != p)
    used = usage["commands"]
    unused = [c for c in primary if not used.get(c)]
    unused_aliases = [a for a in alias_names if not usage["aliases_used"].get(a)]
    references = static_references([*unused, *unused_aliases])
    shipped_skills = plugin_skills()
    return {
        "window_days": days,
        "transcripts": usage["transcripts"],
        "commands": {
            "total": len(primary),
            "used": {c: used[c] for c in sorted(used, key=lambda c: -used[c])},
            "unused": unused,
            "rare": sorted(c for c in primary if 0 < used.get(c, 0) <= 3),
            "references": {c: references[c] for c in unused},
        },
        "aliases": {
            "total": len(alias_names),
            "used": dict(usage["aliases_used"]),
            "unused": unused_aliases,
            "references": {a: references[a] for a in unused_aliases},
        },
        "skills": {
            "shipped": len(shipped_skills),
            "loaded": {s: usage["skills"][s] for s in shipped_skills if usage["skills"].get(s)},
            "never_loaded": [s for s in shipped_skills if not usage["skills"].get(s)],
        },
        "artifacts": artifact_counts(conn, since) if conn is not None else {},
        "limits": LIMITS,
    }
