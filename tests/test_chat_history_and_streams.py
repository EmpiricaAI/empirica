"""What empirica chat sends the model, and what it keeps when a stream fails.

- SYSTEM turns that claim to be context for the model (a card's 'discuss', the
  compact-recovery note) used to be filtered out of the history.
- A stream that failed (Responses `response.failed`, a chat-completions
  `{"error": ...}` chunk) was dropped silently or persisted as an empty
  AGENT_TEXT turn, and the partial reply was lost.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from empirica.cli.tui import chat_app
from empirica.cli.tui.chat_app import ChatApp
from empirica.core.chat import openai_compat_client
from empirica.core.chat.providers import Provider
from empirica.core.chat.session import ChatSession, Turn, TurnKind, load_turns


def _app(tmp_path, wire: str = "chat_completions") -> ChatApp:
    provider = Provider(name="fake", base_url="http://127.0.0.1:9", default_model="m", wire=wire)
    app = ChatApp(providers=[provider])
    app._session = ChatSession.create(root=tmp_path)
    # No running Textual app: widget updates are not what these tests measure.
    app._convo = MagicMock()  # type: ignore[method-assign]
    app.call_from_thread = lambda fn, *a, **k: None  # type: ignore[method-assign]
    return app


def test_discuss_turn_reaches_the_model_history(tmp_path):
    app = _app(tmp_path)
    assert app._session is not None
    card = Turn.new(
        TurnKind.EPISTEMIC_ACTION,
        "the cache is keyed by tty",
        metadata={"artifact_type": "finding", "artifact_id": "abcdef1234"},
    )
    app._session.append(card)
    evt = MagicMock(artifact_type="finding", action="discuss", artifact_id="abcdef1234", turn_id=card.turn_id)
    app.on_artifact_card_action_invoked(evt)
    app._session.append(Turn.new(TurnKind.USER, "what does this mean?"))

    seen: dict[str, Any] = {}

    def fake_dispatch(provider, model, user_text, history):
        seen["history"] = history
        yield {"type": "completed", "text": "ok"}

    app._dispatch = fake_dispatch  # type: ignore[method-assign]
    app._stream_agent_response("what does this mean?")

    texts = [h["text"] for h in seen["history"]]
    assert any("abcdef12" in t and "the cache is keyed by tty" in t for t in texts), texts


def test_compact_recovery_turn_reaches_the_model_history(tmp_path):
    app = _app(tmp_path)
    assert app._session is not None
    app._session.append(Turn.new(TurnKind.SYSTEM, "recovered: provider x", metadata={"compact_recovery": True}))
    app._session.append(Turn.new(TurnKind.SYSTEM, "statusline mode → full"))
    app._session.append(Turn.new(TurnKind.USER, "continue"))

    seen: dict[str, Any] = {}

    def fake_dispatch(provider, model, user_text, history):
        seen["history"] = history
        yield {"type": "completed", "text": "ok"}

    app._dispatch = fake_dispatch  # type: ignore[method-assign]
    app._stream_agent_response("continue")

    texts = [h["text"] for h in seen["history"]]
    assert any("recovered: provider x" in t for t in texts)
    assert not any("statusline mode" in t for t in texts), "UI notices are not model context"


class _FakeResp:
    def __init__(self, lines: list[str]):
        self._lines = lines
        self.ok = True
        self.status_code = 200
        self.text = ""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self, decode_unicode: bool = True):
        yield from self._lines


def test_chat_completions_error_chunk_yields_error_not_empty_completed(monkeypatch):
    lines = [
        'data: {"error": {"message": "model overloaded", "code": 503}}',
        "data: [DONE]",
    ]
    monkeypatch.setattr(openai_compat_client.requests, "post", lambda *a, **k: _FakeResp(lines))
    events = list(openai_compat_client.stream_chat_completions("http://127.0.0.1:9", {"model": "m"}))
    types = [e["type"] for e in events]
    assert "error" in types
    assert "completed" not in types
    err = next(e for e in events if e["type"] == "error")
    assert "model overloaded" in err["message"]


@pytest.mark.parametrize(
    "terminal",
    [
        {"type": "response.failed", "response": {"error": {"message": "upstream 500"}}},
        {"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}}},
        {"type": "error", "message": "upstream 500"},
    ],
)
def test_responses_failure_persists_partial_and_shows_error(tmp_path, monkeypatch, terminal):
    app = _app(tmp_path, wire="responses")
    assert app._session is not None
    app._session.append(Turn.new(TurnKind.USER, "hi"))

    def fake_stream(url, body):
        yield {"type": "response.output_text.delta", "delta": "partial answ"}
        yield terminal

    monkeypatch.setattr(chat_app, "stream_responses", fake_stream)
    app._stream_agent_response("hi")

    persisted = list(load_turns(app._session.jsonl_path))
    agent = [t for t in persisted if t.kind == TurnKind.AGENT_TEXT]
    assert len(agent) == 1
    assert agent[0].text == "partial answ"
    assert agent[0].metadata.get("incomplete") is True
    errors = [t for t in persisted if t.kind == TurnKind.SYSTEM and "error" in t.text.lower()]
    assert errors, [t.text for t in persisted]


def test_exception_mid_stream_keeps_the_partial_reply(tmp_path):
    app = _app(tmp_path)
    assert app._session is not None
    app._session.append(Turn.new(TurnKind.USER, "hi"))

    def fake_dispatch(provider, model, user_text, history):
        yield {"type": "text_delta", "delta": "half"}
        raise ConnectionError("reset by peer")

    app._dispatch = fake_dispatch  # type: ignore[method-assign]
    app._stream_agent_response("hi")

    persisted = list(load_turns(app._session.jsonl_path))
    agent = [t for t in persisted if t.kind == TurnKind.AGENT_TEXT]
    assert [t.text for t in agent] == ["half"]
    assert agent[0].metadata.get("incomplete") is True
    assert any(t.kind == TurnKind.SYSTEM and "reset by peer" in t.text for t in persisted)


def test_empty_completed_is_not_persisted_as_agent_text(tmp_path):
    app = _app(tmp_path)
    assert app._session is not None
    app._session.append(Turn.new(TurnKind.USER, "hi"))

    def fake_dispatch(provider, model, user_text, history):
        yield {"type": "completed", "text": ""}

    app._dispatch = fake_dispatch  # type: ignore[method-assign]
    app._stream_agent_response("hi")

    persisted = list(load_turns(app._session.jsonl_path))
    assert not [t for t in persisted if t.kind == TurnKind.AGENT_TEXT]
    assert any(t.kind == TurnKind.SYSTEM and "empty" in t.text for t in persisted)
