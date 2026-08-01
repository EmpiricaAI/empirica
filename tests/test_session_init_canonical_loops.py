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


# Both canonical loops are now opt-in — cortex-mailbox-poll because wake-on-event
# is the canonical mesh trigger, message-cleanup because it is kind="cron" and no
# cron loop is installed by default (David 2026-08-01). So the catalogue no
# longer contains anything auto-installable, and these MECHANISM tests must not
# depend on it doing so: they inject their own ordinary interval loop. That also
# decouples them from the next policy change, which is what broke them this time.
AUTO_INSTALLABLE = {
    "name": "test-interval-loop",
    "kind": "interval",
    "interval": "5m",
    "description": "an ordinary auto-installable loop, injected by the test",
    "body_skill": "noop",
}


@pytest.fixture
def auto_installable(monkeypatch):
    from empirica.core.cockpit import canonical_loops as cl

    monkeypatch.setattr(cl, "CANONICAL_LOOPS", [*cl.CANONICAL_LOOPS, AUTO_INSTALLABLE])
    return AUTO_INSTALLABLE["name"]


def _make_empirica_project(tmp_path) -> Path:
    """Create a project root with .empirica/ (empirica-aware)."""
    project = tmp_path / "project"
    (project / ".empirica").mkdir(parents=True)
    return project


def test_installs_on_fresh_empirica_aware_project(session_init_module, tmp_path, auto_installable):
    """Project has .empirica/, instance is fresh → install the auto-installable loop."""
    project = _make_empirica_project(tmp_path)
    count = session_init_module._maybe_auto_install_canonical_loops(project)
    assert count >= 1

    # Stamp file should now exist (idempotency marker)
    home = Path(tmp_path / "home")
    stamp_glob = list((home / ".empirica").glob("canonical_loops_installed_*"))
    assert len(stamp_glob) == 1


def test_idempotent_via_stamp_file(session_init_module, tmp_path, auto_installable):
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


def test_auto_install_queues_only_non_opt_in_non_cron(session_init_module, tmp_path, auto_installable):
    """Auto-install queues an ordinary interval loop with a well-formed template,
    and queues NEITHER an opt_in_only loop (cortex-mailbox-poll — wake-on-event is
    the canonical trigger) NOR any cron loop (message-cleanup — cron is opt-in
    only, never installed by default).

    This pins two fixes at once: the drift the shared-helper dedup closed
    (session-init used to lack the opt_in_only carve-out and queued
    cortex-mailbox-poll every session), and the cron policy that superseded
    message-cleanup's housekeeping exemption."""
    import json

    project = _make_empirica_project(tmp_path)
    count = session_init_module._maybe_auto_install_canonical_loops(project)
    assert count >= 1

    home = Path(tmp_path / "home")
    pending = list((home / ".empirica").glob("loop_install_pending_*_test-interval-loop.json"))
    assert len(pending) == 1
    assert json.loads(pending[0].read_text()).get("prompt_template")  # real template, not blank
    # No cron loop may be queued, by policy.
    assert not list((home / ".empirica").glob("loop_install_pending_*_message-cleanup.json"))
    # opt_in_only loops must NOT be auto-queued.
    assert not list((home / ".empirica").glob("loop_install_pending_*_cortex-mailbox-poll.json"))
