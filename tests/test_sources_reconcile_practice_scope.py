"""`sources-reconcile` is the third site of the drifted-project_id under-read.

Reported by cortex after `75f0663c5` fixed the shared lister: this module uses
its OWN direct SQL and never calls `_query_epistemic_sources`, so a bare
`sources-reconcile --register-shared` reported `candidates: 0, registered: 0` —
which reads as "nothing to do" and is actually "looked in the wrong place".

Two independent pairs, not one coupled set (my first reading of the grep hits
paired them wrongly):

  _run_register_shared_backfill  owns read + write, write is `WHERE id = ?`
                                 -> safe to widen the read alone
  _load_local_sources            feeds _set_cortex_uuid_alias / _swap_source_id,
                                 whose writes DID filter on project_id
                                 -> read and writes must widen together

The finding-ref cascade is the sharpest case: it is data integrity, not
visibility. Renaming a source while a filter hides the findings that cite it
leaves dangling refs *created by the repair*.
"""

from __future__ import annotations

import json
import sqlite3

from empirica.cli.command_handlers.sources_reconcile_commands import (
    _load_local_sources,
    _set_cortex_uuid_alias,
    _swap_finding_source_refs,
)

ACTIVE = "748a81a2-ac14-45b8-a185-994997b76828"
DRIFTED = "3be592bd-651d-47f6-8dcd-eec78df7ebfd"


class _FakeDB:
    def __init__(self, conn):
        self.conn = conn


def _db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "sessions.db"))
    conn.execute("""
        CREATE TABLE epistemic_sources (
            id TEXT PRIMARY KEY, project_id TEXT, title TEXT, source_url TEXT,
            content_hash TEXT, size_bytes INTEGER, canonical_path TEXT,
            mime_type TEXT, source_metadata TEXT, cortex_uuid TEXT,
            archived INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE TABLE project_findings (id TEXT PRIMARY KEY, project_id TEXT, source_refs TEXT)")
    conn.executemany(
        "INSERT INTO epistemic_sources (id, project_id, title, archived) VALUES (?,?,?,?)",
        [
            ("src-active", ACTIVE, "Under the active id", 0),
            ("src-drifted", DRIFTED, "Under a drifted id", 0),
            ("src-archived", DRIFTED, "Drifted and archived", 1),
        ],
    )
    conn.commit()
    return _FakeDB(conn)


def test_load_local_sources_surfaces_drifted_rows(tmp_path):
    db = _db(tmp_path)
    got = _load_local_sources(db, ACTIVE)
    assert {r["id"] for r in got} == {"src-active", "src-drifted"}


def test_load_local_sources_still_hides_archived(tmp_path):
    """Widening the id scope must not widen the archived scope."""
    db = _db(tmp_path)
    assert "src-archived" not in {r["id"] for r in _load_local_sources(db, ACTIVE)}


def test_explicit_project_scope_narrows(tmp_path):
    """An explicit --project-id stays a deliberate single-project query."""
    db = _db(tmp_path)
    got = _load_local_sources(db, ACTIVE, practice_scope=False)
    assert {r["id"] for r in got} == {"src-active"}


def test_alias_write_reaches_a_drifted_row(tmp_path):
    """The write half. With `AND project_id = ?` this matched zero rows and said
    nothing, so the source registered upstream and the local stamp vanished."""
    db = _db(tmp_path)
    _set_cortex_uuid_alias(db, "src-drifted", "cortex-uuid-1")
    got = db.conn.execute("SELECT cortex_uuid FROM epistemic_sources WHERE id = 'src-drifted'").fetchone()
    assert got[0] == "cortex-uuid-1", "a drifted row must still receive its cortex_uuid stamp"


def test_finding_ref_cascade_covers_findings_under_a_drifted_project_id(tmp_path):
    """Data integrity, not visibility: a filtered cascade renames the source and
    leaves citations under a drifted id pointing at the old uuid."""
    db = _db(tmp_path)
    db.conn.executemany(
        "INSERT INTO project_findings (id, project_id, source_refs) VALUES (?,?,?)",
        [
            ("f-active", ACTIVE, json.dumps(["src-drifted"])),
            ("f-drifted", DRIFTED, json.dumps(["src-drifted", "other"])),
        ],
    )
    db.conn.commit()
    cursor = db.conn.cursor()
    updated = _swap_finding_source_refs(cursor, "src-drifted", "cortex-uuid-1")
    db.conn.commit()

    refs = dict(db.conn.execute("SELECT id, source_refs FROM project_findings").fetchall())
    assert json.loads(refs["f-active"]) == ["cortex-uuid-1"]
    assert json.loads(refs["f-drifted"]) == ["cortex-uuid-1", "other"], (
        "a finding under a drifted project_id must have its citation rewritten too — "
        "otherwise the swap creates the dangling ref it was meant to prevent"
    )
    assert updated == 2


def test_writes_do_not_filter_on_project_id():
    """Guard the pairing itself: a later edit that re-adds the filter to either
    write re-creates the silent zero-row match."""
    import inspect

    from empirica.cli.command_handlers import sources_reconcile_commands as m

    for fn in (m._set_cortex_uuid_alias, m._swap_source_id, m._swap_finding_source_refs):
        body = "\n".join(ln for ln in inspect.getsource(fn).splitlines() if not ln.strip().startswith("#"))
        assert "project_id = ?" not in body, (
            f"{fn.__name__} must key on id alone — it is fed by a practice-scoped read, so a "
            f"project_id filter silently matches zero rows for any drifted source"
        )
