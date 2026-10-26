"""Tests for the mutants of empirica/data/repositories/vectors.py that the existing suite could not kill (goal c8e5c548, task R4).

The same hand-rolled AST mutation pass that took claims.py to 98.6% killed only 17 of 47 mutants here (36%): the nine test files that import
the vectors repository pin the NULL-vs-0.5 behaviour of store_vectors (A7) and little else. What nothing pinned: where a stored row's
project_id comes from (the session's authoritative value, then a git-derived fallback), the cycle filter of get_check_vectors, and that the
history getter drops vectors that were not assessed. Equivalent mutants left alone: the 5-second git timeouts and the `round` / `lastrowid`
defaults of paths that cannot be reached with a real row.
"""

from __future__ import annotations

import hashlib
import subprocess

import pytest

from empirica.data.repositories import vectors as V
from empirica.data.session_database import SessionDatabase


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "s.db"))
    yield d
    d.close()


def _git(path, *args):
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout.strip()


def _repo(path):
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    return path


def _stored_project(db, row_id):
    return db.conn.execute("SELECT project_id FROM reflexes WHERE id = ?", (row_id,)).fetchone()[0]


# ── where a row's project_id comes from ─────────────────────────────────────────────────────────────────────────


def test_the_session_project_id_is_read_when_the_session_has_one(db):
    sid = db.create_session(ai_id="a", project_id="proj-1")
    assert V._get_project_id_from_session(db.conn, sid) == "proj-1"


def test_a_missing_session_and_a_session_without_a_project_both_read_as_none(db):
    assert V._get_project_id_from_session(db.conn, "no-such-session") is None
    blank = db.create_session(ai_id="a")
    db.conn.execute("UPDATE sessions SET project_id = '' WHERE session_id = ?", (blank,))
    db.conn.commit()
    assert V._get_project_id_from_session(db.conn, blank) is None, "an empty project_id is not a project"


def test_a_failing_session_lookup_reads_as_none_not_as_an_error():
    class Broken:
        @staticmethod
        def execute(*_args, **_kwargs):
            raise RuntimeError("no database")

    assert V._get_project_id_from_session(Broken(), "s") is None


def test_store_vectors_prefers_the_session_project_over_the_git_fallback(db, monkeypatch):
    sid = db.create_session(ai_id="a", project_id="proj-1")
    monkeypatch.setattr(V, "_get_project_id_fallback", lambda: "from-git")
    row = db.vectors.store_vectors(sid, "PREFLIGHT", {"know": 0.5})
    assert _stored_project(db, row) == "proj-1"


def test_store_vectors_uses_an_explicit_project_id_before_either_lookup(db, monkeypatch):
    sid = db.create_session(ai_id="a", project_id="proj-1")
    monkeypatch.setattr(V, "_get_project_id_fallback", lambda: "from-git")
    row = db.vectors.store_vectors(sid, "PREFLIGHT", {"know": 0.5}, project_id="explicit")
    assert _stored_project(db, row) == "explicit"


def test_store_vectors_falls_back_to_the_git_derived_id_only_when_the_session_has_none(db, monkeypatch):
    sid = db.create_session(ai_id="a")
    monkeypatch.setattr(V, "_get_project_id_fallback", lambda: "from-git")
    row = db.vectors.store_vectors(sid, "PREFLIGHT", {"know": 0.5})
    assert _stored_project(db, row) == "from-git"


# ── the git-derived fallback ────────────────────────────────────────────────────────────────────────────────────


def test_the_fallback_hashes_the_origin_url_to_sixteen_hex_characters(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _git(repo, "remote", "add", "origin", "https://example.invalid/team/repo.git")
    monkeypatch.chdir(repo)
    assert V._get_project_id_fallback() == hashlib.sha256(b"https://example.invalid/team/repo.git").hexdigest()[:16]


def test_without_a_remote_the_fallback_hashes_the_checkout_path(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    toplevel = _git(repo, "rev-parse", "--show-toplevel")
    assert V._get_project_id_fallback() == hashlib.sha256(toplevel.encode()).hexdigest()[:16]


def test_outside_a_git_checkout_the_fallback_is_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert V._get_project_id_fallback() is None


# ── reading vectors back ────────────────────────────────────────────────────────────────────────────────────────


def test_get_check_vectors_filters_by_cycle_and_returns_all_when_no_cycle_is_given(db):
    sid = db.create_session(ai_id="a", project_id="p")
    db.vectors.store_vectors(sid, "CHECK", {"know": 0.5}, round_num=1)
    db.vectors.store_vectors(sid, "CHECK", {"know": 0.6}, round_num=2)
    db.vectors.store_vectors(sid, "PREFLIGHT", {"know": 0.4}, round_num=2)
    assert [v["round"] for v in db.vectors.get_check_vectors(sid, cycle=2)] == [2]
    assert sorted(v["round"] for v in db.vectors.get_check_vectors(sid)) == [1, 2]
    assert db.vectors.get_check_vectors(sid, cycle=3) == []


def test_a_vector_that_was_not_assessed_is_absent_from_the_history_not_present_as_none(db):
    sid = db.create_session(ai_id="a", project_id="p")
    db.vectors.store_vectors(sid, "CHECK", {"know": 0.7, "uncertainty": 0.2})
    (record,) = db.vectors.get_session_vector_history(sid)
    assert record["vectors"] == {"know": 0.7, "uncertainty": 0.2}


def test_a_row_stored_without_a_round_number_is_round_one(db):
    """(`round_num: int = 1` -> 2). The default is part of the contract get_check_vectors(cycle=1) relies on."""
    sid = db.create_session(ai_id="a", project_id="p")
    row = db.vectors.store_vectors(sid, "CHECK", {"know": 0.5})
    assert db.conn.execute("SELECT round FROM reflexes WHERE id = ?", (row,)).fetchone()[0] == 1
    assert [v["round"] for v in db.vectors.get_check_vectors(sid, cycle=1)] == [1]
