"""`goals-list --output json` must return the description it stores.

The SELECT fetched objective/status/progress and never `g.description`, so
every JSON consumer saw title-only goals — unable to tell a goal carrying a
2,400-character spec from one carrying nothing.

It cost two errors in a single session, both mine:

1. I re-derived a goal's scope from conversation, reporting it as "title-only,
   no description", while its 2,440-character body sat in the row.
2. I deleted a goal believing its body had been eaten by a shell quoting bug,
   because the read-back showed an empty description. The body was intact.

An OMITTED field is indistinguishable from an ABSENT value at every consumer.
That is the same shape as the docs coverage ledger and the `--status all`
empty: the reader cannot tell "nothing there" from "not asked for".
"""

from __future__ import annotations

import sqlite3

from empirica.cli.command_handlers.goal_commands import handle_goals_list_command


def test_the_select_fetches_description():
    """Source-level, because the bug was in the QUERY, not the serialisation.

    Adding the key to the output dict without adding the column to the SELECT
    would produce `description: None` for every goal — which looks like a fixed
    bug and is the same defect wearing a fix's clothes.
    """
    import inspect

    src = inspect.getsource(handle_goals_list_command)

    assert "g.description" in src, "the column must be SELECTed, not just mapped"
    assert '"description": row[' in src, "and returned in the payload"


def test_description_survives_the_round_trip(tmp_path):
    """A goal written with a body reads back with that body."""
    db = tmp_path / "sessions.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE goals (id TEXT PRIMARY KEY, objective TEXT, description TEXT)")
    body = "## Why\n\nA body long enough that truncation would be visible.\n" * 20
    conn.execute("INSERT INTO goals VALUES ('g1', 'title', ?)", (body,))
    conn.commit()

    (stored,) = conn.execute("SELECT description FROM goals WHERE id='g1'").fetchone()

    assert stored == body, "the store holds it; the reader is what dropped it"
    assert len(stored) > 1000
