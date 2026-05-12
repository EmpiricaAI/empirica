"""Bulk-project verbs (v0.5): projects-discover + projects-list.

projects-bulk-register lives in the same module once T2 ships — it consumes
the manifest produced here.

Filesystem walk strategy: walk roots ($HOME by default), skip well-known
noise dirs, look for any directory containing a `.empirica/` subdirectory.
For each match, parse `git remote get-url origin` (best-effort, normalize
ssh→https), and emit a manifest entry.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from empirica.cli.cli_utils import handle_cli_error

logger = logging.getLogger(__name__)


DEFAULT_MANIFEST_PATH = Path.home() / ".empirica" / "discovered_projects.yaml"

SKIP_DIR_NAMES = frozenset({
    "node_modules", ".git", ".venv", "venv", "__pycache__", ".tox",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist",
    "target", ".next", ".nuxt", ".cache", ".gradle", ".idea", ".vscode",
})


# ── Filesystem walk ─────────────────────────────────────────────────────


def _should_skip_dir(dirname: str, include_hidden: bool) -> bool:
    """Skip noise directories. Hidden dirs skipped unless --include-hidden."""
    if dirname in SKIP_DIR_NAMES:
        return True
    return not include_hidden and dirname.startswith(".") and dirname != "."


def _walk_for_empirica(root: Path, max_depth: int, include_hidden: bool) -> list[Path]:
    """Yield directories under `root` that contain `.empirica/project.yaml`.

    A project is identified by the presence of `.empirica/project.yaml`
    (real project metadata) rather than just an empty `.empirica/` directory.
    This handles the common workspace layout where a parent directory has a
    bare `.empirica/` and each sibling subdirectory is a real project.

    Walker keeps descending past matched projects in case there are nested
    sub-projects, but skips into the `.empirica/` directory itself (no
    point looking for projects inside another project's metadata folder).
    Skips SKIP_DIR_NAMES + hidden dirs (unless --include-hidden).
    """
    if not root.exists() or not root.is_dir():
        return []

    found: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]

    while stack:
        current, depth = stack.pop()

        # Real project: .empirica/project.yaml present
        if (current / ".empirica" / "project.yaml").is_file():
            found.append(current)
            # Still recurse — nested projects exist (rare but valid)

        if depth >= max_depth:
            continue

        try:
            children = list(current.iterdir())
        except (PermissionError, OSError) as e:
            logger.debug(f"projects-discover: skip {current} ({e})")
            continue

        for child in children:
            if not child.is_dir():
                continue
            if _should_skip_dir(child.name, include_hidden):
                continue
            # Don't descend into .empirica/ itself — internal data, no sub-projects
            if child.name == ".empirica":
                continue
            stack.append((child, depth + 1))

    return found


# ── Git remote parsing ──────────────────────────────────────────────────


_SSH_REMOTE_RE = re.compile(r"^git@([^:]+):(.+?)(\.git)?$")
_HTTPS_REMOTE_RE = re.compile(r"^(https?://[^/]+/.+?)(\.git)?$")


def _normalize_remote_url(raw: str) -> str | None:
    """Convert ssh-form remotes to https-form. Pass through https-form. None on garbage.

    Examples:
      git@github.com:Nubaeon/empirica.git → https://github.com/Nubaeon/empirica
      https://github.com/Nubaeon/empirica.git → https://github.com/Nubaeon/empirica
    """
    if not raw:
        return None
    raw = raw.strip()
    m = _SSH_REMOTE_RE.match(raw)
    if m:
        host, path = m.group(1), m.group(2)
        return f"https://{host}/{path}"
    m = _HTTPS_REMOTE_RE.match(raw)
    if m:
        return m.group(1)
    return None


def _read_git_remote_raw(project_path: Path) -> str | None:
    """Best-effort raw `git remote get-url origin` from project_path. None on miss/error.

    Returns the unmodified remote URL string. Caller normalizes to https-form
    via _normalize_remote_url so the manifest can carry both shapes.
    """
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
        logger.debug(f"projects-discover: git remote read failed for {project_path}: {e}")
        return None
    if result.returncode != 0:
        return None
    raw = result.stdout.strip()
    return raw or None


# ── Manifest building ───────────────────────────────────────────────────


def _build_manifest_entry(project_path: Path) -> dict[str, Any]:
    """Build one manifest entry for a discovered project."""
    raw_remote = _read_git_remote_raw(project_path)
    return {
        "path": str(project_path.resolve()),
        "name": project_path.name,
        "repo_url": _normalize_remote_url(raw_remote) if raw_remote else None,
        "has_empirica_dir": True,
        "git_remote_origin": raw_remote,
    }


def discover_projects(
    roots: list[Path] | None = None,
    *,
    max_depth: int = 5,
    include_hidden: bool = False,
) -> dict[str, Any]:
    """Walk roots and build a manifest of discovered .empirica/ projects.

    Public function — callable from tests and other modules without going
    through the CLI handler.
    """
    if roots is None:
        roots = [Path.home()]

    seen: set[str] = set()
    projects: list[dict[str, Any]] = []
    for root in roots:
        for project_path in _walk_for_empirica(root, max_depth, include_hidden):
            resolved = str(project_path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            projects.append(_build_manifest_entry(project_path))

    projects.sort(key=lambda p: p["path"])

    return {
        "discovered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "roots": [str(r) for r in roots],
        "projects": projects,
    }


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    """Write the manifest as YAML to `path`. Creates parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(manifest, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def load_manifest(path: Path) -> dict[str, Any] | None:
    """Load a previously-written manifest. Returns None if missing/unparseable."""
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        logger.debug(f"projects-list: failed to load manifest {path}: {e}")
        return None


# ── Output formatters ──────────────────────────────────────────────────


def _format_table(manifest: dict[str, Any]) -> str:
    projects = manifest.get("projects", [])
    if not projects:
        return "No Empirica projects discovered."

    name_w = max(len("NAME"), max(len(p.get("name") or "") for p in projects))
    path_w = max(len("PATH"), min(60, max(len(p.get("path") or "") for p in projects)))
    repo_w = max(len("REPO"), max(len(p.get("repo_url") or "") for p in projects))

    lines = [f"{'NAME':<{name_w}}  {'PATH':<{path_w}}  {'REPO':<{repo_w}}"]
    lines.append("-" * (name_w + path_w + repo_w + 4))
    for p in projects:
        name = (p.get("name") or "").ljust(name_w)
        path = (p.get("path") or "")[:path_w].ljust(path_w)
        repo = (p.get("repo_url") or "—").ljust(repo_w)
        lines.append(f"{name}  {path}  {repo}")
    lines.append("")
    lines.append(f"{len(projects)} projects discovered (manifest: {manifest.get('discovered_at', '?')}).")
    return "\n".join(lines)


def _format_output(manifest: dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(manifest, indent=2)
    if output_format == "table":
        return _format_table(manifest)
    return yaml.dump(manifest, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ── Command handlers ───────────────────────────────────────────────────


def handle_projects_discover_command(args) -> None:
    """Handle the projects-discover command."""
    try:
        roots = [Path(r).expanduser() for r in (args.roots or [str(Path.home())])]
        manifest = discover_projects(
            roots=roots,
            max_depth=args.max_depth,
            include_hidden=args.include_hidden,
        )

        # Determine target path
        manifest_arg = getattr(args, "manifest", None)
        if manifest_arg == "-":
            target = None
        elif manifest_arg:
            target = Path(manifest_arg).expanduser()
        else:
            target = DEFAULT_MANIFEST_PATH

        # Always print to stdout in chosen format
        output_format = getattr(args, "output", "yaml")
        if output_format == "yaml":
            sys.stdout.write(_format_output(manifest, "yaml"))
        elif output_format == "json":
            sys.stdout.write(_format_output(manifest, "json") + "\n")

        # Write to manifest file unless stdout-only
        if target is not None:
            try:
                write_manifest(manifest, target)
                if output_format != "yaml":
                    # Already printed JSON to stdout; also confirm cache write on stderr
                    print(
                        f"\n📁 Manifest cached at {target} ({len(manifest['projects'])} projects)",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"\n# Manifest cached at {target} ({len(manifest['projects'])} projects)",
                        file=sys.stderr,
                    )
            except OSError as e:
                print(f"⚠ Failed to write manifest cache: {e}", file=sys.stderr)

        # v1.10.0+: --register also upserts into ~/.empirica/registry.yaml
        # (the daemon's served set). --prune additionally drops stale entries.
        if getattr(args, "register", False):
            try:
                summary = _register_discovered_to_registry(
                    manifest, prune=getattr(args, "prune", False),
                )
                print(
                    f"\n📌 Registry updated: +{summary['added']} new, "
                    f"~{summary['updated']} updated"
                    + (f", −{summary['pruned']} pruned" if summary["pruned"] else "")
                    + f" → {summary['total']} total",
                    file=sys.stderr,
                )
            except Exception as e:
                print(f"⚠ Failed to update registry: {e}", file=sys.stderr)
    except Exception as e:
        handle_cli_error(e, "projects-discover")


def _register_discovered_to_registry(
    manifest: dict[str, Any], *, prune: bool = False
) -> dict[str, int]:
    """Upsert discovered projects into ~/.empirica/registry.yaml.

    Reads each discovered project's `.empirica/project.yaml` to extract the
    canonical project_id (Cortex UUID for registered projects; local slug
    for Empirica-only users). Falls back to directory name slug.

    Returns a summary of {added, updated, pruned, total}.
    """
    from empirica.api.registry import (
        load_registry,
        prune_stale,
        save_registry,
        upsert_project,
    )

    registry = load_registry()
    existing_ids = {p.get("project_id") for p in registry.get("projects", [])}

    added = 0
    updated = 0
    for entry in manifest.get("projects", []):
        raw_path = entry.get("path") or ""
        proj_yaml = _read_project_yaml_for_registry(raw_path)
        project_id = (
            proj_yaml.get("project_id")
            or entry.get("project_id")
            or entry.get("slug")
            or entry.get("name")
        )
        if not project_id:
            continue
        slug = entry.get("slug") or proj_yaml.get("slug") or entry.get("name") or ""
        name = (
            proj_yaml.get("display_name")
            or proj_yaml.get("name")
            or entry.get("name")
            or ""
        )
        was_existing = project_id in existing_ids
        upsert_project(
            registry,
            project_id=project_id,
            slug=slug,
            name=name,
            path=raw_path,
            repo_url=entry.get("repo_url"),
        )
        if was_existing:
            updated += 1
        else:
            added += 1
            existing_ids.add(project_id)

    pruned_count = 0
    if prune:
        _, removed = prune_stale(registry)
        pruned_count = len(removed)

    save_registry(registry)
    return {
        "added": added,
        "updated": updated,
        "pruned": pruned_count,
        "total": len(registry.get("projects", [])),
    }


def _read_project_yaml_for_registry(raw_path: str) -> dict[str, Any]:
    """Read .empirica/project.yaml for a discovered project. Returns {} on miss.

    Helper isolated so the registration path doesn't import yaml at module
    level (it's already loaded via the file-top import, but this scopes the
    read for clarity).
    """
    if not raw_path:
        return {}
    try:
        content = (Path(raw_path) / ".empirica" / "project.yaml").read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def handle_daemon_list_command(args) -> None:
    """Handle the daemon-list command (v1.10.0).

    Prints the contents of ~/.empirica/registry.yaml — the daemon's served set.
    """
    try:
        from empirica.api.registry import DEFAULT_REGISTRY_PATH, load_registry

        registry = load_registry()
        projects = registry.get("projects", [])

        output_format = getattr(args, "output", "table")
        if output_format == "json":
            sys.stdout.write(json.dumps(registry, indent=2) + "\n")
            return
        if output_format == "yaml":
            sys.stdout.write(
                yaml.dump(registry, default_flow_style=False, sort_keys=False, allow_unicode=True)
            )
            return

        # Table format
        if not projects:
            print(f"# Registry at {DEFAULT_REGISTRY_PATH} is empty or missing.")
            print(
                "# Populate via: empirica projects-discover --register",
                file=sys.stderr,
            )
            return

        name_w = max(len(p.get("name", "")) for p in projects)
        slug_w = max(len(p.get("slug", "")) for p in projects)
        header = f"{'NAME':{name_w}}  {'SLUG':{slug_w}}  PROJECT_ID                              PATH"
        sys.stdout.write(header + "\n")
        sys.stdout.write("-" * len(header) + "\n")
        for p in projects:
            pid = p.get("project_id", "")
            pid_short = pid[:36] if len(pid) > 36 else pid.ljust(36)
            sys.stdout.write(
                f"{p.get('name',''):{name_w}}  "
                f"{p.get('slug',''):{slug_w}}  "
                f"{pid_short}  "
                f"{p.get('path','')}\n"
            )
        sys.stdout.write(f"\n{len(projects)} projects registered.\n")
    except Exception as e:
        handle_cli_error(e, "daemon-list")


def handle_projects_list_command(args) -> None:
    """Handle the projects-list command."""
    try:
        manifest_arg = getattr(args, "manifest", None)
        target = Path(manifest_arg).expanduser() if manifest_arg else DEFAULT_MANIFEST_PATH

        manifest = None if args.refresh else load_manifest(target)
        if manifest is None:
            # Fall back to fresh scan
            manifest = discover_projects(roots=[Path.home()])
            # Best-effort cache write
            try:
                write_manifest(manifest, target)
            except OSError as e:
                logger.debug(f"projects-list: cache write failed: {e}")

        sys.stdout.write(_format_output(manifest, getattr(args, "output", "table")))
        if getattr(args, "output", "table") == "table":
            sys.stdout.write("\n")
    except Exception as e:
        handle_cli_error(e, "projects-list")


# ── Filter projects ─────────────────────────────────────────────────────


def filter_projects(
    projects: list[dict[str, Any]],
    *,
    includes: list[str] | None = None,
    excludes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter manifest projects by include/exclude regex patterns.

    Each pattern is matched (re.search) against project['name'] OR project['path'].
    Multi-include = OR (kept if any pattern matches). If no includes given, all
    projects pass the include stage. Multi-exclude = OR (dropped if any pattern
    matches). Excludes apply after includes.
    """
    inc_patterns = [re.compile(p) for p in (includes or [])]
    exc_patterns = [re.compile(p) for p in (excludes or [])]

    def _matches_any(project: dict[str, Any], patterns: list[re.Pattern[str]]) -> bool:
        name = project.get("name") or ""
        path = project.get("path") or ""
        return any(p.search(name) or p.search(path) for p in patterns)

    out: list[dict[str, Any]] = []
    for project in projects:
        if inc_patterns and not _matches_any(project, inc_patterns):
            continue
        if exc_patterns and _matches_any(project, exc_patterns):
            continue
        out.append(project)
    return out


# ── Cortex bulk-register ────────────────────────────────────────────────


CORTEX_REGISTER_PATH = "/v1/projects/register"
CORTEX_ADMIN_PATH = "/v1/admin/projects"


def _resolve_cortex_config(args) -> tuple[str | None, str | None]:
    """Return (cortex_url, api_key) from args or env."""
    url = (
        getattr(args, "cortex_url", None)
        or os.environ.get("CORTEX_REMOTE_URL")
        or os.environ.get("CORTEX_URL")
    )
    key = getattr(args, "api_key", None) or os.environ.get("CORTEX_API_KEY")
    return (url.rstrip("/") if url else None, key or None)


def _post_project(
    cortex_url: str,
    path: str,
    payload: dict[str, Any],
    api_key: str,
    timeout: float,
) -> tuple[int, dict[str, Any] | None]:
    """POST one project payload to Cortex. Returns (status_code, response_body|None).

    Network errors raise. HTTP error responses are caught and returned as
    (status_code, body) so the caller can branch on 409/404/etc.
    """
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{cortex_url}{path}",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, (json.loads(raw) if raw else None)
            except json.JSONDecodeError:
                return resp.status, None
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
            return e.code, json.loads(err_body) if err_body else None
        except (OSError, json.JSONDecodeError):
            return e.code, None


def _register_one_project(
    project: dict[str, Any],
    cortex_url: str,
    api_key: str,
    timeout: float,
) -> dict[str, Any]:
    """Try to register a single project. Returns a per-project result dict.

    Result shape: {name, outcome: registered|skipped|failed, status, reason?}
    """
    payload = {"name": project["name"]}
    if project.get("repo_url"):
        payload["repo_url"] = project["repo_url"]
    if project.get("name"):
        payload["display_name"] = project["name"]

    # Try the public register path first; fall back to admin on 404/405
    for path in (CORTEX_REGISTER_PATH, CORTEX_ADMIN_PATH):
        try:
            status, _body = _post_project(cortex_url, path, payload, api_key, timeout)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return {
                "name": project["name"],
                "outcome": "failed",
                "status": 0,
                "reason": f"network: {type(e).__name__}: {e}",
            }

        if status in (200, 201):
            return {"name": project["name"], "outcome": "registered", "status": status}
        if status == 409:
            return {"name": project["name"], "outcome": "skipped", "status": 409,
                    "reason": "already_exists"}
        if status in (404, 405) and path == CORTEX_REGISTER_PATH:
            continue  # try admin path
        return {
            "name": project["name"],
            "outcome": "failed",
            "status": status,
            "reason": f"http {status}",
        }

    return {"name": project["name"], "outcome": "failed", "status": 0,
            "reason": "exhausted endpoints"}


def _format_register_summary(
    results: list[dict[str, Any]],
    output_format: str,
    *,
    dry_run: bool,
    cortex_url: str | None,
) -> str:
    counts = {"registered": 0, "skipped": 0, "failed": 0}
    for r in results:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1

    if output_format == "json":
        return json.dumps({
            "ok": counts["failed"] == 0,
            "dry_run": dry_run,
            "cortex_url": cortex_url,
            "summary": counts,
            "results": results,
        }, indent=2) + "\n"

    lines: list[str] = []
    if dry_run:
        lines.append(f"DRY-RUN: would register {len(results)} projects on Cortex")
    else:
        lines.append(
            f"Registered {counts['registered']}, "
            f"skipped {counts['skipped']} (already exist), "
            f"failed {counts['failed']}"
        )
    if counts["failed"]:
        lines.append("")
        lines.append("Failures:")
        for r in results:
            if r["outcome"] == "failed":
                lines.append(f"  {r['name']}: {r.get('reason', '?')} (status={r.get('status')})")
    lines.append("")
    return "\n".join(lines)


def handle_projects_bulk_register_command(args) -> None:
    """Handle projects-bulk-register. Cortex-dependent."""
    try:
        # Load or build manifest
        manifest_arg = getattr(args, "manifest_path", None)
        target = Path(manifest_arg).expanduser() if manifest_arg else DEFAULT_MANIFEST_PATH
        manifest = load_manifest(target)
        if manifest is None:
            print(
                f"⚠ No manifest at {target} — running projects-discover now.",
                file=sys.stderr,
            )
            manifest = discover_projects(roots=[Path.home()])

        projects = manifest.get("projects", [])
        if not projects:
            print("⚠ No projects to register.", file=sys.stderr)
            return

        # Apply --include/--exclude filters (if any)
        includes = getattr(args, "includes", None) or []
        excludes = getattr(args, "excludes", None) or []
        if includes or excludes:
            try:
                filtered = filter_projects(projects, includes=includes, excludes=excludes)
            except re.error as e:
                print(f"⚠ Invalid filter regex: {e}", file=sys.stderr)
                sys.exit(2)
            if len(filtered) != len(projects):
                print(
                    f"📋 Filter: {len(projects)} → {len(filtered)} projects after include/exclude",
                    file=sys.stderr,
                )
            projects = filtered
            if not projects:
                print("⚠ No projects match the include/exclude filters.", file=sys.stderr)
                return

        # Dry-run: short-circuit before resolving Cortex config
        dry_run = bool(getattr(args, "dry_run", False))
        if dry_run:
            output_format = getattr(args, "output", "human")
            results = [{"name": p["name"], "outcome": "registered", "status": 0,
                        "reason": "dry-run"} for p in projects]
            sys.stdout.write(_format_register_summary(
                results, output_format, dry_run=True, cortex_url=None,
            ))
            return

        # Resolve Cortex config
        cortex_url, api_key = _resolve_cortex_config(args)
        if not cortex_url or not api_key:
            missing = []
            if not cortex_url:
                missing.append("CORTEX_REMOTE_URL or --cortex-url")
            if not api_key:
                missing.append("CORTEX_API_KEY or --api-key")
            print(
                "⚠ Cortex configuration missing: " + ", ".join(missing) + "\n"
                "  This command is Cortex-dependent. Set the env vars or pass "
                "the flags explicitly.",
                file=sys.stderr,
            )
            sys.exit(2)

        timeout = float(getattr(args, "timeout", 10.0))
        output_format = getattr(args, "output", "human")

        if output_format == "human":
            print(
                f"📡 Registering {len(projects)} projects on Cortex at {cortex_url}",
                file=sys.stderr,
            )

        results = [
            _register_one_project(p, cortex_url, api_key, timeout) for p in projects
        ]
        sys.stdout.write(_format_register_summary(
            results, output_format, dry_run=False, cortex_url=cortex_url,
        ))
    except Exception as e:
        handle_cli_error(e, "projects-bulk-register")
