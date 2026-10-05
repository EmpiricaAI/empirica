"""ecodex (prop_ydbrupzosvd4vbpmwewsihwrpa), seen by David after a compaction on ecodex 0.160.0:

    Failed to persist budget state: Cannot determine sessions.db path - not in a git repo ... Run 'empirica project-init'

inside a practice that IS initialised. 5a8c4c000 fixed the case where the hook knows its root; this is the case where it does
not (project_root is None or the file is absent, so session-init passed db_path=None and persist_state re-resolved from
scratch). Two asks, both held here:

1. The hook skips the persist and says why in its own words, instead of letting the resolver speak.
2. The resolver says what it tried, not "initialize this repo", which a practitioner inside a practice cannot act on.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import types
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"
LIB = HOOKS.parent / "lib"


@pytest.fixture(scope="module")
def session_init():
    sys.path.insert(0, str(LIB))
    sys.path.insert(0, str(HOOKS))
    spec = importlib.util.spec_from_file_location("session_init_budget_persist", HOOKS / "session-init.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


# ── 1. the hook skips and says why ──────────────────────────────────────────


def _spy_persist(monkeypatch):
    calls = []
    from empirica.core.context_budget import ContextBudgetManager

    monkeypatch.setattr(ContextBudgetManager, "persist_state", lambda self, db_path=None: calls.append(db_path) or True)
    return calls


def test_an_unknown_root_skips_the_persist_and_says_why_in_the_hooks_words(session_init, monkeypatch):
    calls = _spy_persist(monkeypatch)
    reason = "budget state not persisted: practice root unknown for this SessionStart (source=compact, cwd=/work/x)"

    summary = session_init._init_context_budget("s" * 8, {}, db_path=None, unpersisted_reason=reason)

    assert calls == [], "persist_state must not be reached, so the resolver cannot speak"
    assert summary["persist"] == f"skipped: {reason}"


def test_a_known_path_still_persists_and_reports_it(session_init, monkeypatch, tmp_path):
    calls = _spy_persist(monkeypatch)
    db = tmp_path / "sessions.db"

    summary = session_init._init_context_budget("s" * 8, {}, db_path=db, unpersisted_reason="unused")

    assert calls == [db] and summary["persist"] == "persisted"


def test_the_caller_builds_the_reason_from_the_source_and_the_cwd(session_init):
    text = session_init._unpersisted_reason(None, {"source": "compact"}, "/work/x")
    assert "practice root unknown" in text and "source=compact" in text and "cwd=/work/x" in text

    absent = session_init._unpersisted_reason(Path("/no/such/root"), {"type": "resume"}, "/work/x")
    assert "sessions.db not found" in absent and "/no/such/root" in absent and "source=resume" in absent


# ── persist_state itself: no resolver prose, no wrong remedy ────────────────


def test_persist_state_without_a_resolvable_db_warns_in_one_line_and_returns_false(monkeypatch, caplog):
    from empirica.core.context_budget import ContextBudgetManager

    def refuse(*_a, **_k):
        raise ValueError("Cannot determine sessions.db path.\nTried: EMPIRICA_SESSION_DB (unset); git root (none)")

    monkeypatch.setattr("empirica.config.path_resolver.get_session_db_path", refuse)
    manager = ContextBudgetManager(session_id="s" * 8, auto_subscribe=False)

    with caplog.at_level(logging.WARNING):
        assert manager.persist_state() is False

    assert "budget state not persisted" in caplog.text and "Tried: EMPIRICA_SESSION_DB" in caplog.text
    assert "Failed to persist budget state" not in caplog.text, "the old line framed a skip as a broken install"
    assert "project-init" not in caplog.text


def test_a_genuine_failure_is_still_an_error(monkeypatch, caplog):
    """Control: only the unresolvable-path case is softened; a real failure keeps its error line."""
    from empirica.core.context_budget import ContextBudgetManager

    monkeypatch.setattr(
        "empirica.data.session_database.SessionDatabase", lambda *a, **k: types.SimpleNamespace(conn=None)
    )
    manager = ContextBudgetManager(session_id="s" * 8, auto_subscribe=False)

    with caplog.at_level(logging.ERROR):
        assert manager.persist_state() is False

    assert "No database connection" in caplog.text


# ── 2. the resolver says what it tried ──────────────────────────────────────


def _unresolvable(monkeypatch, tmp_path, context):
    from empirica.config import path_resolver

    monkeypatch.delenv("EMPIRICA_SESSION_DB", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "empirica.utils.session_resolver.InstanceResolver.context", staticmethod(lambda *a, **k: context)
    )
    monkeypatch.setattr(path_resolver, "get_git_root", lambda: None)
    monkeypatch.setattr(path_resolver, "_try_context_project_db", lambda *a, **k: None)

    def no_root():
        raise ValueError("no root")

    monkeypatch.setattr(path_resolver, "get_empirica_root", no_root)
    return path_resolver


def test_the_resolver_error_lists_what_it_tried_and_does_not_prescribe_project_init(monkeypatch, tmp_path):
    resolver = _unresolvable(monkeypatch, tmp_path, {})

    with pytest.raises(ValueError) as exc:
        resolver.get_session_db_path()

    msg = str(exc.value)
    assert "Tried:" in msg and "EMPIRICA_SESSION_DB (unset)" in msg
    assert "instance context project (none)" in msg and "git root from" in msg and str(tmp_path) in msg
    assert "Run 'empirica project-init' to initialize this repo" not in msg
    assert "only for a directory that is not yet a practice" in msg


def test_the_resolver_error_reports_a_context_project_it_found_but_could_not_use(monkeypatch, tmp_path):
    """A found-but-unusable context is the datum ecodex asked for; it must not read as 'none'."""
    gone = str(tmp_path / "gone")
    resolver = _unresolvable(monkeypatch, tmp_path, {"project_path": gone})

    with pytest.raises(ValueError) as exc:
        resolver.get_session_db_path()

    assert f"instance context project ({gone}, not usable)" in str(exc.value)


def test_a_value_error_from_the_write_itself_is_still_an_error_not_a_skip(monkeypatch, caplog):
    """Only failing to FIND a store is softened. A ValueError raised while writing is a real failure."""
    from empirica.core.context_budget import ContextBudgetManager

    class Db:
        conn = types.SimpleNamespace(cursor=lambda: (_ for _ in ()).throw(ValueError("circular reference")))

    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", lambda *a, **k: Db())
    manager = ContextBudgetManager(session_id="s" * 8, auto_subscribe=False)

    with caplog.at_level(logging.WARNING):
        assert manager.persist_state() is False

    assert "Failed to persist budget state: circular reference" in caplog.text
    assert "budget state not persisted" not in caplog.text
