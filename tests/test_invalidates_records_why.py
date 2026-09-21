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


def test_a_bare_edge_still_closes_the_target_and_says_what_it_assumed():
    db = _DB()
    note = gc._supersede_target(db, "new", "old", None)
    assert db.calls[0]["resolution_kind"] == "superseded"
    assert note and "no kind and reason" in note and "retracted" in note


def test_a_kind_without_a_reason_is_honoured_and_the_gap_is_named():
    db = _DB()
    note = gc._supersede_target(db, "new", "old", {"kind": "stale"})
    assert db.calls[0]["resolution_kind"] == "stale"
    assert note and "no reason" in note


def test_an_unknown_kind_is_not_written_into_the_closed_vocabulary():
    db = _DB()
    note = gc._supersede_target(db, "new", "old", {"kind": "wrong", "reason": "x"})
    assert db.calls[0]["resolution_kind"] == "superseded"
    assert note and "no kind" in note
