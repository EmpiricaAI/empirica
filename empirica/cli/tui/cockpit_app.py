"""empirica tui — portrait Textual cockpit (v1.6.1).

Designed for phone terminals first; tmux split-strip and laptop work too.
Targets ~36 cols × 22 rows minimum, expands gracefully when the terminal
is taller. Reads the same JSON as the CLI (`aggregate_all`); no business
logic duplication.

Vertical layout (single column):
  ┌─ Header   (title + clock)
  │  Summary line   (N inst · ⊕ K notif)
  │  Instance table (one row per live Claude)
  │  Action bar    [P sent] [L loops] [S stop] [N notif]
  │  Statusline    k:.. c:.. conf:..% goals:N — ctx:M%
  │  Open goals    (selected instance, top 5)
  │  Notifications (selected instance, top 5; placeholder for ENP)
  └─ Footer    (key bindings)

Actions (mouse OR keyboard):
  p  toggle Sentinel pause/resume
  l  toggle all loops on/off (cron — periodic work)
  e  toggle all listeners on/off (event-driven work)
  s  stop = remote interrupt (tmux send-keys Escape)
  n  clear all notifications for selected instance
  D  toggle live-only / include-dead view
  r  refresh now
  q  quit
"""

from __future__ import annotations

import textwrap
from argparse import Namespace
from typing import Any, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Static,
)

from empirica.cli.command_handlers.cockpit_commands import (
    handle_listener_install_request_command,
    handle_listener_pause_command,
    handle_listener_resume_command,
    handle_loop_install_request_command,
    handle_loop_pause_command,
    handle_loop_resume_command,
)
from empirica.core.cockpit import (
    LoopRegistry,
    aggregate_all,
    clear_notifications,
    context_usage,
    is_loop_paused,
    notifications_for_project,
    open_goals_list,
    pause_sentinel,
    resume_sentinel,
    statusline_summary,
    stop_instance,
)
from empirica.core.cockpit.project_cockpit_config import (
    project_listeners,
    project_loops,
)

REFRESH_SECONDS = 2.0

# Wrap width for the goals + notifications strips (portrait-friendly).
# Items longer than this wrap onto continuation lines indented under
# the bullet so the visual association is preserved.
_WRAP_WIDTH = 36
# Hard cap per item (David: ~200 chars) to bound widget height per row.
_ITEM_HARD_CAP = 200


class CockpitApp(App):
    """Portrait interactive Empirica cockpit."""

    CSS = """
    Screen { layout: vertical; }

    #summary  { padding: 0 1; height: 1; color: $text-muted; }
    #inst-table { height: auto; min-height: 7; max-height: 12; }

    #action-bar {
        height: 3;
        background: $surface;
        align-horizontal: left;
        padding: 0 1;
    }
    #action-bar Button { margin: 0 1; min-width: 8; }

    #statusline {
        height: 1;
        padding: 0 1;
        color: $primary;
        background: $boost;
    }

    #goals-header   { height: 1; padding: 0 1; color: $text-muted; }
    #goals { height: auto; min-height: 3; max-height: 14; padding: 0 1; }
    #dispatch-banner { height: auto; max-height: 2; padding: 0 1; color: $warning; }
    #notif-header   { height: 1; padding: 0 1; color: $text-muted; }
    #notif { height: auto; min-height: 1; max-height: 7; padding: 0 1; }
    #compliance-header { height: 1; padding: 0 1; color: $text-muted; }
    #compliance { height: auto; min-height: 1; max-height: 8; padding: 0 1; }
    #services-header { height: 1; padding: 0 1; color: $text-muted; }
    #services { height: auto; min-height: 1; max-height: 8; padding: 0 1; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('q', 'quit', 'Quit'),
        Binding('r', 'refresh_now', 'Refresh'),
        Binding('p', 'toggle_sentinel', 'Sent.'),
        Binding('l', 'toggle_loops', 'Loops'),
        Binding('e', 'toggle_listeners', 'Listen'),
        Binding('s', 'stop', 'Stop'),
        Binding('n', 'clear_notifications', 'Notif'),
        Binding('c', 'toggle_compliance', 'Compl.'),
        Binding('i', 'toggle_services', 'Servic.'),
        Binding('D', 'toggle_dead', 'Show dead'),
    ]

    def __init__(self, include_dead: bool = False) -> None:
        super().__init__()
        self.payload: dict[str, Any] = {'instances': [], 'summary': {}, 'generated_at': ''}
        self.selected_instance_id: str | None = None
        self.include_dead = include_dead
        # Compliance + services panel expansion state. Failures /
        # collector errors are ALWAYS shown expanded; the toggle keys
        # (`c` / `i`) only flip the clean / passing case so the operator
        # can drill in but never hide problems.
        self.compliance_expanded: bool = False
        self.services_expanded: bool = False

    # ─── lifecycle ────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        # Failure banner sits directly under the header so it's the first
        # thing the eye lands on when ntfy or another backend is broken.
        yield Static('', id='dispatch-banner')
        yield Static('', id='summary')

        table = DataTable(id='inst-table', cursor_type='row', zebra_stripes=True)
        # 'dom' shows the open transaction's domain + criticality glyph
        # (e.g. 'def·M' for default/medium, 'leg·H' for legal/high). Helps
        # readers see which threshold profile each instance is operating
        # under — a writing project differs from a research/legal one.
        table.add_columns('s', 'name', 'ph', 'dom', 'S', 'L', 'E', 'N')
        yield table

        with Horizontal(id='action-bar'):
            yield Button('P sent', id='btn-sent', variant='warning')
            yield Button('L loops', id='btn-loops', variant='warning')
            yield Button('E listen', id='btn-listen', variant='warning')
            yield Button('S stop', id='btn-stop', variant='error')
            yield Button('N notif', id='btn-notif', variant='primary')

        yield Static('', id='statusline')
        yield Static('open goals', id='goals-header')
        yield Static('(none selected)', id='goals')
        # Notifications-per-project is mid-stack — what David asks for
        # when checking each project's inbox at a glance.
        yield Static('notifications', id='notif-header')
        yield Static('', id='notif')
        # Compliance panel (1.9.0): last `empirica compliance-report`
        # result for the selected instance's project. Green collapsed
        # to one line; yellow/red default-expanded showing failures.
        # Press `c` to toggle expansion.
        yield Static('compliance', id='compliance-header')
        yield Static('', id='compliance')
        # Services panel (Phase 2 T2): last `empirica scan` snapshot for
        # the selected instance's project. Collapsed to a one-line
        # summary by default; press `i` (scanner Inventory) to expand.
        yield Static('services', id='services-header')
        yield Static('', id='services')
        yield Footer()

    def on_mount(self) -> None:
        self.title = 'empirica cockpit'
        self.refresh_payload()
        self.set_interval(REFRESH_SECONDS, self.refresh_payload)

    # ─── data + rendering ─────────────────────────────────────────────────

    def refresh_payload(self) -> None:
        try:
            self.payload = aggregate_all(include_dead=self.include_dead)
        except Exception as e:
            self._log_status(f'refresh failed: {e}')
            return
        self._render_summary()
        self._render_table()
        self._render_selected_widgets()
        self._render_dispatcher()

    def action_toggle_dead(self) -> None:
        self.include_dead = not self.include_dead
        self.refresh_payload()

    def _render_summary(self) -> None:
        s = self.payload.get('summary', {})
        notif_total = s.get('open_notifications', 0)
        ts = self.payload.get('generated_at', '').split('T')[-1].split('+')[0][:5]
        notif_part = f' · ⊕{notif_total}' if notif_total else ''

        # Compact dispatcher status — backend dots + 24h emit count.
        # ●/○ glyphs make it immediately scannable. Default backend wins
        # the brackets so the eye knows where things go by default.
        nd = s.get('notify_dispatcher') or {}
        dispatch_part = ''
        backends = nd.get('backends') or []
        if backends:
            cells: list[str] = []
            default = nd.get('default_backend') or ''
            for b in backends:
                glyph = '●' if b.get('configured') else '○'
                name = b.get('name', '?')
                if name == default:
                    cells.append(f'{glyph}[{name}]')
                else:
                    cells.append(f'{glyph}{name}')
            emit_24h = nd.get('emit_count_24h', 0)
            fb_24h = nd.get('fell_back_count_24h', 0)
            stats = f'24h:{emit_24h}'
            if fb_24h:
                stats += f' fb:{fb_24h}'
            dispatch_part = f' · {" ".join(cells)} {stats}'

        text = (
            f"empirica · {s.get('instances', 0)} inst{notif_part}"
            f"{dispatch_part} · {ts}"
        )
        self.query_one('#summary', Static).update(text)

    def _render_table(self) -> None:
        table = self.query_one('#inst-table', DataTable)
        previously_selected = self.selected_instance_id
        table.clear()
        rows = self.payload.get('instances', [])
        for inst in rows:
            iid = inst['instance_id']
            stat = self._state_glyph(inst['state'])
            name = (inst.get('label') or iid)[:16]
            phase = self._phase_short(inst.get('phase'), inst.get('asking', False))
            dom = self._domain_chip(inst.get('transaction'))
            sentinel = '○' if inst['sentinel']['paused'] else '●'
            loops = self._loops_glyph(inst.get('loops') or {})
            listeners = self._listeners_glyph(inst.get('listeners') or {})
            notif = self._notif_glyph(inst.get('notifications') or {})
            table.add_row(stat, name, phase, dom, sentinel, loops, listeners, notif, key=iid)

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
    def _domain_chip(transaction: dict[str, Any] | None) -> str:
        """Compact domain + criticality chip — 5 chars max for the narrow column.

        Shape: '<dom3>·<crit1>' where dom3 is first three letters of domain
        and crit1 is L/M/H. Closed/missing transactions render as '—'.
        Different domains imply different CHECK thresholds — making that
        legible at a glance is the point of this column.
        """
        if not transaction:
            return '—'
        domain = (transaction.get('domain') or '').strip()
        criticality = (transaction.get('criticality') or '').strip()
        if not domain:
            return '—'
        crit_short = {'low': 'L', 'medium': 'M', 'high': 'H'}.get(
            criticality.lower(), '?',
        )
        return f'{domain[:3]}·{crit_short}'

    @staticmethod
    def _state_glyph(state: str) -> str:
        return {
            'active': '🟢', 'idle': '🟡', 'stuck': '🔴',
            'closed': '⊘', 'no-claude': '⊗',
        }.get(state, '?')

    @staticmethod
    def _phase_short(phase: str | None, asking: bool) -> str:
        """Compress phase to ≤4 chars to fit the narrow column."""
        if asking:
            return 'ask⚠'
        if not phase:
            return '—'
        return {
            'noetic': 'noet', 'praxic': 'prax',
            'closed': 'cls', 'no-transaction': '—',
        }.get(phase, phase[:4])

    @staticmethod
    def _loops_glyph(loops: dict[str, Any]) -> str:
        if not loops:
            return '–'
        paused = sum(1 for v in loops.values() if v.get('paused'))
        if paused == 0:
            return '●'
        if paused == len(loops):
            return '○'
        return '◐'

    @staticmethod
    def _listeners_glyph(listeners: dict[str, Any]) -> str:
        """Same shape as _loops_glyph — listeners are sister concept,
        event-driven instead of cron. ●=all armed, ○=all paused, ◐=mixed,
        –=none registered."""
        if not listeners:
            return '–'
        paused = sum(1 for v in listeners.values() if v.get('paused'))
        if paused == 0:
            return '●'
        if paused == len(listeners):
            return '○'
        return '◐'

    @staticmethod
    def _notif_glyph(notif: dict[str, Any]) -> str:
        count = int(notif.get('open_count', 0) or 0)
        if count == 0:
            return '·'
        return f'⊕{count}'

    def _selected_instance(self) -> dict[str, Any] | None:
        if not self.selected_instance_id:
            return None
        for inst in self.payload.get('instances', []):
            if inst['instance_id'] == self.selected_instance_id:
                return inst
        return None

    def _render_selected_widgets(self) -> None:
        """Statusline + open-goals + notifications + compliance + services
        for the selected instance."""
        inst = self._selected_instance()
        statusline_widget = self.query_one('#statusline', Static)
        goals_widget = self.query_one('#goals', Static)
        notif_widget = self.query_one('#notif', Static)
        compliance_widget = self.query_one('#compliance', Static)
        services_widget = self.query_one('#services', Static)

        if inst is None:
            statusline_widget.update('')
            goals_widget.update('(no instance selected)')
            notif_widget.update('')
            compliance_widget.update('')
            services_widget.update('')
            return

        statusline_widget.update(self._format_statusline(inst))
        goals_widget.update(self._format_goals(inst))
        notif_widget.update(self._format_notifications(inst))
        compliance_widget.update(self._format_compliance(inst))
        services_widget.update(self._format_services(inst))

    def _format_statusline(self, inst: dict[str, Any]) -> str:
        """k:X c:Y conf:Z% goals:N — ctx:M% [PAUSED]

        Sentinel pause state is prepended when active so the operator
        sees off-record status without having to scan the table column.
        """
        ss = statusline_summary(
            inst['instance_id'],
            label_fallback=inst.get('label'),
            project_path=inst.get('project_path'),
            session_id=inst.get('session_id'),
        )
        parts: list[str] = []
        sent = inst.get('sentinel') or {}
        if sent.get('paused'):
            scope = sent.get('scope') or 'instance'
            parts.append(f'PAUSED({scope})')
        if ss.know is not None:
            parts.append(f'k:{ss.know:.2f}')
        if ss.context is not None:
            parts.append(f'c:{ss.context:.2f}')
        if ss.confidence is not None:
            parts.append(f'conf:{int(ss.confidence * 100)}%')
        if ss.open_goals is not None:
            parts.append(f'goals:{ss.open_goals}')
        ctx = context_usage(inst['instance_id'])
        line = ' '.join(parts) if parts else '(no vectors)'
        if ctx is not None:
            line = f'{line} — ctx:{ctx}%'
        return line

    def _format_goals(self, inst: dict[str, Any]) -> str:
        # Project-scoped: passes session_id through but it's ignored by
        # the reader (kept for signature compat).
        goals = open_goals_list(
            inst.get('project_path'), inst.get('session_id'), limit=5,
        )
        if not goals:
            return '(none)'
        return '\n'.join(
            _wrap_item(
                ('⏸' if g.status == 'blocked' else '·'),
                g.objective.replace('\n', ' ').strip(),
            )
            for g in goals
        )

    def _format_notifications(self, inst: dict[str, Any]) -> str:
        items = notifications_for_project(inst.get('project_path'), limit=5)
        if not items:
            return '(none for this project)'
        return '\n'.join(_wrap_item('•', n.title) for n in items)

    def _format_compliance(self, inst: dict[str, Any]) -> str:
        """Header is always-on; `c` toggles a passing-checks list below it.

        Layout David asked for:
          - Header (every render): glyph + N/M + age + ', failing: X, Y'
            when there are failures. Failure names live in the header so
            the operator can never use a key to hide them.
          - Default: header only.
          - On `c`: header + per-passing-check rows (✓ name) so the
            operator can confirm which checks actually ran clean.
            The `c` toggle therefore has visible effect in both pass
            and fail states without ever hiding a failure.
        """
        c = inst.get('compliance')
        if not c:
            return '(no compliance-report run for this project — `empirica compliance-report`)'

        score = c.get('score', 0.0) or 0.0
        passed = c.get('checks_passed', 0)
        total = c.get('checks_total', 0)
        failed = c.get('failed_checks') or []
        passed_names = c.get('passed_check_names') or []
        fresh = c.get('fresh', False)
        age = c.get('age_seconds')

        # Glyph: 🛡 green (all pass) | 🛡 yellow (≥80%) | 🛡 red (<80%)
        if not failed:
            glyph = '🛡 ✓'
        elif score >= 0.8:
            glyph = '🛡 ⚠'
        else:
            glyph = '🛡 ✗'

        if not fresh and age is not None:
            staleness = f' (stale {self._format_age(age)})'
        elif age is not None:
            staleness = f' ({self._format_age(age)} ago)'
        else:
            staleness = ''

        head = f'{glyph} {passed}/{total}{staleness}'
        if failed:
            head += f' · failing: {", ".join(failed)}'

        if not self.compliance_expanded or not passed_names:
            return head

        lines = [head, '']
        for label in passed_names:
            lines.append(_wrap_item('  ✓', label))
        lines.append('  (press `c` to collapse)')
        return '\n'.join(lines)

    @staticmethod
    def _format_age(seconds: float) -> str:
        """Compact age render: 5m, 2h, 3d. Falls back to seconds for short."""
        s = int(seconds)
        if s < 60:
            return f'{s}s'
        if s < 3600:
            return f'{s // 60}m'
        if s < 86400:
            return f'{s // 3600}h'
        return f'{s // 86400}d'

    def _format_services(self, inst: dict[str, Any]) -> str:
        """Last `empirica scan` snapshot summary for the selected project.

        Collapsed (default): one line — glyph + processes + listeners +
        coverage % + age.
        Expanded (`i`): adds breakdowns for MCP servers, plugin manifests,
        cron entries, and interesting env-var name count.
        """
        s = inst.get('services')
        if not s:
            return '(no scanner snapshot for this project — `empirica scan --save`)'

        proc_count = s.get('process_count', 0)
        listening = s.get('listening_ports_count', 0)
        integrity = s.get('integrity_ratio', 0.0)
        errors = s.get('errors_count', 0)
        fresh = s.get('fresh', False)
        age = s.get('age_seconds')

        # Glyph: 🔍 green (clean + fresh) | 🔍 yellow (stale) | 🔍 red (errors)
        if errors > 0:
            glyph = '🔍 ✗'
        elif not fresh:
            glyph = '🔍 ⚠'
        else:
            glyph = '🔍 ✓'

        if not fresh and age is not None:
            staleness = f' (stale {self._format_age(age)})'
        elif age is not None:
            staleness = f' ({self._format_age(age)} ago)'
        else:
            staleness = ''

        head = (
            f'{glyph} {proc_count} procs · {listening} listening · '
            f'integrity {int(integrity * 100)}%{staleness}'
        )
        if errors:
            head += f' · {errors} collector errors'

        # Mirrors compliance: error visibility is preserved by the head line
        # (glyph + `· N collector errors`), and `i` toggles the per-category
        # detail breakdown. Errors default-expand the detail; clean/stale
        # default-collapse it. `services_expanded` means "user toggled."
        if errors > 0:
            show_detail = not self.services_expanded
        else:
            show_detail = self.services_expanded

        if not show_detail:
            return head

        lines = [head, '']
        if errors > 0:
            lines.append(_wrap_item('  ✗', f'{errors} collector error(s) during scan'))
        lines.append(_wrap_item('  ·', f'MCP servers: {s.get("mcp_servers_count", 0)}'))
        lines.append(_wrap_item('  ·', f'Plugin manifests: {s.get("plugin_manifests_count", 0)}'))
        lines.append(_wrap_item('  ·', f'Cron entries: {s.get("cron_entries_count", 0)}'))
        lines.append(_wrap_item('  ·', f'Interesting env-var names: {s.get("env_var_names_count", 0)}'))
        host = s.get('host') or '?'
        lines.append(_wrap_item('  ·', f'Host: {host}'))
        if errors > 0:
            lines.append('  (press `i` to collapse — error count stays in header)')
        else:
            lines.append('  (press `i` to collapse)')
        return '\n'.join(lines)

    def _render_dispatcher(self) -> None:
        """Render the failure banner only — backends + 24h counts now live
        inline in the summary line, recent emits live in `empirica status
        --pretty` (CLI single-instance view) so the TUI's bottom widget
        stays focused on per-project notifications."""
        nd = (self.payload.get('summary', {}) or {}).get('notify_dispatcher') or {}
        banner_widget = self.query_one('#dispatch-banner', Static)
        banner = nd.get('banner_failure')
        if banner:
            backend = banner.get('resolved_backend') or '?'
            age = self._age_short(banner.get('age_seconds'))
            detail = (banner.get('detail') or '').split('\n', 1)[0][:60]
            banner_widget.update(
                f'⚠ notify backend {backend} failed {age} ago — {detail}'
            )
        else:
            banner_widget.update('')

    @staticmethod
    def _age_short(seconds: int | None) -> str:
        if seconds is None:
            return '?'
        if seconds < 60:
            return f'{seconds}s'
        if seconds < 3600:
            return f'{seconds // 60}m'
        return f'{seconds // 3600}h'

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value:
            self.selected_instance_id = str(event.row_key.value)
            self._render_selected_widgets()

    # ─── actions ──────────────────────────────────────────────────────────

    def action_refresh_now(self) -> None:
        self.refresh_payload()

    def action_toggle_sentinel(self) -> None:
        inst = self._require_selected()
        if inst is None:
            return
        if inst['sentinel']['paused']:
            resume_sentinel(inst['instance_id'])
            self._log_status(f'sentinel resumed: {inst["instance_id"]}')
        else:
            pause_sentinel(inst['instance_id'], reason='via tui')
            self._log_status(f'sentinel paused: {inst["instance_id"]}')
        self.refresh_payload()

    def action_toggle_loops(self) -> None:
        """Toggle all loops via the proper command handlers.

        Calls handle_loop_pause_command / handle_loop_resume_command so the
        new mechanical pause-cancels-cron mechanism (1.9.0) fires:
          - pause writes loop_uninstall_pending_*.json containing the
            recorded job_id when scheduler_kind=cron-create
          - the loop-uninstall-pickup hook surfaces it as system-reminder
            on the owning Claude's next prompt asking it to CronDelete
          - body's pause-check at next fire is the backstop

        When no loops are registered, surface a hint pointing at the
        install-request CLI (Phase 2 of TUI work will auto-install from
        a project.yaml canonical-loop config).
        """
        inst = self._require_selected()
        if inst is None:
            return
        loops = inst.get('loops') or {}
        if not loops:
            # No loops registered — first click registers + installs from
            # the project's .empirica/project.yaml cockpit.loops block.
            installed = self._install_loops_from_project(inst)
            if installed:
                self._log_status(
                    f'{inst["instance_id"]}: requested install of {installed} '
                    f'loop(s) from project.yaml — owning Claude will pick up '
                    'via UserPromptSubmit'
                )
                self.refresh_payload()
            else:
                self._log_status(
                    f'{inst["instance_id"]}: no loops registered + no '
                    'cockpit.loops in project.yaml — add a loops: block or '
                    'use `empirica loop install-request --instance ID ...`'
                )
            return
        any_unpaused = any(not v.get('paused') for v in loops.values())
        target_paused = bool(any_unpaused)
        handler = handle_loop_pause_command if target_paused else handle_loop_resume_command
        for name in loops:
            args = Namespace(
                name=name,
                instance=inst['instance_id'],
                output='json',
            )
            try:
                handler(args)
            except Exception as e:
                self._log_status(f'{inst["instance_id"]} {name}: {e}')
                return
        verb = 'paused' if target_paused else 'resumed'
        self._log_status(f'{verb} {len(loops)} loop(s) on {inst["instance_id"]}')
        self.refresh_payload()

    def action_toggle_listeners(self) -> None:
        """Toggle all listeners via the proper command handlers.

        Mirror of action_toggle_loops for event-driven listeners. Calls
        handle_listener_pause_command / handle_listener_resume_command so
        the mechanical Monitor-kill flow fires (writes pending uninstall
        with monitor_task_id; listener-uninstall-pickup hook surfaces it).
        """
        inst = self._require_selected()
        if inst is None:
            return
        listeners = inst.get('listeners') or {}
        if not listeners:
            # No listeners registered — first click registers + installs from
            # the project's .empirica/project.yaml cockpit.listeners block.
            installed = self._install_listeners_from_project(inst)
            if installed:
                self._log_status(
                    f'{inst["instance_id"]}: requested install of {installed} '
                    f'listener(s) from project.yaml — owning Claude will arm '
                    'via UserPromptSubmit'
                )
                self.refresh_payload()
            else:
                self._log_status(
                    f'{inst["instance_id"]}: no listeners registered + no '
                    'cockpit.listeners in project.yaml — add a listeners: '
                    'block or use `empirica listener install-request ...`'
                )
            return
        any_unpaused = any(not v.get('paused') for v in listeners.values())
        target_paused = bool(any_unpaused)
        handler = (
            handle_listener_pause_command if target_paused
            else handle_listener_resume_command
        )
        for name in listeners:
            args = Namespace(
                name=name,
                instance=inst['instance_id'],
                output='json',
            )
            try:
                handler(args)
            except Exception as e:
                self._log_status(f'{inst["instance_id"]} {name}: {e}')
                return
        verb = 'paused' if target_paused else 'resumed'
        self._log_status(f'{verb} {len(listeners)} listener(s) on {inst["instance_id"]}')
        self.refresh_payload()

    def action_stop(self) -> None:
        inst = self._require_selected()
        if inst is None:
            return
        result = stop_instance(inst['instance_id'])
        self._log_status(f'stop {inst["instance_id"]}: {result.detail}')
        self.refresh_payload()

    def action_toggle_services(self) -> None:
        """Flip the services panel between one-line and expanded views.

        Mirrors `c` for compliance: collector errors are always shown
        expanded; this toggle only affects the clean / stale case. `i`
        (scanner Inventory) was chosen because `s` is bound to Stop.
        """
        self.services_expanded = not self.services_expanded
        self._render_selected_widgets()

    def action_toggle_compliance(self) -> None:
        """Flip compliance widget between collapsed and expanded.

        Failures are always shown expanded — this toggle only affects the
        clean / passing case. The operator can drill into "all checks
        passing" with `c` but cannot use `c` to hide a failure.
        """
        self.compliance_expanded = not self.compliance_expanded
        self._render_selected_widgets()

    def action_clear_notifications(self) -> None:
        inst = self._require_selected()
        if inst is None:
            return
        project_path = inst.get('project_path')
        cleared = clear_notifications(inst['instance_id'], project_path=project_path)
        scope = project_path or inst['instance_id']
        if cleared:
            self._log_status(f'cleared {cleared} notif(s) for {scope}')
        else:
            self._log_status(f'no notifications to clear for {scope}')
        self.refresh_payload()

    # ─── button events (mouse / touch) ────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            'btn-sent': self.action_toggle_sentinel,
            'btn-loops': self.action_toggle_loops,
            'btn-listen': self.action_toggle_listeners,
            'btn-stop': self.action_stop,
            'btn-notif': self.action_clear_notifications,
        }
        handler = actions.get(event.button.id or '')
        if handler:
            handler()

    # ─── helpers ──────────────────────────────────────────────────────────

    def _require_selected(self) -> dict[str, Any] | None:
        inst = self._selected_instance()
        if inst is None:
            self._log_status('no instance selected')
        return inst

    def _log_status(self, message: str) -> None:
        """Status messages share the notif widget when nothing else lives there."""
        try:
            notif = self.query_one('#notif', Static)
            notif.update(f'• {message}')
        except Exception:  # noqa: S110 — TUI status nudge is best-effort; failure must not crash the app
            pass

    def _install_loops_from_project(self, inst: dict[str, Any]) -> int:
        """Install loops from the project's cockpit.loops config. Returns
        the count installed. Zero means no config found or all entries
        rejected — caller falls back to a CLI hint."""
        configs = project_loops(inst.get('project_path'))
        if not configs:
            return 0
        installed = 0
        for cfg in configs:
            args = Namespace(
                instance=inst['instance_id'],
                name=cfg['name'],
                kind=cfg.get('kind', 'cron'),
                cron=cfg.get('cron'),
                interval=cfg.get('interval'),
                description=cfg.get('description', ''),
                base_interval=cfg.get('base_interval'),
                max_interval=cfg.get('max_interval'),
                output='json',
            )
            try:
                handle_loop_install_request_command(args)
                installed += 1
            except Exception as e:
                self._log_status(
                    f'{inst["instance_id"]} loop {cfg.get("name", "?")}: {e}'
                )
        return installed

    def _install_listeners_from_project(self, inst: dict[str, Any]) -> int:
        """Install listeners from the project's cockpit.listeners config.
        Returns the count installed."""
        configs = project_listeners(inst.get('project_path'))
        if not configs:
            return 0
        installed = 0
        for cfg in configs:
            args = Namespace(
                instance=inst['instance_id'],
                name=cfg['name'],
                topic=cfg['topic'],
                description=cfg.get('description', ''),
                on_wake=cfg.get('on_wake', ''),
                output='json',
            )
            try:
                handle_listener_install_request_command(args)
                installed += 1
            except Exception as e:
                self._log_status(
                    f'{inst["instance_id"]} listener {cfg.get("name", "?")}: {e}'
                )
        return installed


def _wrap_item(marker: str, text: str, width: int = _WRAP_WIDTH) -> str:
    """Wrap a single bulleted item to multiple lines.

    First line: '{marker} {first chunk}'.
    Continuation lines: indented under the marker so the bullet stays
    visually associated. Hard cap at _ITEM_HARD_CAP chars (David's ~200).
    """
    if not text:
        return marker
    capped = text[:_ITEM_HARD_CAP]
    if len(text) > _ITEM_HARD_CAP:
        capped += '…'

    indent = ' ' * (len(marker) + 1)
    body_width = max(8, width - len(marker) - 1)
    chunks = textwrap.wrap(
        capped, width=body_width, break_long_words=True, break_on_hyphens=False,
    )
    if not chunks:
        return marker
    first, rest = chunks[0], chunks[1:]
    lines = [f'{marker} {first}']
    for chunk in rest:
        lines.append(f'{indent}{chunk}')
    return '\n'.join(lines)


def run_tui(include_dead: bool = False) -> int:
    """Entry point for `empirica tui`. Returns shell exit code."""
    try:
        LoopRegistry.__name__  # noqa: B018
        is_loop_paused  # noqa: B018
    except Exception:  # noqa: S110 — import-warming presence check; deliberately silent
        pass

    try:
        app = CockpitApp(include_dead=include_dead)
        app.run()
        return 0
    except KeyboardInterrupt:
        return 130
