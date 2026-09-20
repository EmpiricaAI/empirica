"""Tests for the session-monitor-arm SessionStart hook.

The hook bridges systemd timer fires into a running Claude session by
emitting additionalContext that tells the AI to arm a persistent Monitor
tailing ~/.empirica/loop_fires.log filtered to its instance_id.

We test:
  - list_active_loops_for_instance() filters by instance prefix correctly
  - hook emits empty output when no active loops (no false instructions)
  - hook emits the arm-Monitor block when active loops exist
  - reaction-table maps each loop → its canonical body_skill
  - tail/grep command targets the right log + filter
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from empirica.core.loop_scheduler import (
    list_active_loops_for_instance,
)
from empirica.core.loop_scheduler import (
    systemd as scheduler_mod,
)


def _fake_run(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


# ── Helper: list_active_loops_for_instance ──────────────────────────────


def test_list_active_loops_returns_empty_when_systemd_unavailable(monkeypatch):
    monkeypatch.setattr(scheduler_mod, "is_systemd_available", lambda: False)
    assert list_active_loops_for_instance("cortex") == []


def test_list_active_loops_filters_by_instance_prefix(monkeypatch):
    monkeypatch.setattr(scheduler_mod, "is_systemd_available", lambda: True)

    list_unit_files_out = (
        "empirica-loop-cortex-mailbox-poll.timer enabled enabled\n"
        "empirica-loop-empirica-mailbox-poll.timer enabled enabled\n"
        "empirica-loop-outreach-engagement-fetch.timer enabled enabled\n"
    )

    def fake_run(args, **kw):
        if "list-unit-files" in args:
            return _fake_run(stdout=list_unit_files_out)
        if "is-active" in args:
            return _fake_run(stdout="active\n")
        return _fake_run(stdout="")

    with patch.object(subprocess, "run", fake_run):
        cortex_loops = list_active_loops_for_instance("cortex")
        empirica_loops = list_active_loops_for_instance("empirica")
        ghost_loops = list_active_loops_for_instance("ghost-instance")

    assert cortex_loops == ["mailbox-poll"]
    assert empirica_loops == ["mailbox-poll"]
    assert ghost_loops == []


def test_list_active_loops_skips_inactive_timers(monkeypatch):
    monkeypatch.setattr(scheduler_mod, "is_systemd_available", lambda: True)
    list_unit_files_out = (
        "empirica-loop-cortex-installed-but-inactive.timer disabled disabled\n"
        "empirica-loop-cortex-active-one.timer enabled enabled\n"
    )

    def fake_run(args, **kw):
        if "list-unit-files" in args:
            return _fake_run(stdout=list_unit_files_out)
        if "is-active" in args:
            # Only the second is active
            unit = next((a for a in args if a.endswith(".timer")), "")
            return _fake_run(stdout=("active" if "active-one" in unit else "inactive") + "\n")
        return _fake_run()

    with patch.object(subprocess, "run", fake_run):
        loops = list_active_loops_for_instance("cortex")

    assert loops == ["active-one"]


# ── Hook: session-monitor-arm.py ────────────────────────────────────────


def _run_hook(monkeypatch, instance_id: str | None, active_loops: list[str]) -> dict:
    """Run the hook script's main() in-process, capturing stdout JSON."""
    import importlib.util as _ilu

    hook_path = Path(__file__).resolve().parents[1] / (
        "empirica/plugins/claude-code-integration/hooks/session-monitor-arm.py"
    )
    spec = _ilu.spec_from_file_location("session_monitor_arm_hook", hook_path)
    assert spec and spec.loader, "could not load hook spec"
    mod = _ilu.module_from_spec(spec)
    sys.modules["session_monitor_arm_hook"] = mod
    spec.loader.exec_module(mod)

    monkeypatch.setattr(
        mod.InstanceResolver,
        "instance_id",
        classmethod(lambda cls: instance_id),
    )
    # Post-2026-05-16: hook now calls InstanceResolver.ai_id() first and
    # falls back to instance_id. Stub ai_id to return the same value so
    # tests stay deterministic regardless of the runner's project state.
    monkeypatch.setattr(
        mod.InstanceResolver,
        "ai_id",
        classmethod(lambda cls, *a, **k: instance_id),
    )
    monkeypatch.setattr(
        mod,
        "list_active_loops_for_instance",
        lambda iid: list(active_loops),
    )

    from io import StringIO

    captured = StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    rc = mod.main()
    output = captured.getvalue().strip()
    return {"rc": rc, "stdout": output, "parsed": json.loads(output) if output else {}}


def test_hook_emits_empty_when_no_instance_id(monkeypatch):
    result = _run_hook(monkeypatch, instance_id=None, active_loops=[])
    assert result["rc"] == 0
    assert result["parsed"] == {}


def test_hook_emits_empty_when_no_loops_AND_no_persistent_service(monkeypatch, tmp_path):
    """No wake source at all → no Monitor to arm, hook stays silent.

    Phase-3 fix: the OR condition (loops OR persistent service) replaced
    the loops-only check. Prop_72polrcugn fix added a third signal
    (listener_active_*.json on disk). When ALL THREE are absent, the
    hook correctly emits nothing — there's nothing to wake the session
    for. Pin Path.home() to a clean tmp_path so the prior-intent check
    can't see real listener_active_* files leftover on the runner box.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    result = _run_hook(monkeypatch, instance_id="cortex", active_loops=[])
    assert result["rc"] == 0
    assert result["parsed"] == {}


def test_hook_arms_tail_monitor_when_persistent_service_running_no_loops(monkeypatch):
    """The exact empirica-AI deafness scenario (David, 2026-05-24):

    Persistent OS service is up + writing events to loop_fires.log, but
    no canonical loops are registered, AND the in-session Claude is deaf
    because no Monitor was armed. Pre-Phase-3, the hook short-circuited
    on `not loops` and emitted nothing. Post-Phase-3, it MUST emit the
    tail-Monitor block so the session can read its own log.
    """
    # Persistent service IS running for this ai_id (mocked)
    import importlib.util as _ilu

    hook_path = (
        Path(__file__).parent.parent / "empirica/plugins/claude-code-integration/hooks" / "session-monitor-arm.py"
    )
    spec = _ilu.spec_from_file_location("session_monitor_arm_hook", hook_path)
    assert spec and spec.loader
    mod = _ilu.module_from_spec(spec)
    sys.modules["session_monitor_arm_hook"] = mod
    spec.loader.exec_module(mod)

    monkeypatch.setattr(mod.InstanceResolver, "instance_id", classmethod(lambda cls: "empirica"))
    monkeypatch.setattr(mod.InstanceResolver, "ai_id", classmethod(lambda cls, *a, **k: "empirica"))
    monkeypatch.setattr(mod, "list_active_loops_for_instance", lambda iid: [])  # No canonical loops
    monkeypatch.setattr(mod, "is_listener_running", lambda iid: True)  # But persistent service IS up

    # CLI subprocess returns tail-session payload
    tail_payload = json.dumps(
        {
            "ok": True,
            "status": "persistent_service_tail_session",
            "next_step": {
                "tool": "Monitor",
                "args": {
                    "description": "Cortex orchestration log tail for empirica (persistent-service mode)",
                    "command": 'tail -F -n 0 ~/.empirica/loop_fires.log 2>/dev/null | grep --line-buffered \'"instance_id": "empirica"\'',
                    "persistent": True,
                    "timeout_ms": 3600000,
                },
                "after_arm": "empirica listener arm <monitor_task_id> --name empirica-inbox",
            },
        }
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kw: _fake_run(stdout=tail_payload, returncode=0))

    from io import StringIO

    captured = StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    rc = mod.main()
    output = captured.getvalue().strip()
    parsed = json.loads(output) if output else {}

    assert rc == 0
    ctx = parsed["hookSpecificOutput"]["additionalContext"]
    # The fix: Monitor block IS emitted even with no canonical loops
    assert "Monitor(" in ctx
    assert "tail -F" in ctx
    assert "loop_fires.log" in ctx
    # Wake-source explainer adapts to service-only case
    assert "no canonical loops registered" in ctx
    # No reaction table when no loops — but mesh skills still required
    assert "Active loops + their body skills" not in ctx
    assert "/cortex-mailbox-poll" in ctx
    assert "/cortex-mailbox-send" in ctx


def test_hook_emits_additional_context_with_active_loops(monkeypatch):
    """Post-T8 (2026-05-15): hook now arms Monitor on `empirica loop listen`
    — the push-primary ntfy listener — replacing the earlier tail-F-grep
    approach. Listener handles per-instance filtering internally via the
    --instance flag."""
    result = _run_hook(
        monkeypatch,
        instance_id="cortex",
        active_loops=["cortex-mailbox-poll"],
    )
    assert result["rc"] == 0
    parsed = result["parsed"]
    assert parsed.get("hookSpecificOutput", {}).get("hookEventName") == "SessionStart"
    ctx = parsed["hookSpecificOutput"]["additionalContext"]
    # Must instruct AI to arm Monitor on the listener for THIS instance
    assert "Monitor(" in ctx
    assert "persistent=True" in ctx
    assert "empirica loop listen --instance cortex" in ctx
    assert "ECO" in ctx  # the ECO-gated autonomy property must be surfaced


def test_hook_reaction_table_maps_loop_to_body_skill(monkeypatch):
    result = _run_hook(
        monkeypatch,
        instance_id="empirica",
        active_loops=["cortex-mailbox-poll"],
    )
    ctx = result["parsed"]["hookSpecificOutput"]["additionalContext"]
    # canonical_loops.py maps cortex-mailbox-poll → body_skill cortex-mailbox-poll
    assert "`cortex-mailbox-poll`" in ctx
    assert "`/cortex-mailbox-poll`" in ctx


def test_hook_handles_unknown_loop_name_gracefully(monkeypatch):
    """Loop not in canonical_loops → body_skill falls back to loop name."""
    result = _run_hook(
        monkeypatch,
        instance_id="custom",
        active_loops=["some-project-specific-loop"],
    )
    ctx = result["parsed"]["hookSpecificOutput"]["additionalContext"]
    assert "some-project-specific-loop" in ctx
    # Falls back to using loop name as skill name
    assert "`/some-project-specific-loop`" in ctx


# ── Phase 2: CLI delegation (prop_oxrhoehv4) ────────────────────────────


def _load_hook_module():
    import importlib.util as _ilu

    hook_path = Path(__file__).resolve().parents[1] / (
        "empirica/plugins/claude-code-integration/hooks/session-monitor-arm.py"
    )
    spec = _ilu.spec_from_file_location("session_monitor_arm_hook", hook_path)
    assert spec and spec.loader
    mod = _ilu.module_from_spec(spec)
    sys.modules["session_monitor_arm_hook"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_query_listener_on_returns_parsed_json_on_success():
    mod = _load_hook_module()
    fake_payload = {"ok": True, "status": "awaiting_arm", "next_step": {"tool": "Monitor"}}
    with patch.object(
        subprocess,
        "run",
        return_value=_fake_run(
            stdout=json.dumps(fake_payload),
            returncode=0,
        ),
    ):
        result = mod._query_listener_on("cortex")
    assert result == fake_payload


def test_query_listener_on_returns_none_on_nonzero_exit():
    mod = _load_hook_module()
    with patch.object(
        subprocess,
        "run",
        return_value=_fake_run(
            stdout="",
            returncode=1,
        ),
    ):
        result = mod._query_listener_on("cortex")
    assert result is None


def test_query_listener_on_returns_none_on_empty_stdout():
    mod = _load_hook_module()
    with patch.object(
        subprocess,
        "run",
        return_value=_fake_run(
            stdout="",
            returncode=0,
        ),
    ):
        result = mod._query_listener_on("cortex")
    assert result is None


def test_query_listener_on_returns_none_on_malformed_json():
    mod = _load_hook_module()
    with patch.object(
        subprocess,
        "run",
        return_value=_fake_run(
            stdout="not json",
            returncode=0,
        ),
    ):
        result = mod._query_listener_on("cortex")
    assert result is None


def test_query_listener_on_returns_none_when_cli_missing():
    """FileNotFoundError when `empirica` binary isn't on PATH."""
    mod = _load_hook_module()
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        result = mod._query_listener_on("cortex")
    assert result is None


def test_build_monitor_block_persistent_service_tail_session():
    """Phase-3 wake-delivery fix: persistent_service_tail_session means the
    persistent service is running AND we arm a tail-Monitor on its log so
    wakes still reach this session (without duplicating ntfy curl)."""
    mod = _load_hook_module()
    payload = {
        "ok": True,
        "status": "persistent_service_tail_session",
        "next_step": {
            "tool": "Monitor",
            "args": {
                "description": "Cortex orchestration log tail for myai (persistent-service mode)",
                "command": 'tail -F -n 0 /home/me/.empirica/loop_fires.log 2>/dev/null | grep --line-buffered \'"instance_id": "myai"\'',
                "persistent": True,
                "timeout_ms": 3600000,
            },
            "after_arm": "empirica listener arm <monitor_task_id> --name myai-inbox",
        },
    }
    block = mod._build_monitor_block_from_cli(payload, "myai")
    # The Monitor block IS emitted now — Phase-3 gap closure
    assert "Monitor(" in block
    assert "tail -F" in block
    assert "loop_fires.log" in block
    # Mode-specific explainer tells the reader WHY this shape
    assert "persistent OS listener service is already running" in block
    assert "LOG-TAIL" in block
    assert "WITHOUT spawning a duplicate" in block


def test_build_monitor_block_uses_cli_command_when_awaiting_arm():
    mod = _load_hook_module()
    payload = {
        "ok": True,
        "status": "awaiting_arm",
        "next_step": {
            "tool": "Monitor",
            "args": {
                "description": "Custom desc for cortex",
                "command": "empirica loop listen --instance cortex",
                "persistent": True,
                "timeout_ms": 3600000,
            },
            "after_arm": "empirica listener arm <monitor_task_id> --name cortex-inbox",
        },
    }
    block = mod._build_monitor_block_from_cli(payload, "cortex")
    assert "Monitor(" in block
    assert "Custom desc for cortex" in block
    assert "empirica loop listen --instance cortex" in block
    assert "empirica listener arm" in block


def test_build_monitor_block_falls_back_when_cli_unavailable():
    """payload=None → fallback to canonical default command (preserves
    pre-Phase-2 behavior when the CLI is unavailable)."""
    mod = _load_hook_module()
    block = mod._build_monitor_block_from_cli(None, "myai")
    assert "Monitor(" in block
    assert "empirica loop listen --instance myai" in block


def test_hook_emits_tail_monitor_block_when_cli_reports_persistent(monkeypatch):
    """End-to-end: hook subprocesses listener on, gets persistent_service_tail_session,
    renders a tail-Monitor block (Phase-3 in-session wake-delivery fix).

    Before the fix, the hook said 'no Monitor needed' and the session was
    deaf despite the persistent service writing events to loop_fires.log."""
    persistent_payload = json.dumps(
        {
            "ok": True,
            "status": "persistent_service_tail_session",
            "next_step": {
                "tool": "Monitor",
                "args": {
                    "description": "Cortex orchestration log tail for cortex (persistent-service mode)",
                    "command": 'tail -F -n 0 /tmp/.empirica/loop_fires.log 2>/dev/null | grep --line-buffered \'"instance_id": "cortex"\'',
                    "persistent": True,
                    "timeout_ms": 3600000,
                },
                "after_arm": "empirica listener arm <monitor_task_id> --name cortex-inbox",
            },
        }
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kw: _fake_run(stdout=persistent_payload, returncode=0))
    result = _run_hook(
        monkeypatch,
        instance_id="cortex",
        active_loops=["cortex-mailbox-poll"],
    )
    ctx = result["parsed"]["hookSpecificOutput"]["additionalContext"]
    # Monitor block IS emitted — the Phase-3 wake-delivery gap is closed
    assert "Monitor(" in ctx
    assert "tail -F" in ctx
    assert "loop_fires.log" in ctx
    assert "LOG-TAIL" in ctx


# ── Prior-intent wake source (prop_72polrcugnbwxmpl3dxxvio6rq fix) ─────


def test_has_active_listener_intent_finds_file(tmp_path, monkeypatch):
    """Helper returns True when a listener_active_<instance>_*.json exists."""
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    d = tmp_path / ".empirica"
    d.mkdir()
    (d / "listener_active_empirica-extension_empirica-extension-inbox.json").write_text("{}")
    assert mod._has_active_listener_intent("empirica-extension") is True


def test_has_active_listener_intent_returns_false_for_other_instance(tmp_path, monkeypatch):
    """File for a DIFFERENT instance doesn't count — strict prefix match."""
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    d = tmp_path / ".empirica"
    d.mkdir()
    (d / "listener_active_other-instance_other-inbox.json").write_text("{}")
    assert mod._has_active_listener_intent("empirica-extension") is False


def test_has_active_listener_intent_returns_false_when_no_empirica_dir(tmp_path, monkeypatch):
    """Fresh install / no ~/.empirica/ at all → False, no exception."""
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # Don't create the .empirica dir
    assert mod._has_active_listener_intent("anything") is False


def test_hook_emits_arm_block_when_prior_intent_exists(monkeypatch, tmp_path):
    """The fix per prop_72polrcugnbwxmpl3dxxvio6rq: when a previous CC
    session armed a listener for this instance, the durable
    listener_active_*.json file is on disk. Monitor died with that
    session, persistent service isn't running, no canonical loops
    registered. Pre-fix: hook bailed → user deaf after restart. Post-fix:
    the file is a third wake-source signal → hook emits arm block."""
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    d = tmp_path / ".empirica"
    d.mkdir()
    (d / "listener_active_empirica-extension_empirica-extension-inbox.json").write_text(
        '{"monitor_task_id": null, "armed_at": 1234567890.0}'
    )
    monkeypatch.setattr(mod.InstanceResolver, "instance_id", classmethod(lambda cls: "empirica-extension"))
    monkeypatch.setattr(mod.InstanceResolver, "ai_id", classmethod(lambda cls, *a, **k: "empirica-extension"))
    monkeypatch.setattr(mod, "list_active_loops_for_instance", lambda iid: [])
    monkeypatch.setattr(mod, "is_listener_running", lambda iid: False)
    # _query_listener_on returns None (CLI unavailable) → fallback rendering
    monkeypatch.setattr(mod, "_query_listener_on", lambda iid: None)

    from io import StringIO

    captured = StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    rc = mod.main()
    assert rc == 0
    output = captured.getvalue().strip()
    parsed = json.loads(output) if output else {}
    # Hook must NOT bail — output must carry the arm block
    assert parsed != {}, "hook bailed despite prior-armed listener on disk"
    ctx = parsed["hookSpecificOutput"]["additionalContext"]
    assert "Monitor(" in ctx
    # Wake-source language must reference the prior-armed evidence
    assert "prior-armed" in ctx or "listener_active_" in ctx


def test_arm_block_carries_a_deadline_not_the_removed_persistent_flag(monkeypatch, tmp_path):
    """Claude Code 2.1.271 replaced the no-timeout `persistent` option with a
    deadline of at most 30 min plus a re-arm notification. `persistent: true`
    is still ACCEPTED and silently ignored (probed 2026-09-20: a never-exiting
    tail armed with it expired after its timeout anyway), so an instruction
    that renders it promises a watch nobody gets."""
    mod = _load_hook_module()
    block = mod._build_monitor_block_from_cli(None, "empirica")
    call = block[block.index("Monitor(") : block.index(")\n```")]
    assert "persistent" not in call, f"arm call still passes the removed flag: {call}"
    assert f"timeout_ms={mod.MONITOR_MAX_TIMEOUT_MS}" in block
    assert mod.MONITOR_MAX_TIMEOUT_MS == 1_800_000
    assert "re-arm" in block.lower()


def test_hook_still_bails_when_no_signals_at_all(monkeypatch, tmp_path):
    """Sanity check: a fresh instance with no loops, no service, no prior
    intent must still bail. The fix doesn't break the legitimate empty-
    wake-source case."""
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # No ~/.empirica/ directory at all
    monkeypatch.setattr(mod.InstanceResolver, "instance_id", classmethod(lambda cls: "brand-new-instance"))
    monkeypatch.setattr(mod.InstanceResolver, "ai_id", classmethod(lambda cls, *a, **k: "brand-new-instance"))
    monkeypatch.setattr(mod, "list_active_loops_for_instance", lambda iid: [])
    monkeypatch.setattr(mod, "is_listener_running", lambda iid: False)

    from io import StringIO

    captured = StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    rc = mod.main()
    assert rc == 0
    output = captured.getvalue().strip()
    parsed = json.loads(output) if output else {}
    assert parsed == {}, "hook should bail when ALL three signals are false"


def test_hook_requires_both_mesh_skills_when_listener_armed(monkeypatch):
    """When the hook tells the AI to arm a listener Monitor, it MUST also
    require both mesh skills be loaded before first transaction. Sending-side
    handshake (cortex_complete_proposal) lives in /cortex-mailbox-send, and
    without it the AI processes inbox work but never acks back, leaving the
    source AI's outbox visibly stalled. Loading at event-arrival time is too
    late — load both up front. David, 2026-05-17."""
    result = _run_hook(
        monkeypatch,
        instance_id="empirica",
        active_loops=["cortex-mailbox-poll"],
    )
    ctx = result["parsed"]["hookSpecificOutput"]["additionalContext"]
    # The REQUIRED section + both skills must appear when a listener is armed
    assert "REQUIRED" in ctx
    assert "/cortex-mailbox-poll" in ctx
    assert "/cortex-mailbox-send" in ctx
    # The "before first transaction" framing must be present — that's the
    # precondition phrasing (vs the soft "if needed, load X" pattern that
    # gets routinely missed)
    assert "before your first" in ctx.lower() or "before first" in ctx.lower()
