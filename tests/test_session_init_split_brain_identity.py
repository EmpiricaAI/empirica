"""SessionStart tells its resolvers who is calling, so a foreign claude's open transaction cannot pin the session elsewhere.

NLE (relayed by mesh-support): from a shell whose instance points at another practice, session-init printed
"SPLIT-BRAIN CORRECTED ... healed" but the session row stayed in the wrong practice DB, so POSTFLIGHT failed with
"session ... not found in project DB". Reproduced in a sandbox: with the instance mapped to practice A and A holding an
open transaction owned by ANOTHER claude session, session-create run from B (even pinned with B's project id) wrote the
row into A's DB. path_resolver._has_open_transaction lets a foreign-owned transaction stop pinning only when the CALLER's
id (CLAUDE_CODE_SESSION_ID) is known, and the SessionStart hook, which knows it from its payload, never exported it. The
heal only rewrites project_id inside whichever DB holds the row; it cannot move the row.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture
def init(monkeypatch):
    sys.path.insert(0, str(HOOKS.parent / "lib"))
    spec = importlib.util.spec_from_file_location("session_init_identity", HOOKS / "session-init.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tx(project, suffix, owner, status="open"):
    (project / ".empirica").mkdir(parents=True, exist_ok=True)
    path = project / ".empirica" / f"active_transaction{suffix}.json"
    path.write_text(json.dumps({"status": status, "claude_session_id": owner}))


def _projects(tmp_path):
    stale, cwd = tmp_path / "stale", tmp_path / "cwd"
    for root in (stale, cwd):
        (root / ".empirica").mkdir(parents=True)
    return stale, cwd


def test_the_hook_exports_its_callers_session_id(init, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    init._adopt_caller_identity("claude-me")
    assert os.environ["CLAUDE_CODE_SESSION_ID"] == "claude-me"
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


def test_without_a_session_id_nothing_is_exported(init, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    init._adopt_caller_identity(None)
    assert "CLAUDE_CODE_SESSION_ID" not in os.environ


def test_main_exports_the_identity_before_the_session_is_created(init, monkeypatch, tmp_path):
    seen = {}

    class Stop(Exception):
        pass

    def create(ai_id, project_id=None):
        seen["env"] = os.environ.get("CLAUDE_CODE_SESSION_ID")
        raise Stop

    project = tmp_path / "proj"
    (project / ".empirica").mkdir(parents=True)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setattr(
        sys, "stdin", __import__("io").StringIO(json.dumps({"session_id": "claude-me", "source": "startup"}))
    )
    monkeypatch.setattr(init, "_auto_sync_plugin", lambda: None)
    monkeypatch.setattr(init, "_try_cwd_adoption", lambda: (None, False))
    monkeypatch.setattr(init, "find_project_root", lambda *a, **k: project)
    monkeypatch.setattr(init, "_prefer_cwd_on_startup", lambda root, *a: root)
    monkeypatch.setattr(init, "_resolve_ai_id_for_session", lambda root: "x")
    monkeypatch.setattr(init, "_run_stale_cleanup", lambda sid: 0)
    monkeypatch.setattr(init, "archive_stale_plans", lambda: [])
    monkeypatch.setattr(init, "_handle_orphan_adoption", lambda *a: None)
    monkeypatch.setattr(init, "_resolve_canonical_project_id_for_root", lambda root: "pid")
    monkeypatch.setattr(init, "create_session_and_bootstrap", create)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(Stop):
        init.main()
    assert seen["env"] == "claude-me"
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


@pytest.mark.parametrize("alive", [True, False], ids=["live-owner", "dead-owner"])
def test_another_claudes_open_transaction_does_not_pin_the_startup_project(init, tmp_path, monkeypatch, alive):
    """A fresh startup cannot be continuing someone else's loop, whether or not that claude is still running."""
    stale, cwd = _projects(tmp_path)
    _tx(stale, "_s", "claude-other")
    monkeypatch.setattr(init, "live_claude_pid", lambda csid: 4242 if alive else None)
    monkeypatch.setattr(init, "has_valid_db", lambda root: True)
    assert Path(init._prefer_cwd_on_startup(stale, cwd, "_s", "claude-me")) == cwd


def test_this_sessions_own_open_transaction_still_pins(init, tmp_path, monkeypatch):
    stale, cwd = _projects(tmp_path)
    _tx(stale, "_s", "claude-me")
    monkeypatch.setattr(init, "has_valid_db", lambda root: True)
    assert Path(init._prefer_cwd_on_startup(stale, cwd, "_s", "claude-me")) == stale  # a continuing loop


def test_an_unknown_caller_keeps_the_old_reading(init, tmp_path, monkeypatch):
    stale, cwd = _projects(tmp_path)
    _tx(stale, "_s", "claude-other")
    monkeypatch.setattr(init, "has_valid_db", lambda root: True)
    assert Path(init._prefer_cwd_on_startup(stale, cwd, "_s")) == stale


def test_an_open_transaction_with_no_recorded_owner_still_pins(init, tmp_path, monkeypatch):
    stale, cwd = _projects(tmp_path)
    _tx(stale, "_s", None)
    monkeypatch.setattr(init, "live_claude_pid", lambda csid: None)
    monkeypatch.setattr(init, "has_valid_db", lambda root: True)
    assert Path(init._prefer_cwd_on_startup(stale, cwd, "_s", "claude-me")) == stale


def test_with_no_open_transaction_the_cwd_still_wins(init, tmp_path, monkeypatch):
    stale, cwd = _projects(tmp_path)
    monkeypatch.setattr(init, "has_valid_db", lambda root: True)
    assert Path(init._prefer_cwd_on_startup(stale, cwd, "_s", "claude-me")) == cwd
