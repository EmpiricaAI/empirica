"""A mistyped goal-id must never resolve to SOME goal, and a real goal must resolve.

Reported by cortex (2026-07-30) with two faces:

**Silent, and the dangerous one.** `goals-add-task --goal-id 6a` — a two-character
fragment left by a shell extraction that returned empty — prefix-matched an
unrelated goal and attached the task to it, with a success message. Work parented
to a goal nobody will look at is indistinguishable from work that was never
tracked, except that it reports success.

**Loud.** A full valid uuid failed with ``Error retrieving goal <id>: 'id'`` while
the row sat there with ``status=in_progress``.

The loud face was NOT the reporter's guess (project_id scoping). `goal_data` is a
serialized cache and the `id` COLUMN is the identity; the blob is empty or legacy
for many rows. Measured on this practice: **88 of 1431 goals (6%) were
unreachable**, reported as "Goal not found" for rows sitting intact — a broad
`except Exception` turned every deserialization error into a bare log line.

Legacy encodings found in the wild, all of which now deserialize:
  - `goal_data` = `{}` (everything real lives in columns)
  - `success_criteria` as bare strings rather than objects
  - `scope` as a label (`"project_wide"`) or a float rather than a dict
"""

from __future__ import annotations

import json
import time

import pytest

from empirica.core.goals.repository import GoalRepository
from empirica.core.goals.types import Goal


@pytest.fixture
def repo(tmp_path):
    """Construct against an explicit db_path.

    `GoalRepository()` with no argument resolves sessions.db from git/context, which
    a CI runner does not have — it raised "Cannot determine sessions.db path". The
    first version of this fixture patched the SessionDatabase symbol and passed
    locally for exactly that reason: my box had the context CI lacks. Passing the
    path explicitly removes the environment from the test entirely.
    """
    r = GoalRepository(db_path=str(tmp_path / "t.db"))
    yield r
    r.close()


def _insert(db, gid: str, objective: str, blob: dict | str | None, scope: str | None = None):
    db.conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, goal_data, status) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            gid,
            "s",
            objective,
            scope if scope is not None else json.dumps({"breadth": 0.5, "duration": 0.5, "coordination": 0.5}),
            time.time(),
            blob if isinstance(blob, str) else json.dumps(blob or {}),
            "in_progress",
        ),
    )
    db.conn.commit()


# ── the silent face: a fragment must not resolve ──────────────────────


def test_a_two_char_prefix_refuses_even_when_unique(repo):
    """THE regression. Uniqueness is not safety — `6a` was unique and wrong."""
    _insert(repo.db, "6a35bd73-0000-4000-8000-000000000001", "Add MCP resource exposure", {})

    assert repo.get_goal("6a") is None, "a 2-char fragment must never resolve, unique or not"


@pytest.mark.parametrize("bad", ["", "   ", "\t", None])
def test_empty_or_whitespace_never_resolves(repo, bad):
    """Left to the prefix path an empty id becomes LIKE '%' — matching every goal,
    and resolving whenever the table happens to hold exactly one."""
    _insert(repo.db, "aaaaaaaa-0000-4000-8000-000000000002", "the only goal", {})

    assert repo.get_goal(bad) is None


def test_an_eight_char_prefix_still_works(repo):
    """The floor must not break the ergonomics it protects — 8 is what goals-list
    prints, so it is the shortest a user could legitimately have copied."""
    gid = "bbbbbbbb-0000-4000-8000-000000000003"
    _insert(repo.db, gid, "real goal", {"id": gid, "objective": "real goal", "success_criteria": [], "scope": {}})

    got = repo.get_goal(gid[:8])
    assert got is not None and got.id == gid


def test_ambiguous_prefix_refuses_rather_than_picking_one(repo):
    _insert(repo.db, "cccccccc-1111-4000-8000-000000000004", "first", {})
    _insert(repo.db, "cccccccc-2222-4000-8000-000000000005", "second", {})

    assert repo.get_goal("cccccccc") is None


# ── the loud face: real goals must resolve ────────────────────────────


def test_a_goal_with_an_empty_blob_resolves_from_columns(repo):
    """`goal_data` is a derived cache; the columns are the record. 88 of 1431 goals
    here had `{}` and were reported "not found" with their objective in a column."""
    gid = "dddddddd-0000-4000-8000-000000000006"
    _insert(repo.db, gid, "objective lives in the column", {})

    got = repo.get_goal(gid)
    assert got is not None, "an empty blob must not make a real goal unreachable"
    assert got.id == gid
    assert got.objective == "objective lives in the column"


def test_legacy_string_success_criteria_deserialize(repo):
    """Older records stored criteria as bare strings; `sc["id"]` on a string raised
    "string indices must be integers", which surfaced as "Goal not found"."""
    gid = "eeeeeeee-0000-4000-8000-000000000007"
    _insert(
        repo.db,
        gid,
        "legacy criteria",
        {"id": gid, "objective": "legacy criteria", "success_criteria": ["Goal completion achieved"], "scope": {}},
    )

    got = repo.get_goal(gid)
    assert got is not None
    assert len(got.success_criteria) == 1
    assert got.success_criteria[0].description == "Goal completion achieved"


@pytest.mark.parametrize("legacy_scope", ['"project_wide"', "0.7"])
def test_legacy_scope_encodings_deserialize(repo, legacy_scope):
    """`scope` has been a dict, a label and a float. The non-dict forms carry no
    breadth/duration/coordination, so a NEUTRAL vector is returned — the goal
    becomes addressable without inventing precision nobody measured."""
    gid = "ffffffff-0000-4000-8000-000000000008"
    _insert(repo.db, gid, "legacy scope", {"objective": "legacy scope"}, scope=legacy_scope)

    got = repo.get_goal(gid)
    assert got is not None, "a legacy scope encoding must not make a goal unreachable"
    assert got.scope.breadth == 0.5


def test_from_dict_tolerates_legacy_forms_directly():
    """Tolerance lives in the deserializer, so every read path benefits — not just
    the one call site that happened to report the bug."""
    g = Goal.from_dict(
        {
            "id": "x",
            "objective": "o",
            "success_criteria": ["done when green"],
            "scope": "project_wide",
        }
    )
    assert g.success_criteria[0].description == "done when green"
    assert g.scope.coordination == 0.5


def test_a_genuinely_absent_id_still_returns_none(repo):
    """The repairs must not turn every miss into a hit."""
    _insert(repo.db, "99999999-0000-4000-8000-000000000009", "present", {})

    assert repo.get_goal("00000000-dead-4000-8000-000000000000") is None
