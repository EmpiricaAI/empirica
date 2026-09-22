"""An `invalidates` edge records WHY its target is closed; it no longer decides.

It always picked resolution_kind=superseded with a bare "Superseded by <id>",
so an edge drawn because the target was WRONG was filed as the target having
aged (empirica-autonomy: three load-bearing findings closed that way). A graph
that cannot tell its ageing from its errors cannot calibrate on either.
"""

from __future__ import annotations

from empirica.cli.command_handlers import graph_commands as gc


class _DB:
    def __init__(self):
        self.calls: list[dict] = []
        outer = self

        class _Conn:
            def cursor(self):
                return self

            def execute(self, *_a):
                return self

            def fetchone(self):
                return (1,)

        self.conn = _Conn()
        self._outer = outer

    def resolve_finding(self, finding_id, **kw):
        self.calls.append({"id": finding_id, **kw})


def test_the_author_states_a_retraction_and_it_is_recorded_as_one():
    db = _DB()
    note = gc._supersede_target(db, "new", "old", {"kind": "retracted", "reason": "the 0.5 default never fired"})
    assert note is None
    call = db.calls[0]
    assert call["resolution_kind"] == "retracted"
    assert call["resolution"].startswith("the 0.5 default never fired")
    assert "new" in call["resolution"] and call["superseded_by"] == "new"


def test_a_bare_edge_closes_the_target_unclassified_and_does_not_guess():
    """It used to store `superseded` while warning about it. Three practices then
    found every partly-wrong finding wearing that kind, and a fleet read of the
    column measured house style. A guess in the column is indistinguishable from
    a judgement; NULL is not."""
    db = _DB()
    note = gc._supersede_target(db, "new", "old", None)
    assert db.calls[0]["resolution_kind"] is None
    assert note and "UNCLASSIFIED" in note and "no kind and reason" in note and "retracted" in note
    assert "two artifacts" in note


def test_a_kind_without_a_reason_is_honoured_and_the_gap_is_named():
    db = _DB()
    note = gc._supersede_target(db, "new", "old", {"kind": "stale"})
    assert db.calls[0]["resolution_kind"] == "stale"
    assert note and "no reason" in note


def test_an_unknown_kind_is_not_written_into_the_closed_vocabulary():
    db = _DB()
    note = gc._supersede_target(db, "new", "old", {"kind": "wrong", "reason": "x"})
    assert db.calls[0]["resolution_kind"] is None
    assert note and "no kind" in note


def test_mistyped_is_an_accepted_kind_on_the_edge():
    """Re-logging a finding as the mistake or decision it really was closes the
    original as `mistyped`. The kind was missing from the edge vocabulary, so
    five of nineteen re-typed findings closed unclassified (2026-09-22)."""
    db = _DB()
    note = gc._supersede_target(db, "new", "old", {"kind": "mistyped", "reason": "a choice logged as a finding"})
    assert db.calls[0]["resolution_kind"] == "mistyped"
    assert note is None
