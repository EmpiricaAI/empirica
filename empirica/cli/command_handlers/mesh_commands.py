"""Unified mesh diagnostic + control surface — `empirica mesh ...`.

Consolidates listener service management, instance health, zombie
detection, and live tail across the two layers of the empirica mesh:

  LOCAL — single-tenant multi-practitioner mesh primitives that work
          on empirica core alone (systemd-user / launchd persistent
          listener service running per ai_id, loop_fires.log writes,
          local loop scheduling).

  CORTEX BRIDGE — the optional proprietary cross-tenant overlay
          (curl subscription to ntfy, proposal_event ingestion,
          inbox poll). Surfaced only when `~/.empirica/credentials.yaml`
          declares a cortex section.

The two layers are reported distinctly so empirica core users see local
health without cortex noise; cortex users see both. Falls out of the
core-vs-proprietary split.

Why this exists: diagnosing a curl-subscription zombie (TCP died,
process still alive, no fires writing) previously required
`systemctl status` + `ps aux | grep curl` + `grep loop_fires.log` +
`cortex_inbox_poll` to triangulate. `empirica mesh status` collapses
that surface to one command.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from empirica.core.loop_scheduler.persistent_listener import (
    install_listener_for,
    listener_status_for,
    uninstall_listener_for,
)

LOOP_FIRES_LOG = Path.home() / ".empirica" / "loop_fires.log"
LISTENER_LOG_DIR = Path.home() / ".empirica" / "logs"
CREDENTIALS_YAML = Path.home() / ".empirica" / "credentials.yaml"

# Threshold past which a cortex-bridged instance with no fires is suspected
# zombie. Tuned to be above the normal idle gap between cortex polls.
ZOMBIE_THRESHOLD_SECONDS = 1800  # 30 min

# Freshness window for the listener's positive-liveness health marker.
# The listener writes ~/.empirica/listener_health_<ai_id>.json with
# status=ok + ts on every successful poll cycle. If the marker is fresher
# than this window, the listener has confirmed itself alive — even if no
# events have arrived in the ZOMBIE_THRESHOLD_SECONDS window. Quiet-but-
# healthy is the dominant idle case on real-world mesh traffic; flagging
# it as zombie triggers pointless restarts.
HEALTH_MARKER_FRESH_SECONDS = 300  # 5 min

# How many recent lines of the listener log to scan for backoff-state markers.
# Only the last few entries matter for "what state is this listener in right
# now?" — scanning the whole file would over-report old backoff windows that
# already lifted.
_BACKOFF_SCAN_TAIL_LINES = 80


class MeshInstanceState(NamedTuple):
    ai_id: str
    backend: str
    service_installed: bool
    service_active: bool
    listener_process_pid: int | None
    curl_subprocess_pid: int | None
    last_fire_at_utc: datetime | None
    fires_last_hour: int
    cortex_configured: bool
    loops_registered: int
    backoff_state: str | None  # None | "rate_limit" | "auth_fail"
    health_color: str  # green | yellow | red
    health_reason: str
    # Where the last fire was read from, and how far back the logs reach. A practice
    # quiet since before the last rotation has its fires in loop_fires.log.1, so
    # "no fire in the current file" is not "no fire".
    last_fire_source: str | None = None  # file name, e.g. "loop_fires.log.1"
    fire_window_start: datetime | None = None  # earliest timestamp in the oldest log read
    fire_logs_read: int = 0
    fire_logs_unreadable: int = 0
    # True when the curl found descends from the listener, False when it does not (an orphan a dead
    # listener left behind), None when no curl was found. One snapshot: see _find_listener_procs.
    curl_parented: bool | None = None


def _load_cortex_credentials() -> dict | None:
    """Cortex credentials through the SHARED contract.

    Hand-parsed `~/.empirica/credentials.yaml` and gated on `api_key` being
    present, which was blind twice: an OAuth-only seat has no api_key, so this
    returned None and every caller reported "no cortex creds"; and a repo-local
    credentials file that the loader honours was invisible to a reader that only
    opened HOME. Every private resolver is its own 401 contract.

    Returns the cortex block shape callers already expect — `url` plus `api_key`
    carrying the resolved bearer, whatever its origin — so the two call sites
    reading `.get("api_key")` keep working against an OAuth seat.
    """
    try:
        from empirica.core.auth import cortex_bearer

        creds = cortex_bearer()
    except Exception:
        return None
    url, bearer = creds.get("url"), creds.get("bearer")
    if not (url and bearer):
        return None
    return {**creds, "url": url, "api_key": bearer}


def _enumerate_instances() -> list[str]:
    instances: set[str] = set()
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    if systemd_dir.exists():
        for unit in systemd_dir.glob("empirica-listener-*.service"):
            instances.add(unit.stem.removeprefix("empirica-listener-"))
    launchd_dir = Path.home() / "Library" / "LaunchAgents"
    if launchd_dir.exists():
        for plist in launchd_dir.glob("com.empirica.listener.*.plist"):
            instances.add(plist.stem.removeprefix("com.empirica.listener."))
    return sorted(instances)


class ListenerProcs(NamedTuple):
    listener_pid: int | None
    curl_pid: int | None
    curl_parented: bool | None


def _parse_ps_table(ps_out: str) -> dict[int, tuple[int, str]]:
    """``{pid: (ppid, args)}`` from ``ps -eo pid,ppid,args`` output (header line skipped)."""
    table: dict[int, tuple[int, str]] = {}
    for line in ps_out.splitlines()[1:]:
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            table[int(parts[0])] = (int(parts[1]), parts[2])
        except ValueError:
            continue
    return table


def _descends_from(table: dict[int, tuple[int, str]], pid: int, ancestors: set[int]) -> bool:
    """Is any of ``ancestors`` above ``pid``? Walks ppid links, so a shell or supervisor wrapper
    between the listener and its curl still counts as parented. Bounded, and cycle-safe."""
    seen: set[int] = set()
    cur = pid
    for _ in range(16):
        parent = table.get(cur, (None, ""))[0]
        if parent is None or parent in seen or parent <= 0:
            return False
        if parent in ancestors:
            return True
        seen.add(parent)
        cur = parent
    return False


def _find_listener_procs(ai_id: str, ps_out: str | None = None) -> ListenerProcs:
    """The listener, its curl, and whether that curl descends from it.

    The curl is matched by cmdline (orchestration-events plus the tag), which says nothing about
    who owns it: an orphan left by a dead listener matches exactly like a live bridge, and a curl
    whose parent just died and is being respawned reads as one too. So each match is also walked up
    the process table. A parented curl wins over an orphan when both exist (a respawn in flight),
    and ``curl_parented`` is False when curls exist but none descends from a found listener.

    This is ONE ps snapshot: a listener restarting can make its curl look orphaned for a moment.
    Health therefore still trusts fire-flow first; anything that acts on this (a watchdog restart)
    should confirm with a second sample.
    """
    if ps_out is None:
        try:
            ps_out = subprocess.run(
                ["ps", "-eo", "pid,ppid,args"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        except Exception:
            return ListenerProcs(None, None, None)
    table = _parse_ps_table(ps_out)
    listener_pid = None
    listener_pids: set[int] = set()
    curls: list[int] = []
    listen_needle = f"loop listen --instance {ai_id}"
    # Post-3-form migration (1.11.x strict-canonical), the listener
    # subscribes via _resolve_canonical_ai_id, so the curl cmdline
    # carries `tags=<org>.<tenant>.<ai_id>` (e.g.
    # `tags=empirica.david.empirica-cortex`) rather than the bare
    # basename `tags={ai_id}`. Match either: legacy `tags={ai_id}` for
    # back-compat, or `.{ai_id}` as a suffix of the canonical 3-form.
    # Closing-delimiter set (`"&'` + whitespace) prevents prefix
    # confusion (e.g. `tags=empirica` matching `tags=empirica-cortex`).
    legacy_needle = f"tags={ai_id}"
    canonical_suffix = f".{ai_id}"
    for pid, (_ppid, cmd) in table.items():
        # Boundary match: `--instance empirica` is a prefix of `--instance empirica-workspace`, and a
        # bare substring would hand one practice another's listener pid (and its curl ancestry).
        if "loop listen" in cmd and _tag_matches(cmd, listen_needle):
            listener_pid = pid
            listener_pids.add(pid)
        elif "orchestration-events" in cmd and (
            _tag_matches(cmd, legacy_needle) or _tag_matches(cmd, canonical_suffix)
        ):
            curls.append(pid)
    if not curls:
        return ListenerProcs(listener_pid, None, None)
    parented = [c for c in curls if _descends_from(table, c, listener_pids)]
    if parented:
        return ListenerProcs(listener_pid, parented[-1], True)
    return ListenerProcs(listener_pid, curls[-1], False)


def _tag_matches(cmd: str, needle: str) -> bool:
    """Match needle as a tag token in the curl cmdline.

    Ensures the next character after the needle is a tag-terminating
    delimiter (`&"',` or whitespace or end-of-string) so that e.g.
    `tags=empirica` does not falsely match a cmdline containing
    `tags=empirica-cortex`.
    """
    idx = cmd.find(needle)
    if idx == -1:
        return False
    end = idx + len(needle)
    if end == len(cmd):
        return True
    return cmd[end] in "&\"',; \t"


class FireScan(NamedTuple):
    last_ts: datetime | None
    fires_last_hour: int
    source: str | None  # file the last fire came from
    window_start: datetime | None  # earliest timestamp in the oldest file read
    files_read: int
    files_unreadable: int = 0  # generations that exist but could not be opened


def _parse_ts(value) -> datetime | None:
    """An ISO timestamp as an aware UTC datetime, or None. A naive one is read as UTC: comparing a
    naive with an aware datetime raises, and one such line would otherwise take down the scan."""
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def _line_ts(line: str) -> datetime | None:
    """The ``ts`` of one fires-log line. A line that is not a JSON object (a list, a scalar, a torn
    write) has no timestamp; it must not raise, because it sits between the lines that do."""
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    return _parse_ts(rec.get("ts")) if isinstance(rec, dict) else None


def _fires_log_generations() -> list[Path]:
    """The current fires log, then its rotated generations, newest first.

    Rotation renames the current file to ``<log>.1`` (see
    listener._rotate_fires_log_if_oversized), so today there is at most one prior
    generation. Numbered siblings are read too, in numeric order, so a longer
    retention needs no change here.
    """
    numbered = []
    for p in LOOP_FIRES_LOG.parent.glob(LOOP_FIRES_LOG.name + ".*"):
        suffix = p.name.rsplit(".", 1)[-1]
        if suffix.isdigit():
            numbered.append((int(suffix), p))
    return [p for p in [LOOP_FIRES_LOG, *(p for _, p in sorted(numbered))] if p.exists()]


def _scan_one_fires_log(path: Path, needle: str, cutoff: float) -> tuple[datetime | None, datetime | None, int, bool]:
    """``(first ts in file, last ts for this instance, fires since cutoff, readable)``.

    ``readable`` is False when the file could not be opened: a scan that reads nothing must say so,
    or "no fires" is indistinguishable from "could not look". Bytes that are not UTF-8 are replaced
    rather than raised, so one torn line cannot hide the fires around it.
    """
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    recent = 0
    try:
        with path.open(errors="replace") as f:
            for line in f:
                if first_ts is None:
                    first_ts = _line_ts(line)
                if needle not in line:
                    continue
                ts = _line_ts(line)
                if ts is None:
                    continue
                if last_ts is None or ts > last_ts:
                    last_ts = ts
                if ts.timestamp() >= cutoff:
                    recent += 1
    except OSError:
        return first_ts, last_ts, recent, False
    return first_ts, last_ts, recent, True


def _scan_fires(ai_id: str) -> FireScan:
    """Last fire for an instance across the current and rotated fires logs.

    Reads the current file first and goes back a generation only when it holds
    no fire for the instance (or began less than an hour ago, so the hourly count
    may continue into the older file). Reports which file the answer came from
    and how far back the logs reach, so "nothing found" can say what it looked at.
    """
    cutoff = datetime.now(tz=timezone.utc).timestamp() - 3600
    needle = f'"instance_id": "{ai_id}"'
    last_ts: datetime | None = None
    source: str | None = None
    window_start: datetime | None = None
    recent = 0
    files = _fires_log_generations()
    read = 0
    unreadable = 0
    for path in files:
        first_ts, file_last, file_recent, readable = _scan_one_fires_log(path, needle, cutoff)
        if not readable:
            unreadable += 1
            continue
        read += 1
        window_start = first_ts or window_start
        recent += file_recent
        if file_last is not None and (last_ts is None or file_last > last_ts):
            last_ts, source = file_last, path.name
        if last_ts is not None and not (first_ts is not None and first_ts.timestamp() > cutoff):
            break
    return FireScan(last_ts, recent, source, window_start, read, unreadable)


def _last_fire_for(ai_id: str) -> tuple[datetime | None, int]:
    scan = _scan_fires(ai_id)
    return scan.last_ts, scan.fires_last_hour


def _listener_health_freshness(ai_id: str) -> float | None:
    """Seconds since the listener's positive-liveness health marker was
    last written. None if the marker is missing, malformed, or marked
    degraded.

    The listener writes ``~/.empirica/listener_health_<ai_id>.json`` with
    ``status=ok`` + ``ts`` on every successful poll cycle (per
    listener.py ``_clear_fail_heartbeat``). A fresh ``status=ok`` marker
    is positive proof the listener is alive even when no events have
    arrived — the dominant idle case in real mesh traffic.

    ``status=degraded`` markers return None so the caller falls back to
    the existing fire-flow heuristic (degraded markers carry their own
    surfacing).
    """
    health_file = Path.home() / ".empirica" / f"listener_health_{ai_id}.json"
    if not health_file.exists():
        return None
    try:
        data = json.loads(health_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("status") != "ok":
        return None
    ts = data.get("ts")
    if not ts:
        return None
    try:
        marker_at = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    if marker_at.tzinfo is None:
        marker_at = marker_at.replace(tzinfo=timezone.utc)
    return (datetime.now(tz=timezone.utc) - marker_at).total_seconds()


def _detect_backoff_state(ai_id: str) -> str | None:
    """Read the recent tail of the listener log to detect whether the
    listener is currently in a backoff window (curl intentionally killed
    + sleeping) vs. genuinely broken curl.

    Returns "rate_limit" if the most recent backoff marker is the ntfy
    HTTP 429 path, "auth_fail" for 4xx/5xx generic backoff, or None if
    no recent backoff marker (curl absence means something else).

    The "most recent" wins because the listener emits the backoff log
    line at the START of each backoff window — if a newer "subscribing
    to" or "ntfy event arrived" line appears after the last backoff
    marker, the window already lifted.
    """
    log_path = LISTENER_LOG_DIR / f"listener-{ai_id}.log"
    if not log_path.exists():
        return None
    try:
        with log_path.open(errors="replace") as f:
            try:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 32_768))
                tail = f.read().splitlines()[-_BACKOFF_SCAN_TAIL_LINES:]
            except (OSError, ValueError):
                tail = f.read().splitlines()[-_BACKOFF_SCAN_TAIL_LINES:]
    except OSError:
        return None
    state: str | None = None
    for line in tail:
        if "ntfy rate limit (429)" in line:
            state = "rate_limit"
        elif "applying error backoff" in line and "429" not in line:
            state = "auth_fail"
        elif "ntfy event arrived" in line or "subscribing to" in line:
            # A fresh subscribe or event clears the prior backoff marker —
            # the listener has moved past that window.
            state = None
    return state


def _compute_health(s: dict, cortex_configured: bool) -> tuple[str, str]:
    if not s["service_installed"]:
        return "yellow", "service not installed"
    if not s["service_active"]:
        return "red", "service inactive"
    if s["listener_process_pid"] is None:
        return "red", "listener process not found despite active service"
    if not cortex_configured:
        return "green", "local-only (cortex bridge not configured)"

    curl_alive = s["curl_subprocess_pid"] is not None and s.get("curl_parented") is not False
    curl_gone = (
        "curl subscription orphaned — the curl found is not the listener's child; cortex bridge broken"
        if s["curl_subprocess_pid"] is not None
        else "curl subscription dead — cortex bridge broken"
    )
    # Fire-flow is the AUTHORITATIVE liveness signal — fires are what we
    # actually care about. Curl-pid detection (next block) is a
    # diagnostic refinement, not a primary signal. Pre-1.11.8 this
    # order was inverted (curl-pid-missing → red BEFORE checking fires),
    # which made every cortex instance show red post-3-form-migration
    # because the curl needle was looking for the basename while the
    # listener now subscribes with the canonical tag. The fire path
    # was structurally unreachable. Mesh-support prop_po4nyp3xzb.
    last = s["last_fire_at_utc"]
    src = s.get("last_fire_source")
    where = f" [read from {src}]" if src and src != LOOP_FIRES_LOG.name else ""
    now = datetime.now(tz=timezone.utc)
    if last is not None:
        idle_seconds = (now - last).total_seconds()
        if idle_seconds <= ZOMBIE_THRESHOLD_SECONDS:
            # Fires are flowing — bridge is healthy regardless of whether
            # our curl-pid detection found a match.
            return "green", f"last fire {_fmt_age(idle_seconds)} ago{where}"
        # Fires went silent — escalate using curl-pid + backoff signals.
        if not curl_alive:
            backoff = s.get("backoff_state")
            if backoff == "rate_limit":
                return "yellow", "rate-limited — curl absent during 30-min backoff; catch-up poll still running"
            if backoff == "auth_fail":
                return "yellow", "auth/HTTP backoff — curl absent during 5-min backoff; catch-up poll still running"
            return "red", curl_gone
        # Curl is alive and we have idle gap > ZOMBIE_THRESHOLD. Cross-
        # reference the listener's positive-liveness health marker
        # before flagging zombie — a fresh status=ok marker is direct
        # confirmation the listener completed a poll cycle recently and
        # is just genuinely idle, not stuck. Avoids false-positive
        # restart recommendations on quiet-but-healthy listeners.
        health_age = _listener_health_freshness(s["ai_id"])
        if health_age is not None and health_age <= HEALTH_MARKER_FRESH_SECONDS:
            return (
                "green",
                f"quiet but healthy — no fires in {int(idle_seconds // 60)}m "
                f"but listener health ok ({int(health_age)}s ago){where}",
            )
        return "red", f"zombie suspected: no fires in {int(idle_seconds // 60)} min{where}"

    # No fires recorded yet — fall through to the curl-pid + backoff
    # diagnostic since we have no fire-flow signal to lean on.
    if not curl_alive:
        backoff = s.get("backoff_state")
        if backoff == "rate_limit":
            return "yellow", "rate-limited — curl absent during 30-min backoff; catch-up poll still running"
        if backoff == "auth_fail":
            return "yellow", "auth/HTTP backoff — curl absent during 5-min backoff; catch-up poll still running"
        return "red", curl_gone
    return "yellow", _no_fire_reason(s)


def _no_fire_reason(s: dict) -> str:
    """Why an instance with a live curl has no fire on record, worded by what the scan could see."""
    window = s.get("fire_window_start")
    if window is None and s.get("fire_logs_unreadable") and not s.get("fire_logs_read"):
        # Logs exist and none could be opened: that is not a cold start, it is a blind spot.
        return f"fires log unreadable ({s['fire_logs_unreadable']} file(s)); cannot tell whether fires flow"
    if window is None:
        # No fires log at all: this really is a cold start.
        return "no fires recorded yet (cold start ok if recent install)"
    # Logs exist and hold no fire for this instance. That is a statement about the
    # retained window, not about history: rotation keeps one prior generation.
    n = s.get("fire_logs_read") or 1
    return f"no fire in the retained log window (since {window:%Y-%m-%d}, {n} log file(s) read)"


def _fmt_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _gather_state(ai_id: str, cortex_configured: bool) -> MeshInstanceState:
    svc = listener_status_for(ai_id)
    procs = _find_listener_procs(ai_id)
    listener_pid, curl_pid = procs.listener_pid, procs.curl_pid
    scan = _scan_fires(ai_id)
    last_fire, fires_last_hour = scan.last_ts, scan.fires_last_hour
    loops_count = 0
    try:
        from empirica.core.loop_scheduler.systemd import list_active_loops_for_instance

        loops_count = len(list_active_loops_for_instance(ai_id) or [])
    except Exception:
        pass
    curl_alive = curl_pid is not None and procs.curl_parented is not False
    backoff_state = _detect_backoff_state(ai_id) if not curl_alive else None
    s = {
        "ai_id": ai_id,
        "backend": svc.backend,
        "service_installed": svc.installed,
        "service_active": svc.active,
        "listener_process_pid": listener_pid,
        "curl_subprocess_pid": curl_pid,
        "last_fire_at_utc": last_fire,
        "fires_last_hour": fires_last_hour,
        "cortex_configured": cortex_configured,
        "loops_registered": loops_count,
        "backoff_state": backoff_state,
        "last_fire_source": scan.source,
        "fire_window_start": scan.window_start,
        "fire_logs_read": scan.files_read,
        "fire_logs_unreadable": scan.files_unreadable,
        "curl_parented": procs.curl_parented,
    }
    color, reason = _compute_health(s, cortex_configured)
    return MeshInstanceState(**s, health_color=color, health_reason=reason)


# ── Verbs ──────────────────────────────────────────────────────────────


def handle_mesh_status_command(args) -> int:
    cortex_configured = _load_cortex_credentials() is not None
    target = getattr(args, "instance", None)
    instances = [target] if target else _enumerate_instances()
    rows = [_gather_state(ai_id, cortex_configured) for ai_id in instances]

    if getattr(args, "output", "human") == "json":
        out = []
        for r in rows:
            d = r._asdict()
            for key in ("last_fire_at_utc", "fire_window_start"):
                if d[key]:
                    d[key] = d[key].isoformat()
            out.append(d)
        print(
            json.dumps(
                {"ok": True, "cortex_configured": cortex_configured, "instances": out},
                indent=2,
            )
        )
        return 0

    if not rows:
        print("No mesh instances registered.")
        print("Try: empirica mesh on <ai_id>")
        return 0

    glyph = {"green": "OK ", "yellow": "WARN", "red": "DOWN"}
    header = f"{'ai_id':<20} {'health':<6} {'service':<10} {'curl':<8} {'last fire':<12} {'fires/h':>8}  reason"
    print(header)
    print("-" * len(header))
    from_rotated = False
    for r in rows:
        if r.last_fire_at_utc:
            idle = (datetime.now(tz=timezone.utc) - r.last_fire_at_utc).total_seconds()
            last_str = _fmt_age(idle)
            if r.last_fire_source and r.last_fire_source != LOOP_FIRES_LOG.name:
                last_str += "*"
                from_rotated = True
        else:
            last_str = "-"
        svc_str = "active" if r.service_active else ("dead" if r.service_installed else "absent")
        if r.cortex_configured:
            if not r.curl_subprocess_pid:
                curl_str = "dead"
            else:
                curl_str = "orphan" if r.curl_parented is False else "ok"
        else:
            curl_str = "n/a"
        g = glyph.get(r.health_color, "?")
        print(
            f"{r.ai_id:<20} {g:<6} {svc_str:<10} {curl_str:<8} {last_str:<12} {r.fires_last_hour:>8}  {r.health_reason}"
        )

    if from_rotated:
        print()
        print("* last fire read from a rotated fires log (the current one holds none for this instance)")

    if not cortex_configured:
        print()
        print("Cortex bridge not configured -- running local-only.")
        print("Single-tenant multi-practitioner coordination works without cortex.")
        print("For cross-tenant + push-wake + browser triage, see https://getempirica.com")
    return 0


def handle_mesh_diagnose_command(args) -> int:
    ai_id = args.instance
    cortex_configured = _load_cortex_credentials() is not None
    state = _gather_state(ai_id, cortex_configured)

    # Cortex-side participation checks (opt-in via --cortex). Runs the
    # local diagnose render first, then appends the cortex panel.
    cortex_results = None
    if getattr(args, "cortex", False):
        cortex_results = _run_cortex_panel(ai_id, getattr(args, "peer", None))

    if getattr(args, "output", "human") == "json":
        return _emit_diagnose_json(ai_id, state, cortex_results)

    print(f"=== mesh diagnose: {ai_id} ===")
    print()
    print(f"  Backend:              {state.backend}")
    print(f"  Service installed:    {state.service_installed}")
    print(f"  Service active:       {state.service_active}")
    print(f"  Listener PID:         {state.listener_process_pid or 'NONE'}")
    print(f"  Loops registered:     {state.loops_registered}")
    print(f"  Cortex configured:    {cortex_configured}")
    if cortex_configured:
        if state.curl_subprocess_pid:
            note = (
                "  (NOT the listener's child: an orphan left by a dead listener)"
                if state.curl_parented is False
                else ""
            )
            print(f"  Curl subscription:    {state.curl_subprocess_pid}{note}")
        else:
            print(f"  Curl subscription:    DEAD (no curl process for tags={ai_id})")
    if state.last_fire_at_utc:
        idle = (datetime.now(tz=timezone.utc) - state.last_fire_at_utc).total_seconds()
        from_note = (
            f", read from {state.last_fire_source}"
            if state.last_fire_source and state.last_fire_source != LOOP_FIRES_LOG.name
            else ""
        )
        print(f"  Last fire:            {state.last_fire_at_utc.isoformat()}  ({_fmt_age(idle)} ago{from_note})")
    elif state.fire_window_start:
        # Not "NEVER": the logs only reach back this far, so all that is known is that
        # nothing landed inside the window.
        print(
            f"  Last fire:            none in the retained window "
            f"(since {state.fire_window_start.isoformat()}, {state.fire_logs_read} log file(s) read)"
        )
    else:
        print("  Last fire:            none recorded (no fires log found)")
    print(f"  Fires in last hour:   {state.fires_last_hour}")
    print()
    print(f"  Health: {state.health_color.upper()} -- {state.health_reason}")
    print()

    rc = 0
    if state.health_color == "green":
        print("No action needed.")
    else:
        rc = 1
        if not state.service_installed:
            print(f"Fix: empirica mesh on {ai_id}")
        elif not state.service_active:
            print(f"Fix: empirica mesh restart {ai_id}")
        elif cortex_configured and (state.curl_subprocess_pid is None or state.curl_parented is False):
            print(f"Fix: empirica mesh restart {ai_id}  (curl subprocess died or is orphaned; restart re-spawns)")
        elif (
            state.last_fire_at_utc
            and (datetime.now(tz=timezone.utc) - state.last_fire_at_utc).total_seconds() > ZOMBIE_THRESHOLD_SECONDS
        ):
            print("Likely curl-zombie (TCP died silently while process stayed alive).")
            print(f"Fix: empirica mesh restart {ai_id}")
        else:
            print("Manual investigation needed.")
            print(f"  journalctl --user -u empirica-listener-{ai_id}.service")

    if cortex_results is not None:
        from ._mesh_diagnose_cortex import (
            aggregate_exit_code,
            render_results_human,
        )

        print()
        print(render_results_human(cortex_results))
        cortex_rc = aggregate_exit_code(cortex_results)
        if cortex_rc > rc:
            rc = cortex_rc

    return rc


def _run_cortex_panel(ai_id: str, peer: str | None):
    """Load cortex creds + run the cortex-side check panel. Returns the
    list of CheckResult or None on credential failure (in which case
    the diagnose flow continues without a cortex panel)."""
    cortex_block = _load_cortex_credentials()  # already-unwrapped flat dict
    if not cortex_block:
        sys.stderr.write(
            "--cortex requested but no cortex creds in ~/.empirica/credentials.yaml; skipping cortex-side panel.\n"
        )
        return None
    cortex_url = cortex_block.get("url")
    api_key = cortex_block.get("api_key")
    if not (cortex_url and api_key):
        sys.stderr.write(
            "--cortex requested but credentials.yaml cortex block missing url/api_key; skipping cortex-side panel.\n"
        )
        return None
    # ntfy block lives at top level of credentials.yaml; reload to pick it up.
    # Forward user/password too so the ntfy.read_grant probe authenticates
    # against basic-auth tenants (closes cortex's prop_m7ns4zq3eva6rpeqcdemifksvu
    # false-negative on philipp's box).
    ntfy_url = None
    ntfy_token = None
    ntfy_user = None
    ntfy_password = None
    try:
        import yaml

        with CREDENTIALS_YAML.open() as f:
            full = yaml.safe_load(f) or {}
        ntfy_block = full.get("ntfy") or {}
        ntfy_url = ntfy_block.get("url")
        ntfy_token = ntfy_block.get("token")
        ntfy_user = ntfy_block.get("user")
        ntfy_password = ntfy_block.get("password")
    except Exception:
        pass
    from ._mesh_diagnose_cortex import run_cortex_checks

    return run_cortex_checks(
        ai_id,
        cortex_url=cortex_url,
        api_key=api_key,
        ntfy_url=ntfy_url,
        ntfy_token=ntfy_token,
        ntfy_user=ntfy_user,
        ntfy_password=ntfy_password,
        peer=peer,
    )


def _emit_diagnose_json(ai_id: str, state, cortex_results) -> int:
    """JSON output path for --output json."""
    payload = {
        "ok": True,
        "ai_id": ai_id,
        "local": {
            "backend": state.backend,
            "service_installed": state.service_installed,
            "service_active": state.service_active,
            "listener_process_pid": state.listener_process_pid,
            "curl_subprocess_pid": state.curl_subprocess_pid,
            "curl_parented": state.curl_parented,
            "last_fire_at_utc": (state.last_fire_at_utc.isoformat() if state.last_fire_at_utc else None),
            "last_fire_source": state.last_fire_source,
            "fire_window_start": (state.fire_window_start.isoformat() if state.fire_window_start else None),
            "fire_logs_read": state.fire_logs_read,
            "fires_last_hour": state.fires_last_hour,
            "loops_registered": state.loops_registered,
            "health_color": state.health_color,
            "health_reason": state.health_reason,
        },
    }
    rc = 0 if state.health_color == "green" else 1
    if cortex_results is not None:
        from ._mesh_diagnose_cortex import aggregate_exit_code

        payload["cortex"] = [r.to_dict() for r in cortex_results]
        cortex_rc = aggregate_exit_code(cortex_results)
        if cortex_rc > rc:
            rc = cortex_rc
    print(json.dumps(payload, indent=2))
    return rc


def handle_mesh_restart_command(args) -> int:
    ai_id = args.instance
    svc = listener_status_for(ai_id)
    if not svc.installed:
        sys.stderr.write(f"service for '{ai_id}' not installed -- run: empirica mesh on {ai_id}\n")
        return 2

    print(f"Restarting empirica-listener-{ai_id}...")
    if svc.backend == "systemd":
        rc = subprocess.run(
            ["systemctl", "--user", "restart", f"empirica-listener-{ai_id}.service"],
            capture_output=True,
            text=True,
        )
        if rc.returncode != 0:
            sys.stderr.write(f"systemctl restart failed: {rc.stderr}\n")
            return 1
    elif svc.backend == "launchd":
        rc = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{__import__('os').getuid()}/com.empirica.listener.{ai_id}"],
            capture_output=True,
            text=True,
        )
        if rc.returncode != 0:
            sys.stderr.write(f"launchctl kickstart failed: {rc.stderr}\n")
            return 1
    else:
        sys.stderr.write(f"backend '{svc.backend}' restart not implemented\n")
        return 1

    time.sleep(2)
    new_state = listener_status_for(ai_id)
    if not new_state.active:
        sys.stderr.write("service did not become active after restart\n")
        return 1
    print(f"OK -- {ai_id} listener service restarted (active).")

    cortex_configured = _load_cortex_credentials() is not None
    if not cortex_configured:
        return 0

    print("Waiting up to 10s for first fire to confirm subscription is live...")
    baseline_last, _ = _last_fire_for(ai_id)
    for _ in range(10):
        time.sleep(1)
        last, _ = _last_fire_for(ai_id)
        if last and last != baseline_last:
            print(f"OK -- fire received at {last.isoformat()}; subscription live.")
            return 0
    print("WARN -- no fire received in 10s. Curl may still be warming or subscription is hung.")
    print(f"       diagnose further: empirica mesh diagnose {ai_id}")
    return 0


def handle_mesh_on_command(args) -> int:
    ai_id = args.instance
    svc = listener_status_for(ai_id)
    if svc.installed and svc.active:
        print(f"OK -- service for '{ai_id}' already installed + active.")
        return 0
    if not svc.installed:
        print(f"Installing listener service for '{ai_id}'...")
        try:
            install_listener_for(ai_id)
        except Exception as e:
            sys.stderr.write(f"install failed: {e}\n")
            return 1
    if svc.backend == "systemd":
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", f"empirica-listener-{ai_id}.service"],
            capture_output=True,
            text=True,
            check=False,
        )
    new_state = listener_status_for(ai_id)
    if new_state.active:
        print(f"OK -- {ai_id} listener active.")
        return 0
    sys.stderr.write(f"failed to activate; check journalctl --user -u empirica-listener-{ai_id}.service\n")
    return 1


def handle_mesh_off_command(args) -> int:
    ai_id = args.instance
    svc = listener_status_for(ai_id)
    if not svc.installed:
        print(f"service for '{ai_id}' not installed; nothing to do")
        return 0
    if svc.backend == "systemd":
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", f"empirica-listener-{ai_id}.service"],
            capture_output=True,
            text=True,
            check=False,
        )
    if getattr(args, "uninstall", False):
        try:
            uninstall_listener_for(ai_id)
            print(f"OK -- {ai_id} listener uninstalled.")
        except Exception as e:
            sys.stderr.write(f"uninstall failed: {e}\n")
            return 1
    else:
        print(f"OK -- {ai_id} listener stopped (still installed; pass --uninstall to remove).")
    return 0


def handle_mesh_tail_command(args) -> int:
    if not LOOP_FIRES_LOG.exists():
        sys.stderr.write(f"{LOOP_FIRES_LOG} does not exist\n")
        return 1
    target = getattr(args, "instance", None)
    instances = [target] if target else _enumerate_instances()
    if not instances:
        sys.stderr.write("no instances to tail\n")
        return 1
    pattern = re.compile("|".join(re.escape(f'"instance_id": "{i}"') for i in instances))
    sys.stderr.write(f"tailing {LOOP_FIRES_LOG} for: {', '.join(instances)} (Ctrl-C to stop)\n")
    proc = subprocess.Popen(
        ["tail", "-F", "-n", "0", str(LOOP_FIRES_LOG)],
        stdout=subprocess.PIPE,
        text=True,
    )
    if proc.stdout is None:
        sys.stderr.write("tail subprocess returned no stdout\n")
        return 1
    try:
        for line in proc.stdout:
            if pattern.search(line):
                sys.stdout.write(line)
                sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
    return 0


# ── mesh migrate-topics ─────────────────────────────────────────────────
#
# Closes empirica's slice of SER ser_dd1955ae07e04949a28bd5bc (canonical
# ntfy channel model). The retired bare `orchestration-events` topic and
# any legacy per-practice topic are detected and rewritten to the
# per-tenant canonical (`<org>-orchestration-events-<tenant>`) resolved
# from cortex's notification-channels endpoint.
#
# A topic is RETIRED when:
#   - it equals the bare `orchestration-events` (no org/tenant prefix)
#   - or it lacks the `-orchestration-events-` segment that identifies a
#     per-tenant channel (pre-T16/T17 per-org or per-practice form)
# A topic is CANONICAL when it matches `<something>-orchestration-events-<something>`.


def _strip_ntfy_topic_url(raw: str) -> str:
    """Drop `ntfy:` scheme + `?tags=...` query so we can compare the
    bare topic name across credentials.yaml + listener_active markers."""
    base = raw or ""
    if base.startswith("ntfy:"):
        base = base[len("ntfy:") :]
    return base.split("?", 1)[0]


def _is_retired_topic(base: str) -> bool:
    if not base:
        return False
    if base == "orchestration-events":
        return True
    return "-orchestration-events-" not in base


def _migrate_credentials_topic(canonical_base: str, apply: bool) -> dict:
    """Inspect + (optionally) rewrite the `ntfy.topic` field in
    ~/.empirica/credentials.yaml.

    Reads the file directly rather than via `CredentialsLoader.get_ntfy_config`
    so we distinguish "topic explicitly set in the file" from "loader fell
    back to the default `orchestration-events`". The migration ONLY rewrites
    explicit retired topics; absent topic stays absent (listener resolves
    canonical at runtime).
    """
    try:
        import os as _os

        import yaml

        from empirica.config.credentials_loader import (
            CredentialsLoader,
        )
    except Exception as e:
        return {"checked": False, "error": f"credentials loader unavailable: {e}"}

    env_path = _os.environ.get("EMPIRICA_CREDENTIALS_PATH")
    target = Path(env_path) if env_path else (Path.home() / ".empirica" / "credentials.yaml")
    if not target.exists():
        return {"checked": True, "current": None, "action": "skip", "reason": "credentials.yaml not found"}
    try:
        raw_doc = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return {"checked": False, "error": f"could not read credentials.yaml: {e}"}
    ntfy_block = raw_doc.get("ntfy")
    if not isinstance(ntfy_block, dict) or "topic" not in ntfy_block:
        return {
            "checked": True,
            "current": None,
            "action": "skip",
            "reason": "no explicit ntfy.topic set (listener resolves at runtime)",
        }
    current = ntfy_block.get("topic") or ""
    base = _strip_ntfy_topic_url(current)
    if not _is_retired_topic(base):
        return {"checked": True, "current": base, "action": "keep", "reason": "already canonical"}
    entry = {
        "checked": True,
        "current": base,
        "canonical": canonical_base,
        "action": "rewrite",
        "applied": False,
    }
    if apply:
        try:
            CredentialsLoader().save_ntfy_config(topic=canonical_base)
            entry["applied"] = True
        except Exception as e:
            entry["error"] = str(e)
            entry["applied"] = False
    # Reset singleton cache so the next get_ntfy_config sees the write.
    CredentialsLoader._credentials_cache = None
    return entry


def _migrate_listener_active_markers(canonical_base: str, apply: bool) -> list[dict]:
    """Inspect + (optionally) rewrite the `topic` field on every
    ~/.empirica/listener_active_*.json marker that's pinned to a retired
    topic. Per-AI tag suffix is preserved."""
    reports: list[dict] = []
    home_empirica = Path.home() / ".empirica"
    if not home_empirica.is_dir():
        return reports
    for marker in sorted(home_empirica.glob("listener_active_*.json")):
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            reports.append(
                {
                    "file": str(marker),
                    "action": "error",
                    "error": f"unreadable: {e}",
                }
            )
            continue
        raw_topic = data.get("topic") or ""
        if not raw_topic:
            reports.append({"file": str(marker), "action": "skip", "reason": "no topic field"})
            continue
        base = _strip_ntfy_topic_url(raw_topic)
        if not _is_retired_topic(base):
            reports.append({"file": str(marker), "current": base, "action": "keep"})
            continue
        # Preserve the `?tags=<ai_id>` suffix; rebuild with the canonical base.
        tag_q = ""
        if "?" in raw_topic:
            tag_q = "?" + raw_topic.split("?", 1)[1]
        new_topic = f"ntfy:{canonical_base}{tag_q}"
        entry = {
            "file": str(marker),
            "current": raw_topic,
            "rewritten": new_topic,
            "action": "rewrite",
            "applied": False,
        }
        if apply:
            try:
                data["topic"] = new_topic
                marker.write_text(
                    json.dumps(data, indent=2),
                    encoding="utf-8",
                )
                entry["applied"] = True
            except OSError as e:
                entry["error"] = str(e)
                entry["applied"] = False
        reports.append(entry)
    return reports


def handle_mesh_migrate_topics_command(args) -> int:
    """`empirica mesh migrate-topics` — rewrite retired ntfy topics in
    credentials.yaml + listener_active markers to the per-tenant canonical
    resolved from cortex's notification-channels endpoint.

    Closes empirica's slice of SER ser_dd1955ae07e04949a28bd5bc."""
    apply = bool(getattr(args, "apply", False))
    output = getattr(args, "output", "human")

    try:
        from empirica.core.cockpit.notification_channels import (
            _resolve_base_topic,
            fetch_notification_channels,
        )
    except Exception as e:
        payload = {"ok": False, "error": f"notification_channels module unavailable: {e}"}
        if output == "json":
            print(json.dumps(payload, indent=2))
        else:
            print(f"error: {payload['error']}")
        return 2

    body = fetch_notification_channels(force=True)
    canonical_base = _resolve_base_topic(body)
    if not canonical_base:
        payload = {
            "ok": False,
            "error": (
                "Cortex's notification-channels endpoint returned no canonical "
                "orchestration_events topic. Check cortex reachability + "
                "credentials, then retry."
            ),
        }
        if output == "json":
            print(json.dumps(payload, indent=2))
        else:
            print(f"error: {payload['error']}")
        return 2

    creds_report = _migrate_credentials_topic(canonical_base, apply)
    marker_reports = _migrate_listener_active_markers(canonical_base, apply)
    rewrites_pending = sum(
        1 for r in [creds_report, *marker_reports] if r.get("action") == "rewrite" and not r.get("applied")
    )
    rewrites_done = sum(1 for r in [creds_report, *marker_reports] if r.get("action") == "rewrite" and r.get("applied"))

    payload = {
        "ok": True,
        "dry_run": not apply,
        "canonical_base": canonical_base,
        "credentials": creds_report,
        "listener_active": marker_reports,
        "rewrites_pending": rewrites_pending,
        "rewrites_applied": rewrites_done,
    }

    if output == "json":
        print(json.dumps(payload, indent=2))
    else:
        _render_migrate_topics_human(
            canonical_base,
            creds_report,
            marker_reports,
            apply,
            rewrites_pending,
        )
    return 0


def _render_migrate_topics_creds_line(creds_report: dict, canonical_base: str) -> None:
    action = creds_report.get("action", "?")
    if action == "rewrite":
        tag = "✓ rewritten" if creds_report.get("applied") else "(would rewrite)"
        print(f"    {creds_report.get('current')} → {canonical_base}  {tag}")
    elif action == "keep":
        print(f"    {creds_report.get('current')}  ✓ already canonical")
    elif action == "skip":
        print(f"    (none) — {creds_report.get('reason')}")
    elif action == "error":
        print(f"    ! error: {creds_report.get('error')}")


def _render_migrate_topics_marker_line(marker: dict) -> None:
    name = Path(marker["file"]).name
    a = marker.get("action", "?")
    if a == "rewrite":
        tag = "✓ rewritten" if marker.get("applied") else "(would rewrite)"
        print(f"    - {name}  {tag}")
        print(f"        {marker.get('current')}")
        print(f"      → {marker.get('rewritten')}")
    elif a == "keep":
        print(f"    - {name}  ✓ keep ({marker.get('current')})")
    elif a == "skip":
        print(f"    - {name}  -- {marker.get('reason')}")
    elif a == "error":
        print(f"    - {name}  ! error: {marker.get('error')}")


def _render_migrate_topics_human(
    canonical_base: str,
    creds_report: dict,
    marker_reports: list[dict],
    apply: bool,
    rewrites_pending: int,
) -> None:
    mode = "APPLIED" if apply else "DRY RUN"
    print(f"mesh migrate-topics — {mode}")
    print(f"  canonical per-tenant topic: {canonical_base}")
    print()
    print("  credentials.yaml ntfy.topic:")
    _render_migrate_topics_creds_line(creds_report, canonical_base)
    print()
    print("  ~/.empirica/listener_active_*.json:")
    if not marker_reports:
        print("    (no markers found)")
    for r in marker_reports:
        _render_migrate_topics_marker_line(r)
    if not apply and rewrites_pending:
        print()
        print(f"  {rewrites_pending} pending rewrite(s). Run with --apply to write.")


_MESH_DISPATCH = {
    "status": handle_mesh_status_command,
    "diagnose": handle_mesh_diagnose_command,
    "restart": handle_mesh_restart_command,
    "on": handle_mesh_on_command,
    "off": handle_mesh_off_command,
    "tail": handle_mesh_tail_command,
    "migrate-topics": handle_mesh_migrate_topics_command,
}


def handle_mesh_group_command(args) -> int:
    action = getattr(args, "mesh_action", None)
    if not action:
        sys.stderr.write("usage: empirica mesh <status|diagnose|restart|on|off|tail|migrate-topics> [args...]\n")
        return 2
    handler = _MESH_DISPATCH.get(action)
    if handler is None:
        sys.stderr.write(f"error: unknown mesh action: {action}\n")
        return 2
    return handler(args) or 0


__all__ = [
    "handle_mesh_diagnose_command",
    "handle_mesh_group_command",
    "handle_mesh_migrate_topics_command",
    "handle_mesh_off_command",
    "handle_mesh_on_command",
    "handle_mesh_restart_command",
    "handle_mesh_status_command",
    "handle_mesh_tail_command",
]
