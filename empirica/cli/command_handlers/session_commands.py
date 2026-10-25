"""
Session Management Commands - Query and manage Empirica sessions

Provides commands for:
- Listing all sessions
- Showing detailed session info with epistemic vectors
- Exporting session data to JSON
"""

import json
import logging
from datetime import datetime
from typing import Any

from empirica.utils.session_resolver import InstanceResolver as R

from ..cli_utils import handle_cli_error, print_header

# Set up logging for session commands
logger = logging.getLogger(__name__)


def _format_timestamp(ts):
    """Format timestamp handling str, datetime, or numeric timestamp."""
    if not ts:
        return None
    try:
        if isinstance(ts, str):
            return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
        elif isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        elif hasattr(ts, "strftime"):
            return ts.strftime("%Y-%m-%d %H:%M")
        else:
            return str(ts)
    except (ValueError, AttributeError, OSError):
        return str(ts) if ts else None


def _sessions_list_json_output(sessions):
    """Render sessions list as JSON."""
    if not sessions:
        print(json.dumps({"ok": False, "sessions": [], "count": 0, "message": "No sessions found"}))
        return
    sessions_list = []
    for row in sessions:
        session_id, ai_id, user_id, start_time, end_time, cascades, conf, drift = row
        sessions_list.append(
            {
                "session_id": session_id,
                "ai_id": ai_id,
                "user_id": user_id,
                "start_time": str(start_time),
                "end_time": str(end_time) if end_time else None,
                "total_cascades": cascades,
                "avg_confidence": conf,
                "drift_detected": bool(drift),
            }
        )
    print(json.dumps({"ok": True, "sessions": sessions_list, "count": len(sessions)}))


def _sessions_list_pretty_output(sessions, args):
    """Render sessions list as pretty terminal output."""
    print_header("📋 Empirica Sessions")

    if not sessions:
        logger.info("No sessions found in database")
        print("\n📭 No sessions found")
        print("💡 Create a session with: empirica preflight <task>")
        return

    print(f"\n📊 Found {len(sessions)} sessions:\n")

    for row in sessions:
        session_id, ai_id, user_id, start_time, end_time, cascades, conf, drift = row
        start = _format_timestamp(start_time) or "N/A"
        end = _format_timestamp(end_time) or "Active"
        status = "✅" if end_time else "⏳"
        drift_icon = "⚠️" if drift else ""

        print(f"{status} {session_id[:8]}")
        print(f"   🤖 AI: {ai_id}")
        if user_id:
            print(f"   👤 User: {user_id}")
        print(f"   📅 Started: {start}")
        print(f"   🏁 Ended: {end}")
        print(f"   🔄 Cascades: {cascades}")
        if conf:
            print(f"   📊 Avg Confidence: {conf:.2f}")
        if drift:
            print(f"   {drift_icon} Drift Detected")
        print()

    if len(sessions) >= 50 and not hasattr(args, "limit"):
        print("💡 Showing 50 most recent sessions. Use --limit to see more.")

    print("💡 View details: empirica sessions show <session_id>")


def handle_sessions_list_command(args):
    """List all sessions with summary information"""
    try:
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()  # Use path resolver
        cursor = db.conn.cursor()

        # Build query with optional AI ID filter
        query = """
            SELECT
                session_id, ai_id, user_id, start_time, end_time,
                total_cascades, avg_confidence, drift_detected
            FROM sessions
        """
        params = []

        if hasattr(args, "ai_id") and args.ai_id:
            query += "WHERE ai_id = ? "
            params.append(args.ai_id)

        query += "ORDER BY start_time DESC LIMIT ?"
        params.append(args.limit if hasattr(args, "limit") else 50)

        cursor.execute(query, params)
        sessions = cursor.fetchall()
        logger.info(f"Found {len(sessions)} sessions to display")

        if hasattr(args, "output") and args.output == "json":
            _sessions_list_json_output(sessions)
        else:
            _sessions_list_pretty_output(sessions, args)

        db.close()

    except Exception as e:
        handle_cli_error(e, "Listing sessions", getattr(args, "verbose", False))


def _print_vectors_block(label, vectors, verbose):
    """Print epistemic vectors block (preflight or postflight)."""
    print(f"\n{label}")
    print(f"   • KNOW:    {vectors.get('know', 0.5):.2f}")
    print(f"   • DO:      {vectors.get('do', 0.5):.2f}")
    print(f"   • CONTEXT: {vectors.get('context', 0.5):.2f}")

    if verbose:
        print("\n   Comprehension:")
        print(f"   • CLARITY:   {vectors.get('clarity', 0.5):.2f}")
        print(f"   • COHERENCE: {vectors.get('coherence', 0.5):.2f}")
        print(f"   • SIGNAL:    {vectors.get('signal', 0.5):.2f}")
        print(f"   • DENSITY:   {vectors.get('density', 0.5):.2f}")

        print("\n   Execution:")
        print(f"   • STATE:      {vectors.get('state', 0.5):.2f}")
        print(f"   • CHANGE:     {vectors.get('change', 0.5):.2f}")
        print(f"   • COMPLETION: {vectors.get('completion', 0.5):.2f}")
        print(f"   • IMPACT:     {vectors.get('impact', 0.5):.2f}")

        print("\n   Meta-Cognitive:")
        print(f"   • ENGAGEMENT:  {vectors.get('engagement', 0.5):.2f}")
        print(f"   • UNCERTAINTY: {vectors.get('uncertainty', 0.5):.2f}")


def _print_session_detail(summary, session_id_arg, verbose):
    """Print pretty session detail output (cascades, vectors, delta, tools)."""
    session_id = summary["session_id"]
    print_header(f"📊 Session Details: {session_id[:8]}")

    print(f"\n🆔 Session ID: {session_id}")
    print(f"🤖 AI: {summary['ai_id']}")
    print(f"📅 Started: {summary['start_time']}")
    if summary.get("end_time"):
        print(f"🏁 Ended: {summary['end_time']}")
    else:
        print("⏳ Status: Active")

    print(f"\n🔄 Total Cascades: {summary['total_cascades']}")
    if summary.get("avg_confidence"):
        print(f"📊 Average Confidence: {summary['avg_confidence']:.2f}")

    if verbose and isinstance(summary.get("cascades"), list):
        print("\n📋 Cascade Tasks:")
        for i, cascade in enumerate(summary["cascades"][:10], 1):
            if isinstance(cascade, dict):
                task = cascade.get("task", "Unknown")
                conf = cascade.get("final_confidence")
                print(f"   {i}. {task}")
                if conf:
                    print(f"      Confidence: {conf:.2f}")
            else:
                print(f"   {i}. {cascade}")
        if summary["total_cascades"] > 10:
            print(f"   ... and {summary['total_cascades'] - 10} more")

    if summary.get("preflight"):
        _print_vectors_block("🚀 Preflight Epistemic State:", summary["preflight"], verbose)
    if summary.get("postflight"):
        _print_vectors_block("🏁 Postflight Epistemic State:", summary["postflight"], verbose)

    if summary.get("epistemic_delta"):
        print("\n📈 Learning Delta (Preflight → Postflight):")
        delta = summary["epistemic_delta"]
        significant = {k: v for k, v in delta.items() if abs(v) >= 0.05}
        if significant:
            for key, value in sorted(significant.items(), key=lambda x: abs(x[1]), reverse=True):
                icon = "↗" if value > 0 else "↘"
                print(f"   {icon} {key.upper():12s} {value:+.2f}")
        else:
            print("   ➖ Minimal change (all < ±0.05)")

    if summary.get("tools_used"):
        print("\n🔧 Investigation Tools Used:")
        for tool in summary["tools_used"]:
            print(f"   • {tool['tool']}: {tool['count']} times")

    print(f"\n💡 Export to JSON: empirica sessions export {session_id_arg}")


def handle_sessions_show_command(args):
    """Show detailed session information including epistemic vectors"""
    try:
        import json

        from empirica.data.session_database import SessionDatabase

        session_id_arg = args.session_id or getattr(args, "session_id_named", None)
        if not session_id_arg:
            if getattr(args, "output", None) == "json":
                print(json.dumps({"ok": False, "error": "Session ID required"}))
            else:
                print("\n❌ Session ID required")
                print("💡 Usage: empirica sessions-show <session-id>")
                print("💡 Or: empirica sessions-show --session-id <session-id>")
            return

        try:
            session_id = R.resolve_session(session_id_arg)
        except ValueError as e:
            if getattr(args, "output", None) == "json":
                print(json.dumps({"ok": False, "error": str(e)}))
            else:
                print(f"\n❌ {e!s}")
                print(f"💡 Provided: {session_id_arg}")
                print("💡 List sessions with: empirica sessions-list")
            return

        db = SessionDatabase()
        summary = db.get_session_summary(session_id, detail_level="detailed")

        if not summary:
            logger.warning(f"Session not found: {session_id_arg}")
            if getattr(args, "output", None) == "json":
                print(json.dumps({"ok": False, "error": f"Session not found: {session_id_arg}"}))
            else:
                print(f"\n❌ Session not found: {session_id_arg}")
                print("💡 List sessions with: empirica sessions list")
            db.close()
            return

        if getattr(args, "output", None) == "json":
            print(json.dumps({"ok": True, "session": summary}))
        else:
            _print_session_detail(summary, session_id_arg, args.verbose)

        db.close()

    except Exception as e:
        handle_cli_error(e, "Showing session details", getattr(args, "verbose", False))


def _print_snapshot_pretty(session_id, snapshot):
    """Print human-readable session snapshot output."""
    print(f"\n📸 Session Snapshot: {session_id[:8]}...")
    print(f"   AI: {snapshot['ai_id']}")
    if snapshot.get("subject"):
        print(f"   Subject: {snapshot['subject']}")

    git = snapshot["git_state"]
    if "error" not in git:
        print("\n🔀 Git State:")
        print(f"   Branch: {git['branch']}")
        print(f"   Commit: {git['commit']}")
        print(f"   Diff: {git['diff_stat']}")
        if git.get("last_5_commits"):
            print("   Recent commits:")
            for commit in git["last_5_commits"][:3]:
                print(f"      {commit}")

    trajectory = snapshot["epistemic_trajectory"]
    if trajectory:
        print("\n🧠 Epistemic Trajectory:")
        if "preflight" in trajectory:
            pre = trajectory["preflight"]
            print(f"   PREFLIGHT: know={pre.get('know', 0):.2f}, uncertainty={pre.get('uncertainty', 0):.2f}")
        if "check_gates" in trajectory:
            print(f"   CHECK gates: {len(trajectory['check_gates'])} decision points")
        if "postflight" in trajectory:
            post = trajectory["postflight"]
            print(f"   POSTFLIGHT: know={post.get('know', 0):.2f}, uncertainty={post.get('uncertainty', 0):.2f}")

    delta = snapshot.get("learning_delta", {})
    if delta:
        print("\n📈 Learning Delta:")
        significant = {k: v for k, v in delta.items() if abs(v) >= 0.1}
        for key, value in sorted(significant.items(), key=lambda x: abs(x[1]), reverse=True)[:5]:
            sign = "+" if value > 0 else ""
            print(f"   {key}: {sign}{value:.3f}")

    goals = snapshot.get("active_goals", [])
    if goals:
        print(f"\n🎯 Active Goals ({len(goals)}):")
        for goal in goals[:3]:
            print(f"   - {goal['objective']} ({goal['progress']})")

    sources = snapshot.get("sources_referenced", [])
    if sources:
        print(f"\n📚 Sources Referenced ({len(sources)}):")
        for src in sources[:5]:
            print(f"   - {src['title']} ({src['type']}, confidence={src['confidence']:.2f})")


def handle_session_snapshot_command(args):
    """Handle session-snapshot command - show where you left off"""
    import json

    from empirica.data.session_database import SessionDatabase

    session_id = R.resolve_session(args.session_id)

    db = SessionDatabase()
    snapshot = db.get_session_snapshot(session_id)
    db.close()

    if not snapshot:
        print(f"❌ Session not found: {args.session_id}")
        return 1

    if args.output == "json":
        print(json.dumps(snapshot, indent=2))
        return 0

    _print_snapshot_pretty(session_id, snapshot)
    return 0


def handle_sessions_export_command(args):
    """Export session data to JSON file"""
    try:
        from empirica.data.session_database import SessionDatabase

        # Support both positional and named argument for session ID
        session_id_arg = args.session_id or getattr(args, "session_id_named", None)
        if not session_id_arg:
            print("\n❌ Session ID required")
            print("💡 Usage: empirica sessions-export <session-id>")
            print("💡 Or: empirica sessions-export --session-id <session-id>")
            return

        # Resolve session alias to UUID
        try:
            session_id = R.resolve_session(session_id_arg)
        except ValueError as e:
            print(f"\n❌ {e!s}")
            print(f"💡 Provided: {session_id_arg}")
            return

        print_header(f"📦 Exporting Session: {session_id[:8]}")

        db = SessionDatabase()  # Use path resolver

        # Get full session summary (use resolved session_id)
        summary = db.get_session_summary(session_id, detail_level="full")

        if not summary:
            logger.warning(f"Session not found for export: {session_id_arg}")
            print(f"\n❌ Session not found: {session_id_arg}")
            db.close()
            return

        # Check if output format is JSON (to stdout)
        output_format = getattr(args, "output_format", "file")
        if output_format == "json" or getattr(args, "format", None) == "json":
            # Output JSON to stdout
            print(json.dumps({"ok": True, "session": summary}))
            db.close()
            return

        # Determine output file
        output_file = args.output if hasattr(args, "output") and args.output else f"session_{session_id_arg[:8]}.json"

        # Write to file
        with open(output_file, "w") as f:
            json.dump(summary, f, indent=2, default=str)

        logger.info(f"Session data exported to {output_file}")

        print("\n✅ Session exported successfully")
        print(f"📄 File: {output_file}")
        print(f"📊 Size: {len(json.dumps(summary, default=str))} bytes")

        # Summary stats
        print("\n📋 Exported Data:")
        print(f"   • Session ID: {summary['session_id']}")
        print(f"   • AI: {summary['ai_id']}")
        print(f"   • Cascades: {summary['total_cascades']}")
        if summary.get("preflight"):
            print("   • Preflight vectors: ✅")
        if summary.get("postflight"):
            print("   • Postflight vectors: ✅")
        if summary.get("epistemic_delta"):
            print("   • Learning delta: ✅")

        db.close()

    except Exception as e:
        handle_cli_error(e, "Exporting session", getattr(args, "verbose", False))


# handle_session_end_command removed - use handoff-create instead


def _compact_vectors_to_save(db, session_id, current_vectors):
    """The vectors a pre-compact checkpoint records: the hook's current ones, else each vector's last RECORDED value.

    The newest row is usually a CHECK that assessed only some of the 13; the continuation PREFLIGHT built from it then invented
    numbers for the rest (uncertainty 0.3 + 0.05, state 0.7) and post-compact deltas were measured against them."""
    if current_vectors:
        logger.info("Using current_vectors from hook input (accurate pre-compact state)")
        return current_vectors
    recorded = db.get_last_recorded_vectors(session_id)
    if recorded:
        logger.warning("Using the last recorded vectors (no current_vectors provided)")
        return recorded
    return None


def _compact_create_continuation(db, session_id, ai_id, project_id, compact_mode, vectors_to_save, output):
    """Create continuation session with PREFLIGHT checkpoint for memory compact."""
    from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger

    continuation_session_id = db.create_session(ai_id=ai_id, subject=None, parent_session_id=session_id)

    if project_id:
        cursor = db.conn.cursor()
        cursor.execute("UPDATE sessions SET project_id = ? WHERE session_id = ?", (project_id, continuation_session_id))
        db.conn.commit()

    # Calculate recommended PREFLIGHT for continuation
    recommended_preflight = None
    if vectors_to_save:
        recommended_preflight = vectors_to_save.copy()
        if "context" in recommended_preflight:
            recommended_preflight["context"] = min(recommended_preflight.get("context", 0.5) + 0.10, 1.0)
        recommended_preflight["uncertainty"] = min(recommended_preflight.get("uncertainty", 0.3) + 0.05, 1.0)
        recommended_preflight["state"] = recommended_preflight.get("state", 0.7)
        recommended_preflight["change"] = 0.20
        recommended_preflight["completion"] = 0.15

    # Create PREFLIGHT checkpoint in continuation session (CRITICAL for delta calculation)
    continuation_logger = GitEnhancedReflexLogger(session_id=continuation_session_id)

    if recommended_preflight:
        preflight_checkpoint_id = continuation_logger.add_checkpoint(
            phase="PREFLIGHT",
            vectors=recommended_preflight,
            metadata={
                "parent_session_id": session_id,
                "reason": "memory_compact_continuation",
                "compact_mode": compact_mode,
                "reasoning": "Continuation session PREFLIGHT (adjusted from pre-compact state + bootstrap context)",
            },
            epistemic_tags={"continuation": True, "memory_compact": True},
        )
        logger.info(f"Continuation PREFLIGHT checkpoint created: {preflight_checkpoint_id[:8]}")
        output["continuation_preflight"] = {
            "checkpoint_id": preflight_checkpoint_id,
            "vectors": recommended_preflight,
            "timestamp": datetime.now().isoformat(),
        }
    else:
        logger.warning("No vectors available for continuation PREFLIGHT checkpoint")

    output["continuation"] = {
        "new_session_id": continuation_session_id,
        "parent_session_id": session_id,
        "ai_id": ai_id,
        "lineage_depth": 1,
    }
    logger.info(f"Continuation session created: {continuation_session_id[:8]}")

    if recommended_preflight:
        output["recommended_preflight"] = recommended_preflight
        output["calibration_notes"] = (
            "CONTEXT +0.10 (bootstrap loaded), "
            "UNCERTAINTY +0.05 (fresh session), "
            "CHANGE/COMPLETION reset for continuation"
        )

    return continuation_session_id


def handle_memory_compact_command(args):
    """
    Memory-compact: Create epistemic continuity across session boundaries

    Workflow:
    1. Checkpoint current epistemic state (pre-compact)
    2. Run project-bootstrap to load ground truth
    3. Create continuation session with lineage
    4. Return formatted output for IDE injection

    Args from JSON stdin:
        session_id: Session to compact (supports aliases)
        create_continuation: bool (default: true)
        include_bootstrap: bool (default: true)
        checkpoint_current: bool (default: true)
        compact_mode: "full" | "minimal" | "context_only" (default: "full")
    """
    try:
        import sys

        from empirica.data.session_database import SessionDatabase

        # Read JSON config from stdin or file
        if hasattr(args, "config") and args.config:
            if args.config == "-":
                config = json.load(sys.stdin)
            else:
                with open(args.config) as f:
                    config = json.load(f)
        else:
            config = json.load(sys.stdin)

        session_id_arg = config.get("session_id")
        if not session_id_arg:
            print(
                json.dumps({"ok": False, "error": "session_id required", "hint": "Provide session_id in JSON config"})
            )
            return 1

        create_continuation = config.get("create_continuation", True)
        include_bootstrap = config.get("include_bootstrap", True)
        checkpoint_current = config.get("checkpoint_current", True)
        compact_mode = config.get("compact_mode", "full")
        current_vectors = config.get("current_vectors", None)

        try:
            session_id = R.resolve_session(session_id_arg)
        except ValueError as e:
            print(json.dumps({"ok": False, "error": str(e), "provided": session_id_arg}))
            return 1

        logger.info(f"Starting memory-compact for session {session_id[:8]}...")
        db = SessionDatabase()

        session_info = db.get_session(session_id)
        if not session_info:
            print(json.dumps({"ok": False, "error": f"Session not found: {session_id_arg}"}))
            db.close()
            return 1

        project_id = session_info.get("project_id")
        ai_id = session_info.get("ai_id", "unknown")
        output = {"ok": True, "operation": "memory_compact", "session_id": session_id, "compact_mode": compact_mode}

        # Step 1: Checkpoint current state (pre-compact tag)
        vectors_to_save = None
        if checkpoint_current:
            logger.info("Creating pre-compact checkpoint...")
            vectors_to_save = _compact_vectors_to_save(db, session_id, current_vectors)

            if vectors_to_save:
                from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger

                reflex_logger = GitEnhancedReflexLogger(session_id=session_id)
                checkpoint_id = reflex_logger.add_checkpoint(
                    phase="PRE_MEMORY_COMPACT",
                    vectors=vectors_to_save,
                    metadata={"reasoning": "Pre-compact epistemic state snapshot for continuity measurement"},
                    epistemic_tags={"memory_compact": True, "pre_compact": True},
                )
                output["pre_compact_checkpoint"] = {
                    "checkpoint_id": checkpoint_id,
                    "vectors": vectors_to_save,
                    "timestamp": datetime.now().isoformat(),
                }
                logger.info(f"Pre-compact checkpoint created: {checkpoint_id[:8]}")
            else:
                logger.warning("No epistemic vectors available - skipping checkpoint")
                output["pre_compact_checkpoint"] = None

        # Step 2: Run project-bootstrap (load ground truth)
        bootstrap_context = None
        if include_bootstrap and project_id:
            logger.info(f"Loading bootstrap context for project {project_id[:8]}...")
            try:
                bootstrap_context = db.bootstrap_project_breadcrumbs(
                    project_id=project_id,
                    check_integrity=False,
                    context_to_inject=True,
                    task_description=None,
                    epistemic_state=None,
                    subject=None,
                )
                output["bootstrap_context"] = bootstrap_context
                logger.info(
                    f"Bootstrap loaded: {len(bootstrap_context.get('findings', []))} findings, "
                    f"{len(bootstrap_context.get('unknowns', []))} unknowns, "
                    f"{len(bootstrap_context.get('incomplete_work', []))} incomplete goals"
                )
            except Exception as e:
                logger.error(f"Bootstrap failed: {e}")
                output["bootstrap_context"] = {"error": str(e)}

        # Step 3: Create continuation session
        continuation_session_id = None
        if create_continuation:
            logger.info("Creating continuation session...")
            continuation_session_id = _compact_create_continuation(
                db, session_id, ai_id, project_id, compact_mode, vectors_to_save, output
            )

        # Step 5: Format for IDE injection
        if compact_mode == "full":
            output["ide_injection"] = format_ide_injection(
                session_id=session_id,
                continuation_session_id=continuation_session_id,
                bootstrap_context=bootstrap_context,
                pre_compact_vectors=vectors_to_save,
            )

        db.close()
        print(json.dumps(output, indent=2, default=str))
        return 0

    except Exception as e:
        handle_cli_error(e, "Memory compact", getattr(args, "verbose", False))
        return 1


def _adopt_autodetect_project(from_instance, result):
    """Auto-detect project path for transaction adoption. Returns project_path or None."""
    from pathlib import Path

    # Check instance_projects for from_instance
    from_instance_file = Path.home() / ".empirica" / "instance_projects" / f"{from_instance}.json"
    if from_instance_file.exists():
        try:
            with open(from_instance_file) as f:
                data = json.load(f)
            project_path = data.get("project_path")
            if project_path:
                result["actions"].append(f"Found project in instance_projects: {project_path}")
                return project_path
        except Exception:
            pass

    # Fallback: scan workspace.db for projects with matching transaction file
    try:
        import sqlite3

        ws_db = Path.home() / ".empirica" / "workspace" / "workspace.db"
        if ws_db.exists():
            conn = sqlite3.connect(str(ws_db))
            cursor = conn.cursor()
            cursor.execute("SELECT trajectory_path FROM global_projects WHERE trajectory_path IS NOT NULL")
            all_projects = [row[0] for row in cursor.fetchall()]
            conn.close()

            for proj in all_projects:
                tx_file = Path(proj) / ".empirica" / f"active_transaction_{from_instance}.json"
                if tx_file.exists():
                    result["actions"].append(f"Found transaction in workspace project: {proj}")
                    return proj
    except Exception:
        pass

    return None


def _adopt_execute_rename(from_instance, to_instance, from_tx_file, to_tx_file, project_path, result):
    """Execute the actual transaction file rename and instance_projects update."""
    import os
    import shutil
    from pathlib import Path

    # Step 1: Rename transaction file (skip if same instance)
    if from_instance == to_instance:
        result["actions"].append(f"Same instance ({from_instance}) — transaction file already in place")
    else:
        if to_tx_file.exists():
            backup_file = to_tx_file.with_suffix(".json.backup")
            shutil.move(str(to_tx_file), str(backup_file))
            result["actions"].append(f"Backed up existing: {backup_file}")
        shutil.move(str(from_tx_file), str(to_tx_file))
        result["actions"].append(f"Renamed: {from_tx_file.name} → {to_tx_file.name}")

    # Step 2: Update instance_projects mapping
    instance_projects_dir = Path.home() / ".empirica" / "instance_projects"
    instance_projects_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    to_instance_file = instance_projects_dir / f"{to_instance}.json"
    try:
        instance_data = {
            "project_path": str(project_path),
            "adopted_from": from_instance,
            "adopted_at": datetime.now().isoformat(),
        }
        with open(to_instance_file, "w") as f:
            json.dump(instance_data, f, indent=2)
        os.chmod(to_instance_file, 0o600)
        result["actions"].append(f"Updated: {to_instance_file}")
    except Exception as e:
        result["warning"] = f"Failed to update instance_projects: {e}"
        result["actions"].append(f"Warning: {e}")

    # Step 3: Clean up old instance_projects file
    from_instance_file = instance_projects_dir / f"{from_instance}.json"
    if from_instance_file.exists():
        try:
            from_instance_file.unlink()
            result["actions"].append(f"Removed: {from_instance_file}")
        except Exception:
            pass


def _adopt_print_error(result, output_json, extra_hints=None):
    """Print adoption error in JSON or human format."""
    if output_json:
        print(json.dumps(result))
    else:
        print(f"❌ {result['error']}")
        for hint in extra_hints or []:
            print(hint)


def _adopt_output_result(result, output_json, from_instance, to_instance, project_path):
    """Print transaction adoption result in JSON or human format."""
    if output_json:
        print(json.dumps(result))
    else:
        print("✅ Transaction adopted successfully")
        print("\n📋 Transaction:")
        print(f"   ID: {result['transaction']['transaction_id']}...")
        print(f"   Session: {result['transaction']['session_id']}...")
        print(f"   Status: {result['transaction']['status']}")
        print("\n📁 Instance mapping:")
        print(f"   {from_instance} → {to_instance}")
        print(f"\n📂 Project: {project_path}")
        print("\n💡 Your transaction is now active. Continue working!")


def handle_transaction_adopt_command(args):
    """
    Adopt an orphaned transaction from a different instance.

    Use case: After tmux restart, pane IDs change. The old transaction file
    is orphaned because the new pane can't find it.
    """
    from pathlib import Path

    from_instance = args.from_instance
    to_instance = getattr(args, "to_instance", None)
    project_path = getattr(args, "project_path", None)
    dry_run = getattr(args, "dry_run", False)
    output_json = getattr(args, "output", "human") == "json"

    result: dict[str, Any] = {
        "ok": False,
        "from_instance": from_instance,
        "to_instance": to_instance,
        "project_path": project_path,
        "dry_run": dry_run,
        "actions": [],
    }

    if not to_instance:
        to_instance = R.instance_id() or "default"
        result["to_instance"] = to_instance
        result["actions"].append(f"Auto-detected current instance: {to_instance}")

    if not project_path:
        project_path = _adopt_autodetect_project(from_instance, result)
        if not project_path:
            result["error"] = (
                f"Could not find project with transaction for instance {from_instance}. Specify --project."
            )
            _adopt_print_error(result, output_json)
            return 1
        result["project_path"] = project_path

    project_dir = Path(project_path)
    empirica_dir = project_dir / ".empirica"

    if not empirica_dir.exists():
        result["error"] = f"Not an Empirica project: {project_path}"
        _adopt_print_error(result, output_json)
        return 1

    from_tx_file = empirica_dir / f"active_transaction_{from_instance}.json"
    to_tx_file = empirica_dir / f"active_transaction_{to_instance}.json"

    if not from_tx_file.exists():
        result["error"] = f"Transaction file not found: {from_tx_file}"
        _adopt_print_error(
            result,
            output_json,
            [
                f"💡 Check if {from_instance} is the correct source instance ID",
                f"💡 List transaction files: ls {empirica_dir}/active_transaction_*.json",
            ],
        )
        return 1

    if to_tx_file.exists():
        result["warning"] = f"Target transaction file already exists: {to_tx_file}"
        result["actions"].append("Target file exists - will be overwritten")

    try:
        with open(from_tx_file) as f:
            tx_data = json.load(f)
        result["transaction"] = {
            "transaction_id": tx_data.get("transaction_id", "unknown")[:8],
            "session_id": tx_data.get("session_id", "unknown")[:8],
            "status": tx_data.get("status", "unknown"),
        }
    except Exception as e:
        result["error"] = f"Failed to read transaction file: {e}"
        _adopt_print_error(result, output_json)
        return 1

    if dry_run:
        result["actions"].append(f"[DRY RUN] Would rename: {from_tx_file} → {to_tx_file}")
        result["actions"].append(f"[DRY RUN] Would update: ~/.empirica/instance_projects/{to_instance}.json")
        result["ok"] = True
        if output_json:
            print(json.dumps(result))
        else:
            print("🔍 DRY RUN - No changes made")
            print("\n📋 Transaction to adopt:")
            print(f"   ID: {result['transaction']['transaction_id']}...")
            print(f"   Session: {result['transaction']['session_id']}...")
            print(f"   Status: {result['transaction']['status']}")
            print("\n📁 Files:")
            print(f"   From: {from_tx_file}")
            print(f"   To:   {to_tx_file}")
            print("\n✅ Run without --dry-run to adopt")
        return 0

    try:
        _adopt_execute_rename(from_instance, to_instance, from_tx_file, to_tx_file, project_path, result)
    except Exception as e:
        result["error"] = f"Failed to rename transaction file: {e}"
        _adopt_print_error(result, output_json)
        return 1

    result["ok"] = True
    _adopt_output_result(result, output_json, from_instance, to_instance, project_path)
    return 0


def format_ide_injection(session_id, continuation_session_id, bootstrap_context, pre_compact_vectors):
    """
    Format bootstrap context for IDE injection into conversation summary

    Returns markdown-formatted context for the IDE to inject after summarization.
    """
    lines = []

    lines.append("## Empirica Context (Loaded from Ground Truth)")
    lines.append("")
    lines.append("**Session Continuity:**")
    lines.append(f"- Continuing from session: `{session_id}`")
    if continuation_session_id:
        lines.append(f"- New session: `{continuation_session_id}`")

    if pre_compact_vectors:
        know = pre_compact_vectors.get("foundation", {}).get("know", 0)
        uncertainty = pre_compact_vectors.get("uncertainty", 0)
        lines.append(f"- Pre-compact epistemic state: know={know:.2f}, uncertainty={uncertainty:.2f}")

    lines.append("")

    # Recent findings
    if bootstrap_context and "findings" in bootstrap_context:
        findings = bootstrap_context["findings"]
        if findings:
            lines.append(f"**Recent Findings ({len(findings)} total):**")
            for i, finding in enumerate(findings[:10], 1):
                finding_text = finding if isinstance(finding, str) else finding.get("finding_text", str(finding))
                lines.append(f"{i}. {finding_text[:100]}...")
            lines.append("")

    # Unresolved unknowns
    if bootstrap_context and "unknowns" in bootstrap_context:
        unknowns = bootstrap_context["unknowns"]
        if unknowns:
            lines.append(f"**Unresolved Unknowns ({len(unknowns)} total):**")
            for i, unknown in enumerate(unknowns[:10], 1):
                unknown_text = unknown if isinstance(unknown, str) else unknown.get("unknown", str(unknown))
                lines.append(f"{i}. {unknown_text[:100]}...")
            lines.append("")

    # Incomplete goals
    if bootstrap_context and "incomplete_work" in bootstrap_context:
        goals = bootstrap_context["incomplete_work"]
        if goals:
            lines.append(f"**Incomplete Goals ({len(goals)} in-progress):**")
            for i, goal in enumerate(goals[:5], 1):
                objective = goal.get("goal", goal.get("objective", str(goal)))
                progress = goal.get("progress", "?/?")
                lines.append(f"{i}. {objective[:70]} - {progress}")
            lines.append("")

    # Recommended PREFLIGHT
    if pre_compact_vectors:
        lines.append("**Recommended PREFLIGHT:**")
        know = pre_compact_vectors.get("foundation", {}).get("know", 0)
        context = min(pre_compact_vectors.get("foundation", {}).get("context", 0.5) + 0.10, 1.0)
        uncertainty = min(pre_compact_vectors.get("uncertainty", 0.3) + 0.05, 1.0)
        lines.append(f"- engagement={pre_compact_vectors.get('engagement', 0.85):.2f}")
        lines.append(f"- know={know:.2f}, context={context:.2f}")
        lines.append(f"- uncertainty={uncertainty:.2f}")

    return "\n".join(lines)
