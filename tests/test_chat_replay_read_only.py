"""`empirica chat --replay` is read-only, and a resumed session keeps its system prompt.

Replay used to append to the replayed jsonl (its own "replay mode" notice and a fresh
system-prompt turn 0) and let /finding, /batch and card buttons mutate the project.
Resume (--session-id) skipped the system prompt entirely, so the model ran without it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from empirica.cli.tui import chat_app
from empirica.cli.tui.chat.statusline import StatuslinePanel
from empirica.cli.tui.chat_app import ChatApp
from empirica.core.chat.providers import Provider
from empirica.core.chat.session import ChatSession, Turn, TurnKind

_PROVIDER = Provider(name="fake", base_url="http://127.0.0.1:9", default_model="m")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://127.0.0.1:9")
    # The statusline reads the live practice; it is not what these tests measure.
    monkeypatch.setattr(StatuslinePanel, "refresh_now", lambda self: None)
    return tmp_path


def _seed_session(home: Path) -> ChatSession:
    sess = ChatSession.create(root=home / ".empirica" / "chat_sessions")
    sess.append(Turn.new(TurnKind.USER, "hello"))
    sess.append(Turn.new(TurnKind.AGENT_TEXT, "hi there"))
    sess.append(
        Turn.new(
            TurnKind.EPISTEMIC_ACTION,
            "a finding",
            metadata={"artifact_type": "finding", "artifact_id": "abcdef12"},
        )
    )
    return sess


async def test_replay_mount_leaves_session_file_bytes_unchanged(home):
    sess = _seed_session(home)
    before = sess.jsonl_path.read_bytes()
    app = ChatApp(replay_session_id=sess.session_id, providers=[_PROVIDER])
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        app._handle_slash("/help")
        await pilot.pause()
    assert sess.jsonl_path.read_bytes() == before


@pytest.mark.parametrize(
    "command, target",
    [
        ("/finding something", "log_finding"),
        ("/decision something", "log_decision"),
        ("/unknown something", "log_unknown"),
    ],
)
async def test_replay_refuses_mutating_slash_commands(home, monkeypatch, command, target):
    sess = _seed_session(home)
    action = MagicMock(return_value={"ok": True, "id": "x"})
    monkeypatch.setattr(chat_app, target, action)
    app = ChatApp(replay_session_id=sess.session_id, providers=[_PROVIDER])
    async with app.run_test(headless=True) as pilot:
        app._handle_slash(command)
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert any("read-only" in t.text for t in app._session.turns if t.kind == TurnKind.SYSTEM)  # type: ignore[union-attr]
    action.assert_not_called()


async def test_replay_refuses_batch_commands(home, monkeypatch):
    from empirica.core.chat import actions

    sess = _seed_session(home)
    calls = MagicMock()
    monkeypatch.setattr(actions, "log_artifacts_from_file", calls)
    monkeypatch.setattr(actions, "resolve_artifacts_batch", calls)
    monkeypatch.setattr(actions, "delete_artifacts_batch", calls)
    app = ChatApp(replay_session_id=sess.session_id, providers=[_PROVIDER])
    async with app.run_test(headless=True) as pilot:
        for cmd in ("/batch x.json", "/resolve-batch abc", "/delete-batch --apply finding:abc"):
            app._handle_slash(cmd)
        await pilot.pause()
        await app.workers.wait_for_complete()
    calls.assert_not_called()


async def test_replay_refuses_card_actions(home, monkeypatch):
    sess = _seed_session(home)
    log_finding = MagicMock()
    monkeypatch.setattr("empirica.core.chat.actions.log_finding", log_finding)
    resolve = MagicMock()
    monkeypatch.setattr("empirica.core.chat.actions.resolve_unknown", resolve)
    app = ChatApp(replay_session_id=sess.session_id, providers=[_PROVIDER])
    async with app.run_test(headless=True) as pilot:
        for atype, action in (("finding", "confirm"), ("unknown", "resolve"), ("finding", "pin")):
            evt = MagicMock(artifact_type=atype, action=action, artifact_id="abcdef12", turn_id="t")
            app.on_artifact_card_action_invoked(evt)
        await pilot.pause()
        await app.workers.wait_for_complete()
    log_finding.assert_not_called()
    resolve.assert_not_called()
    assert not list(home.glob(".empirica/chat_pinned_*.json"))


async def test_resumed_session_gets_instructions_without_a_new_turn(home):
    sess = _seed_session(home)
    lines_before = sess.jsonl_path.read_text().count("\n")
    app = ChatApp(session_id=sess.session_id, providers=[_PROVIDER])
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        assert app.instructions is not None
        assert "empirica chat" in app.instructions
    assert sess.jsonl_path.read_text().count("\n") == lines_before
