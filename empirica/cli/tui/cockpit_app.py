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
    handle_loop_disable_command,
    handle_loop_enable_command,
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
    wake_instance,
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
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_now", "Refresh"),
        Binding("p", "toggle_sentinel", "Sent."),
        Binding("e", "toggle_events", "Events"),
        Binding("s", "stop", "Stop"),
        Binding("n", "clear_notifications", "Notif"),
        Binding("c", "toggle_compliance", "Compl."),
        Binding("i", "toggle_services", "Servic."),
        Binding("D", "toggle_dead", "Show dead"),
        Binding("a", "toggle_auto_accept", "AutoAcc"),
    ]

    def __init__(self, include_dead: bool = False) -> None:
        super().__init__()
        self.payload: dict[str, Any] = {"instances": [], "summary": {}, "generated_at": ""}
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
        yield Static("", id="dispatch-banner")
        yield Static("", id="summary")

        table = DataTable(id="inst-table", cursor_type="row", zebra_stripes=True)
        # 'dom' shows the open transaction's domain + criticality glyph
        # (e.g. 'def·M' for default/medium, 'leg·H' for legal/high). Helps
        # readers see which threshold profile each instance is operating
        # under — a writing project differs from a research/legal one.
        # T9 (2026-05-15, David): collapsed loops + listeners + notifications
        # into a single 'N' column. The listener (T8) is the unified wake
        # mechanism — separate columns were noise. N now shows ⊕<count>
        # when there are recent events for this instance, glyph otherwise.
        # '#' = tmux window index the seat lives in (Philipp 2026-07-22): rows
        # sort ascending by it so cockpit order mirrors the tmux window layout,
        # making an off/missing seat quick to spot. Blank when unresolvable.
        table.add_columns("s", "#", "name", "ph", "dom", "S", "N")
        yield table

        with Horizontal(id="action-bar"):
            yield Button("P sent", id="btn-sent", variant="warning")
            yield Button("E events", id="btn-events", variant="warning")
            yield Button("S stop", id="btn-stop", variant="error")
            yield Button("N notif", id="btn-notif", variant="primary")

        yield Static("", id="statusline")
        yield Static("open goals", id="goals-header")
        yield Static("(none selected)", id="goals")
        # Notifications-per-project is mid-stack — what David asks for
        # when checking each project's inbox at a glance.
        yield Static("notifications", id="notif-header")
        yield Static("", id="notif")
        # Compliance panel (1.9.6): last `empirica compliance-report`
        # result for the selected instance's project. Green collapsed
        # to one line; yellow/red default-expanded showing failures.
        # Press `c` to toggle expansion.
        yield Static("compliance", id="compliance-header")
        yield Static("", id="compliance")
        # Services panel (Phase 2 T2): last `empirica scan` snapshot for
        # the selected instance's project. Collapsed to a one-line
        # summary by default; press `i` (scanner Inventory) to expand.
        yield Static("services", id="services-header")
        yield Static("", id="services")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "empirica cockpit"
        self.refresh_payload()
        self.set_interval(REFRESH_SECONDS, self.refresh_payload)

    # ─── data + rendering ─────────────────────────────────────────────────

    def refresh_payload(self) -> None:
        try:
            self.payload = aggregate_all(include_dead=self.include_dead)
        except Exception as e:
            self._log_status(f"refresh failed: {e}")
            return
        self._render_summary()
        self._render_table()
        self._render_selected_widgets()
        self._render_dispatcher()

    def action_toggle_dead(self) -> None:
        self.include_dead = not self.include_dead
        self.refresh_payload()

    def action_toggle_auto_accept(self) -> None:
        """T11: flip the per-user auto-accept-mode toggle on cortex.

        ON: proposals from this user's api_key skip ECO review and go
        straight to accepted with eco_decision.actor='auto-mode:<user>'.
        Target AI's listener wakes as if ECO pressed Accept on the phone.
        Per-user (cortex column), so the toggle propagates wherever the
        same api_key is used (this TUI, the extension, any cockpit).

        OFF: normal ECO review path (default).

        Failure (cortex unreachable / endpoint not shipped) surfaces as a
        status-line message; toggle leaves cortex state untouched."""
        from empirica.core.cockpit.auto_accept import (
            fetch_auto_accept_mode,
            set_auto_accept_mode,
        )

        current = fetch_auto_accept_mode(force=True)
        if current is None:
            self._log_status("auto-accept: cortex unreachable or endpoint not shipped yet — toggle no-op")
            return
        new_state = set_auto_accept_mode(not current)
        if new_state is None:
            self._log_status("auto-accept: toggle failed (cortex returned no body / non-2xx)")
            return
        verb = "ENABLED" if new_state else "DISABLED"
        self._log_status(
            f"auto-accept {verb} for this user. "
            f"{'All future cortex_propose emissions auto-accept (no ECO ack).' if new_state else 'ECO review resumed for future emissions.'}"
        )
        self.refresh_payload()

    def _render_summary(self) -> None:
        s = self.payload.get("summary", {})
        notif_total = s.get("open_notifications", 0)
        ts = self.payload.get("generated_at", "").split("T")[-1].split("+")[0][:5]
        notif_part = f" · ⊕{notif_total}" if notif_total else ""

        # Compact dispatcher status — backend dots + 24h emit count.
        # ●/○ glyphs make it immediately scannable. Default backend wins
        # the brackets so the eye knows where things go by default.
        nd = s.get("notify_dispatcher") or {}
        dispatch_part = ""
        backends = nd.get("backends") or []
        if backends:
            cells: list[str] = []
            default = nd.get("default_backend") or ""
            for b in backends:
                glyph = "●" if b.get("configured") else "○"
                name = b.get("name", "?")
                if name == default:
                    cells.append(f"{glyph}[{name}]")
                else:
                    cells.append(f"{glyph}{name}")
            emit_24h = nd.get("emit_count_24h", 0)
            fb_24h = nd.get("fell_back_count_24h", 0)
            stats = f"24h:{emit_24h}"
            if fb_24h:
                stats += f" fb:{fb_24h}"
            dispatch_part = f" · {' '.join(cells)} {stats}"

        # Auto-accept chip — always visible so the bypass state is never
        # ambiguous (was prev hidden when OFF, which made "is bypass on?"
        # require pressing 'a'). Loud ⚡ glyph when ON (no ECO ack
        # required for emissions from this user); muted text when OFF;
        # hidden only when state is unknown (cortex unreachable / endpoint
        # not shipped — distinguishing "off" from "unknown" matters).
        auto_accept = s.get("auto_accept")
        if auto_accept is True:
            auto_part = " · ⚡AUTO-ACCEPT"
        elif auto_accept is False:
            auto_part = " · auto-accept:off"
        else:
            auto_part = ""

        text = f"empirica · {s.get('instances', 0)} inst{notif_part}{auto_part}{dispatch_part} · {ts}"
        self.query_one("#summary", Static).update(text)

    def _render_table(self) -> None:
        table = self.query_one("#inst-table", DataTable)
        previously_selected = self.selected_instance_id
        table.clear()
        rows = self.payload.get("instances", [])
        # Sort ascending by tmux window index so cockpit order mirrors the
        # operator's tmux window layout; seats with no resolvable window sort
        # last (then by name for a stable, readable order).
        rows = sorted(
            rows,
            key=lambda i: (
                i.get("tmux_window") is None,
                i.get("tmux_window") if i.get("tmux_window") is not None else 0,
                (i.get("label") or i["instance_id"]).lower(),
            ),
        )
        for inst in rows:
            iid = inst["instance_id"]
            stat = self._state_glyph(inst["state"])
            win = inst.get("tmux_window")
            win_cell = str(win) if win is not None else "·"
            name = (inst.get("label") or iid)[:16]
            phase = self._phase_short(inst.get("phase"), inst.get("asking", False))
            dom = self._domain_chip(inst.get("transaction"))
            sentinel = "○" if inst["sentinel"]["paused"] else "●"
            # T9: events cell = recent_events count (most actionable signal)
            # falling back to the loops glyph if no events yet. listeners
            # are subsumed (they're loops with held connections now).
            events_cell = self._events_cell(inst)
            table.add_row(stat, win_cell, name, phase, dom, sentinel, events_cell, key=iid)

        if rows:
            target = previously_selected or rows[0]["instance_id"]
            for idx, inst in enumerate(rows):
                if inst["instance_id"] == target:
                    table.move_cursor(row=idx)
                    self.selected_instance_id = target
                    break
            else:
                table.move_cursor(row=0)
                self.selected_instance_id = rows[0]["instance_id"]
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
            return "—"
        domain = (transaction.get("domain") or "").strip()
        criticality = (transaction.get("criticality") or "").strip()
        if not domain:
            return "—"
        crit_short = {"low": "L", "medium": "M", "high": "H"}.get(
            criticality.lower(),
            "?",
        )
        return f"{domain[:3]}·{crit_short}"

    @staticmethod
    def _state_glyph(state: str) -> str:
        return {
            "active": "🟢",
            "idle": "🟡",
            "stuck": "🔴",
            "closed": "⊘",
            "no-claude": "⊗",
        }.get(state, "?")

    @staticmethod
    def _phase_short(phase: str | None, asking: bool) -> str:
        """Compress phase to ≤4 chars to fit the narrow column."""
        if asking:
            return "ask⚠"
        if not phase:
            return "—"
        return {
            "noetic": "noet",
            "praxic": "prax",
            "closed": "cls",
            "no-transaction": "—",
        }.get(phase, phase[:4])

    @staticmethod
    def _loop_is_off(v: dict[str, Any]) -> bool:
        """A loop is 'off' if its systemd timer is inactive (when systemd-managed),
        or its pause sidecar exists (legacy file-flag path). Phase 1c-tail:
        single source of truth that handles both scheduler kinds.

        VALID_SCHEDULER_KIND uses 'systemd-user' (loop_registry.py line 33);
        match by prefix so any 'systemd*' variant routes through the systemctl
        liveness check instead of falling through to file-flag pause.
        """
        if (v.get("scheduler_kind") or "").lower().startswith("systemd"):
            # systemd_active absent → unknown → treat as off (conservative)
            return not v.get("systemd_active", False)
        return bool(v.get("paused"))

    @staticmethod
    def _loops_glyph(loops: dict[str, Any]) -> str:
        if not loops:
            return "–"
        off = sum(1 for v in loops.values() if CockpitApp._loop_is_off(v))
        if off == 0:
            return "●"
        if off == len(loops):
            return "○"
        return "◐"

    @staticmethod
    def _listeners_glyph(listeners: dict[str, Any]) -> str:
        """Same shape as _loops_glyph — listeners are sister concept,
        event-driven instead of cron. ●=all armed, ○=all paused, ◐=mixed,
        –=none registered."""
        if not listeners:
            return "–"
        paused = sum(1 for v in listeners.values() if v.get("paused"))
        if paused == 0:
            return "●"
        if paused == len(listeners):
            return "○"
        return "◐"

    @staticmethod
    def _notif_glyph(notif: dict[str, Any]) -> str:
        count = int(notif.get("open_count", 0) or 0)
        if count == 0:
            return "·"
        return f"⊕{count}"

    @staticmethod
    def _events_cell(inst: dict[str, Any]) -> str:
        """T9: unified events cell — replaces separate loops/listeners/notif
        columns. Priority: recent-events count if any, else loop liveness."""
        recent = inst.get("recent_events") or []
        if recent:
            # Show count of latest events as the wake-summary chip
            return f"⊕{len(recent)}"
        # No recent events — fall back to loop liveness (T5 _loops_glyph)
        loops = inst.get("loops") or {}
        if loops:
            return CockpitApp._loops_glyph(loops)
        # Also check listeners — registered but no recent events
        listeners = inst.get("listeners") or {}
        if listeners:
            return CockpitApp._listeners_glyph(listeners)
        return "·"

    def _selected_instance(self) -> dict[str, Any] | None:
        if not self.selected_instance_id:
            return None
        for inst in self.payload.get("instances", []):
            if inst["instance_id"] == self.selected_instance_id:
                return inst
        return None

    def _render_selected_widgets(self) -> None:
        """Statusline + open-goals + notifications + compliance + services
        for the selected instance."""
        inst = self._selected_instance()
        statusline_widget = self.query_one("#statusline", Static)
        goals_widget = self.query_one("#goals", Static)
        notif_widget = self.query_one("#notif", Static)
        compliance_widget = self.query_one("#compliance", Static)
        services_widget = self.query_one("#services", Static)

        if inst is None:
            statusline_widget.update("")
            goals_widget.update("(no instance selected)")
            notif_widget.update("")
            compliance_widget.update("")
            services_widget.update("")
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
            inst["instance_id"],
            label_fallback=inst.get("label"),
            project_path=inst.get("project_path"),
            session_id=inst.get("session_id"),
        )
        parts: list[str] = []
        sent = inst.get("sentinel") or {}
        if sent.get("paused"):
            scope = sent.get("scope") or "instance"
            parts.append(f"PAUSED({scope})")
        if ss.know is not None:
            parts.append(f"k:{ss.know:.2f}")
        if ss.context is not None:
            parts.append(f"c:{ss.context:.2f}")
        if ss.confidence is not None:
            parts.append(f"conf:{int(ss.confidence * 100)}%")
        if ss.open_goals is not None:
            parts.append(f"goals:{ss.open_goals}")
        ctx = context_usage(inst["instance_id"])
        line = " ".join(parts) if parts else "(no vectors)"
        if ctx is not None:
            line = f"{line} — ctx:{ctx}%"
        return line

    def _format_goals(self, inst: dict[str, Any]) -> str:
        # Project-scoped: passes session_id through but it's ignored by
        # the reader (kept for signature compat).
        goals = open_goals_list(
            inst.get("project_path"),
            inst.get("session_id"),
            limit=5,
        )
        if not goals:
            return "(none)"
        return "\n".join(
            _wrap_item(
                ("⏸" if g.status == "blocked" else "·"),
                g.objective.replace("\n", " ").strip(),
            )
            for g in goals
        )

    def _format_notifications(self, inst: dict[str, Any]) -> str:
        # T9: prefer fires-log recent_events (the listener's wake events) —
        # those are the actionable AI-orchestration signals. Fall back to
        # project notifications (the older project-level audit notifs) when
        # no event stream activity exists yet for this instance.
        recent = inst.get("recent_events") or []
        if recent:
            lines = []
            for ev in recent:
                lines.append(_format_event_line(ev))
            return "\n".join(lines)
        items = notifications_for_project(inst.get("project_path"), limit=5)
        if not items:
            return "(no events yet — listener silent or not armed)"
        return "\n".join(_wrap_item("•", n.title) for n in items)

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
        c = inst.get("compliance")
        if not c:
            return "(no compliance-report run for this project — `empirica compliance-report`)"

        score = c.get("score", 0.0) or 0.0
        passed = c.get("checks_passed", 0)
        total = c.get("checks_total", 0)
        failed = c.get("failed_checks") or []
        passed_names = c.get("passed_check_names") or []
        fresh = c.get("fresh", False)
        age = c.get("age_seconds")

        # Glyph: 🛡 green (all pass) | 🛡 yellow (≥80%) | 🛡 red (<80%)
        if not failed:
            glyph = "🛡 ✓"
        elif score >= 0.8:
            glyph = "🛡 ⚠"
        else:
            glyph = "🛡 ✗"

        if not fresh and age is not None:
            staleness = f" (stale {self._format_age(age)})"
        elif age is not None:
            staleness = f" ({self._format_age(age)} ago)"
        else:
            staleness = ""

        head = f"{glyph} {passed}/{total}{staleness}"
        if failed:
            head += f" · failing: {', '.join(failed)}"

        if not self.compliance_expanded or not passed_names:
            return head

        lines = [head, ""]
        for label in passed_names:
            lines.append(_wrap_item("  ✓", label))
        lines.append("  (press `c` to collapse)")
        return "\n".join(lines)

    @staticmethod
    def _format_age(seconds: float) -> str:
        """Compact age render: 5m, 2h, 3d. Falls back to seconds for short."""
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m"
        if s < 86400:
            return f"{s // 3600}h"
        return f"{s // 86400}d"

    def _format_services(self, inst: dict[str, Any]) -> str:
        """Last `empirica scan` snapshot summary for the selected project.

        Collapsed (default): one line — glyph + processes + listeners +
        coverage % + age.
        Expanded (`i`): adds breakdowns for MCP servers, plugin manifests,
        cron entries, and interesting env-var name count.
        """
        s = inst.get("services")
        if not s:
            return "(no scanner snapshot for this project — `empirica scan --save`)"

        proc_count = s.get("process_count", 0)
        listening = s.get("listening_ports_count", 0)
        integrity = s.get("integrity_ratio", 0.0)
        errors = s.get("errors_count", 0)
        fresh = s.get("fresh", False)
        age = s.get("age_seconds")

        # Glyph: 🔍 green (clean + fresh) | 🔍 yellow (stale) | 🔍 red (errors)
        if errors > 0:
            glyph = "🔍 ✗"
        elif not fresh:
            glyph = "🔍 ⚠"
        else:
            glyph = "🔍 ✓"

        if not fresh and age is not None:
            staleness = f" (stale {self._format_age(age)})"
        elif age is not None:
            staleness = f" ({self._format_age(age)} ago)"
        else:
            staleness = ""

        head = f"{glyph} {proc_count} procs · {listening} listening · integrity {int(integrity * 100)}%{staleness}"
        if errors:
            head += f" · {errors} collector errors"

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

        lines = [head, ""]
        if errors > 0:
            lines.append(_wrap_item("  ✗", f"{errors} collector error(s) during scan"))
        lines.append(_wrap_item("  ·", f"MCP servers: {s.get('mcp_servers_count', 0)}"))
        lines.append(_wrap_item("  ·", f"Plugin manifests: {s.get('plugin_manifests_count', 0)}"))
        lines.append(_wrap_item("  ·", f"Cron entries: {s.get('cron_entries_count', 0)}"))
        lines.append(_wrap_item("  ·", f"Interesting env-var names: {s.get('env_var_names_count', 0)}"))
        host = s.get("host") or "?"
        lines.append(_wrap_item("  ·", f"Host: {host}"))
        if errors > 0:
            lines.append("  (press `i` to collapse — error count stays in header)")
        else:
            lines.append("  (press `i` to collapse)")
        return "\n".join(lines)

    def _render_dispatcher(self) -> None:
        """Render the failure banner only — backends + 24h counts now live
        inline in the summary line, recent emits live in `empirica status
        --pretty` (CLI single-instance view) so the TUI's bottom widget
        stays focused on per-project notifications."""
        nd = (self.payload.get("summary", {}) or {}).get("notify_dispatcher") or {}
        banner_widget = self.query_one("#dispatch-banner", Static)
        banner = nd.get("banner_failure")
        if banner:
            backend = banner.get("resolved_backend") or "?"
            age = self._age_short(banner.get("age_seconds"))
            detail = (banner.get("detail") or "").split("\n", 1)[0][:60]
            banner_widget.update(f"⚠ notify backend {backend} failed {age} ago — {detail}")
        else:
            banner_widget.update("")

    @staticmethod
    def _age_short(seconds: int | None) -> str:
        if seconds is None:
            return "?"
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m"
        return f"{seconds // 3600}h"

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
        if inst["sentinel"]["paused"]:
            resume_sentinel(inst["instance_id"])
            self._log_status(f"sentinel resumed: {inst['instance_id']}")
        else:
            pause_sentinel(inst["instance_id"], reason="via tui")
            self._log_status(f"sentinel paused: {inst['instance_id']}")
        self.refresh_payload()

    def action_toggle_events(self) -> None:
        """Toggle all event sources (loops + listeners) for the selected row.

        Collapses the previous L (loops) + E (listeners) bindings into one
        Events button. Each press flips both subsystems together — they're
        two halves of the same wake-from-idle mechanism (loop schedules
        the catch-up tick, listener holds the ntfy push connection).

        Per-row, not global: the cockpit shows one row per Claude instance
        and each instance has its own systemd timer + listener registry.

        Empty-state behavior: when nothing is registered yet for this
        instance, attempts to bootstrap from `.empirica/project.yaml`'s
        cockpit.loops + cockpit.listeners blocks via the install-request
        flow (UserPromptSubmit pickup by the owning Claude).
        """
        inst = self._require_selected()
        if inst is None:
            return
        loops = inst.get("loops") or {}
        listeners = inst.get("listeners") or {}

        # Empty registries on both sides — try project.yaml bootstrap.
        if not loops and not listeners:
            installed_l = self._install_loops_from_project(inst)
            installed_e = self._install_listeners_from_project(inst)
            total = installed_l + installed_e
            if total:
                # Active wake — without this, the AI doesn't process the
                # pending install until the user manually prompts. With it,
                # press-Events-and-it-just-works: Space+Enter into the pane
                # triggers UserPromptSubmit hooks that surface the pending
                # state as a system-reminder, AI processes immediately.
                wake = wake_instance(inst["instance_id"])
                wake_note = " · woke target pane" if wake.success else f" · wake unreachable ({wake.detail})"
                self._log_status(
                    f"{inst['instance_id']}: requested install of "
                    f"{installed_l} loop(s) + {installed_e} listener(s)"
                    f"{wake_note}"
                )
                self.refresh_payload()
            else:
                self._log_status(
                    f"{inst['instance_id']}: no events registered + no "
                    "cockpit.loops/listeners in project.yaml — add the "
                    "blocks or use install-request CLI"
                )
            return

        # Decide target state from current state: any-unpaused → pause all.
        any_unpaused = any(not v.get("paused") for v in loops.values()) or any(
            not v.get("paused") for v in listeners.values()
        )
        target_paused = bool(any_unpaused)

        # Loops first (scheduler-aware): systemd-kind uses systemctl
        # enable/disable, legacy cron-create stays on file-flag pause.
        # VALID_SCHEDULER_KIND uses 'systemd-user'; match by prefix so
        # any systemd* variant routes through systemctl.
        #
        # Use ai_id for systemd timer naming (stable across tmux restarts);
        # legacy cron-create paths still use instance_id (tied to the
        # CronCreate in the current AI session, which IS tmux-pane-bound).
        from empirica.utils.session_resolver import InstanceResolver

        ai_id_for_timer = (
            inst.get("ai_id") or InstanceResolver.ai_id(project_path=inst.get("project_path")) or inst["instance_id"]
        )
        for name, loop_data in loops.items():
            scheduler_kind = (loop_data.get("scheduler_kind") or "").lower()
            if scheduler_kind.startswith("systemd"):
                handler = handle_loop_disable_command if target_paused else handle_loop_enable_command
                args_dict = {
                    "name": name,
                    "instance": ai_id_for_timer,
                    "output": "json",
                }
                if not target_paused:
                    args_dict["interval"] = loop_data.get("interval") or loop_data.get("base_interval") or "30s"
                args = Namespace(**args_dict)
            else:
                # Loops are practice-keyed now (registered/enabled under ai_id via
                # _require_loop_key), so pause/resume must target the same
                # ai_id_for_timer as the systemd branch — not the ephemeral seat.
                handler = handle_loop_pause_command if target_paused else handle_loop_resume_command
                args = Namespace(
                    name=name,
                    instance=ai_id_for_timer,
                    output="json",
                )
            try:
                handler(args)
            except Exception as e:
                self._log_status(f"{inst['instance_id']} loop {name}: {e}")
                return

        # Then listeners (mirror mechanical pause flow).
        listener_handler = handle_listener_pause_command if target_paused else handle_listener_resume_command
        for name in listeners:
            args = Namespace(
                name=name,
                instance=inst["instance_id"],
                output="json",
            )
            try:
                listener_handler(args)
            except Exception as e:
                self._log_status(f"{inst['instance_id']} listener {name}: {e}")
                return

        verb = "paused" if target_paused else "resumed"
        self._log_status(f"{verb} {len(loops)} loop(s) + {len(listeners)} listener(s) on {inst['instance_id']}")
        self.refresh_payload()

    def action_stop(self) -> None:
        inst = self._require_selected()
        if inst is None:
            return
        result = stop_instance(inst["instance_id"])
        self._log_status(f"stop {inst['instance_id']}: {result.detail}")
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
        project_path = inst.get("project_path")
        cleared = clear_notifications(inst["instance_id"], project_path=project_path)
        scope = project_path or inst["instance_id"]
        if cleared:
            self._log_status(f"cleared {cleared} notif(s) for {scope}")
        else:
            self._log_status(f"no notifications to clear for {scope}")
        self.refresh_payload()

    # ─── button events (mouse / touch) ────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "btn-sent": self.action_toggle_sentinel,
            "btn-events": self.action_toggle_events,
            "btn-stop": self.action_stop,
            "btn-notif": self.action_clear_notifications,
        }
        handler = actions.get(event.button.id or "")
        if handler:
            handler()

    # ─── helpers ──────────────────────────────────────────────────────────

    def _require_selected(self) -> dict[str, Any] | None:
        inst = self._selected_instance()
        if inst is None:
            self._log_status("no instance selected")
        return inst

    def _log_status(self, message: str) -> None:
        """Status messages share the notif widget when nothing else lives there."""
        try:
            notif = self.query_one("#notif", Static)
            notif.update(f"• {message}")
        except Exception:  # noqa: S110 — TUI status nudge is best-effort; failure must not crash the app
            pass

    def _install_loops_from_project(self, inst: dict[str, Any]) -> int:
        """Install loops from the project's cockpit.loops config, falling
        back to the system-level canonical catalog if no project config
        is present. Returns the count installed. Zero means both sources
        were empty or all entries rejected — caller falls back to a
        CLI hint.

        Precedence: project.yaml first (project-specific intent wins),
        then canonical_loops.CANONICAL_LOOPS (sane default for any
        empirica claude — currently the cortex-mailbox-poll orchestration
        spine).
        """
        configs = project_loops(inst.get("project_path"))
        source_label = "project.yaml"
        if not configs:
            from empirica.core.cockpit.canonical_loops import CANONICAL_LOOPS

            configs = list(CANONICAL_LOOPS)
            source_label = "canonical catalog"
            if not configs:
                return 0
        installed = 0
        # For systemd-user timer naming, use the stable ai_id (project
        # basename) rather than the ephemeral tmux pane id. The
        # session-monitor-arm hook also queries by ai_id — both must
        # agree or the hook finds no timer and emits empty
        # additionalContext. ai_id is sourced from project.yaml's
        # `ai_id` field (set by setup-claude-code via _derive_ai_id);
        # fallback to project basename, then to pane instance_id for
        # pre-convention installs.
        from empirica.utils.session_resolver import InstanceResolver

        timer_instance = (
            inst.get("ai_id") or InstanceResolver.ai_id(project_path=inst.get("project_path")) or inst["instance_id"]
        )
        for cfg in configs:
            scheduler_kind = (cfg.get("scheduler_kind") or "").lower()
            try:
                # canonical_loops.py declares 'systemd-user' per the
                # VALID_SCHEDULER_KIND alphabet — match by prefix so any
                # systemd* variant routes through systemctl install.
                if scheduler_kind.startswith("systemd"):
                    # Phase 1c: systemd-scheduled loops install via systemctl
                    # directly. No pending file, no AI cooperation needed —
                    # the timer starts immediately. SessionStart's monitor-arm
                    # hook (Phase 1b) wires the wake bridge on next session.
                    args = Namespace(
                        instance=timer_instance,
                        name=cfg["name"],
                        interval=cfg.get("interval") or cfg.get("base_interval") or "30s",
                        description=cfg.get("description", ""),
                        output="json",
                    )
                    handle_loop_enable_command(args)
                else:
                    args = Namespace(
                        instance=inst["instance_id"],
                        name=cfg["name"],
                        kind=cfg.get("kind", "cron"),
                        cron=cfg.get("cron"),
                        interval=cfg.get("interval"),
                        description=cfg.get("description", ""),
                        base_interval=cfg.get("base_interval"),
                        max_interval=cfg.get("max_interval"),
                        # Canonical loops carry an optional `body_skill` — the
                        # handler uses it to substitute the skill's actual
                        # prompt template instead of the generic placeholder.
                        body_skill=cfg.get("body_skill"),
                        output="json",
                    )
                    handle_loop_install_request_command(args)
                installed += 1
            except Exception as e:
                self._log_status(f"{inst['instance_id']} loop {cfg.get('name', '?')}: {e}")
        if installed:
            self._log_status(f"{inst['instance_id']}: installed {installed} loop(s) from {source_label}")
        return installed

    def _install_listeners_from_project(self, inst: dict[str, Any]) -> int:
        """Install listeners from the project's cockpit.listeners config.
        Returns the count installed."""
        configs = project_listeners(inst.get("project_path"))
        if not configs:
            return 0
        installed = 0
        for cfg in configs:
            args = Namespace(
                instance=inst["instance_id"],
                name=cfg["name"],
                topic=cfg["topic"],
                description=cfg.get("description", ""),
                on_wake=cfg.get("on_wake", ""),
                output="json",
            )
            try:
                handle_listener_install_request_command(args)
                installed += 1
            except Exception as e:
                self._log_status(f"{inst['instance_id']} listener {cfg.get('name', '?')}: {e}")
        return installed


def _format_event_line(ev: dict[str, Any]) -> str:
    """T9: format one ProposalEvent for the latest-5 cockpit pane.

    Shape per event:  <dir>·<status> <id8> · <eco_actor>  <title>
      ▼·accepted prop_efs… · eco-phone  Close the push gap…
      ▲·completed prop_ox6… · extension  Add completion primitive

    Direction glyphs:
      ▼ inbox (proposal TO this AI — AI acts)
      ▲ outbox (proposal FROM this AI — ack received)
      • unknown / legacy heartbeat
    """
    direction = (ev.get("direction") or "").lower()
    dir_glyph = "▼" if direction == "inbox" else "▲" if direction == "outbox" else "•"
    status = ev.get("status", "")
    pid = (ev.get("proposal_id") or "")[:8]
    eco = ev.get("eco_actor") or ev.get("commit_sha") or ""
    title = (ev.get("proposal_title") or ev.get("loop") or "")[:48]
    suffix = f" · {eco}" if eco else ""
    return _wrap_item(f"{dir_glyph}·{status}", f"{pid}{suffix}  {title}")


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
        capped += "…"

    indent = " " * (len(marker) + 1)
    body_width = max(8, width - len(marker) - 1)
    chunks = textwrap.wrap(
        capped,
        width=body_width,
        break_long_words=True,
        break_on_hyphens=False,
    )
    if not chunks:
        return marker
    first, rest = chunks[0], chunks[1:]
    lines = [f"{marker} {first}"]
    for chunk in rest:
        lines.append(f"{indent}{chunk}")
    return "\n".join(lines)


def run_tui(include_dead: bool = False) -> int:
    """Entry point for `empirica tui`. Returns shell exit code."""
    try:
        LoopRegistry.__name__  # noqa: B018  # pyright: ignore[reportUnusedExpression]
        is_loop_paused  # noqa: B018  # pyright: ignore[reportUnusedExpression]
    except Exception:  # noqa: S110 — import-warming presence check; deliberately silent
        pass

    try:
        app = CockpitApp(include_dead=include_dead)
        app.run()
        return 0
    except KeyboardInterrupt:
        return 130
