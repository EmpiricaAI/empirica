"""Tests for shell-safe body inputs on Mesh message commands."""

from __future__ import annotations

import argparse
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from empirica.cli.command_handlers import message_commands
from empirica.cli.parsers.message_parsers import add_message_parsers


def _args(**overrides):
    defaults = {
        "config": None,
        "from_ai_id": "ecodex",
        "to_ai_id": "peer",
        "to_machine": None,
        "channel": "direct",
        "subject": "Subject",
        "body": None,
        "body_file": None,
        "type": "request",
        "reply_to": None,
        "thread_id": None,
        "ttl": 86400,
        "priority": "normal",
        "session_id": None,
        "goal_id": None,
        "project_id": None,
        "message_id": "message-id",
        "output": "json",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _parser():
    parser = argparse.ArgumentParser()
    add_message_parsers(parser.add_subparsers(dest="command"))
    return parser


def test_message_send_parser_rejects_body_and_body_file_together(tmp_path):
    with pytest.raises(SystemExit):
        _parser().parse_args(
            [
                "message-send",
                "--to-ai-id",
                "peer",
                "--subject",
                "Subject",
                "--body",
                "text",
                "--body-file",
                str(tmp_path / "body.txt"),
            ]
        )


def test_message_reply_parser_exposes_shell_safe_body_inputs():
    args = _parser().parse_args(["message-reply", "--message-id", "id", "--channel", "direct", "--body", "-"])
    assert args.body == "-"
    assert args.body_file is None


def test_message_send_reads_raw_body_from_stdin(monkeypatch, capsys):
    store = Mock()
    store.send_message.return_value = "sent-id"
    monkeypatch.setattr(message_commands, "_get_store", lambda: store)
    monkeypatch.setattr(message_commands.sys, "stdin", io.StringIO("literal `code` and $(text)\n"))

    message_commands.handle_message_send_command(_args(body="-"))

    assert store.send_message.call_args.kwargs["body"] == "literal `code` and $(text)\n"
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_message_send_reads_body_from_utf8_file(tmp_path, monkeypatch):
    body_file = tmp_path / "body.txt"
    body_file.write_text("Grüße from a file\n", encoding="utf-8")
    store = Mock()
    store.send_message.return_value = "sent-id"
    monkeypatch.setattr(message_commands, "_get_store", lambda: store)

    message_commands.handle_message_send_command(_args(body_file=str(body_file)))

    assert store.send_message.call_args.kwargs["body"] == "Grüße from a file\n"


def test_message_reply_reads_body_from_utf8_file(tmp_path, monkeypatch):
    body_file = tmp_path / "reply.txt"
    body_file.write_text("Reply from file", encoding="utf-8")
    store = Mock()
    store.reply.return_value = "reply-id"
    monkeypatch.setattr(message_commands, "_get_store", lambda: store)

    message_commands.handle_message_reply_command(_args(body_file=str(body_file)))

    assert store.reply.call_args.kwargs["body"] == "Reply from file"


@pytest.mark.parametrize(
    "handler", [message_commands.handle_message_send_command, message_commands.handle_message_reply_command]
)
def test_config_stdin_and_raw_body_stdin_are_rejected(handler, monkeypatch, capsys):
    store = Mock()
    monkeypatch.setattr(message_commands, "_get_store", lambda: store)
    monkeypatch.setattr(message_commands.sys, "stdin", io.StringIO('{"body": "config"}'))

    handler(_args(config="-", body="-"))

    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert "together with --body -" in result["message"]
    store.send_message.assert_not_called()
    store.reply.assert_not_called()


def test_missing_body_file_returns_structured_error(tmp_path, monkeypatch, capsys):
    store = Mock()
    monkeypatch.setattr(message_commands, "_get_store", lambda: store)

    message_commands.handle_message_send_command(_args(body_file=str(tmp_path / "missing.txt")))

    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["message"].startswith("Cannot read --body-file:")
    store.send_message.assert_not_called()


def test_config_json_body_keeps_precedence_over_cli_body(monkeypatch):
    args = _args(body="cli")
    assert message_commands._load_message_body(args, {"body": "config"}) == "config"
