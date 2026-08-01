"""Plugin / MCP manifest discovery — paths and registered servers.

Reads filesystem paths and the JSON list of registered MCP servers. Never
inspects the inner traffic of any MCP server, just registration data.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# `~/.claude.json` FIRST — it is the file Claude Code actually maintains, and it
# carries per-project `projects[<cwd>].mcpServers` alongside the global block. The
# two older paths stay as fallbacks for installs that still use them.
#
# Ordering matters and getting it wrong was a real, long-lived defect: doctor and the
# scanner read only the older paths, so on this box they inspected an 822-byte file
# last written 2026-07-28 while the live one was 100KB and rewritten the same day.
# Doctor reported PASS about a file nobody reads. A verdict true of the wrong subject
# is worse than no verdict, because it suppresses the check that would have found the
# real state.
_MCP_REGISTRY_CANDIDATES: tuple[Path, ...] = (
    Path(os.path.expanduser("~/.claude.json")),
    Path(os.path.expanduser("~/.claude/mcp.json")),
    Path(os.path.expanduser("~/.claude/settings.json")),
)

_PLUGIN_MANIFEST_GLOBS: tuple[tuple[Path, str], ...] = (
    (Path(os.path.expanduser("~/.claude/plugins")), "**/plugin.json"),
)


def _read_mcp_servers() -> list[dict[str, Any]]:
    """Best-effort registered-MCP-server enumeration."""
    rows: list[dict[str, Any]] = []
    # First registration of a (name, scope) wins, and candidates are ordered live-file
    # first. Without this the same server registered in both the live and the legacy
    # file is listed twice — measured here as 3 duplicates out of 10 rows — which would
    # make a composition read report servers this practice does not separately have.
    seen: set[tuple[str, str]] = set()
    for path in _MCP_REGISTRY_CANDIDATES:
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.info(f"MCP registry parse skipped {path}: {exc}")
            continue

        if not isinstance(raw, dict):
            continue

        # Two registration scopes live in the same file: a global `mcpServers` block,
        # and per-project blocks under `projects[<abs path>].mcpServers`. Reading only
        # the global one undercounts every project-scoped server — on this box that is
        # most of them.
        blocks: list[tuple[str, dict]] = []
        if isinstance(raw.get("mcpServers"), dict):
            blocks.append(("global", raw["mcpServers"]))
        projects = raw.get("projects")
        if isinstance(projects, dict):
            for proj_path, proj_cfg in projects.items():
                if isinstance(proj_cfg, dict) and isinstance(proj_cfg.get("mcpServers"), dict):
                    blocks.append((str(proj_path), proj_cfg["mcpServers"]))

        for scope, servers_block in blocks:
            for name, cfg in servers_block.items():
                if not isinstance(cfg, dict):
                    continue
                key = (str(name), scope)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "name": name,
                        "source": str(path),
                        # `scope` and `observed_at` make the row self-describing: a
                        # consumer can tell a global registration from a project one,
                        # and how fresh the file backing it was, rather than having to
                        # assume both.
                        "scope": scope,
                        "observed_at": _mtime_iso(path),
                        "command": cfg.get("command"),
                        "args_count": len(cfg.get("args") or []),
                    }
                )
    return rows


def _mtime_iso(path: Path) -> str | None:
    """File mtime as ISO8601, or None if unreadable."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def _scan_plugin_manifests() -> list[str]:
    """Enumerate plugin.json paths under ``~/.claude/plugins``."""
    paths: list[str] = []
    for base, pattern in _PLUGIN_MANIFEST_GLOBS:
        if not base.exists():
            continue
        try:
            for match in base.glob(pattern):
                paths.append(str(match))
        except OSError as exc:
            logger.info(f"plugin manifest scan skipped {base}: {exc}")
    return sorted(paths)


def _detect_env_files(start: Path | None = None) -> list[str]:
    """Return ``.env*`` paths in the current project tree (no contents)."""
    cwd = (start or Path.cwd()).resolve()
    found: set[str] = set()
    for filename in (".env", ".env.local", ".env.production", ".env.development"):
        candidate = cwd / filename
        if candidate.exists() and candidate.is_file():
            found.add(str(candidate))
    return sorted(found)


def collect_manifests(read_surface) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(payload, coverage)`` for filesystem rows + MCP registry."""
    output: dict[str, Any] = {}

    if "plugin_manifest_paths" in read_surface.filesystem:
        output["plugin_manifest_paths"] = _scan_plugin_manifests()
    if "env_files_present" in read_surface.filesystem:
        output["env_files_present"] = _detect_env_files()
    if "recently_touched_model_weights" in read_surface.filesystem:
        # Phase 1: stub. Walking $HOME for >1GB files is expensive and easy
        # to get wrong; defer to Phase 2 with a per-project root + cache.
        output["recently_touched_model_weights"] = []

    if "registered_servers" in read_surface.mcp:
        output["mcp_registered_servers"] = _read_mcp_servers()
    if "active_connections" in read_surface.mcp:
        # Phase 1: stub — true active-connection introspection requires
        # MCP wire-protocol cooperation. Defer to Phase 2.
        output["mcp_active_connections"] = []

    coverage = {
        "plugin_manifests_found": len(output.get("plugin_manifest_paths", [])),
        "env_files_found": len(output.get("env_files_present", [])),
        "mcp_registered_servers": len(output.get("mcp_registered_servers", [])),
        "mcp_active_connections_implemented": False,  # Phase 2
        "model_weights_walk_implemented": False,  # Phase 2
    }
    return output, coverage
