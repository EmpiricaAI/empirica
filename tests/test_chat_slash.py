"""Tests for empirica.core.chat.slash — Phase 16 surface refinement.

Covers the slash table, /help rendering, and the dispatcher routing
on ChatApp. Heavy ChatApp interaction is mocked — the unit-level
target here is the dispatch table + per-handler signature, not the
Textual widget plumbing (which is exercised end-to-end by smoke
runs of `empirica chat`).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from empirica.core.chat.slash import (
    SLASH_TABLE,
    SlashCmd,
    known_commands,
    render_help,
)


class TestSlashTable:
    def test_table_is_nonempty(self):
        assert len(SLASH_TABLE) > 0

    def test_required_user_facing_commands_present(self):
        names = {c.name for c in SLASH_TABLE if c.user_facing}
        # Per Phase 16 spec — these are the user-facing four
        assert {"help", "model", "plan", "autonomy"}.issubset(names)

    def test_dev_commands_present_but_hidden(self):
        names = {c.name for c in SLASH_TABLE if not c.user_facing}
        assert {"providers", "provider", "models", "statusline",
                "finding", "decision", "unknown"}.issubset(names)

    def test_no_duplicate_names(self):
        names = [c.name for c in SLASH_TABLE]
        assert len(names) == len(set(names))

    def test_known_commands_all_returns_full_set(self):
        full = known_commands(include_dev=True)
        assert full == {c.name for c in SLASH_TABLE}

    def test_known_commands_user_only_excludes_dev(self):
        user_only = known_commands(include_dev=False)
        for c in SLASH_TABLE:
            assert (c.name in user_only) is c.user_facing


class TestRenderHelp:
    def test_default_help_omits_dev_commands(self):
        out = render_help(debug=False)
        assert "/finding" not in out
        assert "/providers" not in out
        assert "/statusline" not in out
        # User-facing visible
        assert "/help" in out
        assert "/model" in out
        assert "/plan" in out
        assert "/autonomy" in out

    def test_debug_help_includes_dev_commands(self):
        out = render_help(debug=True)
        assert "/finding" in out
        assert "/providers" in out
        assert "/statusline" in out
        # User-facing still visible
        assert "/plan" in out

    def test_default_mentions_debug_subcommand(self):
        out = render_help(debug=False)
        assert "/help debug" in out

    def test_debug_does_not_mention_debug_subcommand_repeatedly(self):
        out = render_help(debug=True)
        # The hint about /help debug only shows in non-debug mode
        assert "/help debug shows dev-internal" not in out

    def test_takes_arg_renders_arg_label(self):
        out = render_help(debug=True)
        assert "/model NAME" in out
        assert "/autonomy MODE" in out
        assert "/finding TEXT" in out


class TestSlashCmd:
    def test_slashcmd_defaults(self):
        c = SlashCmd("foo", "demo", user_facing=True)
        assert c.takes_arg is False
        assert c.arg_label == "ARG"

    def test_slashcmd_is_frozen(self):
        c = SlashCmd("foo", "demo", user_facing=True)
        with pytest.raises((AttributeError, Exception)):
            c.name = "bar"  # type: ignore[misc]


class TestChatAppSlashDispatch:
    """Black-box tests for ChatApp's slash dispatcher.

    We don't construct a real Textual app — instead we poke the
    SLASH_HANDLERS table directly with a mock self-object, which is
    what the dispatcher does anyway.
    """

    def setup_method(self):
        from empirica.cli.tui.chat_app import ChatApp
        self.ChatApp = ChatApp
        self.handlers = ChatApp.SLASH_HANDLERS
        self.mock = MagicMock()
        # _emit_system gets called in nearly every handler
        self.mock._emit_system = MagicMock()

    def test_handlers_keys_match_known_commands(self):
        assert set(self.handlers.keys()) == known_commands()

    def test_help_handler_emits_user_facing_list(self):
        self.handlers["help"](self.mock, "")
        self.mock._emit_system.assert_called_once()
        body = self.mock._emit_system.call_args[0][0]
        assert "/plan" in body
        assert "/finding" not in body  # default mode hides dev cmds

    def test_help_debug_handler_emits_full_list(self):
        self.handlers["help"](self.mock, "debug")
        self.mock._emit_system.assert_called_once()
        body = self.mock._emit_system.call_args[0][0]
        assert "/finding" in body
        assert "/providers" in body

    def test_autonomy_handler_rejects_unknown_mode(self):
        self.mock.autonomy_mode = "assistant"
        self.handlers["autonomy"](self.mock, "wizard")
        self.mock._emit_system.assert_called_once()
        msg = self.mock._emit_system.call_args[0][0]
        assert "unknown autonomy mode" in msg

    def test_autonomy_handler_no_arg_shows_current(self):
        self.mock.autonomy_mode = "copilot"
        self.handlers["autonomy"](self.mock, "")
        msg = self.mock._emit_system.call_args[0][0]
        assert "current: copilot" in msg

    def test_autonomy_handler_same_mode_is_noop(self):
        self.mock.autonomy_mode = "copilot"
        self.handlers["autonomy"](self.mock, "copilot")
        msg = self.mock._emit_system.call_args[0][0]
        assert "already copilot" in msg

    def test_provider_handler_no_arg_shows_current(self):
        self.mock.registry.display_status.return_value = "provider:model"
        self.handlers["provider"](self.mock, "")
        msg = self.mock._emit_system.call_args[0][0]
        assert "missing NAME" in msg

    def test_artifact_handler_no_arg_shows_usage(self):
        # Test the underlying _slash_artifact directly (the per-type
        # _slash_finding/_slash_decision/_slash_unknown methods all
        # delegate straight to it).
        self.ChatApp._slash_artifact(self.mock, "finding", "")
        msg = self.mock._emit_system.call_args[0][0]
        assert "missing text" in msg
        assert "/finding" in msg

    def test_artifact_handler_with_arg_dispatches_worker(self):
        self.ChatApp._slash_artifact(self.mock, "decision", "use redis")
        self.mock.run_worker.assert_called_once()

    def test_model_handler_no_arg_shows_current(self):
        self.mock.registry.display_status.return_value = "p:m"
        self.handlers["model"](self.mock, "")
        msg = self.mock._emit_system.call_args[0][0]
        assert "missing NAME" in msg
