"""Handler for `empirica scan` (Phase 1 — one-shot).

Per docs/architecture/PROPOSAL_AI_SERVICE_SCANNER.md.

The handler is intentionally thin: it owns format choice, optional
persistence to ``~/.empirica/scans/``, and exit code semantics. All data
collection lives in :mod:`empirica.core.scanner`.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from empirica.core.scanner import collect_snapshot
from empirica.core.scanner.report import render_markdown

logger = logging.getLogger(__name__)


def _empirica_home() -> Path:
    home = Path(os.path.expanduser('~/.empirica'))
    home.mkdir(parents=True, exist_ok=True)
    return home


def _persist_scan(snapshot_dict: dict, project_id: str | None) -> dict[str, str]:
    """Write the snapshot to disk for cockpit consumption.

    Returns a small dict with the paths written, so the JSON output can
    surface them.
    """
    paths: dict[str, str] = {}
    home = _empirica_home()

    scans_dir = home / 'scans'
    scans_dir.mkdir(parents=True, exist_ok=True)
    scan_path = scans_dir / f"{snapshot_dict['scan_id']}.json"
    with scan_path.open('w', encoding='utf-8') as fh:
        json.dump(snapshot_dict, fh, indent=2, default=str)
    paths['scan_path'] = str(scan_path)

    if project_id:
        last_path = home / f"last_scan_{project_id}.json"
        with last_path.open('w', encoding='utf-8') as fh:
            json.dump(snapshot_dict, fh, indent=2, default=str)
        paths['last_scan_path'] = str(last_path)

        history_path = home / f"scan_history_{project_id}.jsonl"
        # Append a one-line summary per scan — keeps the audit trail cheap
        history_summary = {
            'scan_id': snapshot_dict['scan_id'],
            'started_at': snapshot_dict['started_at'],
            'finished_at': snapshot_dict.get('finished_at'),
            'host': snapshot_dict['host'],
            'coverage': snapshot_dict.get('snapshot', {}).get('coverage', {}),
            'errors': len(snapshot_dict.get('errors') or []),
        }
        with history_path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(history_summary, default=str) + '\n')
        paths['history_path'] = str(history_path)

    return paths


def _resolve_project_id(args) -> str | None:
    project_id = getattr(args, 'project_id', None)
    if project_id:
        return project_id
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        path = R.project_path()
        if path:
            return R.project_id_from_db(path)
    except Exception:
        return None
    return None


def _render_explain_hand_off(snapshot_dict: dict, saved_paths: dict[str, str],
                             project_id: str | None) -> str:
    """Render the human-readable hand-off when ``--explain`` is requested.

    The hand-off points the AI at the ``/services-auditor`` skill and
    surfaces the saved snapshot path so the auditor knows where to read.
    No judgment happens here — judgment is the skill's job, executed by
    whatever AI session reads this output.
    """
    cov = snapshot_dict.get('snapshot', {}).get('coverage', {})
    last_path = saved_paths.get('last_scan_path') or saved_paths.get('scan_path', '?')

    lines = [
        "🔍 Scanner snapshot ready for AI judgment (Phase 2).",
        "",
        f"   scan_id: {snapshot_dict.get('scan_id')}",
        f"   saved to: {last_path}",
        f"   processes captured: {cov.get('processes', {}).get('succeeded', '?')} "
        f"of {cov.get('processes', {}).get('attempted', '?')} "
        f"({cov.get('processes', {}).get('ratio', 0) * 100:.1f}%)",
        f"   listening ports: {len(snapshot_dict.get('snapshot', {}).get('network', {}).get('listening_ports') or [])}",
        f"   project_id: {project_id or '(unresolved)'}",
        "",
        "Next: invoke `/services-auditor` to read the snapshot, judge each",
        "AI-touching entry against the bundled security corpus, and emit",
        "findings/assumptions/unknowns with confidence + cited corpus sections.",
        "Citation coverage and process coverage are tracked explicitly in the",
        "auditor's POSTFLIGHT summary.",
        "",
        "If the AI doesn't load the skill automatically, the skill lives at:",
        "  empirica/plugins/claude-code-integration/skills/services-auditor/SKILL.md",
    ]
    return '\n'.join(lines) + '\n'


def _read_history(project_id: str) -> list[dict]:
    """Read scan_history_<project_id>.jsonl. Returns oldest→newest."""
    home = _empirica_home()
    history_path = home / f"scan_history_{project_id}.jsonl"
    if not history_path.exists():
        return []
    entries: list[dict] = []
    try:
        with history_path.open(encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    # A malformed line shouldn't kill the audit trail —
                    # skip and keep walking.
                    continue
    except OSError:
        return []
    return entries


def _read_snapshot(scan_id: str) -> dict | None:
    """Load a full snapshot from ~/.empirica/scans/<scan_id>.json."""
    home = _empirica_home()
    path = home / 'scans' / f"{scan_id}.json"
    if not path.exists():
        return None
    try:
        with path.open(encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _resolve_scan_id_prefix(prefix: str, project_id: str | None) -> str | None:
    """Match a scan_id by prefix against history entries OR scans/ files.

    Helps the operator paste the first 8 chars instead of the full UUID.
    Falls back to scanning the scans/ directory when project_id is missing.
    """
    if not prefix:
        return None
    # Try the project history first — bounded read, ordered.
    if project_id:
        for entry in _read_history(project_id):
            sid = entry.get('scan_id') or ''
            if sid.startswith(prefix):
                return sid
    # Directory walk fallback — covers scans run without project_id binding.
    home = _empirica_home()
    scans_dir = home / 'scans'
    if scans_dir.exists():
        for path in scans_dir.glob(f'{prefix}*.json'):
            return path.stem
    return None


def _summarize_processes(snapshot: dict) -> dict[str, int]:
    """Reduce a snapshot's process list to a per-name count for diffing."""
    procs = snapshot.get('snapshot', {}).get('processes') or []
    counts: dict[str, int] = {}
    for proc in procs:
        if not isinstance(proc, dict):
            continue
        name = proc.get('name') or proc.get('comm') or '?'
        counts[name] = counts.get(name, 0) + 1
    return counts


def _summarize_listeners(snapshot: dict) -> set[str]:
    """Listening-port set in 'host:port' form for set-diff."""
    listeners = snapshot.get('snapshot', {}).get('network', {}).get('listening_ports') or []
    out: set[str] = set()
    for entry in listeners:
        if not isinstance(entry, dict):
            continue
        host = entry.get('host') or entry.get('addr') or '?'
        port = entry.get('port') or '?'
        out.add(f'{host}:{port}')
    return out


def handle_scan_history_command(args) -> int:
    """`empirica scan-history` — list past scan snapshots for the project.

    Reads ~/.empirica/scan_history_<project_id>.jsonl and returns the
    audit trail (newest first, capped by --limit). Each row has scan_id,
    timestamp, host, coverage, error count.
    """
    project_id = _resolve_project_id(args)
    output_format = getattr(args, 'output', 'human')
    limit = int(getattr(args, 'limit', 20) or 20)

    if not project_id:
        msg = "no project_id resolved — pass --project-id or run inside a bound project"
        if output_format == 'json':
            print(json.dumps({'ok': False, 'error': msg}))
        else:
            print(f'❌ {msg}')
        return 1

    entries = _read_history(project_id)
    # Newest first
    entries.reverse()
    if limit > 0:
        entries = entries[:limit]

    if output_format == 'json':
        print(json.dumps({
            'ok': True,
            'project_id': project_id,
            'count': len(entries),
            'entries': entries,
        }, indent=2, default=str))
        return 0

    if not entries:
        print('(no scan history for this project — run `empirica scan --save`)')
        return 0
    print(f'🔍 scan history — project {project_id[:8]}... ({len(entries)} shown)')
    for entry in entries:
        sid = (entry.get('scan_id') or '?')[:8]
        ts = entry.get('finished_at') or entry.get('started_at') or '?'
        host = entry.get('host', '?')
        cov = entry.get('coverage', {}) or {}
        proc_cov = cov.get('processes', {}) or {}
        ratio = proc_cov.get('ratio', 0) or 0
        errs = entry.get('errors', 0)
        err_part = f' ⚠ {errs} errors' if errs else ''
        print(f'  {sid}  {ts}  {host}  proc-cov {ratio*100:.0f}%{err_part}')
    return 0


def handle_scan_show_command(args) -> int:
    """`empirica scan-show <scan_id>` — print a saved snapshot.

    Accepts a UUID prefix (≥8 chars). Output format defaults to markdown
    (re-renders via report.render_markdown), or json for the raw payload.
    """
    scan_id = getattr(args, 'scan_id', None)
    output_format = getattr(args, 'output', 'markdown')
    if not scan_id:
        print(json.dumps({'ok': False, 'error': 'scan_id required'}))
        return 2

    project_id = _resolve_project_id(args)
    resolved = _resolve_scan_id_prefix(scan_id, project_id) or scan_id
    snapshot = _read_snapshot(resolved)
    if snapshot is None:
        msg = f'no snapshot found for scan_id prefix {scan_id!r}'
        if output_format == 'json':
            print(json.dumps({'ok': False, 'error': msg}))
        else:
            print(f'❌ {msg}')
        return 1

    if output_format == 'json':
        print(json.dumps({'ok': True, 'snapshot': snapshot}, indent=2, default=str))
        return 0

    # Markdown — reconstruct via report renderer. The renderer expects a
    # Snapshot object, but we have the dict form on disk. Rebuild a
    # minimal renderable view.
    from empirica.core.scanner.snapshot import Snapshot  # local import — keeps cli module light
    try:
        snap_obj = Snapshot.from_dict(snapshot) if hasattr(Snapshot, 'from_dict') else None
    except Exception:
        snap_obj = None
    if snap_obj is not None:
        print(render_markdown(snap_obj))
    else:
        # Renderer can't round-trip — fall back to a compact summary.
        cov = snapshot.get('snapshot', {}).get('coverage', {})
        print(f"# Scan {resolved[:8]}")
        print(f"started: {snapshot.get('started_at')}")
        print(f"host: {snapshot.get('host')}")
        print(f"processes: {len(snapshot.get('snapshot', {}).get('processes') or [])}")
        print(f"coverage: {cov}")
        print("\n_(use `--output json` for the full snapshot)_")
    return 0


def _compute_scan_diff(snap_a: dict, snap_b: dict) -> dict:
    """Pure-data diff: process + listener deltas plus coverage. Caller
    decides how to render."""
    proc_a = _summarize_processes(snap_a)
    proc_b = _summarize_processes(snap_b)
    proc_changed = [
        {'name': name, 'before': proc_a[name], 'after': proc_b[name]}
        for name in sorted(set(proc_a) & set(proc_b))
        if proc_a[name] != proc_b[name]
    ]
    listen_a = _summarize_listeners(snap_a)
    listen_b = _summarize_listeners(snap_b)
    return {
        'processes': {
            'added': sorted(set(proc_b) - set(proc_a)),
            'removed': sorted(set(proc_a) - set(proc_b)),
            'changed': proc_changed,
        },
        'listeners': {
            'added': sorted(listen_b - listen_a),
            'removed': sorted(listen_a - listen_b),
        },
        'coverage': {
            'a': snap_a.get('snapshot', {}).get('coverage', {}),
            'b': snap_b.get('snapshot', {}).get('coverage', {}),
        },
    }


def _print_scan_diff_human(a_resolved: str, b_resolved: str, diff: dict) -> None:
    """Render _compute_scan_diff output as the human-friendly summary."""
    print(f'🔍 scan diff: {a_resolved[:8]} → {b_resolved[:8]}')
    procs = diff['processes']
    if procs['added'] or procs['removed'] or procs['changed']:
        print('   processes:')
        for n in procs['added']:
            print(f'     + {n}')
        for n in procs['removed']:
            print(f'     - {n}')
        for c in procs['changed']:
            print(f'     ~ {c["name"]} ({c["before"]} → {c["after"]})')
    else:
        print('   processes: no changes')
    listeners = diff['listeners']
    if listeners['added'] or listeners['removed']:
        print('   listening ports:')
        for n in listeners['added']:
            print(f'     + {n}')
        for n in listeners['removed']:
            print(f'     - {n}')
    else:
        print('   listening ports: no changes')


def handle_scan_diff_command(args) -> int:
    """`empirica scan-diff <a> <b>` — compare two snapshots.

    Reports added/removed processes (by name) and added/removed
    listening ports. Coverage delta on the way too. Both args accept
    UUID prefixes.
    """
    scan_a = getattr(args, 'scan_id_a', None)
    scan_b = getattr(args, 'scan_id_b', None)
    output_format = getattr(args, 'output', 'human')
    if not (scan_a and scan_b):
        print(json.dumps({'ok': False, 'error': 'both scan_id_a and scan_id_b required'}))
        return 2

    project_id = _resolve_project_id(args)
    a_resolved = _resolve_scan_id_prefix(scan_a, project_id) or scan_a
    b_resolved = _resolve_scan_id_prefix(scan_b, project_id) or scan_b
    snap_a = _read_snapshot(a_resolved)
    snap_b = _read_snapshot(b_resolved)
    if snap_a is None or snap_b is None:
        missing = [s for s, snap in ((scan_a, snap_a), (scan_b, snap_b)) if snap is None]
        msg = f'snapshot not found: {", ".join(missing)}'
        if output_format == 'json':
            print(json.dumps({'ok': False, 'error': msg}))
        else:
            print(f'❌ {msg}')
        return 1

    diff = _compute_scan_diff(snap_a, snap_b)
    payload = {
        'ok': True,
        'a': {'scan_id': a_resolved, 'started_at': snap_a.get('started_at')},
        'b': {'scan_id': b_resolved, 'started_at': snap_b.get('started_at')},
        **diff,
    }

    if output_format == 'json':
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_scan_diff_human(a_resolved, b_resolved, diff)
    return 0


def _emit_audit_notification(_scan_id: str, diff: dict,
                              project_id: str | None) -> dict:
    """Emit a notification when a services audit detects novel additions.

    Returns a small status dict so callers know whether anything was
    sent. Failure to dispatch isn't fatal — the scan + diff already
    happened and are persisted.
    """
    proc_added = diff.get('processes', {}).get('added') or []
    listen_added = diff.get('listeners', {}).get('added') or []
    if not (proc_added or listen_added):
        return {'emitted': False, 'reason': 'no novelty'}

    try:
        from empirica.core.notify.config import load_config
        from empirica.core.notify.dispatcher import dispatch
        from empirica.core.notify.event import NotifyEvent
    except Exception as exc:
        return {'emitted': False, 'reason': f'notify import failed: {exc}'}

    parts = []
    if proc_added:
        parts.append(f'+{len(proc_added)} processes ({", ".join(proc_added[:3])}'
                     f'{"…" if len(proc_added) > 3 else ""})')
    if listen_added:
        parts.append(f'+{len(listen_added)} listeners')
    summary = '; '.join(parts)

    event = NotifyEvent(
        severity='warning',
        title='Services audit: new running services detected',
        message=summary,
        rationale=(
            'biweekly services-audit cron noticed processes or listening '
            'ports that were not in the previous snapshot'
        ),
        source='loop:services-audit',
        tags=['services-audit', 'security'],
    )
    try:
        config = load_config()
        result = dispatch(event, config, project_id=project_id)
        return {
            'emitted': True,
            'backend': result.resolved_backend,
            'fell_back': result.fell_back,
        }
    except Exception as exc:
        return {'emitted': False, 'reason': f'dispatch failed: {exc}'}


def handle_services_audit_command(args) -> int:
    """`empirica services-audit` — one fire of the biweekly audit loop.

    Runs scan --save, diffs against the previous snapshot in the
    project's history, and emits a notification when novel services
    appear. Returns structured JSON the loop body can read to set its
    heartbeat result (found / empty / fail).
    """
    from empirica.core.scanner import collect_snapshot
    output_format = getattr(args, 'output', 'json')
    project_id = _resolve_project_id(args)
    skip_notify = getattr(args, 'no_notify', False)

    if not project_id:
        msg = "no project_id resolved — services-audit needs a project context"
        if output_format == 'json':
            print(json.dumps({'ok': False, 'error': msg, 'result': 'fail'}))
        else:
            print(f'❌ {msg}')
        return 1

    # 1) Capture the new snapshot.
    try:
        snapshot = collect_snapshot()
    except Exception as exc:
        msg = f'scan failed: {exc}'
        if output_format == 'json':
            print(json.dumps({'ok': False, 'error': msg, 'result': 'fail'}))
        else:
            print(f'❌ {msg}')
        return 1

    snapshot_dict = snapshot.to_dict()
    saved_paths = _persist_scan(snapshot_dict, project_id)

    # 2) Find the previous entry — history is ordered oldest→newest, so
    # the second-to-last is the prior fire (last is the one we just wrote).
    history = _read_history(project_id)
    prior_id = None
    if len(history) >= 2:
        prior_id = history[-2].get('scan_id')

    diff: dict = {}
    if prior_id:
        prior_snapshot = _read_snapshot(prior_id)
        if prior_snapshot is not None:
            diff = _compute_scan_diff(prior_snapshot, snapshot_dict)

    # 3) Decide loop result + (maybe) fire a notification.
    novelty = bool(
        diff.get('processes', {}).get('added')
        or diff.get('listeners', {}).get('added')
    )
    result_kind = 'found' if novelty else 'empty'

    notify_status: dict = {'emitted': False, 'reason': 'skipped'}
    if novelty and not skip_notify:
        notify_status = _emit_audit_notification(
            snapshot_dict.get('scan_id') or '?', diff, project_id,
        )
    elif not novelty:
        notify_status['reason'] = 'no novelty'

    payload = {
        'ok': True,
        'project_id': project_id,
        'scan_id': snapshot_dict.get('scan_id'),
        'prior_scan_id': prior_id,
        'result': result_kind,        # consumed by `loop heartbeat --result`
        'novelty': {
            'processes_added': diff.get('processes', {}).get('added', []),
            'processes_removed': diff.get('processes', {}).get('removed', []),
            'listeners_added': diff.get('listeners', {}).get('added', []),
            'listeners_removed': diff.get('listeners', {}).get('removed', []),
        },
        'saved': saved_paths,
        'notify': notify_status,
    }

    if output_format == 'json':
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_services_audit_human(payload, novelty, prior_id, notify_status)
    return 0


def _print_services_audit_human(payload: dict, novelty: bool,
                                 prior_id: str | None,
                                 notify_status: dict) -> None:
    """Human-readable rendering for services-audit. Pulled out so the
    handler stays under the C901 threshold."""
    print(f'🔍 services-audit: scan {payload["scan_id"][:8]} → {payload["result"]}')
    if novelty:
        n = payload['novelty']
        if n['processes_added']:
            print(f'   +processes: {", ".join(n["processes_added"])}')
        if n['listeners_added']:
            print(f'   +listeners: {", ".join(n["listeners_added"])}')
        if notify_status.get('emitted'):
            print(f'   notified via {notify_status.get("backend")}')
        return
    if prior_id:
        print(f'   (no novelty since {prior_id[:8]})')
    else:
        print('   (first scan — no prior to diff against)')


def handle_scan_command(args) -> int:
    """`empirica scan` — emit a deterministic inventory snapshot.

    With ``--explain``, the snapshot is auto-saved and a system-reminder
    is emitted pointing the AI at the ``services-auditor`` skill.
    """
    output_format = getattr(args, 'output', 'markdown')
    save = getattr(args, 'save', False)
    explain = getattr(args, 'explain', False)
    # --explain implies --save: the auditor needs the file on disk
    if explain:
        save = True
    project_id = _resolve_project_id(args)

    try:
        snapshot = collect_snapshot()
    except Exception as exc:
        logger.error(f"scan failed: {exc}")
        if output_format == 'json':
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"❌ scan failed: {exc}")
        return 1

    snapshot_dict = snapshot.to_dict()
    saved_paths: dict[str, str] = {}
    if save:
        try:
            saved_paths = _persist_scan(snapshot_dict, project_id)
        except OSError as exc:
            logger.warning(f"scan persistence failed: {exc}")
            saved_paths = {'error': str(exc)}

    if explain:
        # The hand-off output is the same in both formats — it's a directive
        # for the AI session, not the snapshot itself. The snapshot lives
        # on disk at saved_paths.
        if output_format == 'json':
            envelope = {
                'ok': True,
                'mode': 'explain',
                'project_id': project_id,
                'scan_id': snapshot_dict.get('scan_id'),
                'saved': saved_paths,
                'next_step': {
                    'skill': 'services-auditor',
                    'snapshot_path': saved_paths.get('last_scan_path')
                        or saved_paths.get('scan_path'),
                },
                'message': 'Invoke /services-auditor to perform AI judgment.',
            }
            print(json.dumps(envelope, indent=2, default=str))
        else:
            print(_render_explain_hand_off(snapshot_dict, saved_paths, project_id))
        return 0

    if output_format == 'json':
        envelope = {
            'ok': True,
            'project_id': project_id,
            'snapshot': snapshot_dict,
            'saved': saved_paths if save else None,
        }
        print(json.dumps(envelope, indent=2, default=str))
    else:
        # Markdown
        markdown = render_markdown(snapshot)
        print(markdown)
        if save and saved_paths.get('scan_path'):
            print(f"_Saved to {saved_paths['scan_path']}_")

    return 0
