"""
Session Create Command - Explicit session creation

Session lifecycle:
- Auto-closes previous sessions in SAME project with POSTFLIGHT
- Warns about active sessions in OTHER projects (doesn't auto-close)
- Ensures complete trajectories for calibration
"""

import json
import logging
import re
import sys

from empirica.utils.session_resolver import InstanceResolver as R

from ..cli_utils import handle_cli_error

logger = logging.getLogger(__name__)


def _is_uuid_format(value: str) -> bool:
    """Check if a string looks like a UUID (8-4-4-4-12 hex format)."""
    if not value or not isinstance(value, str):
        return False
    uuid_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    return bool(re.match(uuid_pattern, value.lower()))


def auto_close_previous_sessions(db, ai_id, current_project_id, current_instance_id=None, output_format="json"):
    """
    Auto-close previous sessions with POSTFLIGHT for clean lifecycle.

    - Same project AND same instance: auto-close with POSTFLIGHT
    - Same project, different instance: leave open (multi-pane support)
    - Other projects: warn only (don't auto-close)

    Args:
        db: SessionDatabase instance
        ai_id: AI identifier
        current_project_id: Project ID for the new session
        current_instance_id: Instance ID (e.g., tmux:%8) for multi-pane isolation
        output_format: Output format ('json' or 'human')

    Returns: dict with closed_sessions and warnings
    """
    from datetime import datetime

    cursor = db.conn.cursor()
    result = {"closed_sessions": [], "warnings": []}

    # Find all active sessions for this AI (no end_time timestamp)
    cursor.execute(
        """
        SELECT session_id, project_id, created_at, instance_id
        FROM sessions
        WHERE ai_id = ? AND end_time IS NULL
        ORDER BY created_at DESC
    """,
        (ai_id,),
    )
    active_sessions = cursor.fetchall()

    for session in active_sessions:
        session_id = session["session_id"]
        session_project_id = session["project_id"]
        session_instance_id = session["instance_id"]

        # Same project: check instance_id for multi-pane support
        if session_project_id == current_project_id:
            # Only auto-close if same instance OR no instance tracking
            # This allows multiple tmux panes to have concurrent sessions
            if current_instance_id and session_instance_id and session_instance_id != current_instance_id:
                # Different instance in same project - leave open (multi-pane)
                continue
            # Same project: auto-close with POSTFLIGHT

            # Get last CHECK or PREFLIGHT vectors for POSTFLIGHT
            cursor.execute(
                """
                SELECT know, uncertainty, do, context, clarity, coherence,
                       signal, density, state, change, completion, impact, engagement
                FROM reflexes
                WHERE session_id = ? AND phase IN ('CHECK', 'PREFLIGHT')
                ORDER BY timestamp DESC LIMIT 1
            """,
                (session_id,),
            )
            last_vectors = cursor.fetchone()

            # Create auto-POSTFLIGHT
            if last_vectors:
                vectors = {
                    "know": last_vectors["know"] or 0.5,
                    "uncertainty": last_vectors["uncertainty"] or 0.3,
                    "do": last_vectors["do"] or 0.5,
                    "context": last_vectors["context"] or 0.5,
                    "clarity": last_vectors["clarity"] or 0.5,
                    "coherence": last_vectors["coherence"] or 0.5,
                    "signal": last_vectors["signal"] or 0.5,
                    "density": last_vectors["density"] or 0.5,
                    "state": last_vectors["state"] or 0.5,
                    "change": last_vectors["change"] or 0.5,
                    "completion": 1.0,  # Session ended = complete
                    "impact": last_vectors["impact"] or 0.5,
                    "engagement": last_vectors["engagement"] or 0.5,
                }
            else:
                # No vectors found, use defaults
                vectors = {
                    "know": 0.5,
                    "uncertainty": 0.3,
                    "do": 0.5,
                    "context": 0.5,
                    "clarity": 0.5,
                    "coherence": 0.5,
                    "signal": 0.5,
                    "density": 0.5,
                    "state": 0.5,
                    "change": 0.5,
                    "completion": 1.0,
                    "impact": 0.5,
                    "engagement": 0.5,
                }

            # Insert auto-POSTFLIGHT
            timestamp = datetime.now().timestamp()
            reflex_data = json.dumps({"auto_closed": True, "reason": "New session created", "vectors": vectors})

            cursor.execute(
                """
                INSERT INTO reflexes (
                    session_id, phase, know, uncertainty, do, context,
                    clarity, coherence, signal, density, state, change,
                    completion, impact, engagement, reflex_data, timestamp
                ) VALUES (?, 'POSTFLIGHT', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    session_id,
                    vectors["know"],
                    vectors["uncertainty"],
                    vectors["do"],
                    vectors["context"],
                    vectors["clarity"],
                    vectors["coherence"],
                    vectors["signal"],
                    vectors["density"],
                    vectors["state"],
                    vectors["change"],
                    vectors["completion"],
                    vectors["impact"],
                    vectors["engagement"],
                    reflex_data,
                    timestamp,
                ),
            )

            # Mark session as ended
            cursor.execute(
                """
                UPDATE sessions SET end_time = ? WHERE session_id = ?
            """,
                (datetime.now().isoformat(), session_id),
            )

            result["closed_sessions"].append({"session_id": session_id, "project_id": session_project_id})

        else:
            # Different project: warn only
            if session_project_id:
                # Get project name for nicer warning
                cursor.execute("SELECT name FROM projects WHERE id = ?", (session_project_id,))
                project_row = cursor.fetchone()
                project_name = project_row["name"] if project_row else session_project_id[:8]

                result["warnings"].append(
                    {
                        "session_id": session_id,
                        "project_id": session_project_id,
                        "project_name": project_name,
                        "message": f"Active session in project '{project_name}' - run 'empirica session-close' there",
                    }
                )

    db.conn.commit()
    return result


def _parse_session_config(args):
    """Parse session config from file/stdin or legacy CLI flags.

    Returns:
        dict with keys: ai_id, user_id, project_id, parent_session_id,
        output_format, config_data
    """
    import os

    from ..cli_utils import parse_json_safely

    config_data = None
    if hasattr(args, "config") and args.config:
        if args.config == "-":
            config_data = parse_json_safely(sys.stdin.read())
        else:
            if not os.path.exists(args.config):
                print(json.dumps({"ok": False, "error": f"Config file not found: {args.config}"}))
                sys.exit(1)
            with open(args.config) as f:
                config_data = parse_json_safely(f.read())

    if config_data:
        ai_id = config_data.get("ai_id")
        user_id = config_data.get("user_id")
        project_id = config_data.get("project_id")
        parent_session_id = config_data.get("parent_session_id")
        output_format = "json"

        if not ai_id:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "Config file must include 'ai_id' field",
                        "hint": "See /tmp/session_config_example.json for schema",
                    }
                )
            )
            sys.exit(1)
    else:
        ai_id = args.ai_id
        user_id = getattr(args, "user_id", None)
        project_id = getattr(args, "project_id", None)
        parent_session_id = getattr(args, "parent_session_id", None)
        output_format = getattr(args, "output", "json")

        if not ai_id:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "Legacy mode requires --ai-id flag",
                        "hint": "For AI-first mode, use: empirica session-create config.json",
                    }
                )
            )
            sys.exit(1)

    return {
        "ai_id": ai_id,
        "user_id": user_id,
        "project_id": project_id,
        "parent_session_id": parent_session_id,
        "output_format": output_format,
        "config_data": config_data,
    }


def _resolve_subject(config_data, args):
    """Auto-detect subject from config, args, or current directory."""
    from empirica.config.project_config_loader import get_current_subject

    subject = config_data.get("subject") if config_data else getattr(args, "subject", None)
    if subject is None:
        subject = get_current_subject()
    return subject


def _handle_auto_init(args, output_format, project_id):
    """Handle --auto-init flag: initialize .empirica/ if missing.

    Returns:
        (auto_init_performed, project_id, auto_init_project_path) —
        updated project_id if auto-init created one, and the project_path
        (git_root) of the just-created project so the caller can wire
        instance_projects/active_work without depending on the resolver
        chain (which can't yet see the brand-new project).
    """
    auto_init_performed = False
    auto_init_project_path = None
    if not getattr(args, "auto_init", False):
        return auto_init_performed, project_id, auto_init_project_path

    from empirica.config.path_resolver import get_git_root

    git_root = get_git_root()

    if not git_root:
        if output_format == "json":
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "Cannot auto-init: Not in a git repository",
                        "hint": "Run 'git init' first, then try again",
                    }
                )
            )
        else:
            print("❌ Cannot auto-init: Not in a git repository")
            print("   Run 'git init' first, then try again")
        sys.exit(1)

    # The just-created (or already-initialized) project lives at git_root.
    # Hand this back to the caller so it can wire instance_projects without
    # relying on the resolver chain that doesn't yet know about the new project.
    auto_init_project_path = str(git_root)

    # Data-loss guard (mesh-support/Philipp repro): NEVER clobber an existing
    # project.yaml. It holds the canonical project_id + type/domain/tenant/
    # calibration_weights and is gitignored (so it's the only copy). The old
    # guard checked config.yaml, but project-init OVERWRITES project.yaml — so a
    # project with project.yaml present but config.yaml absent (or a project_id
    # not yet in the local registry) got regenerated with defaults + a RANDOM
    # project_id, blowing away canonical identity. If project.yaml exists, REUSE
    # its project_id and skip re-init entirely.
    project_yaml = git_root / ".empirica" / "project.yaml"
    if project_yaml.exists():
        try:
            import yaml as _yaml

            existing = _yaml.safe_load(project_yaml.read_text()) or {}
            if isinstance(existing, dict) and existing.get("project_id"):
                project_id = existing["project_id"]
        except Exception:
            pass  # unreadable project.yaml → still never overwrite it
        return auto_init_performed, project_id, auto_init_project_path

    empirica_config = git_root / ".empirica" / "config.yaml"
    if not empirica_config.exists():
        if output_format != "json":
            print("🔧 Auto-initializing Empirica in this repository...")

        try:
            from types import SimpleNamespace

            from empirica.cli.command_handlers.project_init import handle_project_init_command

            init_args = SimpleNamespace(
                non_interactive=True,
                output="json" if output_format == "json" else "default",
                project_name=git_root.name,
                project_description=None,
                enable_beads=False,
                create_semantic_index=False,
                force=False,
            )

            result = handle_project_init_command(init_args)
            if result is None:
                if output_format != "json":
                    print("❌ Auto-init failed. Run 'empirica project-init' manually.")
                sys.exit(1)

            auto_init_performed = True
            project_id = result.get("project_id")

            if output_format != "json":
                print(f"✅ Project auto-initialized: {git_root.name}")
                print()

        except Exception as e:
            if output_format == "json":
                print(
                    json.dumps(
                        {"ok": False, "error": f"Auto-init failed: {e}", "hint": "Run 'empirica project-init' manually"}
                    )
                )
            else:
                print(f"❌ Auto-init failed: {e}")
                print("   Run 'empirica project-init' manually")
            sys.exit(1)

    return auto_init_performed, project_id, auto_init_project_path


def _require_project_initialized(ai_id, output_format):
    """Fail early if project not initialized in a git repo."""
    from empirica.config.path_resolver import get_git_root

    git_root = get_git_root()
    if git_root:
        empirica_config = git_root / ".empirica" / "config.yaml"
        if not empirica_config.exists():
            if output_format == "json":
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "error": "Project not initialized",
                            "hint": "Run 'empirica project-init' or use 'empirica session-create --auto-init'",
                            "git_root": str(git_root),
                        }
                    )
                )
            else:
                print(f"❌ Project not initialized in {git_root.name}")
                print("\nOptions:")
                print("  1. Initialize: empirica project-init")
                print(f"  2. Auto-init:  empirica session-create --ai-id {ai_id} --auto-init")
            sys.exit(1)


def _resolve_folder_name_to_uuid(folder_name):
    """Resolve folder_name to project UUID via workspace.db."""
    import os
    import sqlite3

    if not folder_name:
        return None
    workspace_db = os.path.join(os.path.expanduser("~"), ".empirica", "workspace", "workspace.db")
    if os.path.exists(workspace_db):
        conn = sqlite3.connect(workspace_db)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM global_projects WHERE name = ?", (folder_name,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return row[0]
    return None


def _get_project_id_from_context(context_data):
    """Extract project_id from context data, resolving folder_name or project_path."""
    import os

    # Priority 1: Direct project_id
    if context_data.get("project_id"):
        pid = context_data["project_id"]
        if _is_uuid_format(pid):
            return pid
        return _resolve_folder_name_to_uuid(pid)

    # Priority 2: folder_name (resolve via workspace.db)
    if context_data.get("folder_name"):
        resolved = _resolve_folder_name_to_uuid(context_data["folder_name"])
        if resolved:
            return resolved

    # Priority 3: project_path (query sessions.db, fallback to project.yaml)
    if context_data.get("project_path"):
        project_path = context_data["project_path"]
        db_project_id = R.project_id_from_db(project_path)
        if db_project_id:
            return db_project_id
        import yaml

        project_yaml = os.path.join(project_path, ".empirica", "project.yaml")
        if os.path.exists(project_yaml):
            try:
                with open(project_yaml) as f:
                    config = yaml.safe_load(f)
                    if config and config.get("project_id"):
                        return config["project_id"]
            except Exception:
                pass

    return None


def _resolve_from_context_files():
    """Resolve project_id from resolver context files (instance_projects, TTY, active_work).

    Returns project_id or None.
    """
    import json as _json
    import os

    # Priority 0a: instance_projects (instance-keyed)
    _sc_instance_id = R.instance_id()
    if _sc_instance_id:
        instance_file = os.path.join(
            os.path.expanduser("~"), ".empirica", "instance_projects", f"{_sc_instance_id}.json"
        )
        if os.path.exists(instance_file):
            with open(instance_file) as f:
                instance_data = _json.load(f)
                pid = _get_project_id_from_context(instance_data)
                if pid:
                    return pid

    # Priority 0b: TTY-specific active_work
    tty_session = R.tty_session(warn_if_stale=False)
    if tty_session:
        claude_session_id = tty_session.get("claude_session_id")
        if claude_session_id:
            active_work_path = os.path.join(
                os.path.expanduser("~"), ".empirica", f"active_work_{claude_session_id}.json"
            )
            if os.path.exists(active_work_path):
                with open(active_work_path) as f:
                    active_work = _json.load(f)
                    pid = _get_project_id_from_context(active_work)
                    if pid:
                        return pid

    # Priority 0c: canonical active_work.json
    canonical_path = os.path.join(os.path.expanduser("~"), ".empirica", "active_work.json")
    if os.path.exists(canonical_path):
        with open(canonical_path) as f:
            active_work = _json.load(f)
            return _get_project_id_from_context(active_work)

    return None


def _resolve_from_sessions_db():
    """Resolve project_id from sessions.db or project.yaml fallback.

    Returns project_id or None.
    """
    import os

    context_project = R.project_path()
    if not context_project:
        return None

    pid = R.project_id_from_db(context_project)
    if pid:
        return pid

    import yaml

    project_yaml = os.path.join(context_project, ".empirica", "project.yaml")
    if os.path.exists(project_yaml):
        try:
            with open(project_yaml) as f:
                project_config = yaml.safe_load(f)
                if project_config and project_config.get("project_id"):
                    return project_config["project_id"]
        except Exception:
            pass
    return None


def _resolve_from_git_remote():
    """Resolve project_id by matching git remote URL in sessions.db.

    Returns project_id or None.
    """
    import subprocess

    from empirica.data.session_database import SessionDatabase

    try:
        result = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            git_url = result.stdout.strip()
            db_temp = SessionDatabase()
            cursor = db_temp.conn.cursor()
            cursor.execute("SELECT id FROM projects WHERE repos LIKE ?", (f"%{git_url}%",))
            row = cursor.fetchone()
            db_temp.close()
            if row:
                return row["id"]
    except Exception:
        pass
    return None


def _resolve_early_project_id(project_id):
    """Resolve project_id through multiple strategies (context files, sessions.db, git remote).

    Returns the resolved early_project_id (UUID when possible).
    """
    early_project_id = project_id

    # Resolve folder_name to UUID if explicit --project-id was passed
    if early_project_id and not _is_uuid_format(early_project_id):
        resolved = _resolve_folder_name_to_uuid(early_project_id)
        if resolved:
            early_project_id = resolved

    if not early_project_id:
        # Method 0: Check resolver context files (highest priority)
        try:
            early_project_id = _resolve_from_context_files()
        except Exception:
            pass

        # Method 1: sessions.db (authoritative) or project.yaml (fallback)
        if not early_project_id:
            try:
                early_project_id = _resolve_from_sessions_db()
            except Exception:
                pass

        # Method 1b: Resolve folder_name to UUID via workspace.db
        if early_project_id and not _is_uuid_format(early_project_id):
            resolved = _resolve_folder_name_to_uuid(early_project_id)
            if resolved:
                early_project_id = resolved

        # Method 2: Match git remote URL
        if not early_project_id:
            early_project_id = _resolve_from_git_remote()

    return early_project_id


def _close_and_create_session(ai_id, early_project_id, subject, parent_session_id, output_format):
    """Auto-close previous sessions and create a new one.

    Returns:
        (session_id, close_result)
    """
    from empirica.data.session_database import SessionDatabase

    current_instance_id = R.instance_id()

    db = SessionDatabase()
    logger.info(f"session-create: using DB at {getattr(db, 'db_path', '?')}")
    close_result = auto_close_previous_sessions(db, ai_id, early_project_id, current_instance_id, output_format)

    if close_result["closed_sessions"] and output_format != "json":
        for closed in close_result["closed_sessions"]:
            print(f"🔄 Auto-closed previous session: {closed['session_id'][:8]}... (POSTFLIGHT submitted)")

    if close_result["warnings"] and output_format != "json":
        for warning in close_result["warnings"]:
            print(f"⚠️  {warning['message']}")

    session_id = db.create_session(
        ai_id=ai_id,
        components_loaded=6,
        subject=subject,
        parent_session_id=parent_session_id,
        project_id=early_project_id,
    )
    db.conn.commit()
    db.close()

    return session_id, close_result


def _write_active_session_file(session_id, ai_id):
    """Write active_session file for statusline (instance-specific, atomic write).

    Returns:
        dict on failure (error result), or None on success.
    """
    import os
    import tempfile
    from pathlib import Path

    instance_id = R.instance_id()
    instance_suffix = ""
    if instance_id:
        safe_instance = instance_id.replace(":", "_").replace("%", "")
        instance_suffix = f"_{safe_instance}"

    active_session_file = Path.home() / ".empirica" / f"active_session{instance_suffix}"
    active_session_file.parent.mkdir(parents=True, exist_ok=True)

    resolved_project_path = R.project_path()
    if not resolved_project_path:
        return {"ok": False, "error": "Cannot resolve project path. Run 'empirica project-switch <project>' first."}

    active_session_data = {"session_id": session_id, "project_path": resolved_project_path, "ai_id": ai_id}
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(active_session_file.parent))
    try:
        with os.fdopen(tmp_fd, "w") as tmp_f:
            tmp_f.write(json.dumps(active_session_data))
        os.replace(tmp_path, str(active_session_file))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return None


def _write_tty_session(session_id, project_path_override=None):
    """Write TTY session file for multi-instance isolation (best-effort).

    Args:
        session_id: The Empirica session UUID to bind to this terminal.
        project_path_override: When non-None, used as the project_path
            instead of the resolver chain. Set by --auto-init so the
            instance_projects file points at the just-created project
            (the resolver can't yet see it because instance_projects /
            active_work haven't been written by this point in the flow).
    """
    try:
        project_path = project_path_override or R.project_path()
        if project_path:
            R.tty_write(empirica_session_id=session_id, project_path=project_path)
    except Exception:
        pass


def _init_auto_capture(session_id, output_format):
    """Initialize auto-capture for this session."""
    from empirica.core.issue_capture import initialize_auto_capture, install_auto_capture_hooks

    try:
        auto_capture = initialize_auto_capture(session_id, enable=True)
        install_auto_capture_hooks(auto_capture)
        if output_format != "json":
            print("✅ Auto-capture enabled with logging hooks")
    except Exception as e:
        if output_format != "json":
            print(f"⚠️  Auto-capture initialization warning: {e}")


def _link_session_to_project(session_id, project_id, output_format):
    """Link session to project in database."""
    if not project_id:
        return

    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    cursor = db.conn.cursor()
    cursor.execute(
        """
        UPDATE sessions SET project_id = ? WHERE session_id = ?
    """,
        (project_id, session_id),
    )
    db.conn.commit()

    if output_format != "json":
        cursor.execute("SELECT name FROM projects WHERE id = ?", (project_id,))
        row = cursor.fetchone()
        if row:
            print(f"✅ Session linked to project: {row['name']}")
            print()

    db.close()


def _format_session_output(
    output_format, session_id, ai_id, user_id, project_id, parent_session_id, auto_init_performed, close_result
):
    """Format and print the final session creation output."""
    from empirica.data.session_database import SessionDatabase

    if output_format == "json":
        result = {
            "ok": True,
            "session_id": session_id,
            "ai_id": ai_id,
            "user_id": user_id,
            "project_id": project_id,
            "parent_session_id": parent_session_id,
            "auto_init_performed": auto_init_performed,
            "message": "Session created successfully",
            "lifecycle": {
                "auto_closed_sessions": close_result["closed_sessions"],
                "cross_project_warnings": close_result["warnings"],
            },
        }
        print(json.dumps(result, indent=2))
    else:
        print("✅ Session created successfully!")
        print(f"   📋 Session ID: {session_id}")
        print(f"   🤖 AI ID: {ai_id}")

        if project_id:
            print(f"   📁 Project: {project_id[:8]}...")
            print("\n📚 Project Context:")
            db = SessionDatabase()
            breadcrumbs = db.bootstrap_project_breadcrumbs(project_id, mode="session_start")
            db.close()

            if "error" not in breadcrumbs:
                project = breadcrumbs["project"]
                print(f"   Project: {project['name']}")
                print(f"   Description: {project['description']}")

                if breadcrumbs.get("findings"):
                    print("\n   Recent Findings (last 5):")
                    for finding in breadcrumbs["findings"][:5]:
                        print(f"     • {finding}")

                unresolved = [u for u in breadcrumbs.get("unknowns", []) if not u["is_resolved"]]
                if unresolved:
                    print("\n   Unresolved Unknowns:")
                    for u in unresolved[:3]:
                        print(f"     • {u['unknown']}")

                if breadcrumbs.get("available_skills"):
                    print("\n   Available Skills:")
                    for skill in breadcrumbs["available_skills"][:3]:
                        print(f"     • {skill['title']} ({', '.join(skill['tags'])})")

        print("\nNext steps:")
        print(f'   empirica preflight --session-id {session_id} --prompt "Your task"')


def handle_session_create_command(args):
    """Create a new session - AI-first with config file support"""
    try:
        # Stage 1: Parse config
        cfg = _parse_session_config(args)
        ai_id = cfg["ai_id"]
        user_id = cfg["user_id"]
        project_id = cfg["project_id"]
        parent_session_id = cfg["parent_session_id"]
        output_format = cfg["output_format"]
        config_data = cfg["config_data"]

        # Stage 2: Resolve subject
        subject = _resolve_subject(config_data, args)

        # Stage 3: Show project context (human mode)
        if output_format != "json":
            from empirica.cli.cli_utils import print_project_context

            print()
            print_project_context(quiet=False, verbose=False)
            print()

        # Stage 4: Auto-init if requested
        auto_init_performed, project_id, auto_init_project_path = _handle_auto_init(args, output_format, project_id)

        # Stage 5: Require project initialization
        _require_project_initialized(ai_id, output_format)

        # Stage 6: Resolve early project ID
        early_project_id = _resolve_early_project_id(project_id)

        # Stage 7: Close previous sessions and create new one
        session_id, close_result = _close_and_create_session(
            ai_id, early_project_id, subject, parent_session_id, output_format
        )

        # Stage 8: Write active session file
        error = _write_active_session_file(session_id, ai_id)
        if error:
            return error

        # Stage 9: Write TTY session
        # auto_init_project_path overrides the resolver when --auto-init just
        # created the project — without it, instance_projects never gets the
        # new project_path written and subsequent commands hit "Cannot resolve
        # project path" (ecodex prop_zwfsl26r7fc7ddj6oemkfcwa44).
        _write_tty_session(session_id, project_path_override=auto_init_project_path)

        # Stage 10: Initialize auto-capture
        _init_auto_capture(session_id, output_format)

        # Stage 11: Link session to project
        project_id = early_project_id
        _link_session_to_project(session_id, project_id, output_format)

        # Stage 12: Format output
        _format_session_output(
            output_format, session_id, ai_id, user_id, project_id, parent_session_id, auto_init_performed, close_result
        )

    except Exception as e:
        if getattr(args, "output", "default") == "json":
            print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        else:
            print(f"❌ Failed to create session: {e}")
        handle_cli_error(e, "Session create", getattr(args, "verbose", False))
        sys.exit(1)
