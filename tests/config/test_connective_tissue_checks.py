"""Tests for the connective-tissue checks (graph-edge health).

Three domain-agnostic checks added to the CheckDeclaration registry:
- edge_density      — this transaction's artifacts connect into the graph
- orphan_artifacts  — flag session artifacts fully disconnected
- dangling_edges    — edges must reference artifacts that exist (integrity)

They query the canonical artifact_edges table. These tests build a minimal
in-memory schema and monkeypatch SessionDatabase so the runners exercise real
SQL against controlled data.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.config import service_registry as sr
from empirica.config.service_registry import ServiceRegistry

_ARTIFACT_TABLES = (
    "project_findings", "project_unknowns", "project_dead_ends",
    "mistakes_made", "assumptions", "decisions",
)


class _FakeDB:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def close(self) -> None:
        pass


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    for t in _ARTIFACT_TABLES:
        cur.execute(
            f"CREATE TABLE {t} (id TEXT PRIMARY KEY, session_id TEXT, transaction_id TEXT)"
        )
    cur.execute(
        "CREATE TABLE artifact_edges ("
        "from_id TEXT, to_id TEXT, relation TEXT, "
        "PRIMARY KEY (from_id, to_id, relation))"
    )
    conn.commit()
    return conn


def _add_finding(conn, fid, session_id="S", tx="T"):
    conn.execute(
        "INSERT INTO project_findings (id, session_id, transaction_id) VALUES (?, ?, ?)",
        (fid, session_id, tx),
    )
    conn.commit()


def _add_edge(conn, from_id, to_id, relation="evidence"):
    conn.execute(
        "INSERT OR IGNORE INTO artifact_edges (from_id, to_id, relation) VALUES (?, ?, ?)",
        (from_id, to_id, relation),
    )
    conn.commit()


@pytest.fixture
def patched(monkeypatch):
    """Yield a fresh in-memory conn; SessionDatabase() returns a FakeDB over it."""
    conn = _make_conn()
    monkeypatch.setattr(
        "empirica.data.session_database.SessionDatabase",
        lambda *a, **k: _FakeDB(conn),
    )
    return conn


_CTX = {"session_id": "S", "transaction_id": "T"}


# --- registry-level: registered + domain-agnostic ---------------------------- #
def test_connective_checks_registered_and_domain_agnostic():
    ServiceRegistry.load_builtins()
    ids = ServiceRegistry.list_all()
    for cid in ("edge_density", "orphan_artifacts", "dangling_edges"):
        assert cid in ids, f"{cid} not registered"
    triad = {"edge_density", "orphan_artifacts", "dangling_edges"}
    # Domain-agnostic: resolve for arbitrary (work_type, domain) tuples
    for wt, dom in [("code", "default"), ("research", "legal"), ("comms", "whatever")]:
        resolved = {d.check_id for d in ServiceRegistry.resolve_for(wt, dom)}
        assert triad <= resolved
    # But NOT remote-ops — local sensors can't observe remote work, so no
    # builtin checks apply there (same invariant as artifact_breadth).
    remote = {d.check_id for d in ServiceRegistry.resolve_for("remote-ops", "default")}
    assert not (triad & remote)


# --- edge_density ------------------------------------------------------------ #
def test_edge_density_flags_unconnected_transaction(patched):
    _add_finding(patched, "f1")
    _add_finding(patched, "f2")  # 2 artifacts, no edges
    r = sr._run_edge_density_check(_CTX)
    assert r.passed is False
    assert r.details["total"] == 2
    assert r.details["connected"] == 0


def test_edge_density_passes_when_connected(patched):
    _add_finding(patched, "f1")
    _add_finding(patched, "f2")
    _add_edge(patched, "f1", "f2")
    r = sr._run_edge_density_check(_CTX)
    assert r.passed is True
    assert r.details["connected"] == 2


def test_edge_density_na_below_two_artifacts(patched):
    _add_finding(patched, "f1")  # only 1
    r = sr._run_edge_density_check(_CTX)
    assert r.passed is True
    assert r.details["total"] == 1


# --- orphan_artifacts -------------------------------------------------------- #
def test_orphan_flags_when_majority_disconnected(patched):
    for fid in ("f1", "f2", "f3"):
        _add_finding(patched, fid)  # 3 artifacts, no edges → all orphaned
    r = sr._run_orphan_artifacts_check(_CTX)
    assert r.passed is False
    assert r.details["orphans"] == 3


def test_orphan_passes_when_minority_disconnected(patched):
    for fid in ("f1", "f2", "f3"):
        _add_finding(patched, fid)
    _add_edge(patched, "f1", "f2")  # f1,f2 connected; only f3 orphaned (1/3)
    r = sr._run_orphan_artifacts_check(_CTX)
    assert r.passed is True
    assert r.details["orphans"] == 1


def test_orphan_na_below_three_artifacts(patched):
    _add_finding(patched, "f1")
    _add_finding(patched, "f2")
    r = sr._run_orphan_artifacts_check(_CTX)
    assert r.passed is True
    assert r.details["total"] == 2


# --- dangling_edges ---------------------------------------------------------- #
def test_dangling_flags_edge_to_missing_artifact(patched):
    _add_finding(patched, "f1")
    _add_edge(patched, "f1", "ghost")  # to_id doesn't exist
    r = sr._run_dangling_edges_check(_CTX)
    assert r.passed is False
    assert r.details["dangling"] == 1
    assert r.details["examples"][0]["to"] == "ghost"


def test_dangling_passes_when_all_resolve(patched):
    _add_finding(patched, "f1")
    _add_finding(patched, "f2")
    _add_edge(patched, "f1", "f2")
    r = sr._run_dangling_edges_check(_CTX)
    assert r.passed is True
    assert r.details["dangling"] == 0


def test_dangling_na_when_no_edges(patched):
    _add_finding(patched, "f1")
    r = sr._run_dangling_edges_check(_CTX)
    assert r.passed is True
    assert r.details["total_edges"] == 0


# --- non-blocking contract: no session → skipped, never raises --------------- #
@pytest.mark.parametrize("runner", [
    sr._run_edge_density_check,
    sr._run_orphan_artifacts_check,
    sr._run_dangling_edges_check,
])
def test_no_session_skips_cleanly(runner):
    r = runner({})
    assert r.passed is True
    assert r.details.get("skipped") is True
