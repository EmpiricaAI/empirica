"""project-identity reconcile — converge local stores to the cortex-canonical UUID.

Root cause of web's media blocker: a local project_id can diverge from cortex's
registered id and nothing converges them. The fix (cortex-authority) rekeys
EVERY id-of-record store together — completeness is the guard against the
documented stranding bug (prop_xa6djztv5), which was an *incomplete* correction.
"""

from __future__ import annotations

import sqlite3
import types

import yaml

from empirica.cli.command_handlers.projects_commands import _reconcile_identity_if_diverged
from empirica.core.identity_migration import (
    _rekey_workspace_db,
    reconcile_project_identity,
)

OLD = "258aa934-a34b-4773-b1bb-96f429de6761"
NEW = "dc6298e2-6262-44e6-a468-45cf63ef040e"


def _make_local_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE sessions (session_id TEXT, project_id TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('s1', ?)", (OLD,))
    conn.execute("INSERT INTO sessions VALUES ('s2', ?)", (OLD,))
    conn.commit()
    conn.close()


def _make_workspace_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE global_projects (id TEXT, trajectory_path TEXT)")
    conn.execute("INSERT INTO global_projects VALUES (?, '/x/.empirica')", (OLD,))
    conn.execute("CREATE TABLE entity_registry (entity_type TEXT, entity_id TEXT)")
    conn.execute("INSERT INTO entity_registry VALUES ('project', ?)", (OLD,))
    conn.execute("INSERT INTO entity_registry VALUES ('contact', 'other')")  # must NOT change
    conn.commit()
    conn.close()


def test_reconcile_rekeys_all_local_stores(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".empirica" / "sessions").mkdir(parents=True)
    _make_local_db(proj / ".empirica" / "sessions" / "sessions.db")
    (proj / ".empirica" / "project.yaml").write_text(
        yaml.safe_dump({"name": "P", "project_id": OLD, "ai_id": "p"}), encoding="utf-8"
    )
    ws = tmp_path / "workspace.db"
    _make_workspace_db(ws)

    rep = reconcile_project_identity(proj, OLD, NEW, workspace_db=ws)

    assert rep["reconciled"] is True
    assert rep["qdrant_rebuild_needed"] is True
    # local db rekeyed
    conn = sqlite3.connect(str(proj / ".empirica" / "sessions" / "sessions.db"))
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE project_id = ?", (NEW,)).fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE project_id = ?", (OLD,)).fetchone()[0] == 0
    conn.close()
    # workspace rekeyed (global_projects + entity_registry project row only)
    conn = sqlite3.connect(str(ws))
    assert conn.execute("SELECT id FROM global_projects").fetchone()[0] == NEW
    assert conn.execute("SELECT entity_id FROM entity_registry WHERE entity_type='project'").fetchone()[0] == NEW
    assert conn.execute("SELECT entity_id FROM entity_registry WHERE entity_type='contact'").fetchone()[0] == "other"
    conn.close()
    # project.yaml rewritten
    cfg = yaml.safe_load((proj / ".empirica" / "project.yaml").read_text())
    assert cfg["project_id"] == NEW
    assert cfg["ai_id"] == "p"  # other keys preserved


def test_reconcile_noop_when_aligned(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".empirica").mkdir(parents=True)
    rep = reconcile_project_identity(proj, NEW, NEW)
    assert rep["reconciled"] is False
    assert rep["reason"] == "already_aligned"


def test_workspace_rekey_noop_on_missing_db(tmp_path):
    assert _rekey_workspace_db(OLD, NEW, tmp_path / "nope.db") == {}


def _args(reconcile, force=False):
    return types.SimpleNamespace(reconcile=reconcile, force=force)


def test_reconcile_blocked_by_open_transaction(monkeypatch):
    # Mid-session reconcile is refused (would rekey the live session's own rows).
    import empirica.utils.session_resolver as sr

    monkeypatch.setattr(sr.InstanceResolver, "transaction_read", staticmethod(lambda: {"status": "open"}))
    out = _reconcile_identity_if_diverged(_args(True), "/p", OLD, {"project_id": NEW, "outcome": "registered"}, "json")
    assert out["reconciled"] is False
    assert out["blocked"] == "open_transaction"


def test_reconcile_force_bypasses_open_transaction(tmp_path, monkeypatch):
    import empirica.core.identity_migration as im
    import empirica.utils.session_resolver as sr

    monkeypatch.setattr(sr.InstanceResolver, "transaction_read", staticmethod(lambda: {"status": "open"}))
    monkeypatch.setattr(im, "_rekey_workspace_db", lambda *a, **k: {})
    monkeypatch.setattr(im, "_rekey_registry_yaml", lambda *a, **k: False)
    proj = tmp_path / "proj"
    (proj / ".empirica").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text(yaml.safe_dump({"project_id": OLD}), encoding="utf-8")
    out = _reconcile_identity_if_diverged(
        _args(True, force=True), proj, OLD, {"project_id": NEW, "outcome": "registered"}, "json"
    )
    assert out.get("blocked") is None
    assert out["reconciled"] is True


def test_divergence_detection_aligned_returns_none():
    # cortex id == local → no divergence
    out = _reconcile_identity_if_diverged(_args(False), "/p", OLD, {"project_id": OLD, "outcome": "skipped"}, "json")
    assert out is None


def test_divergence_detection_no_cortex_id_returns_none():
    out = _reconcile_identity_if_diverged(_args(False), "/p", OLD, {"outcome": "failed"}, "json")
    assert out is None


def test_divergence_detection_owner_conflict_never_adopts():
    # owner_conflict must NEVER adopt a foreign id
    out = _reconcile_identity_if_diverged(
        _args(True), "/p", OLD, {"project_id": NEW, "outcome": "owner_conflict"}, "json"
    )
    assert out is None


def test_divergence_warn_only_without_reconcile():
    out = _reconcile_identity_if_diverged(_args(False), "/p", OLD, {"project_id": NEW, "outcome": "skipped"}, "json")
    assert out == {"local_id": OLD, "cortex_id": NEW, "reconciled": False}


def test_divergence_reconciles_with_flag(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / ".empirica" / "sessions").mkdir(parents=True)
    _make_local_db(proj / ".empirica" / "sessions" / "sessions.db")
    (proj / ".empirica" / "project.yaml").write_text(yaml.safe_dump({"name": "P", "project_id": OLD}), encoding="utf-8")
    # keep registry/workspace out of the user's real home for this test
    import empirica.core.identity_migration as im

    monkeypatch.setattr(im, "_rekey_workspace_db", lambda *a, **k: {})
    monkeypatch.setattr(im, "_rekey_registry_yaml", lambda *a, **k: False)

    out = _reconcile_identity_if_diverged(_args(True), proj, OLD, {"project_id": NEW, "outcome": "registered"}, "json")
    assert out["reconciled"] is True
    assert yaml.safe_load((proj / ".empirica" / "project.yaml").read_text())["project_id"] == NEW
