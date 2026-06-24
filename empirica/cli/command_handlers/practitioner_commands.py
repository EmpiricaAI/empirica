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
        from empirica.utils.session_resolver import get_instance_id

        cc = args.session
        ai_id = getattr(args, "ai_id", None) or R.ai_id() or "unknown"
        location = getattr(args, "location", None) or get_instance_id()
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
            )
        except ValueError as ve:
            return _emit_user_error(args, str(ve))
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


_DISPATCH = {
    "write": handle_practitioner_write_command,
    "clear": handle_practitioner_clear_command,
    "list": handle_practitioner_list_command,
}


def handle_practitioner_group_command(args) -> int:
    """`empirica practitioner <write|clear|list>` dispatcher."""
    action = getattr(args, "practitioner_action", None)
    handler = _DISPATCH.get(action)
    if handler is None:
        sys.stdout.write("usage: empirica practitioner <write|clear|list>\n")
        return 2
    return handler(args)
