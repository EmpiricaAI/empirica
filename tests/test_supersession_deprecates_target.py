"""An `invalidates` edge must DEPRECATE its target, not merely record an opinion.

David, 2026-08-03: *"if a new artifact is logged that overturns the last one,
that last one would be removed and the new one takes precedence regardless… in
fast iterative work, that is often what happens."*

It did not. Drawing the edge left the overturned finding at `is_resolved = 0`
with its full retrieval weight, competing with the artifact that replaced it.
Recording that something is superseded and having it still surface as current
are contradictory, and only the edge's author knew which was true.

Recency decay cannot substitute: it knows age, never wrongness. A finding
overturned an hour after it was written is *fresh and false* — the worst
combination for a ranking that weights recency.

Two independent holes, both fixed:

1. The edge had no side effect (here).
2. Live retrieval never filtered `is_resolved` for FINDINGS, though it did for
   unknowns and goals — so even a manually-resolved finding kept surfacing
   (`test_resolved_findings_leave_retrieval`).
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.cli.command_handlers.graph_commands import _supersede_target


class _FakeDB:
    def __init__(self, conn):
        self.conn = conn
        self.resolved: list[tuple] = []

    def resolve_finding(self, fid, resolution=None, superseded_by=None, resolution_kind=None):
        self.resolved.append((fid, resolution, superseded_by, resolution_kind))
        self.conn.execute(
            "UPDATE project_findings SET is_resolved = 1, superseded_by = ?, resolution_kind = ? WHERE id = ?",
            (superseded_by, resolution_kind, fid),
        )
        self.conn.commit()
        return True


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE project_findings (id TEXT PRIMARY KEY, finding TEXT, "
        "is_resolved INTEGER DEFAULT 0, superseded_by TEXT, resolution_kind TEXT)"
    )
    conn.execute("INSERT INTO project_findings (id, finding) VALUES ('old', 'the sky is green')")
    conn.commit()
    return _FakeDB(conn)


def test_invalidating_a_finding_deprecates_it(db):
    note = _supersede_target(db, "new", "old")

    assert note is None, "a clean supersession reports nothing"
    row = db.conn.execute("SELECT is_resolved, superseded_by, resolution_kind FROM project_findings").fetchone()
    assert row[0] == 1
    assert row[1] == "new", "the replacement must be recorded, not just the fact of replacement"
    assert row[2] == "superseded", "kind must distinguish 'was replaced' from 'aged out'"


def test_the_replacement_is_named_not_just_the_state(db):
    """`is_resolved` alone loses which artifact won.

    A reader finding a deprecated artifact needs the pointer to what replaced
    it, or the deprecation is a dead end rather than a redirect.
    """
    _supersede_target(db, "new", "old")
    _, resolution, superseded_by, kind = db.resolved[0]

    assert superseded_by == "new"
    assert "new" in resolution
    assert kind == "superseded"


def test_a_non_finding_target_is_reported_not_silently_skipped(db):
    """Only findings carry `superseded_by` today. Say so rather than no-op."""
    note = _supersede_target(db, "new", "some-unknown-id")

    assert note is not None
    assert "superseded_by" in note
    assert "keeps its current retrieval weight" in note, "name the CONSEQUENCE, not just the limitation"


def test_a_failure_never_breaks_the_log_that_carried_the_edge(db):
    """Deprecation is a side effect. A side effect must not fail the primary act."""

    def boom(*a, **k):
        raise RuntimeError("db exploded")

    db.resolve_finding = boom
    note = _supersede_target(db, "new", "old")

    assert note is not None, "the failure must be reported"
    assert "not deprecated" in note, "and must say the target is still live"


def test_other_relations_do_not_deprecate(db):
    """Only `invalidates` carries this meaning.

    `related` or `evidence` pointing at a finding must leave it alone —
    otherwise citing an artifact would silently kill it.
    """
    import inspect

    from empirica.cli.command_handlers import graph_commands

    src = inspect.getsource(graph_commands._wire_edges)
    assert 'relation == "invalidates"' in src, "the side effect must be gated on the relation"
