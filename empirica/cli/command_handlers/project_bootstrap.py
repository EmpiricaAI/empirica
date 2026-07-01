"""
Project Bootstrap Command - Epistemic context loading for AI sessions

Extracted from project_commands.py for maintainability.
This is the largest single command handler (~900 lines) as it handles:
- Project auto-detection (git repo, local config)
- Breadcrumb loading (findings, unknowns, dead-ends, mistakes)
- Epistemic state display (goals, flow metrics, health score)
- Multiple output formats (human, json)
"""

import json
import logging
import os

from ..cli_utils import handle_cli_error, safe_print

logger = logging.getLogger(__name__)


def _bootstrap_error_output(output_format: str, error_msg: str, hint: str | None = None):
    """Output error in appropriate format and return None."""
    if output_format == "json":
        result = {"ok": False, "error": error_msg}
        if hint:
            result["hint"] = hint
        safe_print(json.dumps(result))
    else:
        safe_print(f"❌ Error: {error_msg}")
        if hint:
            safe_print(f"\nTip: {hint}")
    return None


def _resolve_project_via_context():
    """Try to resolve project_id via unified context resolver (InstanceResolver).

    Returns project_id or None.
    """
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        active_project = R.project_path()
        if not active_project:
            return None

        # Load project_id from sessions.db (authoritative) or project.yaml (fallback)
        project_id = R.project_id_from_db(active_project)
        if project_id:
            return project_id

        # Fallback: project.yaml for fresh projects without sessions
        import yaml

        project_yaml = os.path.join(active_project, ".empirica", "project.yaml")
        if os.path.exists(project_yaml):
            with open(project_yaml) as f:
                project_config = yaml.safe_load(f)
                if project_config and project_config.get("project_id"):
                    return project_config["project_id"]
    except Exception:
        pass
    return None


def _resolve_project_via_git(output_format: str):
    """Try to resolve project_id via git remote URL.

    Returns (project_id, error_return) where error_return is the value to
    return from the main function if resolution failed with an error output,
    or None if resolution should continue.
    """
    import subprocess

    from empirica.data.session_database import SessionDatabase

    try:
        from empirica.cli.utils.project_resolver import (
            get_current_git_repo,
            resolve_project_by_git_repo,
        )

        git_repo = get_current_git_repo()
        if not git_repo:
            return None, _bootstrap_error_output(
                output_format,
                "Not in a git repository or no remote 'origin' configured",
                "Run 'git remote add origin <url>' or use --project-id",
            )

        # Use SessionDatabase() default - it uses unified context resolver
        db = SessionDatabase()
        project_id = resolve_project_by_git_repo(git_repo, db)

        if not project_id:
            # Fallback: try substring match for legacy projects
            result = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                git_url = result.stdout.strip()
                cursor = db.adapter.conn.cursor()
                cursor.execute(
                    """
                    SELECT id FROM projects WHERE repos LIKE ?
                    ORDER BY last_activity_timestamp DESC LIMIT 1
                """,
                    (f"%{git_url}%",),
                )
                row = cursor.fetchone()
                if row:
                    project_id = row["id"]

        db.close()

        if not project_id:
            return None, _bootstrap_error_output(
                output_format,
                f"No project found for git repo: {git_repo}",
                "Create a project with: empirica project-create --name <name>",
            )

        return project_id, None

    except Exception as e:
        return None, _bootstrap_error_output(
            output_format, f"Auto-detecting project failed: {e}", "Use --project-id to specify project explicitly"
        )


def _auto_detect_project_id(project_id_arg, output_format):
    """Auto-detect or resolve the project ID.

    Returns (project_id, error_return). If error_return is not None, the
    caller should return it immediately.
    """
    from empirica.cli.utils.project_resolver import resolve_project_id
    from empirica.data.session_database import SessionDatabase

    if project_id_arg:
        # Resolve project name to UUID if needed
        db = SessionDatabase()
        project_id = resolve_project_id(project_id_arg, db)
        db.close()
        return project_id, None

    # Method 1: Use unified context resolver (respects project-switch, active_work)
    # This is the AUTHORITATIVE source - handles multi-instance isolation
    project_id = _resolve_project_via_context()
    if project_id:
        return project_id, None

    # Method 2: Git remote URL (fallback for repos without project-init or no active context)
    return _resolve_project_via_git(output_format)


def _backfill_calibration_weights():
    """Backfill Tier 2 calibration weights for pre-existing projects.

    Projects created before this feature have no calibration_weights in project.yaml.
    Without them, every POSTFLIGHT falls back to _seed_calibration_weights() dynamically,
    which works but means the weights are never persisted or customizable.
    """
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        _proj_path = R.project_path()
        if not _proj_path:
            return

        from pathlib import Path

        _proj_yaml = Path(_proj_path) / ".empirica" / "project.yaml"
        if not _proj_yaml.exists():
            return

        import yaml

        with open(_proj_yaml) as _f:
            _proj_cfg = yaml.safe_load(_f) or {}

        if "calibration_weights" not in _proj_cfg:
            from .project_init import _seed_calibration_weights

            _ptype = _proj_cfg.get("type", "software")
            _proj_cfg["calibration_weights"] = _seed_calibration_weights(_ptype)
            with open(_proj_yaml, "w") as _f:
                yaml.dump(_proj_cfg, _f, default_flow_style=False, sort_keys=False)
            logger.info(f"Backfilled calibration_weights for project type '{_ptype}'")
    except Exception as e:
        logger.debug(f"Calibration weight backfill skipped: {e}")


def _sync_mesh_sharing_agreements() -> None:
    """Refresh workspace.db.entity_registry mirror of mesh-sharing-agreements
    from cortex. Non-fatal — any failure (no creds, cortex unreachable,
    transport error) downgrades to a debug log and the cached mirror keeps
    serving the visibility ladders check.

    Implements trigger #1 of the
    ``docs/architecture/MESH_SHARING_AGREEMENTS.md`` sync contract.
    Trigger #2 (ntfy push subscriber on ``<org>-mesh-sharing-changed``) is
    tracked separately under goal b22d506d task B.5.

    Cortex side LIVE since 45e1227 (2026-06-03). The check runs once per
    project-bootstrap — which fires on every session start — so the mirror
    refreshes at session-start cadence by default.
    """
    try:
        from empirica.config.credentials_loader import get_credentials_loader
        from empirica.core.mesh_sharing import sync_from_cortex
        from empirica.data.repositories.workspace_db import WorkspaceDBRepository

        creds = get_credentials_loader().get_cortex_config()
        url, key = creds.get("url"), creds.get("api_key")
        if not (url and key):
            logger.debug("mesh-agreements sync skipped — cortex creds missing")
            return

        with WorkspaceDBRepository.open(ensure_schema=True) as repo:
            result = sync_from_cortex(repo, url, key)
        if result.error:
            logger.debug(f"mesh-agreements bootstrap sync: {result.error}")
        else:
            logger.info(
                f"mesh-agreements bootstrap sync: {result.added} added, "
                f"{result.updated} updated, {result.marked_revoked} marked-revoked"
            )
    except Exception as e:
        logger.debug(f"mesh-agreements bootstrap sync skipped: {e}")


def _load_mco_config(session_id, ai_id):
    """Load MCO config from latest pre_summary snapshot or fresh from files.

    Returns mco_config dict or None.
    """
    from pathlib import Path

    from empirica.config.mco_loader import get_mco_config
    from empirica.utils.session_resolver import InstanceResolver as R

    context_project = R.project_path()
    project_base = Path(context_project) if context_project else Path.cwd()
    ref_docs_dir = project_base / ".empirica" / "ref-docs"
    if not ref_docs_dir.exists():
        return None

    snapshot_files = sorted(ref_docs_dir.glob("pre_summary_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not snapshot_files:
        return None

    latest_snapshot = snapshot_files[0]
    try:
        with open(latest_snapshot) as f:
            snapshot_data = json.load(f)
            mco_snapshot = snapshot_data.get("mco_config")

            mco_loader = get_mco_config()
            if mco_snapshot:
                return {
                    "source": "pre_summary_snapshot",
                    "snapshot_path": str(latest_snapshot),
                    "config": mco_snapshot,
                    "formatted": mco_loader.format_for_prompt(mco_snapshot),
                }
            else:
                # Fallback: Load fresh from files
                mco_snapshot = mco_loader.export_snapshot(
                    session_id=session_id or "unknown", ai_id=ai_id, cascade_style="default"
                )
                return {
                    "source": "mco_files_fallback",
                    "snapshot_path": None,
                    "config": mco_snapshot,
                    "formatted": mco_loader.format_for_prompt(mco_snapshot),
                }
    except Exception as e:
        logger.warning(f"Could not load MCO from snapshot: {e}")
        return None


def _retrieve_memories(task_description, project_id):
    """Retrieve eidetic and episodic memories from Qdrant based on task context.

    Returns (eidetic_memories, episodic_memories) tuple, either may be None.
    """
    if not task_description or not project_id:
        return None, None

    try:
        from empirica.core.qdrant.vector_store import _check_qdrant_available, search_eidetic, search_episodic

        if not _check_qdrant_available():
            return None, None

        eidetic_memories = None
        episodic_memories = None

        eidetic_results = search_eidetic(project_id, task_description, limit=5, min_confidence=0.5)
        if eidetic_results:
            eidetic_memories = {"query": task_description, "facts": eidetic_results, "count": len(eidetic_results)}

        episodic_results = search_episodic(project_id, task_description, limit=3, apply_recency_decay=True)
        if episodic_results:
            episodic_memories = {
                "query": task_description,
                "narratives": episodic_results,
                "count": len(episodic_results),
            }

        logger.debug(f"Memory retrieval: {len(eidetic_results or [])} eidetic, {len(episodic_results or [])} episodic")
        return eidetic_memories, episodic_memories
    except Exception as e:
        logger.debug(f"Memory retrieval failed (optional): {e}")
        return None, None


def _query_global_learnings(task_description):
    """Query global learnings for cross-project context from Qdrant.

    Returns global_learnings dict or None.

    This injection runs at EVERY bootstrap, so per-item text is truncated to a
    context budget (``EMPIRICA_PATTERN_MAX_ITEM_CHARS``, default 280) to keep
    the session-start context lean — the same budget #187 applied to pattern
    retrieval. Set ``EMPIRICA_PATTERN_BUDGET_OFF=1`` for the full untrimmed
    text. (The 5-item count cap is already enforced by ``limit=5``.)
    """
    try:
        import os

        from empirica.core.qdrant.vector_store import search_global

        global_results = search_global(task_description, limit=5)
        if not global_results:
            return None
        if os.getenv("EMPIRICA_PATTERN_BUDGET_OFF") != "1":
            from empirica.core.context_budget import truncate_text

            max_chars = int(os.getenv("EMPIRICA_PATTERN_MAX_ITEM_CHARS", "280"))
            for item in global_results:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    item["text"] = truncate_text(item["text"], max_chars)
        return {"query": task_description, "results": global_results, "count": len(global_results)}
    except Exception as e:
        logger.debug(f"Global learnings query failed (non-fatal): {e}")
        return None


def _reinstall_auto_capture_hooks(session_id):
    """Re-install auto-capture hooks for resumed/existing sessions."""
    try:
        from empirica.core.issue_capture import (
            get_auto_capture,
            initialize_auto_capture,
            install_auto_capture_hooks,
        )

        existing = get_auto_capture()
        if not existing:
            auto_capture = initialize_auto_capture(session_id, enable=True)
            install_auto_capture_hooks(auto_capture)
            logger.debug(f"Auto-capture hooks reinstalled for session {session_id[:8]}")
    except Exception as e:
        logger.debug(f"Auto-capture hook reinstall failed (non-fatal): {e}")


def _load_project_skills():
    """Load project skills from project_skills/*.yaml.

    Returns project_skills dict or None.
    """
    try:
        import yaml

        from empirica.utils.session_resolver import InstanceResolver as R

        context_project = R.project_path()
        base_path = context_project if context_project else os.getcwd()
        skills_dir = os.path.join(base_path, "project_skills")
        if not os.path.exists(skills_dir):
            return None

        skills_list = []
        for filename in os.listdir(skills_dir):
            if filename.endswith((".yaml", ".yml")):
                filepath = os.path.join(skills_dir, filename)
                try:
                    with open(filepath, encoding="utf-8") as f:
                        skill = yaml.safe_load(f)
                        if skill:
                            skills_list.append(skill)
                except Exception as skill_err:
                    logger.debug(f"Failed to load skill {filename}: {skill_err}")

        if skills_list:
            return {"count": len(skills_list), "skills": skills_list}
    except Exception as e:
        logger.debug(f"Project skills loading failed (non-fatal): {e}")
    return None


def handle_project_bootstrap_command(args):
    """Handle project-bootstrap command - show epistemic breadcrumbs"""
    try:
        from empirica.config.project_config_loader import get_current_subject
        from empirica.data.session_database import SessionDatabase

        output_format = getattr(args, "output", "human")
        project_id = getattr(args, "project_id", None)

        # Auto-detect project if not provided
        # Priority: 1) unified context resolver, 2) local .empirica/project.yaml, 3) git remote URL
        # CRITICAL: Never use CWD for database path - Claude Code resets CWD constantly
        # Use SessionDatabase() default which uses get_session_db_path() with unified context
        project_id, err = _auto_detect_project_id(project_id, output_format)
        if err is not None:
            return err

        check_integrity = False  # Disabled: naive parser has false positives. Use pattern matcher instead.
        context_to_inject = getattr(args, "context_to_inject", False)
        task_description = getattr(args, "task_description", None)

        # Parse epistemic_state from JSON string if provided
        epistemic_state = None
        epistemic_state_str = getattr(args, "epistemic_state", None)
        if epistemic_state_str:
            try:
                epistemic_state = json.loads(epistemic_state_str)
            except json.JSONDecodeError as e:
                safe_print(f"❌ Invalid JSON in --epistemic-state: {e}")
                return None

        # Auto-detect subject from current directory
        subject = getattr(args, "subject", None)
        if subject is None:
            subject = get_current_subject()  # Auto-detect from directory

        # Use SessionDatabase() default - it uses unified context resolver (NO CWD fallback)
        db = SessionDatabase()

        _backfill_calibration_weights()
        _sync_mesh_sharing_agreements()

        # Get new parameters
        session_id = getattr(args, "session_id", None)
        include_live_state = getattr(args, "include_live_state", False)
        # DEPRECATED: fresh_assess removed - use 'empirica assess-state' for canonical vector capture
        trigger = getattr(args, "trigger", None)
        depth = getattr(args, "depth", "auto")
        ai_id = getattr(args, "ai_id", None)  # Get AI ID for epistemic handoff

        # SessionStart Hook: Auto-load MCO config after memory compact
        mco_config = None
        if trigger == "post_compact":
            mco_config = _load_mco_config(session_id, ai_id)

        breadcrumbs = db.bootstrap_project_breadcrumbs(
            project_id,
            check_integrity=check_integrity,
            context_to_inject=context_to_inject,
            task_description=task_description,
            epistemic_state=epistemic_state,
            subject=subject,
            session_id=session_id,
            include_live_state=include_live_state,
            # fresh_assess removed - use 'empirica assess-state' for canonical vector capture
            trigger=trigger,
            depth=depth,
            ai_id=ai_id,  # Pass AI ID to bootstrap
        )

        # EIDETIC/EPISODIC MEMORY RETRIEVAL: Hot memories based on task context
        eidetic_memories, episodic_memories = _retrieve_memories(task_description, project_id)
        if eidetic_memories:
            breadcrumbs["eidetic_memories"] = eidetic_memories
        if episodic_memories:
            breadcrumbs["episodic_memories"] = episodic_memories

        # Add workflow suggestions based on session state
        session_id = getattr(args, "session_id", None)
        workflow_suggestions = None
        if session_id:
            from empirica.cli.utils.workflow_suggestions import get_workflow_suggestions

            workflow_suggestions = get_workflow_suggestions(project_id=project_id, session_id=session_id, db=db)

        # Optional: Query global learnings for cross-project context
        global_learnings = None
        include_global = getattr(args, "include_global", False)
        if include_global and task_description:
            global_learnings = _query_global_learnings(task_description)

        # Re-install auto-capture hooks for resumed/existing sessions
        if session_id:
            _reinstall_auto_capture_hooks(session_id)

        # Load project skills from project_skills/*.yaml
        project_skills = _load_project_skills()

        db.close()

        if "error" in breadcrumbs:
            safe_print(f"❌ {breadcrumbs['error']}")
            return None

        # Format output — delegated to project_bootstrap_formatter.py
        from .project_bootstrap_formatter import format_bootstrap_output

        format_bootstrap_output(
            args=args,
            project_id=project_id,
            breadcrumbs=breadcrumbs,
            db=db,
            mco_config=mco_config,
            workflow_suggestions=workflow_suggestions,
            global_learnings=global_learnings,
            project_skills=project_skills,
        )

        # Return None to avoid exit code issues and duplicate output
        return None

    except Exception as e:
        handle_cli_error(e, "Project bootstrap", getattr(args, "verbose", False))
        return None
