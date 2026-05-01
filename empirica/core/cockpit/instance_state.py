"""Instance discovery + state aggregation for the cockpit `status` command.

Discovery is via state-file scan only — no tmux, no /proc, no process scanning.
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
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from empirica.core.cockpit.compliance_view import read_compliance_summary
from empirica.core.cockpit.services_view import read_services_summary
from empirica.core.cockpit.enrichment import (
    is_asking,
    notification_summary,
    notifications_total,
)
from empirica.core.cockpit.listener_registry import (
    ListenerRegistry,
    is_listener_paused,
)
from empirica.core.cockpit.liveness import _live_tmux_panes, is_alive
from empirica.core.cockpit.loop_registry import LoopRegistry, is_loop_paused
from empirica.core.cockpit.notify_dispatcher_view import (
    annotate_loops_with_last_notify,
    build_notify_dispatcher_block,
)
from empirica.core.cockpit.sentinel_pause import sentinel_status

EMPIRICA_DIR = Path.home() / '.empirica'

# Stale thresholds — drive the state symbol in --pretty.
ACTIVE_WINDOW_S = 60
IDLE_WINDOW_S = 30 * 60
ABANDONED_WINDOW_S = 24 * 60 * 60

# Files we scan to discover instance_ids.
INSTANCE_GLOBS = (
    'instance_projects/*.json',
    'sentinel_paused_*',
    'loops_*.json',
    'listeners_*.json',
    'active_session_*',
    'hook_counters_*.json',
    'context_usage_*.json',
)

# Files to exclude from instance discovery — they match the glob but are
# not per-instance footprints (e.g. the global pause file).
INSTANCE_EXCLUDE = {
    'sentinel_paused',  # global pause file (no instance suffix)
    'active_session',  # legacy global session
    'active_transaction',  # legacy global transaction
}

# Loop pause files use a different suffix shape — strip the loop component.
LOOP_PAUSE_PATTERN = re.compile(r'^loop_paused_(.+?)_(.+)$')


def _instance_id_from_filename(filename: str) -> str | None:
    """Extract instance_id from a state file name. Returns None if not parseable."""
    if filename in INSTANCE_EXCLUDE:
        return None

    # JSON files: strip .json suffix
    name = filename[:-5] if filename.endswith('.json') else filename

    # Strip known prefixes
    prefixes = (
        'sentinel_paused_',
        'loops_',
        'listeners_',
        'active_session_',
        'active_transaction_',
        'hook_counters_',
        'context_usage_',
        'cortex_remote_cache_',
        'pre_tx_calls_',
    )
    for prefix in prefixes:
        if name.startswith(prefix):
            return name[len(prefix):]

    # instance_projects/{id}.json — name is just the instance_id
    return name


def _instance_label(instance_id: str, project_path: str | None = None) -> str:
    """Resolve the human-readable label.

    Priority:
    1. ~/.empirica/instance_label_{id}    (manual override)
    2. basename of project_path           (matches what statusline shows)
    3. instance_id                        (last-resort fallback)
    """
    label_file = EMPIRICA_DIR / f'instance_label_{instance_id}'
    if label_file.exists():
        try:
            text = label_file.read_text(encoding='utf-8').strip()
            if text:
                return text.splitlines()[0].strip()
        except OSError:
            pass
    if project_path:
        name = Path(project_path).name
        if name:
            return name
    return instance_id


def _instance_project_path(instance_id: str) -> str | None:
    """Return the project_path the instance is currently bound to, or None."""
    candidate = EMPIRICA_DIR / 'instance_projects' / f'{instance_id}.json'
    if candidate.exists():
        try:
            with open(candidate, encoding='utf-8') as f:
                data = json.load(f)
            project_path = data.get('project_path')
            if isinstance(project_path, str) and project_path:
                return project_path
        except (OSError, json.JSONDecodeError):
            pass

    # Fallback: parse legacy active_session_{id}
    session_file = EMPIRICA_DIR / f'active_session_{instance_id}'
    if session_file.exists():
        try:
            with open(session_file, encoding='utf-8') as f:
                data = json.load(f)
            project_path = data.get('project_path')
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
    suffix = f'_{instance_id}'
    empirica_dir = Path(project_path) / '.empirica'
    tx_file = empirica_dir / f'active_transaction{suffix}.json'
    counters_file = empirica_dir / f'hook_counters{suffix}.json'

    result: dict[str, Any] = {
        'phase': 'no-transaction',
        'transaction_id': None,
        'session_id': None,
        'transaction_age_seconds': None,
        'last_activity_iso': None,
        'last_activity_seconds': None,
        'work_type': None,
        'domain': None,
        'criticality': None,
    }

    if not tx_file.exists():
        return result

    try:
        with open(tx_file, encoding='utf-8') as f:
            tx = json.load(f)
    except (OSError, json.JSONDecodeError):
        return result

    result['transaction_id'] = tx.get('transaction_id')
    result['session_id'] = tx.get('session_id')
    result['work_type'] = tx.get('work_type')
    result['domain'] = tx.get('domain')
    result['criticality'] = tx.get('criticality')

    preflight_ts = tx.get('preflight_timestamp')
    now = datetime.now(tz=UTC).timestamp()
    if isinstance(preflight_ts, (int, float)):
        result['transaction_age_seconds'] = max(0.0, now - preflight_ts)

    # Last activity = newest mtime across tx + counters file
    last_mtime = _newest_mtime([tx_file, counters_file])
    if last_mtime is not None:
        result['last_activity_iso'] = datetime.fromtimestamp(last_mtime, tz=UTC).isoformat()
        result['last_activity_seconds'] = max(0.0, now - last_mtime)

    status = tx.get('status', 'open')
    if status == 'closed':
        result['phase'] = 'closed'
        return result

    # Status open — distinguish noetic vs praxic from hook counters.
    praxic = 0
    if counters_file.exists():
        try:
            with open(counters_file, encoding='utf-8') as f:
                counters = json.load(f)
            praxic = int(counters.get('praxic_tool_calls', 0) or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            praxic = 0
    result['phase'] = 'praxic' if praxic > 0 else 'noetic'
    return result


def _derive_state_symbol(tx_state: dict[str, Any], instance_files_mtime: float | None) -> str:
    """Derive the cockpit state from transaction + file age.

    Returns one of: 'active' | 'idle' | 'stuck' | 'closed' | 'no-claude'
    """
    last_seconds = tx_state.get('last_activity_seconds')

    # No transaction at all — fall back to instance file age.
    if tx_state['phase'] == 'no-transaction':
        if instance_files_mtime is None:
            return 'no-claude'
        age = datetime.now(tz=UTC).timestamp() - instance_files_mtime
        if age > ABANDONED_WINDOW_S:
            return 'no-claude'
        return 'closed'

    if tx_state['phase'] == 'closed':
        return 'closed'

    # Open transaction — bucket on last_activity.
    if last_seconds is None:
        return 'closed'
    if last_seconds < ACTIVE_WINDOW_S:
        return 'active'
    if last_seconds < IDLE_WINDOW_S:
        return 'idle'
    return 'stuck'


def discover_instances() -> list[str]:
    """Return the sorted list of known instance_ids.

    Walks the standard state-file globs under ~/.empirica/ and unions the
    derived instance_ids. Excludes loop pause sidecars (different suffix
    shape) and known global files.
    """
    EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()

    for glob in INSTANCE_GLOBS:
        for path in EMPIRICA_DIR.glob(glob):
            instance_id = _instance_id_from_filename(path.name)
            if instance_id and not LOOP_PAUSE_PATTERN.match(path.name):
                seen.add(instance_id)

    return sorted(seen)


def _newest_instance_file_mtime(instance_id: str) -> float | None:
    """Newest mtime of any known per-instance state file. Used for staleness."""
    candidates = [
        EMPIRICA_DIR / 'instance_projects' / f'{instance_id}.json',
        EMPIRICA_DIR / f'sentinel_paused_{instance_id}',
        EMPIRICA_DIR / f'loops_{instance_id}.json',
        EMPIRICA_DIR / f'listeners_{instance_id}.json',
        EMPIRICA_DIR / f'active_session_{instance_id}',
        EMPIRICA_DIR / f'hook_counters_{instance_id}.json',
        EMPIRICA_DIR / f'context_usage_{instance_id}.json',
    ]
    return _newest_mtime(candidates)


def aggregate_instance_state(
    instance_id: str,
    live_panes: set[str] | None = None,
    current_instance_id: str | None = None,
) -> dict[str, Any]:
    """Read all state for one instance and return a serializable dict.

    Layout intentionally matches the proposal's --json schema. Robust to
    partial state — missing pieces become null fields, not exceptions.

    `live_panes` is an optional pre-computed set of live tmux pane numbers
    (sweep optimization). `current_instance_id` exempts the running cockpit
    from liveness checks (it's alive by definition).
    """
    project_path = _instance_project_path(instance_id)
    label = _instance_label(instance_id, project_path)

    if project_path:
        tx_state = _read_transaction_state(project_path, instance_id)
    else:
        tx_state = {
            'phase': 'no-transaction',
            'transaction_id': None,
            'session_id': None,
            'transaction_age_seconds': None,
            'last_activity_iso': None,
            'last_activity_seconds': None,
            'work_type': None,
            'domain': None,
        }

    instance_mtime = _newest_instance_file_mtime(instance_id)
    state = _derive_state_symbol(tx_state, instance_mtime)

    sentinel = sentinel_status(instance_id)

    # Loop registry — graceful when registry doesn't exist.
    registry = LoopRegistry(instance_id, label=label)
    loops_dict: dict[str, Any] = {}
    for entry in registry.list_loops():
        d = entry.to_dict()
        d['paused'] = is_loop_paused(instance_id, entry.name)
        loops_dict[entry.name] = d

    # Per-loop last-notify annotation (audit log → loops by `loop:{name}` source).
    annotate_loops_with_last_notify(loops_dict, instance_label=label)

    # Listener registry — sister to loops but event-driven.
    listener_registry = ListenerRegistry(instance_id, label=label)
    listeners_dict: dict[str, Any] = {}
    for entry in listener_registry.list_listeners():
        d = entry.to_dict()
        d['paused'] = is_listener_paused(instance_id, entry.name)
        listeners_dict[entry.name] = d

    transaction: dict[str, Any] | None
    if tx_state['transaction_id']:
        transaction = {
            'id': tx_state['transaction_id'],
            'age_seconds': tx_state['transaction_age_seconds'],
            'work_type': tx_state['work_type'],
            'domain': tx_state['domain'],
            'criticality': tx_state['criticality'],
        }
    else:
        transaction = None

    liveness = is_alive(
        instance_id,
        last_activity_seconds=tx_state['last_activity_seconds'],
        live_panes=live_panes,
        current_instance_id=current_instance_id,
    )

    # 'ask' supersedes the file-derived phase when CC is waiting for input.
    asking = is_asking(instance_id)
    phase = 'ask' if asking and tx_state['phase'] in ('noetic', 'praxic') else tx_state['phase']

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
        'instance_id': instance_id,
        'label': label,
        'project_path': project_path,
        'session_id': tx_state['session_id'],
        'state': state,
        'phase': phase,
        'asking': asking,
        'transaction': transaction,
        'last_activity': tx_state['last_activity_iso'],
        'last_activity_seconds': tx_state['last_activity_seconds'],
        'alive': liveness.alive,
        'liveness_reason': liveness.reason,
        'sentinel': {
            'paused': sentinel.paused,
            'scope': sentinel.scope,
            'since': sentinel.since,
            'reason': sentinel.reason,
        },
        'loops': loops_dict,
        'listeners': listeners_dict,
        'notifications': {
            'open_count': notif.open_count,
            'has_attention': notif.has_attention,
        },
        'compliance': compliance,
        'services': services,
    }




def aggregate_all(include_dead: bool = False) -> dict[str, Any]:
    """Scan and aggregate every discoverable instance.

    By default returns only LIVE instances (tmux pane exists, PPID alive,
    or recent activity). Set `include_dead=True` for diagnostic mode that
    surfaces every state-file footprint regardless of whether the
    underlying Claude process still exists.
    """
    # Pre-compute the live tmux pane set once, share across instances.
    live_panes = _live_tmux_panes()

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
            i, live_panes=live_panes, current_instance_id=current_id,
        )
        for i in discover_instances()
    ]

    if not include_dead:
        instances = [i for i in instances if i.get('alive')]

    loops_registered = sum(len(i['loops']) for i in instances)
    loops_paused = sum(
        1 for i in instances for loop in i['loops'].values() if loop.get('paused')
    )
    listeners_registered = sum(len(i.get('listeners') or {}) for i in instances)
    listeners_paused = sum(
        1 for i in instances
        for listener in (i.get('listeners') or {}).values()
        if listener.get('paused')
    )
    active_tx = sum(
        1 for i in instances if i['phase'] in ('noetic', 'praxic')
    )

    return {
        'generated_at': datetime.now(tz=UTC).isoformat(),
        'instances': instances,
        'summary': {
            'instances': len(instances),
            'loops_registered': loops_registered,
            'loops_paused': loops_paused,
            'listeners_registered': listeners_registered,
            'listeners_paused': listeners_paused,
            'active_tx': active_tx,
            'open_notifications': notifications_total(),
            'notify_dispatcher': build_notify_dispatcher_block(),
        },
    }


def discover_dead_instances() -> list[str]:
    """Return instance_ids that fail the liveness check.

    Used by `empirica instance prune` for bulk cleanup. Skips the current
    instance even if it lacks PID capture (it's running this code).
    """
    live_panes = _live_tmux_panes()
    try:
        from empirica.utils.session_resolver import get_instance_id
        current_id = get_instance_id()
    except Exception:
        current_id = None

    dead: list[str] = []
    for iid in discover_instances():
        # We need last_activity to evaluate the recent-activity fallback,
        # so a cheap aggregate is unavoidable. _read_transaction_state is
        # the part that costs file I/O; we already pay it for status.
        project_path = _instance_project_path(iid)
        last_activity = None
        if project_path:
            tx = _read_transaction_state(project_path, iid)
            last_activity = tx.get('last_activity_seconds')
        liveness = is_alive(
            iid,
            last_activity_seconds=last_activity,
            live_panes=live_panes,
            current_instance_id=current_id,
        )
        if not liveness.alive:
            dead.append(iid)
    return dead


__all__ = [
    'aggregate_all',
    'aggregate_instance_state',
    'discover_dead_instances',
    'discover_instances',
]
