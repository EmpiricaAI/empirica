"""`mesh status` must not call a quiet practice a cold start.

cortex (prop_lc6bvbcjgbe5plomjtin62cvdq, 2026-09-27): loop_fires.log rotated on
2026-09-20, so a practice with no fire since then fell through to "no fires recorded
yet (cold start ok if recent install)". empirica-web had 62 fires in loop_fires.log.1 and
listener activity since July. A partial read presented as absence.

Rotation is a single rename to `<log>.1` (listener._rotate_fires_log_if_oversized), so
"nothing found" can only ever mean "nothing in the retained window", and the message
must say which window was read.
"""

from __future__ import annotations

import json
import types
from datetime import datetime, timedelta, timezone

import pytest

from empirica.cli.command_handlers import mesh_commands as mc

NOW = datetime.now(tz=timezone.utc)


def _line(instance: str, when: datetime) -> str:
    return json.dumps({"ts": when.isoformat(), "instance_id": instance, "loop": "cortex-mailbox-poll"}) + "\n"


def _write(path, rows: list[tuple[str, datetime]]) -> None:
    path.write_text("".join(_line(i, t) for i, t in rows))


@pytest.fixture
def logs(tmp_path, monkeypatch):
    log = tmp_path / "loop_fires.log"
    monkeypatch.setattr(mc, "LOOP_FIRES_LOG", log)
    return log


def test_a_fire_only_in_the_rotated_log_is_found_and_attributed(logs):
    _write(logs, [("someone-else", NOW - timedelta(days=1))])
    rotated = logs.with_name("loop_fires.log.1")
    last = NOW - timedelta(days=12)
    _write(rotated, [("empirica-web", last - timedelta(hours=1)), ("empirica-web", last)])

    scan = mc._scan_fires("empirica-web")

    assert scan.last_ts == last
    assert scan.source == "loop_fires.log.1"
    assert scan.files_read == 2


def test_the_health_message_for_that_case_is_not_cold_start(logs):
    """The reported bug, end to end through the health function."""
    _write(logs, [("someone-else", NOW)])
    _write(logs.with_name("loop_fires.log.1"), [("empirica-web", NOW - timedelta(days=12))])
    scan = mc._scan_fires("empirica-web")
    state = {
        "ai_id": "empirica-web",
        "service_installed": True,
        "service_active": True,
        "listener_process_pid": 1,
        "curl_subprocess_pid": 2,
        "last_fire_at_utc": scan.last_ts,
        "last_fire_source": scan.source,
        "fire_window_start": scan.window_start,
        "fire_logs_read": scan.files_read,
        "backoff_state": None,
    }

    _, reason = mc._compute_health(state, cortex_configured=True)

    assert "cold start" not in reason
    assert "[read from loop_fires.log.1]" in reason


def test_no_fire_in_either_file_names_the_window_that_was_read(logs):
    """Logs exist and hold nothing for this instance: a statement about a window, not about history.

    Positive control: a different instance DOES have a fire in the rotated file, so the
    scan demonstrably walked it, and an empty result is evidence rather than silence.
    """
    _write(logs, [("someone-else", NOW)])
    start = NOW - timedelta(days=5)
    _write(logs.with_name("loop_fires.log.1"), [("other", start), ("other", start + timedelta(days=1))])
    assert mc._scan_fires("other").last_ts is not None, "positive control: the rotated file is readable and non-empty"

    scan = mc._scan_fires("quiet-practice")

    assert scan.last_ts is None and scan.source is None
    assert scan.files_read == 2
    assert scan.window_start == start
    state = {
        "service_installed": True,
        "service_active": True,
        "listener_process_pid": 1,
        "curl_subprocess_pid": 2,
        "last_fire_at_utc": None,
        "fire_window_start": scan.window_start,
        "fire_logs_read": scan.files_read,
        "ai_id": "quiet-practice",
    }
    color, reason = mc._compute_health(state, cortex_configured=True)
    assert color == "yellow"
    assert f"since {start:%Y-%m-%d}" in reason and "2 log file(s) read" in reason and "cold start" not in reason


def test_with_no_fires_log_at_all_it_really_is_a_cold_start(logs):
    scan = mc._scan_fires("new-practice")

    assert scan.last_ts is None and scan.files_read == 0 and scan.window_start is None
    state = {
        "service_installed": True,
        "service_active": True,
        "listener_process_pid": 1,
        "curl_subprocess_pid": 2,
        "last_fire_at_utc": None,
        "fire_window_start": None,
        "ai_id": "new-practice",
    }
    assert "cold start" in mc._compute_health(state, cortex_configured=True)[1]


def test_the_rotated_file_is_not_read_when_the_current_one_answers(logs):
    _write(logs, [("p", NOW - timedelta(days=2)), ("p", NOW - timedelta(days=1))])
    _write(logs.with_name("loop_fires.log.1"), [("p", NOW - timedelta(days=9))])

    scan = mc._scan_fires("p")

    assert scan.files_read == 1 and scan.source == "loop_fires.log"
    assert scan.last_ts == NOW - timedelta(days=1)


def test_the_hourly_count_continues_into_the_rotated_file_when_rotation_was_recent(logs):
    """The current file began 10 minutes ago, so fires from the last hour are split across both."""
    _write(logs, [("p", NOW - timedelta(minutes=10)), ("p", NOW - timedelta(minutes=2))])
    _write(logs.with_name("loop_fires.log.1"), [("p", NOW - timedelta(minutes=40)), ("p", NOW - timedelta(minutes=20))])

    scan = mc._scan_fires("p")

    assert scan.fires_last_hour == 4 and scan.files_read == 2


def test_numbered_generations_are_read_in_numeric_order(logs):
    """.10 is older than .2, not between .1 and .2."""
    _write(logs, [("someone", NOW)])
    _write(logs.with_name("loop_fires.log.2"), [("p", NOW - timedelta(days=20))])
    _write(logs.with_name("loop_fires.log.10"), [("p", NOW - timedelta(days=90))])

    scan = mc._scan_fires("p")

    assert scan.source == "loop_fires.log.2" and scan.last_ts == NOW - timedelta(days=20)
    assert [p.name for p in mc._fires_log_generations()] == ["loop_fires.log", "loop_fires.log.2", "loop_fires.log.10"]


def test_an_instance_id_that_is_a_prefix_of_another_does_not_borrow_its_fires(logs):
    """`empirica` must not pick up `empirica-web` lines."""
    _write(logs, [("empirica-web", NOW)])

    assert mc._scan_fires("empirica").last_ts is None
    assert mc._scan_fires("empirica-web").last_ts is not None


def test_last_fire_for_keeps_its_old_shape_for_the_restart_baseline(logs):
    """mesh restart compares fires before and after; it must still get (datetime, int)."""
    _write(logs, [("p", NOW - timedelta(minutes=1))])

    last, per_hour = mc._last_fire_for("p")

    assert isinstance(last, datetime) and per_hour == 1


# ── the surfaces ────────────────────────────────────────────────────────────


@pytest.fixture
def fake_listener(monkeypatch):
    monkeypatch.setattr(
        mc, "listener_status_for", lambda ai_id: types.SimpleNamespace(backend="systemd", installed=True, active=True)
    )
    monkeypatch.setattr(mc, "_find_listener_procs", lambda ai_id: mc.ListenerProcs(11, 22, True))  # no real ps
    monkeypatch.setattr(mc, "_load_cortex_credentials", lambda: None)


def test_status_json_reports_the_source_and_the_window(logs, fake_listener, monkeypatch, capsys):
    _write(logs, [("someone", NOW)])
    _write(logs.with_name("loop_fires.log.1"), [("p", NOW - timedelta(days=3))])
    monkeypatch.setattr(mc, "_enumerate_instances", lambda: ["p"])

    mc.handle_mesh_status_command(types.SimpleNamespace(instance=None, output="json"))
    row = json.loads(capsys.readouterr().out)["instances"][0]

    assert row["last_fire_source"] == "loop_fires.log.1"
    assert row["fire_logs_read"] == 2 and isinstance(row["fire_window_start"], str)
    assert isinstance(row["last_fire_at_utc"], str)


def test_status_table_marks_a_fire_read_from_the_rotated_log(logs, fake_listener, monkeypatch, capsys):
    _write(logs, [("someone", NOW)])
    _write(logs.with_name("loop_fires.log.1"), [("p", NOW - timedelta(days=3))])
    monkeypatch.setattr(mc, "_enumerate_instances", lambda: ["p"])

    mc.handle_mesh_status_command(types.SimpleNamespace(instance=None, output="human"))
    out = capsys.readouterr().out

    assert "3d*" in out and "read from a rotated fires log" in out


def test_diagnose_no_longer_says_never_when_it_only_read_a_window(logs, fake_listener, monkeypatch, capsys):
    _write(logs, [("someone", NOW)])
    _write(logs.with_name("loop_fires.log.1"), [("someone", NOW - timedelta(days=4))])

    mc.handle_mesh_diagnose_command(types.SimpleNamespace(instance="quiet", output="human", cortex=False, peer=None))
    out = capsys.readouterr().out

    assert "NEVER" not in out
    assert "none in the retained window" in out and "2 log file(s) read" in out
