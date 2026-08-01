"""Practice-record composition — what a practice is *made of*.

    GET /api/v1/practice/composition?project_id=<id>   (or ?path=)

Built for extension's practice record (prop_od4esudv). Extension is MV3 and has no
filesystem access, so `.empirica/project.yaml`, `module.yaml`, the agent and skill
directories and the MCP registration are all unreachable to it without a route — and
the daemon's 29 existing routes served none of it.

**The contract's load-bearing property is `null` vs `[]`, and it is extension's, not
mine: `null` means ABSENT or unattestable; `[]` means genuinely zero.** A practice
with fifteen skills rendering "skills: none" because the reader could not attest them
is a failure wearing a true negative's clothes. Cheap to honour at write time,
impossible to reconstruct downstream. Every collector below returns `None` on failure
and a list on success, never an empty list as a shrug.

`observed_at` and `config_watermark` carry the same discipline one level up: a
consumer can tell how fresh the answer is and whether the underlying files moved,
rather than assuming both.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/practice", tags=["practice"])

_PLUGIN_ROOT = Path(os.path.expanduser("~/.claude/plugins/local/empirica"))


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _read_module(root: Path) -> dict[str, Any] | None:
    """`module.yaml` if the practice has one. None when absent OR unreadable.

    Those two are deliberately not distinguished here: the contract's `module` field
    is object-or-null, and a practice legitimately having no module is indistinguishable
    from one whose module cannot be parsed *for rendering purposes*. What matters is
    that neither is reported as a module with empty fields.
    """
    for candidate in (root / "module.yaml", root / ".empirica" / "module.yaml"):
        if not candidate.exists():
            continue
        try:
            import yaml

            data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                return {"name": data.get("name"), "version": data.get("version")}
        except Exception as exc:
            logger.info(f"module read skipped {candidate}: {exc}")
            return None
    return None


def _read_project_prompt(root: Path) -> dict[str, Any] | None:
    """Attest the project prompt without shipping its contents.

    Returns presence, path, size and a sha256 — enough for the UI to say "there is a
    project prompt and here is its fingerprint" without the daemon becoming a way to
    exfiltrate a practice's instructions over HTTP.
    """
    for candidate in (root / "CLAUDE.md", root / ".empirica" / "project_prompt.md"):
        if not candidate.exists():
            continue
        try:
            raw = candidate.read_bytes()
        except OSError as exc:
            logger.info(f"project prompt unreadable {candidate}: {exc}")
            return None
        return {
            "present": True,
            "path": str(candidate),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    return None


def _list_markdown_units(directory: Path, source: str) -> list[dict[str, str]] | None:
    """Agents and skills share a shape: one markdown unit per name.

    None when the directory cannot be listed — NOT an empty list. An empty list is
    reserved for a directory that exists and genuinely holds nothing.
    """
    if not directory.exists():
        return None
    try:
        names = set()
        for entry in directory.iterdir():
            if entry.is_dir() and (entry / "SKILL.md").exists():
                names.add(entry.name)
            elif entry.is_file() and entry.suffix == ".md":
                names.add(entry.stem)
        return [{"name": n, "source": source} for n in sorted(names)]
    except OSError as exc:
        logger.info(f"unit listing failed {directory}: {exc}")
        return None


def _merge_units(root: Path, kind: str) -> list[dict[str, str]] | None:
    """Project-scoped units first, then plugin-global, each labelled by `source`.

    Extension's contract specified `source: project|plugin|user` as a DISCRIMINATOR
    and the first version of this route passed the constant "plugin". Their probe
    caught it immediately: three practices returned identical compositions, because
    every field except project_prompt read from the same machine-wide plugin
    directory and the scoping parameter changed nothing.

    The route was telling the truth — this fleet genuinely has no project-scoped
    config — but a record that cannot distinguish inherited environment from
    practice-specific composition renders 18 identical skills everywhere, which is
    truthful and zero-signal. `source` is what makes the record mean something.

    Project-scoped wins on a name collision: a practice that overrides an inherited
    agent has deliberately replaced it, and reporting both would misstate what runs.
    """
    project_units = _list_markdown_units(root / ".claude" / kind, "project")
    plugin_units = _list_markdown_units(_PLUGIN_ROOT / kind, "plugin")

    if project_units is None and plugin_units is None:
        return None

    merged: dict[str, dict[str, str]] = {}
    for unit in (plugin_units or []) + (project_units or []):
        merged[unit["name"]] = unit  # project listed second, so it overwrites
    return sorted(merged.values(), key=lambda u: u["name"])


def _read_mcp() -> list[dict[str, Any]] | None:
    """Registered MCP servers, via the scanner's reader.

    That reader was corrected in 5aa6e99cd to read `~/.claude.json` — the file Claude
    Code actually maintains — rather than the stale `~/.claude/mcp.json`. Building this
    field on the old reader would have shipped a confidently wrong list.
    """
    try:
        from empirica.core.scanner.manifests import _read_mcp_servers

        return [
            {
                "name": r.get("name"),
                "transport": "stdio" if r.get("command") else "unknown",
                "scope": r.get("scope"),
                "observed_at": r.get("observed_at"),
            }
            for r in _read_mcp_servers()
        ]
    except Exception as exc:
        logger.info(f"mcp read failed: {exc}")
        return None


def _watermark(root: Path) -> str | None:
    """Max mtime across the attested files, as a change signal.

    Lets a consumer cache and know when to re-fetch without diffing the payload.
    """
    newest = 0.0
    for p in (
        root / "CLAUDE.md",
        root / "module.yaml",
        root / ".empirica" / "project.yaml",
        _PLUGIN_ROOT / "skills",
        _PLUGIN_ROOT / "agents",
    ):
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            continue
    return f"mtime:{newest:.0f}" if newest else None


def _resolve_root(project_id: str | None, path: str | None) -> Path:
    if path:
        return Path(os.path.expanduser(path))
    if project_id:
        try:
            from empirica.config.registry import resolve_project_path  # type: ignore

            resolved = resolve_project_path(project_id)
            if resolved:
                return Path(resolved)
        except Exception as exc:
            # Log rather than swallow — an unresolvable id and a broken resolver both
            # end in the same 404 for the caller, but only one of them is a bug here.
            logger.info(f"project_id resolution failed for {project_id!r}: {exc}")
        raise HTTPException(status_code=404, detail=f"project_id {project_id!r} not resolvable to a path")
    return Path.cwd()


@router.get("/composition")
def get_composition(
    project_id: str | None = Query(default=None),
    path: str | None = Query(default=None),
) -> dict[str, Any]:
    """What this practice is composed of — module, prompt, agents, skills, MCP.

    Every field is `null` when absent or unattestable and a value when read. A `[]`
    means the collector looked and found nothing.
    """
    root = _resolve_root(project_id, path)
    return {
        "project_id": project_id,
        "path": str(root),
        "module": _read_module(root),
        "project_prompt": _read_project_prompt(root),
        "agents": _merge_units(root, "agents"),
        "skills": _merge_units(root, "skills"),
        "mcp_servers": _read_mcp(),
        "observed_at": _now_iso(),
        "config_watermark": _watermark(root),
    }
