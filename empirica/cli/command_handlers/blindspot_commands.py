"""`empirica blindspot-scan` — dry-run blindspot detection.

Surfaces predicted unknown-unknowns for a session: stated goals/tasks with no
covering artifact and no acknowledging unknown (the intent-gap signal). DRY-RUN —
reports only, wired to nobody (no CHECK nudge, no persistence) until the surfacing
transactions. Human + JSON. Defaults to the current session.
"""

from __future__ import annotations

import json


def _read_intent_gaps(db, session_id: str, active_only: bool = True) -> list[dict]:
    """Read the session's goal tree and detect intent-gap candidates. Degrades to
    [] on any read error — a scan must never raise. ``active_only`` excludes dormant
    ``planned`` goals (backlog, not a blindspot in active work)."""
    try:
        from empirica.core.blindspots import detect_intent_gaps

        tree = db.goals.get_goal_tree(session_id)
        return detect_intent_gaps(tree, active_only=active_only)
    except Exception:
        return []


def handle_blindspot_scan_command(args) -> None:
    """Render the dry-run blindspot scan (human or JSON)."""
    from empirica.data.session_database import SessionDatabase
    from empirica.utils.session_resolver import InstanceResolver as R

    session_id = getattr(args, "session_id", None) or R.session_id()
    if not session_id:
        msg = {"ok": False, "error": "no session_id (pass --session-id or run inside an active session)"}
        print(json.dumps(msg) if getattr(args, "output", "human") == "json" else f"⚠️  {msg['error']}")
        return

    active_only = not getattr(args, "include_planned", False)
    db = SessionDatabase()
    try:
        gaps = _read_intent_gaps(db, session_id, active_only=active_only)
    finally:
        db.close()

    if getattr(args, "output", "human") == "json":
        print(json.dumps({"session_id": session_id, "intent_gaps": gaps, "count": len(gaps)}, indent=2))
        return

    scope = "active goals" if active_only else "all goals (incl. planned backlog)"
    print(f"\n🔦 Blindspot Scan — intent gaps ({scope}, dry-run)")
    print("━" * 60)
    if not gaps:
        print("  no intent gaps — every open task carries a finding, unknown, or attempt")
        print("━" * 60)
        return
    print(f"{len(gaps)} predicted blindspot(s) — stated intent with no coverage and no acknowledged unknown:\n")
    for g in gaps:
        print(f"  • {g['intent']}")
        print(f"      under goal: {g['objective']}")
        print(f"      {g['reason']}")
    print("━" * 60)
    print("Dry-run only — surface an `unknown` to acknowledge one, or dismiss it.")
    print("━" * 60)
