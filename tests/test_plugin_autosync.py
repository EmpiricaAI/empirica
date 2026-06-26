"""GAP 1: cli_core._maybe_autosync_plugin self-heals a stale deployed plugin.

The deployed Claude Code plugin is a copy; ``pip install -U`` upgrades the
package but not the copy. This check runs from the (always-current) CLI and
shells out to ``plugin-sync`` on drift, bootstrapping even a box whose plugin
predates the in-plugin session-init auto-heal. The tests pin: it fires on drift
(including a missing/pre-stamp version), never fires for exempt verbs / opt-out /
within the debounce window, and never raises.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from empirica.cli import cli_core


@pytest.fixture
def harness(tmp_path, monkeypatch):
    home = tmp_path / "home"
    plugin = home / ".claude" / "plugins" / "local" / "empirica"
    plugin.mkdir(parents=True)
    (home / ".empirica").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr("empirica.__version__", "9.9.9")
    monkeypatch.delenv("EMPIRICA_NO_AUTOSYNC", raising=False)
    calls: list = []

    def _run(*a, **k):
        calls.append((a, k))
        return subprocess.CompletedProcess(a[0] if a else [], 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", _run)
    return {"home": home, "plugin": plugin, "calls": calls}


def _stamp(plugin: Path, version: str) -> None:
    (plugin / ".plugin-version").write_text(version + "\n")


def test_drift_triggers_sync(harness):
    _stamp(harness["plugin"], "1.0.0")  # != 9.9.9
    cli_core._maybe_autosync_plugin("finding-log")
    assert len(harness["calls"]) == 1
    assert harness["calls"][0][0][0] == ["empirica", "plugin-sync"]


def test_current_version_no_sync(harness):
    _stamp(harness["plugin"], "9.9.9")  # matches CLI
    cli_core._maybe_autosync_plugin("finding-log")
    assert harness["calls"] == []


def test_missing_stamp_triggers_sync(harness):
    # pre-stamp plugin (no .plugin-version) reads None -> drift -> sync (the bootstrap case)
    cli_core._maybe_autosync_plugin("finding-log")
    assert len(harness["calls"]) == 1


@pytest.mark.parametrize("verb", ["plugin-sync", "setup-claude-code", "plugin-version", "doctor", "help"])
def test_exempt_verb_never_syncs(harness, verb):
    _stamp(harness["plugin"], "1.0.0")  # drift present...
    cli_core._maybe_autosync_plugin(verb)  # ...but the verb is exempt (no re-entrancy)
    assert harness["calls"] == []


def test_optout_env_no_sync(harness, monkeypatch):
    monkeypatch.setenv("EMPIRICA_NO_AUTOSYNC", "1")
    _stamp(harness["plugin"], "1.0.0")
    cli_core._maybe_autosync_plugin("finding-log")
    assert harness["calls"] == []


def test_debounced_recent_marker_no_sync(harness):
    # a fresh marker (mtime ~now, within the 600s TTL) suppresses the check
    (harness["home"] / ".empirica" / ".plugin_autosync_checked").write_text("x")
    _stamp(harness["plugin"], "1.0.0")
    cli_core._maybe_autosync_plugin("finding-log")
    assert harness["calls"] == []


def test_no_plugin_dir_is_safe(tmp_path, monkeypatch):
    # not a Claude Code box (no deployed plugin) -> no sync, no error
    home = tmp_path / "h2"
    (home / ".empirica").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.delenv("EMPIRICA_NO_AUTOSYNC", raising=False)
    calls: list = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(1))
    cli_core._maybe_autosync_plugin("finding-log")  # must not raise
    assert calls == []


def test_sync_failure_never_breaks_command(harness, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("plugin-sync blew up")

    monkeypatch.setattr(subprocess, "run", boom)
    _stamp(harness["plugin"], "1.0.0")
    cli_core._maybe_autosync_plugin("finding-log")  # swallowed; must not raise


def test_sync_failure_writes_breadcrumb(harness, monkeypatch):
    # a non-zero plugin-sync leaves a legible failure breadcrumb — not a silent swallow
    def _fail(*a, **k):
        return subprocess.CompletedProcess(a[0] if a else [], 1, b"", b"permission denied")

    monkeypatch.setattr(subprocess, "run", _fail)
    _stamp(harness["plugin"], "1.0.0")  # drift -> sync runs, fails
    cli_core._maybe_autosync_plugin("finding-log")
    failed = harness["home"] / ".empirica" / ".plugin_autosync_failed"
    assert failed.exists()
    body = failed.read_text()
    assert "rc=1" in body and "permission denied" in body


def test_sync_success_clears_stale_breadcrumb(harness):
    # a recovered box clears a prior failure breadcrumb on the next successful sync
    failed = harness["home"] / ".empirica" / ".plugin_autosync_failed"
    failed.write_text("stale failure from a previous run")
    _stamp(harness["plugin"], "1.0.0")  # drift -> harness sync returns rc=0
    cli_core._maybe_autosync_plugin("finding-log")
    assert not failed.exists()
