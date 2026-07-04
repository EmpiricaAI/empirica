#!/usr/bin/env python3
"""
Empirica Session Init Hook - Auto-creates session + bootstrap for new conversations

This hook runs on new/fresh session starts (not compactions) and:
1. Creates a new Empirica session
2. Runs project-bootstrap to load context
3. Prompts the AI to run PREFLIGHT with loaded context

This ensures every conversation starts with proper epistemic baseline.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Import shared utilities from plugin lib
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from project_resolver import _find_git_root, find_project_root, get_instance_id, has_valid_db


def _proc_create_time(pid: int) -> float | None:
    """Process start time (epoch seconds) for ``pid``, or None if unavailable.

    Captured alongside pid/ppid so cockpit liveness can guard against PID
    reuse: after ``claude --resume`` or a crash the OS may recycle the old
    pid number for an unrelated process, and a bare ``os.kill(pid, 0)`` would
    read that impostor as "alive" (the source of the liveness flapping). A
    stored start time lets the liveness check reject a recycled pid that
    doesn't match. Best-effort — psutil may be absent in a minimal hook env,
    or the process may already have exited.
    """
    try:
        import psutil

        return psutil.Process(pid).create_time()
    except Exception:
        return None


def archive_stale_plans() -> list:
    """
    Archive plan files whose goals are complete.

    Scans ~/.claude/plans/ for .md files, extracts goal_id from content,
    checks if goal is complete, and moves to archive if so.

    Returns list of archived plan names.
    """
    plans_dir = Path.home() / ".claude" / "plans"
    archive_dir = plans_dir / "archive"

    if not plans_dir.exists():
        return []

    archived = []
    goal_id_pattern = re.compile(r"\*\*Goal ID:\*\*\s*`([a-f0-9-]+)`")

    for plan_file in plans_dir.glob("*.md"):
        if plan_file.name.startswith("."):
            continue

        try:
            content = plan_file.read_text()

            # Extract goal_id if present
            match = goal_id_pattern.search(content)
            if not match:
                continue

            goal_id = match.group(1)

            # Check if goal exists and is complete
            result = subprocess.run(
                ["empirica", "goals-progress", "--goal-id", goal_id, "--output", "json"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                try:
                    goal_data = json.loads(result.stdout)
                    status = goal_data.get("status", "")
                    completion_pct = goal_data.get("completion_percentage", 0)

                    # Archive if completed or all subtasks done
                    if status == "completed" or completion_pct >= 100:
                        archive_dir.mkdir(parents=True, exist_ok=True)
                        dest = archive_dir / plan_file.name
                        shutil.move(str(plan_file), str(dest))
                        archived.append(plan_file.name)
                except json.JSONDecodeError:
                    pass
        except Exception:
            continue

    return archived


def _create_empirica_session(ai_id: str, env: dict) -> tuple:
    """Run session-create CLI command and return (session_id, error).

    Returns (session_id, None) on success, (None, error_msg) on failure.
    """
    create_cmd = subprocess.run(
        ["empirica", "session-create", "--ai-id", ai_id, "--output", "json"],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    if create_cmd.returncode != 0:
        return None, f"session-create failed: {create_cmd.stderr}"
    create_output = json.loads(create_cmd.stdout)
    session_id = create_output.get("session_id")
    if not session_id:
        return None, "session-create returned no session_id"
    return session_id, None


def _run_bootstrap(session_id: str, env: dict) -> tuple:
    """Run project-bootstrap and return (bootstrap_data, project_context).

    Returns parsed bootstrap output and extracted context tuple.
    """
    bootstrap_cmd = subprocess.run(
        ["empirica", "project-bootstrap", "--session-id", session_id, "--output", "json"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    if bootstrap_cmd.returncode != 0:
        return None, None
    try:
        bootstrap_data = json.loads(bootstrap_cmd.stdout)
        project_context = {
            "goals": bootstrap_data.get("goals", [])[:3],
            "findings": bootstrap_data.get("findings", [])[:5],
            "unknowns": bootstrap_data.get("unknowns", [])[:5],
        }
        return bootstrap_data, project_context
    except json.JSONDecodeError:
        return {"raw": bootstrap_cmd.stdout[:500]}, None


def _build_cortex_sync_delta(bootstrap_data) -> dict:
    """Extract sync delta from bootstrap breadcrumbs for Cortex remote sync."""
    delta = {}
    if not isinstance(bootstrap_data, dict):
        return delta
    breadcrumbs = bootstrap_data.get("breadcrumbs", {})
    if breadcrumbs:
        delta["findings"] = [
            {"finding": f.get("finding", ""), "impact": f.get("impact", 0.5)}
            for f in breadcrumbs.get("findings", [])[:10]
        ]
        delta["unknowns"] = [{"unknown": u.get("unknown", "")} for u in breadcrumbs.get("unknowns", [])[:5]]
    return delta


def _load_user_profile() -> dict:
    """Load user profile from workflow-protocol.yaml for Cortex sync."""
    user_profile = {"name": "unknown", "role": "member", "domains": []}
    try:
        wp_path = Path.cwd() / "workflow-protocol.yaml"
        if not wp_path.exists():
            wp_path = Path.home() / ".empirica" / "workflow-protocol.yaml"
        if wp_path.exists():
            import yaml

            with open(wp_path) as wp_f:
                wp = yaml.safe_load(wp_f)
            if wp:
                up = wp.get("user_profile", {})
                user_profile["name"] = up.get("name", "unknown")
                user_profile["role"] = up.get("role", "member")
                domains = wp.get("domains", {})
                user_profile["domains"] = domains.get("expert", [])[:5]
    except Exception:
        pass
    return user_profile


def _write_cortex_cache(sync_result: dict, sync_project_id: str) -> dict:
    """Write Cortex remote cache and return sync summary dict."""
    import time as _sync_time

    _suffix = ""
    _tmux = os.environ.get("TMUX_PANE")
    if _tmux:
        _suffix = f"_tmux_{_tmux.lstrip('%')}"
    else:
        _term = os.environ.get("TERM_SESSION_ID") or os.environ.get("WINDOWID") or ""
        if _term:
            _suffix = f"_term_{_term.replace('/', '_')}"

    cache_file = Path.home() / ".empirica" / f"cortex_remote_cache{_suffix}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w") as cf:
        json.dump(
            {
                "timestamp": _sync_time.time(),
                "project_id": sync_project_id,
                "cross_domain_context": sync_result.get("cross_domain_context", []),
                "synced_artifacts": sync_result.get("synced_artifacts", 0),
            },
            cf,
        )

    return {
        "ok": True,
        "synced": sync_result.get("synced_artifacts", 0),
        "cross_domain": len(sync_result.get("cross_domain_context", [])),
    }


def _resolve_cortex_creds() -> tuple[str, str]:
    """Resolve Cortex (api_key, url) via credentials_loader — the same path
    the listener uses — so credentials.yaml is the single source of truth.

    Was raw os.environ.get before; the 2026-05-28 listener-deaf incident
    showed why env-direct reads are fragile: a revoked CORTEX_API_KEY in
    ~/.bashrc got imported into systemd --user and 401'd every poll for 10
    days. The .bashrc exports were removed in the same change, so with no env
    override get_cortex_config() falls through to the file. (Note:
    get_cortex_config remains env-first by design for fleet escape-hatch; a
    separate goal tracks hardening that precedence / warning on env-vs-file
    mismatch.) Falls back to raw env only if the loader import fails."""
    try:
        sys.path.insert(0, str(Path.home() / "empirical-ai" / "empirica"))
        from empirica.config.credentials_loader import get_credentials_loader

        cfg = get_credentials_loader().get_cortex_config() or {}
        key, url = cfg.get("api_key") or "", cfg.get("url") or ""
        if key and url:
            return key, url
    except Exception:
        pass
    return os.environ.get("CORTEX_API_KEY", ""), os.environ.get("CORTEX_REMOTE_URL", "")


def _cortex_remote_sync(result: dict) -> None:
    """Pull cross-domain context from Cortex at session start.

    Graceful degradation — if Cortex unavailable, session continues normally.
    """
    cortex_api_key, cortex_url = _resolve_cortex_creds()
    if not (cortex_api_key and cortex_url):
        return

    import urllib.request

    bootstrap_data = result.get("bootstrap_output", {})
    delta = _build_cortex_sync_delta(bootstrap_data)

    sync_project_id = ""
    if isinstance(bootstrap_data, dict):
        sync_project_id = bootstrap_data.get("project_id", "")

    user_profile = _load_user_profile()

    payload = json.dumps(
        {
            "project_id": sync_project_id,
            "user_profile_summary": user_profile,
            "delta": delta,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{cortex_url.rstrip('/')}/v1/sync",
        data=payload,
        headers={
            "Authorization": f"Bearer {cortex_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        sync_result = json.loads(resp.read())

    if sync_result.get("ok"):
        result["cortex_sync"] = _write_cortex_cache(sync_result, sync_project_id)


_PROJECT_ID_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _resolve_ai_id_for_session(project_root: str | Path | None) -> str:
    """Resolve the canonical ai_id at session-init time.

    Resolution precedence (anchor model — see docs/architecture/AI_ID_AS_ANCHOR.md):
      1. EMPIRICA_AI_ID env var (explicit override; preserved for harnesses that
         pass it in their launch config — codex/Kimi/ecodex-lab pattern)
      2. .empirica/project.yaml `ai_id` field (the canonical declared
         practitioner — what every consumer surface already reads)
      3. basename(project_root) (canonical anchor; empirica- prefix KEPT
         per the 1.11.x strict-canonical decision)
      4. 'claude-code' with stderr warning (final fallback — surfaces
         silent identity-misattribution that previously rode unseen)

    Non-CC harnesses (codex, Kimi, ecodex-lab) report as their declared
    practitioner via project.yaml. Prevents the silent 'every non-CC session
    stamped as claude-code' mesh-identity bug (ecodex prop_vwmutw7nu).
    """
    env_override = os.environ.get("EMPIRICA_AI_ID", "").strip()
    if env_override:
        return env_override

    if project_root:
        try:
            import yaml

            proj_yaml = Path(project_root) / ".empirica" / "project.yaml"
            if proj_yaml.exists():
                cfg = yaml.safe_load(proj_yaml.read_text()) or {}
                declared = cfg.get("ai_id")
                if declared:
                    return str(declared)
        except Exception as exc:
            print(
                f"session-init: project.yaml ai_id read failed ({exc}); falling back to basename",
                file=sys.stderr,
            )

        basename = Path(project_root).name
        if basename:
            return basename

    print(
        "session-init: ai_id unresolvable (no EMPIRICA_AI_ID env, no "
        "project.yaml ai_id field, no project basename) — defaulting to "
        "'claude-code'. This will misattribute mesh identity for non-CC "
        "harnesses; set EMPIRICA_AI_ID or run 'empirica project-init'.",
        file=sys.stderr,
    )
    return "claude-code"


def _heal_project_yaml_ai_id_at_init(project_root: str | None) -> None:
    """Validate-and-heal .empirica/project.yaml ai_id at session-init.

    Post-strict-canonical (1.11.x) the canonical ai_id IS the exact
    project basename — the `empirica-` prefix is KEPT. Legacy
    project.yamls written before the strict-canonical decision may
    still carry the stripped form (e.g. `ai_id: extension` instead of
    `ai_id: empirica-extension`). Cortex's strict-canonical addressing
    bounces those as `delivery_failed`.

    Heal rule (conservative):
      - if ai_id matches basename → no-op (already canonical)
      - if ai_id matches basename.removeprefix('empirica-') AND that
        differs from basename → heal to basename (known stripped form)
      - any other value → leave alone (custom provisioned, ecodex
        sandbox identity, etc. — don't second-guess)
      - ai_id absent → leave alone (project-init handles that case)

    Non-fatal — logs to stderr on issue, never blocks session boot.
    Pairs with the (empirica-)? Monitor-grep transition-compat regex
    in cockpit_commands.py; once installed practices migrate, that
    regex can be tightened to exact-match.
    """
    if not project_root:
        return
    try:
        import yaml

        project_yaml = Path(project_root) / ".empirica" / "project.yaml"
        if not project_yaml.exists():
            return
        cfg = yaml.safe_load(project_yaml.read_text()) or {}
        current = cfg.get("ai_id")
        if not current:
            return  # absent — don't auto-introduce, let project-init handle it
        canonical = Path(project_root).name
        if not canonical:
            return  # defensive — empty basename, nothing to heal toward
        if current == canonical:
            return  # already canonical
        stripped = canonical.removeprefix("empirica-")
        if stripped == canonical:
            return  # no prefix to strip — can't be a stripped-legacy value
        if current != stripped:
            return  # not a known legacy form — leave alone (custom provisioner)

        # Heal: stripped → canonical
        cfg["ai_id"] = canonical
        project_yaml.write_text(yaml.safe_dump(cfg, sort_keys=False))
        print(
            f"session-init: healed project.yaml ai_id {current!r} → {canonical!r} (stripped-prefix legacy)",
            file=sys.stderr,
        )
    except Exception as e:
        print(
            f"session-init: project.yaml ai_id heal skipped ({e})",
            file=sys.stderr,
        )


def _heal_project_yaml_project_id_at_init(project_root: str | None) -> None:
    """Validate-and-heal .empirica/project.yaml project_id at session-init.

    Legacy projects init'd before project-init switched to UUIDs have
    project_id=<slug> (e.g. "empirica", "empirica-outreach") instead
    of the canonical workspace.db UUID. This causes mismatches in any
    code that reads project.yaml directly — including doctor's
    check_project_drift (which compares against /v1/users/me/projects
    UUIDs and naturally fails on a slug).

    Heal: look up the canonical UUID via workspace.db
    global_projects.trajectory_path (same key the session-id healer
    uses) and rewrite yaml. Idempotent — no-op when yaml already has
    a UUID-shape value.

    Non-fatal — logs to stderr on issue, never blocks session boot.
    """
    if not project_root:
        return
    try:
        import yaml

        project_yaml = Path(project_root) / ".empirica" / "project.yaml"
        if not project_yaml.exists():
            return
        cfg = yaml.safe_load(project_yaml.read_text()) or {}
        current = cfg.get("project_id", "") or ""
        if _PROJECT_ID_UUID_RE.match(current):
            return  # already UUID-shaped — nothing to do

        # Look up canonical UUID via workspace.db trajectory_path
        import sqlite3

        ws_db = Path.home() / ".empirica" / "workspace" / "workspace.db"
        if not ws_db.exists():
            return
        trajectory = str(Path(project_root) / ".empirica")
        conn = sqlite3.connect(str(ws_db))
        try:
            cursor = conn.execute(
                "SELECT id FROM global_projects WHERE trajectory_path = ?",
                (trajectory,),
            )
            row = cursor.fetchone()
        finally:
            conn.close()
        if not row:
            return  # not registered — leave alone, never guess
        canonical_uuid = row[0]
        if canonical_uuid == current:
            return  # already matches (defensive — UUID regex should've caught)

        # Atomic rewrite — preserve key order via sort_keys=False
        cfg["project_id"] = canonical_uuid
        project_yaml.write_text(yaml.safe_dump(cfg, sort_keys=False))
        print(
            f"session-init: healed project.yaml project_id {current!r} → {canonical_uuid[:8]}… (slug-shape legacy)",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"session-init: project.yaml heal skipped ({type(e).__name__}: {e})", file=sys.stderr)


def _heal_session_project_id_at_init(session_id: str, project_root: str | None) -> None:
    """Validate-and-heal session.project_id at session-init boundary.

    Mirrors post-compact._auto_heal_session's stage 2 — catches the
    ghost-project_id pattern when session-create returned a session
    bound to a stale project (cross-project --resume, ambiguous
    folder_name match, tmux pane reuse). Cwd is reliable here because
    session-init already os.chdir'd to project_root before this runs.

    Non-fatal — logs to stderr on issue, never blocks session boot.
    """
    if not session_id or not project_root:
        return
    try:
        import sqlite3

        ws_db = Path.home() / ".empirica" / "workspace" / "workspace.db"
        if not ws_db.exists():
            return
        trajectory = str(Path(project_root) / ".empirica")
        conn = sqlite3.connect(str(ws_db))
        try:
            cursor = conn.execute("SELECT id FROM global_projects WHERE trajectory_path = ?", (trajectory,))
            row = cursor.fetchone()
        finally:
            conn.close()
        if not row:
            return  # path not registered — leave alone, never guess
        expected_project_id = row[0]

        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        try:
            status = db.heal_session_project_id(
                session_id=session_id,
                expected_project_id=expected_project_id,
            )
            if status == "healed":
                print(
                    f"session-init: healed session {session_id[:8]} project_id "
                    f"-> {expected_project_id[:8]} (ghost-project_id pattern)",
                    file=sys.stderr,
                )
        finally:
            db.close()
    except Exception as e:
        print(f"session-init: project_id heal skipped ({type(e).__name__}: {e})", file=sys.stderr)


def _heal_mesh_metadata_at_init(project_root: str | None) -> None:
    """Backfill mesh tenant metadata into .empirica/project.yaml at session-init.

    Projects init'd before the strict-canonical seat era have an ``ai_id`` but
    none of {org_id, tenant_slug, mesh_id_prefix, canonical_seat}. Without
    ``canonical_seat``, ``cortex_session_init`` cannot pick a seat for a
    multi-practice api_key (returns ``multi_project_no_seat``) and every mesh
    send must hand-compose ``source_claude``. This self-heals it: resolve the
    tenant metadata from cortex's ``/v1/users/me`` and persist it — which also
    composes the strict canonical 3-form seat (``org.tenant.project``) via
    ``compose_canonical_seat``.

    Read-only against cortex (a GET) — it never passes a ``seat`` to
    session_init, so it cannot trip the seat-param anti-spoof path. That
    (passing the composed seat) is the separate, cortex-phased Phase 2.

    Idempotent — the ``canonical_seat`` fast-path returns BEFORE any network
    call or heavy import, so the steady-state cost is a single yaml read.
    Non-fatal — degrades silently when cortex creds are absent (offline /
    un-onboarded) and never blocks session boot.
    """
    if not project_root:
        return
    try:
        import yaml

        project_yaml = Path(project_root) / ".empirica" / "project.yaml"
        if not project_yaml.exists():
            return
        cfg = yaml.safe_load(project_yaml.read_text()) or {}
        if not isinstance(cfg, dict):
            return
        # Idempotent fast-path: already seated → no import, no network.
        if cfg.get("canonical_seat"):
            return
        ai_id = cfg.get("ai_id")
        if not ai_id:
            return  # nothing to compose a seat from — leave alone, never guess

        # Backfill needed — resolve cortex creds.
        from empirica.config.credentials_loader import get_credentials_loader

        loader = get_credentials_loader()
        loader.reload()
        cortex_cfg = loader.get_cortex_config()
        cortex_url = cortex_cfg.get("url")
        api_key = cortex_cfg.get("api_key")
        if not (cortex_url and api_key):
            return  # offline / un-onboarded — silent no-op

        # Reuse setup-claude-code's REST + persist helpers (persist composes
        # canonical_seat). Lazy-imported only on the unhealed path.
        from empirica.cli.command_handlers.setup_claude_code import (
            _fetch_tenant_metadata,
            _persist_tenant_metadata,
        )

        metadata = _fetch_tenant_metadata(cortex_url, api_key)
        if not metadata or not metadata.get("mesh_id_prefix"):
            return  # can't compose a seat without the prefix — leave alone

        wrote = _persist_tenant_metadata(Path(project_root), **metadata)
        if wrote:
            from empirica.config.project_config_loader import compose_canonical_seat

            seat = compose_canonical_seat(
                mesh_id_prefix=metadata["mesh_id_prefix"],
                ai_id=ai_id,
            )
            print(
                f"session-init: backfilled project.yaml mesh metadata (canonical_seat={seat})",
                file=sys.stderr,
            )
    except Exception as e:
        print(f"session-init: mesh metadata backfill skipped ({type(e).__name__}: {e})", file=sys.stderr)


def create_session_and_bootstrap(ai_id: str, project_id: str | None = None) -> dict:
    """Create session + run bootstrap in sequence.

    Returns dict with session_id, bootstrap_output, error.
    Orchestrates: session creation, bootstrap, and optional Cortex sync.
    """
    result = {"session_id": None, "bootstrap_output": None, "project_context": None, "error": None}

    try:
        # Set EMPIRICA_CWD_RELIABLE=true because session-init already os.chdir'd
        # to the resolved project_root.
        env = {**os.environ, "EMPIRICA_CWD_RELIABLE": "true"}

        # Step 1: Create session
        session_id, error = _create_empirica_session(ai_id, env)
        if error:
            result["error"] = error
            return result
        result["session_id"] = session_id

        # Step 1b: Heal session.project_id if session-create's resolution
        # bound to a stale project (ghost-project_id pattern). Idempotent.
        _heal_session_project_id_at_init(session_id, os.environ.get("PWD") or os.getcwd())

        # Step 1c: Heal .empirica/project.yaml project_id if it's a slug-shape
        # legacy value (pre-UUID project-init era). Idempotent.
        _heal_project_yaml_project_id_at_init(os.environ.get("PWD") or os.getcwd())

        # Step 1d: Heal .empirica/project.yaml ai_id if it's a stripped-prefix
        # legacy value (pre-strict-canonical era, 1.11.x). Idempotent.
        _heal_project_yaml_ai_id_at_init(os.environ.get("PWD") or os.getcwd())

        # Step 1e: Backfill mesh tenant metadata (canonical_seat etc.) for
        # ai_id-only project.yamls so cortex_session_init can seat a
        # multi-practice api_key. Idempotent + cortex-read-only (no seat is
        # passed here — that's the cortex-gated Phase 2).
        _heal_mesh_metadata_at_init(os.environ.get("PWD") or os.getcwd())

        # Step 2: Run bootstrap
        bootstrap_data, project_context = _run_bootstrap(session_id, env)
        if bootstrap_data:
            result["bootstrap_output"] = bootstrap_data
        if project_context:
            result["project_context"] = project_context

        # Step 3: Cortex remote sync (optional, graceful degradation)
        try:
            _cortex_remote_sync(result)
        except Exception:
            pass  # Cortex unavailable — session continues normally

    except subprocess.TimeoutExpired:
        result["error"] = "Command timed out"
    except Exception as e:
        result["error"] = str(e)

    return result


def format_context(ctx: dict) -> str:
    """Format project context for prompt."""
    if not ctx:
        return "  (No context available)"

    parts = []

    if ctx.get("goals"):
        parts.append("**Active Goals:**")
        for g in ctx["goals"]:
            obj = g.get("objective", g) if isinstance(g, dict) else str(g)
            parts.append(f"  - {obj[:100]}")

    if ctx.get("findings"):
        parts.append("\n**Recent Findings:**")
        for f in ctx["findings"]:
            finding = f.get("finding", f) if isinstance(f, dict) else str(f)
            parts.append(f"  - {finding[:100]}")

    if ctx.get("unknowns"):
        parts.append("\n**Open Unknowns:**")
        for u in ctx["unknowns"]:
            unknown = u.get("unknown", u) if isinstance(u, dict) else str(u)
            parts.append(f"  - {unknown[:100]}")

    return "\n".join(parts) if parts else "  (No context loaded)"


def _write_instance_projects(project_path: str, claude_session_id: str, empirica_session_id: str) -> bool:
    """
    Write instance isolation files. Establishes linkage between Claude's
    conversation ID and the Empirica session — critical for project-switch,
    statusline, and sentinel to work correctly.

    Works with or without tmux. Falls back to TTY or 'default' instance.
    """
    try:
        instance_id = get_instance_id()
        instance_dir = Path.home() / ".empirica" / "instance_projects"
        instance_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        instance_file = instance_dir / f"{instance_id}.json"

        # Get TTY key if available
        tty_key = None
        try:
            tty_path = os.ttyname(sys.stdin.fileno())
            tty_key = tty_path.replace("/", "-").lstrip("-")
        except Exception:
            pass

        # Check if another Claude session owns this pane with an open transaction
        # Don't overwrite if they have active work — causes resolver warnings
        if instance_file.exists() and claude_session_id:
            try:
                with open(instance_file) as f:
                    existing = json.load(f)
                existing_claude_id = existing.get("claude_session_id")
                if existing_claude_id and existing_claude_id != claude_session_id:
                    # Different Claude session — check for open transaction
                    from project_resolver import _get_instance_suffix

                    suffix = _get_instance_suffix()
                    tx_file = Path(project_path) / ".empirica" / f"active_transaction{suffix}.json"
                    if tx_file.exists():
                        with open(tx_file) as tx_f:
                            tx_data = json.load(tx_f)
                        if tx_data.get("status") == "open" and tx_data.get("session_id") == existing.get(
                            "empirica_session_id"
                        ):
                            print(
                                f"Warning: Pane {instance_id} has open transaction from another session ({existing_claude_id[:8]}). Not overwriting.",
                                file=sys.stderr,
                            )
                            return True  # Don't overwrite, but don't fail
            except Exception:
                pass  # If check fails, proceed with overwrite

        instance_data = {
            "project_path": project_path,
            "tty_key": tty_key,
            "claude_session_id": claude_session_id,
            "empirica_session_id": empirica_session_id,
            "instance_id": instance_id,
            "timestamp": datetime.now().isoformat(),
            # PIDs captured here so `empirica instance kill <id>` can reach
            # non-tmux instances by signal. ppid is the Claude Code parent;
            # pid is this hook process (short-lived, usually dead by query time).
            "pid": os.getpid(),
            "ppid": os.getppid(),
            # Start time of the Claude parent, so liveness can reject a recycled
            # ppid number (the flapping guard). Re-stamped every SessionStart
            # (incl. `--resume`), keeping the captured pid current.
            "ppid_create_time": _proc_create_time(os.getppid()),
        }
        with open(instance_file, "w") as f:
            json.dump(instance_data, f, indent=2)
        os.chmod(instance_file, 0o600)

        # Write session-specific active_work file (with claude_session_id suffix)
        folder_name = Path(project_path).name
        active_work_data = {
            "project_path": project_path,
            "folder_name": folder_name,
            "claude_session_id": claude_session_id,
            "empirica_session_id": empirica_session_id,
            "source": "session-init",
            "timestamp": datetime.now().isoformat(),
            "timestamp_epoch": datetime.now().timestamp(),
        }

        if claude_session_id:
            active_work_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
            with open(active_work_file, "w") as f:
                json.dump(active_work_data, f, indent=2)
            os.chmod(active_work_file, 0o600)

        # Generic active_work.json only in headless mode (no terminal identity)
        # In interactive mode, instance_projects + active_work_{uuid} handle everything
        if not instance_id and not claude_session_id:
            generic_file = Path.home() / ".empirica" / "active_work.json"
            with open(generic_file, "w") as f:
                json.dump(active_work_data, f, indent=2)
            os.chmod(generic_file, 0o600)

        # Also write claude_session_id to TTY session file if available.
        # CLI commands (session-create) write TTY session but WITHOUT claude_session_id
        # because they don't have access to it. Hooks DO have it from stdin.
        # Without this, project-switch via Bash tool can't reverse-lookup instance_id.
        if tty_key and claude_session_id:
            tty_sessions_dir = Path.home() / ".empirica" / "tty_sessions"
            tty_sessions_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            tty_session_file = tty_sessions_dir / f"{tty_key}.json"

            # Read existing data (session-create may have written it already)
            tty_data = {}
            if tty_session_file.exists():
                try:
                    with open(tty_session_file) as f:
                        tty_data = json.load(f)
                except Exception:
                    pass

            # Update with claude_session_id and instance_id (preserving other fields)
            tty_data["claude_session_id"] = claude_session_id
            tty_data["instance_id"] = instance_id
            tty_data["project_path"] = project_path
            tty_data["empirica_session_id"] = empirica_session_id
            tty_data["tty_key"] = tty_key
            tty_data["timestamp"] = datetime.now().isoformat()
            tty_data["pid"] = os.getpid()
            tty_data["ppid"] = os.getppid()
            tty_data["ppid_create_time"] = _proc_create_time(os.getppid())

            with open(tty_session_file, "w") as f:
                json.dump(tty_data, f, indent=2)
            os.chmod(tty_session_file, 0o600)

        return True
    except Exception as e:
        print(f"Warning: Failed to write instance_projects: {e}", file=sys.stderr)
        return False


def _check_active_work_file(claude_session_id: str) -> dict:
    """Check active_work_{uuid} file for existing session (fastest lookup)."""
    active_work_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
    if not active_work_file.exists():
        return {}
    try:
        with open(active_work_file) as f:
            data = json.load(f)
        session_id = data.get("empirica_session_id")
        if session_id:
            return {"session_id": session_id, "source": "active_work"}
    except Exception:
        pass
    return {}


def _check_active_session_files(project_root: Path) -> dict:
    """Scan all active_session files for a matching project path."""
    for as_file in Path.home().glob(".empirica/active_session_*"):
        try:
            with open(as_file) as f:
                data = json.load(f)
            if data.get("project_path") == str(project_root):
                session_id = data.get("session_id")
                if session_id:
                    return {"session_id": session_id, "source": "active_session"}
        except Exception:
            continue
    return {}


def _find_best_orphaned_transaction(empirica_dir: Path) -> tuple:
    """Find the most recent open transaction file in .empirica dir.

    Returns (tx_file, tx_data) or (None, None) if none found.
    """
    best_tx = None
    best_mtime = 0
    for tx_file in empirica_dir.glob("active_transaction*.json"):
        try:
            mtime = tx_file.stat().st_mtime
            if mtime <= best_mtime:
                continue
            with open(tx_file) as f:
                tx_data = json.load(f)
            if tx_data.get("status") == "open":
                best_tx = (tx_file, tx_data)
                best_mtime = mtime
        except Exception:
            continue
    if best_tx:
        return best_tx
    return None, None


def _adopt_orphaned_transaction(project_root: Path) -> dict:
    """Check for orphaned open transactions and re-key them to the new instance.

    After machine/terminal/tmux restart, instance-keyed files are stale but
    transaction files survive. Adopt and re-key them.
    """
    empirica_dir = project_root / ".empirica"
    if not empirica_dir.exists():
        return {}

    try:
        from project_resolver import _get_instance_suffix

        new_suffix = _get_instance_suffix()
    except ImportError:
        new_suffix = ""

    tx_file, tx_data = _find_best_orphaned_transaction(empirica_dir)
    if not tx_file or not tx_data:
        return {}

    session_id = tx_data.get("session_id")
    if not session_id:
        return {}

    # Re-key the transaction file to the new instance suffix
    new_tx_file = empirica_dir / f"active_transaction{new_suffix}.json"
    if tx_file != new_tx_file:
        try:
            shutil.copy2(str(tx_file), str(new_tx_file))
            tx_file.unlink()
            print(
                f"Adopted orphaned transaction {tx_data.get('transaction_id', '?')[:8]}... -> new instance",
                file=sys.stderr,
            )
        except Exception:
            pass  # Adoption failure is non-fatal
    return {"session_id": session_id, "source": "orphaned_transaction"}


def _check_db_for_active_session(project_root: Path) -> dict:
    """Check DB directly via CLI for an active session."""
    try:
        result = subprocess.run(
            ["empirica", "session-list", "--output", "json", "--limit", "5"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(project_root),
        )
        if result.returncode == 0:
            sessions = json.loads(result.stdout)
            session_list = sessions.get("sessions", [])
            for s in session_list:
                if s.get("status") == "active":
                    return {"session_id": s.get("session_id"), "source": "db_active"}
    except Exception:
        pass
    return {}


def _detect_existing_session(claude_session_id: str, project_root: Path) -> dict:
    """Check if an Empirica session already exists for this conversation.

    Orchestrates a priority chain of lookups to avoid creating duplicates:
    1. active_work file (fastest)
    2. active_session files (scans all WINDOWIDs)
    3. orphaned transactions (re-keyed from previous instance)
    4. DB query (CLI fallback)

    Returns dict with session_id if found, empty dict if not.
    """
    if not claude_session_id:
        return {}

    found = _check_active_work_file(claude_session_id)
    if found:
        return found

    found = _check_active_session_files(project_root)
    if found:
        return found

    found = _adopt_orphaned_transaction(project_root)
    if found:
        return found

    return _check_db_for_active_session(project_root)


def _try_cwd_adoption() -> tuple:
    """Attempt CWD-first adoption of an open transaction on startup.

    On startup, if CWD has an open transaction, adopt it. This is the common
    case after tmux restart. Open transactions are authoritative (KNOWN_ISSUES 11.26).

    Returns (project_root, adopted: bool).
    """
    cwd_root = _find_git_root() or Path.cwd()
    if not has_valid_db(cwd_root):
        return None, False
    empirica_dir = cwd_root / ".empirica"
    try:
        for tx_candidate in sorted(
            empirica_dir.glob("active_transaction*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            try:
                with open(tx_candidate) as f:
                    tx_data = json.load(f)
                if tx_data.get("status") == "open":
                    print(f"Adopted open transaction from CWD: {tx_candidate.name}", file=sys.stderr)
                    return cwd_root, True
            except Exception:
                continue
    except Exception:
        pass
    return None, False


def _prefer_cwd_on_startup(project_root, cwd_root, suffix):
    """STARTUP EXCEPTION (ARCHITECTURE.md; KNOWN_ISSUES 11.26): on a fresh
    'startup' event the user's CWD is explicit intent and must override a
    stale instance-file binding left by a *different* conversation that
    reused this terminal/pane. Without this, pane reuse silently routes the
    new session into the previous occupant's project (the empirica-autonomy
    -> empirica-extension misroute, 2026-05-28).

    Guard: an OPEN transaction on the resolved project (this instance's
    suffix) is authoritative — a continuing loop or compact-rotation — so CWD
    never wins over it. Only override when CWD is itself a valid Empirica
    project, so launching from a non-project dir keeps the last project.

    Pure decision function (only side effect: reading the resolved project's
    transaction file). Returns the project_root to use. Tested directly by
    tests/test_open_transaction_guard.py.
    """
    try:
        if Path(cwd_root).resolve() == Path(project_root).resolve():
            return project_root  # already aligned — nothing to override
    except Exception:
        return project_root

    tx_file = Path(project_root) / ".empirica" / f"active_transaction{suffix}.json"
    try:
        if tx_file.exists():
            with open(tx_file) as f:
                if json.load(f).get("status") == "open":
                    return project_root  # open tx authoritative (11.26)
    except Exception:
        pass

    if has_valid_db(Path(cwd_root)):
        print(
            f"session-init: startup CWD intent overrides stale instance "
            f"binding ({Path(project_root).name} -> {Path(cwd_root).name})",
            file=sys.stderr,
        )
        return Path(cwd_root)
    return project_root


def _run_stale_cleanup(claude_session_id: str) -> int:
    """Opportunistic cleanup of stale instance_projects for dead tmux panes."""
    try:
        sys.path.insert(0, str(Path.home() / "empirical-ai" / "empirica"))
        from empirica.utils.session_resolver import InstanceResolver as R

        removed = R.cleanup_stale_instances()
        removed += R.cleanup_stale_files(current_claude_session_id=claude_session_id)
        return removed
    except Exception:
        return 0


def _check_version_drift() -> str:
    """Compare plugin VERSION with CLI version. Returns warning string or empty."""
    try:
        plugin_version_file = Path(__file__).parent.parent / "VERSION"
        if plugin_version_file.exists():
            plugin_ver = plugin_version_file.read_text().strip()
            from empirica import __version__ as cli_ver

            if plugin_ver != cli_ver:
                return f"Plugin v{plugin_ver} != CLI v{cli_ver}. Run: empirica setup-claude-code --force"
    except Exception:
        pass
    return ""


def _bootstrap_for_existing_session(session_id: str, project_root: Path) -> bool:
    """Run project-bootstrap for an existing/adopted session. Returns success."""
    try:
        bootstrap_cmd = subprocess.run(
            ["empirica", "project-bootstrap", "--session-id", session_id, "--output", "json"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(project_root),
        )
        return bootstrap_cmd.returncode == 0
    except Exception:
        return False


def _write_practitioner_presence(claude_session_id: str, ai_id: str, empirica_session_id: str) -> None:
    """Best-effort presence write/refresh, keyed on the durable claude_session_id.

    Stamps ``session_pid`` = ``os.getppid()`` (the Claude Code parent — this hook
    is spawned by CC). That pid is the daemon's liveness anchor: both
    ``refresh_live_presence`` and ``list_presence``'s pid-liveness override
    (``kill -0``) key off it to keep an alive-but-quiet session visible. Called on
    BOTH new-session init AND resume — on ``claude --resume`` the OS gives the
    session a fresh CC parent, so re-stamping keeps the pid current (a stale pid
    would make the resumed session read as dead and vanish from the fleet).
    Never fails session-init on a presence write.
    """
    if not claude_session_id:
        return
    try:
        subprocess.run(
            [
                "empirica",
                "practitioner",
                "write",
                "--session",
                claude_session_id,
                "--ai-id",
                ai_id,
                "--empirica-session",
                empirica_session_id,
                "--session-pid",
                str(os.getppid()),
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _handle_resume_path(claude_session_id: str, project_root: Path, ai_id: str) -> bool:
    """Handle resume path: detect existing session, update anchors, exit if found.

    Returns True if session was resumed (and sys.exit was called), False to continue.
    """
    existing = _detect_existing_session(claude_session_id, project_root)
    if not existing.get("session_id"):
        print(f"Resume: no existing session found for {project_root.name}, creating new one", file=sys.stderr)
        return False

    session_id = existing["session_id"]
    _write_instance_projects(str(project_root), claude_session_id, session_id)
    bootstrap_ok = _bootstrap_for_existing_session(session_id, project_root)

    # Re-stamp presence with the RESUMED session's fresh CC-parent pid. A resume
    # runs under a new process, so the pid captured at first launch is now dead;
    # without this re-stamp the pid-liveness readers (part 1) kill -0 a dead pid
    # and mark the resumed session gone, vanishing it from the fleet view.
    _write_practitioner_presence(claude_session_id, ai_id, session_id)

    output = {
        "ok": True,
        "session_id": session_id,
        "resumed": True,
        "bootstrap_complete": bootstrap_ok,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"""
## Session Resumed

**Session ID:** `{session_id}` (existing, from {existing.get("source", "unknown")})
**Project:** {project_root}

Anchor files updated for new terminal. Existing session and transaction state preserved.

**Note:** If you need a fresh session, run `empirica session-create --ai-id {ai_id}`.
""",
        },
    }

    print(
        f"""
Empirica: Session Resumed

Session: {session_id} (anchored to new terminal)
Project: {project_root.name}
""",
        file=sys.stderr,
    )

    print(json.dumps(output))
    sys.exit(0)


def _handle_orphan_adoption(claude_session_id: str, project_root: Path) -> bool:
    """Handle startup path: adopt orphaned transactions from previous instance.

    Returns True if adoption happened (and sys.exit was called), False to continue.
    """
    existing = _detect_existing_session(claude_session_id, project_root)
    if not (existing.get("session_id") and existing.get("source") == "orphaned_transaction"):
        return False

    session_id = existing["session_id"]
    _write_instance_projects(str(project_root), claude_session_id, session_id)
    bootstrap_ok = _bootstrap_for_existing_session(session_id, project_root)

    output = {
        "ok": True,
        "session_id": session_id,
        "adopted": True,
        "bootstrap_complete": bootstrap_ok,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"""
## Transaction Adopted After Restart

**Session ID:** `{session_id}` (adopted from orphaned transaction)
**Project:** {project_root}

Found an open transaction from a previous terminal/tmux instance.
Session and transaction state preserved -- anchor files updated for new instance.

**After reviewing context:** Run CHECK or continue your transaction.
""",
        },
    }

    print(
        f"""
Empirica: Transaction Adopted

Session: {session_id} (from orphaned transaction)
Project: {project_root.name}
Transaction state preserved
""",
        file=sys.stderr,
    )

    print(json.dumps(output))
    sys.exit(0)


def _emit_session_error(error: str, ai_id: str) -> None:
    """Emit session init error output and exit."""
    output = {
        "ok": False,
        "error": error,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"""
## Session Init Failed

Error: {error}

**Manual Setup Required:**

```bash
empirica session-create --ai-id {ai_id} --output json
empirica project-bootstrap --session-id <SESSION_ID> --output json
empirica preflight-submit - << 'EOF'
{{
  "session_id": "<SESSION_ID>",
  "task_context": "<task>",
  "vectors": {{ "know": 0.3, "uncertainty": 0.6, "context": 0.3, "engagement": 0.7 }},
  "reasoning": "New session baseline"
}}
EOF
```
""",
        },
    }
    print(json.dumps(output))
    sys.exit(0)


def _init_context_budget(session_id: str, project_context: dict) -> dict:
    """Initialize Context Budget Manager (bootloader phase).

    Returns budget summary dict, or dict with 'error' key on failure.
    """
    try:
        sys.path.insert(0, str(Path.home() / "empirical-ai" / "empirica"))
        from empirica.core.context_budget import (
            ContentType,
            ContextBudgetManager,
            ContextItem,
            InjectionChannel,
            MemoryZone,
            estimate_tokens,
        )

        manager = ContextBudgetManager(
            session_id=session_id,
            auto_subscribe=False,
        )

        # Register anchor zone
        manager.register_item(
            ContextItem(
                id="claude_md",
                zone=MemoryZone.ANCHOR,
                content_type=ContentType.SYSTEM_PROMPT,
                source="CLAUDE.md",
                channel=InjectionChannel.HOOK,
                label="CLAUDE.md system prompt + calibration",
                estimated_tokens=12000,
                epistemic_value=1.0,
                evictable=False,
            )
        )
        manager.register_item(
            ContextItem(
                id="session_state",
                zone=MemoryZone.ANCHOR,
                content_type=ContentType.CALIBRATION,
                source="session-init",
                channel=InjectionChannel.HOOK,
                label=f"Session {session_id[:8]} state",
                estimated_tokens=1000,
                epistemic_value=1.0,
                evictable=False,
            )
        )

        # Register bootstrap context as cache items
        ctx = project_context or {}
        if ctx.get("goals"):
            for i, g in enumerate(ctx["goals"]):
                obj = g.get("objective", str(g)) if isinstance(g, dict) else str(g)
                manager.register_item(
                    ContextItem(
                        id=f"boot_goal_{i}",
                        zone=MemoryZone.WORKING,
                        content_type=ContentType.GOAL,
                        source="project-bootstrap",
                        channel=InjectionChannel.HOOK,
                        label=obj[:80],
                        estimated_tokens=200,
                        epistemic_value=0.8,
                        evictable=False,
                    )
                )
        if ctx.get("findings"):
            for i, f in enumerate(ctx["findings"]):
                text = f.get("finding", str(f)) if isinstance(f, dict) else str(f)
                impact = f.get("impact", 0.5) if isinstance(f, dict) else 0.5
                manager.register_item(
                    ContextItem(
                        id=f"boot_finding_{i}",
                        zone=MemoryZone.CACHE,
                        content_type=ContentType.FINDING,
                        source="project-bootstrap",
                        channel=InjectionChannel.HOOK,
                        label=text[:80],
                        estimated_tokens=estimate_tokens(text),
                        epistemic_value=float(impact) if impact else 0.5,
                    )
                )

        manager.persist_state()
        return manager.get_inventory_summary()
    except Exception as e:
        return {"error": str(e)}


def _init_dashboard(session_id: str, ai_id: str) -> str | None:
    """Initialize System Dashboard. Returns summary string or None."""
    try:
        from empirica.core.system_dashboard import SystemDashboard

        dashboard = SystemDashboard(
            session_id=session_id,
            node_id=ai_id,
            auto_subscribe=False,
        )
        status = dashboard.get_system_status()
        return status.format_summary()
    except Exception:
        return None


def _maybe_auto_install_canonical_loops(project_root: Path) -> int:
    """Zero-touch install: queue install-pending files for each canonical
    loop when this instance is fresh on an empirica-aware project.

    Gates (all must be true):
      1. Instance has a resolvable instance_id (TMUX_PANE, WINDOWID, etc.)
      2. Project has `.empirica/` (signals empirica intent, opted in)
      3. Instance has no loops registered yet (fresh)
      4. No stamp file yet (we only auto-install once per instance)

    The stamp file is `~/.empirica/canonical_loops_installed_<instance_id>`.
    If a user uninstalls a canonical loop later, the stamp stays — they
    explicitly chose to remove it, don't re-install.

    Returns the count of canonical loops queued (0 if any gate failed).
    """
    try:
        from empirica.utils.session_resolver import get_instance_id

        instance_id = get_instance_id()
        if not instance_id:
            return 0  # gate 1: no instance_id (headless / unknown)

        empirica_dir = project_root / ".empirica"
        if not empirica_dir.is_dir():
            return 0  # gate 2: project hasn't been empirica-initialized

        from pathlib import Path as _Path

        stamp = (
            _Path.home() / ".empirica" / f"canonical_loops_installed_{instance_id.replace(':', '_').replace('/', '-')}"
        )
        if stamp.exists():
            return 0  # gate 4: already auto-installed for this instance

        from empirica.core.cockpit.loop_registry import LoopRegistry

        registry = LoopRegistry(instance_id)
        existing = registry.list_loops()
        if existing:
            # Some loops already registered manually — write stamp so we
            # don't auto-install on top of user intent next time.
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text("skipped: registry already had entries\n")
            return 0  # gate 3: not fresh

        # All gates pass — queue install-pending for each canonical loop.
        from empirica.core.cockpit.canonical_loops import CANONICAL_LOOPS
        from empirica.core.cockpit.loop_install_request import write_pending

        installed = 0
        for entry in CANONICAL_LOOPS:
            try:
                write_pending(
                    instance_id=instance_id,
                    name=entry["name"],
                    interval=entry.get("interval", "15m"),
                    description=entry.get("description", ""),
                    base_interval=entry.get("base_interval"),
                    max_interval=entry.get("max_interval"),
                    requested_by="session-init",
                    body_skill=entry.get("body_skill"),
                )
                installed += 1
            except Exception:
                pass

        if installed:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(f"installed {installed} canonical loop(s) at session-init\n")
        return installed
    except Exception:
        return 0


def _build_preflight_prompt(session_id: str, context_text: str) -> str:
    """Build the PREFLIGHT prompt for a new session."""
    return f"""
## New Session Initialized

**Session ID:** `{session_id}`
**Project context loaded via bootstrap**

### Project Context:
{context_text}

### REQUIRED: Run PREFLIGHT (Baseline)

Assess your epistemic state after reviewing the context above:

```bash
empirica preflight-submit - << 'EOF'
{{
  "session_id": "{session_id}",
  "task_context": "<what the user is asking for>",
  "vectors": {{
    "know": <0.0-1.0: How much do you know about this task/codebase?>,
    "uncertainty": <0.0-1.0: How uncertain are you?>,
    "context": <0.0-1.0: How well do you understand the current state?>,
    "engagement": <0.0-1.0: How engaged/aligned are you with the task?>
  }},
  "reasoning": "New session: <explain your starting epistemic state>"
}}
EOF
```

**After PREFLIGHT:** Before any Edit/Write/Bash, run CHECK to validate readiness.

**Operational governance:** Load `/empirica-constitution` when you hit a routing decision you're not sure about (which mechanism, which project, how to interact).
**Complex work:** Load `/epistemic-transaction` when planning multi-step work, decomposing tasks into goals, or structuring transaction sequences.
**Position-holding:** Load `/epistemic-persistence-protocol` when holding or updating a position under user pushback.
"""


def _auto_sync_plugin():
    """Best-effort: heal a stale installed CC plugin so hook fixes from a pip
    upgrade reach this session. Shells out to `empirica plugin-sync` (a no-op
    when the installed version stamp already matches the running empirica).

    This is what prevents the deploy-staleness deadlock class: without it, a
    hook fix that lands in the package on upgrade doesn't reach
    ~/.claude/plugins/local/empirica/ until a manual setup-claude-code --force,
    so running CC sessions keep loading the stale gate. Never blocks session
    start — short timeout, all failures swallowed.
    """
    try:
        subprocess.run(
            ["empirica", "plugin-sync", "--quiet"],
            capture_output=True,
            stdin=subprocess.DEVNULL,  # never consume the hook's JSON stdin
            timeout=8,
            check=False,
        )
    except Exception:
        pass


def main():
    """Main session init logic.

    Orchestrates: input parsing, project resolution, existing session detection
    (resume/adoption), new session creation, budget/dashboard init, and output.
    """
    # Auto-heal a stale installed plugin (deploy-staleness gap) before the
    # session work — so this session's hooks reflect the running empirica.
    _auto_sync_plugin()

    hook_input = {}
    try:
        hook_input = json.loads(sys.stdin.read())
    except Exception:
        pass

    claude_session_id = hook_input.get("session_id")
    event_type = hook_input.get("type", "startup")
    is_resume = event_type == "resume"

    # CWD-FIRST ADOPTION on startup
    cwd_adopted = False
    if event_type == "startup":
        cwd_root, cwd_adopted = _try_cwd_adoption()
        if cwd_adopted:
            project_root = cwd_root

    if not cwd_adopted:
        project_root = find_project_root(claude_session_id, allow_cwd_fallback=True, allow_git_root=True)
        # STARTUP EXCEPTION (ARCHITECTURE.md; KNOWN_ISSUES 11.26): a fresh
        # startup in a directory must override a stale instance binding left
        # by a different conversation that reused this pane. Open transactions
        # on the resolved project still win (guard lives in the helper).
        if event_type == "startup":
            from project_resolver import _get_instance_suffix

            project_root = _prefer_cwd_on_startup(project_root, _find_git_root() or Path.cwd(), _get_instance_suffix())

    os.chdir(project_root)
    ai_id = _resolve_ai_id_for_session(project_root)

    # Housekeeping
    _run_stale_cleanup(claude_session_id)
    archived_plans = archive_stale_plans()
    version_drift_warning = _check_version_drift()

    # RESUME PATH
    if is_resume:
        _handle_resume_path(claude_session_id, project_root, ai_id)

    # STARTUP: Orphaned transaction adoption
    if not is_resume:
        _handle_orphan_adoption(claude_session_id, project_root)

    # Create session and bootstrap
    result = create_session_and_bootstrap(ai_id)

    if result.get("session_id"):
        _write_instance_projects(str(project_root), claude_session_id, result["session_id"])
        # Register practitioner presence, keyed on the durable claude_session_id
        # (survives compaction; the empirica session_id rotates per compact
        # window). Same call re-stamps the pid on resume — see the helper.
        _write_practitioner_presence(claude_session_id, ai_id, result["session_id"])

    if result.get("error"):
        _emit_session_error(result["error"], ai_id)

    # Initialize subsystems
    session_id = result["session_id"]
    budget_summary = _init_context_budget(session_id, result.get("project_context", {}))
    dashboard_status = _init_dashboard(session_id, ai_id)

    # Zero-touch: auto-install canonical loops if this instance is fresh
    # on an empirica-aware project. One-time per instance (stamp file).
    canonical_loops_installed = _maybe_auto_install_canonical_loops(project_root)

    # Build output
    context_text = format_context(result.get("project_context"))
    prompt = _build_preflight_prompt(session_id, context_text)

    output = {
        "ok": True,
        "session_id": session_id,
        "bootstrap_complete": result.get("bootstrap_output") is not None,
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": prompt},
    }

    # User-visible message
    archive_msg = f"\nArchived {len(archived_plans)} stale plan(s)" if archived_plans else ""
    budget_msg = ""
    if budget_summary and not budget_summary.get("error"):
        budget_msg = f"\nBudget: {budget_summary.get('tokens_used', 0):,}t used / {budget_summary.get('tokens_available', 0):,}t avail ({budget_summary.get('utilization_pct', 0)}%)"
    dash_msg = f"\n{dashboard_status}" if dashboard_status else ""
    drift_msg = f"\n{version_drift_warning}" if version_drift_warning else ""
    loops_msg = (
        f"\nQueued {canonical_loops_installed} canonical loop(s) for install — "
        f"will surface on your next /loop invocation"
        if canonical_loops_installed
        else ""
    )
    print(
        f"""
Empirica: New Session Initialized

Session created: {session_id}
Project context loaded{archive_msg}{budget_msg}{dash_msg}{drift_msg}{loops_msg}

Run PREFLIGHT to establish baseline, then CHECK before actions.
""",
        file=sys.stderr,
    )

    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
