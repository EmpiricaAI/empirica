"""source-review — record a human/AI verdict on a logged source.

The REVIEW half of David's source-lifecycle triad: ``sources-check`` DETECTS a
stale/broken source (automated probing), ``source-update`` RE-FETCHES it (the
ACT), and ``source-review`` records a *judgment* — an attestation that a source
is still valid/relevant, or a verdict that routes to the next lifecycle verb.

It appends a ``reviewed`` event to ``lifecycle_audit_log`` (migration 044, the
full history) AND stamps the latest verdict into the queryable
``last_reviewed_at`` / ``review_verdict`` columns (migration 054), so downstream
hygiene (sanctify / check) can surface unreviewed or stale-review sources
without parsing the JSON log. Mirrors the source-update handler's prefix-resolve
+ audit-append + column-update shape.
"""

from __future__ import annotations

import json
import time

from empirica.cli.cli_utils import handle_cli_error

# The verdict a reviewer renders. Each routes to a next lifecycle action:
#   valid       → keep (no action)
#   stale       → content outdated → run ``source-update``
#   superseded  → replaced by a better source → ``source-archive --reason superseded``
#   irrelevant  → no longer relevant to the project → ``source-archive``
_VALID_VERDICTS = ("valid", "stale", "superseded", "irrelevant")

# What to do next, keyed by verdict — surfaced in the result so review flows
# into the rest of the triad instead of dead-ending.
_NEXT_ACTION = {
    "valid": None,
    "stale": "source-update --source-id <id>  (re-fetch to refresh content)",
    "superseded": "source-archive --source-id <id> --reason superseded",
    "irrelevant": "source-archive --source-id <id>",
}


def _build_review_event(verdict: str, note: str | None, reviewer: str | None, at: float) -> dict:
    """Construct the ``reviewed`` lifecycle_audit_log event (pure, testable)."""
    return {
        "event": "reviewed",
        "at": at,
        "verdict": verdict,
        "note": note,
        "reviewer": reviewer,
    }


def handle_source_review_command(args):
    """source-review --source-id <id> --verdict <v> — record a review verdict."""
    db = None
    try:
        from empirica.data.session_database import SessionDatabase

        source_id = args.source_id
        verdict = args.verdict
        note = getattr(args, "note", None)
        reviewer = getattr(args, "reviewer", None)
        output_format = getattr(args, "output", "human")

        # Defensive — argparse `choices` already constrains this, but never trust
        # the caller (MCP / programmatic paths bypass the parser).
        if verdict not in _VALID_VERDICTS:
            print(
                json.dumps({"ok": False, "error": f"Invalid verdict '{verdict}'. One of: {', '.join(_VALID_VERDICTS)}"})
            )
            return 1

        db = SessionDatabase()
        cur = db.conn.cursor()
        # Resolve full id from a prefix (matches source-update / source-archive UX).
        cur.execute(
            "SELECT id, title, lifecycle_audit_log FROM epistemic_sources WHERE id = ? OR id LIKE ? LIMIT 2",
            (source_id, f"{source_id}%"),
        )
        rows = cur.fetchall()
        if not rows:
            print(json.dumps({"ok": False, "error": f"Source not found: {source_id}"}))
            return 1
        if len(rows) > 1:
            print(json.dumps({"ok": False, "error": f"Source ID '{source_id}' is ambiguous — use the full UUID."}))
            return 1
        full_id, title, audit_json = rows[0]

        at = time.time()
        audit = json.loads(audit_json) if audit_json else []
        if not isinstance(audit, list):
            audit = []
        audit.append(_build_review_event(verdict, note, reviewer, at))

        cur.execute(
            "UPDATE epistemic_sources SET last_reviewed_at = ?, review_verdict = ?, "
            "lifecycle_audit_log = ? WHERE id = ?",
            (at, verdict, json.dumps(audit), full_id),
        )
        db.conn.commit()

        next_action = _NEXT_ACTION.get(verdict)
        result = {
            "ok": True,
            "source_id": full_id,
            "title": title,
            "verdict": verdict,
            "reviewer": reviewer,
            "next_action": next_action,
        }
        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            tail = f" → next: {next_action}" if next_action else ""
            print(f"source-review: {full_id[:8]} '{title}' — verdict={verdict}{tail}")
        return 0
    except Exception as e:
        handle_cli_error(e, "Source review", getattr(args, "verbose", False))
        return 1
    finally:
        if db:
            db.close()
