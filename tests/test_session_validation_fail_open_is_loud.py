"""A session validation that cannot run fails OPEN — and says so at WARNING.

`_validate_session_in_db` returns True when the database check itself errors,
so a resolver never blocks every command over an unreadable store. Logged at
DEBUG, a persistent failure made every stale pointer file read as a validated
session, indistinguishable from a healthy resolution. The level is now the one
the function already uses for "stale session" — a failed check is at least as
important as a failed match.
"""

from __future__ import annotations

import logging

from empirica.utils import session_resolver as sr


def test_db_error_accepts_the_session_but_warns_with_the_cause(monkeypatch, caplog, tmp_path):
    db = tmp_path / ".empirica" / "sessions" / "sessions.db"
    db.parent.mkdir(parents=True)
    db.write_text("this is not a database")  # sqlite raises DatabaseError on query

    with caplog.at_level(logging.WARNING, logger=sr.logger.name):
        ok = sr._validate_session_in_db("sess-1234-abcd", project_path=str(tmp_path))

    assert ok is True  # fail-open, as before
    assert "DB check FAILED" in caplog.text
    assert "UNVALIDATED" in caplog.text
    assert "sess-123" in caplog.text


def test_a_missing_session_is_still_refused(tmp_path):
    """Positive control: the check works when the DB is readable."""
    import sqlite3

    db = tmp_path / ".empirica" / "sessions" / "sessions.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sessions (session_id TEXT, ai_id TEXT, start_time REAL)")
    conn.execute("INSERT INTO sessions VALUES ('present', 'ai', 0)")
    conn.commit()
    conn.close()

    assert sr._validate_session_in_db("present", project_path=str(tmp_path)) is True
    assert sr._validate_session_in_db("absent", project_path=str(tmp_path)) is False
