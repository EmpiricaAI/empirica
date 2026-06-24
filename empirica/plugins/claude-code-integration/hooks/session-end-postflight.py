#!/usr/bin/env python3
"""
Empirica Session End Hook - Auto-captures POSTFLIGHT

This hook runs when a session ends and automatically captures a POSTFLIGHT
assessment based on the session's final state. Since the AI can't respond
after session end, this uses the last known vectors to complete the
PREFLIGHT→POSTFLIGHT cycle.

This ensures the learning delta is always captured, even if the AI didn't
explicitly run POSTFLIGHT.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Import shared utilities from plugin lib
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from project_resolver import find_project_root

sys.path.insert(0, str(Path.home() / "empirical-ai" / "empirica"))


def get_session_state(session_id: str) -> dict:
    """
    Get session's epistemic state for POSTFLIGHT.

    Returns:
        {
            "has_preflight": bool,
            "has_postflight": bool,
            "last_vectors": dict or None,
            "last_phase": str,
            "needs_postflight": bool
        }
    """
    try:
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        cursor = db.conn.cursor()

        # Get all reflexes for session
        cursor.execute(
            """
            SELECT phase, engagement, know, do, context,
                   clarity, coherence, signal, density,
                   state, change, completion, impact, uncertainty,
                   timestamp
            FROM reflexes
            WHERE session_id = ?
            ORDER BY timestamp DESC
        """,
            (session_id,),
        )

        rows = cursor.fetchall()
        db.close()

        if not rows:
            return {
                "has_preflight": False,
                "has_postflight": False,
                "last_vectors": None,
                "last_phase": None,
                "needs_postflight": False,
            }

        phases = [r[0] for r in rows]
        has_preflight = "PREFLIGHT" in phases
        has_postflight = "POSTFLIGHT" in phases
        last_phase = phases[0] if phases else None

        # Get vectors from most recent entry
        latest = rows[0]
        last_vectors = {
            "engagement": latest[1],
            "know": latest[2],
            "do": latest[3],
            "context": latest[4],
            "clarity": latest[5],
            "coherence": latest[6],
            "signal": latest[7],
            "density": latest[8],
            "state": latest[9],
            "change": latest[10],
            "completion": latest[11],
            "impact": latest[12],
            "uncertainty": latest[13],
        }
        # Filter None values
        last_vectors = {k: v for k, v in last_vectors.items() if v is not None}

        # Needs POSTFLIGHT if has PREFLIGHT but no POSTFLIGHT
        needs_postflight = has_preflight and not has_postflight

        return {
            "has_preflight": has_preflight,
            "has_postflight": has_postflight,
            "last_vectors": last_vectors,
            "last_phase": last_phase,
            "needs_postflight": needs_postflight,
        }

    except Exception as e:
        return {
            "has_preflight": False,
            "has_postflight": False,
            "last_vectors": None,
            "last_phase": None,
            "needs_postflight": False,
            "error": str(e),
        }


def auto_postflight(session_id: str, vectors: dict) -> dict:
    """
    Automatically submit POSTFLIGHT with final vectors.

    This completes the PREFLIGHT→POSTFLIGHT cycle so learning delta
    is captured even if AI didn't explicitly call postflight-submit.
    """
    try:
        # Build POSTFLIGHT payload
        payload = {
            "session_id": session_id,
            "vectors": vectors,
            "learnings": ["Session ended - auto-captured POSTFLIGHT"],
            "delta_summary": "Auto-captured at session end",
        }

        # Submit via CLI
        cmd = subprocess.run(
            ["empirica", "postflight-submit", "-"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=15,
        )

        if cmd.returncode == 0:
            try:
                return {"ok": True, "output": json.loads(cmd.stdout)}
            except json.JSONDecodeError:
                return {"ok": True, "output": cmd.stdout[:500]}
        else:
            return {"ok": False, "error": cmd.stderr}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_active_session() -> str | None:
    """Get active session ID."""
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        # Resolve the canonical ai_id (project.yaml → basename); fall back to
        # the legacy 'claude-code' literal only when resolution yields nothing.
        return R.latest_session_id(ai_id=R.ai_id() or "claude-code", active_only=True)
    except Exception:
        return None


def _find_session_via_active_work(claude_session_id: str) -> tuple:
    """Find empirica session_id and project_path from active_work file.

    Returns (empirica_session_id, project_path) or (None, None).
    """
    if not claude_session_id:
        return None, None
    try:
        active_work_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
        if active_work_file.exists():
            with open(active_work_file) as f:
                data = json.load(f)
            return data.get("empirica_session_id"), data.get("project_path")
    except Exception:
        pass
    return None, None


def _cleanup_session_files(claude_session_id: str | None):
    """Clean up isolation files for a completed session.

    Safety net: removes active_work for this Claude session. The primary
    cleanup happens in the POSTFLIGHT pipeline (post-test), but if the
    session ends without POSTFLIGHT, this catches it.
    """
    if not claude_session_id:
        return

    # Restore any active memory swap before cleaning up state. The swap was
    # established by post-compact when the harness CWD didn't match the
    # active transaction project; restoring it on session end returns the
    # CWD project's memory dir to its original contents. (KNOWN_ISSUES 11.28)
    try:
        from empirica.utils.memory_swap import read_manifest, restore_memory

        cwd = Path.cwd().resolve()
        manifest = read_manifest(cwd)
        if manifest:
            restore_memory(cwd)
    except Exception:
        pass

    # Clean up active_work file
    try:
        active_work_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
        if active_work_file.exists():
            active_work_file.unlink()
    except Exception:
        pass

    # Clear practitioner presence (keyed on the same durable claude_session_id).
    # Best-effort shell-out to the CLI — the hook is stdlib-only and does not
    # import the empirica package. Never fail session-end on a presence clear.
    try:
        subprocess.run(
            ["empirica", "practitioner", "clear", "--session", claude_session_id, "--output", "json"],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        pass

    # Clean up TTY session file (terminal association is gone)
    try:
        tty_sessions_dir = Path.home() / ".empirica" / "tty_sessions"
        if tty_sessions_dir.exists():
            for tty_file in tty_sessions_dir.glob("*.json"):
                try:
                    with open(tty_file) as f:
                        data = json.load(f)
                    if data.get("claude_session_id") == claude_session_id:
                        tty_file.unlink()
                        break
                except Exception:
                    continue
    except Exception:
        pass


MEMORY_AUTO_START = "<!-- empirica-auto-start -->"
MEMORY_AUTO_END = "<!-- empirica-auto-end -->"


def _get_memory_md_path() -> Path | None:
    """Find the MEMORY.md path for current project."""
    try:
        # Derive project key same way Claude Code does (absolute path with / → -)
        cwd = Path.cwd().resolve()
        project_key = str(cwd).replace("/", "-")  # /home/... → -home-...
        memory_dir = Path.home() / ".claude" / "projects" / project_key / "memory"
        if memory_dir.exists():
            return memory_dir / "MEMORY.md"
        # Try git root if cwd didn't match
        project_root = find_project_root(allow_cwd_fallback=True)
        if project_root:
            project_key = str(project_root).replace("/", "-")
            memory_dir = Path.home() / ".claude" / "projects" / project_key / "memory"
            if memory_dir.exists():
                return memory_dir / "MEMORY.md"
    except Exception:
        pass
    return None


def _resolve_project_id(session_id: str, db_path: Path) -> str | None:
    """Resolve project_id from session_id via DB lookup."""
    try:
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return row[0]
    except Exception:
        pass
    return None


def _fetch_breadcrumbs(session_id: str) -> dict:
    """Fetch recent breadcrumbs from DB for hot cache, scoped to project."""
    result = {"findings": [], "unknowns": [], "dead_ends": [], "goals": [], "mistakes": []}
    try:
        db_path = Path.cwd() / ".empirica" / "sessions" / "sessions.db"
        if not db_path.exists():
            db_path = Path.home() / ".empirica" / "sessions" / "sessions.db"
        if not db_path.exists():
            return result

        # Resolve project_id for isolation
        project_id = _resolve_project_id(session_id, db_path)

        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Build WHERE clause — filter by project_id if available
        if project_id:
            pf = "WHERE project_id = ?"
            pf_args = (project_id,)
            uf = "WHERE is_resolved = 0 AND project_id = ?"
            uf_args = (project_id,)
            df = "WHERE project_id = ?"
            df_args = (project_id,)
            gf = "WHERE is_completed = 0 AND project_id = ?"
            gf_args = (project_id,)
        else:
            pf = ""
            pf_args = ()
            uf = "WHERE is_resolved = 0"
            uf_args = ()
            df = ""
            df_args = ()
            gf = "WHERE is_completed = 0"
            gf_args = ()

        # Recent findings (last 20)
        cursor.execute(
            f"""
            SELECT finding, impact, created_timestamp FROM project_findings
            {pf} ORDER BY created_timestamp DESC LIMIT 20
        """,
            pf_args,
        )
        for row in cursor.fetchall():
            result["findings"].append({"finding": row[0], "impact": row[1] or 0.5, "created_timestamp": row[2]})

        # Open unknowns
        cursor.execute(
            f"""
            SELECT unknown, impact, created_timestamp FROM project_unknowns
            {uf} ORDER BY created_timestamp DESC LIMIT 10
        """,
            uf_args,
        )
        for row in cursor.fetchall():
            result["unknowns"].append({"unknown": row[0], "impact": row[1] or 0.5, "created_timestamp": row[2]})

        # Recent dead ends
        cursor.execute(
            f"""
            SELECT approach, why_failed, created_timestamp FROM project_dead_ends
            {df} ORDER BY created_timestamp DESC LIMIT 10
        """,
            df_args,
        )
        for row in cursor.fetchall():
            result["dead_ends"].append(
                {"approach": row[0], "why_failed": row[1], "impact": 0.7, "created_timestamp": row[2]}
            )

        # Active goals
        cursor.execute(
            f"""
            SELECT objective, status, created_timestamp FROM goals
            {gf} ORDER BY created_timestamp DESC LIMIT 10
        """,
            gf_args,
        )
        for row in cursor.fetchall():
            result["goals"].append(
                {"objective": row[0], "status": row[1] or "in_progress", "impact": 0.6, "created_timestamp": row[2]}
            )

        # Recent mistakes (no project_id column — filter by session's project via join)
        if project_id:
            cursor.execute(
                """
                SELECT m.mistake, m.created_timestamp FROM mistakes_made m
                JOIN sessions s ON m.session_id = s.session_id
                WHERE s.project_id = ?
                ORDER BY m.created_timestamp DESC LIMIT 5
            """,
                (project_id,),
            )
        else:
            cursor.execute("""
                SELECT mistake, created_timestamp FROM mistakes_made
                ORDER BY created_timestamp DESC LIMIT 5
            """)
        for row in cursor.fetchall():
            result["mistakes"].append({"mistake": row[0], "impact": 0.7, "created_timestamp": row[1]})

        conn.close()
    except Exception:
        pass
    return result


def update_memory_hot_cache(session_id: str):
    """
    Update Claude Code's MEMORY.md with epistemically-ranked artifacts.

    Preserves manual content. Auto-generated section is delimited by markers.
    Keeps total auto section under ~100 lines to leave room for manual notes.
    """
    memory_path = _get_memory_md_path()
    if not memory_path:
        return

    # Fetch and rank breadcrumbs
    breadcrumbs = _fetch_breadcrumbs(session_id)

    # Import summarizer
    sys.path.insert(0, str(Path(__file__).parent))
    from epistemic_summarizer import format_epistemic_focus

    focus = format_epistemic_focus(
        findings=breadcrumbs["findings"],
        unknowns=breadcrumbs["unknowns"],
        dead_ends=breadcrumbs["dead_ends"],
        goals=breadcrumbs["goals"],
        mistakes=breadcrumbs["mistakes"],
        max_items=12,
        session_id=session_id,
    )

    auto_section = f"\n{MEMORY_AUTO_START}\n{focus}\n{MEMORY_AUTO_END}\n"

    # Read existing MEMORY.md
    if memory_path.exists():
        existing = memory_path.read_text()
    else:
        existing = "# Empirica Project Memory\n"

    # Replace or append auto section
    if MEMORY_AUTO_START in existing and MEMORY_AUTO_END in existing:
        # Replace existing auto section
        start_idx = existing.index(MEMORY_AUTO_START)
        end_idx = existing.index(MEMORY_AUTO_END) + len(MEMORY_AUTO_END)
        # Include any trailing newline
        if end_idx < len(existing) and existing[end_idx] == "\n":
            end_idx += 1
        updated = existing[:start_idx] + auto_section.lstrip("\n") + existing[end_idx:]
    else:
        # Append at end
        updated = existing.rstrip("\n") + "\n" + auto_section

    # Write back
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(updated)


def _auto_embed_project(session_id: str):
    """Auto-sync epistemic artifacts + code API to Qdrant at session end.

    Runs project-embed via CLI subprocess to avoid import conflicts.
    Uses the project_id resolved from the session.
    """
    try:
        db_path = Path.cwd() / ".empirica" / "sessions" / "sessions.db"
        if not db_path.exists():
            return

        project_id = _resolve_project_id(session_id, db_path)
        if not project_id:
            return

        # Run project-embed with timeout — this is incremental and fast
        subprocess.run(
            ["empirica", "project-embed", "--project-id", project_id, "--output", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        pass  # Best-effort — never fail session end for embedding


def _resolve_session_and_project(claude_session_id):
    """Resolve session ID and project path, set CWD.

    Returns (session_id, claude_session_id) tuple.
    Session ID may be None if no active session found.
    """
    session_id = None
    project_path = None

    if claude_session_id:
        session_id, project_path = _find_session_via_active_work(claude_session_id)

    if project_path:
        os.chdir(project_path)
    else:
        project_root = find_project_root(allow_cwd_fallback=True)
        os.chdir(project_root)

    if not session_id:
        session_id = get_active_session()

    return session_id


def _skip_exit(claude_session_id, reason, session_id=None, state=None):
    """Clean up and exit with a skip result."""
    _cleanup_session_files(claude_session_id)
    output = {"ok": True, "skipped": True, "reason": reason, "cleanup": "active_work purged"}
    if session_id:
        output["session_id"] = session_id
    if state:
        output["state"] = state
    print(json.dumps(output))
    sys.exit(0)


def _resolve_cortex_creds() -> tuple[str, str]:
    """Resolve Cortex (api_key, url) via credentials_loader so credentials.yaml
    is the single source of truth (mirrors session-init). Was raw os.environ
    before; a stale ~/.bashrc CORTEX_API_KEY shadowing the file is what caused
    the 2026-05-28 10-day listener-deaf incident. Falls back to raw env only
    if the loader import fails."""
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


def _run_postflight_cortex_sync(vectors):
    """Push final artifacts to Cortex remote. Fire-and-forget."""
    cortex_api_key, cortex_url = _resolve_cortex_creds()
    if not cortex_api_key or not cortex_url:
        return

    import urllib.request

    push_delta = {}
    if vectors:
        push_delta["session_vectors"] = vectors

    push_project_id = ""
    try:
        project_root = find_project_root()
        if project_root:
            project_yaml = project_root / ".empirica" / "project.yaml"
            if project_yaml.exists():
                with open(project_yaml) as f:
                    for line in f:
                        if line.startswith("project_id:"):
                            push_project_id = line.split(":", 1)[1].strip()
                            break
    except Exception:
        pass

    payload = json.dumps(
        {
            "project_id": push_project_id,
            "delta": push_delta,
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
    urllib.request.urlopen(req, timeout=5)


def main():
    """Main session end logic."""
    hook_input = {}
    try:
        hook_input = json.loads(sys.stdin.read())
    except Exception:
        pass

    claude_session_id = hook_input.get("session_id")
    session_id = _resolve_session_and_project(claude_session_id)

    if not session_id:
        _skip_exit(claude_session_id, "No active Empirica session")

    state = get_session_state(session_id)

    if not state.get("needs_postflight"):
        reason = "POSTFLIGHT already exists" if state.get("has_postflight") else "No PREFLIGHT found"
        _skip_exit(claude_session_id, reason, session_id=session_id, state=state)

    vectors = state.get("last_vectors", {})
    if not vectors:
        _skip_exit(claude_session_id, "No vectors available for auto-POSTFLIGHT", session_id=session_id)

    # Boost completion since session is ending
    vectors["completion"] = max(vectors.get("completion", 0.5), 0.7)

    result = auto_postflight(session_id, vectors)

    # Non-critical post-POSTFLIGHT tasks
    try:
        from empirica.core.memory_manager import (
            demote_stale_memories,
            enforce_memory_md_cap,
            promote_eidetic_to_memory,
            resolve_project_id,
            update_hot_cache,
        )

        update_hot_cache(session_id)
        _pid = resolve_project_id(session_id)
        if _pid:
            promote_eidetic_to_memory(project_id=_pid)
        demote_stale_memories()
        enforce_memory_md_cap()
    except Exception:
        pass  # memory management is non-critical at session end
    try:
        _auto_embed_project(session_id)
    except Exception:
        pass

    _cleanup_session_files(claude_session_id)

    if result.get("ok"):
        print(
            f"""
📊 Empirica: Auto-POSTFLIGHT captured

Session: {session_id}
Vectors: know={vectors.get("know", "N/A")}, uncertainty={vectors.get("uncertainty", "N/A")}
Learning delta will be calculated from PREFLIGHT baseline.
🧹 Session files cleaned up.
""",
            file=sys.stderr,
        )

    try:
        _run_postflight_cortex_sync(vectors)
    except Exception:
        pass  # Cortex unavailable — session ends normally

    output = {
        "ok": result.get("ok", False),
        "session_id": session_id,
        "auto_postflight": True,
        "vectors_used": vectors,
        "result": result,
        "cleanup": "active_work purged",
    }

    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
