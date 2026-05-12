"""Daemon active-project resolver.

Resolves which Empirica project the `empirica serve` daemon is bound to. Held
for the daemon's process lifetime — user restarts the daemon to switch projects
(matches existing project-switch CLI semantics for transient commands; daemon's
long-running nature makes per-call re-resolution unnecessary).

Resolution chain (high-to-low precedence):
  1. **`InstanceResolver.project_path()`** — the canonical resolver. Picks up
     instance_projects/{instance_id}.json (P0), active_work_{uuid}.json (P1),
     headless active_work.json (P2). This handles the case where the daemon
     is launched in a tmux pane sibling to an active CC instance, or in a
     terminal where project-switch has set context.
  2. **CWD walk-up** for `.empirica/project.yaml` — daemon-specific tail for
     the case "user launches `empirica serve` in a project tree without any
     active CC context" (e.g., fresh terminal, headless launch). The canonical
     resolver intentionally fails-fast on this case because guessing CWD for
     transient CLI commands risks polluting other instances; for a daemon
     bound to one project, walking up from CWD is correct.
  3. **None** — daemon starts but per-project endpoints return 503.

Per docs/architecture/instance_isolation/: *NOT* adding a competing resolver
chain. Step 1 IS the canonical chain; step 2 is the daemon-specific tail
that canonical fails-fast on by design.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def _walk_up_for_empirica(start: Path, max_depth: int = 20) -> Path | None:
    """Walk up from `start` looking for a `.empirica/project.yaml` marker.

    Matches `git`'s `.git` discovery pattern. Caps at `max_depth` to avoid
    chasing symlink loops or pathological filesystems.
    """
    cur = start.resolve()
    for _ in range(max_depth):
        if (cur / ".empirica" / "project.yaml").is_file():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def _read_project_yaml(project_path: Path) -> dict:
    """Read and parse .empirica/project.yaml. Returns {} on any error."""
    try:
        content = (project_path / ".empirica" / "project.yaml").read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError) as e:
        logger.debug(f"daemon_project: failed to read project.yaml: {e}")
        return {}


def _read_git_remote(project_path: Path) -> str | None:
    """Best-effort `git remote get-url origin`. Returns None on any miss."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        logger.debug(f"daemon_project: git remote read failed: {e}")
        return None
    if result.returncode != 0:
        return None
    raw = result.stdout.strip()
    if not raw:
        return None
    # Normalize ssh-form to https-form (mirrors projects_commands._normalize_remote_url)
    if raw.startswith("git@"):
        # git@host:owner/repo(.git)? → https://host/owner/repo
        try:
            host_part, path_part = raw[4:].split(":", 1)
            if path_part.endswith(".git"):
                path_part = path_part[:-4]
            return f"https://{host_part}/{path_part}"
        except ValueError:
            return raw
    if raw.endswith(".git"):
        raw = raw[:-4]
    return raw


def _slugify_project_name(name: str) -> str:
    """Normalize a project name into a wire slug.

    Lowercase, alphanumerics + hyphens, runs of non-allowed → single hyphen,
    trim leading/trailing hyphens. Stable across machines for the same name.
    """
    import re
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or name.lower()


def resolve_daemon_project() -> dict | None:
    """Resolve the daemon's active project.

    Returns a dict with keys: project_id, project_path, project_name,
    project_slug, repo_url. Returns None if neither canonical resolver
    nor CWD walk-up finds a project.

    project_id may be None even when the project resolves (local-only project
    not registered on Cortex). project_name and project_slug are always set
    when resolution succeeds. repo_url is None if no git remote.
    """
    project_path: Path | None = None

    # 1. Canonical resolver (instance_projects → active_work_{uuid} → headless)
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        canonical = R.project_path()
        if canonical:
            candidate = Path(canonical)
            if (candidate / ".empirica" / "project.yaml").is_file():
                project_path = candidate
                logger.debug(f"daemon_project: resolved via InstanceResolver: {project_path}")
    except Exception as e:
        logger.debug(f"daemon_project: InstanceResolver failed: {e}")

    # 2. CWD walk-up tail (for "no CC context" daemon launches)
    if project_path is None:
        cwd = Path(os.environ.get("PWD") or os.getcwd())
        walked = _walk_up_for_empirica(cwd)
        if walked:
            project_path = walked
            logger.debug(f"daemon_project: resolved via CWD walk-up: {project_path}")

    if project_path is None:
        return None

    # Read project.yaml — provides display name + slug-like project_id field.
    # In long-lived projects, yaml's `project_id` is typically a human-readable
    # slug (e.g. "empirica") that matches `projects.name`, NOT the canonical
    # `projects.id` UUID used as the foreign key in artifact tables.
    project_yaml = _read_project_yaml(project_path)
    project_name = project_yaml.get("display_name") or project_yaml.get("name") or project_path.name
    yaml_id = project_yaml.get("project_id")  # slug for old projects, UUID for new

    # Canonical UUID resolution. Two-step lookup:
    #   1. If yaml_id looks UUID-shaped → trust it.
    #   2. Otherwise treat yaml_id as a slug and look up projects.id WHERE name=?
    project_uuid = _resolve_project_uuid(project_path, yaml_id)

    project_id = project_uuid or yaml_id  # UUID preferred; fall back to whatever yaml had
    project_slug = _slugify_project_name(yaml_id) if (yaml_id and not _looks_uuid(yaml_id)) else _slugify_project_name(project_name)
    repo_url = _read_git_remote(project_path)

    return {
        "project_id": project_id,
        "project_path": str(project_path),
        "project_name": project_name,
        "project_slug": project_slug,
        "repo_url": repo_url,
    }


def _looks_uuid(value: str | None) -> bool:
    """Cheap UUID-shape check (8-4-4-4-12 hex). Doesn't validate, just shape."""
    if not value or not isinstance(value, str):
        return False
    parts = value.split("-")
    return (
        len(parts) == 5
        and len(parts[0]) == 8 and len(parts[1]) == 4 and len(parts[2]) == 4
        and len(parts[3]) == 4 and len(parts[4]) == 12
    )


def _resolve_project_uuid(project_path: Path, yaml_id: str | None) -> str | None:
    """Get the canonical UUID for the project.

    If yaml_id is UUID-shaped, return it as-is. Otherwise treat it as a slug
    and look up `projects.id WHERE name = ?` in the local sqlite. Falls back
    to None if no match — caller treats that as a local-only project.
    """
    if yaml_id and _looks_uuid(yaml_id):
        return yaml_id

    db_path = project_path / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        return None

    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        # Try by name (yaml_id as slug)
        if yaml_id:
            cursor.execute("SELECT id FROM projects WHERE name = ? LIMIT 1", (yaml_id,))
            row = cursor.fetchone()
            if row:
                conn.close()
                return row[0]
        # Try by folder name as a last resort
        folder_name = project_path.name
        cursor.execute("SELECT id FROM projects WHERE name = ? LIMIT 1", (folder_name,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        logger.debug(f"_resolve_project_uuid: lookup failed: {e}")
        return None


# Process-lifetime cache. The daemon holds one project for its lifetime;
# user restarts to switch.
_cached_project: dict | None = None
_cached: bool = False


def get_cached_daemon_project(refresh: bool = False) -> dict | None:
    """Return the daemon's active project, resolving once and caching.

    Pass refresh=True to force re-resolution (used in tests).
    """
    global _cached_project, _cached
    if refresh or not _cached:
        _cached_project = resolve_daemon_project()
        _cached = True
    return _cached_project


# ── v1.10.0 multi-project resolution ───────────────────────────────────


def resolve_for_request(
    project_id: str | None = None,
    project_path_override: str | None = None,
) -> dict | None:
    """Resolve the project for a single request.

    Precedence (high → low):
      1. `project_path_override` (`?path=Y`) — power-user bypass. Walks up
         from Y looking for `.empirica/project.yaml`. No registry lookup.
         Returns None if Y has no `.empirica/` subdirectory.
      2. `project_id` (`?project_id=X`) — registry lookup. Returns None if
         X is not registered; caller maps None → 404.
      3. Neither — falls back to `get_cached_daemon_project()` (current
         CWD-bound behavior; backward-compat).

    Symlinks are followed (Path.resolve in both walk-up and path-override).
    """
    if project_path_override:
        candidate = Path(project_path_override).resolve(strict=False)
        if not (candidate / ".empirica" / "project.yaml").is_file():
            walked = _walk_up_for_empirica(candidate)
            if walked is None:
                return None
            candidate = walked
        return _synthesize_project_entry(candidate)

    if project_id:
        from empirica.api.registry import find_by_project_id, load_registry
        entry = find_by_project_id(load_registry(), project_id)
        if not entry:
            return None
        candidate = Path(entry["path"]).resolve(strict=False)
        if not (candidate / ".empirica").is_dir():
            # Registry entry points at a path that no longer has .empirica/.
            # Don't synthesize — caller maps None → 404 so the user knows
            # the registry is stale and can run `projects-discover
            # --register --prune`.
            return None
        return {
            "project_id": entry.get("project_id"),
            "project_path": str(candidate),
            "project_name": entry.get("name") or candidate.name,
            "project_slug": entry.get("slug") or candidate.name,
            "repo_url": entry.get("repo_url"),
        }

    return get_cached_daemon_project()


def _synthesize_project_entry(project_path: Path) -> dict:
    """Build a project dict for a path that may not be in the registry.

    Mirrors `resolve_daemon_project()` shape so callers can use the result
    interchangeably with the cached CWD-bound resolver.
    """
    project_yaml = _read_project_yaml(project_path)
    project_name = (
        project_yaml.get("display_name")
        or project_yaml.get("name")
        or project_path.name
    )
    yaml_id = project_yaml.get("project_id")
    project_uuid = _resolve_project_uuid(project_path, yaml_id)
    project_id = project_uuid or yaml_id
    project_slug = (
        _slugify_project_name(yaml_id)
        if (yaml_id and not _looks_uuid(yaml_id))
        else _slugify_project_name(project_name)
    )
    return {
        "project_id": project_id,
        "project_path": str(project_path),
        "project_name": project_name,
        "project_slug": project_slug,
        "repo_url": _read_git_remote(project_path),
    }
