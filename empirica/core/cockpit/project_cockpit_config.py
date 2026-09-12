"""Read cockpit.loops + cockpit.listeners from a project's
.empirica/project.yaml.

This is the per-project canonical config that the TUI consults when
clicking L (loops) or E (listeners) on an instance whose registry is
currently empty — register + install in one motion. After the first
install the registry is the source of truth; subsequent clicks just
toggle pause/resume via the existing handlers.

Schema (under the top-level `cockpit:` key — namespaced to keep
project.yaml's surface tidy as more cockpit features land):

    cockpit:
      loops:
        - name: outreach-inbox-poll      # required
          kind: cron                      # cron | interval | monitor
          cron: "8,23,38,53 * * * *"      # for kind=cron
          interval: "15m"                  # for kind=interval (or as base for cron+backoff)
          description: "Outreach Claude self-poll"
          base_interval: "15m"             # optional backoff floor
          max_interval: "4h"               # optional backoff ceiling
      listeners:
        - name: outreach-inbox            # required
          topic: ntfy:outreach-claude-inbox  # required, scheme:rest
          description: "Cortex orchestration inbox"
          on_wake: "Process new orchestration message"

Missing file / unreadable yaml / missing key → empty list. The TUI
falls back to the install-request CLI hint when nothing is configured.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def project_yaml_path(project_path: str | Path) -> Path:
    return Path(project_path) / ".empirica" / "project.yaml"


def _load_cockpit_block(project_path: str | Path | None) -> dict[str, Any]:
    """Read .empirica/project.yaml and return the cockpit: dict.

    Returns {} on any failure (no project_path, missing file, parse error,
    no cockpit key). Intentionally non-raising — callers want a clean
    list-or-empty contract, not exceptions to handle.
    """
    from empirica.utils.yaml_cache import load_yaml_cached

    if not project_path:
        return {}
    cockpit = load_yaml_cached(project_yaml_path(project_path)).get("cockpit")
    if not isinstance(cockpit, dict):
        return {}
    return cockpit


def project_loops(project_path: str | Path | None) -> list[dict[str, Any]]:
    """Return cockpit.loops — list of loop config dicts. Skips entries
    that don't have a 'name' field (basic shape validation)."""
    cockpit = _load_cockpit_block(project_path)
    raw = cockpit.get("loops")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("name")]


def project_listeners(project_path: str | Path | None) -> list[dict[str, Any]]:
    """Return cockpit.listeners — list of listener config dicts. Skips
    entries that don't have both 'name' and 'topic' fields."""
    cockpit = _load_cockpit_block(project_path)
    raw = cockpit.get("listeners")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("name") and item.get("topic")]


def transcript_reading_enabled(project_path: str | Path | None) -> bool:
    """Whether this practice permits its Claude Code transcript to be read.

    Governs the cockpit's model/effort columns, which are sourced from the
    seat's own transcript. **Defaults to False**, and the flag is read from
    the project whose transcript would be opened — not from the operator's
    project — so consent is granted by the subject rather than on its behalf
    (David's ruling, 2026-09-11: acceptable in the open-source core, but
    opt-in, so nothing reads conversation logs without having been asked).

        cockpit:
          read_transcripts: true

    Any non-``True`` value, a missing key, an unreadable file or a
    non-boolean leaves it off — an ambiguous config must not resolve to MORE
    access than no config at all.
    """
    return _load_cockpit_block(project_path).get("read_transcripts") is True


__all__ = [
    "project_listeners",
    "project_loops",
    "project_yaml_path",
    "transcript_reading_enabled",
]
