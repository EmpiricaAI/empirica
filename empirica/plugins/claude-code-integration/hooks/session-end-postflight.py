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


def _launch_cwd() -> Path | None:
    """The directory this hook was started in, or None if it no longer exists.

    Read once, at import, BEFORE main() moves to the resolved practice: the memory swap is keyed by the harness's directory,
    and after the chdir the cwd is the active project, whose own memory dir holds no manifest."""
    try:
        return Path(os.getcwd())
    except OSError:
        return None


_LAUNCH_CWD = _launch_cwd()


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

        # Each vector takes its most recent RECORDED value. The newest row is usually a CHECK, which supplies only some of the 13
        # (the rest are NULL = not assessed), and the auto-POSTFLIGHT built from it must not go out sparse: a POSTFLIGHT row full of
        # NULLs breaks readers that rely on it being complete. Rows are newest first, so the first non-NULL per column wins.
        names = [
            "engagement", "know", "do", "context", "clarity", "coherence", "signal",
            "density", "state", "change", "completion", "impact", "uncertainty",
        ]  # fmt: skip
        last_vectors = {}
        for row in rows:
            for offset, name in enumerate(names, start=1):
                if name not in last_vectors and row[offset] is not None:
                    last_vectors[name] = row[offset]

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
        # Only keys postflight-submit accepts: it refuses any other top-level key (GH #409) before recording anything. This
        # payload used to carry `learnings` and `delta_summary`, so the auto-POSTFLIGHT failed on every run.
        payload = {
            "session_id": session_id,
            "vectors": vectors,
            "reasoning": "Session ended - auto-captured POSTFLIGHT from the last recorded vectors (completion floored at 0.7)",
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
        from empirica.utils.memory_swap import restore_if_swapped

        if _LAUNCH_CWD is None:
            print(
                "memory restore skipped: the launch directory is gone, so the swapped project is unknown",
                file=sys.stderr,
            )
            restored = None
        else:
            from empirica.utils.memory_swap import current_harness

            restored = restore_if_swapped(_LAUNCH_CWD, caller_harness=current_harness())
        if restored and restored.get("ok") and (restored.get("written_back") or restored.get("write_back_conflicts")):
            print(f"memory restore: {restored.get('message', '')}", file=sys.stderr)
        if restored and not restored.get("ok", False):
            print(f"memory restore failed: {restored.get('action')}: {restored.get('message', '')}", file=sys.stderr)
    except Exception as e:
        print(f"memory restore failed: {e}", file=sys.stderr)

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

        # Fire-and-forget: detach the embed so session end never blocks on it.
        # project-embed is incremental (O(new) — see _rehydrate_eidetic), so on
        # steady state it finishes fast; but the local embedder can still take
        # >30s on a cold/large run, and harnesses that cap SessionEnd hooks
        # (e.g. codex at 3s) would otherwise kill it mid-run every session so it
        # NEVER completes. start_new_session detaches it from the hook's process
        # group so it survives the hook exiting and runs to completion in the bg.
        subprocess.Popen(
            ["empirica", "project-embed", "--project-id", project_id, "--output", "json"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
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
        from empirica.config.credentials_loader import get_credentials_loader

        # Either credential. An api_key-only read meant this never ran on a seat
        # authenticated by `empirica auth login` — silently, since the caller
        # returns early with no log.
        from empirica.core.auth.cortex_oauth import cortex_bearer

        resolved = cortex_bearer(get_credentials_loader())
        key, url = resolved.get("bearer") or "", resolved.get("url") or ""
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

    # Non-critical post-POSTFLIGHT tasks. Claude Code's memory folder is left to Claude Code (goal ea3335ec).
    try:
        _auto_embed_project(session_id)
    except Exception:
        pass

    _cleanup_session_files(claude_session_id)

    if not result.get("ok"):
        # Session end is never blocked, but a POSTFLIGHT that did not land must not look like one that did.
        print(
            f"Empirica: auto-POSTFLIGHT FAILED for session {session_id}: {str(result.get('error', 'unknown error')).strip()[:300]}",
            file=sys.stderr,
        )
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
