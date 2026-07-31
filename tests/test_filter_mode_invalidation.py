"""Bulk correction must reach dead_ends and mistakes, and must INVALIDATE not resolve.

Migration 060 gave `project_dead_ends` and `mistakes_made` an `is_invalidated`
lifecycle precisely so permanent negative guidance could be proven wrong. But
``resolve-artifacts`` filter mode only ever accepted `finding` and `unknown`, so
the bulk path could not reach the two types the migration was built for.

That blocked a real pass (autonomy, 2026-07-31: ~108 tool-noise dead-ends), and the
per-id fallback was not available either — NO verb enumerates artifact UUIDs
(`epistemics-list` returns vector trajectories despite its name). The bulk
correction path required an input the CLI never produced.

The distinction these tests defend: findings/unknowns get `is_resolved`,
dead_ends/mistakes get `is_invalidated`. Migration 060 kept them separate on
purpose — a dead-end is never "done", it is either still-constraining or wrong —
and collapsing them would let a gardening pass silently return dead approaches to
the option space.
"""

from __future__ import annotations

import io
import json
import sys

import pytest


@pytest.fixture
def wired(tmp_path, monkeypatch):
    import empirica.data.session_database as _sdb

    db_file = str(tmp_path / "t.db")
    real = _sdb.SessionDatabase
    monkeypatch.setattr(_sdb, "SessionDatabase", lambda *a, **k: real(db_path=db_file))
    return db_file, real


def _seed_dead_end(real, db_file, did, approach, project_id="p"):
    db = real(db_path=db_file)
    try:
        db.conn.execute(
            "INSERT INTO project_dead_ends (id, project_id, session_id, approach, why_failed, "
            "created_timestamp, dead_end_data) VALUES (?,?,?,?,?,?,?)",
            (did, project_id, "s", approach, "it failed", 0.0, "{}"),
        )
        db.conn.commit()
    finally:
        db.close()


def _seed_mistake(real, db_file, mid, mistake):
    db = real(db_path=db_file)
    try:
        db.conn.execute(
            "INSERT INTO mistakes_made (id, session_id, mistake, why_wrong, created_timestamp, "
            "mistake_data, project_id) VALUES (?,?,?,?,?,?,?)",
            (mid, "s", mistake, "wrong because", 0.0, "{}", "p"),
        )
        db.conn.commit()
    finally:
        db.close()


def _run(payload) -> dict:
    from empirica.cli.command_handlers.graph_commands import handle_resolve_artifacts_command

    class _Args:
        input = "-"
        output = "json"
        verbose = False

    buf, out = sys.stdout, io.StringIO()
    stdin, sys.stdin = sys.stdin, io.StringIO(json.dumps(payload))
    sys.stdout = out
    try:
        handle_resolve_artifacts_command(_Args())
    finally:
        sys.stdin, sys.stdout = stdin, buf
    return json.loads(out.getvalue())


def test_dead_end_filter_is_accepted_at_all(wired):
    """THE regression — filter.type dead_end was rejected outright."""
    db_file, real = wired
    _seed_dead_end(real, db_file, "d1111111-0000-4000-8000-000000000001", "Bash: some tool noise")

    res = _run({"filter": {"type": "dead_end", "matching": "Bash:%"}, "resolution": "tool noise"})

    assert res.get("ok") is True, res
    assert res.get("matched") == 1


def test_dry_run_is_the_default_and_mutates_nothing(wired):
    """Bulk invalidation of the cognitive immune system must not fire on a typo."""
    db_file, real = wired
    _seed_dead_end(real, db_file, "d2222222-0000-4000-8000-000000000002", "Bash: noise")

    _run({"filter": {"type": "dead_end", "matching": "Bash:%"}, "resolution": "r"})

    db = real(db_path=db_file)
    try:
        flag = db.conn.execute("SELECT is_invalidated FROM project_dead_ends WHERE id LIKE 'd2222222%'").fetchone()[0]
    finally:
        db.close()
    assert not flag, "dry-run must not mutate"


def test_apply_sets_is_invalidated_not_is_resolved(wired):
    """The load-bearing distinction. dead_ends have NO is_resolved column, so writing
    the wrong one would either error or silently no-op — and a gardening pass that
    reported success while leaving dead approaches live is the worse outcome."""
    db_file, real = wired
    _seed_dead_end(real, db_file, "d3333333-0000-4000-8000-000000000003", "Bash: noise")

    res = _run(
        {"filter": {"type": "dead_end", "matching": "Bash:%"}, "resolution": "auto-captured noise", "apply": True}
    )
    assert res.get("resolved") == 1, res

    db = real(db_path=db_file)
    try:
        row = db.conn.execute(
            "SELECT is_invalidated, invalidation_reason, invalidated_at FROM project_dead_ends "
            "WHERE id LIKE 'd3333333%'"
        ).fetchone()
    finally:
        db.close()

    assert row[0] == 1
    assert row[1] == "auto-captured noise", "the reason must be recorded, not just the flag"
    assert row[2] is not None


def test_mistakes_are_reachable_too(wired):
    db_file, real = wired
    _seed_mistake(real, db_file, "m1111111-0000-4000-8000-000000000001", "a mis-recorded mistake")

    res = _run({"filter": {"type": "mistake", "matching": "a mis-%"}, "resolution": "noise", "apply": True})
    assert res.get("resolved") == 1, res

    db = real(db_path=db_file)
    try:
        flag = db.conn.execute("SELECT is_invalidated FROM mistakes_made WHERE id LIKE 'm1111111%'").fetchone()[0]
    finally:
        db.close()
    assert flag == 1


def test_genuine_dead_ends_are_not_swept_by_a_noise_filter(wired):
    """The immune system must survive the pass. A tool-prefix filter must leave
    hand-authored dead-ends alone — they are the record of what does not work."""
    db_file, real = wired
    _seed_dead_end(real, db_file, "d4444444-0000-4000-8000-000000000004", "Bash: tool noise")
    _seed_dead_end(real, db_file, "d5555555-0000-4000-8000-000000000005", "Tried passport.js for JWT-only auth")

    _run({"filter": {"type": "dead_end", "matching": "Bash:%"}, "resolution": "noise", "apply": True})

    db = real(db_path=db_file)
    try:
        noise = db.conn.execute("SELECT is_invalidated FROM project_dead_ends WHERE id LIKE 'd4444444%'").fetchone()[0]
        genuine = db.conn.execute("SELECT is_invalidated FROM project_dead_ends WHERE id LIKE 'd5555555%'").fetchone()[
            0
        ]
    finally:
        db.close()

    assert noise == 1
    assert not genuine, "a hand-authored dead-end must survive a tool-noise sweep"


def test_already_invalidated_rows_are_not_rematched(wired):
    """Idempotence: the open-state predicate must read is_invalidated for these types,
    or a second pass re-counts everything and the receipt lies."""
    db_file, real = wired
    _seed_dead_end(real, db_file, "d6666666-0000-4000-8000-000000000006", "Bash: noise")

    _run({"filter": {"type": "dead_end", "matching": "Bash:%"}, "resolution": "noise", "apply": True})
    second = _run({"filter": {"type": "dead_end", "matching": "Bash:%"}, "resolution": "noise"})

    assert second.get("matched") == 0, "an invalidated dead-end is no longer OPEN"


def test_findings_still_resolve_the_old_way(wired):
    """Guard against the openflag refactor breaking the two original types."""
    db_file, real = wired
    db = real(db_path=db_file)
    try:
        db.conn.execute(
            "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, finding_data) "
            "VALUES (?,?,?,?,?,?)",
            ("f1111111-0000-4000-8000-000000000001", "p", "s", "test noise finding", 0.0, "{}"),
        )
        db.conn.commit()
    finally:
        db.close()

    res = _run({"filter": {"type": "finding", "matching": "test noise%"}, "resolution": "noise", "apply": True})
    assert res.get("resolved") == 1, res

    db = real(db_path=db_file)
    try:
        flag = db.conn.execute("SELECT is_resolved FROM project_findings WHERE id LIKE 'f1111111%'").fetchone()[0]
    finally:
        db.close()
    assert flag == 1


def test_an_unsupported_type_is_still_refused_by_name(wired):
    res = _run({"filter": {"type": "decision", "matching": "%"}, "resolution": "r"})

    assert res.get("ok") is False
    assert "dead_end" in res.get("error", ""), "the error must list what IS supported"
