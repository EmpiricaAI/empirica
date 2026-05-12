"""Daemon project registry (v1.10.0).

Maintains ~/.empirica/registry.yaml — the list of projects the local `empirica
serve` daemon is willing to serve. Companion to the existing
`~/.empirica/discovered_projects.yaml` manifest (produced by
`empirica projects-discover`), but with a different role:

  discovered_projects.yaml  → scanner output (what's on disk)
  registry.yaml             → daemon's served set (subset, opt-in)

Schema (version 1):

  version: 1
  projects:
    - project_id: <Cortex UUID OR local slug>
      slug: <directory-style slug>
      name: <display name>
      path: <absolute filesystem path>
      repo_url: <https://... or null>
      last_seen: <RFC3339 timestamp or null>

Writes are atomic (tempfile + rename) so a partial write never corrupts the
file. Reads are lenient — bad shape or missing file returns an empty registry
rather than raising.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

REGISTRY_VERSION = 1
DEFAULT_REGISTRY_PATH = Path.home() / ".empirica" / "registry.yaml"


def load_registry(path: Path | None = None) -> dict[str, Any]:
    """Load the registry. Returns an empty registry if missing or unparseable.

    Empty shape: {"version": 1, "projects": []}.
    """
    p = path or DEFAULT_REGISTRY_PATH
    if not p.exists():
        return {"version": REGISTRY_VERSION, "projects": []}

    try:
        content = p.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
    except (OSError, yaml.YAMLError) as e:
        logger.warning(f"registry: failed to read {p}: {e}")
        return {"version": REGISTRY_VERSION, "projects": []}

    if not isinstance(data, dict):
        return {"version": REGISTRY_VERSION, "projects": []}

    raw_projects = data.get("projects")
    projects = raw_projects if isinstance(raw_projects, list) else []
    return {
        "version": data.get("version", REGISTRY_VERSION),
        "projects": [entry for entry in projects if isinstance(entry, dict)],
    }


def save_registry(registry: dict[str, Any], path: Path | None = None) -> None:
    """Write the registry atomically (tempfile + rename).

    Creates parent directory if missing.
    """
    p = path or DEFAULT_REGISTRY_PATH
    p.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": registry.get("version", REGISTRY_VERSION),
        "projects": registry.get("projects", []),
    }

    # Atomic write: tempfile in same dir → rename.
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=".registry-", suffix=".yaml.tmp", dir=str(p.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            yaml.dump(payload, f, default_flow_style=False, sort_keys=False,
                      allow_unicode=True)
        os.replace(tmp_path, p)
    except Exception:
        # Clean up tempfile on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def find_by_project_id(registry: dict[str, Any], project_id: str) -> dict[str, Any] | None:
    """Find a registry entry by project_id. Returns None if not found."""
    for entry in registry.get("projects", []):
        if entry.get("project_id") == project_id:
            return entry
    return None


def upsert_project(
    registry: dict[str, Any],
    *,
    project_id: str,
    slug: str,
    name: str,
    path: str,
    repo_url: str | None = None,
    last_seen: str | None = None,
) -> dict[str, Any]:
    """Insert or update a registry entry. Returns the modified registry.

    Matches on project_id. If project_id already present, updates the entry
    in place. Updates `last_seen` to now() if not provided.
    """
    entry = {
        "project_id": project_id,
        "slug": slug,
        "name": name,
        "path": path,
        "repo_url": repo_url,
        "last_seen": last_seen or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    projects = registry.setdefault("projects", [])
    for i, existing in enumerate(projects):
        if existing.get("project_id") == project_id:
            projects[i] = entry
            return registry

    projects.append(entry)
    return registry


def prune_stale(registry: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Remove entries whose path no longer exists. Returns (registry, removed).

    "Stale" = `path` is missing OR `.empirica/` subdirectory is missing.
    Follows symlinks (matches daemon resolver behavior).
    """
    keep: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []

    for entry in registry.get("projects", []):
        raw_path = entry.get("path")
        if not raw_path:
            removed.append(entry)
            continue
        try:
            resolved = Path(raw_path).resolve(strict=False)
        except (OSError, RuntimeError):
            removed.append(entry)
            continue
        if not resolved.exists() or not (resolved / ".empirica").is_dir():
            removed.append(entry)
            continue
        keep.append(entry)

    registry["projects"] = keep
    return registry, removed


def list_known_projects(path: Path | None = None) -> list[dict[str, Any]]:
    """Return the registry's projects as a list of dicts.

    Convenience for the /api/v1/health endpoint. Returns [] if the registry
    file is missing — callers must handle empty list as "no multi-project
    setup, single-project mode active".
    """
    return load_registry(path).get("projects", [])
