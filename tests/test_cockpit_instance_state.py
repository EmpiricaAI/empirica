"""Tests for empirica.core.cockpit.instance_state.

Builds synthetic state-file layouts under a tmp ~/.empirica/ and a tmp
project dir, then verifies discovery + aggregation produce the expected
shape.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from empirica.core.cockpit import instance_state as ist
from empirica.core.cockpit import liveness as lv
from empirica.core.cockpit import loop_registry as lr
from empirica.core.cockpit import sentinel_pause as sp


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Create a tmp ~/.empirica/ + tmp project dir, redirect all modules."""
    fake_home = tmp_path / ".empirica"
    fake_home.mkdir(parents=True)
    (fake_home / "instance_projects").mkdir()

    project = tmp_path / "project"
    (project / ".empirica").mkdir(parents=True)

    monkeypatch.setattr(ist, "EMPIRICA_DIR", fake_home)
    monkeypatch.setattr(lr, "EMPIRICA_DIR", fake_home)
    monkeypatch.setattr(sp, "EMPIRICA_DIR", fake_home)
    monkeypatch.setattr(sp, "GLOBAL_PAUSE_FILE", fake_home / "sentinel_paused")
    # liveness._read_captured_pids reads EMPIRICA_DIR/instance_projects/<id>.json
    # for pid/ppid lookup. Without patching lv.EMPIRICA_DIR, the test reads the
    # real ~/.empirica/ — when an old tmux_5 pid file exists there with a now-
    # dead pid, is_alive returns DEAD and aggregate state flips to 'no-claude'.
    # This made test_aggregate_phase_noetic flaky on hosts with leftover state.
    monkeypatch.setattr(lv, "EMPIRICA_DIR", fake_home)
    # Isolate from the host tmux server — discovery now scans live panes
    # to surface pre-empirica Claude sessions, but tests should see only
    # what they wrote. Patch BOTH instance_state (used in discovery) and
    # liveness (used inside is_alive) so synthetic instances aren't probed
    # against the host's real tmux server.
    monkeypatch.setattr(ist, "_live_tmux_panes", lambda: None)
    monkeypatch.setattr(lv, "_live_tmux_panes", lambda: None)
    monkeypatch.setattr(lv, "_all_tmux_panes", lambda: None)
    # Recent activity makes the synthetic transaction look 'active' once
    # the tmux signal is silenced.
    return fake_home, project


def _bind_instance(home: Path, project: Path, instance_id: str) -> None:
    (home / "instance_projects" / f"{instance_id}.json").write_text(json.dumps({"project_path": str(project)}))


def _write_transaction(project: Path, instance_id: str, status: str = "open", praxic_calls: int = 0) -> None:
    suffix = f"_{instance_id}"
    tx = {
        "transaction_id": "tx-1234-5678",
        "session_id": "sess-aaaa-bbbb",
        "preflight_timestamp": time.time() - 60,
        "status": status,
        "project_path": str(project),
        "updated_at": time.time(),
        "work_type": "code",
    }
    (project / ".empirica" / f"active_transaction{suffix}.json").write_text(json.dumps(tx))
    if praxic_calls > 0:
        counters = {"praxic_tool_calls": praxic_calls, "noetic_tool_calls": 1}
        (project / ".empirica" / f"hook_counters{suffix}.json").write_text(json.dumps(counters))


def test_discover_empty_returns_empty(env):
    assert ist.discover_instances() == []


def test_discover_via_instance_projects(env):
    home, project = env
    _bind_instance(home, project, "tmux_5")
    _bind_instance(home, project, "tmux_7")
    assert ist.discover_instances() == ["tmux_5", "tmux_7"]


def test_discover_via_pause_file(env):
    home, _ = env
    (home / "sentinel_paused_term_x86").write_text("")
    assert "term_x86" in ist.discover_instances()


def test_discover_excludes_global_pause_file(env):
    home, _ = env
    (home / "sentinel_paused").write_text("")
    assert ist.discover_instances() == []


def test_discover_excludes_loop_pause_sidecars(env):
    home, _ = env
    (home / "loop_paused_tmux_5_some-loop").write_text("")
    # Should ideally not pollute discovery with the loop name as instance_id
    discovered = ist.discover_instances()
    # The implementation skips loop_paused_ via LOOP_PAUSE_PATTERN
    assert "tmux_5_some-loop" not in discovered


def test_aggregate_phase_noetic(env):
    home, project = env
    _bind_instance(home, project, "tmux_5")
    _write_transaction(project, "tmux_5", status="open", praxic_calls=0)
    state = ist.aggregate_instance_state("tmux_5")
    assert state["phase"] == "noetic"
    assert state["transaction"]["id"] == "tx-1234-5678"
    assert state["state"] == "active"
    assert state["project_path"] == str(project)


def test_aggregate_phase_praxic(env):
    home, project = env
    _bind_instance(home, project, "tmux_5")
    _write_transaction(project, "tmux_5", status="open", praxic_calls=3)
    state = ist.aggregate_instance_state("tmux_5")
    assert state["phase"] == "praxic"


def test_aggregate_phase_closed(env):
    home, project = env
    _bind_instance(home, project, "tmux_5")
    _write_transaction(project, "tmux_5", status="closed", praxic_calls=5)
    state = ist.aggregate_instance_state("tmux_5")
    assert state["phase"] == "closed"


def test_aggregate_no_transaction(env):
    home, project = env
    _bind_instance(home, project, "tmux_5")
    state = ist.aggregate_instance_state("tmux_5")
    assert state["phase"] == "no-transaction"
    assert state["transaction"] is None


def test_aggregate_includes_sentinel_pause(env):
    home, project = env
    _bind_instance(home, project, "tmux_5")
    sp.pause_sentinel("tmux_5", reason="maintenance")
    state = ist.aggregate_instance_state("tmux_5")
    assert state["sentinel"]["paused"] is True
    assert state["sentinel"]["scope"] == "instance"
    assert state["sentinel"]["reason"] == "maintenance"


def test_aggregate_includes_loops_with_pause_state(env):
    home, project = env
    _bind_instance(home, project, "tmux_5")
    reg = lr.LoopRegistry("tmux_5")
    reg.register(name="poll-a", kind="cron", cron="*/5 * * * *")
    reg.register(name="poll-b", kind="monitor")
    lr.set_loop_paused("tmux_5", "poll-b", True)

    state = ist.aggregate_instance_state("tmux_5")
    assert set(state["loops"].keys()) == {"poll-a", "poll-b"}
    assert state["loops"]["poll-a"]["paused"] is False
    assert state["loops"]["poll-b"]["paused"] is True


def test_aggregate_all_summary_counts(env):
    home, project = env
    _bind_instance(home, project, "tmux_5")
    _write_transaction(project, "tmux_5", status="open")
    _bind_instance(home, project, "tmux_7")
    _write_transaction(project, "tmux_7", status="closed")
    reg = lr.LoopRegistry("tmux_5")
    reg.register(name="poll", kind="monitor")

    # include_dead=True so synthetic instances aren't filtered by liveness.
    payload = ist.aggregate_all(include_dead=True)
    assert payload["summary"]["instances"] == 2
    assert payload["summary"]["loops_registered"] == 1
    assert payload["summary"]["loops_paused"] == 0
    assert payload["summary"]["active_tx"] == 1


def test_state_symbol_no_claude_when_abandoned(env):
    home, _ = env
    # Old pause file with no transaction → looks abandoned
    old_file = home / "sentinel_paused_old-instance"
    old_file.write_text("")
    import os

    very_old = time.time() - (40 * 24 * 60 * 60)  # 40 days ago
    os.utime(old_file, (very_old, very_old))
    state = ist.aggregate_instance_state("old-instance")
    assert state["state"] == "no-claude"


def test_instance_label_falls_back_to_project_basename(env):
    """No manual label + bound to project → label is project basename
    (matches what statusline shows)."""
    home, project = env
    _bind_instance(home, project, "tmux_5")
    state = ist.aggregate_instance_state("tmux_5")
    assert state["label"] == project.name  # 'project' (basename of tmp_path/project)


def test_instance_label_falls_back_to_id_when_no_project(env):
    """No project binding + no manual label → fall through to instance_id."""
    state = ist.aggregate_instance_state("tmux_5")
    assert state["label"] == "tmux_5"


def test_instance_label_read_from_file(env):
    home, project = env
    _bind_instance(home, project, "tmux_5")
    (home / "instance_label_tmux_5").write_text("outreach\nignored\n")
    state = ist.aggregate_instance_state("tmux_5")
    assert state["label"] == "outreach"


def test_instance_label_manual_overrides_project_basename(env):
    """Manual label > project basename — explicit user override wins."""
    home, project = env
    _bind_instance(home, project, "tmux_5")
    (home / "instance_label_tmux_5").write_text("custom-name\n")
    state = ist.aggregate_instance_state("tmux_5")
    assert state["label"] == "custom-name"


# ─── Liveness-driven state symbol (Philipp's GitHub feedback) ─────────


def test_state_idle_when_alive_with_closed_transaction(env, monkeypatch):
    """Claude is running in pane %5 but its last transaction is closed.
    Old logic returned 'closed' (⊘ — looks dead). New logic returns
    'idle' (🟡 — alive, between tasks)."""
    home, project = env
    _bind_instance(home, project, "tmux_5")
    _write_transaction(project, "tmux_5", status="closed", praxic_calls=2)
    monkeypatch.setattr(ist, "_live_tmux_panes", lambda: {"5"})
    state = ist.aggregate_instance_state("tmux_5", live_panes={"5"})
    assert state["alive"] is True
    assert state["state"] == "idle"
    assert state["phase"] == "closed"  # phase column still carries the info


def test_state_idle_when_alive_with_no_transaction(env, monkeypatch):
    """Pre-empirica Claude session — alive in tmux, no state file written.
    Should show 'idle' not 'no-claude'."""
    monkeypatch.setattr(ist, "_live_tmux_panes", lambda: {"9"})
    state = ist.aggregate_instance_state("tmux_9", live_panes={"9"})
    assert state["alive"] is True
    assert state["state"] == "idle"


def test_state_closed_when_dead_with_closed_transaction(env, monkeypatch):
    """Cleanly closed dead instance — preserve ⊘ symbol for diagnostic
    --include-dead view (distinct from ⊗ no-claude / abandoned)."""
    import os

    home, project = env
    _bind_instance(home, project, "tmux_5")
    _write_transaction(project, "tmux_5", status="closed", praxic_calls=0)
    # Age the transaction file past the recent-activity fallback window
    # (1h) so liveness can't claim alive on activity alone.
    stale = time.time() - (2 * 60 * 60)
    tx_file = project / ".empirica" / "active_transaction_tmux_5.json"
    os.utime(tx_file, (stale, stale))
    monkeypatch.setattr(ist, "_live_tmux_panes", lambda: set())
    state = ist.aggregate_instance_state("tmux_5", live_panes=set())
    assert state["alive"] is False
    assert state["state"] == "closed"


def test_discover_includes_tmux_panes_without_state_files(env, monkeypatch):
    """Philipp's case: pane running claude but no instance_projects file
    (session predates empirica install). Cockpit must still surface it."""
    monkeypatch.setattr(ist, "_live_tmux_panes", lambda: {"4", "11"})
    discovered = ist.discover_instances()
    assert "tmux_4" in discovered
    assert "tmux_11" in discovered


def test_discover_unions_state_files_and_tmux_panes(env, monkeypatch):
    """When some instances have state files and others are tmux-only,
    discovery returns the union (deduped, sorted)."""
    home, project = env
    _bind_instance(home, project, "tmux_5")
    _bind_instance(home, project, "term-pts-7")
    monkeypatch.setattr(ist, "_live_tmux_panes", lambda: {"5", "8"})
    discovered = ist.discover_instances()
    assert discovered == ["term-pts-7", "tmux_5", "tmux_8"]


def test_discover_when_tmux_unavailable(env, monkeypatch):
    """When tmux query fails (returns None), only state-file discovery runs."""
    home, project = env
    _bind_instance(home, project, "tmux_5")
    monkeypatch.setattr(ist, "_live_tmux_panes", lambda: None)
    assert ist.discover_instances() == ["tmux_5"]


# --- _dedup_process_scan_overcount -----------------------------------------


def _scan_inst(project_path, last_activity, signal="process_cwd", alive=True):
    """Minimal instance dict for dedup tests."""
    return {
        "project_path": project_path,
        "last_activity_seconds": last_activity,
        "alive": alive,
        "liveness_signal": signal,
        "liveness_reason": signal,
    }


def test_dedup_caps_process_scan_at_live_proc_count(tmp_path):
    """3 stale instance files for a project but only 1 live claude proc →
    keep the most-recently-active, demote the other 2."""
    proj = str((tmp_path / "p").resolve())
    instances = [
        _scan_inst(proj, 30.0),
        _scan_inst(proj, 10.0),  # most recent
        _scan_inst(proj, 20.0),
    ]
    ist._dedup_process_scan_overcount(instances, {proj: 1})
    alive = [i for i in instances if i["alive"]]
    assert len(alive) == 1
    assert alive[0]["last_activity_seconds"] == 10.0
    demoted = [i for i in instances if not i["alive"]]
    assert all("duplicate stale session" in str(d["liveness_reason"]) for d in demoted)


def test_dedup_strong_signal_consumes_budget(tmp_path):
    """A pid-alive instance consumes the only live-proc slot → the
    process_cwd sibling for the same project is demoted."""
    proj = str((tmp_path / "p").resolve())
    instances = [
        _scan_inst(proj, 5.0, signal="pid"),
        _scan_inst(proj, 10.0, signal="process_cwd"),
    ]
    ist._dedup_process_scan_overcount(instances, {proj: 1})
    assert instances[0]["alive"] is True  # pid untouched
    assert instances[1]["alive"] is False  # process_cwd demoted


def test_dedup_keeps_within_budget(tmp_path):
    """2 live procs, 2 process_scan instances → both kept."""
    proj = str((tmp_path / "p").resolve())
    instances = [_scan_inst(proj, 5.0), _scan_inst(proj, 10.0)]
    ist._dedup_process_scan_overcount(instances, {proj: 2})
    assert all(i["alive"] for i in instances)


def test_dedup_never_demotes_strong_signals(tmp_path):
    """Even with zero scanned procs reported, tmux/pid verdicts stand."""
    proj = str((tmp_path / "p").resolve())
    instances = [_scan_inst(proj, 5.0, signal="tmux"), _scan_inst(proj, 10.0, signal="pid")]
    ist._dedup_process_scan_overcount(instances, {proj: 0})
    assert all(i["alive"] for i in instances)


def test_dedup_env_match_consumes_slot_and_survives(tmp_path):
    """An exact env match (process_env) is strong: it consumes the only live
    slot (never demoted), so the cwd-fallback sibling is demoted."""
    proj = str((tmp_path / "p").resolve())
    instances = [
        _scan_inst(proj, 5.0, signal="process_env"),
        _scan_inst(proj, 10.0, signal="process_cwd"),
    ]
    ist._dedup_process_scan_overcount(instances, {proj: 1})
    assert instances[0]["alive"] is True  # env match untouched
    assert instances[1]["alive"] is False  # cwd fallback demoted


# ─── pass 2: pane-footprint dedup (tmux_N doubles of canonical records) ──────


def test_dedup_pane_footprint_covered_by_canonical(tmp_path, monkeypatch):
    """One session, two footprints: canonical ai_id (process_env) + bare pane
    record (tmux), one live proc → the pane duplicate is demoted, canonical
    stays. This is the doubled-fleet case (34 rows for 20 sessions)."""
    monkeypatch.setattr(ist, "tmux_pane_cwds", lambda: {})
    proj = str((tmp_path / "p").resolve())
    canonical = _scan_inst(proj, 5.0, signal="process_env")
    pane = dict(_scan_inst(proj, 10.0, signal="tmux"), instance_id="tmux_7")
    instances = [canonical, pane]
    ist._dedup_process_scan_overcount(instances, {proj: 1})
    assert canonical["alive"] is True
    assert pane["alive"] is False
    assert "canonical" in pane["liveness_reason"]


def test_dedup_pane_only_session_survives(tmp_path, monkeypatch):
    """A session whose ONLY identity is its pane record (launched without
    EMPIRICA_INSTANCE_ID) keeps its row: no canonical sibling, one live proc."""
    monkeypatch.setattr(ist, "tmux_pane_cwds", lambda: {})
    proj = str((tmp_path / "p").resolve())
    pane = dict(_scan_inst(proj, 5.0, signal="tmux"), instance_id="tmux_30")
    instances = [pane]
    ist._dedup_process_scan_overcount(instances, {proj: 1})
    assert pane["alive"] is True


def test_dedup_pane_record_stands_when_scan_blind(tmp_path, monkeypatch):
    """Scan reports zero procs for the project (blind / cwd mismatch) → never
    demote a tmux verdict on absent evidence, even with a canonical sibling."""
    monkeypatch.setattr(ist, "tmux_pane_cwds", lambda: {})
    proj = str((tmp_path / "p").resolve())
    canonical = _scan_inst(proj, 5.0, signal="process_env")
    pane = dict(_scan_inst(proj, 10.0, signal="tmux"), instance_id="tmux_7")
    instances = [canonical, pane]
    ist._dedup_process_scan_overcount(instances, {proj: 0})
    assert pane["alive"] is True


def test_dedup_unbound_pane_grouped_via_pane_cwd(tmp_path, monkeypatch):
    """A bare tmux_N record with NO project binding is grouped through its
    pane's cwd and demoted when a canonical record covers the same project."""
    proj = str((tmp_path / "p").resolve())
    monkeypatch.setattr(ist, "tmux_pane_cwds", lambda: {"7": proj})
    canonical = _scan_inst(proj, 5.0, signal="process_env")
    pane = dict(_scan_inst(None, 10.0, signal="tmux"), instance_id="tmux_7")
    instances = [canonical, pane]
    ist._dedup_process_scan_overcount(instances, {proj: 1})
    assert canonical["alive"] is True
    assert pane["alive"] is False


# ─── discover_dead_instances reaps superseded fallback ghosts ───────────────


def test_discover_dead_reaps_superseded_fallback_ghost(env, monkeypatch):
    """A canonical env-matched instance + an old fallback ghost (tmux_N) for the
    same project, with one live proc: the ghost is kept 'alive' by the cwd
    signal but dedup demotes it → it lands in the dead list (prune reaps it).
    The canonical instance stays alive and is NOT reaped."""
    home, project = env
    _bind_instance(home, project, "empirica")  # canonical (env id)
    _bind_instance(home, project, "tmux_16")  # superseded fallback ghost
    rp = os.path.realpath(str(project))

    monkeypatch.setattr(
        ist,
        "scan_live_claude",
        lambda: lv.LiveClaudeScan(instance_ids={"empirica"}, cwd_counts={rp: 1}),
    )
    monkeypatch.setattr("empirica.utils.session_resolver.get_instance_id", lambda: None)

    dead = ist.discover_dead_instances()
    assert "tmux_16" in dead  # ghost reaped
    assert "empirica" not in dead  # canonical (process_env) survives


def test_discover_dead_keeps_ghost_when_no_canonical(env, monkeypatch):
    """If a fallback record is the ONLY instance for a project with a live proc,
    it legitimately represents that process (within budget) → not reaped."""
    home, project = env
    _bind_instance(home, project, "tmux_16")
    rp = os.path.realpath(str(project))

    monkeypatch.setattr(
        ist,
        "scan_live_claude",
        lambda: lv.LiveClaudeScan(instance_ids=set(), cwd_counts={rp: 1}),
    )
    monkeypatch.setattr("empirica.utils.session_resolver.get_instance_id", lambda: None)

    dead = ist.discover_dead_instances()
    assert "tmux_16" not in dead  # within budget — kept
