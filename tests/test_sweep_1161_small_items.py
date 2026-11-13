"""Smaller items from the 1.16.1 pre-publish sweep (goal 24979a81)."""

from __future__ import annotations

import sqlite3
import types

import pytest


def test_a_failed_model_load_is_reported_as_failed_and_retried():
    from empirica.local_server.embedder import Embedder

    calls = []

    def load():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("network unreachable")
        return lambda texts: [[0.0] for _ in texts]

    e = Embedder(load=load)
    with pytest.raises(OSError):
        e(["x"])
    assert e.state == "failed" and "network unreachable" in e.error
    assert e(["x"]) == [[0.0]] and e.state == "ready" and e.error is None


def test_the_semantic_status_names_a_failed_load(monkeypatch):
    import empirica.core.mesh_endpoint as me
    import empirica.core.qdrant.connection as qc
    import empirica.core.qdrant.embeddings as emb

    monkeypatch.setattr(qc, "local_server_service", lambda name: "http://127.0.0.1:8000/ollama")
    monkeypatch.setattr(
        emb, "get_embedding_provider", lambda: types.SimpleNamespace(ollama_url="http://127.0.0.1:8000/ollama")
    )
    monkeypatch.setattr(
        me,
        "_probe_health",
        lambda url: {"services": {"embeddings_state": "failed", "embeddings_error": "OSError: offline"}},
    )
    msg = qc._local_model_not_ready()
    assert "failed to load" in msg and "offline" in msg


@pytest.mark.parametrize(
    ("want", "value", "ok"),
    [({"$gt": 0.2}, 0.2, False), ({"$gt": 0.2}, 0.3, True), ({"$lt": 0.2}, 0.2, False), ({"$gte": 0.2}, 0.2, True)],
)
def test_range_operators_are_applied(want, value, ok):
    from empirica.core.qdrant.epistemics_store import _matches

    assert _matches({"deltas": {"know": value}}, {"deltas.know": want}) is ok


def test_an_unknown_operator_is_refused_not_ignored():
    from empirica.core.qdrant.epistemics_store import search_epistemics

    with pytest.raises(ValueError, match=r"\$ne"):
        search_epistemics("p", "q", filters={"deltas.know": {"$ne": 0.2}})


def test_mesh_on_says_it_could_not_confirm_when_launchctl_did_not_answer(monkeypatch, capsys):
    from empirica.cli.command_handlers import mesh_commands as mc

    states = iter(
        [
            types.SimpleNamespace(installed=True, active=False, uncertain=False, backend="launchd"),
            types.SimpleNamespace(installed=True, active=False, uncertain=True, backend="launchd"),
        ]
    )
    monkeypatch.setattr(mc, "listener_status_for", lambda ai_id: next(states))
    monkeypatch.setattr(mc, "start_listener_for", lambda ai_id: None)
    assert mc.handle_mesh_on_command(types.SimpleNamespace(instance="x")) == 1
    assert "could not confirm" in capsys.readouterr().err


def test_registering_a_session_again_reopens_its_registry_row(tmp_path, monkeypatch):
    from empirica.data.repositories.sessions import _register_session_globally

    home = tmp_path / "home"
    (home / ".empirica" / "workspace").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    db = home / ".empirica" / "workspace" / "workspace.db"
    sqlite3.connect(str(db)).close()
    assert _register_session_globally("s1", "t", None, None)
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE global_sessions SET status = 'closed', closed_at = 5")
    conn.commit()
    assert _register_session_globally("s1", "t", None, None)
    assert conn.execute("SELECT status, closed_at FROM global_sessions").fetchone() == ("active", None)
    conn.close()
