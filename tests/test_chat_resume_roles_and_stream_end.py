"""What a resumed chat sends the model, in which roles, and how a chat-completions stream that stops early is kept.

- Every resume appended another compact-recovery turn, and every one of them went to the model.
- A context line is a user-role message right before the next user turn; templates that require alternating roles
  (llama.cpp with Gemma- or Mistral-style templates) reject two user messages in a row.
- A stream that closed without [DONE] was reported as completed, and finish_reason "length" was saved as a complete
  reply, while the Responses path treats response.incomplete as an error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from empirica.cli.tui.chat.statusline import StatuslinePanel
from empirica.cli.tui.chat_app import ChatApp
from empirica.core.chat import openai_compat_client
from empirica.core.chat.compact import save_breadcrumb
from empirica.core.chat.openai_compat_client import build_chat_request
from empirica.core.chat.providers import Provider
from empirica.core.chat.session import ChatSession, Turn, TurnKind, load_turns

_PROVIDER = Provider(name="fake", base_url="http://127.0.0.1:9", default_model="m")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(StatuslinePanel, "refresh_now", lambda self: None)
    return tmp_path


def _history_sent(app: ChatApp, user_text: str) -> list[dict[str, Any]]:
    assert app._session is not None
    app._convo = MagicMock()  # type: ignore[method-assign]
    app.call_from_thread = lambda fn, *a, **k: None  # type: ignore[method-assign]
    app._session.append(Turn.new(TurnKind.USER, user_text))
    seen: dict[str, Any] = {}

    def fake_dispatch(provider, model, text, history):
        seen["history"] = history
        yield {"type": "completed", "text": "ok"}

    app._dispatch = fake_dispatch  # type: ignore[method-assign]
    app._stream_agent_response(user_text)
    return seen["history"]


async def _resume(session_id: str) -> ChatApp:
    app = ChatApp(session_id=session_id, providers=[_PROVIDER], enable_system_prompt=False)
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
    return app


def _recovery_lines(history: list[dict[str, Any]]) -> list[str]:
    return [h["text"] for h in history if "post-compact recovery" in h["text"]]


async def test_three_resumes_send_the_model_one_recovery_line(home):
    sess = ChatSession.create()
    sess.append(Turn.new(TurnKind.USER, "hello"))
    sess.append(Turn.new(TurnKind.AGENT_TEXT, "hi there"))
    save_breadcrumb(session_id=sess.session_id, provider_name="fake", model="m", autonomy_mode="a", statusline_mode="s")
    for _ in range(3):
        app = await _resume(sess.session_id)
    recoveries = [t for t in load_turns(sess.jsonl_path) if (t.metadata or {}).get("compact_recovery")]
    assert len(recoveries) == 1, "a resume from the same breadcrumb adds no second recovery turn"
    assert len(_recovery_lines(_history_sent(app, "continue"))) == 1


def test_only_the_latest_recovery_turn_is_sent_and_discuss_turns_all_stay(tmp_path):
    app = ChatApp(providers=[_PROVIDER])
    app._session = ChatSession.create(root=tmp_path)
    for text in ("▶ post-compact recovery\nold", "▶ post-compact recovery\nnew"):
        app._session.append(Turn.new(TurnKind.SYSTEM, text, metadata={"compact_recovery": True}))
    for text in ("discuss one", "discuss two"):
        app._session.append(Turn.new(TurnKind.SYSTEM, text, metadata={"discuss_context": True}))
    history = _history_sent(app, "go on")
    assert [line.split("\n")[-1] for line in _recovery_lines(history)] == ["new"]
    texts = "\n".join(h["text"] for h in history)
    assert "discuss one" in texts and "discuss two" in texts


def test_a_context_line_before_a_user_turn_still_alternates_roles(tmp_path):
    app = ChatApp(providers=[_PROVIDER])
    app._session = ChatSession.create(root=tmp_path)
    app._session.append(Turn.new(TurnKind.USER, "first question"))
    app._session.append(Turn.new(TurnKind.AGENT_TEXT, "first answer"))
    app._session.append(Turn.new(TurnKind.SYSTEM, "card text", metadata={"discuss_context": True}))
    app._session.append(Turn.new(TurnKind.USER, "what about this card?"))
    app._session.append(Turn.new(TurnKind.AGENT_TEXT, "it says so"))
    app._session.append(Turn.new(TurnKind.SYSTEM, "another card", metadata={"discuss_context": True}))
    history = _history_sent(app, "and this one?")
    body = build_chat_request("and this one?", "m", instructions="sys", history=history)
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user"], roles
    assert body["messages"][3]["content"].endswith("\n\nwhat about this card?")
    assert body["messages"][-1]["content"].endswith("another card\n\nand this one?")


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


def _chunk(content: str, finish: str | None = None) -> str:
    import json

    return "data: " + json.dumps({"id": "r1", "choices": [{"delta": {"content": content}, "finish_reason": finish}]})


def _run_stream(tmp_path, monkeypatch, lines: list[str]) -> tuple[list[dict[str, Any]], ChatSession]:
    monkeypatch.setattr(openai_compat_client.requests, "post", lambda *a, **k: _FakeResp(lines))
    events = list(openai_compat_client.stream_chat_completions("http://127.0.0.1:9", {"model": "m"}))
    app = ChatApp(providers=[_PROVIDER])
    app._session = ChatSession.create(root=tmp_path)
    app._convo = MagicMock()  # type: ignore[method-assign]
    app.call_from_thread = lambda fn, *a, **k: None  # type: ignore[method-assign]
    app._session.append(Turn.new(TurnKind.USER, "hi"))
    app._stream_agent_response("hi")
    return events, app._session


def test_a_stream_that_closes_without_done_is_an_error_and_keeps_the_partial(tmp_path, monkeypatch):
    events, sess = _run_stream(tmp_path, monkeypatch, [_chunk("half a "), _chunk("reply")])
    assert events[-1]["type"] == "error" and events[-1]["text"] == "half a reply", events
    assert not any(e["type"] == "completed" for e in events)
    turns = list(load_turns(sess.jsonl_path))
    agent = [t for t in turns if t.kind == TurnKind.AGENT_TEXT]
    assert agent and agent[-1].text == "half a reply" and agent[-1].metadata.get("incomplete") is True
    assert turns[-1].kind == TurnKind.SYSTEM and "error" in turns[-1].text


def test_finish_reason_length_is_saved_incomplete_with_a_note(tmp_path, monkeypatch):
    events, sess = _run_stream(tmp_path, monkeypatch, [_chunk("cut "), _chunk("off", "length"), "data: [DONE]"])
    assert not any(e["type"] == "completed" for e in events), events
    turns = list(load_turns(sess.jsonl_path))
    agent = [t for t in turns if t.kind == TurnKind.AGENT_TEXT]
    assert agent and agent[-1].text == "cut off" and agent[-1].metadata.get("incomplete") is True
    assert turns[-1].kind == TurnKind.SYSTEM and "length" in turns[-1].text


def test_a_finished_reply_without_done_is_still_complete(tmp_path, monkeypatch):
    """A server that sends finish_reason "stop" and closes without [DONE] finished its reply."""
    events, sess = _run_stream(tmp_path, monkeypatch, [_chunk("all "), _chunk("done", "stop")])
    assert events[-1]["type"] == "completed" and events[-1]["text"] == "all done", events
    agent = [t for t in load_turns(sess.jsonl_path) if t.kind == TurnKind.AGENT_TEXT]
    assert agent and not agent[-1].metadata.get("incomplete")
