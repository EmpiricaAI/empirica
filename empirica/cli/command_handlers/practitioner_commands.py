"""Practitioner presence CLI — the bridge the session hooks shell out to.

The plugin hooks are stdlib-only and shell out to the ``empirica`` CLI (they do
not import the empirica package), so presence is written/cleared via these verbs
rather than an in-hook import:

- ``empirica practitioner write --session <claude_session_id>`` — register +
  heartbeat (resolves practice ai_id / location / empirica session /
  active-transaction from the running context).
- ``empirica practitioner clear --session <claude_session_id>`` — session-end.
- ``empirica practitioner list [--practice <ai_id>]`` — the resolver surface
  (practice → live practitioners), for the cockpit/autonomy + debugging.

Presence is keyed on the DURABLE ``claude_session_id`` (see
empirica.core.practitioner_presence) — the empirica session id rotates per
compact window, so it rides along as a churning attribute.
"""

from __future__ import annotations

import json
import sys

from ..cli_utils import handle_cli_error


def _emit_user_error(args, message: str, error: str = "invalid_argument") -> int:
    output = getattr(args, "output", "human")
    payload = {"ok": False, "error": error, "message": message}
    if output == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(f"❌ {message}", file=sys.stderr)
    return 1


def handle_practitioner_write_command(args) -> int:
    """practitioner write — upsert this practitioner's presence (register + heartbeat)."""
    try:
        from empirica.core.practitioner_presence import write_presence
        from empirica.utils.session_resolver import InstanceResolver as R
        from empirica.utils.session_resolver import detect_current_location

        cc = args.session
        ai_id = getattr(args, "ai_id", None) or R.ai_id() or "unknown"
        # LOCATION, not identity: the physical terminal/pane, re-resolved live each
        # per-turn write (context-shift-tracker). Uses detect_current_location (not
        # get_instance_id) so a practitioner whose EMPIRICA_INSTANCE_ID carries a
        # durable identity — e.g. an ecodex thread_id — records its real tmux/TTY
        # location, not its identity. The durable key stays claude_session_id (cc).
        location = getattr(args, "location", None) or detect_current_location()
        empirica_sid = getattr(args, "empirica_session", None) or R.session_id(claude_session_id=cc)
        tx = getattr(args, "active_transaction", None) or R.transaction_id(claude_session_id=cc)
        status = getattr(args, "status", "active")
        try:
            rec = write_presence(
                cc,
                practice_ai_id=ai_id,
                location=location,
                status=status,
                pending_question=getattr(args, "pending_question", None),
                active_transaction_id=tx,
                empirica_session_id=empirica_sid,
                session_pid=getattr(args, "session_pid", None),
            )
        except ValueError as ve:
            return _emit_user_error(args, str(ve))
        # B4: mirror the DURABLE half into the ERM — the practitioner entity +
        # occupies→practice edge. Best-effort (never fail the live presence write
        # on it); skipped when the practice ai_id is unknown. Idempotent upsert, so
        # the per-turn write is self-healing.
        if ai_id and ai_id != "unknown":
            try:
                from empirica.data.repositories.workspace_db import WorkspaceDBRepository

                with WorkspaceDBRepository.open() as repo:
                    repo.upsert_practitioner_entity(cc, ai_id)
            except Exception:
                pass
        if getattr(args, "output", "human") == "json":
            print(json.dumps({"ok": True, "presence": rec}, indent=2, default=str))
        else:
            print(f"📍 presence: {cc[:12]} @ {ai_id} ({status})")
        return 0
    except Exception as e:
        handle_cli_error(e, "practitioner write", getattr(args, "verbose", False))
        return 1


def handle_practitioner_clear_command(args) -> int:
    """practitioner clear — remove this practitioner's presence (session-end)."""
    try:
        from empirica.core.practitioner_presence import clear_presence

        removed = clear_presence(args.session)
        if getattr(args, "output", "human") == "json":
            print(json.dumps({"ok": True, "removed": removed}))
        else:
            print(f"🧹 presence cleared: {args.session[:12]}" if removed else "(no presence to clear)")
        return 0
    except Exception as e:
        handle_cli_error(e, "practitioner clear", getattr(args, "verbose", False))
        return 1


def handle_practitioner_list_command(args) -> int:
    """practitioner list — the resolver: a practice's live practitioners."""
    try:
        from empirica.core.practitioner_presence import list_presence

        rows = list_presence(getattr(args, "practice", None), include_stale=getattr(args, "include_stale", False))
        if getattr(args, "output", "human") == "json":
            print(json.dumps({"ok": True, "count": len(rows), "practitioners": rows}, indent=2, default=str))
            return 0
        if not rows:
            print("(no live practitioners)")
            return 0
        for r in rows:
            stale = " STALE" if r.get("stale") else ""
            pq = f"  ?{r['pending_question']}" if r.get("pending_question") else ""
            print(
                f"  {r.get('practice_ai_id', '?'):28} {(r.get('claude_session_id') or '?')[:12]} "
                f"{r.get('status', '?'):8} @ {r.get('location') or '-'}{stale}{pq}"
            )
        return 0
    except Exception as e:
        handle_cli_error(e, "practitioner list", getattr(args, "verbose", False))
        return 1


def handle_practitioner_heartbeat_command(args) -> int:
    """practitioner heartbeat — push local presence to cortex (one session or all).

    Reads the local presence store and forwards each record to cortex's
    POST /v1/practitioners/heartbeat. With --session, emits just that
    practitioner; otherwise emits every local non-stale practitioner. This is
    the one-shot the persistent service / loop body triggers on a cadence.
    """
    try:
        from empirica.core.loop_scheduler.practitioner_heartbeat import (
            emit_practitioner_heartbeat,
        )
        from empirica.core.practitioner_presence import list_presence, read_presence

        session = getattr(args, "session", None)
        if session:
            rec = read_presence(session)
            records = [rec] if rec else []
        else:
            records = list_presence(include_stale=getattr(args, "include_stale", False))

        results = []
        for rec in records:
            code = emit_practitioner_heartbeat(rec)
            results.append({"session_id": rec.get("claude_session_id"), "status_code": code})

        if getattr(args, "output", "human") == "json":
            print(json.dumps({"ok": True, "emitted": len(results), "results": results}, indent=2, default=str))
            return 0
        if not results:
            print("(no local presence to emit)" if not session else f"(no local presence for {session[:12]})")
            return 0
        for r in results:
            code = r["status_code"]
            label = {0: "skipped (no cortex creds / unmappable)", 200: "ok", -1: "network error"}.get(
                code, f"http {code}"
            )
            print(f"  📡 {(r['session_id'] or '?')[:12]} → {label}")
        return 0
    except Exception as e:
        handle_cli_error(e, "practitioner heartbeat", getattr(args, "verbose", False))
        return 1


_DISPATCH = {
    "write": handle_practitioner_write_command,
    "clear": handle_practitioner_clear_command,
    "list": handle_practitioner_list_command,
    "heartbeat": handle_practitioner_heartbeat_command,
}


def handle_practitioner_group_command(args) -> int:
    """`empirica practitioner <write|clear|list|heartbeat>` dispatcher."""
    action = getattr(args, "practitioner_action", None)
    handler = _DISPATCH.get(action)
    if handler is None:
        sys.stdout.write("usage: empirica practitioner <write|clear|list|heartbeat>\n")
        return 2
    return handler(args)
