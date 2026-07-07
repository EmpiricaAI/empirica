"""Harness-guard on session-init's `_auto_sync_plugin` (ecodex prop_gcwxecse).

`_auto_sync_plugin` shells `empirica plugin-sync` on every SessionStart to heal
a stale installed CC plugin. That only makes sense under Claude Code: plugin-sync
heals ``~/.claude/plugins/local/empirica/``, a path other harnesses never load.
These tests pin the guard: it fires under CC (default), no-ops under a non-CC
harness (``EMPIRICA_HARNESS=codex``), and honors the explicit
``EMPIRICA_NO_AUTOSYNC`` opt-out — while never raising.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

HOOK_PATH = (
    Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "session-init.py"
)
_spec = importlib.util.spec_from_file_location("session_init_harness_guard", HOOK_PATH)
assert _spec is not None and _spec.loader is not None
session_init = importlib.util.module_from_spec(_spec)
sys.modules["session_init_harness_guard"] = session_init
_spec.loader.exec_module(session_init)


# ── _harness() — the identity signal ────────────────────────────────────


def test_harness_defaults_to_claude_code(monkeypatch):
    monkeypatch.delenv("EMPIRICA_HARNESS", raising=False)
    assert session_init._harness() == "claude-code"


def test_harness_reads_env(monkeypatch):
    monkeypatch.setenv("EMPIRICA_HARNESS", "codex")
    assert session_init._harness() == "codex"


def test_harness_blank_falls_back_to_claude_code(monkeypatch):
    # An empty / whitespace value must not defeat the default (env set to "" by a
    # launcher shouldn't read as a distinct harness that skips the CC side effects).
    monkeypatch.setenv("EMPIRICA_HARNESS", "   ")
    assert session_init._harness() == "claude-code"


# ── _auto_sync_plugin() — the guard ─────────────────────────────────────


def test_autosync_fires_under_claude_code(monkeypatch):
    monkeypatch.delenv("EMPIRICA_HARNESS", raising=False)
    monkeypatch.delenv("EMPIRICA_NO_AUTOSYNC", raising=False)
    with patch.object(session_init.subprocess, "run") as run:
        session_init._auto_sync_plugin()
    assert run.call_count == 1
    assert run.call_args[0][0] == ["empirica", "plugin-sync", "--quiet"]


def test_autosync_skipped_under_non_cc_harness(monkeypatch):
    monkeypatch.setenv("EMPIRICA_HARNESS", "codex")
    monkeypatch.delenv("EMPIRICA_NO_AUTOSYNC", raising=False)
    with patch.object(session_init.subprocess, "run") as run:
        session_init._auto_sync_plugin()
    run.assert_not_called()  # heals a path codex never loads — pure wasted work


def test_autosync_honors_no_autosync_optout_under_cc(monkeypatch):
    monkeypatch.delenv("EMPIRICA_HARNESS", raising=False)  # => claude-code
    monkeypatch.setenv("EMPIRICA_NO_AUTOSYNC", "1")
    with patch.object(session_init.subprocess, "run") as run:
        session_init._auto_sync_plugin()
    run.assert_not_called()  # CC user opted out — parity with cli_core auto-sync


def test_autosync_never_raises_on_subprocess_error(monkeypatch):
    monkeypatch.delenv("EMPIRICA_HARNESS", raising=False)
    monkeypatch.delenv("EMPIRICA_NO_AUTOSYNC", raising=False)
    with patch.object(session_init.subprocess, "run", side_effect=OSError("boom")):
        session_init._auto_sync_plugin()  # best-effort — must not raise
