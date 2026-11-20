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
