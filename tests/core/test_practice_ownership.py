"""A session that belongs to another practice is recognised as such.

David, via mesh-support prop_337a3aqsffem7a7ozcbruc3vba: a transaction started
from one practice's checkout against another practice's session must be refused,
because it writes into that practice's store. It happened and left an orphan
PREFLIGHT row in core (unknown 4ca7fcc7).

The discrimination that matters is between "owned elsewhere" (refuse) and "exists
nowhere" (warn, which is a legitimate first transaction). Every path here is
built under tmp_path: these tests must not read the developer's registry.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from empirica.core.practice_ownership import find_owning_practice, registered_practices


def _store(root, name, sessions=()):
    """A practice checkout with a sessions store, as the registry expects it."""
    db_dir = root / name / ".empirica" / "sessions"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(str(db_dir / "sessions.db"))
    conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO sessions VALUES (?)", [(s,) for s in sessions])
    conn.commit()
    conn.close()
    return db_dir / "sessions.db"


def _registry(tmp_path, entries):
    """entries: [(project_id, display_name, trajectory_path or None)]"""
    path = tmp_path / "workspace.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE entity_registry (entity_type TEXT, entity_id TEXT, display_name TEXT, metadata TEXT)")
    conn.executemany(
        "INSERT INTO entity_registry VALUES ('project', ?, ?, ?)",
        [(pid, name, json.dumps({"trajectory_path": str(t)}) if t else None) for pid, name, t in entries],
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def two_practices(tmp_path):
    peer_db = _store(tmp_path, "peer", sessions=["sess-peer"])
    local_db = _store(tmp_path, "local", sessions=["sess-local"])
    reg = _registry(
        tmp_path,
        [
            ("p-peer", "Peer Practice", tmp_path / "peer" / ".empirica"),
            ("p-local", "Local Practice", tmp_path / "local" / ".empirica"),
        ],
    )
    return reg, local_db, peer_db


def test_a_session_owned_elsewhere_names_the_practice(two_practices):
    reg, local_db, _ = two_practices
    owner = find_owning_practice("sess-peer", workspace_db=reg, exclude_db=local_db)
    assert owner is not None
    assert owner["name"] == "Peer Practice" and owner["project_id"] == "p-peer"


def test_a_session_that_exists_nowhere_is_not_owned(two_practices):
    """The case that must stay a warning: a first transaction on a new session."""
    reg, local_db, _ = two_practices
    assert find_owning_practice("sess-unknown", workspace_db=reg, exclude_db=local_db) is None


def test_the_local_store_is_excluded_so_a_local_session_is_never_foreign(two_practices):
    reg, local_db, _ = two_practices
    assert find_owning_practice("sess-local", workspace_db=reg, exclude_db=local_db) is None


def test_without_the_exclusion_the_local_store_would_answer(two_practices):
    """Positive control for the exclusion: the row IS findable through the registry."""
    reg, _, _ = two_practices
    assert find_owning_practice("sess-local", workspace_db=reg)["name"] == "Local Practice"


def test_an_absent_registry_claims_no_ownership(tmp_path):
    """No registry is not evidence of ownership — PREFLIGHT's warning must stand."""
    assert registered_practices(tmp_path / "nope.db") == []
    assert find_owning_practice("sess-peer", workspace_db=tmp_path / "nope.db") is None


def test_rows_without_a_path_or_without_a_store_are_skipped(tmp_path):
    reg = _registry(
        tmp_path,
        [
            ("p-nometa", "No Path", None),
            ("p-gone", "Never Created", tmp_path / "gone" / ".empirica"),
        ],
    )
    assert registered_practices(reg) == []


def test_an_unreadable_store_is_skipped_not_fatal(tmp_path):
    good = _store(tmp_path, "good", sessions=["sess-x"])
    bad_dir = tmp_path / "bad" / ".empirica" / "sessions"
    bad_dir.mkdir(parents=True)
    (bad_dir / "sessions.db").write_text("not a database")
    reg = _registry(
        tmp_path,
        [
            ("p-bad", "Corrupt", tmp_path / "bad" / ".empirica"),
            ("p-good", "Good", tmp_path / "good" / ".empirica"),
        ],
    )
    assert find_owning_practice("sess-x", workspace_db=reg)["name"] == "Good"
    assert good.is_file()
