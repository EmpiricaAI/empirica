"""A rebuild reads an unknown's resolution whichever key its note uses (1.16.0 sweep, pass 2).

Live resolve paths write `resolved: true` into an unknown's note; `doctor --reconcile-notes` writes `is_resolved: true`.
The rebuild read only `resolved`, so every unknown the reconcile had stamped came back open (244 on this practice).
"""

from __future__ import annotations

from empirica.cli.command_handlers.sync_commands import _rebuild_apply_resolution
from empirica.data.session_database import SessionDatabase


def test_an_unknown_stamped_is_resolved_by_the_reconcile_comes_back_resolved(tmp_path):
    db = SessionDatabase(db_path=str(tmp_path / "s.db"))
    try:
        db.conn.execute("PRAGMA foreign_keys=OFF")
        uid = db.log_unknown(session_id="s", project_id="p", unknown="why")
        _rebuild_apply_resolution(db, "unknowns", uid, {"is_resolved": True, "resolved_by": "answered"})
        row = db.conn.execute("SELECT is_resolved, resolved_by FROM project_unknowns WHERE id = ?", (uid,)).fetchone()
    finally:
        db.close()
    assert tuple(row) == (1, "answered")
