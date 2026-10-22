"""A rebuild step that failed must not be reported as a success (goal d897e750, medium item).

``rebuild --qdrant`` took ``ok`` from the notes import alone: a Qdrant rebuild that returned ``ok: false`` or raised was attached
to the result and the verb still said ``ok: true`` and exited 0. ``sync-pull --rebuild`` did the same with the SQLite rebuild: the
fetch succeeded, the rebuild came back with ``error``, and the pull reported success. Both are the caller learning that the graph
is current from a verb that did not make it current.

Each failing case is paired with the case that succeeds, so a handler that always says failure cannot pass.
"""

from __future__ import annotations

import json
import subprocess
import types

import pytest

import empirica.cli.command_handlers.sync_commands as sc

_QDRANT = "empirica.core.qdrant.vector_store.rebuild_qdrant_from_db"


def _out(capsys):
    return json.loads(capsys.readouterr().out)


def _rebuild_args(**kw):
    base = {"output": "json", "from_notes": True, "qdrant": True, "qdrant_only": False, "verbose": False}
    return types.SimpleNamespace(**{**base, **kw})


@pytest.fixture
def notes_ok(monkeypatch):
    monkeypatch.setattr(sc, "_rebuild_from_notes", lambda: {"findings": 3})


def test_a_qdrant_rebuild_that_reports_failure_fails_the_verb(notes_ok, monkeypatch, capsys):
    monkeypatch.setattr(_QDRANT, lambda: {"ok": False, "error": "qdrant unreachable"})
    rc = sc.handle_rebuild_command(_rebuild_args())
    out = _out(capsys)
    assert rc == 1 and out["ok"] is False
    assert out["rebuilt"] == {"findings": 3}, "what DID rebuild is still reported"


def test_a_qdrant_rebuild_that_raises_fails_the_verb(notes_ok, monkeypatch, capsys):
    def boom():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(_QDRANT, boom)
    rc = sc.handle_rebuild_command(_rebuild_args())
    out = _out(capsys)
    assert rc == 1 and out["ok"] is False and "connection refused" in out["qdrant_error"]


def test_a_qdrant_rebuild_that_succeeds_still_succeeds(notes_ok, monkeypatch, capsys):
    monkeypatch.setattr(_QDRANT, lambda: {"ok": True, "successful": 2})
    rc = sc.handle_rebuild_command(_rebuild_args())
    assert rc == 0 and _out(capsys)["ok"] is True


def test_without_the_qdrant_flag_the_qdrant_step_is_not_consulted(notes_ok, monkeypatch, capsys):
    monkeypatch.setattr(_QDRANT, lambda: pytest.fail("not asked for"))
    assert sc.handle_rebuild_command(_rebuild_args(qdrant=False)) == 0


# --- sync-pull --rebuild -----------------------------------------------------------------------------------------------------


@pytest.fixture
def pull_env(monkeypatch):
    monkeypatch.setattr(sc, "_load_sync_config", lambda: {"enabled": True})
    monkeypatch.setattr(sc, "_resolve_or_refuse", lambda *a, **k: "origin")
    monkeypatch.setattr(sc, "_check_remote", lambda r: True)
    monkeypatch.setattr(sc, "_count_local_notes", lambda: {})
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="", stderr=""))


def _pull_args(**kw):
    base = {"output": "json", "rebuild": True, "verbose": False, "force": False, "remote": None}
    return types.SimpleNamespace(**{**base, **kw})


def test_a_pull_whose_rebuild_failed_is_not_reported_ok(pull_env, monkeypatch, capsys):
    monkeypatch.setattr(sc, "_rebuild_from_notes", lambda: {"findings": 0, "error": "database is locked"})
    rc = sc.handle_sync_pull_command(_pull_args())
    out = _out(capsys)
    assert rc == 1 and out["ok"] is False
    assert out["rebuild"]["error"] == "database is locked", "the failure is named, not just flagged"


def test_a_pull_whose_rebuild_worked_is_ok(pull_env, monkeypatch, capsys):
    monkeypatch.setattr(sc, "_rebuild_from_notes", lambda: {"findings": 4})
    rc = sc.handle_sync_pull_command(_pull_args())
    assert rc == 0 and _out(capsys)["ok"] is True


def test_a_pull_without_rebuild_never_rebuilds(pull_env, monkeypatch, capsys):
    monkeypatch.setattr(sc, "_rebuild_from_notes", lambda: pytest.fail("not asked for"))
    assert sc.handle_sync_pull_command(_pull_args(rebuild=False)) == 0
