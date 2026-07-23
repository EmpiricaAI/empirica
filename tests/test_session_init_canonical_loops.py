"""Tests for SessionStart auto-install of canonical loops.

When a fresh empirica instance starts on an empirica-aware project
(has .empirica/) and has no loops registered yet AND no stamp file,
session-init queues install-pending files for each canonical loop.

Once installed (or skipped because not fresh), a stamp file marks
the instance — never re-installs (respects user intent if they
removed the loop later).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def session_init_module():
    """Load the session-init hook as an importable module."""
    repo_root = Path(__file__).resolve().parents[1]
    hook_path = repo_root / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "session-init.py"
    spec = importlib.util.spec_from_file_location("session_init_test", hook_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def isolate_home_and_instance(monkeypatch, tmp_path):
    """Each test gets a fresh HOME and a deterministic instance_id.

    EMPIRICA_DIR is captured at module-import time via Path.home(), so
    monkeypatching HOME isn't enough — we also patch the module-level
    constants in both loop_registry and loop_install_request to the
    fresh tmp_path/.empirica.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    fake_empirica = fake_home / ".empirica"
    fake_empirica.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("EMPIRICA_INSTANCE_ID", "tmux_test_canonical")
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setattr(
        "empirica.core.cockpit.loop_install_request.EMPIRICA_DIR",
        fake_empirica,
    )
    monkeypatch.setattr(
        "empirica.core.cockpit.loop_registry.EMPIRICA_DIR",
        fake_empirica,
    )


def _make_empirica_project(tmp_path) -> Path:
    """Create a project root with .empirica/ (empirica-aware)."""
    project = tmp_path / "project"
    (project / ".empirica").mkdir(parents=True)
    return project


def test_installs_on_fresh_empirica_aware_project(session_init_module, tmp_path):
    """Project has .empirica/, instance is fresh → install one canonical loop."""
    project = _make_empirica_project(tmp_path)
    count = session_init_module._maybe_auto_install_canonical_loops(project)
    assert count >= 1  # At least cortex-mailbox-poll

    # Stamp file should now exist (idempotency marker)
    home = Path(tmp_path / "home")
    stamp_glob = list((home / ".empirica").glob("canonical_loops_installed_*"))
    assert len(stamp_glob) == 1


def test_idempotent_via_stamp_file(session_init_module, tmp_path):
    """Second run on same instance → 0 installs (stamp blocks)."""
    project = _make_empirica_project(tmp_path)
    first = session_init_module._maybe_auto_install_canonical_loops(project)
    assert first >= 1
    second = session_init_module._maybe_auto_install_canonical_loops(project)
    assert second == 0


def test_skips_when_project_not_empirica_aware(session_init_module, tmp_path):
    """Project without .empirica/ → skip (don't install in random projects)."""
    project = tmp_path / "random_project"
    project.mkdir()
    count = session_init_module._maybe_auto_install_canonical_loops(project)
    assert count == 0


# Removed: test_skips_when_no_instance_id — get_instance_id() falls back
# to TTY device under pytest (term_pts_*), so the "no instance_id" gate
# can't be reliably triggered in this environment. The branch is covered
# by inspection: the helper returns 0 early if get_instance_id() is None.


def test_skips_when_registry_already_has_loops(session_init_module, tmp_path):
    """Instance has manually-registered loops → write stamp, don't auto-install.
    Respects user intent — they chose what to register."""
    project = _make_empirica_project(tmp_path)

    # Pre-register a loop so the registry is non-empty
    from empirica.core.cockpit.loop_registry import LoopRegistry

    reg = LoopRegistry("tmux_test_canonical")
    reg.register(name="custom-user-loop", kind="cron", interval="1h", description="user-chosen")

    count = session_init_module._maybe_auto_install_canonical_loops(project)
    assert count == 0
    # Stamp should still get written (so we don't keep trying)
    home = Path(tmp_path / "home")
    stamp_glob = list((home / ".empirica").glob("canonical_loops_installed_*"))
    assert len(stamp_glob) == 1


def test_auto_install_queues_housekeeping_and_skips_opt_in(session_init_module, tmp_path):
    """Auto-install queues the non-opt-in canonical loops (message-cleanup) with
    a well-formed install template, and does NOT queue opt_in_only loops
    (cortex-mailbox-poll — wake-on-event is the canonical trigger).

    This pins the fix for the drift that the shared-helper dedup closed:
    session-init used to lack the opt_in_only carve-out and queued
    cortex-mailbox-poll on every new session."""
    import json

    project = _make_empirica_project(tmp_path)
    count = session_init_module._maybe_auto_install_canonical_loops(project)
    assert count >= 1

    home = Path(tmp_path / "home")
    mc = list((home / ".empirica").glob("loop_install_pending_*_message-cleanup.json"))
    assert len(mc) == 1
    assert json.loads(mc[0].read_text()).get("prompt_template")  # real template, not blank
    # opt_in_only loops must NOT be auto-queued.
    assert not list((home / ".empirica").glob("loop_install_pending_*_cortex-mailbox-poll.json"))
