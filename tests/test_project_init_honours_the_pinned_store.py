"""`project-init` writes the project row where the other verbs will read it.

It hardcoded `<git_root>/.empirica/sessions/sessions.db` while session-create,
finding-log and goals-* all honour `EMPIRICA_SESSION_DB` — so with the override
set, the project existed in a database nothing else opened, and a freshly
init'd repo could not create a session. The project's own config.yaml advertises
that override, so this was a contract violation, not a preference.
"""

from __future__ import annotations

from pathlib import Path

from empirica.cli.command_handlers.project_init import _resolve_init_db_path


def test_the_pinned_store_wins_when_it_is_set(tmp_path, monkeypatch):
    pinned = tmp_path / "pinned.db"
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(pinned))
    assert _resolve_init_db_path(tmp_path / "repo") == pinned


def test_the_project_local_store_is_the_default(tmp_path, monkeypatch):
    """Positive control: unset, the behaviour is the old one — a store belongs
    to one project, and that stays the default for a normal checkout."""
    monkeypatch.delenv("EMPIRICA_SESSION_DB", raising=False)
    root = tmp_path / "repo"
    assert _resolve_init_db_path(root) == root / ".empirica" / "sessions" / "sessions.db"


def test_an_empty_override_is_not_a_path(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPIRICA_SESSION_DB", "   ")
    root = tmp_path / "repo"
    assert _resolve_init_db_path(root) == root / ".empirica" / "sessions" / "sessions.db"


def test_it_returns_a_path_not_a_string(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "x.db"))
    assert isinstance(_resolve_init_db_path(tmp_path), Path)
