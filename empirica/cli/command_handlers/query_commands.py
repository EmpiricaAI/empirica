"""
Unified Query Commands - Consistent interface for querying epistemic breadcrumbs

Handles: findings, unknowns, deadends, mistakes, issues, handoffs, goals
With consistent --scope flag (session, project, global)
"""

import json
import sqlite3


def handle_query_command(args):
    """Handle unified query command"""
    try:

        query_type = args.type
        scope = getattr(args, 'scope', 'global')
        session_id = getattr(args, 'session_id', None)
        project_id = getattr(args, 'project_id', None)
        limit = getattr(args, 'limit', 20)
        status = getattr(args, 'status', None)
        ai_id = getattr(args, 'ai_id', None)
        since = getattr(args, 'since', None)
        output = getattr(args, 'output', 'human')

        # Validate scope requirements
        if scope == 'session' and not session_id:
            result = {'ok': False, 'error': 'Session scope requires --session-id'}
            print(json.dumps(result))
            return 1
        if scope == 'project' and not project_id:
            result = {'ok': False, 'error': 'Project scope requires --project-id'}
            print(json.dumps(result))
            return 1

        # Route to type-specific query
        handlers = {
            'findings': _query_findings,
            'unknowns': _query_unknowns,
            'deadends': _query_deadends,
            'mistakes': _query_mistakes,
            'issues': _query_issues,
            'handoffs': _query_handoffs,
            'goals': _query_goals,
            'blockers': _query_blockers,
        }

        handler = handlers.get(query_type)
        if not handler:
            result = {'ok': False, 'error': f'Unknown query type: {query_type}'}
            print(json.dumps(result))
            return 1

        results = handler(
            scope=scope,
            session_id=session_id,
            project_id=project_id,
            limit=limit,
            status=status,
            ai_id=ai_id,
            since=since
        )

        # Format output
        if output == 'json':
            print(json.dumps({
                'ok': True,
                'type': query_type,
                'scope': scope,
                'count': len(results),
                'results': results
            }, indent=2))
        else:
            _print_human(query_type, scope, results)

        return 0

    except Exception as e:
        result = {'ok': False, 'error': str(e)}
        print(json.dumps(result))
        return 1


def _query_findings(scope: str, session_id: str, project_id: str,
                    limit: int, status: str, ai_id: str, since: str) -> list[dict]:
    """Query findings from project_findings (canonical table)."""
    from empirica.data.session_database import SessionDatabase
    db = SessionDatabase()

    if scope == 'session':
        query = "SELECT id, session_id, finding as content, impact, created_timestamp as created_at FROM project_findings WHERE session_id = ? ORDER BY created_timestamp DESC LIMIT ?"
        params = [session_id, limit]
    elif scope == 'project':
        query = "SELECT id, project_id, finding as content, impact, created_timestamp as created_at FROM project_findings WHERE project_id = ? ORDER BY created_timestamp DESC LIMIT ?"
        params = [project_id, limit]
    else:  # global
        query = "SELECT id, session_id, project_id, finding as content, impact, created_timestamp as created_at FROM project_findings ORDER BY created_timestamp DESC LIMIT ?"
        params = [limit]

    cursor = db.conn.cursor()
    cursor.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    results = [dict(zip(columns, row)) for row in cursor.fetchall()]
    db.close()
    return results


def _query_unknowns(scope: str, session_id: str, project_id: str,
                    limit: int, status: str, ai_id: str, since: str) -> list[dict]:
    """Query unknowns from project_unknowns (canonical table)."""
    from empirica.data.session_database import SessionDatabase
    db = SessionDatabase()

    # Map status to is_resolved boolean
    status_filter = ""
    if status == 'resolved':
        status_filter = " AND is_resolved = 1"
    elif status == 'open':
        status_filter = " AND is_resolved = 0"

    if scope == 'session':
        query = f"SELECT id, session_id, unknown as content, CASE WHEN is_resolved THEN 'resolved' ELSE 'open' END as status, resolved_by, created_timestamp as created_at FROM project_unknowns WHERE session_id = ?{status_filter} ORDER BY created_timestamp DESC LIMIT ?"
        params = [session_id, limit]
    elif scope == 'project':
        query = f"SELECT id, project_id, unknown as content, CASE WHEN is_resolved THEN 'resolved' ELSE 'open' END as status, resolved_by, created_timestamp as created_at FROM project_unknowns WHERE project_id = ?{status_filter} ORDER BY created_timestamp DESC LIMIT ?"
        params = [project_id, limit]
    else:  # global
        query = f"SELECT id, session_id, project_id, unknown as content, CASE WHEN is_resolved THEN 'resolved' ELSE 'open' END as status, resolved_by, created_timestamp as created_at FROM project_unknowns WHERE 1=1{status_filter} ORDER BY created_timestamp DESC LIMIT ?"
        params = [limit]

    cursor = db.conn.cursor()
    cursor.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    results = [dict(zip(columns, row)) for row in cursor.fetchall()]
    db.close()
    return results


def _query_deadends(scope: str, session_id: str, project_id: str,
                    limit: int, status: str, ai_id: str, since: str) -> list[dict]:
    """Query dead ends from project_dead_ends (canonical table)."""
    from empirica.data.session_database import SessionDatabase
    db = SessionDatabase()

    if scope == 'session':
        query = "SELECT id, session_id, approach, why_failed, created_timestamp as created_at FROM project_dead_ends WHERE session_id = ? ORDER BY created_timestamp DESC LIMIT ?"
        params = [session_id, limit]
    elif scope == 'project':
        query = "SELECT id, project_id, approach, why_failed, created_timestamp as created_at FROM project_dead_ends WHERE project_id = ? ORDER BY created_timestamp DESC LIMIT ?"
        params = [project_id, limit]
    else:  # global
        query = "SELECT id, session_id, project_id, approach, why_failed, created_timestamp as created_at FROM project_dead_ends ORDER BY created_timestamp DESC LIMIT ?"
        params = [limit]

    cursor = db.conn.cursor()
    cursor.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    results = [dict(zip(columns, row)) for row in cursor.fetchall()]
    db.close()
    return results


def _query_mistakes(scope: str, session_id: str, project_id: str,
                    limit: int, status: str, ai_id: str, since: str) -> list[dict]:
    """Query mistakes from session_mistakes or mistakes_made"""
    from empirica.data.session_database import SessionDatabase
    db = SessionDatabase()

    cursor = db.conn.cursor()

    if scope == 'session':
        query = "SELECT id, session_id, mistake as description, why_wrong, cost_estimate, prevention, created_timestamp as created_at FROM session_mistakes WHERE session_id = ? ORDER BY created_timestamp DESC LIMIT ?"
        params = [session_id, limit]
    elif scope == 'project':
        query = "SELECT id, session_id, project_id, mistake as description, why_wrong, cost_estimate, prevention, created_timestamp as created_at FROM mistakes_made WHERE project_id = ? ORDER BY created_timestamp DESC LIMIT ?"
        params = [project_id, limit]
    else:
        # Global - use mistakes_made (includes project scope)
        query = """
            SELECT id, session_id, project_id, mistake as description, why_wrong, cost_estimate, prevention, created_timestamp as created_at
            FROM mistakes_made
            ORDER BY created_timestamp DESC LIMIT ?
        """
        params = [limit]

    try:
        cursor.execute(query, params)
        columns = [desc[0] for desc in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        results = []

    db.close()
    return results


def _query_issues(scope: str, session_id: str, project_id: str,
                  limit: int, status: str, ai_id: str, since: str) -> list[dict]:
    """Query auto-captured issues"""
    from empirica.data.session_database import SessionDatabase
    db = SessionDatabase()

    query = """
        SELECT i.*, s.project_id
        FROM auto_captured_issues i
        JOIN sessions s ON i.session_id = s.session_id
        WHERE 1=1
    """
    params = []

    if scope == 'session' and session_id:
        query += " AND i.session_id = ?"
        params.append(session_id)
    elif scope == 'project' and project_id:
        query += " AND s.project_id = ?"
        params.append(project_id)

    if status:
        query += " AND i.status = ?"
        params.append(status)

    query += " ORDER BY i.created_at DESC LIMIT ?"
    params.append(limit)

    cursor = db.conn.cursor()
    try:
        cursor.execute(query, params)
        columns = [desc[0] for desc in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        results = []

    db.close()
    return results


def _query_handoffs(scope: str, session_id: str, project_id: str,
                    limit: int, status: str, ai_id: str, since: str) -> list[dict]:
    """Query handoff reports (uses HybridHandoffStorage for git + db)"""
    from empirica.core.handoff.storage import HybridHandoffStorage

    storage = HybridHandoffStorage()

    if scope == 'session' and session_id:
        handoff = storage.load_handoff(session_id)
        return [handoff] if handoff else []
    else:
        return storage.query_handoffs(ai_id=ai_id, since=since, limit=limit)


def _query_goals(scope: str, session_id: str, project_id: str,
                 limit: int, status: str, ai_id: str, since: str) -> list[dict]:
    """Query goals with tasks"""
    from empirica.data.session_database import SessionDatabase
    db = SessionDatabase()

    query = """
        SELECT g.id, g.objective, g.status, g.created_timestamp, g.session_id, s.ai_id,
               (SELECT COUNT(*) FROM subtasks WHERE goal_id = g.id) as total_subtasks,
               (SELECT COUNT(*) FROM subtasks WHERE goal_id = g.id AND status = 'completed') as completed_subtasks
        FROM goals g
        LEFT JOIN sessions s ON g.session_id = s.session_id
        WHERE 1=1
    """
    params = []

    if scope == 'session' and session_id:
        query += " AND g.session_id = ?"
        params.append(session_id)
    elif scope == 'project' and project_id:
        query += " AND g.project_id = ?"
        params.append(project_id)

    if status == 'active':
        query += " AND g.status != 'completed'"
    elif status == 'completed':
        query += " AND g.status = 'completed'"

    if ai_id:
        query += " AND s.ai_id = ?"
        params.append(ai_id)

    query += " ORDER BY g.created_timestamp DESC LIMIT ?"
    params.append(limit)

    cursor = db.conn.cursor()
    cursor.execute(query, params)

    results = []
    for row in cursor.fetchall():
        total = row[6] or 0
        completed = row[7] or 0
        results.append({
            'id': row[0],
            'objective': row[1],
            'status': row[2],
            'created_at': row[3],
            'session_id': row[4],
            'ai_id': row[5],
            'progress': f"{completed}/{total}",
            'progress_pct': round(completed / total * 100, 1) if total > 0 else 0
        })

    db.close()
    return results


def _query_blockers(scope: str, session_id: str, project_id: str,
                    limit: int, status: str, ai_id: str, since: str) -> list[dict]:
    """Query goal-linked unknowns (blockers) sorted by impact"""
    from empirica.data.session_database import SessionDatabase
    db = SessionDatabase()

    query = """
        SELECT
            su.id,
            su.unknown as content,
            su.impact,
            su.goal_id,
            g.objective as goal_objective,
            g.status as goal_status,
            su.session_id,
            su.created_timestamp as created_at
        FROM project_unknowns su
        JOIN goals g ON su.goal_id = g.id
        WHERE su.is_resolved = FALSE
          AND su.goal_id IS NOT NULL
    """
    params = []

    # Optional: filter to only blockers for active goals
    if status == 'active':
        query += " AND g.status != 'completed'"
    elif status == 'completed':
        query += " AND g.status = 'completed'"

    if scope == 'session' and session_id:
        query += " AND su.session_id = ?"
        params.append(session_id)

    query += " ORDER BY su.impact DESC, su.created_timestamp DESC LIMIT ?"
    params.append(limit)

    cursor = db.conn.cursor()
    cursor.execute(query, params)

    results = []
    for row in cursor.fetchall():
        results.append({
            'id': row[0],
            'content': row[1],
            'impact': row[2] or 0.5,
            'goal_id': row[3],
            'goal_objective': row[4],
            'goal_status': row[5],
            'session_id': row[6],
            'created_at': row[7]
        })

    db.close()
    return results


def _print_human(query_type: str, scope: str, results: list[dict]):
    """Print human-readable output"""
    type_emoji = {
        'findings': '💡',
        'unknowns': '❓',
        'deadends': '🚫',
        'mistakes': '⚠️',
        'issues': '🐛',
        'handoffs': '📋',
        'goals': '🎯',
        'blockers': '🚧'
    }

    emoji = type_emoji.get(query_type, '📄')
    print(f"\n{'='*70}")
    print(f"{emoji} {query_type.upper()} ({scope} scope) - {len(results)} found")
    print(f"{'='*70}\n")

    if not results:
        print("  No results found.")
        return

    for i, r in enumerate(results, 1):
        if query_type == 'findings':
            impact = r.get('impact', 0)
            content = r.get('content', '')[:80]
            print(f"{i}. [{impact:.1f}] {content}")
        elif query_type == 'unknowns':
            status = r.get('status', 'open')
            content = r.get('content', '')[:80]
            icon = '✓' if status == 'resolved' else '○'
            print(f"{i}. {icon} {content}")
        elif query_type == 'deadends':
            approach = r.get('approach', '')[:60]
            why = r.get('why_failed', '')[:40]
            print(f"{i}. {approach}")
            print(f"   → {why}")
        elif query_type == 'mistakes':
            desc = r.get('description', r.get('mistake', ''))[:80]
            print(f"{i}. {desc}")
        elif query_type == 'issues':
            msg = r.get('message', '')[:60]
            severity = r.get('severity', 'unknown')
            status = r.get('status', 'new')
            print(f"{i}. [{severity}] {msg} ({status})")
        elif query_type == 'handoffs':
            task = r.get('task_summary', '')[:60]
            ai = r.get('ai_id', 'unknown')
            print(f"{i}. {task}")
            print(f"   AI: {ai}")
        elif query_type == 'goals':
            obj = r.get('objective', '')[:60]
            progress = r.get('progress', '0/0')
            pct = r.get('progress_pct', 0)
            print(f"{i}. {obj}")
            print(f"   Progress: {progress} ({pct}%)")
        elif query_type == 'blockers':
            content = r.get('content', '')[:70]
            impact = r.get('impact', 0)
            goal = r.get('goal_objective', '')[:40]
            print(f"{i}. [{impact:.1f}] {content}")
            print(f"   🎯 Blocks: {goal}")
        print()
