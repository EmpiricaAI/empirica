"""A practice must be able to record that a claim was WRONG, not only that it aged.

Migration 057 gave findings ``is_resolved`` + a free-text ``resolution``. Free text
cannot be queried and — the load-bearing half — cannot be *offered*. What the surface
does not name, nobody reaches for.

Measured on the empirica practice 2026-07-30, after real gardening passes:

    4199 findings · 1268 resolved · 0 with superseded_by populated
    1267 resolutions mean stale/superseded/snapshot · 1 means error

A true error rate of 1-in-4199 over six months is not plausible, so errors were not
being EXPRESSED rather than not occurring. The rebuttal "we simply had not gardened"
does not apply — 1268 findings were gardened. Gardening itself was staleness-only.

These tests pin the vocabulary and, more importantly, the two places where a
retraction must behave DIFFERENTLY from a staleness resolution — otherwise the new
kind is a label nothing reads, which is how the free-text field failed.
"""

from __future__ import annotations

import pytest

from empirica.data.resolution_kind import (
    RESOLUTION_KINDS,
    is_retraction,
    normalize_resolution_kind,
)
from empirica.data.session_database import SessionDatabase


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "t.db"))
    try:
        yield d
    finally:
        d.close()


def _insert_finding(db, fid: str, text: str = "a claim") -> str:
    db.conn.execute(
        "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, finding_data) "
        "VALUES (?,?,?,?,?,?)",
        (fid, "p", "s", text, 0.0, "{}"),
    )
    db.conn.commit()
    return fid


# ── the vocabulary ────────────────────────────────────────────────────


def test_retracted_is_in_the_vocabulary():
    """THE regression. Everything else here is scaffolding around this one word."""
    assert "retracted" in RESOLUTION_KINDS


def test_the_four_kinds_are_the_closed_set():
    assert set(RESOLUTION_KINDS) == {"stale", "superseded", "retracted", "mistyped"}


@pytest.mark.parametrize("bad", ["wrong", "", "  ", "STALE-ish", None, "invalidated"])
def test_unrecognised_values_normalize_to_none_never_to_a_guess(bad):
    """NULL means 'not classified' and must stay reachable.

    Coercing an unknown value to the nearest kind would silently manufacture the
    exact misclassification being measured — a retraction recorded as staleness.
    Note `invalidated` is deliberately NOT accepted: it is the dead_end/mistake
    verb from migration 060, and quietly aliasing it here would blur two lifecycles.
    """
    assert normalize_resolution_kind(bad) is None


@pytest.mark.parametrize("good", ["stale", "SUPERSEDED", " retracted ", "Mistyped"])
def test_case_and_whitespace_tolerated(good):
    assert normalize_resolution_kind(good) in RESOLUTION_KINDS


def test_is_retraction_separates_wrong_from_old():
    """The predicate the calibration surfaces care about: how often did this
    practice discover it was WRONG? That number read as zero for six months."""
    assert is_retraction("retracted") is True
    assert is_retraction("mistyped") is True, "a mistake logged as a finding was never a finding"
    assert is_retraction("stale") is False, "ageing is not error"
    assert is_retraction("superseded") is False, "being replaced is not being wrong"
    assert is_retraction(None) is False


# ── it must actually persist ──────────────────────────────────────────


def test_resolve_finding_persists_the_kind(db):
    _insert_finding(db, "aaaaaaaa-0000-4000-8000-000000000001")

    assert db.resolve_finding(
        "aaaaaaaa-0000-4000-8000-000000000001",
        "the benchmark never showed this",
        resolution_kind="retracted",
    )

    row = db.conn.execute(
        "SELECT is_resolved, resolution_kind FROM project_findings WHERE id LIKE 'aaaaaaaa%'"
    ).fetchone()
    assert row[0]
    assert row[1] == "retracted"


def test_an_invalid_kind_stores_null_rather_than_failing_the_resolve(db):
    """Resolution is the correction path. It must not become harder to correct a
    finding than to log one — a bad --kind degrades to unclassified, it does not
    block the resolve."""
    _insert_finding(db, "bbbbbbbb-0000-4000-8000-000000000002")

    assert db.resolve_finding("bbbbbbbb-0000-4000-8000-000000000002", "reason", resolution_kind="nonsense")

    row = db.conn.execute(
        "SELECT is_resolved, resolution_kind FROM project_findings WHERE id LIKE 'bbbbbbbb%'"
    ).fetchone()
    assert row[0], "the resolve itself must still land"
    assert row[1] is None


def test_omitting_the_kind_keeps_the_pre_existing_behaviour(db):
    """1268 existing resolutions carry no kind and are deliberately not backfilled —
    inferring intent from months-old prose is inference, not fact."""
    _insert_finding(db, "cccccccc-0000-4000-8000-000000000003")

    assert db.resolve_finding("cccccccc-0000-4000-8000-000000000003", "stale — subsystem removed")

    row = db.conn.execute(
        "SELECT is_resolved, resolution_kind FROM project_findings WHERE id LIKE 'cccccccc%'"
    ).fetchone()
    assert row[0]
    assert row[1] is None


def test_the_stale_vs_wrong_split_is_queryable_in_sql(db):
    """The whole point. This split previously required grepping free text for
    error-words, which is how it went unnoticed for 4199 findings."""
    for i, kind in enumerate(["stale", "stale", "retracted", "superseded"]):
        fid = f"dddddddd-000{i}-4000-8000-00000000000{i}"
        _insert_finding(db, fid)
        db.resolve_finding(fid, "r", resolution_kind=kind)

    wrong = db.conn.execute(
        "SELECT COUNT(*) FROM project_findings WHERE resolution_kind IN ('retracted','mistyped')"
    ).fetchone()[0]
    aged = db.conn.execute(
        "SELECT COUNT(*) FROM project_findings WHERE resolution_kind IN ('stale','superseded')"
    ).fetchone()[0]

    assert (wrong, aged) == (1, 3)


# ── the behaviour that must DIFFER, or the label is inert ─────────────


def test_retracting_a_finding_does_not_tell_its_sources_they_were_confirmed(tmp_path, monkeypatch):
    """Latent bug this vocabulary exposed.

    The batch resolve recorded a source outcome of "confirmed" whenever
    `superseded_by` was absent. That was harmless while every resolution meant
    "stale" — but a RETRACTED finding was never true, and reporting CONFIRMED to
    the sources it cited inverts the signal exactly where it matters most.

    The handler calls a bare ``SessionDatabase()``, which resolves sessions.db from
    ambient git/context — so the db is pinned explicitly here. A previous test of
    mine passed locally and failed in CI for exactly that reason.
    """
    import empirica.data.session_database as _sdb
    from empirica.cli.command_handlers.graph_commands import handle_resolve_artifacts_command

    db_file = str(tmp_path / "t.db")
    _real = _sdb.SessionDatabase
    monkeypatch.setattr(_sdb, "SessionDatabase", lambda *a, **k: _real(db_path=db_file))

    db = _real(db_path=db_file)
    try:
        src = db.breadcrumbs.create_source(
            project_id="p", session_id="s", title="a misleading doc", url="http://x", source_type="reference"
        )
        fid = "eeeeeeee-0000-4000-8000-000000000009"
        db.conn.execute(
            "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, "
            "finding_data) VALUES (?,?,?,?,?,?)",
            (fid, "p", "s", "claim built on the doc", 0.0, "{}"),
        )
        # The outcome channel walks artifact_edges, not the source_refs column —
        # the citation must be a real graph edge to be fed back.
        db.conn.execute(
            "INSERT INTO artifact_edges (from_id, to_id, relation) VALUES (?,?,?)",
            (fid, src, "sourced_from"),
        )
        db.conn.commit()
    finally:
        db.close()

    import json as _json

    payload = {
        "resolutions": [{"type": "finding", "id": fid, "resolution": "was false", "resolution_kind": "retracted"}]
    }

    class _Args:
        input = "-"
        output = "json"
        verbose = False

    import io
    import sys

    stdin, sys.stdin = sys.stdin, io.StringIO(_json.dumps(payload))
    try:
        handle_resolve_artifacts_command(_Args())
    finally:
        sys.stdin = stdin

    db = _real(db_path=db_file)
    try:
        log = db.conn.execute("SELECT lifecycle_audit_log FROM epistemic_sources WHERE id = ?", (src,)).fetchone()[0]
    finally:
        db.close()

    assert "confirmed" not in (log or ""), "a retracted finding must never report CONFIRMED to its sources"
    assert "retracted" in (log or "")
