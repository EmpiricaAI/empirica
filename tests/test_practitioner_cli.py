"""Tests for the `empirica practitioner write|clear|list` CLI group (B2b).

These verbs are the bridge the stdlib-only session hooks shell out to (the hooks
do not import the empirica package). The group resolves the practitioner's
identity and upserts/clears/lists presence keyed on the DURABLE
``claude_session_id``.

The load-bearing invariant — David's anchor — is encoded in
``test_write_is_keyed_on_claude_session_not_empirica_session``: the empirica
session id rotates per compact window, but presence must stay ONE practitioner
across those rotations because the key is the claude_session_id.
"""

from __future__ import annotations

import argparse

import pytest

from empirica.cli.command_handlers.practitioner_commands import (
    handle_practitioner_group_command,
)
from empirica.cli.parsers.cockpit_parsers import add_cockpit_parsers
from empirica.core import practitioner_presence


@pytest.fixture
def presence_dir(tmp_path, monkeypatch):
    """Redirect presence storage to a tmp dir so tests never touch the real ~/.empirica."""
    d = tmp_path / "empirica"
    monkeypatch.setattr(practitioner_presence, "EMPIRICA_DIR", d)
    return d


def _parse(argv):
    """Parse a real `empirica <argv>` through the cockpit parser (tests wiring too)."""
    parser = argparse.ArgumentParser(prog="empirica")
    subparsers = parser.add_subparsers(dest="command")
    add_cockpit_parsers(subparsers)
    return parser.parse_args(argv)


def _write_argv(session, **over):
    """A fully-overridden write argv — hermetic (no resolver calls hit real state)."""
    argv = [
        "practitioner",
        "write",
        "--session",
        session,
        "--ai-id",
        over.get("ai_id", "empirica-test"),
        "--location",
        over.get("location", "tmux_test"),
        "--empirica-session",
        over.get("empirica_session", "esid-1"),
        "--active-transaction",
        over.get("active_transaction", "tx-1"),
        "--output",
        "json",
    ]
    if "status" in over:
        argv += ["--status", over["status"]]
    if "pending_question" in over:
        argv += ["--pending-question", over["pending_question"]]
    return argv


# ---- parser wiring -------------------------------------------------------


def test_parser_registers_practitioner_group():
    args = _parse(["practitioner", "write", "--session", "cc-1"])
    assert args.command == "practitioner"
    assert args.practitioner_action == "write"
    assert args.session == "cc-1"


def test_group_dispatch_usage_on_no_action(capsys):
    args = _parse(["practitioner"])
    # No sub-action → dispatcher prints usage and returns 2 (argparse convention).
    rc = handle_practitioner_group_command(args)
    assert rc == 2
    assert "usage: empirica practitioner" in capsys.readouterr().out


# ---- write ---------------------------------------------------------------


def test_write_upserts_presence(presence_dir, capsys):
    args = _parse(_write_argv("cc-write", status="active"))
    rc = handle_practitioner_group_command(args)
    assert rc == 0
    capsys.readouterr()  # drain

    rec = practitioner_presence.read_presence("cc-write")
    assert rec is not None
    assert rec["claude_session_id"] == "cc-write"
    assert rec["practice_ai_id"] == "empirica-test"
    assert rec["location"] == "tmux_test"
    assert rec["status"] == "active"
    assert rec["empirica_session_id"] == "esid-1"
    assert rec["active_transaction_id"] == "tx-1"
    assert isinstance(rec["last_heartbeat"], (int, float))


def test_write_carries_pending_question(presence_dir, capsys):
    args = _parse(_write_argv("cc-blocked", status="blocked", pending_question="need schema review"))
    rc = handle_practitioner_group_command(args)
    assert rc == 0
    capsys.readouterr()

    rec = practitioner_presence.read_presence("cc-blocked")
    assert rec["status"] == "blocked"
    assert rec["pending_question"] == "need schema review"


def test_write_invalid_status_fails_loud(presence_dir, capsys):
    args = _parse(_write_argv("cc-bad", status="nonsense"))
    rc = handle_practitioner_group_command(args)
    assert rc == 1  # write_presence raises ValueError → _emit_user_error → 1
    assert practitioner_presence.read_presence("cc-bad") is None


def test_write_is_keyed_on_claude_session_not_empirica_session(presence_dir, capsys):
    """David's anchor: the empirica session rotates per compact window, but the
    claude_session_id is durable — so two heartbeats with DIFFERENT empirica
    sessions under the SAME claude session must remain ONE practitioner."""
    handle_practitioner_group_command(_parse(_write_argv("cc-stable", empirica_session="esid-A")))
    handle_practitioner_group_command(_parse(_write_argv("cc-stable", empirica_session="esid-B")))
    capsys.readouterr()

    # Exactly one presence file for the stable claude_session_id.
    files = list(presence_dir.glob("practitioner_presence_*.json"))
    assert len(files) == 1
    rec = practitioner_presence.read_presence("cc-stable")
    # The churning measurement-cycle attribute reflects the latest heartbeat.
    assert rec["empirica_session_id"] == "esid-B"


# ---- clear ---------------------------------------------------------------


def test_clear_removes_presence(presence_dir, capsys):
    handle_practitioner_group_command(_parse(_write_argv("cc-clear")))
    assert practitioner_presence.read_presence("cc-clear") is not None

    rc = handle_practitioner_group_command(
        _parse(["practitioner", "clear", "--session", "cc-clear", "--output", "json"])
    )
    assert rc == 0
    capsys.readouterr()
    assert practitioner_presence.read_presence("cc-clear") is None


def test_clear_missing_is_noop_success(presence_dir, capsys):
    rc = handle_practitioner_group_command(
        _parse(["practitioner", "clear", "--session", "cc-none", "--output", "json"])
    )
    assert rc == 0  # idempotent — clearing a non-existent practitioner is not an error


# ---- list ----------------------------------------------------------------


def test_list_scopes_to_practice(presence_dir, capsys):
    handle_practitioner_group_command(_parse(_write_argv("cc-a", ai_id="empirica-alpha")))
    handle_practitioner_group_command(_parse(_write_argv("cc-b", ai_id="empirica-beta")))
    capsys.readouterr()

    rows = practitioner_presence.list_presence("empirica-alpha")
    assert [r["claude_session_id"] for r in rows] == ["cc-a"]

    # The resolver returns nothing for a practice with no live practitioner.
    assert practitioner_presence.list_presence("empirica-nobody") == []


def test_list_command_json_returns_zero(presence_dir, capsys):
    handle_practitioner_group_command(_parse(_write_argv("cc-list", ai_id="empirica-gamma")))
    capsys.readouterr()

    rc = handle_practitioner_group_command(
        _parse(["practitioner", "list", "--practice", "empirica-gamma", "--output", "json"])
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "cc-list" in out
    assert "empirica-gamma" in out
