"""Deprecated CLI commands and aliases: the notice before removal (goal cfc94d52). The list lives in
config/deprecated_commands.yaml; David approves what goes on it."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_FILE = Path(__file__).resolve().parent.parent / "config" / "deprecated_commands.yaml"


@lru_cache(maxsize=1)
def deprecated_commands() -> dict[str, dict]:
    """{name: entry} from the data file; empty when it is missing or unreadable (a notice is never worth a crash)."""
    try:
        import yaml

        data = yaml.safe_load(_FILE.read_text()) or {}
    except Exception:
        return {}
    return {str(e["name"]): e for e in data.get("commands") or [] if isinstance(e, dict) and e.get("name")}


def notice(command: str | None) -> str | None:
    """The line to print when ``command`` is deprecated, else None."""
    entry = deprecated_commands().get(command or "")
    if not entry:
        return None
    instead = f"; use `empirica {entry['replacement']}` instead" if entry.get("replacement") else ""
    return (
        f"⚠ `empirica {command}` is deprecated and goes away in {entry.get('remove_in', 'a coming release')}{instead}"
    )


@lru_cache(maxsize=1)
def deprecated_flags() -> list[dict]:
    """The ``flags:`` entries of the data file (goal 95c3ee16); empty when it is missing or unreadable."""
    try:
        import yaml

        data = yaml.safe_load(_FILE.read_text()) or {}
    except Exception:
        return []
    return [e for e in data.get("flags") or [] if isinstance(e, dict) and e.get("command") and e.get("flag")]


def flag_notices(command: str | None, argv: list[str], options: set[str] | None = None) -> list[str]:
    """One line for each deprecated flag of ``command`` used in ``argv``: ``--flag``, ``--flag=value``, or, given the
    command's ``options``, an abbreviation argparse resolved to it (a prefix that is not itself another option; 1.17.18
    sweep: `--chang` ran with no notice)."""
    tokens = [a.split("=", 1)[0] for a in argv if a.startswith("--")]
    lines = []
    for e in deprecated_flags():
        flag = str(e["flag"])
        if e["command"] != command:
            continue
        used = any(
            t == flag or (options is not None and len(t) > 2 and flag.startswith(t) and t not in options)
            for t in tokens
        )
        if not used:
            continue
        instead = f"; use {e['replacement']} instead" if e.get("replacement") else ""
        lines.append(
            f"⚠ `empirica {command} {flag}` is deprecated and goes away in {e.get('remove_in', 'a coming release')}{instead}"
        )
    return lines
