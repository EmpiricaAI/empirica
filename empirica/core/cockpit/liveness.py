"""Liveness detection for cockpit instances.

An instance is "alive" when there is reason to believe an actual Claude
Code process is running in it. Otherwise the cockpit shouldn't list it
by default — `kill` would just say "already dead", and the row is noise.

Signal hierarchy (first definitive signal wins):

  1. tmux instance: `tmux list-panes -a` includes %N → maybe alive (continue);
     %N missing → DEAD (terminal closed, Claude is gone with it).
  2. Captured PPID alive: os.kill(ppid, 0) succeeds → ALIVE.
     PPID dead → DEAD (Claude process exited).
  3. No PID and no tmux info, but recent activity (< RECENT_ACTIVITY_S):
     → ALIVE (likely fresh session that hasn't synced yet).
  4. Otherwise → DEAD.

A consequence: a tmux pane that exists but contains a plain shell where
Claude exited will show DEAD if we have a captured PPID — exactly the
case David flagged.

The tmux pane query is cached per-call to avoid spawning a subprocess per
instance during a status sweep.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

EMPIRICA_DIR = Path.home() / ".empirica"
TTY_SESSIONS_DIR = EMPIRICA_DIR / "tty_sessions"

# Wall-clock ceiling for a psutil process-table walk. On macOS, .environ()/
# .cmdline() issue sysctl(KERN_PROCARGS2), which can block uninterruptibly on a
# wedged process (network-mount helper, zombie, VPN/AV agent) — a native syscall
# block, not a Python exception, so per-process try/except never fires. Bounding
# the walk turns an indefinite hang (issue #365) into a logged, graceful
# degradation. Env-overridable as an escape hatch without a release.
_LIVENESS_SCAN_TIMEOUT_S = float(os.environ.get("EMPIRICA_LIVENESS_SCAN_TIMEOUT", "3.0"))


def _run_bounded(fn: Callable[[], _T | None], label: str) -> _T | None:
    """Run a psutil process-table walk under a hard wall-clock bound.

    On timeout we abandon the (daemon) worker thread — it stays blocked in the
    syscall but can't hold up process exit — and return None, honoring the
    callers' 'None = inconclusive, never a dead verdict' contract (liveness then
    degrades to the tmux / captured-PID / recent-activity signals).
    """
    box: dict[str, _T | None] = {}

    def _target():
        try:
            box["v"] = fn()
        except Exception:
            box["v"] = None

    t = threading.Thread(target=_target, name=f"empirica-{label}", daemon=True)
    t.start()
    t.join(_LIVENESS_SCAN_TIMEOUT_S)
    if t.is_alive():
        logger.warning(
            "%s exceeded %.1fs — a process's environ()/cmdline() is blocking; "
            "treating liveness as inconclusive (raise EMPIRICA_LIVENESS_SCAN_TIMEOUT to adjust)",
            label,
            _LIVENESS_SCAN_TIMEOUT_S,
        )
        return None
    return box.get("v")


TMUX_INSTANCE_PATTERN = re.compile(r"^tmux_(.+)$")

# An instance with no PID/tmux info but activity within this window is
# treated as alive (covers fresh sessions where session-init hasn't yet
# captured a PID).
RECENT_ACTIVITY_S = 60 * 60  # 1 hour


@dataclass
class LivenessResult:
    alive: bool
    reason: str
    pid_checked: int | None = None
    tmux_pane: str | None = None
    # Which signal produced the verdict — for programmatic consumers
    # (e.g. aggregate_all's count-aware dedup). One of:
    # "current" | "tmux" | "process_scan" | "pid" | "recent_activity" | "".
    signal: str = ""


# Commands tmux reports as the foreground process when Claude Code is running.
# 'claude' is the bin name; 'node' covers older installations / dev launches.
_CLAUDE_COMMANDS = frozenset({"claude", "node"})

# Claude Code 2.1.x renames the foreground process to a bare version string, so
# tmux's pane_current_command reports e.g. "2.1.212" instead of "claude". A pane
# whose command matches this is a mangled-CC candidate — resolve its process tree
# to confirm (Philipp verified: every live 2.1.212 pane reports this).
_VERSION_NAME_RE = re.compile(r"^\d+\.\d+")


def _pane_hosts_claude(pane_pid: int) -> bool:
    """True if the tmux pane's process tree contains a Claude Code process
    (wall-clock bounded — the .cmdline() calls carry the same macOS syscall-hang
    risk as scan_live_claude, #365; on timeout treat the pane as not-claude)."""
    return bool(_run_bounded(lambda: _pane_hosts_claude_impl(pane_pid), "pane_hosts_claude"))


def _pane_hosts_claude_impl(pane_pid: int) -> bool:
    """Resolve the pane's foreground process + descendants and reuse
    `_is_claude_proc` (matches cmdline[0] basename, surviving the CC 2.1.x rename)."""
    try:
        import psutil

        proc = psutil.Process(pane_pid)
        for p in [proc, *proc.children(recursive=True)]:
            try:
                if _is_claude_proc(p.name(), p.cmdline()):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:  # noqa: S110 — best-effort; psutil missing / proc gone must never break liveness
        pass
    return False


def _live_tmux_panes() -> set[str] | None:
    """Return set of pane numbers (e.g. {'1', '2', '3'}) where Claude Code is running.

    Uses `pane_current_command` to distinguish "Claude is running here" from
    "this pane exists but it's just a bash shell". A bash pane that once
    hosted Claude after the user `exit`ed is correctly classified as not
    hosting Claude — which is exactly what David flagged.

    Returns None if we couldn't query tmux at all (signal inconclusive,
    fall through to PID/activity checks).
    """
    if shutil.which("tmux") is None:
        return None
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_id} #{pane_pid} #{pane_current_command}"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        # tmux not running, no server, etc. — no Claude panes alive.
        return set()
    panes = set()
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) != 3:
            continue
        pane_id, pane_pid, cmd = parts
        if cmd in _CLAUDE_COMMANDS:
            panes.add(pane_id.lstrip("%"))
        elif _VERSION_NAME_RE.match(cmd):
            # Mangled CC 2.1.x name (e.g. "2.1.212") — confirm via the process
            # tree. Only version-string names are tree-walked, so shells/python
            # panes cost nothing.
            try:
                if _pane_hosts_claude(int(pane_pid)):
                    panes.add(pane_id.lstrip("%"))
            except ValueError:
                pass
    return panes


def _all_tmux_panes() -> set[str] | None:
    """Return set of ALL pane numbers regardless of command. Used for
    distinguishing 'pane gone' (terminal closed) from 'pane exists but
    Claude exited' — both are 'dead' for the cockpit, but the explanation
    differs."""
    if shutil.which("tmux") is None:
        return None
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_id}"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return set()
    return {line.strip().lstrip("%") for line in result.stdout.splitlines() if line.strip()}


def _try_create_time(pid: int) -> float | None:
    """psutil start time (epoch secs) for ``pid``, or None if psutil is
    unavailable or the process is gone."""
    try:
        import psutil

        return psutil.Process(pid).create_time()
    except Exception:
        return None


def _process_alive(pid: int, expected_create_time: float | None = None) -> bool:
    """True if ``pid`` is a live process.

    When ``expected_create_time`` is given, additionally require the process's
    start time to match it (within 1s) — this rejects a *recycled* pid number
    whose original owner has exited (the cause of cockpit liveness flapping:
    a bare ``os.kill`` reads whatever impostor now holds the reused number).
    Falls back to the bare ``os.kill`` probe when psutil is unavailable or no
    start time was captured, preserving prior behavior.
    """
    if pid <= 1:
        return False
    if expected_create_time is not None:
        actual = _try_create_time(pid)
        if actual is not None:
            return abs(actual - expected_create_time) < 1.0
        # couldn't read start time → fall through to the os.kill probe below
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _is_claude_proc(name: str | None, cmdline: list[str] | None) -> bool:
    """Heuristic: is this process a Claude Code session?

    The CC binary reports as ``claude``; older/dev installs run via ``node``
    with the claude entrypoint in argv. We require the ``claude`` token in
    the cmdline for the node case to avoid sweeping in unrelated node apps.

    Claude Code 2.1.x mangles the OS process name (psutil ``name()``) to a bare
    version string (e.g. ``2.1.212``), so a name-only gate silently hides every
    live 2.1.x seat — and it recurs on each version bump. ``cmdline()[0]``'s
    basename reliably stays ``claude`` regardless, so we match on that too.
    """
    nm = (name or "").lower()
    if nm == "claude":
        return True
    cmd = cmdline or []
    # argv[0] basename is 'claude' even when name() is a mangled version string.
    if cmd and os.path.basename(cmd[0] or "").lower() == "claude":
        return True
    if nm in _CLAUDE_COMMANDS:  # node — confirm it's actually claude
        return any("claude" in (arg or "").lower() for arg in cmd)
    return False


class LiveClaudeScan(NamedTuple):
    """Result of one process-table walk for live Claude sessions.

    ``instance_ids`` — the ``EMPIRICA_INSTANCE_ID`` env of every live claude
    process that declares one. EXACT and resume-proof: it maps a record to its
    live process regardless of pid changes (``claude --resume`` mints a new pid
    the record never learned) or which multiplexer draws the pane. This is the
    PRIMARY liveness signal.

    ``cwd_counts`` — realpath cwd → live-proc count. A coarser, project-level
    FALLBACK for the rare live proc that carries no ``EMPIRICA_INSTANCE_ID``
    (legacy launch). It can't tell which same-project record maps to which proc,
    so aggregate_all count-caps how many records it may revive.
    """

    instance_ids: set[str]
    cwd_counts: dict[str, int]


def scan_live_claude() -> LiveClaudeScan | None:
    """Walk the process table for live ``claude`` sessions (wall-clock bounded).

    Bounded by :func:`_run_bounded` so a wedged process's blocking
    ``environ()``/``cmdline()`` on macOS can't hang the whole command (#365).
    """
    return _run_bounded(_scan_live_claude_impl, "scan_live_claude")


def _scan_live_claude_impl() -> LiveClaudeScan | None:
    """Walk the process table once for live ``claude`` sessions.

    MULTIPLEXER-AGNOSTIC and STATE-INDEPENDENT: sees Claude regardless of
    tmux/screen/WezTerm/zellij/cmux or whether ``session-init`` captured a PID.
    Returns ``None`` if psutil is unavailable or the whole walk fails — an
    inconclusive signal, never a dead verdict. Per-process access failures are
    skipped, not fatal to the sweep.
    """
    try:
        import psutil
    except ImportError:
        return None

    instance_ids: set[str] = set()
    cwd_counts: dict[str, int] = {}
    try:
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                info = proc.info
                if not _is_claude_proc(info.get("name"), info.get("cmdline")):
                    continue
            except (psutil.Error, OSError):
                continue
            # Primary: EMPIRICA_INSTANCE_ID env — exact, resume-proof.
            try:
                iid = proc.environ().get("EMPIRICA_INSTANCE_ID")
            except (psutil.Error, OSError):
                iid = None
            if iid:
                instance_ids.add(iid)
            # Fallback: cwd — coarse project-level attribution.
            try:
                cwd = proc.cwd()
            except (psutil.Error, OSError):
                cwd = None
            if cwd:
                rp = os.path.realpath(cwd)
                cwd_counts[rp] = cwd_counts.get(rp, 0) + 1
    except Exception:
        return None
    return LiveClaudeScan(instance_ids=instance_ids, cwd_counts=cwd_counts)


def claude_pane_bindings() -> tuple[dict[str, int], set[str]]:
    """Bounded ``(windows_by_instance, owned_panes)`` from one proc→pane scan.

    - ``windows_by_instance``: instance_id → tmux window index. Named seats are
      matched by their live claude proc's ``EMPIRICA_INSTANCE_ID`` env; a
      ``tmux_<pane_id>`` fallback covers panes NOT owned by a named seat (unbound
      / pre-empirica sessions). Feeds the cockpit's pane-number column.
    - ``owned_panes``: pane numbers (``%`` stripped) whose foreground claude
      declares an ``EMPIRICA_INSTANCE_ID`` — panes already owned by a named
      seat, so :func:`discover_instances` can skip minting a synthetic
      ``tmux_<pane>`` shadow for them.

    Both empty on any failure (no tmux / no psutil / bounded timeout). Purely a
    display + discovery affordance — never a liveness verdict, never blocks a
    sweep.
    """
    return _run_bounded(_scan_claude_pane_map, "claude_pane_bindings") or ({}, set())


def live_claude_windows_by_instance() -> dict[str, int]:
    """Bounded ``instance_id → tmux window index`` (thin wrapper — the window
    half of :func:`claude_pane_bindings`)."""
    return claude_pane_bindings()[0]


def _scan_claude_pane_map() -> tuple[dict[str, int], set[str]]:
    if shutil.which("tmux") is None:
        return {}, set()
    try:
        import psutil
    except ImportError:
        return {}, set()
    # pane shell pid -> window (claude proc is a child of the shell); pane id
    # (%N, stripped) -> window (for the tmux_<N> fallback below).
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_id} #{pane_pid} #{window_index}"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {}, set()
    if result.returncode != 0:
        return {}, set()
    pane_pid_to_pane: dict[int, str] = {}
    pane_id_to_window: dict[str, int] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        pane_id, pane_pid, win = parts
        try:
            window = int(win)
        except ValueError:
            continue
        pane = pane_id.lstrip("%")
        pane_id_to_window[pane] = window
        try:
            pane_pid_to_pane[int(pane_pid)] = pane
        except ValueError:
            pass
    if not pane_pid_to_pane and not pane_id_to_window:
        return {}, set()
    windows: dict[str, int] = {}
    owned_panes: set[str] = set()
    # Named seats: match each live claude proc's EMPIRICA_INSTANCE_ID to the
    # tmux pane (and window) it runs in — exact, resume-proof.
    try:
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                info = proc.info
                if not _is_claude_proc(info.get("name"), info.get("cmdline")):
                    continue
                iid = proc.environ().get("EMPIRICA_INSTANCE_ID")
            except (psutil.Error, OSError):
                continue
            if not iid:
                continue
            # Walk the claude proc's ancestry up to the tmux pane shell that
            # owns it; the pane shell's pid is the tmux pane_pid.
            node = proc
            for _ in range(8):
                try:
                    if node.pid in pane_pid_to_pane:
                        pane = pane_pid_to_pane[node.pid]
                        owned_panes.add(pane)
                        if pane in pane_id_to_window:
                            windows[iid] = pane_id_to_window[pane]
                        break
                    node = node.parent()
                except (psutil.Error, OSError):
                    break
                if node is None:
                    break
    except Exception:
        pass
    # Fallback window for unbound seats registered under the tmux_<pane_id> id
    # (they never declared EMPIRICA_INSTANCE_ID). The id already encodes the
    # pane, so read the window straight off pane_id — but only for a window NOT
    # already claimed by a named seat (defensive: shadows are suppressed at
    # discovery now, so this fires for genuine unbound fallbacks like a
    # not-yet-rebound seat, not for named-seat ghosts).
    claimed = set(windows.values())
    for pane, window in pane_id_to_window.items():
        if window in claimed:
            continue
        windows[f"tmux_{pane}"] = window
    return windows, owned_panes


def live_claude_pids_by_instance() -> dict[str, tuple[int, float | None]] | None:
    """Map each live claude ``EMPIRICA_INSTANCE_ID`` to ``(pid, create_time)``
    (wall-clock bounded — same macOS syscall-hang guard as
    :func:`scan_live_claude`, #365)."""
    return _run_bounded(_live_claude_pids_by_instance_impl, "live_claude_pids_by_instance")


def _live_claude_pids_by_instance_impl() -> dict[str, tuple[int, float | None]] | None:
    """Map each live claude ``EMPIRICA_INSTANCE_ID`` to ``(pid, create_time)``.

    Like :func:`scan_live_claude` but keyed by instance id and carrying the
    process's own pid + start time — used by ``instance rebind`` to re-stamp a
    record's captured pid from the actually-running process. Returns ``None``
    if psutil is unavailable or the scan fails.
    """
    try:
        import psutil
    except ImportError:
        return None

    out: dict[str, tuple[int, float | None]] = {}
    try:
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                info = proc.info
                if not _is_claude_proc(info.get("name"), info.get("cmdline")):
                    continue
                iid = proc.environ().get("EMPIRICA_INSTANCE_ID")
            except (psutil.Error, OSError):
                continue
            if not iid:
                continue
            try:
                ct = proc.create_time()
            except (psutil.Error, OSError):
                ct = None
            out[iid] = (proc.pid, ct)
    except Exception:
        return None
    return out


def _pids_from_data(data: dict) -> tuple[int | None, int | None, float | None]:
    """Extract (pid, ppid, ppid_create_time) from a state-file dict."""
    pid = data.get("pid") if isinstance(data.get("pid"), int) else None
    ppid = data.get("ppid") if isinstance(data.get("ppid"), int) else None
    ct = data.get("ppid_create_time")
    ct = float(ct) if isinstance(ct, (int, float)) else None
    return pid, ppid, ct


def _read_captured_pids(instance_id: str) -> tuple[int | None, int | None, float | None]:
    """Return (pid, ppid, ppid_create_time) captured at session-init, or Nones.

    ``ppid_create_time`` is the Claude parent's start time — used to reject a
    recycled ppid number (the flapping guard). Absent for instances captured
    before that field existed → falls back to the bare ``os.kill`` probe.
    """
    inst_file = EMPIRICA_DIR / "instance_projects" / f"{instance_id}.json"
    if inst_file.exists():
        try:
            with open(inst_file, encoding="utf-8") as f:
                data = json.load(f)
            pid, ppid, ct = _pids_from_data(data)
            if pid or ppid:
                return pid, ppid, ct
            tty_key = data.get("tty_key")
        except (OSError, json.JSONDecodeError):
            tty_key = None
    else:
        tty_key = None

    if tty_key:
        tty_file = TTY_SESSIONS_DIR / f"{tty_key}.json"
        if tty_file.exists():
            try:
                with open(tty_file, encoding="utf-8") as f:
                    data = json.load(f)
                return _pids_from_data(data)
            except (OSError, json.JSONDecodeError):
                pass

    return None, None, None


def is_alive(
    instance_id: str,
    last_activity_seconds: float | None = None,
    live_panes: set[str] | None = None,
    current_instance_id: str | None = None,
    *,
    project_path: str | None = None,
    live_claude_instance_ids: set[str] | None = None,
    live_claude_cwds: set[str] | None = None,
) -> LivenessResult:
    """Determine whether an instance is alive.

    Signal precedence (any one alive signal makes the instance alive;
    only when ALL signals report dead do we report dead):

      1. Current instance — running this code → ALIVE.
      2. A live claude process declares this ``instance_id`` in its
         ``EMPIRICA_INSTANCE_ID`` env → ALIVE. EXACT and resume-proof;
         overrides a stale captured PID. Primary signal — consulted when the
         caller passes ``live_claude_instance_ids`` (a sweep precomputes it
         once via ``scan_live_claude``).
      3. Tmux pane shows claude foreground → ALIVE (definitive).
      4. Live claude process whose cwd == this instance's project_path →
         ALIVE. Coarser project-level FALLBACK for a live proc with no
         ``EMPIRICA_INSTANCE_ID`` env. Only consulted when the caller passes
         ``live_claude_cwds``.
      5. Captured PID alive (``os.kill(pid, 0)``) → ALIVE (definitive).
      6. Recent activity (< RECENT_ACTIVITY_S) → ALIVE (fallback).
      7. Otherwise → DEAD.

    The process-scan signals (2, 4) are the fix for non-tmux multiplexers
    (screen/wezterm/zellij/cmux) and ``claude --resume`` / env-unset manual
    restarts: a Claude that is genuinely running but is neither the tmux pane
    foreground nor has a *live* captured PID is still detectable in the process
    table. Signal 2 (env) is exact; signal 4 (cwd) is a coarse fallback. Both
    are ALIVE-positive only — absence is never a dead verdict, so they never
    override a definitive negative.

    The earlier shape short-circuited on tmux: if a pane existed but
    Claude was not the foreground command (e.g. user temporarily at
    bash, claude-in-a-split, wrapper script holding the foreground),
    is_alive returned DEAD without ever checking the captured PID.
    Philipp reported the symptom on his machine — 10 Claude PIDs
    alive via ``ps`` but only 1 visible in the cockpit. The fix is
    structural: tmux disagreement is no longer a verdict. The PID
    check is a parallel definitive signal, and the cockpit reports
    DEAD only when every signal agrees the process is gone.

    Args:
        instance_id: the instance to check
        last_activity_seconds: seconds since most recent state-file write
        live_panes: pre-computed set of live tmux pane numbers (sweep
            optimization — pass None to query lazily)
        current_instance_id: if equal to instance_id, treat as alive
            (the running cockpit is alive by definition)
        project_path: this instance's project directory — matched against
            ``live_claude_cwds`` for the cwd-fallback process signal
        live_claude_instance_ids: pre-computed set of EMPIRICA_INSTANCE_IDs
            declared by live claude processes (the exact, resume-proof primary
            process signal; pass None to skip it)
        live_claude_cwds: pre-computed set of realpath cwds hosting a live
            claude process (the coarse fallback signal; pass None to skip it)
    """
    if current_instance_id and instance_id == current_instance_id:
        return LivenessResult(alive=True, reason="current instance", signal="current")

    # Signal 2 — a live claude process declares this instance_id in its env.
    # Exact + resume-proof: survives pid changes and is independent of any
    # multiplexer. Checked first among the process signals because it maps the
    # record to its live process unambiguously.
    if live_claude_instance_ids and instance_id in live_claude_instance_ids:
        return LivenessResult(
            alive=True,
            reason="live claude process (EMPIRICA_INSTANCE_ID match)",
            signal="process_env",
        )

    # Signal 3 — tmux pane shows claude foreground.
    tmux_pane: str | None = None
    pane_state: str | None = None  # 'claude' | 'bash' | 'absent' | None (untestable)
    m = TMUX_INSTANCE_PATTERN.match(instance_id)
    if m:
        tmux_pane = m.group(1)
        if live_panes is None:
            live_panes = _live_tmux_panes()
        if live_panes is not None:
            if tmux_pane in live_panes:
                return LivenessResult(
                    alive=True,
                    reason=f"tmux pane %{tmux_pane} running claude",
                    tmux_pane=tmux_pane,
                    signal="tmux",
                )
            all_panes = _all_tmux_panes() or set()
            pane_state = "bash" if tmux_pane in all_panes else "absent"
        # tmux not queryable → pane_state stays None; fall through to PID

    # Signal 4 — live claude process attributable to this project by cwd.
    # Coarse FALLBACK for a live proc with no EMPIRICA_INSTANCE_ID env (the
    # exact env match is Signal 2). Catches Claude under screen/wezterm/zellij/
    # cmux and stale-PID restarts. ALIVE-positive only. Placed before the
    # captured-PID check so a genuinely-live process overrides a stale PID.
    if project_path and live_claude_cwds and os.path.realpath(project_path) in live_claude_cwds:
        return LivenessResult(
            alive=True,
            reason=f"live claude process in {project_path}",
            tmux_pane=tmux_pane,
            signal="process_cwd",
        )

    # Signal 5 — captured PID liveness. Authoritative when present.
    pid, ppid, ppid_ct = _read_captured_pids(instance_id)
    target_pid = ppid if ppid else pid
    if target_pid:
        # The create_time guard was captured for the ppid; only apply it when
        # the ppid is what we're probing (reject a recycled ppid number).
        expected_ct = ppid_ct if (ppid and target_pid == ppid) else None
        if _process_alive(target_pid, expected_ct):
            # PID overrides tmux disagreement: claude is running even
            # though it's not the pane foreground (sub-process, wrapper,
            # split window, etc.).
            return LivenessResult(
                alive=True,
                reason=f"pid {target_pid} alive",
                pid_checked=target_pid,
                tmux_pane=tmux_pane,
                signal="pid",
            )
        # PID dead → definitive dead, independent of tmux.
        return LivenessResult(
            alive=False,
            reason=f"pid {target_pid} dead",
            pid_checked=target_pid,
            tmux_pane=tmux_pane,
        )

    # Signal 6 — recent activity. Last-resort fallback when neither
    # tmux nor a captured PID can be consulted (e.g., fresh non-tmux
    # session, or tmux server unreachable). SKIP when tmux gave a
    # definitive negative — a stale instance file getting touched by a
    # housekeeping sweep doesn't revive a tmux pane whose foreground
    # is bash, and the recent-activity glow shouldn't override that.
    pane_negative = pane_state in ("bash", "absent")
    if not pane_negative and last_activity_seconds is not None and last_activity_seconds < RECENT_ACTIVITY_S:
        return LivenessResult(
            alive=True,
            reason=f"recent activity ({int(last_activity_seconds)}s ago)",
            tmux_pane=tmux_pane,
            signal="recent_activity",
        )

    # All signals exhausted. If tmux gave us a definitive negative,
    # surface that as the reason; otherwise generic.
    if pane_state == "bash":
        reason = f"tmux pane %{tmux_pane} exists but claude is not running there and no captured PID survived"
    elif pane_state == "absent":
        reason = f"tmux pane %{tmux_pane} does not exist"
    else:
        reason = "no pid, no recent activity, no tmux pane evidence"

    return LivenessResult(alive=False, reason=reason, tmux_pane=tmux_pane)


__all__ = [
    "LiveClaudeScan",
    "LivenessResult",
    "is_alive",
    "live_claude_pids_by_instance",
    "scan_live_claude",
]
