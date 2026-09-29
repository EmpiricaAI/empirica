"""`mesh status` must know whether the curl it found belongs to the listener.

mesh-support and cortex (prop_qo7p77i5wzds5phfkdq637lbbq): `_find_listener_pids` matched any
process with `orchestration-events` and the tag, and never read its parent. So an orphaned curl
left by a dead listener read as a live bridge, and a curl whose listener was being respawned
read as dead. A watchdog that gates a restart on the raw pid inherits both mistakes.

Cortex measured 9 of 9 curls parented on a healthy box: that is the positive control here.
"""

from __future__ import annotations

import json
import types
from datetime import datetime, timezone

import pytest

from empirica.cli.command_handlers import mesh_commands as mc

HEADER = "  PID  PPID COMMAND\n"
URL = "https://cortex.example/v1/orchestration-events/stream?tags=empirica.david.cortex&since=0"


def _ps(*rows: tuple[int, int, str]) -> str:
    return HEADER + "".join(f"{pid:>5} {ppid:>5} {cmd}\n" for pid, ppid, cmd in rows)


LISTENER = (100, 1, "/usr/bin/python3 -m empirica loop listen --instance cortex --foo")


def test_a_curl_that_descends_from_the_listener_is_parented():
    """POSITIVE CONTROL: the healthy shape."""
    procs = mc._find_listener_procs("cortex", _ps(LISTENER, (101, 100, f"curl -sSN {URL}")))

    assert procs == mc.ListenerProcs(100, 101, True)


def test_a_curl_left_by_a_dead_listener_is_an_orphan_not_a_live_bridge():
    """The listener died; init adopted its curl. Same cmdline, wrong owner."""
    procs = mc._find_listener_procs("cortex", _ps((101, 1, f"curl -sSN {URL}")))

    assert procs.curl_pid == 101 and procs.curl_parented is False and procs.listener_pid is None


def test_a_curl_whose_parent_is_some_other_process_is_an_orphan_even_with_a_live_listener():
    procs = mc._find_listener_procs("cortex", _ps(LISTENER, (555, 1, "bash"), (101, 555, f"curl -sSN {URL}")))

    assert procs.listener_pid == 100 and procs.curl_pid == 101 and procs.curl_parented is False


def test_a_wrapper_between_the_listener_and_its_curl_still_counts_as_parented():
    """A supervisor or `sh -c` in the middle must not turn a healthy curl into an orphan."""
    procs = mc._find_listener_procs(
        "cortex", _ps(LISTENER, (102, 100, "sh -c exec curl ..."), (103, 102, f"curl -sSN {URL}"))
    )

    assert procs.curl_pid == 103 and procs.curl_parented is True


def test_a_parented_curl_wins_over_an_orphan_when_both_exist():
    """A respawn in flight: the old curl not yet reaped, the new one already the listener's."""
    procs = mc._find_listener_procs(
        "cortex", _ps(LISTENER, (90, 1, f"curl -sSN {URL}"), (101, 100, f"curl -sSN {URL}"))
    )

    assert procs.curl_pid == 101 and procs.curl_parented is True


def test_no_curl_at_all_is_none_not_false():
    procs = mc._find_listener_procs("cortex", _ps(LISTENER))

    assert procs.curl_pid is None and procs.curl_parented is None and procs.listener_pid == 100


def test_another_practices_curl_is_never_matched():
    """`cortex` must not claim `empirica-cortex`'s curl (the delimiter rule still holds)."""
    other = "https://x/orchestration-events?tags=empirica.david.empirica-cortex"
    procs = mc._find_listener_procs("cortex", _ps(LISTENER, (101, 100, f"curl -sSN {other}")))

    assert procs.curl_pid is None


def test_a_cyclic_or_missing_parent_chain_terminates():
    """ps snapshots race: a parent can be gone, and a hand-built table can loop."""
    looped = _ps((101, 102, f"curl -sSN {URL}"), (102, 101, "sh"))

    assert mc._find_listener_procs("cortex", looped).curl_parented is False


# ── health ──────────────────────────────────────────────────────────────────


def _state(**over):
    base = {
        "ai_id": "cortex",
        "service_installed": True,
        "service_active": True,
        "listener_process_pid": 100,
        "curl_subprocess_pid": 101,
        "curl_parented": True,
        "last_fire_at_utc": None,
        "fire_window_start": None,
        "backoff_state": None,
    }
    return {**base, **over}


def test_an_orphaned_curl_with_no_fires_is_red_and_says_orphaned_not_healthy():
    color, reason = mc._compute_health(_state(curl_parented=False), cortex_configured=True)

    assert color == "red" and "orphaned" in reason and "not the listener's child" in reason


def test_a_parented_curl_with_no_fires_is_unchanged():
    color, reason = mc._compute_health(_state(), cortex_configured=True)

    assert color == "yellow" and "cold start" in reason


def test_recent_fires_stay_authoritative_even_if_the_curl_looks_orphaned():
    """One ps snapshot can catch a respawn in flight; fires flowing means the bridge works."""
    now = datetime.now(tz=timezone.utc)

    color, reason = mc._compute_health(_state(curl_parented=False, last_fire_at_utc=now), cortex_configured=True)

    assert color == "green" and "last fire" in reason


def test_an_orphan_with_silent_fires_is_red_not_zombie_or_quiet():
    old = datetime.fromtimestamp(datetime.now(tz=timezone.utc).timestamp() - 7200, tz=timezone.utc)

    color, reason = mc._compute_health(_state(curl_parented=False, last_fire_at_utc=old), cortex_configured=True)

    assert color == "red" and "orphaned" in reason


def test_no_curl_at_all_still_reads_dead():
    color, reason = mc._compute_health(_state(curl_subprocess_pid=None, curl_parented=None), cortex_configured=True)

    assert color == "red" and "dead" in reason and "orphaned" not in reason


# ── the surfaces ────────────────────────────────────────────────────────────


@pytest.fixture
def wired(monkeypatch, tmp_path):
    monkeypatch.setattr(mc, "LOOP_FIRES_LOG", tmp_path / "loop_fires.log")
    monkeypatch.setattr(
        mc, "listener_status_for", lambda ai_id: types.SimpleNamespace(backend="systemd", installed=True, active=True)
    )
    monkeypatch.setattr(mc, "_load_cortex_credentials", lambda: {"api_key": "x"})
    monkeypatch.setattr(mc, "_enumerate_instances", lambda: ["cortex"])


def _with_table(monkeypatch, *rows):
    monkeypatch.setattr(mc.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout=_ps(*rows)))


def test_status_json_reports_curl_parented_beside_the_pid(wired, monkeypatch, capsys):
    _with_table(monkeypatch, (101, 1, f"curl -sSN {URL}"), LISTENER)

    mc.handle_mesh_status_command(types.SimpleNamespace(instance=None, output="json"))
    row = json.loads(capsys.readouterr().out)["instances"][0]

    assert row["curl_subprocess_pid"] == 101 and row["curl_parented"] is False


def test_status_table_says_orphan_for_an_unparented_curl_and_ok_for_a_parented_one(wired, monkeypatch, capsys):
    _with_table(monkeypatch, LISTENER, (101, 1, f"curl -sSN {URL}"))
    mc.handle_mesh_status_command(types.SimpleNamespace(instance=None, output="human"))
    assert "orphan" in capsys.readouterr().out

    _with_table(monkeypatch, LISTENER, (101, 100, f"curl -sSN {URL}"))
    mc.handle_mesh_status_command(types.SimpleNamespace(instance=None, output="human"))
    out = capsys.readouterr().out
    assert " ok " in out and "orphan" not in out


def test_diagnose_json_carries_curl_parented_too(capsys):
    state = mc.MeshInstanceState(
        ai_id="cortex",
        backend="systemd",
        service_installed=True,
        service_active=True,
        listener_process_pid=100,
        curl_subprocess_pid=101,
        last_fire_at_utc=None,
        fires_last_hour=0,
        cortex_configured=True,
        loops_registered=0,
        backoff_state=None,
        health_color="red",
        health_reason="x",
        curl_parented=False,
    )

    mc._emit_diagnose_json("cortex", state, None)
    local = json.loads(capsys.readouterr().out)["local"]

    assert local["curl_subprocess_pid"] == 101 and local["curl_parented"] is False


# ── the listener match is a boundary match (broccoli) ───────────────────────


def test_a_listener_for_a_longer_instance_name_is_not_this_practices_listener():
    """`--instance empirica` is a prefix of `--instance empirica-workspace`. Measured on the
    box: `empirica` was reported with the workspace listener's pid."""
    other = (200, 1, "/usr/bin/python3 -m empirica loop listen --instance empirica-workspace --foo")
    procs = mc._find_listener_procs("empirica", _ps(other))

    assert procs.listener_pid is None


def test_the_exact_listener_is_still_found_beside_a_longer_named_one():
    """Positive control for the test above, and the end-of-command case (no trailing argument)."""
    other = (200, 1, "/usr/bin/python3 -m empirica loop listen --instance empirica-workspace")
    mine = (300, 1, "/usr/bin/python3 -m empirica loop listen --instance empirica")

    assert mc._find_listener_procs("empirica", _ps(other, mine)).listener_pid == 300
    assert mc._find_listener_procs("empirica-workspace", _ps(other, mine)).listener_pid == 200
