"""
Bootstrap Output Formatter — extracted from project_bootstrap.py

Reduces handler complexity by separating output formatting (~600 lines)
from context loading logic (~120 lines).
"""

import json
import logging
import os
import subprocess

from ..cli_utils import safe_print

logger = logging.getLogger(__name__)


def _format_json_output(
    args, project_id, breadcrumbs,
    mco_config=None, workflow_suggestions=None,
    global_learnings=None, project_skills=None,
):
    """Format and print JSON bootstrap output."""
    result = {
        "ok": True,
        "project_id": project_id,
        "project_name": breadcrumbs.get('project', {}).get('name'),
        "breadcrumbs": breadcrumbs
    }
    if workflow_suggestions:
        result['workflow_automation'] = workflow_suggestions
    if mco_config:
        result['mco_config'] = mco_config
    if global_learnings:
        result['global_learnings'] = global_learnings
    if project_skills:
        result['project_skills'] = project_skills
    safe_print(json.dumps(result, indent=2))


def _format_mco_section(mco_config):
    """Print MCO configuration section."""
    safe_print("\n" + "=" * 70)
    safe_print("\U0001f527 MCO Configuration Restored (SessionStart Hook)")
    safe_print("=" * 70)
    if mco_config['source'] == 'pre_summary_snapshot':
        safe_print(f"   Source: {mco_config['snapshot_path']}")
    else:
        safe_print("   Source: Fresh load from MCO files (snapshot had no MCO)")
    safe_print("=" * 70)
    safe_print(mco_config['formatted'])
    safe_print("\n" + "=" * 70)
    safe_print("\U0001f4a1 Your configuration has been restored from pre-compact snapshot.")
    safe_print("   Apply these bias corrections during CASCADE assessments.")
    safe_print("=" * 70 + "\n")


def _format_project_banner(project, project_id, db):
    """Print the project context banner section."""
    safe_print("\u2501" * 64)
    safe_print("\U0001f3af PROJECT CONTEXT")
    safe_print("\u2501" * 64)
    safe_print()
    safe_print(f"\U0001f4c1 Project: {project['name']}")
    safe_print(f"\U0001f194 ID: {project_id}")

    # Get git URL
    try:
        result = subprocess.run(
            ['git', 'remote', 'get-url', 'origin'],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            git_url = result.stdout.strip()
            safe_print(f"\U0001f517 Repository: {git_url}")
    except Exception:
        pass

    safe_print(f"\U0001f4cd Location: {db.db_path.parent.parent if hasattr(db, 'db_path') and db.db_path else 'Unknown'}")
    safe_print("\U0001f4be Database: .empirica/sessions/sessions.db")
    safe_print()
    safe_print("\u26a0\ufe0f  All commands write to THIS project's database.")
    safe_print("   Findings, sessions, goals \u2192 stored in this project context.")
    safe_print()
    safe_print("\u2501" * 64)
    safe_print()


def _format_project_summary(project, last):
    """Print the project summary section."""
    safe_print("\U0001f4cb Project Summary")
    safe_print(f"   {project['description']}")
    if project['repos']:
        safe_print(f"   Repos: {', '.join(project['repos'])}")
    safe_print(f"   Total sessions: {project['total_sessions']}")
    safe_print()

    safe_print("\U0001f550 Last Activity:")
    safe_print(f"   {last['summary']}")
    safe_print(f"   Next focus: {last['next_focus']}")
    safe_print()


def _format_vector_with_delta(label, vectors, deltas, key, invert=False):
    """Print a single vector value with optional delta arrow."""
    val = vectors.get(key, 'N/A')
    safe_print(f"      {label}: {val:.2f}", end='')
    if key in deltas and deltas[key] is not None:
        delta = deltas[key]
        if invert:
            arrow = "\u2193" if delta < 0 else "\u2191" if delta > 0 else "\u2192"
        else:
            arrow = "\u2191" if delta > 0 else "\u2193" if delta < 0 else "\u2192"
        safe_print(f" {arrow} {delta:+.2f}", end='')
    safe_print()


def _format_handoff_section(handoff):
    """Print the AI epistemic handoff section."""
    safe_print(f"\U0001f9e0 Epistemic Handoff (from {handoff.get('ai_id', 'unknown')}):")
    vectors = handoff.get('vectors', {})
    deltas = handoff.get('deltas', {})

    if vectors:
        safe_print("   State (POSTFLIGHT):")
        _format_vector_with_delta("Engagement", vectors, deltas, 'engagement')

        if 'foundation' in vectors:
            f = vectors['foundation']
            d = deltas.get('foundation', {})
            safe_print(f"      Foundation: know={f.get('know', 'N/A'):.2f}", end='')
            if d.get('know') is not None:
                safe_print(f" {d['know']:+.2f}", end='')
            safe_print(f", do={f.get('do', 'N/A'):.2f}", end='')
            if d.get('do') is not None:
                safe_print(f" {d['do']:+.2f}", end='')
            safe_print(f", context={f.get('context', 'N/A'):.2f}", end='')
            if d.get('context') is not None:
                safe_print(f" {d['context']:+.2f}", end='')
            safe_print()

        _format_vector_with_delta("Uncertainty", vectors, deltas, 'uncertainty', invert=True)

    if handoff.get('reasoning'):
        safe_print(f"   Learning: {handoff['reasoning'][:80]}...")
    safe_print()


def _format_flow_current_section(flow):
    """Print the current flow state section."""
    current = flow.get('current_flow')
    if not current:
        return

    safe_print("\u26a1 Flow State (AI Productivity):")
    safe_print(f"   Current: {current['emoji']} {current['flow_state']} ({current['flow_score']}/100)")

    # Show trend if available
    trend = flow.get('trend', {})
    if trend.get('emoji'):
        safe_print(f"   Trend: {trend['emoji']} {trend['description']}")

    # Show average
    avg = flow.get('average_flow', 0)
    safe_print(f"   Average (last 5): {avg}/100")

    # Show blockers if any
    blockers = flow.get('blockers', [])
    if blockers:
        safe_print("   \u26a0\ufe0f  Blockers:")
        for blocker in blockers[:3]:
            safe_print(f"      \u2022 {blocker}")

    # Show flow triggers status
    triggers = flow.get('triggers_present', {})
    if triggers:
        active_triggers = [name for name, present in triggers.items() if present]
        if active_triggers:
            safe_print(f"   \u2713 Active triggers: {', '.join(active_triggers)}")

    safe_print()


def _format_health_section(health):
    """Print the health score section."""
    current = health.get('current_health')
    if not current:
        return

    safe_print("\U0001f4aa Health Score (Epistemic Quality):")
    safe_print(f"   Current: {current['health_score']}/100")

    # Show trend if available
    trend = health.get('trend', {})
    if trend.get('emoji'):
        safe_print(f"   Trend: {trend['emoji']} {trend['description']}")

    # Show average
    avg = health.get('average_health', 0)
    safe_print(f"   Average (last 5): {avg}/100")

    # Show component breakdown
    components = health.get('components', {})
    if components:
        safe_print("   Components:")
        kq = components.get('knowledge_quality', {})
        ep = components.get('epistemic_progress', {})
        cap = components.get('capability', {})
        conf = components.get('confidence', {})
        eng = components.get('engagement', {})

        safe_print(f"      Knowledge Quality: {kq.get('average', 0):.2f}")
        safe_print(f"      Epistemic Progress: {ep.get('average', 0):.2f}")
        safe_print(f"      Capability: {cap.get('average', 0):.2f}")
        safe_print(f"      Confidence: {conf.get('confidence_score', 0):.2f}")
        safe_print(f"      Engagement: {eng.get('engagement', 0):.2f}")
    safe_print()


def _format_findings_subsection(breadcrumbs):
    """Print findings and unknowns subsection."""
    if breadcrumbs.get('findings'):
        safe_print("\U0001f4dd Recent Findings (last 10):")
        for i, f in enumerate(breadcrumbs['findings'][:10], 1):
            safe_print(f"   {i}. {f}")
        safe_print()

    if breadcrumbs.get('unknowns'):
        unresolved = [u for u in breadcrumbs['unknowns'] if not u['is_resolved']]
        if unresolved:
            safe_print("\u2753 Unresolved Unknowns:")
            for i, u in enumerate(unresolved[:5], 1):
                safe_print(f"   {i}. {u['unknown']}")
            safe_print()


def _format_dead_ends_and_mistakes(breadcrumbs):
    """Print dead ends, mistakes, and key decisions subsection."""
    if breadcrumbs.get('dead_ends'):
        safe_print("\U0001f480 Dead Ends (What Didn't Work):")
        for i, d in enumerate(breadcrumbs['dead_ends'][:5], 1):
            safe_print(f"   {i}. {d['approach']}")
            safe_print(f"      \u2192 Why: {d['why_failed']}")
        safe_print()

    if breadcrumbs['mistakes_to_avoid']:
        safe_print("\u26a0\ufe0f  Recent Mistakes to Avoid:")
        for i, m in enumerate(breadcrumbs['mistakes_to_avoid'][:3], 1):
            cost = m.get('cost_estimate', 'unknown')
            cause = m.get('root_cause_vector', 'unknown')
            safe_print(f"   {i}. {m['mistake']} (cost: {cost}, cause: {cause})")
            safe_print(f"      \u2192 {m['prevention']}")
        safe_print()

    if breadcrumbs.get('key_decisions'):
        safe_print("\U0001f4a1 Key Decisions:")
        for i, d in enumerate(breadcrumbs['key_decisions'], 1):
            safe_print(f"   {i}. {d}")
        safe_print()


def _format_refdocs_and_artifacts(breadcrumbs):
    """Print reference docs and recent artifacts subsection."""
    if breadcrumbs.get('reference_docs'):
        safe_print("\U0001f4c4 Reference Docs:")
        for i, doc in enumerate(breadcrumbs['reference_docs'][:5], 1):
            path = doc.get('doc_path', 'unknown')
            doc_type = doc.get('doc_type', 'unknown')
            safe_print(f"   {i}. {path} ({doc_type})")
            if doc.get('description'):
                safe_print(f"      {doc['description']}")
        safe_print()

    if breadcrumbs.get('recent_artifacts'):
        safe_print("\U0001f4dd Recently Modified Files (last 10 sessions):")
        for i, artifact in enumerate(breadcrumbs['recent_artifacts'][:10], 1):
            safe_print(f"   {i}. Session {artifact['session_id']} ({artifact['ai_id']})")
            safe_print(f"      Task: {artifact['task_summary']}")
            safe_print(f"      Files modified ({len(artifact['files_modified'])}):")
            for file in artifact['files_modified'][:5]:
                safe_print(f"        \u2022 {file}")
            if len(artifact['files_modified']) > 5:
                safe_print(f"        ... and {len(artifact['files_modified']) - 5} more")
        safe_print()


def _format_findings_section(breadcrumbs):
    """Print findings, unknowns, dead ends, mistakes, decisions, ref docs, and recent artifacts."""
    _format_findings_subsection(breadcrumbs)
    _format_dead_ends_and_mistakes(breadcrumbs)
    _format_refdocs_and_artifacts(breadcrumbs)


def _format_active_work_section(breadcrumbs):
    """Print the active work section (sessions, goals, artifacts, AI activity)."""
    if not (breadcrumbs.get('active_sessions') or breadcrumbs.get('active_goals')):
        return

    safe_print("\U0001f680 Active Work (In Progress):")
    safe_print()

    # Show active sessions
    if breadcrumbs.get('active_sessions'):
        safe_print("   \U0001f4e1 Active Sessions:")
        for sess in breadcrumbs['active_sessions'][:3]:
            from datetime import datetime
            start = datetime.fromisoformat(str(sess['start_time']))
            elapsed = datetime.now() - start
            hours = int(elapsed.total_seconds() / 3600)
            safe_print(f"      \u2022 {sess['session_id'][:8]}... ({sess['ai_id']}) - {hours}h ago")
            if sess.get('subject'):
                safe_print(f"        Subject: {sess['subject']}")
        safe_print()

    # Show active goals
    if breadcrumbs.get('active_goals'):
        safe_print("   \U0001f3af Goals In Progress:")
        for goal in breadcrumbs['active_goals'][:5]:
            beads_link = f" [BEADS: {goal['beads_issue_id']}]" if goal.get('beads_issue_id') else " \u26a0\ufe0f No BEADS link"
            safe_print(f"      \u2022 [{goal['id'][:8]}] {goal['objective']}{beads_link}")
            safe_print(f"        AI: {goal['ai_id']} | Tasks: {goal['subtask_count']}")

            # Show recent findings for this goal
            goal_findings = [f for f in breadcrumbs.get('findings_with_goals', []) if f['goal_id'] == goal['id']]
            if goal_findings:
                safe_print(f"        Latest: {goal_findings[0]['finding'][:60]}...")
        safe_print()

    # Show epistemic artifacts
    if breadcrumbs.get('epistemic_artifacts'):
        safe_print("   \U0001f4ca Epistemic Artifacts:")
        for artifact in breadcrumbs['epistemic_artifacts'][:3]:
            size_kb = artifact['size'] / 1024
            safe_print(f"      \u2022 {artifact['path']} ({size_kb:.1f} KB)")
        safe_print()

    # Show AI activity summary
    if breadcrumbs.get('ai_activity'):
        safe_print("   \U0001f465 AI Activity (Last 7 Days):")
        for ai in breadcrumbs['ai_activity'][:5]:
            safe_print(f"      \u2022 {ai['ai_id']}: {ai['session_count']} session(s)")
        safe_print()
        safe_print("   \U0001f4a1 Tip: Use format '<model>-<workstream>' (e.g., claude-cli-testing)")
        safe_print()


def _format_flow_scores_section(flow_metrics):
    """Print the flow state analysis section (flow scores per session)."""
    safe_print("\U0001f4ca Flow State Analysis (Recent Sessions):")
    safe_print()

    flow_data = flow_metrics.get('flow_scores', [])
    if flow_data:
        for i, session in enumerate(flow_data[:5], 1):
            score = session['flow_score']
            # Choose emoji based on score
            if score >= 0.9:
                emoji = "\u2b50"
            elif score >= 0.7:
                emoji = "\U0001f7e2"
            elif score >= 0.5:
                emoji = "\U0001f7e1"
            else:
                emoji = "\U0001f534"

            safe_print(f"   {i}. {session['session_id']} ({session['ai_id']})")
            safe_print(f"      Flow Score: {score:.2f} {emoji}")

            # Show top 3 components
            components = session['components']
            top_3 = sorted(components.items(), key=lambda x: x[1], reverse=True)[:3]
            safe_print(f"      Top factors: {', '.join([f'{k}={v:.2f}' for k, v in top_3])}")

            # Show recommendations if any
            if session['recommendations']:
                safe_print(f"      \U0001f4a1 {session['recommendations'][0]}")
            safe_print()

        # Show what creates flow
        safe_print("   \U0001f4a1 Flow Triggers (Optimize for these):")
        safe_print("      \u2705 CASCADE complete (PREFLIGHT \u2192 POSTFLIGHT)")
        safe_print("      \u2705 Bootstrap loaded early")
        safe_print("      \u2705 Goal with tasks")
        safe_print("      \u2705 CHECK for high-scope work")
        safe_print("      \u2705 AI naming convention (<model>-<workstream>)")
        safe_print()
    else:
        safe_print("   \U0001f4a1 No completed sessions yet")
        safe_print("   Tip: Close active sessions with POSTFLIGHT to see flow metrics")
        safe_print("   Flow score will show patterns from completed work")
        safe_print()


def _format_database_section(db_summary):
    """Print the database schema summary section."""
    safe_print("\U0001f5c4\ufe0f  Database Schema (Epistemic Data Store):")
    safe_print()

    safe_print(f"   Total Tables: {db_summary.get('total_tables', 0)}")
    safe_print(f"   Tables With Data: {db_summary.get('tables_with_data', 0)}")
    safe_print()

    # Show key tables (static knowledge reminder)
    if db_summary.get('key_tables'):
        safe_print("   \U0001f4cc Key Tables:")
        for table, description in list(db_summary['key_tables'].items())[:6]:
            safe_print(f"      \u2022 {table}: {description}")
        safe_print()

    # Show top tables by row count
    if db_summary.get('top_tables'):
        safe_print("   \U0001f4ca Most Active Tables:")
        for table_info in db_summary['top_tables'][:5]:
            safe_print(f"      \u2022 {table_info}")
        safe_print()

    # Reference to full schema
    if db_summary.get('schema_doc'):
        safe_print(f"   \U0001f4d6 Full Schema: {db_summary['schema_doc']}")
        safe_print()


def _format_structure_health_section(health):
    """Print the project structure health section."""
    safe_print("\U0001f3d7\ufe0f  Project Structure Health:")
    safe_print()

    # .get() returns None if key exists with None value -- guard against it
    confidence = health.get('confidence') or 0.0
    conformance = health.get('conformance') or 0.0

    # Choose emoji based on conformance
    if conformance >= 0.9:
        emoji = "\u2705"
    elif conformance >= 0.7:
        emoji = "\U0001f7e2"
    elif conformance >= 0.5:
        emoji = "\U0001f7e1"
    else:
        emoji = "\U0001f534"

    safe_print(f"   Detected Pattern: {health.get('detected_name', 'Unknown')} {emoji}")
    safe_print(f"   Detection Confidence: {confidence:.2f}")
    safe_print(f"   Pattern Conformance: {conformance:.2f}")
    safe_print(f"   Description: {health.get('description', '')}")
    safe_print()

    # Show violations if any
    violations = health.get('violations', [])
    if violations:
        safe_print(f"   \u26a0\ufe0f  Conformance Issues ({len(violations)}):")
        for violation in violations[:3]:
            safe_print(f"      \u2022 {violation}")
        if len(violations) > 3:
            safe_print(f"      ... and {len(violations) - 3} more")
        safe_print()

    # Show suggestions
    suggestions = health.get('suggestions', [])
    if suggestions:
        safe_print("   \U0001f4a1 Suggestions:")
        for suggestion in suggestions[:3]:
            safe_print(f"      {suggestion}")
        safe_print()


def _format_dependency_section(dep):
    """Print the dependency graph section."""
    safe_print(f"\U0001f4ca Project Dependencies ({dep.get('module_count', '?')} modules):")
    safe_print()
    if dep.get('hotspots'):
        safe_print("   \U0001f525 Coupling Hotspots:")
        for h in dep['hotspots'][:5]:
            safe_print(f"      {h['module']} ({h['importers']} importers)")
    if dep.get('entry_points'):
        safe_print(f"   \U0001f680 Entry Points: {', '.join(dep['entry_points'][:5])}")
    if dep.get('external_deps'):
        safe_print(f"   \U0001f4e6 External: {', '.join(sorted(dep['external_deps'])[:10])}")
    safe_print()


def _format_incomplete_work_section(incomplete_work):
    """Print the incomplete work section."""
    safe_print("\U0001f3af Incomplete Work:")
    for i, w in enumerate(incomplete_work, 1):
        objective = w.get('objective', w.get('goal', 'Unknown'))
        status = w.get('status', 'unknown')
        safe_print(f"   {i}. {objective} ({status})")
    safe_print()


def _format_skills_section(skills):
    """Print the available skills section."""
    safe_print("\U0001f6e0\ufe0f  Available Skills:")
    for i, skill in enumerate(skills, 1):
        tags = ', '.join(skill.get('tags', [])) if skill.get('tags') else 'no tags'
        safe_print(f"   {i}. {skill['title']} ({skill['id']})")
        safe_print(f"      Tags: {tags}")
    safe_print()


def _format_semantic_docs_section(docs):
    """Print the core documentation section."""
    safe_print("\U0001f4d6 Core Documentation:")
    for i, doc in enumerate(docs[:3], 1):
        safe_print(f"   {i}. {doc['title']}")
        safe_print(f"      Path: {doc['path']}")
    safe_print()


def _format_integrity_section(integrity):
    """Print the doc-code integrity analysis section."""
    safe_print("\U0001f50d Doc-Code Integrity Analysis:")

    if 'error' in integrity:
        safe_print(f"   \u26a0\ufe0f  Analysis failed: {integrity['error']}")
    else:
        cli = integrity['cli_commands']
        safe_print(f"   Score: {cli['integrity_score']:.1%} ({cli['total_in_code']} code, {cli['total_in_docs']} docs)")

        if integrity.get('missing_code'):
            safe_print(f"\n   \U0001f534 Missing Implementations ({cli['missing_implementations']} total):")
            for item in integrity['missing_code'][:5]:
                safe_print(f"      \u2022 empirica {item['command']} (severity: {item['severity']})")
                if item['mentioned_in']:
                    safe_print(f"        Mentioned in: {item['mentioned_in'][0]['file']}")

        if integrity.get('missing_docs'):
            safe_print(f"\n   \U0001f4dd Missing Documentation ({cli['missing_documentation']} total):")
            for item in integrity['missing_docs'][:5]:
                safe_print(f"      \u2022 empirica {item['command']}")
    safe_print()


def _format_memory_gap_section(breadcrumbs):
    """Print the memory gap analysis section."""
    analysis = breadcrumbs['memory_gap_analysis']
    enforcement = analysis.get('enforcement_mode', 'inform')

    # Select emoji based on enforcement mode
    mode_emoji = {
        'inform': '\U0001f9e0',
        'warn': '\u26a0\ufe0f',
        'strict': '\U0001f534',
        'block': '\U0001f6d1'
    }.get(enforcement, '\U0001f9e0')

    safe_print(f"{mode_emoji} Memory Gap Analysis (Mode: {enforcement.upper()}):")

    if analysis['detected']:
        _format_memory_gap_details(analysis, breadcrumbs)
    else:
        safe_print("   \u2705 No memory gaps detected - context is current")

    safe_print()


def _format_memory_gap_details(analysis, breadcrumbs):
    """Print the detailed memory gap information when gaps are detected."""
    gap_score = analysis['overall_gap']
    claimed = analysis['claimed_know']
    expected = analysis['expected_know']

    safe_print("   Knowledge Assessment:")
    safe_print(f"      Claimed KNOW:  {claimed:.2f}")
    safe_print(f"      Expected KNOW: {expected:.2f}")
    safe_print(f"      Gap Score:     {gap_score:.2f}")

    # Group gaps by type
    gaps_by_type = {}
    for gap in breadcrumbs.get('memory_gaps', []):
        gap_type = gap['type']
        if gap_type not in gaps_by_type:
            gaps_by_type[gap_type] = []
        gaps_by_type[gap_type].append(gap)

    # Display gaps by severity
    if gaps_by_type:
        safe_print("\n   Detected Gaps:")

        # Priority order
        type_order = ['confabulation', 'unreferenced_findings', 'unincorporated_unknowns',
                     'file_unawareness', 'compaction']

        severity_icon = {
            'critical': '\U0001f534',
            'high': '\U0001f7e0',
            'medium': '\U0001f7e1',
            'low': '\U0001f535'
        }

        for gap_type in type_order:
            if gap_type not in gaps_by_type:
                continue

            gaps = gaps_by_type[gap_type]

            # Show type header
            type_label = gap_type.replace('_', ' ').title()
            safe_print(f"\n      {type_label} ({len(gaps)}):")

            # Show top 3 gaps of this type
            for gap in gaps[:3]:
                icon = severity_icon.get(gap['severity'], '\u2022')
                content = gap['content'][:80] + '...' if len(gap['content']) > 80 else gap['content']
                safe_print(f"      {icon} {content}")
                if gap.get('resolution_action'):
                    safe_print(f"         \u2192 {gap['resolution_action']}")

            if len(gaps) > 3:
                safe_print(f"         ... and {len(gaps) - 3} more")

    # Show recommended actions
    if analysis.get('recommended_actions'):
        safe_print("\n   Recommended Actions:")
        for i, action in enumerate(analysis['recommended_actions'][:5], 1):
            safe_print(f"      {i}. {action}")


def _format_workflow_suggestions_section(workflow_suggestions):
    """Print workflow automation suggestions if available."""
    from empirica.cli.utils.workflow_suggestions import format_workflow_suggestions
    workflow_output = format_workflow_suggestions(workflow_suggestions)
    if workflow_output.strip():
        safe_print(workflow_output)


def _format_workspace_context_section(project_id, breadcrumbs):
    """Print workspace context section if empirica-workspace is installed."""
    try:
        from empirica_workspace.bootstrap.project_context import (  # pyright: ignore[reportMissingImports]
            get_project_bootstrap_context,
            render_workspace_context,
        )
        # Look up project_type from workspace.db
        ws_project_type = 'product'
        try:
            import sqlite3 as _sqlite3
            from pathlib import Path as _Path
            ws_db_path = _Path.home() / ".empirica" / "workspace" / "workspace.db"
            if ws_db_path.exists():
                ws_conn = _sqlite3.connect(str(ws_db_path))
                ws_cur = ws_conn.cursor()
                ws_cur.execute(
                    "SELECT project_type FROM global_projects WHERE id = ?",
                    (project_id,)
                )
                ws_row = ws_cur.fetchone()
                if ws_row and ws_row[0]:
                    ws_project_type = ws_row[0]
                ws_conn.close()
        except Exception:
            pass

        # Resolve project root from active context
        ws_project_root = None
        try:
            from empirica.utils.session_resolver import InstanceResolver as R
            ws_project_root = R.project_path()
        except Exception:
            pass
        if not ws_project_root:
            ws_project_root = os.getcwd()

        proj_info = breadcrumbs.get('project', {})
        workspace_ctx = get_project_bootstrap_context(
            project_id=proj_info.get('id', project_id),
            project_type=ws_project_type,
            project_root=ws_project_root,
        )
        if workspace_ctx:
            rendered = render_workspace_context(workspace_ctx)
            if rendered.strip():
                safe_print(rendered)
    except ImportError:
        pass  # empirica-workspace not installed
    except Exception as ws_err:
        logger.debug("Workspace context hook failed: %s", ws_err)


def _format_human_output(project_id, breadcrumbs, db, mco_config, workflow_suggestions):
    """Format and print the human-readable bootstrap output sections."""
    if mco_config:
        _format_mco_section(mco_config)

    project = breadcrumbs['project']
    last = breadcrumbs['last_activity']

    _format_project_banner(project, project_id, db)
    _format_project_summary(project, last)

    if breadcrumbs.get('ai_epistemic_handoff'):
        _format_handoff_section(breadcrumbs['ai_epistemic_handoff'])

    if breadcrumbs.get('flow_metrics'):
        _format_flow_current_section(breadcrumbs['flow_metrics'])

    if breadcrumbs.get('health_score'):
        _format_health_section(breadcrumbs['health_score'])

    _format_findings_section(breadcrumbs)
    _format_active_work_section(breadcrumbs)

    if breadcrumbs.get('flow_metrics') is not None:
        _format_flow_scores_section(breadcrumbs['flow_metrics'])

    if breadcrumbs.get('database_summary'):
        _format_database_section(breadcrumbs['database_summary'])

    if breadcrumbs.get('structure_health'):
        _format_structure_health_section(breadcrumbs['structure_health'])

    if breadcrumbs.get('dependency_graph'):
        _format_dependency_section(breadcrumbs['dependency_graph'])

    if breadcrumbs['incomplete_work']:
        _format_incomplete_work_section(breadcrumbs['incomplete_work'])

    if breadcrumbs.get('available_skills'):
        _format_skills_section(breadcrumbs['available_skills'])

    if breadcrumbs.get('semantic_docs'):
        _format_semantic_docs_section(breadcrumbs['semantic_docs'])

    if breadcrumbs.get('integrity_analysis'):
        _format_integrity_section(breadcrumbs['integrity_analysis'])

    if workflow_suggestions:
        _format_workflow_suggestions_section(workflow_suggestions)

    if breadcrumbs.get('memory_gap_analysis'):
        _format_memory_gap_section(breadcrumbs)

    _format_workspace_context_section(project_id, breadcrumbs)


def format_bootstrap_output(
    args, project_id, breadcrumbs, db=None,
    mco_config=None, workflow_suggestions=None,
    global_learnings=None, project_skills=None,
):
    """Format and print bootstrap output (JSON or human-readable)."""
    if hasattr(args, 'output') and args.output == 'json':
        _format_json_output(
            args, project_id, breadcrumbs,
            mco_config=mco_config,
            workflow_suggestions=workflow_suggestions,
            global_learnings=global_learnings,
            project_skills=project_skills,
        )
        return

    _format_human_output(project_id, breadcrumbs, db, mco_config, workflow_suggestions)
