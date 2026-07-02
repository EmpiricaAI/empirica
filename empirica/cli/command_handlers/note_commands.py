"""Note command — fast scratchpad / note-to-self.

Low-friction jottings captured mid-flow and reviewed later. The complement to the
structured ``*-log`` artifacts: capture now, classify later. Pure metadata — NOT
shared, NOT embedded in Qdrant, NOT written to git notes. The durability win
(survives context compaction) is why these live in sessions.db rather than
in-context.

**Resurface scope is the PROJECT, not the transaction.** ``--list`` and the
POSTFLIGHT retrospective surface every untriaged note for the project until it is
triaged — a note left in one transaction must reappear in the next (and after a
compaction/session rotation), or "classify later" never happens. ``--clear``
marks the project's untriaged notes triaged.

Usage:
    empirica note "static query should stay static, not f-string"
    empirica note "ask cortex if gap1 overlaps R3" --tag followup
    empirica note --list      # review the project's untriaged notes (any transaction)
    empirica note --clear     # mark the project's untriaged notes triaged
"""

from __future__ import annotations

import json
import time
import uuid

from .artifact_log_commands import _resolve_artifact_context

_NOTES_DDL = """
    CREATE TABLE IF NOT EXISTS notes (
        note_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        transaction_id TEXT,
        project_id TEXT,
        ai_id TEXT,
        text TEXT NOT NULL,
        tag TEXT,
        created_at REAL NOT NULL,
        triaged INTEGER DEFAULT 0
    )
"""


def handle_note_command(args) -> dict | int | None:
    """Add a note-to-self, or review/clear with --list / --clear."""
    db = None
    try:
        ctx = _resolve_artifact_context(None, args, required_fields=None)
        db = ctx["db"]
        conn = db.conn
        output_format = ctx["output_format"]

        # Self-heal: older DBs predate the schema addition.
        conn.execute(_NOTES_DDL)

        if getattr(args, "list", False):
            return _list_notes(conn, ctx, output_format)
        if getattr(args, "clear", False):
            return _clear_notes(conn, ctx, output_format)

        text = getattr(args, "text", None) or getattr(args, "text_flag", None)
        if not text:
            msg = 'note text required — e.g. empirica note "..." (or --list / --clear)'
            if output_format == "json":
                print(json.dumps({"ok": False, "error": msg}))
                return None
            print(f"❌ {msg}")
            return 1

        tag = getattr(args, "tag", None)
        note_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO notes (note_id, session_id, transaction_id, project_id, "
            "ai_id, text, tag, created_at, triaged) VALUES (?,?,?,?,?,?,?,?,0)",
            (
                note_id,
                ctx["session_id"],
                ctx["transaction_id"],
                ctx["project_id"],
                ctx["ai_id"],
                text,
                tag,
                time.time(),
            ),
        )
        conn.commit()

        if output_format == "json":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "note_id": note_id,
                        "tag": tag,
                        "transaction_id": ctx["transaction_id"],
                    }
                )
            )
            return None
        print(f"📝 Noted{f' [{tag}]' if tag else ''}: {text}")
        print("   (surfaces at POSTFLIGHT for triage)")
        return None
    except Exception as e:
        if db is None or getattr(args, "output", "human") == "json":
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            print(f"❌ Failed to record note: {e}")
        return 1
    finally:
        if db is not None:
            db.close()


def _query_untriaged(conn, ctx):
    """Untriaged notes for the current PROJECT — resurface until triaged.

    Scoped by ``project_id``, NOT transaction/session. This is the fix for the
    stranding bug: the whole point of a note is "capture now, classify LATER",
    and "later" is almost always a different transaction (and often a different
    session — the empirica session_id rotates on compaction). Transaction/session
    scoping meant a note jotted in transaction A was invisible from B, so
    cross-transaction follow-ups never resurfaced. Project scope makes them
    durable. Falls back to session_id only when the note/context predates
    project_id.
    """
    if ctx.get("project_id"):
        rows = conn.execute(
            "SELECT note_id, text, tag, created_at FROM notes WHERE project_id = ? AND triaged = 0 ORDER BY created_at",
            (ctx["project_id"],),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT note_id, text, tag, created_at FROM notes WHERE session_id = ? AND triaged = 0 ORDER BY created_at",
            (ctx["session_id"],),
        ).fetchall()
    return rows


def _list_notes(conn, ctx, output_format) -> dict | int | None:
    rows = _query_untriaged(conn, ctx)
    notes = [{"note_id": r[0], "text": r[1], "tag": r[2], "created_at": r[3]} for r in rows]
    if output_format == "json":
        print(json.dumps({"ok": True, "count": len(notes), "notes": notes}))
        return None
    if not notes:
        print("📝 No untriaged notes.")
        return None
    print(f"📝 {len(notes)} untriaged note(s):")
    for n in notes:
        tag = f" [{n['tag']}]" if n["tag"] else ""
        print(f"   • {n['text']}{tag}")
    print("   → promote to artifacts/goals, then `empirica note --clear`.")
    return None


def _clear_notes(conn, ctx, output_format) -> dict | int | None:
    rows = _query_untriaged(conn, ctx)
    ids = [r[0] for r in rows]
    for note_id in ids:
        conn.execute("UPDATE notes SET triaged = 1 WHERE note_id = ?", (note_id,))
    conn.commit()
    if output_format == "json":
        print(json.dumps({"ok": True, "cleared": len(ids)}))
        return None
    print(f"📝 Cleared {len(ids)} note(s) (marked triaged).")
    return None
