"""ecodex: `Hook failed: Failed to persist budget state: Cannot determine sessions.db path`, and two sessions
created 0.26 s apart at one SessionStart.

1. `ContextBudgetManager.persist_state()` built `SessionDatabase()` with no path, so the sessions.db location
   was re-resolved from scratch inside a hook that had already resolved and chdir-ed to the practice root. At a
   moment the resolver saw no git repo and no env, it failed. The hooks now hand it the path they hold.
2. Under codex the SessionStart fires both session-init and post-compact. post-compact never looked at the
   SessionStart `source`, and when the active session was complete it created a new one, beside the one
   session-init had just made. It now does nothing for a start that is not a compaction.

Built under tmp_path; the resolver is made to fail by clearing its env and standing in a non-git directory.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

from empirica.core.context_budget import ContextBudgetManager
from empirica.data.session_database import SessionDatabase

HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"
LIB = HOOKS.parent / "lib"


def _load(name: str, filename: str):
    sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


@pytest.fixture
def unresolvable(tmp_path, monkeypatch):
    """A working directory where the sessions.db resolver finds nothing: no git, no env, no config."""
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "nowhere"
    work.mkdir()
    for var in ("EMPIRICA_SESSION_DB", "EMPIRICA_DATA_DIR", "EMPIRICA_WORKSPACE_ROOT", "EMPIRICA_INSTANCE_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(work)
    dbp = tmp_path / "practice" / ".empirica" / "sessions" / "sessions.db"
    dbp.parent.mkdir(parents=True)
    db = SessionDatabase(db_path=str(dbp))
    sid = db.create_session(ai_id="a")
    db.close()
    return dbp, sid


def _rows(dbp, sid):
    import sqlite3

    con = sqlite3.connect(dbp)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS context_budget_state (session_id TEXT)")
        return con.execute("SELECT session_id FROM context_budget_state WHERE session_id = ?", (sid,)).fetchall()
    finally:
        con.close()


# ── persist_state takes the path the hook holds ─────────────────────────────


def test_the_database_is_opened_at_the_path_given_and_only_resolved_when_none_is(unresolvable, monkeypatch):
    """What changed, asserted without depending on what the global resolver happens to find: with a path,
    SessionDatabase is built AT that path; without one it is built with none (the re-resolving form ecodex hit)."""
    import empirica.data.session_database as sdb

    dbp, sid = unresolvable
    seen = []
    real = sdb.SessionDatabase

    class Recording(real):
        def __init__(self, *args, **kwargs):
            seen.append(kwargs.get("db_path"))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(sdb, "SessionDatabase", Recording)
    manager = ContextBudgetManager(session_id=sid, auto_subscribe=False)

    manager.persist_state(db_path=dbp)
    try:
        manager.persist_state()
    except Exception:
        pass  # whether the default resolution succeeds is the box's business, not this test's

    assert seen[0] == str(dbp)
    assert seen[1] is None


def test_with_the_path_the_hook_holds_it_persists_even_where_the_resolver_finds_nothing(unresolvable):
    dbp, sid = unresolvable

    assert ContextBudgetManager(session_id=sid, auto_subscribe=False).persist_state(db_path=dbp) is True
    assert _rows(dbp, sid) == [(sid,)]


def test_session_init_passes_its_practice_db_to_the_budget_init(unresolvable):
    dbp, sid = unresolvable
    init = _load("si_budget_path", "session-init.py")

    summary = init._init_context_budget(sid, {}, db_path=dbp)

    assert "error" not in summary, summary
    assert _rows(dbp, sid) == [(sid,)]


def test_pre_compact_passes_its_practice_db_to_the_budget_triage(unresolvable):
    dbp, sid = unresolvable
    pre = _load("pc_budget_path", "pre-compact.py")

    report = pre._run_context_budget_triage(sid, db_path=dbp)

    assert report is not None
    assert _rows(dbp, sid) == [(sid,)]


# ── post-compact does nothing for a start that is not a compaction ──────────


class _Reached(Exception):
    """Raised by the stand-in for the first thing post-compact does after deciding to run."""


@pytest.fixture
def post(monkeypatch):
    mod = _load("post_compact_source", "post-compact.py")

    def boom(*_args, **_kwargs):
        raise _Reached

    monkeypatch.setattr(mod, "_resolve_project_and_setup", boom)
    return mod


def _run(post, monkeypatch, capsys, payload: dict):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    try:
        post.main()
    except SystemExit:
        pass
    except _Reached:
        return "reached"
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.mark.parametrize("source", ["startup", "resume", "clear"])
def test_a_start_that_is_not_a_compaction_is_skipped_before_anything_is_resolved(post, monkeypatch, capsys, source):
    out = _run(post, monkeypatch, capsys, {"session_id": "s", "source": source})

    assert out != "reached", "post-compact must not resolve a project, let alone create a session, for this start"
    assert out["skipped"] is True and source in out["reason"]


def test_a_compaction_still_runs_post_compact(post, monkeypatch, capsys):
    """Positive control: Claude Code's compact matcher sends source=compact."""
    assert _run(post, monkeypatch, capsys, {"session_id": "s", "source": "compact"}) == "reached"


def test_a_harness_that_sends_no_source_still_runs_post_compact(post, monkeypatch, capsys):
    """No source to judge by: behave as before rather than silently drop a compaction recovery."""
    assert _run(post, monkeypatch, capsys, {"session_id": "s"}) == "reached"
