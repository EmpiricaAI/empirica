"""An epistemic snapshot must carry the vectors the session really reported (goal 1cd7caa3, HIGH item).

`EpistemicSnapshotProvider._extract_vectors_from_assessment` mapped a set of LEGACY column names (``epistemic_humility``,
``cognitive_flexibility``, ...) that exist in no table, and read them off a flat dict, while `get_latest_vectors` returns the
thirteen values nested under ``vectors`` with the names the reflexes table has (``know``, ``do``, ...). Every lookup missed, so
every snapshot was thirteen 0.5s whatever the session had reported, and the delta between two snapshots, which is computed from
them, was always zero.

The test goes through a real database and the real reader. A test that fed the extractor a dict in the shape the extractor
expected would have been green throughout, which is how this survived.
"""

from __future__ import annotations

import pytest

from empirica.data.session_database import SessionDatabase
from empirica.data.snapshot_provider import EpistemicSnapshotProvider

REPORTED = {
    "know": 0.81,
    "do": 0.72,
    "context": 0.66,
    "clarity": 0.9,
    "coherence": 0.55,
    "signal": 0.61,
    "density": 0.43,
    "state": 0.77,
    "change": 0.12,
    "completion": 0.0,
    "impact": 0.88,
    "engagement": 0.95,
    "uncertainty": 0.19,
}


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "s.db"))
    yield d
    d.close()


def _session_with_preflight(db, vectors):
    sid = db.create_session(ai_id="snapshot-test")
    db.vectors.store_vectors(sid, "PREFLIGHT", vectors)
    return sid


def test_a_snapshot_carries_the_preflight_the_session_reported(db):
    sid = _session_with_preflight(db, REPORTED)
    snapshot = EpistemicSnapshotProvider(db).create_snapshot_from_session(sid)
    assert snapshot.vectors == {k.upper(): pytest.approx(v) for k, v in REPORTED.items()}


def test_the_values_are_not_the_neutral_default(db):
    """Control for the bug itself: at least one reported value differs from 0.5, so a default-filled snapshot cannot pass."""
    sid = _session_with_preflight(db, REPORTED)
    snapshot = EpistemicSnapshotProvider(db).create_snapshot_from_session(sid)
    assert any(abs(v - 0.5) > 0.2 for v in snapshot.vectors.values())


def test_a_session_with_no_preflight_gets_the_neutral_default_and_says_nothing_was_measured(db):
    sid = db.create_session(ai_id="snapshot-test")
    snapshot = EpistemicSnapshotProvider(db).create_snapshot_from_session(sid)
    assert set(snapshot.vectors.values()) == {0.5}, "no measurement: the neutral default, as before"


def test_a_partial_preflight_keeps_what_was_reported_and_defaults_only_the_rest(db):
    sid = _session_with_preflight(db, {"know": 0.9, "uncertainty": 0.1})
    snapshot = EpistemicSnapshotProvider(db).create_snapshot_from_session(sid)
    assert snapshot.vectors["KNOW"] == pytest.approx(0.9) and snapshot.vectors["UNCERTAINTY"] == pytest.approx(0.1)
    assert snapshot.vectors["CLARITY"] == 0.5


def test_the_extractor_reads_the_shape_get_latest_vectors_returns(db):
    sid = _session_with_preflight(db, REPORTED)
    assessment = db.get_preflight_assessment(sid)
    assert assessment is not None and "vectors" in assessment, "the contract the extractor must follow"
    out = EpistemicSnapshotProvider(db)._extract_vectors_from_assessment(assessment)
    assert out["KNOW"] == pytest.approx(0.81) and out["ENGAGEMENT"] == pytest.approx(0.95)


def test_a_flat_dict_of_lowercase_vectors_is_accepted_too(db):
    """Other callers hand the extractor a flat {name: value} dict; both shapes must work."""
    out = EpistemicSnapshotProvider(db)._extract_vectors_from_assessment({"know": 0.3, "do": 0.4})
    assert out["KNOW"] == pytest.approx(0.3) and out["DO"] == pytest.approx(0.4) and out["CONTEXT"] == 0.5


def test_a_later_snapshot_shows_the_change_between_preflights(db):
    """The delta is computed from the snapshot vectors, so it was always zero while they were all 0.5."""
    sid = _session_with_preflight(db, {**REPORTED, "know": 0.4})
    provider = EpistemicSnapshotProvider(db)
    first = provider.create_snapshot_from_session(sid)
    db.vectors.store_vectors(sid, "PREFLIGHT", {**REPORTED, "know": 0.9})
    second = provider.create_snapshot_from_session(sid)
    assert first.vectors["KNOW"] == pytest.approx(0.4) and second.vectors["KNOW"] == pytest.approx(0.9)
