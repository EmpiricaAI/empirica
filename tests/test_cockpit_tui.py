"""Tests for the empirica tui (compact Textual cockpit).

Uses textual's headless `App.run_test()`. Layout under test is the v1.4
compact design: 6-col table (stat/name/phase/S/L/N), 4 action buttons
(P sent / L loops / S stop / N notif), no kill button (CLI-only), no
right-detail pane, single statusline + recent strip at the bottom.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

pytest_plugins = ["pytest_asyncio"]


@pytest.fixture
def cockpit_env(tmp_path, monkeypatch):
    fake_home = tmp_path / ".empirica"
    fake_home.mkdir()
    (fake_home / "instance_projects").mkdir()
    (fake_home / "tty_sessions").mkdir()

    project = tmp_path / "project"
    (project / ".empirica").mkdir(parents=True)

    from empirica.core.cockpit import (
        enrichment,
        instance_actions,
        instance_state,
        listener_install_request,
        listener_registry,
        loop_install_request,
        loop_registry,
        loop_uninstall_request,
        sentinel_pause,
    )

    monkeypatch.setattr(sentinel_pause, "EMPIRICA_DIR", fake_home)
    monkeypatch.setattr(sentinel_pause, "GLOBAL_PAUSE_FILE", fake_home / "sentinel_paused")
    monkeypatch.setattr(instance_state, "EMPIRICA_DIR", fake_home)
    monkeypatch.setattr(instance_actions, "EMPIRICA_DIR", fake_home)
    monkeypatch.setattr(instance_actions, "TTY_SESSIONS_DIR", fake_home / "tty_sessions")
    monkeypatch.setattr(loop_registry, "EMPIRICA_DIR", fake_home)
    monkeypatch.setattr(loop_install_request, "EMPIRICA_DIR", fake_home)
    monkeypatch.setattr(loop_uninstall_request, "EMPIRICA_DIR", fake_home)
    monkeypatch.setattr(listener_registry, "EMPIRICA_DIR", fake_home)
    monkeypatch.setattr(listener_install_request, "EMPIRICA_DIR", fake_home)
    monkeypatch.setattr(enrichment, "EMPIRICA_DIR", fake_home)
    monkeypatch.setattr(
        enrichment,
        "ENP_PENDING_PATH",
        fake_home / "enp" / "pending.json",
    )
    # Discovery now scans live tmux panes to surface pre-empirica Claude
    # sessions; isolate tests from the host tmux server so they only see
    # the instances they explicitly bind.
    monkeypatch.setattr(instance_state, "_live_tmux_panes", lambda: None)
    return fake_home, project


def _bind_instance(home: Path, project: Path, instance_id: str) -> None:
    (home / "instance_projects" / f"{instance_id}.json").write_text(json.dumps({"project_path": str(project)}))


# ─── mount + structure ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tui_mounts_compact_widgets(cockpit_env):
    from textual.widgets import Button, DataTable, Static

    from empirica.cli.tui import CockpitApp

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause()
        await pilot.pause()

        assert app.query_one("#inst-table", DataTable) is not None
        assert app.query_one("#summary", Static) is not None
        assert app.query_one("#statusline", Static) is not None
        # v1.6: portrait layout — recent strip replaced with goals + notif
        assert app.query_one("#goals", Static) is not None
        assert app.query_one("#notif", Static) is not None
        for btn_id in ("btn-sent", "btn-events", "btn-stop", "btn-notif"):
            assert app.query_one(f"#{btn_id}", Button) is not None, btn_id


@pytest.mark.asyncio
async def test_tui_has_no_kill_button(cockpit_env):
    """Kill is CLI-only by design; the TUI should not expose it."""
    from textual.css.query import NoMatches

    from empirica.cli.tui import CockpitApp

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause()
        await pilot.pause()
        with pytest.raises(NoMatches):
            app.query_one("#btn-kill")


@pytest.mark.asyncio
async def test_table_has_seven_columns(cockpit_env):
    """T9 (2026-05-15, David): collapsed separate loops + listeners +
    notifications columns into a unified 'N' (events). The listener (T8)
    is the single wake mechanism now — three columns for the same concept
    was noise.

    2026-07-22 (Philipp): a '#' tmux-window column was inserted between 's'
    and 'name' so rows sort by the operator's tmux window layout."""
    from textual.widgets import DataTable

    from empirica.cli.tui import CockpitApp

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(60, 20)) as pilot:
        await pilot.pause()
        await pilot.pause()
        table = app.query_one("#inst-table", DataTable)
        col_labels = [c.label.plain for c in table.columns.values()]
        assert col_labels == ["s", "#", "name", "ph", "dom", "S", "N"]


# ─── data loading ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tui_loads_instances(cockpit_env):
    from empirica.cli.tui import CockpitApp

    home, project = cockpit_env
    _bind_instance(home, project, "tmux_42")
    _bind_instance(home, project, "tmux_99")

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause()
        await pilot.pause()
        ids = sorted(i["instance_id"] for i in app.payload["instances"])
        assert ids == ["tmux_42", "tmux_99"]


# ─── toggle sentinel ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p_toggles_sentinel(cockpit_env):
    """`p` toggles: pauses if not paused, resumes if paused."""
    from empirica.cli.tui import CockpitApp

    home, project = cockpit_env
    _bind_instance(home, project, "tmux_42")

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause()
        await pilot.pause()

        # First press: pauses
        await pilot.press("p")
        await pilot.pause()
        assert (home / "sentinel_paused_tmux_42").exists()

        # Second press: resumes
        await pilot.press("p")
        await pilot.pause()
        assert not (home / "sentinel_paused_tmux_42").exists()


@pytest.mark.asyncio
async def test_btn_sent_toggles_sentinel(cockpit_env):
    from empirica.cli.tui import CockpitApp

    home, project = cockpit_env
    _bind_instance(home, project, "tmux_42")

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.click("#btn-sent")
        await pilot.pause()
        assert (home / "sentinel_paused_tmux_42").exists()


# ─── auto-accept chip always visible ──────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_accept_chip_shows_off_state(cockpit_env, monkeypatch):
    """The auto-accept chip is now ALWAYS visible — loud ⚡AUTO-ACCEPT when
    ON, muted 'auto-accept:off' when OFF. Only hidden when state is None
    (cortex unreachable / endpoint unshipped). Lets users see the bypass
    state without pressing 'a' first."""
    from textual.widgets import Static

    # Force the aggregator to return auto_accept=False (OFF, known state).
    import empirica.cli.tui.cockpit_app as cockpit_module
    from empirica.cli.tui import CockpitApp

    real_aggregate = cockpit_module.aggregate_all

    def stub_aggregate(*args, **kwargs):
        payload = real_aggregate(*args, **kwargs)
        payload.setdefault("summary", {})["auto_accept"] = False
        return payload

    monkeypatch.setattr(cockpit_module, "aggregate_all", stub_aggregate)

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(80, 20)) as pilot:
        await pilot.pause()
        await pilot.pause()
        summary = app.query_one("#summary", Static)
        from rich.console import Console

        c = Console(width=120, file=io.StringIO())
        with c.capture() as cap:
            c.print(summary.render())
        text = cap.get()
        assert "auto-accept:off" in text, f"OFF state should be visible in summary; got: {text!r}"


@pytest.mark.asyncio
async def test_auto_accept_chip_shows_on_state(cockpit_env, monkeypatch):
    """The loud ⚡AUTO-ACCEPT marker remains for the ON state."""
    from textual.widgets import Static

    import empirica.cli.tui.cockpit_app as cockpit_module
    from empirica.cli.tui import CockpitApp

    real_aggregate = cockpit_module.aggregate_all

    def stub_aggregate(*args, **kwargs):
        payload = real_aggregate(*args, **kwargs)
        payload.setdefault("summary", {})["auto_accept"] = True
        return payload

    monkeypatch.setattr(cockpit_module, "aggregate_all", stub_aggregate)

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(80, 20)) as pilot:
        await pilot.pause()
        await pilot.pause()
        summary = app.query_one("#summary", Static)
        from rich.console import Console

        c = Console(width=120, file=io.StringIO())
        with c.capture() as cap:
            c.print(summary.render())
        text = cap.get()
        assert "⚡AUTO-ACCEPT" in text or "AUTO-ACCEPT" in text, f"ON state should be loudly visible; got: {text!r}"


# ─── toggle loops ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_l_pauses_all_loops_when_any_unpaused(cockpit_env):
    from empirica.cli.tui import CockpitApp
    from empirica.core.cockpit import LoopRegistry

    home, project = cockpit_env
    _bind_instance(home, project, "tmux_42")
    # Loops are practice-keyed (ai_id = project basename); the TUI resolves the
    # same ai_id_for_timer and pauses under it.
    key = project.name
    reg = LoopRegistry(key)
    reg.register(name="loop-a", kind="monitor")
    reg.register(name="loop-b", kind="monitor")

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()

        from empirica.core.cockpit import is_loop_paused

        assert is_loop_paused(key, "loop-a")
        assert is_loop_paused(key, "loop-b")


@pytest.mark.asyncio
async def test_l_resumes_all_loops_when_all_paused(cockpit_env):
    from empirica.cli.tui import CockpitApp
    from empirica.core.cockpit import LoopRegistry, is_loop_paused, set_loop_paused

    home, project = cockpit_env
    _bind_instance(home, project, "tmux_42")
    # Practice-keyed loops (ai_id = project basename).
    key = project.name
    reg = LoopRegistry(key)
    reg.register(name="loop-a", kind="monitor")
    reg.register(name="loop-b", kind="monitor")
    set_loop_paused(key, "loop-a", True)
    set_loop_paused(key, "loop-b", True)

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()

        assert not is_loop_paused(key, "loop-a")
        assert not is_loop_paused(key, "loop-b")


# ─── stop ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_calls_stop_instance(cockpit_env, monkeypatch):
    from empirica.cli.tui import CockpitApp
    from empirica.core.cockpit import instance_actions

    captured: list[str] = []
    monkeypatch.setattr(
        "empirica.cli.tui.cockpit_app.stop_instance",
        lambda iid, **k: (captured.append(iid), instance_actions.StopResult(iid, True, "stub", "tmux-send-keys"))[1],
    )

    home, project = cockpit_env
    _bind_instance(home, project, "tmux_42")

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()

        assert captured == ["tmux_42"]


# ─── notifications (placeholder) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_n_clears_notifications(cockpit_env):
    """`n` keybinding marks the project's pending notifications as acked.

    New scope (post per-project refactor): notifications live in
    ~/.empirica/enp/pending.json keyed by repo path. Clear acks the
    entries whose repo matches the selected instance's project_path.
    """
    from empirica.cli.tui import CockpitApp

    home, project = cockpit_env
    _bind_instance(home, project, "tmux_42")
    # Open transaction so the instance has a project_path resolved.
    import time

    suffix = "_tmux_42"
    tx = {
        "transaction_id": "tx-1",
        "session_id": "s-1",
        "preflight_timestamp": time.time(),
        "status": "open",
        "project_path": str(project),
    }
    (project / ".empirica").mkdir(exist_ok=True)
    (project / ".empirica" / f"active_transaction{suffix}.json").write_text(json.dumps(tx))

    enp_dir = home / "enp"
    enp_dir.mkdir()
    pending_path = enp_dir / "pending.json"
    pending_path.write_text(
        json.dumps(
            [
                {"id": "a", "repo": str(project), "title": "open", "acknowledged": False},
                {"id": "b", "repo": "/other", "title": "other", "acknowledged": False},
            ]
        )
    )

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()

        result = json.loads(pending_path.read_text())
        for n in result:
            if n["repo"] == str(project):
                assert n["acknowledged"] is True
            else:
                assert n["acknowledged"] is False


# ─── ask state phase ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_phase_ask_when_asking_flag_present(cockpit_env):
    """Phase shows 'ask ⚠' when ~/.empirica/asking_{id} flag exists."""
    from textual.widgets import DataTable

    from empirica.cli.tui import CockpitApp

    home, project = cockpit_env
    _bind_instance(home, project, "tmux_42")
    # Create transaction so phase isn't no-transaction
    import time

    suffix = "_tmux_42"
    tx = {
        "transaction_id": "tx-1",
        "session_id": "s-1",
        "preflight_timestamp": time.time(),
        "status": "open",
        "project_path": str(project),
    }
    (project / ".empirica" / f"active_transaction{suffix}.json").write_text(json.dumps(tx))
    # Trigger ask
    (home / "asking_tmux_42").write_text("")

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause()
        await pilot.pause()
        table = app.query_one("#inst-table", DataTable)
        for row_key in table.rows:
            if str(row_key.value) == "tmux_42":
                row = table.get_row(row_key)
                # v1.6: phase column is shortened — 'ask⚠' with no space.
                # 2026-07-22: '#' column inserted at idx 1 → phase now at idx 3.
                phase_cell = str(row[3])
                assert "ask" in phase_cell, f"expected ask phase, got {phase_cell!r}"
                return
        pytest.fail("tmux_42 row not found")


# ─── v1.6 statusline + open goals ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_statusline_format_includes_conf_and_goals(cockpit_env):
    """Statusline format: k:X c:Y conf:Z% goals:N (when vectors available)."""
    import sqlite3
    import time

    from rich.console import Console
    from textual.widgets import Static

    from empirica.cli.tui import CockpitApp

    home, project = cockpit_env
    _bind_instance(home, project, "tmux_42")
    # Create the project DB with a snapshot the cockpit can read.
    db_dir = project / ".empirica" / "sessions"
    db_dir.mkdir(parents=True, exist_ok=True)
    db = db_dir / "sessions.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE epistemic_snapshots (
            snapshot_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            ai_id TEXT NOT NULL, timestamp TEXT NOT NULL,
            cascade_phase TEXT, cascade_id TEXT,
            vectors TEXT NOT NULL, delta TEXT, previous_snapshot_id TEXT,
            context_summary TEXT, evidence_refs TEXT, db_session_ref TEXT,
            domain_vectors TEXT
        );
        CREATE TABLE goals (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            objective TEXT NOT NULL, scope TEXT NOT NULL,
            created_timestamp REAL NOT NULL, completed_timestamp REAL,
            is_completed BOOLEAN DEFAULT 0, goal_data TEXT NOT NULL,
            status TEXT DEFAULT 'in_progress'
        );
    """)
    import json as _json

    conn.execute(
        "INSERT INTO epistemic_snapshots (snapshot_id, session_id, ai_id, timestamp, vectors) VALUES (?, ?, ?, ?, ?)",
        (
            "snap-1",
            "s-1",
            "cc",
            "2026-04-27T10:00:00+00:00",
            _json.dumps({"know": 0.8, "uncertainty": 0.2, "context": 0.7, "completion": 0.3}),
        ),
    )
    conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, goal_data, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("g-1", "s-1", "finish cockpit v1.6", "{}", time.time(), "{}", "in_progress"),
    )
    conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, goal_data, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("g-2", "s-1", "wire ENP integration", "{}", time.time(), "{}", "in_progress"),
    )
    conn.commit()
    conn.close()

    # Stub the active_transaction so session_id flows through.
    tx = {
        "transaction_id": "tx-1",
        "session_id": "s-1",
        "preflight_timestamp": time.time(),
        "status": "open",
        "project_path": str(project),
    }
    (project / ".empirica" / "active_transaction_tmux_42.json").write_text(_json.dumps(tx))

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(40, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        sl = app.query_one("#statusline", Static)
        c = Console(width=40, file=io.StringIO())
        with c.capture() as cap:
            c.print(sl.render())
        text = cap.get().strip()
        assert "k:0.80" in text, text
        assert "c:0.70" in text, text
        assert "conf:" in text, text
        assert "goals:2" in text, text


@pytest.mark.asyncio
async def test_open_goals_widget_shows_goals(cockpit_env):
    """Open goals widget lists open goals, not phase events."""
    import sqlite3
    import time

    from rich.console import Console
    from textual.widgets import Static

    from empirica.cli.tui import CockpitApp

    home, project = cockpit_env
    _bind_instance(home, project, "tmux_42")
    db_dir = project / ".empirica" / "sessions"
    db_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_dir / "sessions.db")
    conn.executescript("""
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, project_id TEXT
        );
        CREATE TABLE epistemic_snapshots (
            snapshot_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            ai_id TEXT NOT NULL, timestamp TEXT NOT NULL,
            vectors TEXT NOT NULL
        );
        CREATE TABLE goals (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            objective TEXT NOT NULL, scope TEXT NOT NULL,
            created_timestamp REAL NOT NULL, goal_data TEXT NOT NULL,
            is_completed BOOLEAN DEFAULT 0,
            project_id TEXT,
            status TEXT DEFAULT 'in_progress'
        );
    """)
    conn.execute("INSERT INTO sessions VALUES (?, ?)", ("s-1", "p-1"))
    conn.execute(
        "INSERT INTO goals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("g-1", "s-1", "distinctive goal A", "{}", time.time(), "{}", 0, "p-1", "in_progress"),
    )
    conn.commit()
    conn.close()

    tx = {
        "transaction_id": "tx-1",
        "session_id": "s-1",
        "preflight_timestamp": time.time(),
        "status": "open",
        "project_path": str(project),
    }
    import json as _json

    (project / ".empirica" / "active_transaction_tmux_42.json").write_text(_json.dumps(tx))

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(40, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        goals_w = app.query_one("#goals", Static)
        c = Console(width=40, file=io.StringIO())
        with c.capture() as cap:
            c.print(goals_w.render())
        text = cap.get().strip()
        assert "distinctive goal A" in text, text


@pytest.mark.asyncio
async def test_no_recent_widget(cockpit_env):
    """v1.6 dropped the recent-events widget — confirm it's gone."""
    from textual.css.query import NoMatches

    from empirica.cli.tui import CockpitApp

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(40, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        with pytest.raises(NoMatches):
            app.query_one("#recent")


# ─── L button mechanical-kill regression (1.9.6 + this commit) ────────────


@pytest.mark.asyncio
async def test_l_button_writes_pending_uninstall_when_armed(cockpit_env):
    """The TUI's L (toggle loops) button used to call set_loop_paused
    directly, bypassing the pause-cancels-cron mechanism. After this
    commit it calls handle_loop_pause_command, which writes the pending
    uninstall file when scheduler_kind=cron-create + job_id is recorded.
    """
    home, project = cockpit_env
    _bind_instance(home, project, "tmux_test")

    from empirica.cli.tui import CockpitApp
    from empirica.core.cockpit.loop_registry import LoopRegistry

    # Register a loop with a recorded job_id (mimics what a healthy
    # loop body's heartbeat call writes after CronCreate). Practice-keyed
    # (ai_id = project basename) — the TUI pauses under the same key.
    reg = LoopRegistry(project.name)
    reg.register(name="foo", kind="cron", cron="*/15 * * * *", description="t")
    reg.heartbeat(
        name="foo",
        status="ok",
        result="empty",
        next_scheduled_job_id="cron-job-xyz",
        scheduler_kind="cron-create",
    )

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(40, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        # Press L — should toggle to paused, which now writes pending uninstall
        await pilot.press("e")
        await pilot.pause()
        await pilot.pause()

    # The pending uninstall file should now exist.
    pending = list(home.glob("loop_uninstall_pending_*.json"))
    assert len(pending) == 1, (
        f"Expected pending uninstall file after L press; found {pending}. "
        "TUI L button must call handle_loop_pause_command, not set_loop_paused."
    )
    data = json.loads(pending[0].read_text())
    assert data["name"] == "foo"
    assert data["job_id"] == "cron-job-xyz"


# ─── E binding (listener) ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_e_button_toggles_listener_pause(cockpit_env):
    """E binding should toggle listener pause via handle_listener_pause_command."""
    home, project = cockpit_env
    _bind_instance(home, project, "tmux_test")

    from empirica.cli.tui import CockpitApp
    from empirica.core.cockpit.listener_registry import (
        ListenerRegistry,
        is_listener_paused,
    )

    reg = ListenerRegistry("tmux_test")
    reg.register(name="inbox", topic="ntfy:t", description="cortex")

    assert not is_listener_paused("tmux_test", "inbox")

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(40, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        await pilot.pause()

    assert is_listener_paused("tmux_test", "inbox"), "E press should have paused the listener via the proper handler."


@pytest.mark.asyncio
async def test_e_button_empty_registry_falls_back_to_canonical(
    cockpit_env,
    monkeypatch,
):
    """E with empty registries should bootstrap from the canonical catalog
    (cortex-mailbox-poll loop + inbox-listener). Tests the empty-state
    install path of the consolidated Events action.

    CRITICAL: stub handle_loop_enable_command so we don't actually
    `systemctl --user enable` a real timer named for 'tmux_test' on
    the dev machine. Discovered 2026-05-16 when David spotted a stray
    `empirica-loop-tmux_test-cortex-mailbox-poll.timer` after a test
    run — pre-stub fix the test installed a real systemd unit.
    """
    enable_calls: list[tuple[str, str]] = []

    def fake_enable(args):
        enable_calls.append((args.instance, args.name))
        return 0

    monkeypatch.setattr(
        "empirica.cli.tui.cockpit_app.handle_loop_enable_command",
        fake_enable,
    )

    home, project = cockpit_env
    _bind_instance(home, project, "tmux_test")

    from empirica.cli.tui import CockpitApp

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(40, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        # Canonical loop catalog provides cortex-mailbox-poll by default —
        # pressing 'e' on an empty registry should install it via
        # handle_loop_enable_command (systemd-user path, post-d96bde0c8).
        # The instance arg should be the project's ai_id (basename
        # 'project' here, not the tmux pane id 'tmux_test').
        assert ("project", "cortex-mailbox-poll") in enable_calls, (
            f"expected systemd install for project/cortex-mailbox-poll; got {enable_calls}"
        )


@pytest.mark.asyncio
async def test_e_button_empty_registry_actively_wakes_target_pane(
    cockpit_env,
    monkeypatch,
):
    """E press on empty registry should ALSO actively wake the target pane
    via tmux send-keys — without this, the AI doesn't process the pending
    install until the user manually prompts. This was the gap David hit
    2026-05-16: 'I enabled event listeners from TUI but instances showed
    no event listener.'"""
    from empirica.core.cockpit import instance_actions

    captured: list[str] = []

    def fake_wake(instance_id):
        captured.append(instance_id)
        return instance_actions.WakeResult(
            instance_id=instance_id,
            success=True,
            method="tmux-send-keys",
            detail="stubbed",
        )

    monkeypatch.setattr("empirica.cli.tui.cockpit_app.wake_instance", fake_wake)

    home, project = cockpit_env
    _bind_instance(home, project, "tmux_test")

    from empirica.cli.tui import CockpitApp

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(40, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()

    assert captured == ["tmux_test"], f"wake_instance should fire exactly once for the target; got {captured}"


# ─── E column in instance table ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_listeners_surface_in_aggregate(cockpit_env):
    """aggregate_all should include a 'listeners' dict per instance + a
    listeners_registered/listeners_paused summary."""
    home, project = cockpit_env
    _bind_instance(home, project, "tmux_test")

    from empirica.core.cockpit import aggregate_all
    from empirica.core.cockpit.listener_registry import (
        ListenerRegistry,
        set_listener_paused,
    )

    reg = ListenerRegistry("tmux_test")
    reg.register(name="a", topic="ntfy:1")
    reg.register(name="b", topic="ntfy:2")
    set_listener_paused("tmux_test", "a", True)

    payload = aggregate_all(include_dead=True)
    [inst] = [i for i in payload["instances"] if i["instance_id"] == "tmux_test"]
    assert "listeners" in inst
    assert set(inst["listeners"].keys()) == {"a", "b"}
    assert inst["listeners"]["a"]["paused"] is True
    assert inst["listeners"]["b"]["paused"] is False

    summary = payload["summary"]
    assert summary["listeners_registered"] >= 2
    assert summary["listeners_paused"] >= 1


# ─── Phase 2: install-on-click from project.yaml cockpit block ─────────────


def _write_project_yaml(project: Path, cockpit: dict) -> None:
    import yaml

    (project / ".empirica" / "project.yaml").write_text(yaml.safe_dump({"cockpit": cockpit}))


@pytest.mark.asyncio
async def test_l_click_empty_registry_installs_from_project_yaml(cockpit_env):
    """Phase 2: when registry is empty, L click should read cockpit.loops
    from project.yaml and queue install-request for each entry."""
    home, project = cockpit_env
    _bind_instance(home, project, "tmux_test")
    _write_project_yaml(
        project,
        {
            "loops": [
                {
                    "name": "auto-poll",
                    "kind": "cron",
                    "cron": "*/15 * * * *",
                    "description": "auto-installed",
                    "base_interval": "15m",
                    "max_interval": "4h",
                },
            ],
        },
    )

    from empirica.cli.tui import CockpitApp
    from empirica.core.cockpit.loop_registry import LoopRegistry

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(40, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        await pilot.pause()

    # Loop should now be in the registry (install-request registers).
    reg = LoopRegistry("tmux_test")
    entry = reg.get("auto-poll")
    assert entry is not None, "L on empty registry should have installed auto-poll"
    assert entry.kind == "cron"

    # Pending install request file should also exist for owning Claude pickup.
    pending = list(home.glob("loop_install_pending_tmux_test_*.json"))
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_l_click_empty_registry_no_yaml_falls_back_to_canonical_catalog(
    cockpit_env,
    monkeypatch,
):
    """When project.yaml has no cockpit.loops block, L click should
    fall back to the system-level canonical catalog (v1.9.6+) and
    install the canonical loops instead of just showing a hint.

    Updated 2026-05-16 — canonical_loops.py declares scheduler_kind=
    'systemd-user', so the install path now dispatches via systemctl
    (handle_loop_enable_command) directly rather than writing a pending
    file. Pre-fix bug: cockpit_app.py compared `== 'systemd'` literally
    against the canonical value 'systemd-user' → mismatch → fell through
    to legacy cron-create pending-file path. After the prefix-match fix,
    systemd* values route to direct install.

    We stub handle_loop_enable_command so the test doesn't need a real
    systemd-user environment, and verify it gets called once per
    canonical systemd-user loop."""
    from empirica.core.cockpit.canonical_loops import CANONICAL_LOOPS

    captured: list[tuple[str, str]] = []

    def fake_enable(args):
        captured.append((args.instance, args.name))
        return 0

    monkeypatch.setattr(
        "empirica.cli.tui.cockpit_app.handle_loop_enable_command",
        fake_enable,
    )

    home, project = cockpit_env
    _bind_instance(home, project, "tmux_test")
    _write_project_yaml(project, {})  # empty cockpit block

    from empirica.cli.tui import CockpitApp

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(40, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()

    systemd_loops = [c for c in CANONICAL_LOOPS if (c.get("scheduler_kind") or "").lower().startswith("systemd")]
    assert len(captured) == len(systemd_loops), (
        f"expected {len(systemd_loops)} systemd loop installs, got {len(captured)}: {captured}"
    )
    captured_names = {name for _, name in captured}
    expected_names = {c["name"] for c in systemd_loops}
    assert captured_names == expected_names


@pytest.mark.asyncio
async def test_e_click_empty_registry_installs_listener_from_project_yaml(cockpit_env):
    """Same as the loop test, but for listeners + cockpit.listeners block."""
    home, project = cockpit_env
    _bind_instance(home, project, "tmux_test")
    _write_project_yaml(
        project,
        {
            "listeners": [
                {
                    "name": "auto-inbox",
                    "topic": "ntfy:auto-channel",
                    "description": "auto-installed listener",
                    "on_wake": "Process new event",
                },
            ],
        },
    )

    from empirica.cli.tui import CockpitApp
    from empirica.core.cockpit.listener_registry import ListenerRegistry

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(40, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        await pilot.pause()

    reg = ListenerRegistry("tmux_test")
    entry = reg.get("auto-inbox")
    assert entry is not None
    assert entry.topic == "ntfy:auto-channel"

    pending = list(home.glob("listener_install_pending_tmux_test_*.json"))
    assert len(pending) == 1


# ─── Compliance panel + domain chip (1.9.6) ───────────────────────────────


def _write_project_id(project: Path, project_id: str) -> None:
    """Add project_id to .empirica/project.yaml so compliance_view can
    resolve project_path → project_id."""
    import yaml

    p = project / ".empirica" / "project.yaml"
    data = {}
    if p.exists():
        data = yaml.safe_load(p.read_text()) or {}
    data["project_id"] = project_id
    p.write_text(yaml.safe_dump(data))


def _write_active_transaction(
    home: Path,
    project: Path,
    instance_id: str,
    domain: str = "default",
    criticality: str = "medium",
) -> None:
    """Simulate an open transaction with domain + criticality fields so
    instance_state surfaces them in the transaction block."""
    import json

    tx_path = project / ".empirica" / f"active_transaction_{instance_id}.json"
    tx_path.parent.mkdir(parents=True, exist_ok=True)
    tx_path.write_text(
        json.dumps(
            {
                "transaction_id": "test-tx-id",
                "session_id": "test-session",
                "preflight_timestamp": 1775380000.0,
                "status": "open",
                "project_path": str(project),
                "work_type": "code",
                "domain": domain,
                "criticality": criticality,
            }
        )
    )


@pytest.mark.asyncio
async def test_compliance_widget_shows_passing_collapsed(cockpit_env, monkeypatch):
    """When all checks pass, the compliance widget collapses to a single line."""
    home, project = cockpit_env
    _bind_instance(home, project, "tmux_test")
    _write_project_id(project, "test-project")

    # Persist a passing compliance result
    from empirica.core.cockpit import compliance_view

    monkeypatch.setattr(compliance_view, "EMPIRICA_DIR", home)
    compliance_view.write_last_compliance(
        "test-project",
        {
            "overall": {
                "status": "compliant",
                "score": 1.0,
                "checks_passed": 11,
                "checks_total": 11,
            },
            "checks": [
                {"check": "lint", "passed": True},
                {"check": "complexity", "passed": True},
            ],
        },
    )

    from textual.widgets import Static

    from empirica.cli.tui import CockpitApp

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(60, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        widget = app.query_one("#compliance", Static)
        from rich.console import Console

        c = Console(width=120, file=io.StringIO())
        with c.capture() as cap:
            c.print(widget.render())
        text = cap.get()
        assert "🛡" in text
        assert "11/11" in text
        # Collapsed: should NOT contain a "press `c`" hint
        assert "✗" not in text


@pytest.mark.asyncio
async def test_compliance_widget_failures_appear_in_header(
    cockpit_env,
    monkeypatch,
):
    """Failing check names live in the header always — operator can never
    use a key to hide them, and the toggle-only-affects-passing rule keeps
    failure visibility independent of `compliance_expanded`."""
    home, project = cockpit_env
    _bind_instance(home, project, "tmux_test")
    _write_project_id(project, "test-project")

    from empirica.core.cockpit import compliance_view

    monkeypatch.setattr(compliance_view, "EMPIRICA_DIR", home)
    compliance_view.write_last_compliance(
        "test-project",
        {
            "overall": {
                "status": "non_compliant",
                "score": 0.7273,
                "checks_passed": 8,
                "checks_total": 11,
            },
            "checks": [
                {"check": "lint", "passed": False},
                {"check": "complexity", "passed": False},
                {"check": "type_safety", "passed": False},
                {"check": "tests", "passed": True},
            ],
        },
    )

    from textual.widgets import Static

    from empirica.cli.tui import CockpitApp

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(60, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        widget = app.query_one("#compliance", Static)
        from rich.console import Console

        c = Console(width=120, file=io.StringIO())
        with c.capture() as cap:
            c.print(widget.render())
        text = cap.get()
        assert "8/11" in text
        # Default-expanded: failure labels visible
        assert "lint" in text
        assert "complexity" in text
        assert "type_safety" in text


@pytest.mark.asyncio
async def test_compliance_widget_no_data_message(cockpit_env):
    """When compliance hasn't been run, the widget shows a guidance hint."""
    home, project = cockpit_env
    _bind_instance(home, project, "tmux_test")
    # No project_id, no last_compliance file

    from textual.widgets import Static

    from empirica.cli.tui import CockpitApp

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(60, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        widget = app.query_one("#compliance", Static)
        from rich.console import Console

        c = Console(width=120, file=io.StringIO())
        with c.capture() as cap:
            c.print(widget.render())
        text = cap.get()
        assert "compliance-report" in text


@pytest.mark.asyncio
async def test_c_key_toggles_compliance_expansion(cockpit_env, monkeypatch):
    """`c` reveals per-passing-check rows when all green; default is head only."""
    home, project = cockpit_env
    _bind_instance(home, project, "tmux_test")
    _write_project_id(project, "test-project")

    from empirica.core.cockpit import compliance_view

    monkeypatch.setattr(compliance_view, "EMPIRICA_DIR", home)
    compliance_view.write_last_compliance(
        "test-project",
        {
            "overall": {
                "status": "compliant",
                "score": 1.0,
                "checks_passed": 3,
                "checks_total": 3,
            },
            "checks": [
                {"check": "lint", "passed": True},
                {"check": "complexity", "passed": True},
                {"check": "tests", "passed": True},
            ],
        },
    )

    from empirica.cli.tui import CockpitApp

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(60, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        inst = app._selected_instance()
        assert inst is not None

        # Default for passing = head only, no per-check rows
        assert app.compliance_expanded is False
        rendered_default = app._format_compliance(inst)
        assert "3/3" in rendered_default
        assert "  ✓ lint" not in rendered_default
        assert "  ✓ complexity" not in rendered_default

        await pilot.press("c")
        await pilot.pause()
        assert app.compliance_expanded is True
        rendered_after = app._format_compliance(inst)
        # Each passing check listed individually
        assert "  ✓ lint" in rendered_after
        assert "  ✓ complexity" in rendered_after
        assert "  ✓ tests" in rendered_after


@pytest.mark.asyncio
async def test_c_key_toggles_compliance_in_failure_state(cockpit_env, monkeypatch):
    """In failure state: `c` reveals the *passing* checks; failures stay
    in the header always. The toggle never hides a failure and never
    re-lists failures (they're already in the header).
    """
    home, project = cockpit_env
    _bind_instance(home, project, "tmux_test")
    _write_project_id(project, "test-project")

    from empirica.core.cockpit import compliance_view

    monkeypatch.setattr(compliance_view, "EMPIRICA_DIR", home)
    compliance_view.write_last_compliance(
        "test-project",
        {
            "overall": {
                "status": "fail",
                "score": 0.6,
                "checks_passed": 3,
                "checks_total": 5,
            },
            "checks": [
                {"check": "lint", "passed": False},
                {"check": "complexity", "passed": False},
                {"check": "tests", "passed": True},
                {"check": "dep_audit", "passed": True},
                {"check": "type_safety", "passed": True},
            ],
        },
    )

    from empirica.cli.tui import CockpitApp

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(80, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        inst = app._selected_instance()
        assert inst is not None

        # Default = head only. Failures live in the header.
        assert app.compliance_expanded is False
        rendered_default = app._format_compliance(inst)
        assert "failing: lint, complexity" in rendered_default
        # No passing-check rows yet
        assert "  ✓ tests" not in rendered_default
        assert "  ✓ dep_audit" not in rendered_default

        # Press `c` — passing checks appear; failures stay in head; no
        # per-failure rows are added (header already lists them).
        await pilot.press("c")
        await pilot.pause()
        assert app.compliance_expanded is True
        rendered_expanded = app._format_compliance(inst)
        assert "failing: lint, complexity" in rendered_expanded
        assert "  ✓ tests" in rendered_expanded
        assert "  ✓ dep_audit" in rendered_expanded
        assert "  ✓ type_safety" in rendered_expanded
        # Failures are NEVER duplicated as detail rows
        assert "  ✗ lint" not in rendered_expanded
        assert "  ✗ complexity" not in rendered_expanded

        # Press `c` again — back to head-only
        await pilot.press("c")
        await pilot.pause()
        assert app.compliance_expanded is False
        rendered_collapsed = app._format_compliance(inst)
        assert "failing: lint, complexity" in rendered_collapsed
        assert "  ✓ tests" not in rendered_collapsed


@pytest.mark.asyncio
async def test_domain_chip_in_table_when_transaction_open(cockpit_env):
    """When an instance has an open transaction with domain + criticality,
    the table renders a 'dom·crit' chip in the dom column."""
    home, project = cockpit_env
    _bind_instance(home, project, "tmux_test")
    _write_active_transaction(
        home,
        project,
        "tmux_test",
        domain="legal",
        criticality="high",
    )

    from textual.widgets import DataTable

    from empirica.cli.tui import CockpitApp

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(60, 20)) as pilot:
        await pilot.pause()
        await pilot.pause()
        table = app.query_one("#inst-table", DataTable)
        # Read the 'dom' cell directly via table API (column index 4,
        # zero-indexed: s, #, name, ph, dom, ...). The '#' tmux-window column
        # (2026-07-22) shifted dom from idx 3 to 4. The table renders as a
        # widget — we go via get_cell which works in headless tests.
        row_keys = list(table.rows.keys())
        assert row_keys, "expected at least one row"
        # First row, dom column
        dom_value = table.get_cell_at((0, 4))
        # 'leg·H' = legal/high domain chip
        assert dom_value == "leg·H", f"expected leg·H, got {dom_value!r}"


@pytest.mark.asyncio
async def test_domain_chip_dash_when_no_transaction(cockpit_env):
    """No open transaction → '—' in dom column."""
    home, project = cockpit_env
    _bind_instance(home, project, "tmux_test")

    from textual.widgets import DataTable

    from empirica.cli.tui import CockpitApp

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(60, 20)) as pilot:
        await pilot.pause()
        await pilot.pause()
        table = app.query_one("#inst-table", DataTable)
        row_keys = list(table.rows.keys())
        assert row_keys, "expected at least one row"
        dom_value = table.get_cell_at((0, 3))
        assert dom_value == "—", f"expected —, got {dom_value!r}"
