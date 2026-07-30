"""Assumptions must be resolvable through the batch path — they never were.

Found 2026-07-30 while resolving a real assumption during the correction-surface
work: ``resolve-artifacts`` targeted ``project_assumptions.assumption_id`` and set
``is_verified``/``verified_by``. None of those exist. The table is
``assumptions(id)`` and it carries a three-valued ``status`` CHECK'd to
``unverified | verified | falsified``.

So every batch resolve of an assumption raised "no such table: project_assumptions"
for the life of the verb. It failed LOUDLY, which is why nobody noticed — the graph
never looked wrong, the call just never worked, and the single-artifact path was
used instead.

The distinction the fix must preserve is the same one migration 061 adds for
findings: **borne out and falsified are opposite events.** An assumption layer that
cannot say "this did not hold" is the pre-blindspot surface with its point removed.
"""

from __future__ import annotations

import io
import json
import sys

import pytest


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Pin the handler's bare ``SessionDatabase()`` at a temp db."""
    import empirica.data.session_database as _sdb

    db_file = str(tmp_path / "t.db")
    real = _sdb.SessionDatabase
    monkeypatch.setattr(_sdb, "SessionDatabase", lambda *a, **k: real(db_path=db_file))
    return db_file, real


def _seed(real, db_file: str, aid: str) -> None:
    db = real(db_path=db_file)
    try:
        db.conn.execute(
            "INSERT INTO assumptions (id, assumption, confidence, status, created_timestamp) VALUES (?,?,?,?,?)",
            (aid, "an unverified belief", 0.5, "unverified", 0.0),
        )
        db.conn.commit()
    finally:
        db.close()


def _resolve(payload: dict) -> None:
    from empirica.cli.command_handlers.graph_commands import handle_resolve_artifacts_command

    class _Args:
        input = "-"
        output = "json"
        verbose = False

    stdin, sys.stdin = sys.stdin, io.StringIO(json.dumps(payload))
    try:
        handle_resolve_artifacts_command(_Args())
    finally:
        sys.stdin = stdin


def _status(real, db_file: str, aid: str) -> str:
    db = real(db_path=db_file)
    try:
        return db.conn.execute("SELECT status FROM assumptions WHERE id = ?", (aid,)).fetchone()[0]
    finally:
        db.close()


def test_a_falsified_assumption_resolves(wired):
    """THE regression — this raised 'no such table: project_assumptions'."""
    db_file, real = wired
    _seed(real, db_file, "a1111111-0000-4000-8000-000000000001")

    _resolve(
        {
            "resolutions": [
                {
                    "type": "assumption",
                    "id": "a1111111-0000-4000-8000-000000000001",
                    "resolution": "measured, and it did not hold",
                }
            ]
        }
    )

    assert _status(real, db_file, "a1111111-0000-4000-8000-000000000001") == "falsified"


def test_verified_true_records_borne_out_not_falsified(wired):
    """The opposite event must be reachable, or the fix just inverts the loss."""
    db_file, real = wired
    _seed(real, db_file, "a2222222-0000-4000-8000-000000000002")

    _resolve(
        {
            "resolutions": [
                {
                    "type": "assumption",
                    "id": "a2222222-0000-4000-8000-000000000002",
                    "resolution": "checked against the live system",
                    "verified": True,
                }
            ]
        }
    )

    assert _status(real, db_file, "a2222222-0000-4000-8000-000000000002") == "verified"


def test_absent_verified_defaults_to_falsified_never_to_confirmation(wired):
    """A resolved-but-unstated assumption is far more often one that did not hold.

    Defaulting to `verified` would manufacture confirmation the practitioner never
    claimed — the same inversion as a retracted finding reporting CONFIRMED to its
    sources.
    """
    db_file, real = wired
    _seed(real, db_file, "a3333333-0000-4000-8000-000000000003")

    _resolve(
        {"resolutions": [{"type": "assumption", "id": "a3333333-0000-4000-8000-000000000003", "resolution": "done"}]}
    )

    assert _status(real, db_file, "a3333333-0000-4000-8000-000000000003") == "falsified"


def test_status_stays_within_the_schema_check_constraint(wired):
    """`assumptions.status` is CHECK'd — an out-of-vocabulary write raises rather
    than silently storing, so this pins that we only ever emit legal values."""
    db_file, real = wired
    _seed(real, db_file, "a4444444-0000-4000-8000-000000000004")

    _resolve(
        {
            "resolutions": [
                {
                    "type": "assumption",
                    "id": "a4444444-0000-4000-8000-000000000004",
                    "resolution": "r",
                    "verified": "not-a-bool",
                }
            ]
        }
    )

    assert _status(real, db_file, "a4444444-0000-4000-8000-000000000004") in (
        "unverified",
        "verified",
        "falsified",
    )


def test_a_missing_assumption_still_reports_not_found(wired):
    """The repair must not turn every miss into a hit."""
    db_file, real = wired
    _seed(real, db_file, "a5555555-0000-4000-8000-000000000005")

    db = real(db_path=db_file)
    try:
        before = db.conn.execute("SELECT COUNT(*) FROM assumptions WHERE status != 'unverified'").fetchone()[0]
    finally:
        db.close()

    _resolve({"resolutions": [{"type": "assumption", "id": "deadbeef-dead-4000-8000-000000000000", "resolution": "r"}]})

    db = real(db_path=db_file)
    try:
        after = db.conn.execute("SELECT COUNT(*) FROM assumptions WHERE status != 'unverified'").fetchone()[0]
    finally:
        db.close()

    assert before == after == 0, "a fabricated id must not resolve some other assumption"
