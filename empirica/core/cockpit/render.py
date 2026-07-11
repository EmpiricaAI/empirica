"""ANSI + JSON renderers for `empirica status`.

The `--json` form is the source of truth all renderers consume — pretty is
just a colored projection of the same dict. Keep both in sync.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

# Glyphs we use that render two visual columns in monospace terminals.
# Most colored circle emojis are East Asian Wide; ⊘ and ⊗ are narrow.
_WIDE_GLYPHS = frozenset("🟢🟡🔴🟠🟣🟤🟦🟧🟨🟩🟪🟫🔵🔶🔷◆●○◐⚠")


def _visible_len(text: str) -> int:
    """Visible column count, ANSI-stripped, with wide-glyph awareness."""
    stripped = _ANSI_RE.sub("", text)
    extra = sum(1 for ch in stripped if ch in _WIDE_GLYPHS)
    return len(stripped) + extra


def _pad(text: str, width: int) -> str:
    """Pad text to width using visible character count (ANSI-aware)."""
    pad = max(0, width - _visible_len(text))
    return text + (" " * pad)


# ANSI color constants — kept tiny on purpose. We don't want a curses
# dependency for what is fundamentally a status line.
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_CYAN = "\033[36m"
_GRAY = "\033[90m"

# Mapping: instance state → (symbol, color)
_STATE_GLYPH = {
    "active": ("🟢", _GREEN, "active"),
    "idle": ("🟡", _YELLOW, "idle"),
    "stuck": ("🔴", _RED, "stuck"),
    "closed": ("⊘", _GRAY, "closed"),
    "no-claude": ("⊗", _GRAY, "no-claude"),
}

_STALE_LOOP_FACTOR = 2.0  # last_run age > 2× interval → stale warning


def _color_enabled() -> bool:
    """Use ANSI colors only when stdout is a TTY and NO_COLOR is unset."""
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _c(text: str, color: str, enabled: bool) -> str:
    if not enabled or not color:
        return text
    return f"{color}{text}{_RESET}"


def _humanize_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86_400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86_400)}d"


def _short_id(uid: str | None, n: int = 8) -> str:
    if not uid:
        return "—"
    return uid.split("-")[0][:n] if "-" in uid else uid[:n]


def render_json(payload: dict[str, Any]) -> str:
    """Serialize the cockpit payload as a JSON string."""
    return json.dumps(payload, indent=2, sort_keys=False)


def _interval_to_seconds(interval: str | None) -> float | None:
    """Parse '5m' / '30s' / '2h' / '1d' to seconds. Returns None on failure."""
    if not interval or not isinstance(interval, str):
        return None
    interval = interval.strip().lower()
    if not interval:
        return None
    suffix_map = {"s": 1, "m": 60, "h": 3600, "d": 86_400}
    suffix = interval[-1]
    if suffix in suffix_map:
        try:
            return float(interval[:-1]) * suffix_map[suffix]
        except ValueError:
            return None
    try:
        return float(interval) * 60  # bare number → minutes
    except ValueError:
        return None


def _loop_age_seconds(loop: dict[str, Any]) -> float | None:
    last_run = loop.get("last_run")
    if not last_run:
        return None
    try:
        dt = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(tz=timezone.utc) - dt).total_seconds()
    except (ValueError, TypeError):
        return None


def _loop_is_stale(loop: dict[str, Any]) -> bool:
    interval_s = _interval_to_seconds(loop.get("interval"))
    if interval_s is None or interval_s <= 0:
        return False
    age = _loop_age_seconds(loop)
    if age is None:
        return False
    return age > interval_s * _STALE_LOOP_FACTOR


def _loops_summary(loops: dict[str, Any], color: bool) -> str:
    """Render '●● 2/2' style loop summary."""
    if not loops:
        return _c("○ 0/0", _GRAY, color)

    paused = sum(1 for loop in loops.values() if loop.get("paused"))
    stale = any(_loop_is_stale(loop) for loop in loops.values())
    total = len(loops)
    active = total - paused

    if paused == total:
        glyph = _c("○", _RED, color)
    elif paused == 0:
        glyph = _c("●" * min(active, 3), _GREEN, color)
    else:
        glyph = _c("◐", _YELLOW, color)

    suffix = " ⚠" if stale else ""
    suffix_colored = _c(suffix, _YELLOW, color) if suffix else ""
    return f"{glyph} {active}/{total}{suffix_colored}"


def _sentinel_cell(sentinel: dict[str, Any], color: bool) -> str:
    if sentinel.get("paused"):
        return f"{_c('○', _RED, color)} PAUSED"
    return f"{_c('●', _GREEN, color)} ON"


def _phase_cell(phase: str, color: bool) -> str:
    if phase == "noetic":
        return _c("noetic", _CYAN, color)
    if phase == "praxic":
        return _c("praxic", _GREEN, color)
    if phase == "closed":
        return _c("closed", _GRAY, color)
    return _c("—", _GRAY, color)


def _state_cell(state: str, color: bool) -> str:
    glyph, c, name = _STATE_GLYPH.get(state, ("?", _GRAY, state))
    return f"{glyph} {_c(name, c, color)}"


def _tx_cell(transaction: dict[str, Any] | None, color: bool) -> str:
    if not transaction:
        return _c("—", _GRAY, color)
    age_s = transaction.get("age_seconds")
    age_str = _humanize_seconds(age_s)
    if age_s is None:
        age_color = ""
    elif age_s > 30 * 60:
        age_color = _RED
    elif age_s > 10 * 60:
        age_color = _YELLOW
    else:
        age_color = ""
    age_part = _c(age_str, age_color, color) if age_color else age_str
    return f"{_short_id(transaction.get('id'))} {age_part}"


def _humanize_seconds_short(seconds: int | None) -> str:
    """Compact age string for banner ('14m', '2h'). None → '?'."""
    if seconds is None:
        return "?"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def _failure_banner(notify_dispatcher: dict[str, Any], color: bool) -> str | None:
    """Return the cockpit-header failure banner string, or None.

    Renders only when banner_failure is present (failure within last hour).
    """
    if not notify_dispatcher:
        return None
    bf = notify_dispatcher.get("banner_failure")
    if not bf:
        return None
    backend = bf.get("resolved_backend") or "?"
    age = _humanize_seconds_short(bf.get("age_seconds"))
    detail = (bf.get("detail") or "").split("\n", 1)[0][:80]
    msg = f"⚠ notify backend {backend} failed {age} ago — {detail}"
    return _c(msg, _YELLOW, color)


def _notify_dispatcher_block(
    notify_dispatcher: dict[str, Any],
    color: bool,
) -> list[str]:
    """Render the notify dispatcher detail block (single-instance view)."""
    if not notify_dispatcher or not notify_dispatcher.get("default_backend"):
        return []

    out: list[str] = []
    default_backend = notify_dispatcher.get("default_backend") or "?"
    emit_24h = notify_dispatcher.get("emit_count_24h", 0)
    fb_24h = notify_dispatcher.get("fell_back_count_24h", 0)
    header_summary = f"default: {default_backend}   24h: {emit_24h} emits, {fb_24h} fallback"
    out.append(f"Notify dispatcher  {_c(header_summary, _DIM, color)}")

    backend_cells: list[str] = []
    for b in notify_dispatcher.get("backends", []):
        glyph = _c("●", _GREEN, color) if b.get("configured") else _c("○", _RED, color)
        name = b.get("name", "?")
        bits = [name]
        if b.get("is_default"):
            bits.append("(default)")
        if b.get("name") == "ntfy":
            auth = b.get("auth_method") or "none"
            srv = b.get("server")
            if srv:
                # Trim scheme for compact display.
                trimmed = srv.replace("https://", "").replace("http://", "")
                bits.append(f"{auth} @{trimmed}")
            else:
                bits.append(auth)
        backend_cells.append(f"{glyph} {' '.join(bits)}")
    if backend_cells:
        out.append(f"  Backends   {'   '.join(backend_cells)}")

    recent = notify_dispatcher.get("recent") or []
    if recent:
        out.append("  Recent")
        for r in recent:
            ts_raw = r.get("ts", "")
            # Trim ISO ts to HH:MM:SS for compact rows.
            ts_short = ts_raw.split("T")[-1].split("+")[0].split(".")[0][:8]
            source = (r.get("source") or "manual")[:22].ljust(22)
            arrow = _c("↻", _YELLOW, color) if r.get("fell_back") else _c("→", _DIM, color)
            backend = r.get("resolved_backend") or "?"
            topic = r.get("topic") or ""
            dest = f"{backend}/{topic}" if topic else backend
            dest = dest.ljust(28)[:28]
            ok_glyph = _c("ok", _GREEN, color) if r.get("ok") else _c("FAIL", _RED, color)
            rc = r.get("response_code")
            rc_str = f" ({rc})" if rc else ""
            out.append(f"    {_c(ts_short, _DIM, color)}  {source}{arrow} {dest}{ok_glyph}{rc_str}")
    else:
        out.append(f"  Recent     {_c('(no activity)', _DIM, color)}")
    return out


def _loop_notify_annotation(loop: dict[str, Any], color: bool) -> str:
    """Compact ↗ glyph showing a loop's last notify destination, or empty."""
    ln = loop.get("last_notify")
    if not ln:
        return ""
    backend = ln.get("resolved_backend") or "?"
    topic = ln.get("topic") or ""
    dest = f"{backend}/{topic}" if topic else backend
    if ln.get("fell_back"):
        return _c(f"  ↻{dest}", _YELLOW, color)
    if not ln.get("ok"):
        return _c(f"  ↗{dest} FAIL", _RED, color)
    return _c(f"  ↗{dest}", _CYAN, color)


def render_pretty(payload: dict[str, Any], all_instances: bool = True) -> str:
    """Render the cockpit overview as ANSI-colored text.

    When all_instances is False and the payload has exactly one instance, the
    single-instance detail layout is used.
    """
    color = _color_enabled()
    instances = payload.get("instances", [])
    summary = payload.get("summary", {})

    now_local = datetime.now().astimezone()
    timestamp = now_local.strftime("%H:%M:%S %z")

    if not all_instances and len(instances) == 1:
        return _render_single(instances[0], summary, timestamp, color)

    return _render_overview(instances, summary, timestamp, color)


def _render_overview(
    instances: list[dict[str, Any]],
    summary: dict[str, Any],
    timestamp: str,
    color: bool,
) -> str:
    lines: list[str] = []
    title = _c("empirica cockpit", _BOLD, color)
    pad = max(20, 60 - len("empirica cockpit"))
    lines.append(f"{title}{' ' * pad}{timestamp}")

    banner = _failure_banner(summary.get("notify_dispatcher") or {}, color)
    if banner:
        lines.append(banner)
    lines.append("")

    if not instances:
        lines.append(_c("no instances discovered", _DIM, color))
        lines.append("")
        lines.append(_c("hint: empirica preflight-submit -  starts a transaction", _DIM, color))
        return "\n".join(lines)

    cols = (14, 13, 10, 12, 18, 14)
    header = (
        f"{'Instance':<{cols[0]}}{'State':<{cols[1]}}{'Phase':<{cols[2]}}"
        f"{'Sentinel':<{cols[3]}}{'Loops':<{cols[4]}}{'Last Tx':<{cols[5]}}"
    )
    lines.append(_c(header, _BOLD, color))
    lines.append(_c("─" * sum(cols), _DIM, color))

    for inst in instances:
        label_raw = inst.get("label") or inst.get("instance_id", "?")
        label = _pad(label_raw[: cols[0] - 1], cols[0])
        state = _pad(_state_cell(inst["state"], color), cols[1])
        phase = _pad(_phase_cell(inst["phase"], color), cols[2])
        sentinel = _pad(_sentinel_cell(inst["sentinel"], color), cols[3])
        loops = _pad(_loops_summary(inst.get("loops", {}), color), cols[4])
        tx = _tx_cell(inst.get("transaction"), color)
        lines.append(f"{label}{state}{phase}{sentinel}{loops}{tx}")

    lines.append("")
    sentence = (
        f"{summary.get('instances', 0)} instances · "
        f"{summary.get('loops_registered', 0)} loops registered · "
        f"{summary.get('loops_paused', 0)} paused · "
        f"{summary.get('active_tx', 0)} active tx"
    )
    lines.append(_c(sentence, _DIM, color))
    lines.append("")
    lines.extend(_render_overview_hints(color))

    return "\n".join(lines)


def _render_overview_hints(color: bool) -> list[str]:
    """Footer with the action verbs — turns the read-only overview into a
    discoverable control plane without yet building a TUI."""
    hints = [
        ("pause sentinel ", "empirica sentinel pause --instance <ID>"),
        ("pause loop     ", "empirica loop pause <NAME> --instance <ID>"),
        ("kill instance  ", "empirica instance kill <ID>  (--force for SIGKILL)"),
        ("forget instance", "empirica instance forget <ID>  (cleanup state files)"),
        ("label          ", 'empirica instance label <ID> "<name>"'),
    ]
    out = [_c("Controls:", _DIM, color)]
    for label, cmd in hints:
        out.append(_c(f"  {label}  →  {cmd}", _DIM, color))
    return out


def _injection_cell(budget: dict | None) -> str | None:
    """Compact injection measure-view for the status detail view (prop_3por4fwg),
    or None when there's no budget. 'injected' is the volume ('do I need a cap?');
    'dropped' is what a cap removed ('is it biting?')."""
    if not budget:
        return None
    injected = budget.get("injected_total", 0)
    cap_total = budget.get("cap_total")
    cap_pc = budget.get("cap_per_category")
    dropped = (budget.get("capped_per_category", 0) or 0) + (budget.get("capped_total", 0) or 0)
    cap_str = str(cap_total) if cap_total else (f"{cap_pc}/cat" if cap_pc else "uncapped")
    line = f"{injected} injected · cap {cap_str}"
    if dropped:
        line += f" · {dropped} dropped"
    return line


def _render_single(
    inst: dict[str, Any],
    summary: dict[str, Any],
    timestamp: str,
    color: bool,
) -> str:
    lines: list[str] = []
    label = inst.get("label") or inst.get("instance_id", "?")
    instance_id = inst.get("instance_id", "?")
    title = _c(f"empirica ◆ {label} ({instance_id})", _BOLD, color)
    pad = max(2, 60 - len(f"empirica ◆ {label} ({instance_id})"))
    lines.append(f"{title}{' ' * pad}{timestamp}")

    banner = _failure_banner(summary.get("notify_dispatcher") or {}, color)
    if banner:
        lines.append(banner)
    lines.append("")

    state_phase_tx_parts = [_state_cell(inst["state"], color), _phase_cell(inst["phase"], color)]
    transaction = inst.get("transaction")
    if transaction:
        age = _humanize_seconds(transaction.get("age_seconds"))
        state_phase_tx_parts.append(f"transaction {_short_id(transaction.get('id'))} ({age})")
    lines.append("  ·  ".join(state_phase_tx_parts))
    lines.append("")

    inj = _injection_cell(inst.get("injection_budget"))
    if inj:
        lines.append(_c(f"Injection  {inj}", _DIM, color))
        lines.append("")

    lines.append(f"Sentinel  {_sentinel_cell(inst['sentinel'], color)}")
    sent_since = inst["sentinel"].get("since")
    sent_reason = inst["sentinel"].get("reason")
    if sent_since:
        lines.append(_c(f"  since {sent_since}", _DIM, color))
    if sent_reason:
        lines.append(_c(f"  reason: {sent_reason}", _DIM, color))
    lines.append("")

    loops = inst.get("loops", {}) or {}
    if not loops:
        lines.append("Loops   (none registered)")
    else:
        lines.append("Loops")
        for name in sorted(loops.keys()):
            loop = loops[name]
            paused = loop.get("paused")
            glyph = _c("○", _RED, color) if paused else _c("●", _GREEN, color)
            kind = loop.get("kind", "monitor")
            schedule = loop.get("cron") or loop.get("interval") or ""
            schedule_str = f"{kind} {schedule}".strip().ljust(28)
            last_run = loop.get("last_run")
            if last_run:
                age = _loop_age_seconds(loop)
                age_str = f"({_humanize_seconds(age)})"
                stale = _c(" ⚠", _YELLOW, color) if _loop_is_stale(loop) else ""
                last_str = f"last {last_run.split('T')[-1].split('+')[0][:8]} {age_str}{stale}"
            else:
                last_str = "never run"
            status_glyph = ""
            status = loop.get("last_status")
            if status == "fail":
                status_glyph = _c(" fail", _RED, color)
            elif status == "ok":
                status_glyph = _c(" ok", _GREEN, color)
            paused_label = _c("PAUSED", _RED, color) if paused else ""
            notify_glyph = _loop_notify_annotation(loop, color)
            lines.append(
                f"  {glyph} {name:<22}{schedule_str}{last_str}{status_glyph}  {paused_label}{notify_glyph}".rstrip()
            )

    # Notify dispatcher block (per outreach Claude spec).
    notify_block = _notify_dispatcher_block(
        summary.get("notify_dispatcher") or {},
        color,
    )
    if notify_block:
        lines.append("")
        lines.extend(notify_block)

    lines.append("")
    instance_id = inst.get("instance_id", "?")
    hints = [
        ("toggle sentinel", f"empirica sentinel pause --instance {instance_id}"),
        ("toggle loop    ", f"empirica loop pause <NAME> --instance {instance_id}"),
        ("kill           ", f"empirica instance kill {instance_id}  (--force for SIGKILL)"),
        ("forget         ", f"empirica instance forget {instance_id}"),
        ("label          ", f'empirica instance label {instance_id} "<name>"'),
    ]
    lines.append(_c("Controls:", _DIM, color))
    for label, cmd in hints:
        lines.append(_c(f"  {label}  →  {cmd}", _DIM, color))

    return "\n".join(lines)


__all__ = ["render_json", "render_pretty"]
