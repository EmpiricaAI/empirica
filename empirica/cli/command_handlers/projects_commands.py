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

SKIP_DIR_NAMES = frozenset(
    {
        "node_modules",
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "build",
        "dist",
        "target",
        ".next",
        ".nuxt",
        ".cache",
        ".gradle",
        ".idea",
        ".vscode",
    }
)


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
      git@github.com:EmpiricaAI/empirica.git → https://github.com/EmpiricaAI/empirica
      https://github.com/EmpiricaAI/empirica.git → https://github.com/EmpiricaAI/empirica
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

        # v1.9.6+: --register also upserts into ~/.empirica/registry.yaml
        # (the daemon's served set). --prune additionally drops stale entries.
        # v1.11.x+: --register NAME filters the upsert to a single project
        # (matched by directory basename or project.yaml name).
        register_target = getattr(args, "register", None)
        if register_target is not None:
            register_manifest = manifest
            single_target = None
            if register_target != "*":
                single_target = str(register_target)
                register_manifest = _filter_manifest_to_target(manifest, single_target)
                if not register_manifest.get("projects"):
                    print(
                        f"⚠ No discovered project matches --register {single_target!r} "
                        f"(checked directory basename + project.yaml name). "
                        f"Try `empirica projects-discover` without --register to see "
                        f"what was found.",
                        file=sys.stderr,
                    )
                    return
            if single_target and getattr(args, "prune", False):
                print(
                    "⚠ --prune ignored: only meaningful with --register without NAME "
                    "(targeted register doesn't sweep the registry).",
                    file=sys.stderr,
                )
            try:
                summary = _register_discovered_to_registry(
                    register_manifest,
                    prune=getattr(args, "prune", False) and single_target is None,
                )
                target_note = f" ({single_target!r})" if single_target else ""
                print(
                    f"\n📌 Registry updated{target_note}: +{summary['added']} new, "
                    f"~{summary['updated']} updated"
                    + (f", −{summary['pruned']} pruned" if summary["pruned"] else "")
                    + f" → {summary['total']} total",
                    file=sys.stderr,
                )
            except Exception as e:
                print(f"⚠ Failed to update registry: {e}", file=sys.stderr)
    except Exception as e:
        handle_cli_error(e, "projects-discover")


def _filter_manifest_to_target(
    manifest: dict[str, Any],
    target: str,
) -> dict[str, Any]:
    """Filter a discover-manifest down to a single named project.

    Matches against (in order): directory basename (manifest entry.name),
    project.yaml.name, project.yaml.display_name. Case-sensitive exact
    match — no partials or globs (those belong on a separate `--match`
    flag if needed). Returns a manifest with `projects` narrowed to the
    matching entry/entries; preserves all other manifest keys.
    """
    matches: list[dict[str, Any]] = []
    for entry in manifest.get("projects", []):
        candidates: list[str] = []
        if entry.get("name"):
            candidates.append(str(entry["name"]))
        if entry.get("path"):
            candidates.append(Path(entry["path"]).name)
        proj_yaml = _read_project_yaml_for_registry(entry.get("path") or "")
        for key in ("name", "display_name"):
            v = proj_yaml.get(key)
            if v:
                candidates.append(str(v))
        if target in candidates:
            matches.append(entry)
    out = dict(manifest)
    out["projects"] = matches
    return out


def _register_discovered_to_registry(manifest: dict[str, Any], *, prune: bool = False) -> dict[str, int]:
    """Upsert discovered projects into ~/.empirica/registry.yaml.

    Reads each discovered project's `.empirica/project.yaml` to extract the
    canonical project_id (Cortex UUID for registered projects; local slug
    for Empirica-only users). Falls back to directory name slug.

    Returns a summary of {added, updated, pruned, total}.
    """
    from empirica.api.registry import (
        dedupe_registry,
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
        project_id = proj_yaml.get("project_id") or entry.get("project_id") or entry.get("slug") or entry.get("name")
        if not project_id:
            continue
        slug = entry.get("slug") or proj_yaml.get("slug") or entry.get("name") or ""
        name = proj_yaml.get("display_name") or proj_yaml.get("name") or entry.get("name") or ""
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

    # Reconcile same-path duplicates before persisting (a re-key/clone can leave a
    # stale slug-keyed twin) so sync never writes a transient dup.
    registry, _ = dedupe_registry(registry)
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
    """Handle the daemon-list command (v1.9.6).

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
            sys.stdout.write(yaml.dump(registry, default_flow_style=False, sort_keys=False, allow_unicode=True))
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
                f"{p.get('name', ''):{name_w}}  {p.get('slug', ''):{slug_w}}  {pid_short}  {p.get('path', '')}\n"
            )
        sys.stdout.write(f"\n{len(projects)} projects registered.\n")
    except Exception as e:
        handle_cli_error(e, "daemon-list")


def handle_daemon_grant_command(args) -> None:
    """Approve a pending credential grant requested by the extension.

    UI-prompted-token consent flow (goal 167fc8d4 / prop_b4si26t7c5):
      1. Extension POSTs /api/v1/credentials/grant/request → daemon
         prints a user_code to its stdout.
      2. User runs `empirica daemon-grant <user_code>` (THIS verb).
      3. Extension polls /api/v1/credentials/grant/poll → receives
         the credentials snapshot exactly once.

    Approve snapshots the current Cortex credentials from
    credentials.yaml into the grant record. Past-expiry / unknown
    user_code / already-decided grants all surface as errors.
    """
    user_code = getattr(args, "user_code", None)
    output_format = getattr(args, "output", "human")
    if not user_code:
        _emit_grant_error(
            "user_code required (positional)",
            output_format,
        )
        return
    try:
        from empirica.api import daemon_grants
        from empirica.config.credentials_loader import CredentialsLoader

        # Snapshot what the extension will receive on its next poll.
        cortex_cfg = CredentialsLoader().get_cortex_config()
        credentials_snapshot = {"cortex": cortex_cfg}
        record = daemon_grants.approve_grant(
            user_code=user_code,
            credentials=credentials_snapshot,
        )
        if record is None:
            _emit_grant_error(
                f"No pending grant for user_code={user_code!r} (unknown, expired, or already approved/denied)",
                output_format,
            )
            return
        payload = {
            "ok": True,
            "user_code": record.user_code,
            "device_code_prefix": record.device_code[:8],
            "requesting_app": record.requesting_app,
            "approved_at": record.approved_at,
            "expires_at": record.expires_at,
        }
        if output_format == "json":
            sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        else:
            sys.stdout.write(
                f"✅ Grant approved for {record.requesting_app!r} "
                f"(user_code={record.user_code}).\n"
                f"   The extension's next poll will receive the credentials.\n"
            )
    except Exception as e:
        handle_cli_error(e, "daemon-grant")


def handle_daemon_deny_command(args) -> None:
    """Deny a pending credential grant requested by the extension."""
    user_code = getattr(args, "user_code", None)
    output_format = getattr(args, "output", "human")
    if not user_code:
        _emit_grant_error(
            "user_code required (positional)",
            output_format,
        )
        return
    try:
        from empirica.api import daemon_grants

        record = daemon_grants.deny_grant(user_code=user_code)
        if record is None:
            _emit_grant_error(
                f"No pending grant for user_code={user_code!r}",
                output_format,
            )
            return
        payload = {
            "ok": True,
            "user_code": record.user_code,
            "device_code_prefix": record.device_code[:8],
            "requesting_app": record.requesting_app,
            "denied_at": record.denied_at,
        }
        if output_format == "json":
            sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        else:
            sys.stdout.write(f"🚫 Grant denied for {record.requesting_app!r} (user_code={record.user_code}).\n")
    except Exception as e:
        handle_cli_error(e, "daemon-deny")


def handle_daemon_grants_list_command(args) -> None:
    """List current credential grant records on disk.

    Reaps expired records first so the listing matches what the
    extension's next poll would see.
    """
    output_format = getattr(args, "output", "table")
    try:
        from empirica.api import daemon_grants

        daemon_grants.reap_expired()
        records = daemon_grants.list_records()
        payload = {
            "ok": True,
            "grants": [
                {
                    "user_code": r.user_code,
                    "device_code_prefix": r.device_code[:8],
                    "requesting_app": r.requesting_app,
                    "status": r.status,
                    "created_at": r.created_at,
                    "expires_at": r.expires_at,
                }
                for r in records
            ],
        }
        if output_format == "json":
            sys.stdout.write(json.dumps(payload, indent=2) + "\n")
            return
        if not records:
            sys.stdout.write("# No pending credential grants.\n")
            return
        sys.stdout.write(
            f"{'USER_CODE':<11}  {'STATUS':<10}  {'REQUESTING_APP':<18}  DEVICE_CODE_PREFIX  EXPIRES_IN_SEC\n"
        )
        sys.stdout.write("-" * 80 + "\n")
        import time as _time

        now = _time.time()
        for r in records:
            ttl = max(0, int(r.expires_at - now))
            sys.stdout.write(
                f"{r.user_code:<11}  {r.status:<10}  {r.requesting_app:<18}  {r.device_code[:8]}            {ttl}\n"
            )
    except Exception as e:
        handle_cli_error(e, "daemon-grants-list")


def _emit_grant_error(message: str, output_format: str) -> None:
    """Shared error surface for the daemon-grant family."""
    if output_format == "json":
        sys.stdout.write(json.dumps({"ok": False, "error": message}) + "\n")
    else:
        sys.stderr.write(f"error: {message}\n")


def handle_projects_sync_command(args) -> None:
    """Master sync verb: discover → registry → Cortex POST, one shot.

    Closes prop_ncitlxqewrabzheagvdkra5ahi. Collapses the discover →
    registry upsert → bulk-register chain into one verb so users don't
    have to know the two-name sequence. Each phase is independently
    skippable via flags so the same command works for online sync,
    offline registry-only, and pure-preview modes.

    Phase 1 (always): filesystem walk via discover_projects.
    Phase 2 (skipped by --no-write/--dry-run): write manifest cache +
        upsert registry.yaml. --prune drops stale rows.
    Phase 3 (skipped by --no-cortex/--no-write/--dry-run): POST each
        registered project to Cortex via the existing bulk-register loop.

    All composition delegates to existing helpers — no logic duplication.
    """
    try:
        # ── Phase 1: filesystem walk (always) ─────────────────────
        roots = [Path(r).expanduser() for r in (args.roots or [str(Path.home())])]
        manifest = discover_projects(
            roots=roots,
            max_depth=args.max_depth,
            include_hidden=args.include_hidden,
        )
        n_discovered = len(manifest.get("projects", []))

        output_format = getattr(args, "output", "human")
        dry_run = bool(getattr(args, "dry_run", False))
        no_write = bool(getattr(args, "no_write", False))
        no_cortex = bool(getattr(args, "no_cortex", False))

        # Stash phase-by-phase outcomes for the summary
        outcome: dict[str, Any] = {
            "discovered": n_discovered,
            "manifest_written": False,
            "registry": None,  # filled by phase 2
            "cortex": None,  # filled by phase 3
            "phases_skipped": [],
        }

        if no_write or dry_run:
            outcome["phases_skipped"].append("manifest_write")
            outcome["phases_skipped"].append("registry_upsert")
            outcome["phases_skipped"].append("cortex_post")
            _emit_sync_summary(outcome, output_format, dry_run=True)
            return

        # ── Phase 2: manifest cache + registry upsert ──────────────
        try:
            write_manifest(manifest, DEFAULT_MANIFEST_PATH)
            outcome["manifest_written"] = True
        except OSError as e:
            print(f"⚠ Failed to write manifest cache: {e}", file=sys.stderr)

        try:
            outcome["registry"] = _register_discovered_to_registry(
                manifest,
                prune=getattr(args, "prune", False),
            )
        except Exception as e:
            print(f"⚠ Registry upsert failed: {e}", file=sys.stderr)
            outcome["phases_skipped"].append("cortex_post")
            _emit_sync_summary(outcome, output_format, dry_run=False)
            return

        # ── Phase 3: Cortex POST (unless --no-cortex) ──────────────
        if no_cortex:
            outcome["phases_skipped"].append("cortex_post")
            _emit_sync_summary(outcome, output_format, dry_run=False)
            return

        outcome["cortex"] = _sync_phase3_cortex_post(args, output_format)
        _emit_sync_summary(outcome, output_format, dry_run=False)
    except Exception as e:
        handle_cli_error(e, "projects-sync")


def _sync_phase3_cortex_post(args, output_format: str) -> dict[str, Any] | None:
    """Run the Cortex POST loop using the registry as source of truth.

    Returns a dict with {cortex_url, results, registered, failed} or None
    when Cortex config is missing (signaled to caller for summary).
    """
    cortex_url, api_key = _resolve_cortex_config(args)
    if not cortex_url or not api_key:
        missing = []
        if not cortex_url:
            missing.append("CORTEX_REMOTE_URL or --cortex-url")
        if not api_key:
            missing.append("CORTEX_API_KEY or --api-key")
        print(
            "⚠ Cortex configuration missing: " + ", ".join(missing) + "\n"
            "  Use --no-cortex to skip the POST phase explicitly, or set "
            "the env vars / pass the flags.",
            file=sys.stderr,
        )
        return None

    # Source from registry.yaml (the curated set we just upserted)
    projects = _load_projects_for_register(None, from_discovered=False)
    if not projects:
        return {"cortex_url": cortex_url, "results": [], "registered": 0, "failed": 0}

    # Apply filters
    includes = getattr(args, "includes", None) or []
    excludes = getattr(args, "excludes", None) or []
    if includes or excludes:
        try:
            projects = filter_projects(projects, includes=includes, excludes=excludes)
        except re.error as e:
            print(f"⚠ Invalid filter regex: {e}", file=sys.stderr)
            return {"cortex_url": cortex_url, "results": [], "registered": 0, "failed": 0}
    if not projects:
        return {"cortex_url": cortex_url, "results": [], "registered": 0, "failed": 0}

    timeout = float(getattr(args, "timeout", 10.0))
    force_metadata = bool(getattr(args, "force_metadata_update", False))
    if output_format == "human":
        print(f"📡 Registering {len(projects)} projects on Cortex at {cortex_url}", file=sys.stderr)
    results = [
        _register_one_project(p, cortex_url, api_key, timeout, force_metadata_update=force_metadata) for p in projects
    ]
    registered = sum(1 for r in results if r.get("status") in (0, 200, 201))
    failed = len(results) - registered
    return {
        "cortex_url": cortex_url,
        "results": results,
        "registered": registered,
        "failed": failed,
    }


def _emit_sync_summary(outcome: dict[str, Any], output_format: str, *, dry_run: bool) -> None:
    """Render the projects-sync summary (json or human)."""
    if output_format == "json":
        payload = {
            "ok": True,
            "dry_run": dry_run,
            "discovered": outcome["discovered"],
            "manifest_written": outcome["manifest_written"],
            "registry": outcome["registry"],
            "cortex": outcome["cortex"],
            "phases_skipped": outcome["phases_skipped"],
        }
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return

    # Human format
    prefix = "🔎 [DRY RUN] " if dry_run else "📦 "
    print(f"{prefix}Discovered {outcome['discovered']} project(s)", file=sys.stderr)

    if outcome["registry"]:
        r = outcome["registry"]
        line = (
            f"📌 Registry: +{r['added']} added, ~{r['updated']} updated"
            + (f", −{r['pruned']} pruned" if r["pruned"] else "")
            + f" → {r['total']} total"
        )
        print(line, file=sys.stderr)
    elif "registry_upsert" in outcome["phases_skipped"] and not dry_run:
        print("⏭  Registry upsert skipped", file=sys.stderr)

    if outcome["cortex"]:
        c = outcome["cortex"]
        print(f"☁️  Cortex: {c['registered']} registered, {c['failed']} failed ({c['cortex_url']})", file=sys.stderr)
    elif "cortex_post" in outcome["phases_skipped"]:
        reason = "(--no-cortex)" if not dry_run else "(dry-run)"
        print(f"⏭  Cortex POST skipped {reason}", file=sys.stderr)


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
CORTEX_UNREGISTER_PATH = "/v1/projects/unregister"
CORTEX_ADMIN_PATH = "/v1/admin/projects"
CORTEX_USER_PROJECTS_PATH = "/v1/users/me/projects"


def _resolve_cortex_config(args) -> tuple[str | None, str | None]:
    """Return (cortex_url, api_key) by precedence:

    1. CLI flags (`--cortex-url`, `--api-key`) — explicit per-invocation override
    2. Env vars (`CORTEX_REMOTE_URL` / `CORTEX_URL`, `CORTEX_API_KEY`)
    3. `cortex:` block in `~/.empirica/credentials.yaml` via the
       centralized CredentialsLoader (so users don't have to export
       env vars in every shell — mirrors the extension's chrome.storage
       save on the browser side)
    """
    arg_url = getattr(args, "cortex_url", None)
    arg_key = getattr(args, "api_key", None)
    if arg_url and arg_key:
        return arg_url.rstrip("/"), arg_key

    from empirica.config.credentials_loader import get_credentials_loader

    cfg = get_credentials_loader().get_cortex_config()
    url = arg_url or cfg.get("url")
    key = arg_key or cfg.get("api_key")
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


def _link_user_to_project(
    cortex_url: str,
    project_id: str,
    api_key: str,
    timeout: float,
) -> dict[str, Any]:
    """POST /v1/users/me/projects to add project_id to the caller's user.project_ids.

    Cortex's `/v1/projects/register` runs link_user_to_project implicitly on
    every call regardless of new-vs-existing (SHA 51da2d1, 2026-05-18). This
    explicit call is **defensive depth** — eliminates the implicit dependency
    so a future cortex change can't silently break the user-link path. Also
    covers the `--force-metadata-update` case where the user expects the link
    to be refreshed even on idempotent re-registers.

    Returns: {linked: bool, status: int, reason?: str}.
    Status semantics:
      - 200/201/204 → linked (newly or already present)
      - 409          → already-linked (treated as success)
      - else         → failure (non-fatal — register itself already succeeded)

    Closes prop_oqijggci4fctlejurnryhomccm (cortex AI, 2026-05-18).
    """
    payload = {"project_id": project_id}
    try:
        status, _body = _post_project(
            cortex_url,
            CORTEX_USER_PROJECTS_PATH,
            payload,
            api_key,
            timeout,
        )
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"linked": False, "status": 0, "reason": f"network: {type(e).__name__}: {e}"}
    if status in (200, 201, 204, 409):
        return {"linked": True, "status": status}
    return {"linked": False, "status": status, "reason": f"http {status}"}


def _register_one_project(
    project: dict[str, Any],
    cortex_url: str,
    api_key: str,
    timeout: float,
    force_metadata_update: bool = False,
) -> dict[str, Any]:
    """Try to register a single project. Returns a per-project result dict.

    Result shape: {name, outcome: registered|skipped|failed, status, reason?, link?}

    When force_metadata_update=True, the request body carries
    `force_metadata_update: true` so the Cortex side (when supported)
    updates an existing row's name + display_name + repo_url from
    UUID-shaped placeholders to the real values from the local manifest.
    Without this flag, repeat POSTs return 409/already_exists and the
    stale row is preserved.

    Post-register, attempts an explicit POST /v1/users/me/projects to link
    the project to the caller's user.project_ids (defensive depth — see
    `_link_user_to_project` docstring). The link is best-effort; failure
    there does not flip the register outcome.
    """
    payload: dict[str, Any] = {"name": project["name"]}
    if project.get("repo_url"):
        payload["repo_url"] = project["repo_url"]
    if project.get("name"):
        payload["display_name"] = project["name"]
    if force_metadata_update:
        payload["force_metadata_update"] = True

    # Try the public register path first; fall back to admin on 404/405
    register_result: dict[str, Any] | None = None
    project_id: str | None = None
    for path in (CORTEX_REGISTER_PATH, CORTEX_ADMIN_PATH):
        try:
            status, body = _post_project(cortex_url, path, payload, api_key, timeout)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return {
                "name": project["name"],
                "outcome": "failed",
                "status": 0,
                "reason": f"network: {type(e).__name__}: {e}",
            }

        if status in (200, 201):
            if isinstance(body, dict):
                project_id = body.get("project_id") or body.get("id")
            register_result = {
                "name": project["name"],
                "outcome": "registered",
                "status": status,
                "project_id": project_id,
            }
            break
        if status == 409:
            if isinstance(body, dict):
                project_id = body.get("project_id") or body.get("id")
            register_result = {
                "name": project["name"],
                "outcome": "skipped",
                "status": 409,
                "reason": "already_exists",
                "project_id": project_id,
            }
            break
        if status in (404, 405) and path == CORTEX_REGISTER_PATH:
            continue  # try admin path
        if _is_owner_conflict(status, body):
            # Foreign project_id (clone/dup). Return early — do NOT run the
            # defensive user-link below, which would leak the foreign pid.
            return {
                "name": project["name"],
                "outcome": "owner_conflict",
                "status": status,
                "reason": _owner_conflict_message(body) or "project_id already registered to a different owner",
                "hint": _OWNER_CONFLICT_HINT,
            }
        return {
            "name": project["name"],
            "outcome": "failed",
            "status": status,
            "reason": f"http {status}",
        }

    if register_result is None:
        return {"name": project["name"], "outcome": "failed", "status": 0, "reason": "exhausted endpoints"}

    # Defensive user-link (closes prop_oqijggci4fctlejurnryhomccm). Best-effort —
    # register itself already succeeded; a link failure does NOT flip the
    # outcome. Surfaced under `link` so callers can surface stragglers.
    if project_id:
        register_result["link"] = _link_user_to_project(
            cortex_url,
            project_id,
            api_key,
            timeout,
        )
    else:
        register_result["link"] = {
            "linked": False,
            "status": 0,
            "reason": "no project_id in register response",
        }
    return register_result


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
        return (
            json.dumps(
                {
                    "ok": counts["failed"] == 0,
                    "dry_run": dry_run,
                    "cortex_url": cortex_url,
                    "summary": counts,
                    "results": results,
                },
                indent=2,
            )
            + "\n"
        )

    lines: list[str] = []
    if dry_run:
        lines.append(f"DRY-RUN: would register {len(results)} projects on Cortex")
    else:
        lines.append(
            f"Registered {counts['registered']}, skipped {counts['skipped']} (already exist), failed {counts['failed']}"
        )
    if counts["failed"]:
        lines.append("")
        lines.append("Failures:")
        for r in results:
            if r["outcome"] == "failed":
                lines.append(f"  {r['name']}: {r.get('reason', '?')} (status={r.get('status')})")
    lines.append("")
    return "\n".join(lines)


def _load_projects_for_register(manifest_arg: str | None, from_discovered: bool) -> list[dict[str, Any]] | None:
    """Source projects for bulk-register.

    Default: read the user's curated `~/.empirica/registry.yaml` (the same
    file the daemon uses to decide which projects to serve). If the user
    has run `empirica projects-discover --register` they have a curated
    set; that IS the answer.

    `--from-discovered` (or explicit `--from <path>`): use the raw scanner
    output `~/.empirica/discovered_projects.yaml` instead — for the "I
    want to register everything I have, no curation" workflow.

    Returns a normalized list of {name, path, repo_url} dicts or None if
    no source was findable.
    """
    if manifest_arg:
        target = Path(manifest_arg).expanduser()
        manifest = load_manifest(target)
        if manifest is None:
            print(f"⚠ Manifest not found at {target}.", file=sys.stderr)
            return None
        return manifest.get("projects", [])

    if from_discovered:
        target = DEFAULT_MANIFEST_PATH
        manifest = load_manifest(target)
        if manifest is None:
            print(
                f"⚠ No manifest at {target} — running projects-discover now.",
                file=sys.stderr,
            )
            manifest = discover_projects(roots=[Path.home()])
        return manifest.get("projects", [])

    # Default: source from the curated registry.yaml
    from empirica.api.registry import DEFAULT_REGISTRY_PATH, load_registry

    registry = load_registry()
    if not registry.get("projects"):
        print(
            f"⚠ No projects in {DEFAULT_REGISTRY_PATH}.\n"
            f"  Either run `empirica projects-discover --register` to populate it,\n"
            f"  or pass --from-discovered to source from the full filesystem scan.",
            file=sys.stderr,
        )
        return None
    # Normalize registry shape → bulk-register payload shape
    return [
        {
            "name": entry.get("slug") or entry.get("name") or "",
            "path": entry.get("path", ""),
            "repo_url": entry.get("repo_url"),
        }
        for entry in registry["projects"]
        if entry.get("slug") or entry.get("name")
    ]


# ── projects-unregister ─────────────────────────────────────────────────


def _resolve_project_id_from_yaml() -> str | None:
    """Walk up from cwd looking for `.empirica/project.yaml` and return its
    `project_id` field, or None if not found / not readable."""
    try:
        project_path = Path.cwd()
        for parent in [project_path, *project_path.parents]:
            yaml_path = parent / ".empirica" / "project.yaml"
            if yaml_path.exists():
                with open(yaml_path) as f:
                    data = yaml.safe_load(f) or {}
                return data.get("project_id")
    except Exception as e:
        logger.debug(f"project.yaml resolution failed: {e}")
    return None


def _classify_unregister_outcome(
    status: int,
    purge: bool,
) -> tuple[bool, str]:
    """Map cortex /v1/projects/unregister status code → (ok, outcome)."""
    if status in (200, 201, 204):
        return True, ("purged" if purge else "archived")
    if status == 404:
        return False, "not_found"
    if status == 409:
        return True, "already_archived"  # idempotent — already in target state
    return False, "error"


def handle_projects_unregister_command(args) -> None:
    """Unregister a project from Cortex (soft archive by default, --purge to hard-delete).

    Soft archive (default):
      - Sets `projects.is_archived=true` and `archived_at=now()` on cortex.
      - Removes the project_id from caller's `users.project_ids` (no longer
        surfaces in roster, /threads, /sers projections).
      - Preserves rows: proposals, SER records, artifact history — all stay
        readable for audit.

    Hard purge (--purge):
      - DELETEs the project row from `projects`.
      - Cascade-deletes proposals + SERs + artifacts owned by the project.
      - Irreversible. Requires --confirm to actually execute.

    Project resolution (precedence):
      1. --project-id <uuid>
      2. --slug <slug>      (resolves against current user's projects)
      3. .empirica/project.yaml `project_id` (when run from a project tree)
    """
    try:
        project_id = getattr(args, "project_id", None)
        slug = getattr(args, "slug", None)
        purge = bool(getattr(args, "purge", False))
        confirm = bool(getattr(args, "confirm", False))
        output_format = getattr(args, "output", "human")
        timeout = float(getattr(args, "timeout", 10.0))

        # Resolve project_id from .empirica/project.yaml when neither
        # --project-id nor --slug was supplied. Slug → handled by cortex.
        if not project_id and not slug:
            project_id = _resolve_project_id_from_yaml()

        if not project_id and not slug:
            print(
                "Error: project not identified. Pass --project-id <uuid> or "
                "--slug <slug>, or run from inside a project tree with "
                ".empirica/project.yaml.",
                file=sys.stderr,
            )
            sys.exit(2)

        if purge and not confirm:
            print(
                "Error: --purge is irreversible and requires --confirm. "
                "Re-run with --confirm to hard-delete the project row + "
                "cascade artifacts. Without --purge the default is a soft "
                "archive (reversible).",
                file=sys.stderr,
            )
            sys.exit(2)

        cortex_url, api_key = _resolve_cortex_config(args)
        if not cortex_url or not api_key:
            print(
                "Error: cortex config missing. Pass --cortex-url + --api-key, "
                "set CORTEX_URL + CORTEX_API_KEY env, or configure cortex: "
                "block in ~/.empirica/credentials.yaml.",
                file=sys.stderr,
            )
            sys.exit(2)

        payload: dict[str, Any] = {"purge": purge}
        if project_id:
            payload["project_id"] = project_id
        if slug:
            payload["slug"] = slug

        status, body = _post_project(
            cortex_url,
            CORTEX_UNREGISTER_PATH,
            payload,
            api_key,
            timeout,
        )

        ok, outcome = _classify_unregister_outcome(status, purge)

        result = {
            "ok": ok,
            "outcome": outcome,
            "status_code": status,
            "project_id": project_id,
            "slug": slug,
            "purge": purge,
            "reason": (body or {}).get("reason") if body else None,
        }

        if output_format == "json":
            print(json.dumps(result, indent=2, default=str))
        else:
            verb = "Purged" if purge else "Archived"
            if ok:
                ident = project_id or slug or "?"
                print(f"✅ {verb} project {ident} on cortex (status={status})")
            else:
                print(f"❌ Unregister failed: {outcome} (status={status})", file=sys.stderr)
                if body and body.get("reason"):
                    print(f"   {body['reason']}", file=sys.stderr)

        if not ok:
            sys.exit(1)
    except Exception as e:
        handle_cli_error(e, "projects-unregister")


# ── V1.5: single-project register (atomic discover-one + dual-write + cortex POST) ──


def _project_register_error(message: str, hint: str, output_format: str) -> int:
    """Emit an actionable error for project-register. Returns the exit code."""
    if output_format == "json":
        print(json.dumps({"ok": False, "error": message, "hint": hint}, indent=2))
    else:
        print(f"❌ {message}", file=sys.stderr)
        print(f"   {hint}", file=sys.stderr)
    return 1


def _cortex_canonical_project_id(project_path, cortex_outcome):
    """Cortex's AUTHORITATIVE canonical project id for this project.

    Prefer ``GET /v1/projects/by-slug`` (the id roster / practice-context /
    blob-upload all validate against). Fall back to the register-POST response
    only when by-slug can't resolve.

    Why not the register response directly: the register endpoint can report a
    stale ``already_registered`` id that DISAGREES with tenants.db / roster —
    web hit exactly this (register said 258aa934 / diverged:false, while
    roster + blob-upload only know dc6298e2). Trusting the register flag made
    --reconcile decline to rekey while the media upload kept failing. by-slug is
    the surface that matters, so it's the truth the reconcile must target.
    """
    try:
        from pathlib import Path

        import yaml

        from empirica.core.identity_migration import _make_cortex_slug_resolver

        cfg = {}
        pj = Path(project_path) / ".empirica" / "project.yaml"
        if pj.exists():
            cfg = yaml.safe_load(pj.read_text(encoding="utf-8")) or {}
        slug = cfg.get("slug") or cfg.get("name") or Path(project_path).name
        canonical = _make_cortex_slug_resolver()(slug, None)
        if canonical:
            return canonical
    except Exception:
        pass
    return cortex_outcome.get("project_id")


def _reconcile_identity_if_diverged(args, project_path, local_id, cortex_outcome, output_format):
    """After register, detect local ≠ cortex-canonical project UUID.

    Cortex is authority (David's directive): the canonical id comes from
    ``GET /v1/projects/by-slug`` (roster/practice-context), NOT the register
    response — the register endpoint can report a stale id that disagrees with
    the surface blob-upload validates against (web's 258aa934 vs dc6298e2). If
    the canonical differs from the local id, the identity has diverged (the root
    cause of web's media blocker). Warn ALWAYS (closes the silent gap); with
    --reconcile, rekey every local store to the canonical id.

    Returns a divergence dict for the result payload, or None when aligned.
    """
    cortex_id = _cortex_canonical_project_id(project_path, cortex_outcome)
    if not cortex_id or cortex_id == local_id or cortex_outcome.get("outcome") == "owner_conflict":
        return None
    info: dict[str, Any] = {"local_id": local_id, "cortex_id": cortex_id, "reconciled": False}
    if getattr(args, "reconcile", False):
        # Guard: reconcile rekeys the live session's OWN rows (sessions.db) while
        # the running process still holds the old project_id in memory — running
        # it mid-transaction strands the session against its own rekeyed data.
        # Refuse when a transaction is open unless --force.
        if not getattr(args, "force", False):
            try:
                from empirica.utils.session_resolver import InstanceResolver as _R

                tx = _R.transaction_read()
            except Exception:
                tx = None
            if tx and tx.get("status") == "open":
                info["blocked"] = "open_transaction"
                if output_format != "json":
                    print(
                        "   ⛔ --reconcile blocked: an open transaction is active. Reconcile "
                        "rekeys the live session's own rows — POSTFLIGHT/close the session first, "
                        "or re-run with --force."
                    )
                return info

        from empirica.core.identity_migration import reconcile_project_identity

        rep = reconcile_project_identity(project_path, local_id, cortex_id)
        info["reconciled"] = bool(rep.get("reconciled"))
        info["report"] = rep
        if output_format != "json":
            print(
                f"   🔧 Identity reconciled: local {local_id[:8]}… → cortex {cortex_id[:8]}… (all local stores rekeyed)"
            )
            print("   ⚠ Run 'empirica rebuild --qdrant' to re-point Qdrant collections to the new id.")
    else:
        if output_format != "json":
            print(f"   ⚠ IDENTITY DIVERGENCE: local {local_id[:8]}… ≠ cortex-canonical {cortex_id[:8]}…")
            print("   → Run 'empirica project-register --reconcile' to converge local stores to the cortex id.")
    return info


def handle_project_register_command(args) -> None:
    """V1.5 single-project register — atomic discover-one + register-one.

    Replaces the brittle chain of ``projects-discover --register NAME &&
    projects-bulk-register --include NAME`` with one verb optimised for the
    AI-as-CLI-user / copy-prompt UX (extension's prop_apevka5iwj Discover/Register
    surface design depends on this).

    Sequence (local-first atomic):
      1. Resolve PATH (default: cwd). Require ``.empirica/project.yaml``.
      2. Read project.yaml. project_id must be present; if absent, point the
         user at ``empirica project-init`` (V1.5 doesn't mint — separation of
         concerns).
      3. Dual-write workspace.db via ``_register_in_workspace_db`` (T1 pattern:
         both global_projects AND entity_registry get the same UUID).
      4. Upsert ``~/.empirica/registry.yaml`` via ``api/registry.upsert_project``.
      5. POST to cortex /v1/projects/register with the local project_id in the
         payload so the (planned) adopt-local-UUID slice reconciles back to
         the canonical UUID. On cortex 5xx the local writes stay; the user
         can re-run safely.

    Exit codes:
      0 — local + cortex both shipped
      1 — local writes failed (nothing got written) OR config error
      2 — local writes shipped, cortex POST failed (re-runnable)
    """
    output_format = getattr(args, "output", "human")
    no_cortex = getattr(args, "no_cortex", False)
    skip_link = getattr(args, "skip_user_link", False)
    timeout = float(getattr(args, "timeout", 10.0))
    force_metadata_update = getattr(args, "force_metadata_update", False)

    try:
        # 1. Resolve path
        raw_path = getattr(args, "path", None) or "."
        project_path = Path(raw_path).resolve()
        project_yaml_path = project_path / ".empirica" / "project.yaml"

        if not project_path.exists():
            sys.exit(
                _project_register_error(
                    f"Path does not exist: {project_path}",
                    "Pass a path to a directory containing .empirica/project.yaml",
                    output_format,
                )
            )
        if not project_yaml_path.exists():
            sys.exit(
                _project_register_error(
                    f"No .empirica/project.yaml at {project_path}",
                    f"Run 'empirica project-init' in {project_path} first.",
                    output_format,
                )
            )

        # 2. Read project.yaml
        try:
            project_yaml = yaml.safe_load(project_yaml_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as e:
            sys.exit(
                _project_register_error(
                    f"Could not read {project_yaml_path}: {e}",
                    "Check file permissions and YAML syntax.",
                    output_format,
                )
            )

        project_id = project_yaml.get("project_id")
        if not project_id:
            sys.exit(
                _project_register_error(
                    f"{project_yaml_path} has no project_id",
                    "Run 'empirica project-init --force' to mint and persist a project_id.",
                    output_format,
                )
            )

        name = project_yaml.get("display_name") or project_yaml.get("name") or project_path.name
        description = project_yaml.get("description") or ""
        project_type = project_yaml.get("type") or "software"
        repo_url = project_yaml.get("repository") or _git_remote_for_path(project_path)

        # 3. Dual-write workspace.db (global_projects + entity_registry)
        from .workspace_init import _register_in_workspace_db

        ws_ok = _register_in_workspace_db(
            project_id=project_id,
            name=name,
            trajectory_path=str(project_path / ".empirica"),
            description=description,
            git_remote_url=repo_url,
            project_type=project_type,
        )
        if not ws_ok:
            sys.exit(
                _project_register_error(
                    "Failed to write workspace.db",
                    "Check ~/.empirica/workspace/workspace.db permissions and disk space.",
                    output_format,
                )
            )

        # 4. Upsert registry.yaml
        from empirica.api.registry import (
            dedupe_registry,
            load_registry,
            save_registry,
            upsert_project,
        )

        registry = load_registry()
        upsert_project(
            registry,
            project_id=project_id,
            slug=project_yaml.get("slug") or name,
            name=name,
            path=str(project_path),
            repo_url=repo_url,
        )
        # Reconcile a same-path slug/clone twin before persisting (path-unique).
        registry, _ = dedupe_registry(registry)
        save_registry(registry)

        local_summary = {
            "project_id": project_id,
            "name": name,
            "path": str(project_path),
            "workspace_db": True,
            "registry_yaml": True,
        }

        # 5. Cortex POST (unless --no-cortex)
        cortex_outcome = _project_register_cortex_step(
            args,
            project_id=project_id,
            name=name,
            repo_url=repo_url,
            no_cortex=no_cortex,
            skip_link=skip_link,
            force_metadata_update=force_metadata_update,
            timeout=timeout,
        )

        # 5b. Identity reconcile — converge local stores to the cortex-canonical
        # UUID when they've diverged (airtight local↔cortex identity).
        divergence = _reconcile_identity_if_diverged(args, project_path, project_id, cortex_outcome, output_format)

        # 6. Report + exit
        result = {
            "ok": True,
            "local": local_summary,
            "cortex": cortex_outcome,
        }
        if divergence:
            result["identity_divergence"] = divergence

        cortex_failed = not no_cortex and not cortex_outcome.get("skipped") and not cortex_outcome.get("ok")

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            _format_project_register_human(result)

        if cortex_failed:
            sys.exit(2)
        return

    except SystemExit:
        raise
    except Exception as e:
        handle_cli_error(e, "project-register")
        sys.exit(1)


def _project_register_cortex_step(
    args,
    *,
    project_id: str,
    name: str,
    repo_url: str | None,
    no_cortex: bool,
    skip_link: bool,
    force_metadata_update: bool,
    timeout: float,
) -> dict[str, Any]:
    """The cortex POST step of handle_project_register_command.

    Extracted to keep the outer handler under the complexity threshold.
    Returns the cortex_outcome dict the handler emits in `result['cortex']`.
    """
    if no_cortex:
        return {"skipped": True, "reason": "--no-cortex"}

    cortex_url, api_key = _resolve_cortex_config(args)
    if not (cortex_url and api_key):
        return {
            "skipped": True,
            "reason": "no cortex_url/api_key resolved (configure ~/.empirica/credentials.yaml)",
        }

    payload: dict[str, Any] = {
        "project_id": project_id,  # ask cortex to adopt the local UUID
        "name": name,
        "display_name": name,
    }
    if repo_url:
        payload["repo_url"] = repo_url
    if force_metadata_update:
        payload["force_metadata_update"] = True

    try:
        status, body = _post_project(
            cortex_url,
            CORTEX_REGISTER_PATH,
            payload,
            api_key,
            timeout,
        )
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {
            "ok": False,
            "reason": f"network: {type(e).__name__}: {e}",
        }

    cortex_outcome = _interpret_cortex_register_response(status, body, project_id)
    if cortex_outcome.get("ok") and not skip_link and cortex_outcome.get("project_id"):
        cortex_outcome["link"] = _link_user_to_project(
            cortex_url,
            cortex_outcome["project_id"],
            api_key,
            timeout,
        )
    return cortex_outcome


def _git_remote_for_path(project_path: Path) -> str | None:
    """git remote get-url origin in the given path. None on miss."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", str(project_path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        url = result.stdout.strip()
        return url if (result.returncode == 0 and url) else None
    except Exception:
        return None


# Actionable guidance when cortex rejects a register because the project_id is
# owned by someone else — the clone-and-register-with-foreign-yaml case. Cortex
# closes it server-side (register-time 400); this is the box-side prompt so a
# human's clone/retry self-corrects instead of just hitting the 400.
_OWNER_CONFLICT_HINT = (
    "This project_id belongs to another owner — likely a cloned repo carrying a foreign "
    ".empirica/project.yaml. Regenerate your own with `empirica project-init --force` "
    "(mints a fresh project_id), then re-register; or `empirica projects-unregister` the "
    "existing row if it is genuinely yours."
)


def _owner_conflict_message(body: dict[str, Any] | None) -> str:
    """Extract cortex's human message from a register-reject body."""
    if not isinstance(body, dict):
        return ""
    return str(body.get("error") or body.get("message") or body.get("detail") or "")


def _is_owner_conflict(status: int, body: dict[str, Any] | None) -> bool:
    """True when cortex rejected register because project_id is owned by another user.

    Cortex's register raises a 400 whose message says the id is "already registered
    to a different owner" (cortex 31d041b7). Matched leniently on that phrase so a
    wording tweak doesn't silently downgrade it to a generic failure.
    """
    if status != 400:
        return False
    msg = _owner_conflict_message(body).lower()
    return "different owner" in msg or "already registered to a different" in msg


def _interpret_cortex_register_response(
    status: int,
    body: dict[str, Any] | None,
    local_project_id: str,
) -> dict[str, Any]:
    """Map cortex's /v1/projects/register response to a result dict.

    Reads the project_id field from the body and flags any divergence from
    the local UUID. Divergence is NOT a hard error — extension's prop_twit75
    confirmed the V1 stance is zone-2 diagnostic (no merge button); we report
    the mismatch so callers can surface it.
    """
    if status in (200, 201):
        returned_id = (body or {}).get("project_id") or (body or {}).get("id")
        diverged = bool(returned_id and returned_id != local_project_id)
        return {
            "ok": True,
            "status": status,
            "outcome": "registered",
            "project_id": returned_id or local_project_id,
            "diverged": diverged,
            "local_project_id": local_project_id if diverged else None,
        }
    if status == 409:
        returned_id = (body or {}).get("project_id") or (body or {}).get("id")
        diverged = bool(returned_id and returned_id != local_project_id)
        return {
            "ok": True,
            "status": 409,
            "outcome": "already_registered",
            "project_id": returned_id or local_project_id,
            "diverged": diverged,
            "local_project_id": local_project_id if diverged else None,
        }
    if _is_owner_conflict(status, body):
        return {
            "ok": False,
            "status": status,
            "outcome": "owner_conflict",
            "reason": _owner_conflict_message(body) or "project_id already registered to a different owner",
            "hint": _OWNER_CONFLICT_HINT,
        }
    return {
        "ok": False,
        "status": status,
        "outcome": "failed",
        "reason": f"http {status}",
    }


def _format_project_register_human(result: dict[str, Any]) -> None:
    """Render the project-register result for human stdout."""
    local = result["local"]
    cortex = result["cortex"]

    print("✅ Local: dual-write + registry.yaml")
    print(f"   project_id:  {local['project_id']}")
    print(f"   name:        {local['name']}")
    print(f"   path:        {local['path']}")

    if cortex.get("skipped"):
        reason = cortex.get("reason") or "skipped"
        print(f"\n⊙ Cortex: skipped ({reason})")
    elif cortex.get("ok"):
        verb = "registered" if cortex.get("outcome") == "registered" else "already registered"
        print(f"\n✅ Cortex: {verb} (status={cortex.get('status')})")
        if cortex.get("diverged"):
            print(
                f"   ⚠ project_id divergence: cortex returned "
                f"{cortex.get('project_id')} (local: {cortex.get('local_project_id')})",
                file=sys.stderr,
            )
            print(
                "   This is the SER ser_542199e3 Break 1 path — the adopt-local-UUID slice "
                "in cortex closes it. Today's local stores still use the local UUID; "
                "extension renders the divergence diagnostically.",
                file=sys.stderr,
            )
        link = cortex.get("link") or {}
        if link.get("linked") is False and link.get("reason"):
            print(f"   ⚠ user-link best-effort: {link.get('reason')}", file=sys.stderr)
    else:
        print(
            f"\n❌ Cortex: {cortex.get('outcome', 'failed')} (status={cortex.get('status', 0)})",
            file=sys.stderr,
        )
        if cortex.get("reason"):
            print(f"   {cortex['reason']}", file=sys.stderr)
        if cortex.get("outcome") == "owner_conflict":
            # Re-running won't help an owner-conflict — surface the re-key path instead.
            print(f"\n   → {cortex.get('hint') or _OWNER_CONFLICT_HINT}", file=sys.stderr)
        else:
            print(
                "\n   Local writes succeeded — re-run 'empirica project register' to retry cortex.",
                file=sys.stderr,
            )
