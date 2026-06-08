"""Handlers for `empirica visibility <action>` (Phase 0).

Per docs/architecture/PROPOSAL_VISIBILITY_TIERS.md.

Phase 0 commands:
  - list : counts by tier (+ optional recent items per tier and per type)
  - show : single artifact's tier (looks up by UUID prefix across all tables)
"""

from __future__ import annotations

import json
import sys

from empirica.data.session_database import SessionDatabase
from empirica.data.visibility import VISIBILITY_TIERS
from empirica.utils.session_resolver import InstanceResolver as R

# Each artifact table → (table, content_column_for_preview, timestamp_column).
# The timestamp column name diverges across artifact tables — the 7 epistemic
# tables use `created_timestamp` (REAL), epistemic_sources uses `discovered_at`
# (TIMESTAMP) — so visibility queries must read the column name per table or
# they fail with `no such column: created_timestamp` on the sources branch.
_ARTIFACT_TABLES: dict[str, tuple[str, str, str]] = {
    'finding': ('project_findings', 'finding', 'created_timestamp'),
    'unknown': ('project_unknowns', 'unknown', 'created_timestamp'),
    'dead_end': ('project_dead_ends', 'approach', 'created_timestamp'),
    'mistake': ('mistakes_made', 'mistake', 'created_timestamp'),
    'assumption': ('assumptions', 'assumption', 'created_timestamp'),
    'decision': ('decisions', 'choice', 'created_timestamp'),
    'goal': ('goals', 'objective', 'created_timestamp'),
    'source': ('epistemic_sources', 'title', 'discovered_at'),
}


def _resolve_project_id(args, db) -> str | None:
    """Resolve to a UUID-shaped project_id.

    InstanceResolver.project_id_from_db can return a project name (not a UUID)
    depending on the workspace.db state, so we always pass the value through
    db.resolve_project_id when it doesn't look like a UUID.
    """
    import re

    def _looks_like_uuid(s: str) -> bool:
        return bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', s, re.I))

    project_id = getattr(args, 'project_id', None)
    if not project_id:
        try:
            path = R.project_path()
            if path:
                project_id = R.project_id_from_db(path)
        except Exception:
            pass

    if project_id and not _looks_like_uuid(project_id):
        try:
            resolved = db.resolve_project_id(project_id)
            if resolved:
                project_id = resolved
        except Exception:
            pass
    return project_id


def _table_has_visibility_column(cursor, table: str) -> bool:
    cursor.execute("SELECT 1 FROM pragma_table_info(?) WHERE name = 'visibility'", (table,))
    return cursor.fetchone() is not None


def _count_by_tier(cursor, table: str, project_id: str | None) -> dict[str, int]:
    """Return {tier: count} for a table, scoped to project_id when present."""
    counts = dict.fromkeys(VISIBILITY_TIERS, 0)
    if not _table_has_visibility_column(cursor, table):
        return counts

    where = ""
    params: tuple = ()
    if project_id:
        cursor.execute("SELECT 1 FROM pragma_table_info(?) WHERE name = 'project_id'", (table,))
        if cursor.fetchone():
            where = " WHERE project_id = ?"
            params = (project_id,)

    cursor.execute(
        f"SELECT visibility, COUNT(*) FROM {table}{where} GROUP BY visibility",
        params,
    )
    for tier, count in cursor.fetchall():
        # Treat NULL/unknown as 'shared' (the column default) so counts always sum cleanly
        canonical = tier if tier in counts else 'shared'
        counts[canonical] += count
    return counts


def _recent_for_tier(cursor, table: str, content_col: str, ts_col: str,
                     tier: str, project_id: str | None, limit: int) -> list[dict]:
    if not _table_has_visibility_column(cursor, table):
        return []

    cursor.execute("SELECT 1 FROM pragma_table_info(?) WHERE name = 'project_id'", (table,))
    has_project = cursor.fetchone() is not None

    where = "WHERE visibility = ?"
    params: list = [tier]
    if project_id and has_project:
        where += " AND project_id = ?"
        params.append(project_id)

    cursor.execute(
        f"SELECT id, {content_col} as content, {ts_col} as ts "
        f"FROM {table} {where} ORDER BY {ts_col} DESC LIMIT ?",
        (*params, limit),
    )
    return [dict(row) for row in cursor.fetchall()]


def handle_visibility_list_command(args) -> int:  # noqa: C901 — list+filter+output dispatcher
    """`empirica visibility list` — counts and recent items by tier."""
    db = SessionDatabase()
    if not db.conn:
        print(json.dumps({"ok": False, "error": "No database connection"}))
        return 1

    cursor = db.conn.cursor()
    project_id = _resolve_project_id(args, db)
    tier_filter = getattr(args, 'tier', None)
    type_filter = getattr(args, 'artifact_type', None)
    limit = getattr(args, 'limit', 10)
    output_format = getattr(args, 'output', 'human')

    by_type: dict[str, dict[str, int]] = {}
    totals = dict.fromkeys(VISIBILITY_TIERS, 0)

    for atype, (table, _content_col, _ts_col) in _ARTIFACT_TABLES.items():
        if type_filter and atype != type_filter:
            continue
        counts = _count_by_tier(cursor, table, project_id)
        by_type[atype] = counts
        for tier, count in counts.items():
            totals[tier] += count

    # Sample recent items per tier (when not in JSON-only mode)
    samples: dict[str, list[dict]] = {tier: [] for tier in VISIBILITY_TIERS}
    if limit and (tier_filter or output_format == 'human'):
        target_tiers = [tier_filter] if tier_filter else list(VISIBILITY_TIERS)
        for tier in target_tiers:
            for atype, (table, content_col, ts_col) in _ARTIFACT_TABLES.items():
                if type_filter and atype != type_filter:
                    continue
                rows = _recent_for_tier(cursor, table, content_col, ts_col,
                                        tier, project_id, limit)
                for row in rows:
                    samples[tier].append({
                        'type': atype,
                        'id': row['id'],
                        'content': (row['content'] or '')[:120],
                        'created_timestamp': row['ts'],
                    })
            # Trim to global per-tier limit (most recent overall).
            # Timestamps may be either float epoch or ISO strings depending on
            # which writer landed the row, so coerce defensively.
            def _ts_key(row: dict) -> float:
                ts = row.get('created_timestamp')
                if isinstance(ts, (int, float)):
                    return float(ts)
                if isinstance(ts, str):
                    try:
                        return float(ts)
                    except (TypeError, ValueError):
                        return 0.0
                return 0.0

            samples[tier].sort(key=_ts_key, reverse=True)
            samples[tier] = samples[tier][:limit]

    db.close()

    payload = {
        "ok": True,
        "project_id": project_id,
        "totals": totals,
        "by_type": by_type,
        "samples": samples,
        "filters": {"tier": tier_filter, "type": type_filter, "limit": limit},
    }

    if output_format == 'json':
        print(json.dumps(payload, indent=2))
        return 0

    # Human-readable
    pid_label = (project_id[:8] + '...') if project_id else '(no project)'
    print(f"🔒 visibility — project {pid_label}")
    summary = "  ".join(f"{tier}: {totals[tier]}" for tier in VISIBILITY_TIERS)
    print(f"   totals: {summary}")
    print()
    print("   by type:")
    for atype, counts in by_type.items():
        line = "  ".join(f"{tier}={counts[tier]}" for tier in VISIBILITY_TIERS)
        print(f"     {atype:11s}  {line}")

    if any(samples[tier] for tier in samples):
        print()
        for tier in VISIBILITY_TIERS:
            items = samples[tier]
            if not items:
                continue
            print(f"   recent {tier} (top {len(items)}):")
            for item in items:
                print(f"     [{item['type']}] {item['id'][:8]}  {item['content']}")
    return 0


def handle_visibility_show_command(args) -> int:
    """`empirica visibility show <artifact-id>` — single artifact tier lookup."""
    artifact_id = getattr(args, 'artifact_id', None)
    output_format = getattr(args, 'output', 'human')

    if not artifact_id or len(artifact_id) < 8:
        print(json.dumps({"ok": False,
                          "error": "artifact_id required (UUID or prefix ≥8 chars)"}))
        return 2

    db = SessionDatabase()
    if not db.conn:
        print(json.dumps({"ok": False, "error": "No database connection"}))
        return 1

    cursor = db.conn.cursor()
    match: dict | None = None
    for atype, (table, content_col, ts_col) in _ARTIFACT_TABLES.items():
        if not _table_has_visibility_column(cursor, table):
            continue
        cursor.execute(
            f"SELECT id, {content_col} as content, visibility, {ts_col} as ts "
            f"FROM {table} WHERE id LIKE ? LIMIT 1",
            (f"{artifact_id}%",),
        )
        row = cursor.fetchone()
        if row:
            match = {
                'type': atype,
                'id': row['id'],
                'content': row['content'],
                'visibility': row['visibility'] or 'shared',
                'created_timestamp': row['ts'],
            }
            break
    db.close()

    if not match:
        payload = {"ok": False, "error": f"No artifact found for prefix '{artifact_id}'"}
        print(json.dumps(payload, indent=2))
        return 1

    if output_format == 'json':
        print(json.dumps({"ok": True, **match}, indent=2))
        return 0

    print(f"🔒 {match['type']} {match['id'][:8]}")
    print(f"   visibility: {match['visibility']}")
    if match['content']:
        print(f"   content:    {match['content'][:200]}")
    return 0


_VISIBILITY_DISPATCH = {
    'list': handle_visibility_list_command,
    'show': handle_visibility_show_command,
}


def handle_visibility_group_command(args) -> int:
    """Dispatcher for `empirica visibility <action>`."""
    action = getattr(args, 'visibility_action', None)
    if not action:
        sys.stderr.write('usage: empirica visibility <list|show> [args...]\n')
        return 2
    handler = _VISIBILITY_DISPATCH.get(action)
    if handler is None:
        sys.stderr.write(f'error: unknown visibility action: {action}\n')
        return 2
    return handler(args) or 0


__all__ = [
    'handle_visibility_group_command',
    'handle_visibility_list_command',
    'handle_visibility_show_command',
]
