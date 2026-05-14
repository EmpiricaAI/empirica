"""
Workspace Commands - Workspace overview, map, and project listing

Split from project_commands.py for maintainability.
"""

import json
import logging

from ..cli_utils import handle_cli_error

logger = logging.getLogger(__name__)


def _overview_sort_and_filter(projects, sort_by, filter_status):
    """Sort and filter projects list based on user options. Returns filtered list."""
    if sort_by == 'knowledge':
        projects.sort(key=lambda p: p.get('health_score', 0), reverse=True)
    elif sort_by == 'uncertainty':
        projects.sort(key=lambda p: p.get('epistemic_state', {}).get('uncertainty', 0.5))
    elif sort_by == 'name':
        projects.sort(key=lambda p: p.get('name', ''))

    if filter_status:
        projects = [p for p in projects if p.get('status') == filter_status]
    return projects


def _overview_print_dashboard(stats, projects):
    """Print human-readable dashboard for workspace overview."""
    print("╔════════════════════════════════════════════════════════════════╗")
    print("║  Empirica Workspace Overview - Epistemic Project Management    ║")
    print("╚════════════════════════════════════════════════════════════════╝\n")

    print("📊 Workspace Summary")
    print(f"   Total Projects:    {stats['total_projects']}")
    print(f"   Total Sessions:    {stats['total_sessions']}")
    print(f"   Active Sessions:   {stats['active_sessions']}")
    print(f"   Average Know:      {stats['avg_know']:.2f}")
    print(f"   Average Uncertainty: {stats['avg_uncertainty']:.2f}")
    print()

    if not projects:
        print("   No projects found.")
        print(json.dumps({"projects": []}, indent=2))
        return

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    print("📁 Projects by Epistemic Health\n")

    tiers = [
        (0.7, float('inf'), "🟢 HIGH KNOWLEDGE (know ≥ 0.7)"),
        (0.5, 0.7, "🟡 MEDIUM KNOWLEDGE (0.5 ≤ know < 0.7)"),
        (0.0, 0.5, "🔴 LOW KNOWLEDGE (know < 0.5)"),
    ]
    for low, high, label in tiers:
        tier_projects = [p for p in projects if low <= p['health_score'] < high]
        if tier_projects:
            print(label)
            for i, p in enumerate(tier_projects, 1):
                _display_project(i, p)
            print()

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    print("💡 Quick Commands:")
    print("   • Bootstrap project:  empirica project-bootstrap --project-id <PROJECT_ID>")
    print("   • Check ready goals:  empirica goals-ready --session-id <SESSION_ID>")
    print("   • List all projects:  empirica project-list")
    print()


def handle_workspace_overview_command(args):
    """Handle workspace-overview command - show epistemic health of all projects"""
    try:
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        overview = db.get_workspace_overview()
        db.close()

        output_format = getattr(args, 'output', 'dashboard')
        sort_by = getattr(args, 'sort_by', 'activity')
        filter_status = getattr(args, 'filter', None)

        projects = _overview_sort_and_filter(overview['projects'], sort_by, filter_status)

        if output_format == 'json':
            result = {
                "ok": True,
                "workspace_stats": overview['workspace_stats'],
                "total_projects": len(projects),
                "projects": projects
            }
            print(json.dumps(result, indent=2))
            return None

        _overview_print_dashboard(overview['workspace_stats'], projects)
        return None

    except Exception as e:
        handle_cli_error(e, "Workspace overview", getattr(args, 'verbose', False))
        return None


def _display_project(index, project):
    """Helper to display a single project in dashboard format"""
    name = project['name']
    health = project['health_score']
    know = project['epistemic_state']['know']
    uncertainty = project['epistemic_state']['uncertainty']
    findings = project['findings_count']
    unknowns = project['unknowns_count']
    dead_ends = project['dead_ends_count']
    sessions = project['total_sessions']

    # Format last activity
    last_activity = project.get('last_activity')
    if last_activity:
        try:
            from datetime import datetime
            last_dt = datetime.fromtimestamp(last_activity)
            now = datetime.now()
            delta = now - last_dt
            if delta.days == 0:
                time_ago = "today"
            elif delta.days == 1:
                time_ago = "1 day ago"
            elif delta.days < 7:
                time_ago = f"{delta.days} days ago"
            elif delta.days < 30:
                weeks = delta.days // 7
                time_ago = f"{weeks} week{'s' if weeks > 1 else ''} ago"
            else:
                months = delta.days // 30
                time_ago = f"{months} month{'s' if months > 1 else ''} ago"
        except Exception:
            time_ago = "unknown"
    else:
        time_ago = "never"

    print(f"   {index}. {name} │ Health: {health:.2f} │ Know: {know:.2f} │ Sessions: {sessions} │ ⏰ {time_ago}")
    print(f"      Findings: {findings}  Unknowns: {unknowns}  Dead Ends: {dead_ends}")

    # Show warnings
    if uncertainty > 0.7:
        print(f"      ⚠️  High uncertainty ({uncertainty:.2f}) - needs investigation")
    if dead_ends > 0 and sessions > 0:
        dead_end_ratio = dead_ends / sessions
        if dead_end_ratio > 0.3:
            print(f"      🚨 High dead end ratio ({dead_end_ratio:.0%}) - many failed approaches")
    if unknowns > 20:
        print(f"      ❓ Many unresolved unknowns ({unknowns}) - systematically resolve them")

    # Show project ID (shortened)
    project_id = project['project_id']
    print(f"      ID: {project_id[:8]}...")


def _wsmap_scan_git_repos(parent_dir):
    """Scan parent directory for git repos and get remote URLs.

    Returns list of repo_info dicts.
    """
    import subprocess

    git_repos = []
    logger.info(f"Scanning {parent_dir} for git repositories...")

    for item in parent_dir.iterdir():
        if not item.is_dir() or not (item / '.git').exists():
            continue

        try:
            result = subprocess.run(
                ['git', '-C', str(item), 'remote', 'get-url', 'origin'],
                capture_output=True, text=True, timeout=5
            )
            remote_url = result.stdout.strip() if result.returncode == 0 else None
            git_repos.append({
                'path': str(item), 'name': item.name,
                'remote_url': remote_url, 'has_remote': remote_url is not None
            })
        except Exception as e:
            logger.debug(f"Error getting remote for {item.name}: {e}")
            git_repos.append({
                'path': str(item), 'name': item.name,
                'remote_url': None, 'has_remote': False, 'error': str(e)
            })

    return git_repos


def _wsmap_match_empirica_projects(git_repos):
    """Match git repos with Empirica projects from the database."""
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    cursor = db.conn.cursor()

    for repo in git_repos:
        if not repo['has_remote']:
            repo['empirica_project'] = None
            continue

        # total_sessions: live COUNT(*) because the projects.total_sessions
        # denormalized column is stale (never wired to insert trigger).
        cursor.execute("""
            SELECT id, name, status,
                   (SELECT COUNT(*) FROM sessions s
                    WHERE s.project_id = projects.id) as total_sessions,
                   (SELECT r.know FROM reflexes r
                    JOIN sessions s ON s.session_id = r.session_id
                    WHERE s.project_id = projects.id
                    ORDER BY r.timestamp DESC LIMIT 1) as latest_know,
                   (SELECT r.uncertainty FROM reflexes r
                    JOIN sessions s ON s.session_id = r.session_id
                    WHERE s.project_id = projects.id
                    ORDER BY r.timestamp DESC LIMIT 1) as latest_uncertainty
            FROM projects
            WHERE repos LIKE ?
        """, (f'%{repo["remote_url"]}%',))

        row = cursor.fetchone()
        if row:
            repo['empirica_project'] = {
                'project_id': row[0], 'name': row[1], 'status': row[2],
                'total_sessions': row[3],
                'know': row[4] if row[4] else 0.5,
                'uncertainty': row[5] if row[5] else 0.5
            }
        else:
            repo['empirica_project'] = None

    db.close()


def _wsmap_enrich_ecosystem(git_repos, eco_graph):
    """Enrich repos with ecosystem dependency info."""
    if not eco_graph:
        return
    for repo in git_repos:
        eco_name = repo['name']
        if eco_name in eco_graph.projects:
            downstream = sorted(eco_graph.downstream(eco_name))
            upstream = sorted(eco_graph.upstream(eco_name))
            eco_config = eco_graph.projects[eco_name]
            repo['ecosystem'] = {
                'role': eco_config.get('role'), 'type': eco_config.get('type'),
                'downstream': downstream, 'downstream_count': len(downstream),
                'upstream': upstream, 'upstream_count': len(upstream),
            }
        else:
            repo['ecosystem'] = None


def _wsmap_print_dashboard(parent_dir, git_repos, eco_graph):
    """Print human-readable dashboard for workspace map."""
    tracked = [r for r in git_repos if r['empirica_project']]
    untracked = [r for r in git_repos if not r['empirica_project']]

    print("╔════════════════════════════════════════════════════════════════╗")
    print("║  Git Workspace Map - Epistemic Health                         ║")
    print("╚════════════════════════════════════════════════════════════════╝\n")

    print(f"📂 Parent Directory: {parent_dir}")
    print(f"   Total Git Repos:  {len(git_repos)}")
    print(f"   Tracked:          {len(tracked)}")
    print(f"   Untracked:        {len(untracked)}")
    print()

    if tracked:
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        print("🟢 Tracked in Empirica\n")
        for repo in tracked:
            proj = repo['empirica_project']
            status_icon = "🟢" if proj['status'] == 'active' else "🟡"
            print(f"{status_icon} {repo['name']}")
            print(f"   Path: {repo['path']}")
            print(f"   Project: {proj['name']}")
            print(f"   Know: {proj['know']:.2f} | Uncertainty: {proj['uncertainty']:.2f} | Sessions: {proj['total_sessions']}")
            print(f"   ID: {proj['project_id'][:8]}...")
            eco = repo.get('ecosystem')
            if eco:
                print(f"   Role: {eco['role']} | Deps: {eco['upstream_count']} upstream, {eco['downstream_count']} downstream")
            print()

    if untracked:
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        print("⚪ Not Tracked in Empirica\n")
        for repo in untracked:
            print(f"⚪ {repo['name']}")
            print(f"   Path: {repo['path']}")
            if repo['has_remote']:
                print(f"   Remote: {repo['remote_url']}")
                print(f"   → To track: empirica project-create --name '{repo['name']}' --repos '[\"{repo['remote_url']}\"]'")
            else:
                print("   ⚠️  No remote configured")
            print()

    if eco_graph:
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        summary = eco_graph.summary()
        print(f"📋 Ecosystem Manifest: {summary['total_projects']} projects, {summary['dependency_edges']} dependency edges")
        print()

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    print("Quick Commands:")
    print("   empirica workspace-overview           # Epistemic health of all projects")
    print("   empirica ecosystem-check              # Full ecosystem dependency map")
    print("   empirica ecosystem-check --project X  # Upstream/downstream for project X")
    print("   empirica ecosystem-check --file F     # Impact analysis for file F")
    print()


def handle_workspace_map_command(args):
    """Handle workspace-map command - discover git repos and show epistemic status"""
    try:
        from pathlib import Path

        parent_dir = Path.cwd().parent
        output_format = getattr(args, 'output', 'dashboard')

        # Stage 1: Scan for git repos
        git_repos = _wsmap_scan_git_repos(parent_dir)

        # Stage 2: Load ecosystem manifest (optional)
        eco_graph = None
        try:
            from empirica.core.ecosystem import load_ecosystem
            eco_graph = load_ecosystem()
        except Exception:
            pass

        # Stage 3: Match with Empirica projects
        _wsmap_match_empirica_projects(git_repos)

        # Stage 4: Enrich with ecosystem info
        _wsmap_enrich_ecosystem(git_repos, eco_graph)

        # Stage 5: Output
        if output_format == 'json':
            result = {
                "ok": True,
                "parent_directory": str(parent_dir),
                "total_repos": len(git_repos),
                "tracked_repos": sum(1 for r in git_repos if r['empirica_project']),
                "untracked_repos": sum(1 for r in git_repos if not r['empirica_project']),
                "has_ecosystem_manifest": eco_graph is not None,
                "repos": git_repos
            }
            print(json.dumps(result, indent=2))
            return None

        _wsmap_print_dashboard(parent_dir, git_repos, eco_graph)
        return 0

    except Exception as e:
        handle_cli_error(e, "Workspace map", getattr(args, 'verbose', False))
        return 1


def _wslist_query_projects(args):
    """Query projects from DB with filters. Returns (projects, filter_type, filter_tags, filter_parent)."""
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    cursor = db.conn.cursor()

    # total_sessions: live COUNT(*) because the projects.total_sessions
    # denormalized column is stale (never wired to insert trigger).
    query = """
        SELECT id, name, description, status, project_type, project_tags,
               parent_project_id,
               (SELECT COUNT(*) FROM sessions s WHERE s.project_id = projects.id)
                    AS total_sessions,
               last_activity_timestamp
        FROM projects
    """
    params = []
    conditions = []

    filter_type = getattr(args, 'type', None)
    if filter_type:
        conditions.append("project_type = ?")
        params.append(filter_type)

    filter_parent = getattr(args, 'parent', None)
    if filter_parent:
        conditions.append("parent_project_id = ?")
        params.append(filter_parent)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY project_type, name"

    cursor.execute(query, params)
    projects = [dict(row) for row in cursor.fetchall()]

    filter_tags = getattr(args, 'tags', None)
    if filter_tags:
        tag_list = [t.strip().lower() for t in filter_tags.split(',')]
        projects = [
            p for p in projects
            if any(tag in [t.lower() for t in json.loads(p.get('project_tags') or '[]')]
                   for tag in tag_list)
        ]

    db.close()

    for p in projects:
        p['project_tags'] = json.loads(p.get('project_tags') or '[]')

    return projects, filter_type, filter_tags, filter_parent


def _wslist_print_by_type(projects):
    """Print projects grouped by type."""
    from empirica.data.repositories.projects import ProjectRepository

    types_order = ProjectRepository.PROJECT_TYPES
    for ptype in types_order:
        type_projects = [p for p in projects if p.get('project_type') == ptype]
        if type_projects:
            icon = _get_type_icon(ptype)
            print(f"{icon} {ptype.upper()}")
            print("─" * 60)
            for p in type_projects:
                tags_str = ', '.join(p['project_tags']) if p['project_tags'] else ''
                parent_str = f" (child of {p['parent_project_id'][:8]}...)" if p['parent_project_id'] else ''
                print(f"   {p['name']}{parent_str}")
                print(f"      ID: {p['id'][:8]}...  Sessions: {p['total_sessions']}")
                if tags_str:
                    print(f"      Tags: {tags_str}")
                if p['description']:
                    print(f"      {p['description'][:60]}...")
                print()
            print()


def handle_workspace_list_command(args):
    """Handle workspace-list command - list projects with types, tags, and hierarchy"""
    try:
        projects, filter_type, filter_tags, filter_parent = _wslist_query_projects(args)

        output_format = getattr(args, 'output', 'human')
        show_tree = getattr(args, 'tree', False)

        if output_format == 'json':
            result = {
                "ok": True,
                "filters": {"type": filter_type, "tags": filter_tags, "parent": filter_parent},
                "total_projects": len(projects),
                "projects": projects
            }
            print(json.dumps(result, indent=2))
            return None

        print("╔════════════════════════════════════════════════════════════════╗")
        print("║  Empirica Workspace - Projects by Type                         ║")
        print("╚════════════════════════════════════════════════════════════════╝\n")

        if not projects:
            print("   No projects found matching filters.")
            return None

        if show_tree:
            _display_project_tree(projects)
        else:
            _wslist_print_by_type(projects)

        type_counts = {}
        for p in projects:
            ptype = p.get('project_type', 'product')
            type_counts[ptype] = type_counts.get(ptype, 0) + 1

        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("📊 Summary")
        for ptype, count in sorted(type_counts.items()):
            print(f"   {_get_type_icon(ptype)} {ptype}: {count}")
        print()

        return None

    except Exception as e:
        handle_cli_error(e, "Workspace list", getattr(args, 'verbose', False))
        return None


def _get_type_icon(project_type):
    """Get emoji icon for project type"""
    icons = {
        'product': '📦',
        'application': '🖥️',
        'feature': '⚡',
        'research': '🔬',
        'documentation': '📚',
        'infrastructure': '🏗️',
        'operations': '⚙️'
    }
    return icons.get(project_type, '📁')


def _display_project_tree(projects):
    """Display projects as a tree based on parent relationships"""
    # Build parent -> children map
    children_map = {}
    roots = []

    for p in projects:
        parent_id = p.get('parent_project_id')
        if parent_id:
            if parent_id not in children_map:
                children_map[parent_id] = []
            children_map[parent_id].append(p)
        else:
            roots.append(p)

    def print_tree(project, indent=0):
        prefix = "   " * indent
        icon = _get_type_icon(project.get('project_type', 'product'))
        tags_str = f" [{', '.join(project['project_tags'])}]" if project['project_tags'] else ''
        print(f"{prefix}{icon} {project['name']}{tags_str}")
        print(f"{prefix}   ID: {project['id'][:8]}... | Type: {project.get('project_type', 'product')}")

        # Print children
        children = children_map.get(project['id'], [])
        for child in children:
            print_tree(child, indent + 1)

    for root in roots:
        print_tree(root)
        print()
