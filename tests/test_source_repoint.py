"""`source-update --url` re-points a source whose file MOVED.

Gardening is prune AND replant. Without this the CLI could only re-FETCH, never
re-TARGET, so a moved doc had to be archived and re-added — losing its id and every
`sourced_from` edge pointing at it. Losing the edges is the real cost: they are the
feedback channel that makes a source's relevance measurable.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from empirica.cli.command_handlers import sources_update_commands as su


class _Args:
    def __init__(self, source_id, url=None):
        self.source_id = source_id
        self.url = url
        self.output = "json"
        self.verbose = False


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "s.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE epistemic_sources (id TEXT PRIMARY KEY, title TEXT, source_url TEXT, "
        "canonical_path TEXT, content_hash TEXT, size_bytes INTEGER, mime_type TEXT, lifecycle_audit_log TEXT)"
    )
    conn.execute(
        "INSERT INTO epistemic_sources (id, title, source_url, canonical_path, content_hash) "
        "VALUES ('abc123', 'Moved Doc', 'docs/old_place.md', 'docs/old_place.md', 'oldhash')"
    )
    conn.commit()
    conn.close()

    class _DB:
        def __init__(self):
            self.conn = sqlite3.connect(str(path))

        def close(self):
            self.conn.close()

    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", _DB)
    return path


def test_repoint_updates_location_and_keeps_the_id(db, tmp_path, monkeypatch, capsys):
    """The id must survive — every sourced_from edge points at it."""
    moved = tmp_path / "new_place.md"
    moved.write_text("real content", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    rc = su.handle_source_update_command(_Args("abc123", url=str(moved)))
    assert rc == 0, capsys.readouterr().out

    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT id, source_url, content_hash, lifecycle_audit_log FROM epistemic_sources").fetchone()
    conn.close()
    sid, url, new_hash, audit_json = row

    assert sid == "abc123", "the id must not change — edges point at it"
    assert url == str(moved), "source_url must point at the new location"
    assert new_hash != "oldhash", "content identity is recomputed from the new location"

    events = [e["event"] for e in json.loads(audit_json)]
    assert "repointed" in events, "the move must be recorded, not silent"
    audit = json.loads(audit_json)
    move = next(e for e in audit if e["event"] == "repointed")
    assert move["old_location"] == "docs/old_place.md"
    assert move["new_location"] == str(moved)


def test_plain_update_without_url_does_not_move_the_source(db, tmp_path, monkeypatch):
    """Re-fetch stays re-fetch — the flag must not change existing behaviour."""
    target = tmp_path / "docs" / "old_place.md"
    target.parent.mkdir()
    target.write_text("content", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    su.handle_source_update_command(_Args("abc123"))

    conn = sqlite3.connect(str(db))
    url, audit_json = conn.execute("SELECT source_url, lifecycle_audit_log FROM epistemic_sources").fetchone()
    conn.close()
    assert url == "docs/old_place.md", "an ordinary update must not retarget"
    assert "repointed" not in [e["event"] for e in json.loads(audit_json or "[]")]
