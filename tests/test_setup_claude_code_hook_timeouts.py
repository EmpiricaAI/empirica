"""Tests for the generated Claude Code hook timeouts.

The lightweight per-prompt / per-SessionStart hooks (tool-router, context-shift
tracker, monitor-arm, loop/listener pickups) were generated at 3s/5s. On a
many-instance restart-herd box those timed out and Claude Code silently
discarded their stdout (context injection lost). They're now generated at
``LIGHT_HOOK_TIMEOUT`` (>= 10s). This guards against regressing back to the
tight values.
"""

from __future__ import annotations

from pathlib import Path

from empirica.cli.command_handlers import setup_claude_code as scc

# The lightweight hooks that must run on the generous timeout.
_TIGHT_HOOKS = {
    "tool-router.py",
    "context-shift-tracker.py",
    "session-monitor-arm.py",
    "loop-install-pickup.py",
    "loop-uninstall-pickup.py",
    "listener-install-pickup.py",
    "listener-uninstall-pickup.py",
}


def _collect_hook_timeouts(settings: dict) -> dict[str, int]:
    """Map hook-script basename → timeout across all generated events."""
    found: dict[str, int] = {}
    for entries in settings["hooks"].values():
        for entry in entries:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                for name in _TIGHT_HOOKS:
                    if name in cmd:
                        found[name] = h.get("timeout")
    return found


def test_light_hook_timeout_is_generous():
    assert scc.LIGHT_HOOK_TIMEOUT >= 10


def test_tight_hooks_generated_with_light_timeout():
    settings: dict = {"hooks": {}}
    scc._register_all_hooks(settings, Path("/plugin"), "python3", "json")

    timeouts = _collect_hook_timeouts(settings)
    assert timeouts, "expected to find the lightweight hooks in generated settings"
    for name, t in timeouts.items():
        assert t == scc.LIGHT_HOOK_TIMEOUT, f"{name} timeout={t}, expected {scc.LIGHT_HOOK_TIMEOUT}"
        assert t >= 10, f"{name} timeout={t} is too tight (restart-herd drops context injection)"


def test_heavy_hooks_keep_their_own_timeouts():
    """The compaction / session-init / postflight hooks must NOT be lowered to
    the light timeout — they legitimately need their larger budgets."""
    settings: dict = {"hooks": {}}
    scc._register_all_hooks(settings, Path("/plugin"), "python3", "json")

    heavy_seen = {}
    for entries in settings["hooks"].values():
        for entry in entries:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                for name in ("pre-compact.py", "post-compact.py", "session-init.py"):
                    if name in cmd:
                        heavy_seen[name] = h.get("timeout")
    assert heavy_seen, "expected heavy hooks present"
    for name, t in heavy_seen.items():
        assert t >= 20, f"{name} timeout={t} unexpectedly low"
