"""Tests for the empirica tui (Textual cockpit app).

Uses textual's headless `App.run_test()` runtime so tests don't require
a real terminal. The app's actions read from and write to ~/.empirica/,
so we monkeypatch the cockpit module's EMPIRICA_DIR to a tmp path before
the app starts.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest_plugins = ['pytest_asyncio']


@pytest.fixture
def cockpit_env(tmp_path, monkeypatch):
    """Redirect every cockpit module's EMPIRICA_DIR to a tmp dir."""
    fake_home = tmp_path / '.empirica'
    fake_home.mkdir()
    (fake_home / 'instance_projects').mkdir()
    (fake_home / 'tty_sessions').mkdir()

    project = tmp_path / 'project'
    project.mkdir()

    from empirica.core.cockpit import (
        instance_actions,
        instance_state,
        loop_registry,
        sentinel_pause,
    )

    monkeypatch.setattr(sentinel_pause, 'EMPIRICA_DIR', fake_home)
    monkeypatch.setattr(sentinel_pause, 'GLOBAL_PAUSE_FILE', fake_home / 'sentinel_paused')
    monkeypatch.setattr(instance_state, 'EMPIRICA_DIR', fake_home)
    monkeypatch.setattr(instance_actions, 'EMPIRICA_DIR', fake_home)
    monkeypatch.setattr(instance_actions, 'TTY_SESSIONS_DIR', fake_home / 'tty_sessions')
    monkeypatch.setattr(loop_registry, 'EMPIRICA_DIR', fake_home)

    return fake_home, project


def _bind_instance(home: Path, project: Path, instance_id: str) -> None:
    (home / 'instance_projects' / f'{instance_id}.json').write_text(
        json.dumps({'project_path': str(project)})
    )


@pytest.mark.asyncio
async def test_tui_mounts_with_all_widgets(cockpit_env):
    from empirica.cli.tui import CockpitApp
    from textual.widgets import Button, DataTable, Log, Static

    app = CockpitApp()
    async with app.run_test(headless=True, size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        assert app.query_one('#inst-table', DataTable) is not None
        assert app.query_one('#summary', Static) is not None
        assert app.query_one('#detail-content', Static) is not None
        assert app.query_one('#log', Log) is not None
        # Action bar buttons
        for btn_id in (
            'btn-pause-sent', 'btn-resume-sent', 'btn-loops',
            'btn-relabel', 'btn-kill', 'btn-forget', 'btn-refresh',
        ):
            assert app.query_one(f'#{btn_id}', Button) is not None, btn_id


@pytest.mark.asyncio
async def test_tui_loads_instances_from_state_files(cockpit_env):
    from empirica.cli.tui import CockpitApp

    home, project = cockpit_env
    _bind_instance(home, project, 'tmux_42')
    _bind_instance(home, project, 'tmux_99')

    app = CockpitApp()
    async with app.run_test(headless=True, size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        ids = sorted(i['instance_id'] for i in app.payload['instances'])
        assert ids == ['tmux_42', 'tmux_99']


@pytest.mark.asyncio
async def test_pause_sentinel_button_writes_pause_file(cockpit_env):
    from empirica.cli.tui import CockpitApp

    home, project = cockpit_env
    _bind_instance(home, project, 'tmux_42')

    app = CockpitApp()
    async with app.run_test(headless=True, size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        assert app.selected_instance_id == 'tmux_42'

        await pilot.click('#btn-pause-sent')
        await pilot.pause()

        assert (home / 'sentinel_paused_tmux_42').exists()


@pytest.mark.asyncio
async def test_resume_sentinel_button_removes_pause_file(cockpit_env):
    from empirica.cli.tui import CockpitApp
    from empirica.core.cockpit import pause_sentinel

    home, project = cockpit_env
    _bind_instance(home, project, 'tmux_42')
    pause_sentinel('tmux_42', reason='setup')
    assert (home / 'sentinel_paused_tmux_42').exists()

    app = CockpitApp()
    async with app.run_test(headless=True, size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        await pilot.click('#btn-resume-sent')
        await pilot.pause()

        assert not (home / 'sentinel_paused_tmux_42').exists()


@pytest.mark.asyncio
async def test_keyboard_shortcut_p_pauses_sentinel(cockpit_env):
    from empirica.cli.tui import CockpitApp

    home, project = cockpit_env
    _bind_instance(home, project, 'tmux_42')

    app = CockpitApp()
    async with app.run_test(headless=True, size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        await pilot.press('p')
        await pilot.pause()

        assert (home / 'sentinel_paused_tmux_42').exists()


@pytest.mark.asyncio
async def test_kill_button_opens_confirm_modal(cockpit_env):
    from empirica.cli.tui import CockpitApp
    from empirica.cli.tui.cockpit_app import ConfirmScreen

    home, project = cockpit_env
    _bind_instance(home, project, 'tmux_42')

    app = CockpitApp()
    async with app.run_test(headless=True, size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        await pilot.click('#btn-kill')
        await pilot.pause()

        # The active screen should now be a ConfirmScreen
        assert isinstance(app.screen, ConfirmScreen)


@pytest.mark.asyncio
async def test_kill_modal_cancel_does_not_act(cockpit_env, monkeypatch):
    from empirica.cli.tui import CockpitApp
    from empirica.core.cockpit import instance_actions

    called = {'killed': False}
    monkeypatch.setattr(
        instance_actions, 'kill_instance',
        lambda *a, **k: called.setdefault('killed', True)  # type: ignore[arg-type]
    )

    home, project = cockpit_env
    _bind_instance(home, project, 'tmux_42')

    app = CockpitApp()
    async with app.run_test(headless=True, size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        await pilot.click('#btn-kill')
        await pilot.pause()
        # Cancel via Escape
        await pilot.press('escape')
        await pilot.pause()

        assert called['killed'] is False


@pytest.mark.asyncio
async def test_summary_payload_shows_counts(cockpit_env):
    """Verify the payload that drives the summary widget — the widget's
    rendered string is checked indirectly so we don't bind to Textual's
    internal Static API."""
    from empirica.cli.tui import CockpitApp

    home, project = cockpit_env
    _bind_instance(home, project, 'tmux_42')
    _bind_instance(home, project, 'tmux_99')

    app = CockpitApp()
    async with app.run_test(headless=True, size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        assert app.payload['summary']['instances'] == 2
