"""purge_ineligible_from_global removes what this project's ineligible artifacts left in the shared pool, and nothing else.

David's ruling 2026-10-06: sync stops sending local-tier, resolved or retracted artifacts AND the points they already left behind are
purged. The pool is read by peers, so the safeguards are the point of the tests: dry-run by default, only ids derived from THIS
project's own artifacts are ever looked up, a peer's point and an eligible point survive, and a failure is reported, not raised.
The Qdrant client is a fake: the live collection is never touched.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from empirica.core.qdrant import global_sync as gs

PROJECT = "proj-1"


class FakeClient:
    def __init__(self, point_ids):
        self.points = set(point_ids)
        self.retrieved: list[int] = []
        self.deleted: list[int] = []

    def collection_exists(self, name):
        return True

    def retrieve(self, collection_name, ids, with_payload=False):
        self.retrieved += list(ids)
        return [SimpleNamespace(id=i) for i in ids if i in self.points]

    def delete(self, collection_name, points_selector):
        self.deleted += list(points_selector)
        self.points -= set(points_selector)


class _SessionDB:
    def __init__(self, conn):
        self.conn = conn

    def close(self):
        pass


def _store():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE project_findings (id TEXT PRIMARY KEY, project_id TEXT, is_resolved INT DEFAULT 0, visibility TEXT);
        CREATE TABLE project_unknowns (id TEXT PRIMARY KEY, project_id TEXT, is_resolved INT DEFAULT 0, visibility TEXT);
        CREATE TABLE project_dead_ends (id TEXT PRIMARY KEY, project_id TEXT, visibility TEXT);
        """
    )
    rows = [
        ("project_findings", ("f-local", PROJECT, 0, "local")),
        ("project_findings", ("f-resolved", PROJECT, 1, "shared")),
        ("project_findings", ("f-null-visibility", PROJECT, 0, None)),
        ("project_findings", ("f-shared", PROJECT, 0, "shared")),
        ("project_findings", ("f-public", PROJECT, 0, "public")),
        ("project_findings", ("f-other-project-local", "proj-2", 0, "local")),
        ("project_unknowns", ("u-local", PROJECT, 1, "local")),
        ("project_unknowns", ("u-shared", PROJECT, 1, "shared")),
        ("project_dead_ends", ("d-local", PROJECT, "local")),
        ("project_dead_ends", ("d-shared", PROJECT, "shared")),
    ]
    for table, row in rows:
        conn.execute(f"INSERT INTO {table} VALUES ({', '.join('?' for _ in row)})", row)
    conn.commit()
    return conn


INELIGIBLE = {"f-local", "f-resolved", "f-null-visibility", "u-local", "d-local"}
ELIGIBLE_OR_FOREIGN = {"f-shared", "f-public", "f-other-project-local", "u-shared", "d-shared"}


@pytest.fixture
def pool(monkeypatch):
    conn = _store()
    peer_point = 123456789
    client = FakeClient([gs._global_point_id(i) for i in INELIGIBLE | ELIGIBLE_OR_FOREIGN] + [peer_point])
    monkeypatch.setattr(gs, "_check_qdrant_available", lambda *a, **k: True)
    monkeypatch.setattr(gs, "_get_qdrant_client", lambda *a, **k: client)
    monkeypatch.setattr(gs, "_global_learnings_collection", lambda: "global_test")
    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", lambda *a, **k: _SessionDB(conn))
    return SimpleNamespace(client=client, peer_point=peer_point)


def test_dry_run_counts_the_ineligible_points_and_deletes_nothing(pool):
    out = gs.purge_ineligible_from_global(PROJECT)
    assert out["ok"] is True and out["applied"] is False
    assert (out["candidates"], out["present"], out["deleted"]) == (5, 5, 0)
    assert out["by_type"] == {"finding": 3, "unknown": 1, "dead_end": 1}
    assert pool.client.deleted == []


def test_apply_deletes_exactly_the_ineligible_points_and_spares_everything_else(pool):
    out = gs.purge_ineligible_from_global(PROJECT, apply=True)
    assert (out["present"], out["deleted"]) == (5, 5)
    assert set(pool.client.deleted) == {gs._global_point_id(i) for i in INELIGIBLE}
    assert {gs._global_point_id(i) for i in ELIGIBLE_OR_FOREIGN} | {pool.peer_point} <= pool.client.points


def test_only_ids_derived_from_this_projects_artifacts_are_ever_looked_up(pool):
    gs.purge_ineligible_from_global(PROJECT, apply=True)
    assert pool.peer_point not in pool.client.retrieved
    assert gs._global_point_id("f-other-project-local") not in pool.client.retrieved


def test_a_second_apply_finds_nothing_left(pool):
    gs.purge_ineligible_from_global(PROJECT, apply=True)
    again = gs.purge_ineligible_from_global(PROJECT, apply=True)
    assert (again["present"], again["deleted"]) == (0, 0) and again["ok"] is True


def test_qdrant_unavailable_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(gs, "_check_qdrant_available", lambda *a, **k: False)
    out = gs.purge_ineligible_from_global(PROJECT, apply=True)
    assert out["ok"] is False and "unavailable" in out["error"]


def test_a_client_failure_is_reported_with_its_type(pool, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("qdrant went away")

    monkeypatch.setattr(pool.client, "retrieve", boom)
    out = gs.purge_ineligible_from_global(PROJECT, apply=True)
    assert out["ok"] is False and out["error"].startswith("ConnectionError") and out["deleted"] == 0


def test_a_missing_collection_is_an_empty_pool_not_an_error(pool, monkeypatch):
    monkeypatch.setattr(pool.client, "collection_exists", lambda name: False)
    out = gs.purge_ineligible_from_global(PROJECT, apply=True)
    assert out["ok"] is True and out["present"] == 0 and out["candidates"] == 5


def test_embed_and_purge_agree_on_the_point_id():
    """The purge finds points by recomputing the id embed_to_global writes; one function owns the formula."""
    import hashlib

    assert gs._global_point_id("abc") == int(hashlib.md5(b"global_abc").hexdigest()[:15], 16)
