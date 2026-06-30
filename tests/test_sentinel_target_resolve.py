"""Tests for the practitioner-identity phase ① sentinel target resolver.

`_resolve_sentinel_targets` is the band-aid that kills the silent pause-miss:
`empirica sentinel pause --instance <ai_id>` used to write sentinel_paused_<ai_id>
while the gate (keyed on the runtime instance_id) never read it — the pauser saw
success, nothing paused. The resolver now maps a practice ai_id to its live
runtime instance, fails LOUD on no-match (the silent-miss class), and requires
--session/--all when an ai_id is ambiguous (>1 live instance — decision-2: no
silent fan-out).

See docs/architecture/instance_isolation/PRACTITIONER_IDENTITY.md §6.
"""

from __future__ import annotations

import types

import pytest

from empirica.cli.command_handlers import cockpit_commands as cc


def _inst(instance_id, ai_id=None, session_id=None):
    return {"instance_id": instance_id, "ai_id": ai_id, "session_id": session_id, "alive": True}


def _args(**kw):
    base = {
        "instance": None,
        "session": None,
        "all": False,
        "global_scope": False,
        "output": "json",
        "reason": None,
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


@pytest.fixture
def patch_live(monkeypatch):
    """Override cockpit_commands.aggregate_all with a controlled live-instance set."""

    def _set(*instances):
        monkeypatch.setattr(cc, "aggregate_all", lambda include_dead=False: {"instances": list(instances)})

    return _set


# ── default / passthrough ────────────────────────────────────────────────────


def test_no_selector_uses_current_instance(monkeypatch, patch_live):
    patch_live(_inst("tmux_5", ai_id="empirica"))
    monkeypatch.setattr(cc, "get_instance_id", lambda: "tmux_5")
    assert cc._resolve_sentinel_targets(_args()) == ["tmux_5"]


def test_no_selector_global_when_no_current(monkeypatch, patch_live):
    patch_live()
    monkeypatch.setattr(cc, "get_instance_id", lambda: None)
    assert cc._resolve_sentinel_targets(_args()) == [None]


def test_direct_instance_id_passthrough(patch_live):
    patch_live(_inst("tmux_3", ai_id="empirica"), _inst("tmux_8", ai_id="cortex"))
    assert cc._resolve_sentinel_targets(_args(instance="tmux_3")) == ["tmux_3"]


# ── --global scope (empirica off --global) ───────────────────────────────────


def test_global_resolves_to_none(monkeypatch):
    # --global → [None] → the single global pause file the gate reads for ALL
    # instances. No live-instance lookup needed; current instance is irrelevant.
    monkeypatch.setattr(cc, "get_instance_id", lambda: "tmux_5")
    assert cc._resolve_sentinel_targets(_args(global_scope=True)) == [None]


def test_global_overrides_narrower_selectors(patch_live):
    # --global is the broadest scope: it wins even when --instance / --all are set.
    patch_live(_inst("tmux_3", ai_id="empirica"), _inst("tmux_8", ai_id="empirica"))
    assert cc._resolve_sentinel_targets(_args(global_scope=True, instance="tmux_3")) == [None]
    assert cc._resolve_sentinel_targets(_args(global_scope=True, all=True)) == [None]


# ── ai_id resolution (the fix) ───────────────────────────────────────────────


def test_ai_id_single_resolves_to_runtime(patch_live):
    patch_live(_inst("tmux_8", ai_id="empirica"))
    # The ① fix: --instance <ai_id> → its live runtime instance, not verbatim.
    assert cc._resolve_sentinel_targets(_args(instance="empirica")) == ["tmux_8"]


def test_ai_id_no_live_instance_is_loud(patch_live):
    patch_live(_inst("tmux_8", ai_id="cortex"))
    with pytest.raises(cc.SentinelResolveError):
        cc._resolve_sentinel_targets(_args(instance="empirica"))


def test_unknown_instance_id_is_loud(patch_live):
    # Previously a silent success: wrote sentinel_paused_<ghost> the gate never reads.
    patch_live(_inst("tmux_8", ai_id="empirica"))
    with pytest.raises(cc.SentinelResolveError):
        cc._resolve_sentinel_targets(_args(instance="tmux_99"))


def test_ai_id_ambiguous_without_all_is_loud(patch_live):
    patch_live(_inst("tmux_3", ai_id="empirica"), _inst("tmux_8", ai_id="empirica"))
    with pytest.raises(cc.SentinelResolveError):
        cc._resolve_sentinel_targets(_args(instance="empirica"))


def test_ai_id_ambiguous_with_all_fans_out(patch_live):
    patch_live(_inst("tmux_3", ai_id="empirica"), _inst("tmux_8", ai_id="empirica"))
    out = cc._resolve_sentinel_targets(_args(instance="empirica", all=True))
    assert sorted(out) == ["tmux_3", "tmux_8"]


# ── --session / --all selectors ──────────────────────────────────────────────


def test_session_resolves_to_instance(patch_live):
    patch_live(_inst("tmux_8", ai_id="empirica", session_id="sess-abc"))
    assert cc._resolve_sentinel_targets(_args(session="sess-abc")) == ["tmux_8"]


def test_session_no_match_is_loud(patch_live):
    patch_live(_inst("tmux_8", ai_id="empirica", session_id="sess-abc"))
    with pytest.raises(cc.SentinelResolveError):
        cc._resolve_sentinel_targets(_args(session="sess-zzz"))


def test_all_without_instance_targets_every_live(patch_live):
    patch_live(_inst("tmux_3", ai_id="empirica"), _inst("tmux_8", ai_id="cortex"))
    out = cc._resolve_sentinel_targets(_args(all=True))
    assert sorted(out) == ["tmux_3", "tmux_8"]


# ── handler integration: loud-fail + resolution land in pause_sentinel ───────


def test_pause_handler_loud_fail_does_not_pause(monkeypatch, patch_live):
    patch_live(_inst("tmux_8", ai_id="cortex"))
    calls: list = []
    monkeypatch.setattr(cc, "pause_sentinel", lambda *a, **k: calls.append((a, k)))
    rc = cc.handle_sentinel_pause_command(_args(instance="empirica"))
    assert rc == 1  # loud (non-zero exit)
    assert calls == []  # never paused anything on a no-match


def test_pause_handler_resolves_ai_id_before_pausing(monkeypatch, patch_live):
    patch_live(_inst("tmux_8", ai_id="empirica"))
    calls: list = []

    class _St:
        paused = True
        instance_id = "tmux_8"
        scope = "instance"
        since = "now"
        reason = None

    def _fake_pause(instance_id, reason=None):
        calls.append(instance_id)
        return _St()

    monkeypatch.setattr(cc, "pause_sentinel", _fake_pause)
    rc = cc.handle_sentinel_pause_command(_args(instance="empirica"))
    assert rc == 0
    assert calls == ["tmux_8"]  # the resolved runtime id, NOT the literal "empirica"
