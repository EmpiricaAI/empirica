"""
Unified resolve command — auto-detects artifact type from ID.

Usage:
  empirica resolve <artifact-id> [--resolved-by "reason"]

Searches across all artifact tables (findings, unknowns, dead-ends,
mistakes, assumptions, decisions) to find the artifact, then applies
the appropriate resolution action.
"""

from __future__ import annotations

import json
import sys

from empirica.cli.cli_utils import handle_cli_error, printed

# Tables to search, in priority order
ARTIFACT_TABLES = [
    ("project_unknowns", "unknown", "is_resolved", "unknown"),
    ("project_findings", "finding", None, "finding"),
    ("project_dead_ends", "dead_end", None, "approach"),
    ("mistakes_made", "mistake", None, "mistake"),
    ("assumptions", "assumption", None, "assumption"),
    ("decisions", "decision", None, "choice"),
]


def handle_resolve_command(args):
    """Resolve any artifact by ID — auto-detects type."""
    try:
        artifact_id = args.artifact_id
        resolved_by = getattr(args, "resolved_by", None) or "Resolved via unified resolve command"
        output = getattr(args, "output", "json")

        if not artifact_id:
            print(json.dumps({"ok": False, "error": "artifact_id is required"}))
            sys.exit(1)

        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()

        cursor = db.conn.cursor()

        # Search all artifact tables for the ID (prefix match)
        found_table = None
        found_type = None
        found_row = None
        found_text_col = None
        found_resolve_col = None

        for table, art_type, resolve_col, text_col in ARTIFACT_TABLES:
            try:
                cursor.execute(
                    f"SELECT * FROM {table} WHERE id LIKE ? LIMIT 1",
                    (f"{artifact_id}%",),
                )
                row = cursor.fetchone()
                if row:
                    found_table = table
                    found_type = art_type
                    found_row = row
                    found_text_col = text_col
                    found_resolve_col = resolve_col
                    # Get column names
                    col_names = [desc[0] for desc in cursor.description]
                    found_row = dict(zip(col_names, row))
                    break
            except Exception:
                continue

        if not found_row:
            msg = f"Artifact '{artifact_id}' not found in any table"
            if output == "json":
                print(json.dumps({"ok": False, "error": msg}))
            else:
                print(f"  {msg}")
            db.close()
            return 1

        full_id = found_row.get("id", artifact_id)

        # Apply resolution based on type
        if found_type == "unknown" and found_resolve_col:
            cursor.execute(
                f"UPDATE {found_table} SET {found_resolve_col} = 1 WHERE id = ?",
                (full_id,),
            )
            db.conn.commit()
            action = "resolved"
        else:
            action = "found (no resolve action for this type)"

        artifact_text = found_row.get(found_text_col, "")[:100] if found_text_col else ""

        result = {
            "ok": True,
            "artifact_id": full_id,
            "type": found_type,
            "table": found_table,
            "action": action,
            "text": artifact_text,
            "resolved_by": resolved_by if action == "resolved" else None,
        }

        if output == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"  {found_type}: {artifact_text}")
            print(f"  Action: {action}")

        db.close()
        return printed(result, args)

    except Exception as e:
        handle_cli_error(e, "Resolve", getattr(args, "verbose", False))
        return 1  # handle_cli_error has printed it
