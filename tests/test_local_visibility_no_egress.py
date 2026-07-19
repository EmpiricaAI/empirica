"""visibility=local is a true no-egress tier — its content must never sync to cortex.

Backs the getempirica.com local-first claim: an artifact tagged visibility=local
stays in local SQLite + local vector store and is excluded from every cortex
/v1/sync content path. Default 'shared' (and legacy NULL) still sync.

Gated paths:
  - POSTFLIGHT: _workflow_postflight._cortex_extract_transaction_delta + the
    graph extractor (_cortex_graph_artifact_nodes) — SQL predicate.
  - session-init: _build_cortex_sync_delta (+ _local_artifact_ids) — id lookup.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import empirica.cli.command_handlers._workflow_postflight as wp

HOOK_PATH = (
    Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "session-init.py"
)


def _load_hook_module():
    spec = importlib.util.spec_from_file_location("session_init_hook", HOOK_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    plugin_lib = HOOK_PATH.parent.parent / "lib"
    sys.path.insert(0, str(plugin_lib))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(plugin_lib))
    return mod


# --- schema shared by the postflight extractor tests -----------------------

_SCHEMA = {
    "project_findings": "id TEXT, transaction_id TEXT, goal_id TEXT, finding TEXT, impact REAL, subject TEXT, visibility TEXT",
    "project_unknowns": "id TEXT, transaction_id TEXT, goal_id TEXT, unknown TEXT, subject TEXT, visibility TEXT",
    "project_dead_ends": "id TEXT, transaction_id TEXT, goal_id TEXT, approach TEXT, why_failed TEXT, impact REAL, subject TEXT, visibility TEXT",
    "mistakes_made": "id TEXT, transaction_id TEXT, goal_id TEXT, mistake TEXT, why_wrong TEXT, prevention TEXT, visibility TEXT",
    "assumptions": "id TEXT, transaction_id TEXT, goal_id TEXT, assumption TEXT, confidence REAL, status TEXT, visibility TEXT",
    "decisions": "id TEXT, transaction_id TEXT, goal_id TEXT, choice TEXT, rationale TEXT, alternatives TEXT, reversibility TEXT, visibility TEXT",
    "artifact_edges": "from_id TEXT, to_id TEXT, relation TEXT",
}


class _FakeDB:
    def __init__(self, conn):
        self.conn = conn

    def close(self):
        pass


def _mkconn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "s.db"))
    conn.row_factory = sqlite3.Row
    for tbl, cols in _SCHEMA.items():
        conn.execute(f"CREATE TABLE {tbl} ({cols})")
    return conn


def _seed_findings(conn, tx):
    conn.execute(
        "INSERT INTO project_findings (id, transaction_id, finding, impact, visibility) VALUES (?,?,?,?,?)",
        ("f-shared", tx, "shared finding text", 0.5, "shared"),
    )
    conn.execute(
        "INSERT INTO project_findings (id, transaction_id, finding, impact, visibility) VALUES (?,?,?,?,?)",
        ("f-public", tx, "public finding text", 0.5, "public"),
    )
    conn.execute(
        "INSERT INTO project_findings (id, transaction_id, finding, impact, visibility) VALUES (?,?,?,?,?)",
        ("f-null", tx, "null-visibility finding", 0.5, None),
    )
    conn.execute(
        "INSERT INTO project_findings (id, transaction_id, finding, impact, visibility) VALUES (?,?,?,?,?)",
        ("f-local", tx, "SECRET local finding text", 0.5, "local"),
    )
    conn.commit()


# --- POSTFLIGHT delta extractor -------------------------------------------


def test_postflight_delta_excludes_local(tmp_path, monkeypatch):
    tx = "tx-1"
    conn = _mkconn(tmp_path)
    _seed_findings(conn, tx)
    monkeypatch.setattr(wp.R, "transaction_read", staticmethod(lambda: {"transaction_id": tx}))
    monkeypatch.setattr(wp, "_get_db_for_session", lambda _sid: _FakeDB(conn))

    delta = wp._cortex_extract_transaction_delta("sess")
    texts = [f.get("finding") for f in delta.get("findings", [])]
    assert "shared finding text" in texts
    assert "public finding text" in texts
    assert "null-visibility finding" in texts  # default-shared, syncs
    assert "SECRET local finding text" not in texts  # withheld


# --- POSTFLIGHT graph extractor -------------------------------------------


def test_postflight_graph_excludes_local(tmp_path, monkeypatch):
    tx = "tx-2"
    conn = _mkconn(tmp_path)
    _seed_findings(conn, tx)
    monkeypatch.setattr(wp.R, "transaction_read", staticmethod(lambda: {"transaction_id": tx}))
    monkeypatch.setattr(wp, "_get_db_for_session", lambda _sid: _FakeDB(conn))

    graph = wp._cortex_extract_transaction_graph("sess")
    node_findings = [n["data"].get("finding") for n in graph.get("nodes", []) if n["type"] == "finding"]
    assert "shared finding text" in node_findings
    assert "SECRET local finding text" not in node_findings


# --- session-init breadcrumb delta ----------------------------------------


def _seed_sessioninit_db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "s.db"))
    conn.execute("CREATE TABLE project_findings (id TEXT, finding TEXT, visibility TEXT)")
    conn.execute("CREATE TABLE project_unknowns (id TEXT, unknown TEXT, visibility TEXT)")
    conn.execute("INSERT INTO project_findings VALUES ('f-sh','shared f','shared')")
    conn.execute("INSERT INTO project_findings VALUES ('f-lo','SECRET f','local')")
    conn.execute("INSERT INTO project_unknowns VALUES ('u-sh','shared u','shared')")
    conn.execute("INSERT INTO project_unknowns VALUES ('u-lo','SECRET u','local')")
    conn.commit()
    return conn


def test_sessioninit_delta_excludes_local(tmp_path, monkeypatch):
    mod = _load_hook_module()
    conn = _seed_sessioninit_db(tmp_path)

    class _DB:
        def __init__(self):
            self.conn = conn

        def close(self):
            pass

    monkeypatch.setattr(mod, "SessionDatabase", lambda *a, **k: _DB(), raising=False)
    # ensure the helper's local import resolves to our fake
    import empirica.data.session_database as sdb_mod

    monkeypatch.setattr(sdb_mod, "SessionDatabase", lambda *a, **k: _DB())

    bootstrap = {
        "breadcrumbs": {
            "findings": [
                {"id": "f-sh", "finding": "shared f", "impact": 0.5},
                {"id": "f-lo", "finding": "SECRET f", "impact": 0.5},
            ],
            "unknowns": [{"id": "u-sh", "unknown": "shared u"}, {"id": "u-lo", "unknown": "SECRET u"}],
        }
    }
    delta = mod._build_cortex_sync_delta(bootstrap)
    fs = [f["finding"] for f in delta["findings"]]
    us = [u["unknown"] for u in delta["unknowns"]]
    assert "shared f" in fs and "SECRET f" not in fs
    assert "shared u" in us and "SECRET u" not in us


def test_local_artifact_ids_fail_closed(monkeypatch):
    """If visibility can't be determined, withhold everything (never leak)."""
    mod = _load_hook_module()
    import empirica.data.session_database as sdb_mod

    def _boom(*a, **k):
        raise OSError("db unavailable")

    monkeypatch.setattr(sdb_mod, "SessionDatabase", _boom)
    out = mod._local_artifact_ids(["a", "b"], ["c"])
    assert out == {"a", "b", "c"}  # all treated as local → withheld


def test_local_artifact_ids_empty():
    mod = _load_hook_module()
    assert mod._local_artifact_ids([], []) == set()
    assert mod._local_artifact_ids([None], [None]) == set()
