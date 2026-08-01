"""The batch artifact verbs mutated by unbounded prefix match.

`resolve-artifacts`, `delete-artifacts` and `update-artifacts` are the documented
DEFAULT path for multi-artifact work, and all three addressed rows with
`WHERE id LIKE 'prefix%'`:

  - **resolve**: six branches issued `UPDATE ... WHERE id LIKE ?` with **no
    LIMIT**, so a short id resolved *every* matching artifact — while
    `resolved_count += 1` reported exactly one. Fifty rows changed, receipt said
    "1 resolved".
  - **delete**: took `fetchone()` of a LIKE match with no minimum length and no
    ambiguity check. Deletion is the one lever with no history to recover from.
  - **update**: had a length floor but no ambiguity check, and the UPDATE had no
    LIMIT either.

Fixed by resolving the prefix to exactly one full id up front and addressing rows
by `WHERE id = ?`. That makes each operation single-row by construction, which is
why the undercount disappears with it rather than needing its own fix.

This is the fourth appearance of one defect (goals, tasks, and now the artifact
verbs). Each earlier fix guarded the path that happened to bite someone instead of
the shared shape — hence the shared resolver these tests exercise directly.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.data.id_guard import MIN_ID_PREFIX, resolve_id_prefix


@pytest.fixture
def cursor():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE project_findings (id TEXT PRIMARY KEY, is_resolved INTEGER DEFAULT 0)")
    return conn.cursor()


def _add(cursor, artifact_id: str) -> str:
    cursor.execute("INSERT INTO project_findings (id) VALUES (?)", (artifact_id,))
    return artifact_id


ID_A = "1aaaaaaa-0000-4000-8000-000000000001"
ID_B = "1bbbbbbb-0000-4000-8000-000000000002"


def test_a_short_prefix_is_refused(cursor):
    """POSITIVE CONTROL — a one-character id spans every id starting with '1'."""
    _add(cursor, ID_A)
    _add(cursor, ID_B)

    full_id, error = resolve_id_prefix(cursor, "project_findings", "id", "1")

    assert full_id is None
    assert "shorter than 8" in error


def test_an_ambiguous_prefix_is_refused(cursor):
    """Length alone is not identity — two artifacts can share an 8-char prefix,
    and picking one by row order is a coin flip returned as a result."""
    _add(cursor, "abcdef12-0000-4000-8000-000000000001")
    _add(cursor, "abcdef12-0000-4000-8000-000000000002")

    full_id, error = resolve_id_prefix(cursor, "project_findings", "id", "abcdef12")

    assert full_id is None
    assert "ambiguous" in error
    assert "matches 2 rows" in error


def test_a_blank_id_is_refused(cursor):
    """An empty id becomes LIKE '%' and matches everything."""
    _add(cursor, ID_A)

    full_id, error = resolve_id_prefix(cursor, "project_findings", "id", "   ")

    assert full_id is None
    assert "empty" in error


def test_a_unique_prefix_resolves(cursor):
    """NEGATIVE CONTROL: refusing everything would pass every test above while
    breaking the prefix ergonomics the batch verbs depend on."""
    _add(cursor, ID_A)
    _add(cursor, "99999999-0000-4000-8000-000000000002")

    full_id, error = resolve_id_prefix(cursor, "project_findings", "id", ID_A[:MIN_ID_PREFIX])

    assert error is None
    assert full_id == ID_A


def test_a_full_uuid_resolves(cursor):
    """NEGATIVE CONTROL: the common path must be untouched."""
    _add(cursor, ID_A)

    assert resolve_id_prefix(cursor, "project_findings", "id", ID_A) == (ID_A, None)


def test_a_short_id_containing_a_dash_is_not_length_refused(cursor):
    """The length floor exempts dashed ids so a full UUID is never rejected for
    being 'short'. A dashed fragment still has to resolve uniquely."""
    _add(cursor, "ab-cd")

    assert resolve_id_prefix(cursor, "project_findings", "id", "ab-cd") == ("ab-cd", None)


def test_a_prefix_matching_nothing_is_refused(cursor):
    _add(cursor, ID_A)

    full_id, error = resolve_id_prefix(cursor, "project_findings", "id", "deadbeef")

    assert full_id is None
    assert "not found" in error


# ── the property that made this severe ────────────────────────────────


def test_resolution_is_single_row_by_construction(cursor):
    """The heart of it. The old code ran `UPDATE ... WHERE id LIKE '1%'`, which
    SQL applies to every match — there is no implicit LIMIT. Resolving first and
    addressing by exact id is what bounds the write, so this asserts the property
    the fix relies on rather than the guard that produces it."""
    _add(cursor, ID_A)
    _add(cursor, ID_B)

    # What the old code did:
    cursor.execute("UPDATE project_findings SET is_resolved = 1 WHERE id LIKE ?", ("1%",))
    assert cursor.rowcount == 2, "prefix UPDATE hits every match — this is why it had to go"

    cursor.execute("UPDATE project_findings SET is_resolved = 0")

    # What it does now:
    full_id, error = resolve_id_prefix(cursor, "project_findings", "id", ID_A)
    assert error is None
    cursor.execute("UPDATE project_findings SET is_resolved = 1 WHERE id = ?", (full_id,))
    assert cursor.rowcount == 1

    untouched = cursor.execute("SELECT is_resolved FROM project_findings WHERE id = ?", (ID_B,)).fetchone()
    assert untouched[0] == 0, "the sibling artifact must not have been resolved"


def test_no_batch_verb_still_mutates_by_prefix():
    """Source guard. Each earlier fix in this family patched one path and the
    defect reappeared in the next verb; this fails if any batch handler goes back
    to interpolating an id into a LIKE pattern."""
    from pathlib import Path

    import empirica.cli.command_handlers.graph_commands as gc

    src = Path(gc.__file__).read_text(encoding="utf-8")

    assert 'f"{artifact_id}%"' not in src, "a resolve/delete path is prefix-matching again"
    assert 'f"{aid}%"' not in src, "the update path is prefix-matching again"
    assert "resolve_id_prefix" in src, "the shared resolver is no longer used"


# ── delete-artifacts previews by default ──────────────────────────────
#
# The gardening skill, ARTIFACT_HYGIENE.md and the global system prompt all
# stated that delete-artifacts is dry-run by default and that `--apply` performs
# the deletion. Neither was true: `--dry-run` was store_true (default False), so
# a bare invocation deleted immediately, and `--apply` did not exist. Someone
# following the documented "preview first" workflow destroyed artifacts.
#
# Resolved in favour of the docs (David, 2026-08-01): deletion is the one lever
# with no history to recover from, so preview is the safe default and the code
# was the bug. `--dry-run` stays accepted as a no-op, since it is the flag three
# documents told people to pass.


def _delete_args(**kw):
    import types

    base = {"config": "-", "schema": False, "apply": False, "dry_run": False, "output": "json", "verbose": False}
    base.update(kw)
    return types.SimpleNamespace(**base)


class _FakeDB:
    """Stands in for SessionDatabase so this test needs no project on disk.

    The first version of these tests let the handler open the real database.
    They passed on a developer box with a populated .empirica/ and failed in CI,
    where there is none: the handler produced no stdout and json.loads("")
    raised. A test that only passes where the author happens to be sitting is
    not a test of the code.
    """

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE project_findings (id TEXT PRIMARY KEY)")

    def close(self):
        self.conn.close()


def _run_delete(monkeypatch, capsys, args, payload):
    import json as _json

    import empirica.cli.command_handlers.graph_commands as gc
    import empirica.data.session_database as sdb

    monkeypatch.setattr(gc, "_read_deletion_input", lambda _a: payload)
    monkeypatch.setattr(sdb, "SessionDatabase", _FakeDB)
    gc.handle_delete_artifacts_command(args)
    out = capsys.readouterr().out
    assert out.strip(), "handler produced no stdout — it must always emit a JSON receipt"
    return _json.loads(out)


PAYLOAD = {"deletions": [{"type": "finding", "id": "nonexistent-but-well-formed-id"}], "reason": "test"}


def test_a_bare_invocation_previews(monkeypatch, capsys):
    """POSITIVE CONTROL — the reproduction. This used to delete."""
    result = _run_delete(monkeypatch, capsys, _delete_args(), PAYLOAD)

    assert result["dry_run"] is True


def test_apply_actually_deletes(monkeypatch, capsys):
    """NEGATIVE CONTROL: if preview were unconditional the verb would be inert."""
    result = _run_delete(monkeypatch, capsys, _delete_args(apply=True), PAYLOAD)

    assert result["dry_run"] is False


def test_the_dry_run_flag_is_still_accepted(monkeypatch, capsys):
    """It is the flag three documents told people to pass — it must not error."""
    result = _run_delete(monkeypatch, capsys, _delete_args(dry_run=True), PAYLOAD)

    assert result["dry_run"] is True


def test_an_explicit_body_value_still_wins(monkeypatch, capsys):
    """Callers that set dry_run in the JSON body kept their meaning; only the
    DEFAULT changed."""
    body = {**PAYLOAD, "dry_run": False}

    result = _run_delete(monkeypatch, capsys, _delete_args(), body)

    assert result["dry_run"] is False
