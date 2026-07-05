"""Tests for the Sentinel gating-state-change emit (immune-system Req 1).

Autonomy ruling prop_72y3dqeb: a pause/resume that changes gating state must
REPORT itself on cortex's system-event surface so a silent disarm of the fleet's
gates is observable (the incident: fleet ran ungated ~50min undetected). Emit is
best-effort — it must never raise or block the pause.
"""

from __future__ import annotations

import types

from empirica.cli.command_handlers.cockpit_commands import _emit_sentinel_state_event


def _status(scope="global", instance_id="tmux_4", reason=None):
    return types.SimpleNamespace(scope=scope, instance_id=instance_id, reason=reason)


def test_envelope_matches_ruling_schema():
    captured = {}
    _emit_sentinel_state_event(
        "sentinel_pause",
        _status(scope="global", instance_id="tmux_4", reason="deadlock recovery"),
        _emit_fn=lambda env: captured.update(env),
    )
    assert captured["event"] == "sentinel_pause"
    assert captured["scope"] == "global"
    assert captured["instance_id"] == "tmux_4"
    assert captured["reason"] == "deadlock recovery"
    assert captured["actor"] == "cli"
    assert captured["emit_path"] == "cli_verb"  # marks verb-emitted vs raw-touch
    assert "created_at" in captured
    assert "practice_ai_id" in captured  # best-available (may be None off-project)


def test_resume_event_name():
    captured = {}
    _emit_sentinel_state_event(
        "sentinel_resume",
        _status(scope="instance", instance_id="tmux_1"),
        _emit_fn=lambda env: captured.update(env),
    )
    assert captured["event"] == "sentinel_resume"
    assert captured["scope"] == "instance"


def test_emit_failure_never_raises():
    def boom(_env):
        raise ConnectionError("cortex unreachable")

    # Must swallow — a pause must not fail on an emit error.
    _emit_sentinel_state_event("sentinel_pause", _status(), _emit_fn=boom)


def test_handler_emits_on_pause(monkeypatch):
    """The pause handler emits a state-change event per affected target — without
    touching a real pause file (pause_sentinel + emit are stubbed)."""
    from empirica.cli.command_handlers import cockpit_commands as cc

    emitted = []
    monkeypatch.setattr(cc, "_resolve_sentinel_targets", lambda args: ["tmux_9"])
    monkeypatch.setattr(
        cc,
        "pause_sentinel",
        lambda t, reason=None: types.SimpleNamespace(
            paused=True, instance_id=t, scope="instance", since="now", reason=reason
        ),
    )
    monkeypatch.setattr(
        "empirica.cli.command_handlers.system_event.emit_system_event",
        lambda env, **kw: emitted.append(env) or (200, {"ok": True}),
    )
    args = types.SimpleNamespace(reason="test", output="json")
    rc = cc.handle_sentinel_pause_command(args)
    assert rc == 0
    assert len(emitted) == 1
    assert emitted[0]["event"] == "sentinel_pause"
    assert emitted[0]["instance_id"] == "tmux_9"
    assert emitted[0]["emit_path"] == "cli_verb"
