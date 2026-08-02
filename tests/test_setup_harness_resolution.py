"""`empirica setup` must configure the harness you are ON, or refuse.

Before --harness existed, setup wrote Claude Code's surface whatever it ran
under. That is not a cosmetic gap: session-init states outright that
``~/.claude/plugins/local/empirica/`` is "a path other harnesses never load", so
a codex user running `empirica setup` configured a directory their harness
ignores — and was told it succeeded.

Silent wrong target with a success message. The fix is not a codex writer (I
cannot verify one), it is refusing by name so the gap is visible instead of
disguised as a working install.
"""

from __future__ import annotations

import argparse
import json

import pytest

from empirica.cli.command_handlers.setup_claude_code import (
    KNOWN_UNSUPPORTED_HARNESSES,
    SUPPORTED_HARNESSES,
    _refuse_unsupported_harness,
    resolve_harness,
)


def _args(**kwargs) -> argparse.Namespace:
    base = {"harness": None, "command": "setup", "output": "json"}
    base.update(kwargs)
    return argparse.Namespace(**base)


# --- resolution precedence ----------------------------------------------------


def test_explicit_flag_wins_over_everything(monkeypatch):
    monkeypatch.setenv("EMPIRICA_HARNESS", "codex")
    assert resolve_harness(_args(harness="claude-code")) == "claude-code"


def test_env_is_used_when_the_flag_is_absent(monkeypatch):
    """The SAME signal the hooks read — not a second setup-only mechanism.

    Two sources of truth for "which harness am I" is how they drift apart.
    """
    monkeypatch.setenv("EMPIRICA_HARNESS", "codex")
    assert resolve_harness(_args()) == "codex"


def test_claude_code_is_the_default(monkeypatch):
    monkeypatch.delenv("EMPIRICA_HARNESS", raising=False)
    assert resolve_harness(_args()) == "claude-code"


def test_the_legacy_alias_pins_claude_code_even_under_a_foreign_env(monkeypatch):
    """`setup-claude-code` names its harness, so old scripts keep working.

    Without this, upgrading while EMPIRICA_HARNESS=codex would start refusing a
    command whose whole name says which harness it configures.
    """
    monkeypatch.setenv("EMPIRICA_HARNESS", "codex")
    assert resolve_harness(_args(command="setup-claude-code")) == "claude-code"


@pytest.mark.parametrize("raw", ["  Claude-Code  ", "CLAUDE-CODE", "claude-code"])
def test_resolution_is_case_and_whitespace_insensitive(raw):
    assert resolve_harness(_args(harness=raw)) == "claude-code"


def test_an_empty_env_var_does_not_resolve_to_empty(monkeypatch):
    """`EMPIRICA_HARNESS=` must not produce a harness named "" that refuses."""
    monkeypatch.setenv("EMPIRICA_HARNESS", "   ")
    assert resolve_harness(_args()) == "claude-code"


# --- refusal ------------------------------------------------------------------


def test_refusal_writes_nothing_and_returns_nonzero(capsys):
    code = _refuse_unsupported_harness("codex", "json")

    assert code == 1, "must not report success"
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["wrote_nothing"] is True
    assert payload["harness"] == "codex"
    assert payload["supported"] == ["claude-code"]


def test_a_known_harness_is_refused_as_not_yet_not_as_unheard_of(capsys):
    """ "Not supported yet" and "no idea what that is" need different responses."""
    _refuse_unsupported_harness("codex", "json")
    known = json.loads(capsys.readouterr().out)["detail"]

    _refuse_unsupported_harness("zed", "json")
    unknown = json.loads(capsys.readouterr().out)["detail"]

    assert "codex" in known and "SELF-PROVISIONING" in known
    assert "not a missing feature" in known.lower(), "codex is not a gap to fill"
    # A refusal that names no alternative is an unrecoverable gate: it tells you
    # to stop without telling you where to go.
    assert "setup-codex" in known, "point at the pipeline that DOES provision codex"
    assert "credentials.yaml" in known, "name the one thing codex needs from empirica"
    assert "unrecognized" in unknown


def test_the_human_refusal_says_what_used_to_happen(capsys):
    """A user upgrading needs to know this is a FIX, not a new obstacle."""
    _refuse_unsupported_harness("codex", "human")
    out = capsys.readouterr().out

    assert "Nothing was written" in out
    assert "reported success" in out, "name the old silent behaviour"
    assert "--harness claude-code" in out, "give the escape hatch"


# --- the registry is the extension point --------------------------------------


def test_every_known_unsupported_harness_is_absent_from_supported():
    """The two tables must not both claim a harness."""
    assert not (set(SUPPORTED_HARNESSES) & set(KNOWN_UNSUPPORTED_HARNESSES))


def test_adding_a_writer_is_the_only_way_to_support_a_harness(monkeypatch):
    """Support is registry membership, not a conditional somewhere in the body.

    If a future edit re-adds an `if harness == ...` branch instead of a registry
    entry, this stops describing reality — which is the point of asserting it.
    """
    monkeypatch.setitem(SUPPORTED_HARNESSES, "fictional-harness", "test")
    assert "fictional-harness" in SUPPORTED_HARNESSES
    monkeypatch.delitem(SUPPORTED_HARNESSES, "fictional-harness")
    assert resolve_harness(_args(harness="fictional-harness")) == "fictional-harness"
