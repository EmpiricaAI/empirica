"""The CLI sources lister must be practice-scoped, like the daemon's.

`13dafd2f7` fixed the daemon's `_list_sources` (a practice's project_id drifts
over its life, so its own sources scatter across several ids and
`WHERE project_id = ?` hides them) and left the CLI twin untouched. So
`sources-map`, `source-list` and `sources-check` kept under-reading.

Measured on this practice 2026-08-05: `sources-map` reported 29 while the db
held 39 unarchived, 10 under the stale id `3be592bd` named in that very commit.
On mesh-support the drift is total — 17 rows, 0 shown — which is why the bug was
filed as "the catalogue is empty" rather than "the read path is broken".

The db path is the practice boundary (`SessionDatabase()` resolves to THIS
project's `.empirica/sessions/sessions.db`), so reading the whole table cannot
leak another practice's rows. An explicit `--project-id` is the one case that
must keep the strict single-id read.
"""

from __future__ import annotations

import sqlite3

from empirica.cli.command_handlers.artifact_log_commands import _query_epistemic_sources

CANONICAL = "748a81a2-ac14-45b8-a185-994997b76828"
DRIFTED = "3be592bd-651d-47f6-8dcd-eec78df7ebfd"


class _FakeDB:
    """Minimal stand-in: the helper needs `.conn` and the refdocs lookup."""

    def __init__(self, conn):
        self.conn = conn

    def get_project_reference_docs(self, _project_id):
        return []


def _db_with_drift(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "sessions.db"))
    conn.execute("""
        CREATE TABLE epistemic_sources (
            id TEXT PRIMARY KEY, project_id TEXT, source_type TEXT, title TEXT,
            description TEXT, confidence REAL, epistemic_layer TEXT, source_url TEXT,
            discovered_at TEXT, source_metadata TEXT, archived INTEGER DEFAULT 0,
            archive_reason TEXT, archive_target_id TEXT, archived_at TEXT
        )
    """)
    rows = [
        ("s1", CANONICAL, "doc", "Canonical one", "", 0.9, "noetic", "u1", "2026-01-01", "{}", 0),
        ("s2", CANONICAL, "doc", "Canonical two", "", 0.9, "noetic", "u2", "2026-01-02", "{}", 0),
        ("s3", DRIFTED, "doc", "Stranded under a stale id", "", 0.9, "noetic", "u3", "2026-01-03", "{}", 0),
        ("s4", DRIFTED, "doc", "Also stranded", "", 0.9, "noetic", "u4", "2026-01-04", "{}", 0),
        ("s5", DRIFTED, "doc", "Stranded AND archived", "", 0.9, "noetic", "u5", "2026-01-05", "{}", 1),
    ]
    conn.executemany(
        "INSERT INTO epistemic_sources (id, project_id, source_type, title, description, confidence, "
        "epistemic_layer, source_url, discovered_at, source_metadata, archived) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return _FakeDB(conn)


def test_practice_scope_surfaces_sources_under_a_drifted_project_id(tmp_path):
    db = _db_with_drift(tmp_path)
    got = _query_epistemic_sources(db, CANONICAL, None, "all", include_archived=False)
    ids = {s["id"] for s in got}
    assert ids == {"s1", "s2", "s3", "s4"}, (
        "the practice-scoped read must surface rows sitting under a drifted project_id — "
        "hiding them is what made a populated catalogue read as empty"
    )


def test_drift_stays_visible_per_row(tmp_path):
    """Aggregating the drift away would hide it from gardening."""
    db = _db_with_drift(tmp_path)
    got = _query_epistemic_sources(db, CANONICAL, None, "all", include_archived=False)
    by_pid: dict[str, int] = {}
    for s in got:
        by_pid[s["project_id"]] = by_pid.get(s["project_id"], 0) + 1
    assert by_pid == {CANONICAL: 2, DRIFTED: 2}


def test_explicit_project_scope_still_reads_one_id(tmp_path):
    """An explicit --project-id is a deliberate cross-project query."""
    db = _db_with_drift(tmp_path)
    got = _query_epistemic_sources(db, DRIFTED, None, "all", include_archived=False, practice_scope=False)
    assert {s["id"] for s in got} == {"s3", "s4"}


def test_archived_still_hidden_by_default_under_practice_scope(tmp_path):
    """Widening the id scope must not widen the archived scope."""
    db = _db_with_drift(tmp_path)
    default = _query_epistemic_sources(db, CANONICAL, None, "all", include_archived=False)
    forensic = _query_epistemic_sources(db, CANONICAL, None, "all", include_archived=True)
    assert "s5" not in {s["id"] for s in default}
    assert "s5" in {s["id"] for s in forensic}


def test_sources_check_reads_practice_scoped():
    """A rot check that skips drifted rows reports a clean corpus it never read."""
    import inspect

    from empirica.cli.command_handlers import sources_check_commands

    src = inspect.getsource(sources_check_commands._default_list_sources)
    assert "practice_scope=True" in src, (
        "sources-check must read practice-scoped — otherwise it certifies sources it never looked at"
    )
