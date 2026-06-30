"""Instance discovery + state aggregation for the cockpit `status` command.

Discovery (which instances exist) is via state-file scan only — no tmux, no
/proc. LIVENESS (whether an instance's Claude is still running) is a separate
concern handled by `liveness.is_alive`, which additionally consults a
multiplexer-agnostic process scan (`liveness.scan_live_claude`) so non-tmux
multiplexers and `claude --resume` / env-unset restarts aren't under-reported.

An instance is anything that has left a footprint in ~/.empirica/:

  - instance_projects/{instance_id}.json  (canonical: instance → project map)
  - sentinel_paused_{instance_id}         (sentinel pause flag)
  - loops_{instance_id}.json              (loop registry)
  - active_session_{instance_id}          (legacy session pointer)
  - hook_counters_{instance_id}.json      (transaction hook counters)
  - context_usage_{instance_id}.json      (context tracking)

Aggregation reads the per-instance project pointer, then opens the project's
.empirica/active_transaction{suffix}.json to derive phase + age.

Phase model (file-derived, no DB):
  - "closed"          → status == "closed"
  - "praxic"          → status == "open" AND hook_counters has praxic_tool_calls > 0
  - "noetic"          → status == "open" AND no praxic activity yet
  - "no-transaction"  → no transaction file at all
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from empirica.core.cockpit.compliance_view import read_compliance_summary
from empirica.core.cockpit.enrichment import (
    is_asking,
    notification_summary,
    notifications_total,
)
from empirica.core.cockpit.listener_registry import (
    ListenerRegistry,
    is_listener_paused,
)
from empirica.core.cockpit.liveness import _live_tmux_panes, is_alive, scan_live_claude
from empirica.core.cockpit.loop_registry import LoopRegistry, is_loop_paused
from empirica.core.cockpit.notify_dispatcher_view import (
    annotate_loops_with_last_notify,
    build_notify_dispatcher_block,
)
from empirica.core.cockpit.sentinel_pause import sentinel_status
from empirica.core.cockpit.services_view import read_services_summary

EMPIRICA_DIR = Path.home() / ".empirica"

# Stale thresholds — drive the state symbol in --pretty.
ACTIVE_WINDOW_S = 60
IDLE_WINDOW_S = 30 * 60
ABANDONED_WINDOW_S = 24 * 60 * 60

# Files we scan to discover instance_ids.
INSTANCE_GLOBS = (
    "instance_projects/*.json",
    "sentinel_paused_*",
    "loops_*.json",
    "listeners_*.json",
    "active_session_*",
    "hook_counters_*.json",
    "context_usage_*.json",
)

# Files to exclude from instance discovery — they match the glob but are
# not per-instance footprints (e.g. the global pause file).
INSTANCE_EXCLUDE = {
    "sentinel_paused",  # global pause file (no instance suffix)
    "active_session",  # legacy global session
    "active_transaction",  # legacy global transaction
}

# Loop pause files use a different suffix shape — strip the loop component.
LOOP_PAUSE_PATTERN = re.compile(r"^loop_paused_(.+?)_(.+)$")


def _instance_id_from_filename(filename: str) -> str | None:
    """Extract instance_id from a state file name. Returns None if not parseable."""
    if filename in INSTANCE_EXCLUDE:
        return None

    # JSON files: strip .json suffix
    name = filename[:-5] if filename.endswith(".json") else filename

    # Strip known prefixes
    prefixes = (
        "sentinel_paused_",
        "loops_",
        "listeners_",
        "active_session_",
        "active_transaction_",
        "hook_counters_",
        "context_usage_",
        "cortex_remote_cache_",
        "pre_tx_calls_",
    )
    for prefix in prefixes:
        if name.startswith(prefix):
            return name[len(prefix) :]

    # instance_projects/{id}.json — name is just the instance_id
    return name


def _instance_label(instance_id: str, project_path: str | None = None) -> str:
    """Resolve the human-readable label.

    Priority:
    1. ~/.empirica/instance_label_{id}    (manual override)
    2. basename of project_path           (matches what statusline shows)
    3. instance_id                        (last-resort fallback)
    """
    label_file = EMPIRICA_DIR / f"instance_label_{instance_id}"
    if label_file.exists():
        try:
            text = label_file.read_text(encoding="utf-8").strip()
            if text:
                return text.splitlines()[0].strip()
        except OSError:
            pass
    if project_path:
        name = Path(project_path).name
        if name:
            return name
    return instance_id


def _project_ai_id(project_path: str | None) -> str | None:
    """Read canonical ai_id from project.yaml, with basename fallback.

    Delegates to InstanceResolver.ai_id(project_path=...) — single
    source of truth for the resolution chain (project.yaml → basename
    derivation). See docs/architecture/AI_ID_AS_ANCHOR.md.
    """
    from empirica.utils.session_resolver import InstanceResolver

    return InstanceResolver.ai_id(project_path=project_path)


def _instance_project_path(instance_id: str) -> str | None:
    """Return the project_path the instance is currently bound to, or None."""
    candidate = EMPIRICA_DIR / "instance_projects" / f"{instance_id}.json"
    if candidate.exists():
        try:
            with open(candidate, encoding="utf-8") as f:
                data = json.load(f)
            project_path = data.get("project_path")
            if isinstance(project_path, str) and project_path:
                return project_path
        except (OSError, json.JSONDecodeError):
            pass

    # Fallback: parse legacy active_session_{id}
    session_file = EMPIRICA_DIR / f"active_session_{instance_id}"
    if session_file.exists():
        try:
            with open(session_file, encoding="utf-8") as f:
                data = json.load(f)
            project_path = data.get("project_path")
            if isinstance(project_path, str) and project_path:
                return project_path
        except (OSError, json.JSONDecodeError):
            pass

    return None


def _newest_mtime(paths: list[Path]) -> float | None:
    """Return the newest mtime among existing paths, or None if none exist."""
    best: float | None = None
    for path in paths:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best:
            best = mtime
    return best


def _read_transaction_state(project_path: str, instance_id: str) -> dict[str, Any]:
    """Read transaction info for an instance. Returns dict with phase + age fields.

    Returns:
        {
            'phase': 'noetic' | 'praxic' | 'closed' | 'no-transaction',
            'transaction_id': str | None,
            'transaction_age_seconds': float | None,
            'last_activity_iso': str | None,
            'last_activity_seconds': float | None,
            'work_type': str | None,
            'domain': str | None,
            'criticality': str | None,
        }
    """
    suffix = f"_{instance_id}"
    empirica_dir = Path(project_path) / ".empirica"
    tx_file = empirica_dir / f"active_transaction{suffix}.json"
    counters_file = empirica_dir / f"hook_counters{suffix}.json"

    result: dict[str, Any] = {
        "phase": "no-transaction",
        "transaction_id": None,
        "session_id": None,
        "transaction_age_seconds": None,
        "last_activity_iso": None,
        "last_activity_seconds": None,
        "work_type": None,
        "domain": None,
        "criticality": None,
    }

    if not tx_file.exists():
        return result

    try:
        with open(tx_file, encoding="utf-8") as f:
            tx = json.load(f)
    except (OSError, json.JSONDecodeError):
        return result

    result["transaction_id"] = tx.get("transaction_id")
    result["session_id"] = tx.get("session_id")
    result["work_type"] = tx.get("work_type")
    result["domain"] = tx.get("domain")
    result["criticality"] = tx.get("criticality")

    preflight_ts = tx.get("preflight_timestamp")
    now = datetime.now(tz=timezone.utc).timestamp()
    if isinstance(preflight_ts, (int, float)):
        result["transaction_age_seconds"] = max(0.0, now - preflight_ts)

    # Last activity = newest mtime across tx + counters file
    last_mtime = _newest_mtime([tx_file, counters_file])
    if last_mtime is not None:
        result["last_activity_iso"] = datetime.fromtimestamp(last_mtime, tz=timezone.utc).isoformat()
        result["last_activity_seconds"] = max(0.0, now - last_mtime)

    status = tx.get("status", "open")
    if status == "closed":
        result["phase"] = "closed"
        return result

    # Status open — distinguish noetic vs praxic from hook counters.
    praxic = 0
    if counters_file.exists():
        try:
            with open(counters_file, encoding="utf-8") as f:
                counters = json.load(f)
            praxic = int(counters.get("praxic_tool_calls", 0) or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            praxic = 0
    result["phase"] = "praxic" if praxic > 0 else "noetic"
    return result


def _derive_state_symbol(
    tx_state: dict[str, Any],
    instance_files_mtime: float | None,
    alive: bool = True,
) -> str:
    """Derive the cockpit state from liveness + transaction + file age.

    Liveness is the primary signal: a Claude process that is running but
    between transactions is 🟡 idle (ready for work), not ⊘ closed (which
    visually reads as 'dead'). The phase column carries the open/closed
    transaction info separately, so we don't lose it.

    Returns one of: 'active' | 'idle' | 'stuck' | 'closed' | 'no-claude'
    """
    if not alive:
        # Dead — distinguish 'cleanly closed' (had a finished transaction)
        # from 'no-claude' (no transaction or abandoned > 24h).
        if tx_state["phase"] == "closed":
            return "closed"
        if tx_state["phase"] == "no-transaction" and instance_files_mtime is not None:
            age = datetime.now(tz=timezone.utc).timestamp() - instance_files_mtime
            if age <= ABANDONED_WINDOW_S:
                return "closed"
        return "no-claude"

    # Alive with an open transaction — bucket on last_activity age.
    if tx_state["phase"] in ("noetic", "praxic"):
        last_seconds = tx_state.get("last_activity_seconds")
        if last_seconds is None or last_seconds < ACTIVE_WINDOW_S:
            return "active"
        if last_seconds < IDLE_WINDOW_S:
            return "idle"
        return "stuck"

    # Alive but no open transaction — Claude is running between tasks.
    return "idle"


def discover_instances() -> list[str]:
    """Return the sorted list of known instance_ids.

    Walks the standard state-file globs under ~/.empirica/ and unions the
    derived instance_ids. Excludes loop pause sidecars (different suffix
    shape) and known global files.

    Also unions in any tmux pane currently running claude as foreground —
    this catches sessions started before empirica was installed (no
    state file ever written) so the cockpit can still see them.
    Synthetic ``tmux_{pane}`` IDs are stable across cockpit refreshes
    because tmux pane numbers are stable for the lifetime of the pane.
    """
    EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()

    for glob in INSTANCE_GLOBS:
        for path in EMPIRICA_DIR.glob(glob):
            instance_id = _instance_id_from_filename(path.name)
            if instance_id and not LOOP_PAUSE_PATTERN.match(path.name):
                seen.add(instance_id)

    live_panes = _live_tmux_panes()
    if live_panes:
        for pane in live_panes:
            seen.add(f"tmux_{pane}")

    return sorted(seen)


def _newest_instance_file_mtime(instance_id: str) -> float | None:
    """Newest mtime of any known per-instance state file. Used for staleness."""
    candidates = [
        EMPIRICA_DIR / "instance_projects" / f"{instance_id}.json",
        EMPIRICA_DIR / f"sentinel_paused_{instance_id}",
        EMPIRICA_DIR / f"loops_{instance_id}.json",
        EMPIRICA_DIR / f"listeners_{instance_id}.json",
        EMPIRICA_DIR / f"active_session_{instance_id}",
        EMPIRICA_DIR / f"hook_counters_{instance_id}.json",
        EMPIRICA_DIR / f"context_usage_{instance_id}.json",
    ]
    return _newest_mtime(candidates)


def _read_recent_events_for_instance(instance_id: str, limit: int = 5) -> list[dict]:
    """Tail ~/.empirica/loop_fires.log filtered to `instance_id`, return the
    last `limit` events parsed as dicts.

    T9 (goal f718156c): the cockpit's per-instance pane shows these as the
    "latest 5" — the single human-readable surface for ECO-decided AI work
    arriving via the listener push or content-poll catch-up. Lines that
    don't parse are skipped silently.

    Returns most-recent-first ordering."""
    log = Path.home() / ".empirica" / "loop_fires.log"
    if not log.exists():
        return []
    try:
        # Read tail — modest log size expected (one line per emission); for
        # very large files this could be optimized to seek-from-end, but
        # T6/T7 emission is content-only so the log stays small in practice.
        with open(log, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []

    out: list[dict] = []
    for ln in reversed(lines):
        ln = ln.strip()
        if not ln:
            continue
        try:
            event = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if event.get("instance_id") != instance_id:
            continue
        out.append(event)
        if len(out) >= limit:
            break
    return out


def _annotate_loops_with_systemd_state(
    instance_id: str,
    loops_dict: dict[str, Any],
) -> None:
    """For each loop with scheduler_kind='systemd', query live systemd state
    and inject systemd_active / systemd_enabled fields.

    Phase 1c-tail (goal f718156c): the TUI panel + glyph logic needs accurate
    state for systemd-managed loops because the legacy `paused` field
    (file-flag pause sidecar) doesn't apply — `systemctl --user is-active`
    is the source of truth. Best-effort: silently skip if the loop_scheduler
    module isn't importable or systemd-user isn't available.
    """
    systemd_loops = [
        name
        for name, info in loops_dict.items()
        if (info.get("scheduling") or {}).get("scheduler_kind") == "systemd-user"
    ]
    if not systemd_loops:
        return
    try:
        # T10: portable across systemd (Linux/WSL2) and launchd (macOS).
        from empirica.core.loop_scheduler import (
            LoopSchedulerUnavailable,
            get_loop_scheduler,
        )
    except Exception:
        return
    try:
        sched = get_loop_scheduler("empirica")
    except LoopSchedulerUnavailable:
        return
    try:
        for name in systemd_loops:
            try:
                st = sched.status(instance_id, name)
                loops_dict[name]["systemd_active"] = st.active
                loops_dict[name]["systemd_enabled"] = st.enabled
                loops_dict[name]["last_trigger"] = st.last_trigger
                loops_dict[name]["next_trigger"] = st.next_trigger
            except Exception:
                # Per-loop probe failure → leave fields absent. UI treats
                # absent as "unknown / unhealthy" → conservative.
                loops_dict[name]["systemd_active"] = False
                loops_dict[name]["systemd_enabled"] = False
    except Exception:  # noqa: S110 — best-effort systemd state probe
        pass


def aggregate_instance_state(
    instance_id: str,
    live_panes: set[str] | None = None,
    current_instance_id: str | None = None,
    live_claude_instance_ids: set[str] | None = None,
    live_claude_cwds: set[str] | None = None,
) -> dict[str, Any]:
    """Read all state for one instance and return a serializable dict.

    Layout intentionally matches the proposal's --json schema. Robust to
    partial state — missing pieces become null fields, not exceptions.

    `live_panes` is an optional pre-computed set of live tmux pane numbers
    (sweep optimization). `current_instance_id` exempts the running cockpit
    from liveness checks (it's alive by definition). `live_claude_instance_ids`
    (EMPIRICA_INSTANCE_ID env per live proc) is the exact, resume-proof
    liveness signal; `live_claude_cwds` is the coarse cwd fallback. Pass None
    to skip either.
    """
    project_path = _instance_project_path(instance_id)
    label = _instance_label(instance_id, project_path)

    if project_path:
        tx_state = _read_transaction_state(project_path, instance_id)
    else:
        tx_state = {
            "phase": "no-transaction",
            "transaction_id": None,
            "session_id": None,
            "transaction_age_seconds": None,
            "last_activity_iso": None,
            "last_activity_seconds": None,
            "work_type": None,
            "domain": None,
        }

    instance_mtime = _newest_instance_file_mtime(instance_id)

    liveness = is_alive(
        instance_id,
        last_activity_seconds=tx_state["last_activity_seconds"],
        live_panes=live_panes,
        current_instance_id=current_instance_id,
        project_path=project_path,
        live_claude_instance_ids=live_claude_instance_ids,
        live_claude_cwds=live_claude_cwds,
    )

    state = _derive_state_symbol(tx_state, instance_mtime, alive=liveness.alive)

    sentinel = sentinel_status(instance_id)

    # Loop registry — graceful when registry doesn't exist.
    registry = LoopRegistry(instance_id, label=label)
    loops_dict: dict[str, Any] = {}
    for entry in registry.list_loops():
        d = entry.to_dict()
        d["paused"] = is_loop_paused(instance_id, entry.name)
        loops_dict[entry.name] = d

    # systemd state annotation (Phase 1c-tail, goal f718156c): for loops
    # registered with scheduler_kind='systemd', the file-flag pause is
    # meaningless — the truth is `systemctl --user is-active`. Inject
    # `systemd_active` + `systemd_enabled` so the TUI panel + glyph
    # logic can show accurate state. Best-effort: skip silently when
    # systemd-user isn't available (macOS / Windows-native hosts).
    _annotate_loops_with_systemd_state(instance_id, loops_dict)

    # Per-loop last-notify annotation (audit log → loops by `loop:{name}` source).
    annotate_loops_with_last_notify(loops_dict)

    # Listener registry — sister to loops but event-driven.
    listener_registry = ListenerRegistry(instance_id, label=label)
    listeners_dict: dict[str, Any] = {}
    for entry in listener_registry.list_listeners():
        d = entry.to_dict()
        d["paused"] = is_listener_paused(instance_id, entry.name)
        listeners_dict[entry.name] = d

    transaction: dict[str, Any] | None
    if tx_state["transaction_id"]:
        transaction = {
            "id": tx_state["transaction_id"],
            "age_seconds": tx_state["transaction_age_seconds"],
            "work_type": tx_state["work_type"],
            "domain": tx_state["domain"],
            "criticality": tx_state["criticality"],
        }
    else:
        transaction = None

    # 'ask' supersedes the file-derived phase when CC is waiting for input.
    asking = is_asking(instance_id)
    phase = "ask" if asking and tx_state["phase"] in ("noetic", "praxic") else tx_state["phase"]

    notif = notification_summary(instance_id, project_path=project_path)

    # Compliance is project-scoped (audits the source tree, not the
    # instance's transaction state), so multiple instances of the same
    # project share the same compliance result. Embedding it per-instance
    # keeps the cockpit row self-contained — the TUI doesn't have to
    # cross-reference a separate project map.
    compliance = read_compliance_summary(project_path)

    # Services is also project-scoped — same shape, different source
    # (last `empirica scan` snapshot). Phase 2 T2 surfaces deterministic
    # Phase 1 metrics (process count, listening ports, integrity ratio);
    # auditor judgment counts will land here once Phase 2 T3 wires the
    # POSTFLIGHT coverage block.
    services = read_services_summary(project_path)

    return {
        "instance_id": instance_id,
        "ai_id": _project_ai_id(project_path),
        "label": label,
        "project_path": project_path,
        "session_id": tx_state["session_id"],
        "state": state,
        "phase": phase,
        "asking": asking,
        "transaction": transaction,
        "last_activity": tx_state["last_activity_iso"],
        "last_activity_seconds": tx_state["last_activity_seconds"],
        "alive": liveness.alive,
        "liveness_reason": liveness.reason,
        "liveness_signal": liveness.signal,
        "sentinel": {
            "paused": sentinel.paused,
            "scope": sentinel.scope,
            "since": sentinel.since,
            "reason": sentinel.reason,
        },
        "loops": loops_dict,
        "listeners": listeners_dict,
        "notifications": {
            "open_count": notif.open_count,
            "has_attention": notif.has_attention,
        },
        "compliance": compliance,
        "services": services,
        # T9: latest 5 fires-log events for the cockpit detail pane.
        # ECO-decided proposal events surface here (one row per inbox-act
        # or outbox-ack), making "notifications" the unified surface that
        # subsumes the older separate loops/listeners columns.
        "recent_events": _read_recent_events_for_instance(instance_id, limit=5),
    }


def _dedup_process_scan_overcount(instances: list[dict[str, Any]], live_cwd_counts: dict[str, int]) -> None:
    """Demote surplus process_cwd-revived instances in place.

    The cwd FALLBACK liveness signal keys on project cwd, so every stale
    instance file for a project whose cwd hosts a live claude process flips
    alive — over-counting when there are more stale files than live procs
    (the duplicate-session incident). For each project keep at most
    (live procs − instances already alive via a stronger, process-bearing
    signal) instances alive via process_cwd, preferring the most-recently
    active; demote the rest back to dead. Touches process_cwd instances
    only — never demotes a current/tmux/process_env/pid/recent_activity verdict.

    The exact env match (process_env) is instance-level and counts as strong:
    it consumes a live-proc slot but is itself never demoted here.
    """
    strong = {"current", "tmux", "process_env", "pid"}  # each = a real process

    by_project: dict[str, list[dict[str, Any]]] = {}
    for inst in instances:
        if not inst.get("alive"):
            continue
        pp = inst.get("project_path")
        if not pp:
            continue
        by_project.setdefault(os.path.realpath(pp), []).append(inst)

    for rp, insts in by_project.items():
        scanned = [i for i in insts if i.get("liveness_signal") == "process_cwd"]
        if not scanned:
            continue
        strong_count = sum(1 for i in insts if i.get("liveness_signal") in strong)
        budget = max(0, live_cwd_counts.get(rp, 0) - strong_count)
        if len(scanned) <= budget:
            continue
        # Most-recently-active first (smallest last_activity_seconds); None last.
        scanned.sort(
            key=lambda i: i.get("last_activity_seconds") if i.get("last_activity_seconds") is not None else float("inf")
        )
        for surplus in scanned[budget:]:
            surplus["alive"] = False
            surplus["liveness_signal"] = ""
            surplus["liveness_reason"] = "duplicate stale session — no distinct live claude process"


def aggregate_all(include_dead: bool = False) -> dict[str, Any]:
    """Scan and aggregate every discoverable instance.

    By default returns only LIVE instances (tmux pane exists, PPID alive,
    or recent activity). Set `include_dead=True` for diagnostic mode that
    surfaces every state-file footprint regardless of whether the
    underlying Claude process still exists.
    """
    # Pre-compute the live tmux pane set once, share across instances.
    live_panes = _live_tmux_panes()

    # Walk the process table once for live claude sessions. instance_ids
    # (EMPIRICA_INSTANCE_ID env) is the exact, resume-proof primary signal;
    # cwd_counts is the coarse fallback + feeds the count-aware dedup.
    scan = scan_live_claude()
    live_iids = scan.instance_ids if scan else None
    live_cwds = set(scan.cwd_counts) if scan else None

    # Resolve the current instance lazily — exempts the running cockpit
    # from liveness checks (it's alive by definition, even if PID/PPID
    # weren't captured by an old session-init).
    try:
        from empirica.utils.session_resolver import get_instance_id

        current_id = get_instance_id()
    except Exception:
        current_id = None

    instances = [
        aggregate_instance_state(
            i,
            live_panes=live_panes,
            current_instance_id=current_id,
            live_claude_instance_ids=live_iids,
            live_claude_cwds=live_cwds,
        )
        for i in discover_instances()
    ]

    # Count-aware dedup: the cwd FALLBACK signal is project-level, so several
    # stale instance files for one project would all flip alive. Cap the
    # process_cwd-revived instances per project at the live-proc count. The
    # exact env-match (process_env) is instance-level and needs no dedup.
    if scan:
        _dedup_process_scan_overcount(instances, scan.cwd_counts)

    if not include_dead:
        instances = [i for i in instances if i.get("alive")]

    loops_registered = sum(len(i["loops"]) for i in instances)
    loops_paused = sum(1 for i in instances for loop in i["loops"].values() if loop.get("paused"))
    listeners_registered = sum(len(i.get("listeners") or {}) for i in instances)
    listeners_paused = sum(
        1 for i in instances for listener in (i.get("listeners") or {}).values() if listener.get("paused")
    )
    active_tx = sum(1 for i in instances if i["phase"] in ("noetic", "praxic"))

    # T11: auto-accept mode (per-user, cortex-persisted). Cached at module
    # scope so the TUI's 5s refresh doesn't hammer cortex. None → state
    # unknown / cortex unreachable / endpoint not shipped — TUI hides chip.
    try:
        from empirica.core.cockpit.auto_accept import fetch_auto_accept_mode

        auto_accept = fetch_auto_accept_mode()
    except Exception:
        auto_accept = None

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "instances": instances,
        "summary": {
            "instances": len(instances),
            "loops_registered": loops_registered,
            "loops_paused": loops_paused,
            "listeners_registered": listeners_registered,
            "listeners_paused": listeners_paused,
            "active_tx": active_tx,
            "open_notifications": notifications_total(),
            "notify_dispatcher": build_notify_dispatcher_block(),
            "auto_accept": auto_accept,
        },
    }


def discover_dead_instances() -> list[str]:
    """Return instance_ids that fail the liveness check.

    Used by `empirica instance prune` for bulk cleanup. Skips the current
    instance even if it lacks PID capture (it's running this code).

    Applies the same count-aware dedup as the cockpit, so a superseded
    *fallback ghost* — an old tty/pane-name record (``tmux_N`` / ``term_*``)
    whose project still hosts a live claude declared under a canonical
    ``EMPIRICA_INSTANCE_ID`` — is reaped instead of being kept alive by the
    project-level cwd signal. Without the dedup such ghosts read alive via
    ``process_cwd`` and survive prune forever (the cockpit dedups them away,
    but prune never cleaned them up).
    """
    live_panes = _live_tmux_panes()
    # Same multiplexer-agnostic signals the cockpit uses — so `prune` never
    # offers to kill a Claude that's alive under a non-tmux multiplexer or
    # whose captured PID went stale across a manual restart.
    scan = scan_live_claude()
    live_iids = scan.instance_ids if scan else None
    live_cwds = set(scan.cwd_counts) if scan else None
    try:
        from empirica.utils.session_resolver import get_instance_id

        current_id = get_instance_id()
    except Exception:
        current_id = None

    records: list[dict[str, Any]] = []
    for iid in discover_instances():
        # We need last_activity to evaluate the recent-activity fallback,
        # so a cheap aggregate is unavoidable. _read_transaction_state is
        # the part that costs file I/O; we already pay it for status.
        project_path = _instance_project_path(iid)
        last_activity = None
        if project_path:
            tx = _read_transaction_state(project_path, iid)
            last_activity = tx.get("last_activity_seconds")
        liveness = is_alive(
            iid,
            last_activity_seconds=last_activity,
            live_panes=live_panes,
            current_instance_id=current_id,
            project_path=project_path,
            live_claude_instance_ids=live_iids,
            live_claude_cwds=live_cwds,
        )
        records.append(
            {
                "instance_id": iid,
                "project_path": project_path,
                "last_activity_seconds": last_activity,
                "alive": liveness.alive,
                "liveness_signal": liveness.signal,
            }
        )

    # Reap superseded fallback ghosts: demote cwd-only revivals that exceed the
    # live-proc count for their project (env-matched canonicals are strong and
    # never demoted), then anything not-alive is dead.
    if scan and scan.cwd_counts:
        _dedup_process_scan_overcount(records, scan.cwd_counts)

    return [r["instance_id"] for r in records if not r["alive"]]


__all__ = [
    "aggregate_all",
    "aggregate_instance_state",
    "discover_dead_instances",
    "discover_instances",
]
