"""Tests for Phase 7 — replay mode.

Covers the parser flag, mutually exclusive validation, and the
ChatApp constructor / dispatch behavior. Full Textual run is exercised
by manual smoke (`empirica chat --replay <session-id>`).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from empirica.cli.tui.chat_app import ChatApp


class TestReplayConstruction:
    def test_default_no_replay(self):
        app = ChatApp()
        assert app.replay_mode is False
        assert app.replay_session_id is None

    def test_replay_session_id_sets_flags(self):
        app = ChatApp(replay_session_id="abc-uuid")
        assert app.replay_session_id == "abc-uuid"
        assert app.replay_mode is True


class TestReplayInputDispatch:
    """Verify on_chat_input_submitted refuses non-slash input in replay."""

    def setup_method(self):
        self.app = ChatApp(replay_session_id="some-id")
        # Stub session so the assertion passes
        self.app._session = MagicMock()
        # Stub _emit_system so we can capture messages
        self.app._emit_system = MagicMock()
        # Stub _handle_slash so we can verify slash commands still dispatch
        self.app._handle_slash = MagicMock()

    def test_non_slash_input_is_blocked_in_replay(self):
        evt = MagicMock()
        evt.text = "hello can you help"
        self.app.on_chat_input_submitted(evt)
        msg = self.app._emit_system.call_args[0][0]
        assert "replay mode is read-only" in msg
        # No LLM dispatch
        self.app._handle_slash.assert_not_called()

    def test_slash_commands_still_dispatch_in_replay(self):
        evt = MagicMock()
        evt.text = "/help"
        self.app.on_chat_input_submitted(evt)
        # Should dispatch to slash handler, NOT emit the read-only warning
        self.app._handle_slash.assert_called_once_with("/help")

    def test_non_replay_app_does_not_have_replay_mode_set(self):
        # The replay-mode early-return only fires when self.replay_mode is True.
        # Verify the flag is False on a normal (non-replay) ChatApp.
        app = ChatApp()
        assert app.replay_mode is False
        # The on_chat_input_submitted will skip the replay-mode early return
        # for any non-replay app. Verifying via state inspection avoids the
        # full Textual run_worker dependency that bare unit tests can't satisfy.


class TestParserAndCommandValidation:
    def test_parser_includes_replay_flag(self):
        import argparse

        from empirica.cli.parsers.chat_parsers import add_chat_parsers

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        add_chat_parsers(sub)

        # Help text should mention the new flag
        chat_parser = sub.choices["chat"]
        actions = {a.option_strings[0] for a in chat_parser._actions if a.option_strings}
        assert "--replay" in actions

    def test_command_handler_rejects_replay_with_session_id(self, tmp_path, capsys):
        from empirica.cli.command_handlers.chat_commands import handle_chat_command

        args = MagicMock()
        args.feed = None
        args.session_id = "resume-id"
        args.replay = "replay-id"
        args.provider = None
        rc = handle_chat_command(args)
        assert rc == 2
        captured = capsys.readouterr()
        assert "conflicts with --session-id" in captured.out

    def test_command_handler_rejects_replay_with_feed(self, tmp_path, capsys):
        from empirica.cli.command_handlers.chat_commands import handle_chat_command

        feed = tmp_path / "feed.jsonl"
        feed.write_text("")
        args = MagicMock()
        args.feed = str(feed)
        args.session_id = None
        args.replay = "replay-id"
        args.provider = None
        rc = handle_chat_command(args)
        assert rc == 2
        captured = capsys.readouterr()
        assert "conflicts with --feed" in captured.out

    def test_command_handler_rejects_missing_session(self, capsys, monkeypatch, tmp_path):
        from empirica.cli.command_handlers.chat_commands import handle_chat_command

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        args = MagicMock()
        args.feed = None
        args.session_id = None
        args.replay = "no-such-session"
        args.provider = None
        rc = handle_chat_command(args)
        assert rc == 2
        captured = capsys.readouterr()
        assert "not found" in captured.out
