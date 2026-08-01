"""`issue-resolve` reported success while resolving nothing.

The release gate blocks on unresolved high-severity issues and tells you exactly
how to clear them:

    Found 2 unresolved high-severity issue(s):
      [58533163] Unknown log command failed: ...
    Resolve with: empirica issue-resolve --session-id <SID> --issue-id <ID> ...

Running precisely that printed `{"ok": true, "message": "Issue 58533163 marked
as resolved"}` — twice — and left both rows at `status='new'`. The gate stayed
blocked on the same two issues, with the tool insisting it had cleared them.

Two defects, and the pairing is what made it airtight:

1. The gate DISPLAYS an 8-character id while the stored id is a full UUID, so
   resolving by the id you were just shown matched no row.
2. `resolve_issue` ran its UPDATE and returned True unconditionally, never
   consulting rowcount — so matching nothing was indistinguishable from success.

Either alone would have been survivable: a wrong id that reported failure is a
typo you fix, and an unchecked rowcount on a correct id still does the work.
Together they produce a verb that cannot be used correctly and never says so.

Same shape as #390 (`unknown-resolve` reporting success for nonexistent UUIDs) —
the eighth wrong-key/silent-success defect in this release, and the only one
sitting inside the release gate's own remediation instructions.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.core.issue_capture import AutoIssueCaptureService

FULL_ID = "58533163-2872-48ca-925e-00de627a4876"


@pytest.fixture
def service(tmp_path, monkeypatch):
    db = tmp_path / "sessions.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE auto_captured_issues (id TEXT PRIMARY KEY, session_id TEXT, severity TEXT, "
        "category TEXT, message TEXT, status TEXT, resolution TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO auto_captured_issues (id, session_id, severity, category, message, status) "
        "VALUES (?, 's1', 'high', 'c', 'boom', 'new')",
        (FULL_ID,),
    )
    conn.commit()
    conn.close()

    svc = AutoIssueCaptureService.__new__(AutoIssueCaptureService)
    svc.session_id = "s1"
    svc._get_connection = lambda: sqlite3.connect(db)  # type: ignore[method-assign]
    svc._db_path = db
    return svc


def _status(svc) -> tuple[str, str | None]:
    conn = sqlite3.connect(svc._db_path)
    row = conn.execute("SELECT status, resolution FROM auto_captured_issues WHERE id = ?", (FULL_ID,)).fetchone()
    conn.close()
    return row


def test_the_displayed_short_id_actually_resolves(service):
    """POSITIVE CONTROL — the reproduction. The gate prints this exact form."""
    assert service.resolve_issue("58533163", "fixed upstream") is True

    status, resolution = _status(service)
    assert status == "resolved"
    assert resolution == "fixed upstream"


def test_a_nonexistent_id_reports_failure(service):
    """The other half. Returning True here is what made the first defect
    invisible — and what the release gate believed."""
    assert service.resolve_issue("deadbeef", "should refuse") is False

    assert _status(service)[0] == "new", "a failed resolve must not have moved the row"


def test_the_full_uuid_still_resolves(service):
    """NEGATIVE CONTROL: the documented path must not regress."""
    assert service.resolve_issue(FULL_ID, "fixed") is True
    assert _status(service)[0] == "resolved"


def test_an_ambiguous_prefix_is_refused(service):
    """Two issues sharing a prefix must not resolve by coin flip — resolving the
    wrong issue would clear a gate that should still be blocking."""
    conn = sqlite3.connect(service._db_path)
    conn.execute(
        "INSERT INTO auto_captured_issues (id, session_id, severity, category, message, status) "
        "VALUES ('58533163-0000-0000-0000-000000000000', 's1', 'high', 'c', 'other', 'new')"
    )
    conn.commit()
    conn.close()

    assert service.resolve_issue("58533163", "ambiguous") is False
    assert _status(service)[0] == "new"
