"""empirica tui — Textual cockpit app.

Layout (one screen):
- Header                                : title, clock
- Summary bar                           : N instances · M loops · K paused · A active tx
- Instance DataTable                    : one row per instance, click to select
- Detail / loops pane (right or below)  : selected instance details
- Action footer                         : keyboard bindings + buttons

Selection is row-based. Actions operate on the selected instance:
  p — pause Sentinel
  r — resume Sentinel
  l — pause/resume loops menu
  k — kill (modal confirm)
  f — forget (modal confirm)
  R — relabel (input)
  q — quit

Mouse: click row to select, click action button at the bottom to invoke.
Auto-refresh: every 2 seconds via app.set_interval (matches the watch
recipe cadence). On any action, refresh immediately so feedback is
visible without waiting for the next tick.
"""

from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Log,
    Static,
)

from empirica.core.cockpit import (
    aggregate_all,
    forget_instance,
    kill_instance,
    pause_sentinel,
    resume_sentinel,
    set_label,
    set_loop_paused,
)
from empirica.core.cockpit.loop_registry import LoopRegistry, is_loop_paused

REFRESH_SECONDS = 2.0


# ─── modal confirmations ───────────────────────────────────────────────────

class ConfirmScreen(ModalScreen[bool]):
    """Yes/No modal. Returns True if confirmed."""

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    #dialog {
        width: 60; height: 11;
        border: thick $error 80%;
        background: $surface;
        padding: 1 2;
    }
    #dialog-buttons {
        align-horizontal: center; height: 3; margin-top: 1;
    }
    #dialog Button { margin: 0 1; min-width: 10; }
    """

    BINDINGS = [
        Binding('escape', 'dismiss(False)', 'Cancel'),
        Binding('y', 'confirm', 'Yes'),
        Binding('n', 'dismiss(False)', 'No'),
    ]

    def __init__(self, prompt: str, danger: bool = True) -> None:
        super().__init__()
        self.prompt = prompt
        self.danger = danger

    def compose(self) -> ComposeResult:
        with Container(id='dialog'):
            yield Label(self.prompt)
            with Horizontal(id='dialog-buttons'):
                yield Button(
                    'Yes' if not self.danger else 'Confirm',
                    id='yes', variant='error' if self.danger else 'primary',
                )
                yield Button('Cancel', id='no', variant='default')

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == 'yes')

    def action_confirm(self) -> None:
        self.dismiss(True)


class InputScreen(ModalScreen[str | None]):
    """Single-line input modal. Returns string or None on cancel."""

    DEFAULT_CSS = """
    InputScreen {
        align: center middle;
    }
    #dialog {
        width: 70; height: 12;
        border: thick $accent 80%;
        background: $surface;
        padding: 1 2;
    }
    #dialog-input { margin-top: 1; }
    #dialog-buttons {
        align-horizontal: center; height: 3; margin-top: 1;
    }
    #dialog Button { margin: 0 1; min-width: 10; }
    """

    BINDINGS = [
        Binding('escape', 'dismiss(None)', 'Cancel'),
    ]

    def __init__(self, prompt: str, initial: str = '') -> None:
        super().__init__()
        self.prompt = prompt
        self.initial = initial

    def compose(self) -> ComposeResult:
        with Container(id='dialog'):
            yield Label(self.prompt)
            yield Input(value=self.initial, id='dialog-input')
            with Horizontal(id='dialog-buttons'):
                yield Button('OK', id='ok', variant='primary')
                yield Button('Cancel', id='cancel', variant='default')

    def on_mount(self) -> None:
        self.query_one('#dialog-input', Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == 'ok':
            self.dismiss(self.query_one('#dialog-input', Input).value)
        else:
            self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.dismiss(self.query_one('#dialog-input', Input).value)


class LoopMenuScreen(ModalScreen[tuple[str, str] | None]):
    """Loop pick → (loop_name, action) where action is 'pause' or 'resume'."""

    DEFAULT_CSS = """
    LoopMenuScreen {
        align: center middle;
    }
    #dialog {
        width: 80; height: 24;
        border: thick $accent 80%;
        background: $surface;
        padding: 1 2;
    }
    #loops-table { height: 14; }
    #dialog-buttons {
        align-horizontal: center; height: 3; margin-top: 1;
    }
    #dialog Button { margin: 0 1; min-width: 10; }
    """

    BINDINGS = [
        Binding('escape', 'dismiss(None)', 'Cancel'),
    ]

    def __init__(self, instance_id: str, loops: dict[str, dict[str, Any]]) -> None:
        super().__init__()
        self.instance_id = instance_id
        self.loops = loops
        self._selected_loop: str | None = None

    def compose(self) -> ComposeResult:
        with Container(id='dialog'):
            yield Label(f'Loops on {self.instance_id} — pick one to toggle')
            table: DataTable = DataTable(id='loops-table', cursor_type='row', zebra_stripes=True)
            table.add_columns('Loop', 'Kind', 'Schedule', 'Last Run', 'Status', 'Paused')
            for name in sorted(self.loops):
                loop = self.loops[name]
                schedule = loop.get('cron') or loop.get('interval') or ''
                last_run = (loop.get('last_run') or '').split('T')[-1].split('+')[0][:8] or '—'
                status = loop.get('last_status') or '—'
                paused = 'PAUSED' if loop.get('paused') else 'ON'
                table.add_row(name, loop.get('kind', '?'), schedule, last_run, status, paused, key=name)
            yield table
            with Horizontal(id='dialog-buttons'):
                yield Button('Pause', id='pause', variant='warning')
                yield Button('Resume', id='resume', variant='primary')
                yield Button('Cancel', id='cancel', variant='default')

    def on_mount(self) -> None:
        # Pre-select first row so a click-and-act with no navigation works.
        if self.loops:
            self._selected_loop = sorted(self.loops)[0]

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value:
            self._selected_loop = str(event.row_key.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == 'cancel':
            self.dismiss(None)
            return
        if not self._selected_loop:
            return
        self.dismiss((self._selected_loop, event.button.id))


# ─── main app ──────────────────────────────────────────────────────────────

class CockpitApp(App):
    """Interactive Empirica cockpit."""

    CSS = """
    Screen { layout: vertical; }

    #summary { padding: 0 2; height: 1; color: $text-muted; }

    #main { layout: horizontal; }

    #instances {
        width: 60%;
        border-right: tall $surface-darken-1;
    }

    #detail {
        width: 40%;
        padding: 1 2;
    }

    #log {
        height: 6;
        border-top: tall $surface-darken-1;
        padding: 0 1;
    }

    DataTable { height: 1fr; }

    #action-bar {
        dock: bottom;
        height: 3;
        background: $surface;
        align-horizontal: left;
        padding: 0 1;
    }
    #action-bar Button { margin: 0 1; min-width: 10; }
    """

    BINDINGS = [
        Binding('q', 'quit', 'Quit'),
        Binding('r', 'refresh_now', 'Refresh'),
        Binding('p', 'sentinel_pause', 'Pause Sent.'),
        Binding('shift+p', 'sentinel_resume', 'Resume Sent.'),
        Binding('l', 'loops', 'Loops'),
        Binding('k', 'kill', 'Kill'),
        Binding('f', 'forget', 'Forget'),
        Binding('R', 'relabel', 'Rename'),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.payload: dict[str, Any] = {'instances': [], 'summary': {}, 'generated_at': ''}
        self.selected_instance_id: str | None = None

    # ─── lifecycle ────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static('', id='summary')
        with Horizontal(id='main'):
            with Vertical(id='instances'):
                table = DataTable(id='inst-table', cursor_type='row', zebra_stripes=True)
                table.add_columns('Instance', 'State', 'Phase', 'Sentinel', 'Loops', 'Last Tx')
                yield table
            with Vertical(id='detail'):
                yield Static('Select an instance to see details', id='detail-content')
        yield Log(id='log', highlight=False)
        with Horizontal(id='action-bar'):
            yield Button('Pause Sent. (p)', id='btn-pause-sent', variant='warning')
            yield Button('Resume Sent. (P)', id='btn-resume-sent', variant='primary')
            yield Button('Loops (l)', id='btn-loops', variant='default')
            yield Button('Rename (R)', id='btn-relabel', variant='default')
            yield Button('Kill (k)', id='btn-kill', variant='error')
            yield Button('Forget (f)', id='btn-forget', variant='error')
            yield Button('Refresh (r)', id='btn-refresh', variant='default')
        yield Footer()

    def on_mount(self) -> None:
        self.title = 'empirica cockpit'
        self.refresh_payload()
        self.set_interval(REFRESH_SECONDS, self.refresh_payload)

    # ─── data + rendering ─────────────────────────────────────────────────

    def refresh_payload(self) -> None:
        try:
            self.payload = aggregate_all()
        except Exception as e:
            self._log(f'refresh failed: {e}')
            return
        self._render_summary()
        self._render_table()
        self._render_detail()

    def _render_summary(self) -> None:
        s = self.payload.get('summary', {})
        ts = self.payload.get('generated_at', '').split('T')[-1].split('+')[0][:8]
        text = (
            f"{s.get('instances', 0)} instances · "
            f"{s.get('loops_registered', 0)} loops · "
            f"{s.get('loops_paused', 0)} paused · "
            f"{s.get('active_tx', 0)} active tx · refreshed {ts}"
        )
        self.query_one('#summary', Static).update(text)

    def _render_table(self) -> None:
        table = self.query_one('#inst-table', DataTable)
        # Preserve the selected key across rebuilds.
        previously_selected = self.selected_instance_id
        table.clear()
        rows = self.payload.get('instances', [])
        for inst in rows:
            iid = inst['instance_id']
            label = inst.get('label') or iid
            state = self._state_glyph(inst['state']) + ' ' + inst['state']
            phase = inst.get('phase') or '—'
            sentinel = '🟢 ON' if not inst['sentinel']['paused'] else '🔴 PAUSED'
            loops = inst.get('loops', {}) or {}
            lp = sum(1 for v in loops.values() if v.get('paused'))
            loops_cell = f'{len(loops) - lp}/{len(loops)}' if loops else '0/0'
            tx = inst.get('transaction')
            tx_cell = tx['id'].split('-')[0] if tx else '—'
            table.add_row(label, state, phase, sentinel, loops_cell, tx_cell, key=iid)
        # Re-select if still present, else pick first row.
        if rows:
            target = previously_selected or rows[0]['instance_id']
            for idx, inst in enumerate(rows):
                if inst['instance_id'] == target:
                    table.move_cursor(row=idx)
                    self.selected_instance_id = target
                    break
            else:
                table.move_cursor(row=0)
                self.selected_instance_id = rows[0]['instance_id']
        else:
            self.selected_instance_id = None

    @staticmethod
    def _state_glyph(state: str) -> str:
        return {
            'active': '🟢', 'idle': '🟡', 'stuck': '🔴',
            'closed': '⊘', 'no-claude': '⊗',
        }.get(state, '?')

    def _render_detail(self) -> None:
        detail = self.query_one('#detail-content', Static)
        inst = self._selected_instance()
        if inst is None:
            detail.update('Select an instance to see details')
            return
        lines = [
            f"[b]{inst.get('label') or inst['instance_id']}[/b] ({inst['instance_id']})",
            f"project: {inst.get('project_path') or '—'}",
            f"state:   {self._state_glyph(inst['state'])} {inst['state']}",
            f"phase:   {inst.get('phase')}",
            '',
        ]
        sent = inst.get('sentinel', {})
        if sent.get('paused'):
            lines.append(f"[red]Sentinel PAUSED[/red] (scope={sent.get('scope')})")
            if sent.get('since'):
                lines.append(f"  since {sent['since']}")
            if sent.get('reason'):
                lines.append(f"  reason: {sent['reason']}")
        else:
            lines.append('[green]Sentinel ON[/green]')
        lines.append('')

        loops = inst.get('loops') or {}
        if not loops:
            lines.append('Loops: (none)')
        else:
            lines.append('[b]Loops[/b]')
            for name in sorted(loops):
                loop = loops[name]
                paused = 'PAUSED' if loop.get('paused') else 'on'
                kind = loop.get('kind', '?')
                schedule = loop.get('cron') or loop.get('interval') or ''
                last = (loop.get('last_run') or '').split('T')[-1].split('+')[0][:8] or 'never'
                lines.append(f'  • {name}  [{kind} {schedule}]  last {last}  {paused}')

        tx = inst.get('transaction')
        if tx:
            lines.append('')
            lines.append(f"transaction: {tx['id'][:8]}  age {int(tx.get('age_seconds') or 0)}s")
        detail.update('\n'.join(lines))

    def _selected_instance(self) -> dict[str, Any] | None:
        if not self.selected_instance_id:
            return None
        for inst in self.payload.get('instances', []):
            if inst['instance_id'] == self.selected_instance_id:
                return inst
        return None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value:
            self.selected_instance_id = str(event.row_key.value)
            self._render_detail()

    # ─── actions (bindings) ───────────────────────────────────────────────

    def action_refresh_now(self) -> None:
        self.refresh_payload()
        self._log('refreshed')

    def action_sentinel_pause(self) -> None:
        inst = self._require_selected()
        if inst is None:
            return
        result = pause_sentinel(inst['instance_id'], reason='via tui')
        self._log(f"paused sentinel for {inst['instance_id']} (scope={result.scope})")
        self.refresh_payload()

    def action_sentinel_resume(self) -> None:
        inst = self._require_selected()
        if inst is None:
            return
        result = resume_sentinel(inst['instance_id'])
        self._log(f"resume requested for {inst['instance_id']} (now: scope={result.scope})")
        self.refresh_payload()

    def action_loops(self) -> None:
        inst = self._require_selected()
        if inst is None:
            return
        loops = inst.get('loops') or {}
        if not loops:
            self._log(f"{inst['instance_id']}: no loops registered")
            return

        def _on_pick(result: tuple[str, str] | None) -> None:
            if result is None:
                return
            name, action = result
            paused = action == 'pause'
            set_loop_paused(inst['instance_id'], name, paused)
            self._log(f"loop {name} on {inst['instance_id']}: {action}")
            self.refresh_payload()

        self.push_screen(LoopMenuScreen(inst['instance_id'], loops), _on_pick)

    def action_kill(self) -> None:
        inst = self._require_selected()
        if inst is None:
            return
        iid = inst['instance_id']
        is_current = iid == _current_instance_id()
        warning = ' [YOUR CURRENT INSTANCE]' if is_current else ''
        prompt = f"Kill {iid}?{warning}\n\nThis terminates the Claude Code process for this instance."

        def _confirmed(yes: bool) -> None:
            if not yes:
                return
            result = kill_instance(iid)
            self._log(f'kill {iid} → {result.method}: {result.detail}')
            self.refresh_payload()

        self.push_screen(ConfirmScreen(prompt, danger=True), _confirmed)

    def action_forget(self) -> None:
        inst = self._require_selected()
        if inst is None:
            return
        iid = inst['instance_id']
        is_current = iid == _current_instance_id()
        warning = ' [YOUR CURRENT INSTANCE]' if is_current else ''
        prompt = f"Forget {iid}?{warning}\n\nRemoves all per-instance state files. Idempotent."

        def _confirmed(yes: bool) -> None:
            if not yes:
                return
            result = forget_instance(iid)
            self._log(f'forget {iid} → removed {len(result.removed)} files')
            self.refresh_payload()

        self.push_screen(ConfirmScreen(prompt, danger=True), _confirmed)

    def action_relabel(self) -> None:
        inst = self._require_selected()
        if inst is None:
            return
        iid = inst['instance_id']
        current = inst.get('label') or ''

        def _on_input(value: str | None) -> None:
            if value is None:
                return
            new = set_label(iid, value if value.strip() else None)
            self._log(f'label {iid} → {new or "(cleared)"}')
            self.refresh_payload()

        self.push_screen(InputScreen(f'Label for {iid}:', initial=current), _on_input)

    # ─── button events (mouse) ────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            'btn-pause-sent': self.action_sentinel_pause,
            'btn-resume-sent': self.action_sentinel_resume,
            'btn-loops': self.action_loops,
            'btn-relabel': self.action_relabel,
            'btn-kill': self.action_kill,
            'btn-forget': self.action_forget,
            'btn-refresh': self.action_refresh_now,
        }
        handler = actions.get(event.button.id or '')
        if handler:
            handler()

    # ─── helpers ──────────────────────────────────────────────────────────

    def _require_selected(self) -> dict[str, Any] | None:
        inst = self._selected_instance()
        if inst is None:
            self._log('no instance selected')
        return inst

    def _log(self, message: str) -> None:
        try:
            log = self.query_one('#log', Log)
            log.write_line(message)
        except Exception:
            pass


def _current_instance_id() -> str | None:
    """Lazy import to avoid circular dependency at module load."""
    try:
        from empirica.utils.session_resolver import get_instance_id
        return get_instance_id()
    except Exception:
        return None


def run_tui() -> int:
    """Entry point for `empirica tui` CLI command. Returns shell exit code."""
    # Sanity: ensure LoopRegistry import path resolves (helps surface install issues).
    try:
        LoopRegistry.__name__  # noqa: B018  (touch the symbol)
        is_loop_paused  # noqa: B018  (touch the symbol)
    except Exception:
        pass

    try:
        app = CockpitApp()
        app.run()
        return 0
    except KeyboardInterrupt:
        return 130
