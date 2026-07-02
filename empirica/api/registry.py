"""Daemon project registry (v1.9.6).

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
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

REGISTRY_VERSION = 1
DEFAULT_REGISTRY_PATH = Path.home() / ".empirica" / "registry.yaml"


def load_registry(path: Path | None = None, *, auto_dedupe: bool = True) -> dict[str, Any]:
    """Load the registry. Returns an empty registry if missing or unparseable.

    Empty shape: {"version": 1, "projects": []}.

    Auto-dedupe: when two entries share the same filesystem `path`, prefer the
    canonical-UUID-keyed entry over the legacy-slug-keyed entry and rewrite
    the registry. Same-path duplication comes from registrations across the
    legacy slug-as-id era and the canonical UUID-as-id era both surviving in
    the file. Disable with ``auto_dedupe=False`` for test paths that want to
    inspect raw state.
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
    registry: dict[str, Any] = {
        "version": data.get("version", REGISTRY_VERSION),
        "projects": [entry for entry in projects if isinstance(entry, dict)],
    }

    if auto_dedupe:
        deduped, removed = dedupe_registry(registry)
        if removed:
            logger.info(
                f"registry: auto-deduped {len(removed)} legacy slug-keyed "
                f"entries (canonical UUID-keyed wins on same-path collision)"
            )
            registry = deduped
            try:
                save_registry(registry, path=p)
            except Exception as e:
                # Non-fatal: in-memory dedup still applied; persistence retried
                # on next mutating verb. Don't let a disk write failure poison
                # the daemon read path.
                logger.warning(f"registry: dedup persist failed (non-fatal): {e}")

    return registry


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
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".registry-", suffix=".yaml.tmp", dir=str(p.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            yaml.dump(payload, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
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


# Loose UUID matcher — accepts canonical 8-4-4-4-12 hex form. Anything else
# (slug, name, or partial id) is treated as a legacy non-UUID key.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _is_canonical_uuid(value: Any) -> bool:
    """True iff value is a string matching the canonical UUID form."""
    return isinstance(value, str) and bool(_UUID_RE.match(value))


def dedupe_registry(
    registry: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Collapse same-path dual-key duplication. Returns (deduped, removed).

    Two entries sharing the same filesystem ``path`` are a legacy artifact
    from the era when ``project_id`` was a slug; modern entries use the
    canonical UUID. Per the SER ser_542199e3 canonical-identity lock
    (UUID-internal / 3-form-wire / name-display), UUID-keyed entries WIN.

    Behaviour per same-path group:
      - Exactly one UUID-keyed entry → keep it, drop all others. Removed
        entries' identifying fields (project_id, slug, path) are returned so
        callers can log what changed.
      - Multiple UUID-keyed entries → genuine conflict (two real projects
        sharing one path? unlikely but possible). Keep both; logged warning.
        Same path with multiple UUIDs is a state we shouldn't try to resolve
        silently — surfacing is safer than guessing.
      - Zero UUID-keyed entries → all-legacy. Keep the most recently seen
        (by ``last_seen``) entry; drop the others. Best-effort.

    Entries with no ``path`` field are passed through untouched — they're
    malformed but not the dedup target.
    """
    projects = list(registry.get("projects", []))
    if not projects:
        return registry, []

    by_path: dict[str, list[dict[str, Any]]] = {}
    pathless: list[dict[str, Any]] = []
    for entry in projects:
        raw_path = entry.get("path")
        if not raw_path or not isinstance(raw_path, str):
            pathless.append(entry)
            continue
        by_path.setdefault(raw_path, []).append(entry)

    kept: list[dict[str, Any]] = list(pathless)
    removed: list[dict[str, Any]] = []
    for raw_path, group in by_path.items():
        if len(group) == 1:
            kept.append(group[0])
            continue

        uuid_keyed = [e for e in group if _is_canonical_uuid(e.get("project_id"))]
        legacy = [e for e in group if not _is_canonical_uuid(e.get("project_id"))]

        if len(uuid_keyed) == 1:
            kept.append(uuid_keyed[0])
            removed.extend(legacy)
            continue

        if len(uuid_keyed) > 1:
            logger.warning(
                f"registry: dedup skipping conflict at {raw_path!r} — "
                f"{len(uuid_keyed)} UUID-keyed entries; not auto-resolving"
            )
            kept.extend(group)
            continue

        # Zero UUID-keyed entries — all legacy. Keep most recently seen.
        survivor = max(group, key=lambda e: e.get("last_seen") or "")
        kept.append(survivor)
        for e in group:
            if e is not survivor:
                removed.append(e)
                logger.info(
                    f"registry: legacy-only group at {raw_path!r}, kept {survivor.get('project_id')!r} by last_seen"
                )

    deduped = {
        "version": registry.get("version", REGISTRY_VERSION),
        "projects": kept,
    }
    return deduped, removed


def _resolve_path_key(path: Any) -> str | None:
    """Symlink-following normalized path key (matches prune_stale / the daemon
    resolver). Returns None for empty/unresolvable paths so they never match."""
    if not path or not isinstance(path, str):
        return None
    try:
        return str(Path(path).resolve(strict=False))
    except (OSError, RuntimeError):
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

    Matches on the resolved realpath of ``path`` FIRST, then on ``project_id``.
    Path-first is the fix for registry.yaml drift: a re-keyed project (same
    path, NEW project_id — e.g. a slug→canonical-UUID promotion, or a re-clone)
    must UPDATE its existing entry in place, not append a fresh-UUID duplicate.
    Appending created two entries at one path; when both are UUID-keyed,
    ``dedupe_registry`` refuses to auto-resolve → per-request "dedup skipping
    conflict" spam on every daemon request (surfaced by mesh-support on Philipp's
    box). Canonical-identity guard: a same-path match never DOWNGRADES an
    existing canonical UUID to a slug (UUID-keyed wins, per ser_542199e3).
    Updates ``last_seen`` to now() if not provided.
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
    target_key = _resolve_path_key(path)
    for i, existing in enumerate(projects):
        same_path = target_key is not None and _resolve_path_key(existing.get("path")) == target_key
        if same_path or existing.get("project_id") == project_id:
            # Don't let a same-path re-sync clobber a canonical UUID with a slug.
            if same_path and _is_canonical_uuid(existing.get("project_id")) and not _is_canonical_uuid(project_id):
                entry["project_id"] = existing["project_id"]
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
