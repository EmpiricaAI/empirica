"""
Goal Commands - MCP v2 Integration Commands

Handles CLI commands for:
- goals-create: Create new goal
- goals-add-subtask: Add subtask to existing goal
- goals-complete-subtask: Mark subtask as complete
- goals-progress: Get goal completion progress
- goals-list: List goals
- sessions-resume: Resume previous sessions

These commands provide JSON output for MCP v2 server integration.
"""

import json
import logging
import os
import re
import subprocess
import sys
import time

from empirica.utils.session_resolver import InstanceResolver as R

from ..cli_utils import handle_cli_error, parse_json_safely

logger = logging.getLogger(__name__)


_CRITERION_RE = re.compile(
    r"^\s*(?P<method>[a-z_]+)"
    r"\s*:\s*(?P<metric>[A-Za-z0-9_\-./]+)"
    r"\s*@\s*(?P<op>>=|<=)"
    r"\s*(?P<threshold>-?\d+(?:\.\d+)?)\s*$"
)


def _parse_criterion_entry(entry):
    """Build a SuccessCriterion from a CLI entry.

    Supports three input forms:

    1. Bare string ("All subtasks complete") → completion criterion with
       no threshold. Backward-compatible default for users who don't care
       about the typed syntax.

    2. Typed expression "method:metric@op:threshold" — e.g.
         "quality_gate:prose_stylometry_adherence@<=0.25"
         "completion:subtask_ratio@>=0.9"
       Parsed into SuccessCriterion(validation_method=method,
       description=metric, threshold=threshold). The op is informational —
       the evaluator's direction inference handles comparison semantics.

    3. dict {description, validation_method, threshold, is_required} —
       passed through directly. Used by programmatic callers.

    Falls back to bare-string completion criterion on any parse failure.
    """
    import uuid as _uuid

    from empirica.core.goals.types import SuccessCriterion

    if isinstance(entry, dict):
        return SuccessCriterion(
            id=entry.get("id") or str(_uuid.uuid4()),
            description=str(entry.get("description", "")),
            validation_method=entry.get("validation_method", "completion"),
            threshold=entry.get("threshold"),
            is_required=entry.get("is_required", True),
            is_met=entry.get("is_met", False),
        )

    raw = str(entry)
    m = _CRITERION_RE.match(raw)
    if not m:
        return SuccessCriterion(
            id=str(_uuid.uuid4()),
            description=raw,
            validation_method="completion",
            is_required=True,
            is_met=False,
        )

    try:
        threshold = float(m.group("threshold"))
    except ValueError:
        return SuccessCriterion(
            id=str(_uuid.uuid4()),
            description=raw,
            validation_method="completion",
            is_required=True,
            is_met=False,
        )

    return SuccessCriterion(
        id=str(_uuid.uuid4()),
        description=m.group("metric"),
        validation_method=m.group("method"),
        threshold=threshold,
        is_required=True,
        is_met=False,
    )


def _check_for_similar_goals_helper(cursor, db, objective, session_id, similar, threshold):
    """Extracted from _check_for_similar_goals to reduce complexity."""
    if not similar:
        try:
            # Auto-detect project ID from session
            project_id = None
            try:
                from empirica.data.session_database import SessionDatabase
                db = SessionDatabase()
                cursor = db.conn.execute(
                    "SELECT project_id FROM sessions WHERE session_id = ?",
                    (session_id,)
                )
                row = cursor.fetchone()
                if row:
                    project_id = row[0]
                db.close()
            except Exception:
                pass

            if project_id:
                result = subprocess.run(
                    ['empirica', 'goals-search', objective[:100],
                     '--project-id', project_id, '--status', 'in_progress',
                     '--limit', '3', '--threshold', str(threshold), '--output', 'json'],
                    capture_output=True, text=True, timeout=10,
                    cwd=os.getcwd()
                )
                if result.returncode == 0:
                    search_result = json.loads(result.stdout)
                    for goal in search_result.get('results', []):
                        score = goal.get('score', 0)
                        if score >= threshold:
                            similar.append({
                                'goal_id': goal.get('id'),
                                'objective': goal.get('objective'),
                                'session_id': goal.get('session_id'),
                                'match_type': 'semantic',
                                'score': score
                            })
        except Exception as e:
            logger.debug(f"Semantic duplicate check failed: {e}")

def _check_for_similar_goals(objective: str, session_id: str | None = None, threshold: float = 0.85) -> list:
    """Check for similar existing goals using text matching and semantic search.

    Args:
        objective: The new goal's objective text
        session_id: Optional session ID for context
        threshold: Similarity threshold (0.0-1.0)

    Returns:
        List of similar goals found, empty if none
    """
    import re

    similar = []

    # Normalize objective text for comparison
    def normalize(text: str) -> str:
        """Normalize text for comparison by removing special characters and lowercasing."""
        return re.sub(r'[^\w\s]', '', text.lower().strip())

    normalized_objective = normalize(objective)

    # Strategy 1: Check database for exact/near-exact text matches
    try:
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase()
        cursor = db.conn.execute("""
            SELECT id, objective, session_id, is_completed, created_timestamp
            FROM goals
            WHERE is_completed = 0
            ORDER BY created_timestamp DESC
            LIMIT 50
        """)
        for row in cursor.fetchall():
            existing_obj = normalize(row[1] or '')
            # Check for exact match or high substring overlap
            if existing_obj == normalized_objective:
                similar.append({
                    'goal_id': row[0],
                    'objective': row[1],
                    'session_id': row[2],
                    'match_type': 'exact',
                    'score': 1.0
                })
            elif normalized_objective in existing_obj or existing_obj in normalized_objective:
                # Substring match - one contains the other
                similar.append({
                    'goal_id': row[0],
                    'objective': row[1],
                    'session_id': row[2],
                    'match_type': 'substring',
                    'score': 0.9
                })
        db.close()
    except Exception as e:
        logger.debug(f"Database duplicate check failed: {e}")

    # Strategy 2: Semantic search via Qdrant (if available)
    _check_for_similar_goals_helper(cursor, db, objective, session_id, similar, threshold)

    # Deduplicate by goal_id
    seen = set()
    unique = []
    for s in similar:
        if s['goal_id'] not in seen:
            seen.add(s['goal_id'])
            unique.append(s)

    return unique


def _link_goal_to_beads(args, config_data, goal, objective,
                        scope_breadth, scope_duration, output_format):
    """Create linked BEADS issue for a goal. Returns issue_id or None."""
    use_beads = getattr(args, 'use_beads', False) or (config_data and config_data.get('use_beads', False))
    if not use_beads and not hasattr(args, 'use_beads'):
        try:
            from empirica.config.project_config_loader import load_project_config
            project_config = load_project_config()
            if project_config and project_config.default_use_beads:
                use_beads = True
        except Exception:
            pass

    if not use_beads:
        return None

    try:
        from empirica.integrations.beads import BeadsAdapter
        beads = BeadsAdapter()
        if not beads.is_available():
            msg = (
                "⚠️  BEADS integration requested but 'bd' CLI not found.\n\n"
                "To use BEADS: pip install beads-project && bd init\n"
                "Or omit --use-beads to create goal without issue tracking."
            )
            logger.warning("BEADS not available")
            print(f"\n{msg}", file=sys.stderr)
            return None

        priority = 1 if scope_breadth > 0.7 else (2 if scope_breadth > 0.3 else 3)
        issue_type = "epic" if scope_breadth > 0.7 else "feature"
        issue_id = beads.create_issue(
            title=objective,
            description=f"Empirica Goal {goal.id[:8]}\nScope: breadth={scope_breadth:.2f}, duration={scope_duration:.2f}",
            priority=priority, issue_type=issue_type, labels=["empirica"])

        if issue_id:
            from empirica.data.session_database import SessionDatabase
            temp_db = SessionDatabase()
            temp_db.conn.execute("UPDATE goals SET beads_issue_id = ? WHERE id = ?", (issue_id, goal.id))
            temp_db.conn.commit()
            temp_db.close()
            logger.info(f"Linked goal {goal.id[:8]} to BEADS issue {issue_id}")
        return issue_id
    except Exception as e:
        logger.warning(f"BEADS integration failed: {e}")
        return None


def _parse_goal_config(args):
    """Parse goal config from file/stdin or legacy CLI flags.

    Returns:
        dict with keys: session_id, objective, scope_breadth, scope_duration,
        scope_coordination, success_criteria_list, estimated_complexity,
        constraints, metadata, output_format, config_data
    """
    import os

    config_data = None
    if hasattr(args, 'config') and args.config:
        if args.config == '-':
            config_data = parse_json_safely(sys.stdin.read())
        else:
            if not os.path.exists(args.config):
                print(json.dumps({"ok": False, "error": f"Config file not found: {args.config}"}))
                sys.exit(1)
            with open(args.config) as f:
                config_data = parse_json_safely(f.read())

    if config_data:
        session_id = config_data.get('session_id')
        objective = config_data.get('objective')
        description = config_data.get('description')

        scope_config = config_data.get('scope', {})
        if isinstance(scope_config, dict):
            scope_breadth = scope_config.get('breadth', 0.3)
            scope_duration = scope_config.get('duration', 0.2)
            scope_coordination = scope_config.get('coordination', 0.1)
        else:
            scope_breadth = 0.3
            scope_duration = 0.2
            scope_coordination = 0.1

        success_criteria_list = config_data.get('success_criteria', [])
        estimated_complexity = config_data.get('estimated_complexity')
        constraints = config_data.get('constraints')
        metadata = config_data.get('metadata')
        output_format = 'json'
    else:
        session_id = args.session_id
        objective = args.objective
        description = getattr(args, 'description', None)
        scope_breadth = float(args.scope_breadth) if hasattr(args, 'scope_breadth') and args.scope_breadth else 0.3
        scope_duration = float(args.scope_duration) if hasattr(args, 'scope_duration') and args.scope_duration else 0.2
        scope_coordination = float(args.scope_coordination) if hasattr(args, 'scope_coordination') and args.scope_coordination else 0.1
        estimated_complexity = getattr(args, 'estimated_complexity', None)
        constraints = parse_json_safely(args.constraints) if args.constraints else None
        metadata = parse_json_safely(args.metadata) if args.metadata else None
        output_format = getattr(args, 'output', 'json')

        success_criteria_list = _parse_legacy_success_criteria(args)

    return {
        'session_id': session_id,
        'objective': objective,
        'description': description,
        'scope_breadth': scope_breadth,
        'scope_duration': scope_duration,
        'scope_coordination': scope_coordination,
        'success_criteria_list': success_criteria_list,
        'estimated_complexity': estimated_complexity,
        'constraints': constraints,
        'metadata': metadata,
        'output_format': output_format,
        'config_data': config_data,
    }


def _parse_legacy_success_criteria(args):
    """Parse success criteria from legacy CLI flags (file, stdin, or inline).

    Returns:
        list of success criteria strings/dicts
    """
    import os

    success_criteria_list = []
    if hasattr(args, 'success_criteria_file') and args.success_criteria_file:
        if not os.path.exists(args.success_criteria_file):
            print(f"❌ Error: File not found: {args.success_criteria_file}", file=sys.stderr)
            sys.exit(1)
        with open(args.success_criteria_file) as f:
            success_criteria_list = parse_json_safely(f.read())
    elif hasattr(args, 'success_criteria') and args.success_criteria:
        if args.success_criteria == '-':
            success_criteria_list = parse_json_safely(sys.stdin.read())
        elif args.success_criteria.strip().startswith('['):
            success_criteria_list = parse_json_safely(args.success_criteria)
        else:
            success_criteria_list = [args.success_criteria]

    if isinstance(success_criteria_list, str):
        success_criteria_list = [success_criteria_list]

    return success_criteria_list


def _resolve_goal_session(session_id, config_data, args):
    """Resolve session_id, target_project_id, and validate required fields.

    Returns:
        (session_id, target_project_id, is_cross_project)
    Exits on validation failure.
    """
    target_project_id = None
    if config_data:
        target_project_id = config_data.get('project_id')
    elif hasattr(args, 'project_id') and args.project_id:
        target_project_id = args.project_id

    if not session_id:
        session_id = R.session_id()

    is_cross_project = bool(target_project_id)
    if not session_id and is_cross_project:
        session_id = "cross-project"

    return session_id, target_project_id, is_cross_project


def _check_goal_duplicates(objective, session_id, output_format, args, config_data):
    """Check for duplicate goals (unless --force). Exits if duplicates found."""
    force_create = getattr(args, 'force', False) or (config_data and config_data.get('force', False))
    if not force_create:
        similar_goals = _check_for_similar_goals(objective, session_id)
        if similar_goals:
            if output_format == 'json':
                print(json.dumps({
                    "ok": False,
                    "error": "Similar goal(s) already exist",
                    "similar_goals": similar_goals,
                    "hint": "Use --force to create anyway, or use goals-refresh to resume a stale goal",
                    "objective": objective
                }))
            else:
                print("⚠️  Similar goal(s) found:")
                for sg in similar_goals:
                    print(f"   - {sg['objective'][:60]}... (score: {sg.get('score', 'N/A')})")
                print("\n   Use --force to create anyway")
            sys.exit(1)


def _build_and_save_goal(objective, success_criteria_list, scope_breadth, scope_duration,
                         scope_coordination, estimated_complexity, constraints, metadata,
                         session_id, is_cross_project, target_project_id, config_data, args,
                         description=None):
    """Build Goal object and save to database.

    Returns:
        (goal, goal_repo, success_criteria_objects, scope, success, target_project_id)
    """
    from empirica.core.goals.repository import GoalRepository
    from empirica.core.goals.types import Goal, ScopeVector

    scope = ScopeVector(
        breadth=scope_breadth,
        duration=scope_duration,
        coordination=scope_coordination
    )

    if not success_criteria_list:
        success_criteria_list = ["Goal completion achieved"]

    # Resolve cross-project DB path
    goal_repo_db_path = None
    if is_cross_project and target_project_id:
        from empirica.cli.command_handlers.artifact_log_commands import _get_db_for_project
        cross_db = _get_db_for_project(target_project_id)
        if cross_db:
            resolved_pid = cross_db.resolve_project_id(target_project_id)
            if resolved_pid:
                target_project_id = resolved_pid
                goal_repo_db_path = cross_db.db_path
            cross_db.close()
    goal_repo = GoalRepository(db_path=goal_repo_db_path)

    # Create SuccessCriterion objects.
    # Each entry can be either:
    #   1. A bare string (e.g. "All subtasks complete") → completion criterion
    #   2. A typed expression "method:metric@op:threshold" e.g.
    #      "quality_gate:prose_stylometry_adherence@<=0.25"
    #      "completion:subtask_ratio@>=1.0"
    #   3. A pre-built dict {description, validation_method, threshold, ...}
    success_criteria_objects = []
    for entry in success_criteria_list:
        success_criteria_objects.append(_parse_criterion_entry(entry))

    # Create Goal object
    goal = Goal.create(
        objective=objective,
        success_criteria=success_criteria_objects,
        scope=scope,
        description=description,
        estimated_complexity=estimated_complexity,
        constraints=constraints,
        metadata=metadata
    )

    # Auto-derive transaction_id
    transaction_id = None
    try:
        transaction_id = R.transaction_id()
    except Exception:
        pass

    success = goal_repo.save_goal(goal, session_id, transaction_id=transaction_id)

    # Set initial status
    initial_status = 'in_progress'
    if config_data:
        initial_status = config_data.get('status', 'in_progress')
    elif hasattr(args, 'status') and args.status:
        initial_status = args.status
    if initial_status == 'planned' and success:
        goal_repo.db.conn.execute(
            "UPDATE goals SET status = 'planned' WHERE id = ?", (goal.id,))
        goal_repo.db.conn.commit()

    return goal, goal_repo, success_criteria_objects, scope, success, target_project_id


def _goal_post_create_integrations(goal, session_id, objective, scope, success_criteria_objects,
                                   scope_breadth, scope_duration, scope_coordination,
                                   estimated_complexity, constraints, metadata,
                                   args, config_data, output_format, result):
    """Run post-creation integrations: BEADS, git notes, Qdrant. Mutates result dict."""
    # BEADS
    beads_issue_id = _link_goal_to_beads(
        args, config_data, goal, objective,
        scope_breadth, scope_duration, output_format)
    result["beads_issue_id"] = beads_issue_id

    # CHECK recommendation for high-scope goals
    if scope_breadth >= 0.6 or scope_duration >= 0.5:
        result["check_recommendation"] = {
            "type": "check_recommendation",
            "reason": "high_scope",
            "message": "High-scope goal: Consider running CHECK after initial investigation",
            "scope_trigger": {
                "breadth": scope_breadth if scope_breadth >= 0.6 else None,
                "duration": scope_duration if scope_duration >= 0.5 else None
            },
            "suggested_timing": "after 1-2 subtasks or 30+ minutes",
            "command": f"empirica check --session-id {session_id}"
        }

    # Git notes storage
    try:
        from empirica.core.canonical.empirica_git import GitGoalStore

        ai_id = getattr(args, 'ai_id', 'empirica_cli')
        goal_store = GitGoalStore()
        goal_data = {
            'objective': objective,
            'scope': scope.to_dict(),
            'success_criteria': [sc.description for sc in success_criteria_objects],
            'estimated_complexity': estimated_complexity,
            'constraints': constraints,
            'metadata': metadata
        }
        goal_store.store_goal(
            goal_id=goal.id, session_id=session_id,
            ai_id=ai_id, goal_data=goal_data
        )
        logger.debug(f"Goal {goal.id[:8]} stored in git notes for cross-AI discovery")
    except Exception as e:
        logger.debug(f"Git goal storage skipped: {e}")

    # Qdrant embedding
    try:
        from empirica.core.qdrant.vector_store import embed_goal
        from empirica.data.session_database import SessionDatabase as GoalDB

        goal_db = GoalDB()
        cursor = goal_db.conn.cursor()
        cursor.execute("SELECT project_id, ai_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        goal_db.close()

        if row and row[0]:
            project_id = row[0]
            ai_id = row[1] or getattr(args, 'ai_id', 'empirica_cli')
            qdrant_embedded = embed_goal(
                project_id=project_id, goal_id=goal.id,
                objective=objective, description=goal.description,
                session_id=session_id, ai_id=ai_id,
                scope_breadth=scope_breadth, scope_duration=scope_duration,
                scope_coordination=scope_coordination,
                estimated_complexity=estimated_complexity,
                success_criteria=[sc.description for sc in success_criteria_objects],
                status="in_progress", timestamp=goal.created_timestamp,
            )
            if qdrant_embedded:
                result['qdrant_embedded'] = True
    except Exception as e:
        logger.debug(f"Goal Qdrant embedding skipped: {e}")

    return beads_issue_id


def _format_goal_output(output_format, result, objective, scope, estimated_complexity,
                        beads_issue_id, use_beads):
    """Format and print goal creation output."""
    if output_format == 'json':
        print(json.dumps(result, indent=2))
        if result['ok'] and not beads_issue_id and not use_beads:
            print("\n💡 Tip: Add --use-beads flag to track this goal in BEADS issue tracker", file=sys.stderr)
    else:
        if result['ok']:
            print("✅ Goal created successfully")
            print(f"   Goal ID: {result['goal_id']}")
            print(f"   Objective: {objective[:80]}..." if len(objective) > 80 else f"   Objective: {objective}")
            print(f"   Scope: breadth={scope.breadth}, duration={scope.duration}, coordination={scope.coordination}")
            if estimated_complexity:
                print(f"   Complexity: {estimated_complexity:.2f}")
            if beads_issue_id:
                print(f"   BEADS Issue: {beads_issue_id}")
            elif not use_beads:
                print("\n💡 Tip: Add --use-beads flag to track goals in BEADS issue tracker")
        else:
            print(f"❌ {result.get('message', 'Failed to create goal')}")


def handle_goals_create_command(args):
    """Handle goals-create command - AI-first with legacy flag support"""
    try:
        # Stage 1: Parse config
        cfg = _parse_goal_config(args)
        session_id = cfg['session_id']
        objective = cfg['objective']
        description = cfg.get('description')
        scope_breadth = cfg['scope_breadth']
        scope_duration = cfg['scope_duration']
        scope_coordination = cfg['scope_coordination']
        success_criteria_list = cfg['success_criteria_list']
        estimated_complexity = cfg['estimated_complexity']
        constraints = cfg['constraints']
        metadata = cfg['metadata']
        output_format = cfg['output_format']
        config_data = cfg['config_data']

        # Stage 2: Resolve session and project
        session_id, target_project_id, is_cross_project = _resolve_goal_session(
            session_id, config_data, args)

        # Validate required fields
        if not session_id or not objective:
            print(json.dumps({
                "ok": False,
                "error": "No active transaction and --session-id not provided",
                "hint": "Either run PREFLIGHT first, or provide --session-id explicitly"
            }))
            sys.exit(1)

        # Stage 3: Duplicate detection
        _check_goal_duplicates(objective, session_id, output_format, args, config_data)

        # Stage 4: Build and save goal
        goal, goal_repo, success_criteria_objects, scope, success, target_project_id = (
            _build_and_save_goal(
                objective, success_criteria_list, scope_breadth, scope_duration,
                scope_coordination, estimated_complexity, constraints, metadata,
                session_id, is_cross_project, target_project_id, config_data, args,
                description=description))

        # Stage 5: Post-creation integrations and result building
        use_beads = getattr(args, 'use_beads', False) or (config_data and config_data.get('use_beads', False))  # type: ignore[reportAttributeAccessIssue]
        beads_issue_id = None

        if success:
            result = {
                "ok": True,
                "goal_id": goal.id,
                "session_id": session_id,
                "message": "Goal created successfully",
                "objective": objective,
                "description": description,
                "scope": scope.to_dict(),
                "timestamp": goal.created_timestamp,
            }
            beads_issue_id = _goal_post_create_integrations(
                goal, session_id, objective, scope, success_criteria_objects,
                scope_breadth, scope_duration, scope_coordination,
                estimated_complexity, constraints, metadata,
                args, config_data, output_format, result)
        else:
            result = {
                "ok": False,
                "goal_id": None,
                "session_id": session_id,
                "message": "Failed to save goal to database",
                "objective": objective,
                "scope": scope.to_dict()
            }

        # Stage 6: Format output
        _format_goal_output(output_format, result, objective, scope,
                            estimated_complexity, beads_issue_id, use_beads)

        goal_repo.close()
        return None

    except Exception as e:
        handle_cli_error(e, "Create goal", getattr(args, 'verbose', False))


def handle_goals_add_subtask_command(args):
    """Handle goals-add-subtask command"""
    try:

        from empirica.core.goals.repository import GoalRepository
        from empirica.core.tasks.repository import TaskRepository
        from empirica.core.tasks.types import EpistemicImportance, SubTask

        # Parse arguments
        goal_id = args.goal_id
        description = args.description
        importance = EpistemicImportance[args.importance.upper()] if args.importance else EpistemicImportance.MEDIUM
        dependencies = parse_json_safely(args.dependencies) if args.dependencies else []
        estimated_tokens = getattr(args, 'estimated_tokens', None)

        # Resolve short ID prefix to full ID
        goal_repo = GoalRepository()
        goal = goal_repo.get_goal(goal_id)
        if not goal:
            goal_repo.close()
            result = {"ok": False, "error": f"Goal not found: {goal_id}"}
            if hasattr(args, 'output') and args.output == 'json':
                print(json.dumps(result))
            else:
                print(f"❌ Goal not found: {goal_id}")
            return result
        resolved_goal_id = goal.id
        goal_repo.close()

        # Use the real Task repository
        task_repo = TaskRepository()

        # Create real SubTask object
        subtask = SubTask.create(
            goal_id=resolved_goal_id,
            description=description,
            epistemic_importance=importance,
            dependencies=dependencies,
            estimated_tokens=estimated_tokens
        )

        # Save to database
        success = task_repo.save_subtask(subtask)

        if success:
            # BEADS Integration (Optional): Create child issue with dependency
            beads_subtask_id = None
            use_beads = getattr(args, 'use_beads', False)

            if use_beads:
                try:
                    from empirica.data.session_database import SessionDatabase
                    from empirica.integrations.beads import BeadsAdapter

                    # Get parent goal's BEADS ID
                    db = SessionDatabase()
                    cursor = db.conn.execute(
                        "SELECT beads_issue_id FROM goals WHERE id = ?",
                        (goal_id,)
                    )
                    row = cursor.fetchone()
                    parent_beads_id = row[0] if row and row[0] else None

                    if parent_beads_id:
                        beads = BeadsAdapter()
                        if beads.is_available():
                            # Map importance to BEADS priority
                            priority_map = {
                                EpistemicImportance.CRITICAL: 1,
                                EpistemicImportance.HIGH: 1,
                                EpistemicImportance.MEDIUM: 2,
                                EpistemicImportance.LOW: 3
                            }
                            priority = priority_map.get(importance, 2)

                            # Create BEADS child issue (gets hierarchical ID like bd-a1b2.1)
                            beads_subtask_id = beads.create_issue(
                                title=description,
                                description=f"Empirica Subtask {subtask.id[:8]}\nParent Goal: {goal_id[:8]}",
                                priority=priority,
                                issue_type="task",
                                labels=["empirica", "subtask"]
                            )

                            if beads_subtask_id:
                                # Add dependency: subtask blocks parent
                                beads.add_dependency(
                                    child_id=beads_subtask_id,
                                    parent_id=parent_beads_id,
                                    dep_type='blocks'
                                )

                                # Store BEADS link in subtask_data
                                db.conn.execute("""
                                    UPDATE subtasks
                                    SET subtask_data = json_set(subtask_data, '$.beads_issue_id', ?)
                                    WHERE id = ?
                                """, (beads_subtask_id, subtask.id))
                                db.conn.commit()
                                logger.info(f"Linked subtask {subtask.id[:8]} to BEADS issue {beads_subtask_id}")
                    else:
                        logger.warning("Parent goal has no BEADS issue - cannot create linked subtask")

                    db.close()
                except Exception as e:
                    logger.warning(f"BEADS subtask integration failed: {e}")
                    # Continue without BEADS - it's optional

            # Qdrant embedding (safe degradation)
            try:
                from empirica.core.qdrant.vector_store import embed_subtask
                from empirica.data.session_database import SessionDatabase as SubtaskDB

                # Get goal's session and project info
                st_db = SubtaskDB()
                cursor = st_db.conn.execute("""
                    SELECT g.objective, g.session_id, s.project_id, s.ai_id
                    FROM goals g
                    LEFT JOIN sessions s ON g.session_id = s.session_id
                    WHERE g.id = ?
                """, (goal_id,))
                row = cursor.fetchone()
                st_db.close()

                if row:
                    goal_objective, session_id, project_id, ai_id = row
                    if project_id:
                        embed_subtask(
                            project_id=project_id,
                            subtask_id=subtask.id,
                            description=description,
                            goal_id=goal_id,
                            goal_objective=goal_objective,
                            session_id=session_id,
                            ai_id=ai_id,
                            epistemic_importance=importance.value,
                            status=subtask.status.value,
                            timestamp=subtask.created_timestamp,
                        )
            except Exception as e:
                logger.debug(f"Subtask Qdrant embedding skipped: {e}")

            result = {
                "ok": True,
                "task_id": subtask.id,
                "goal_id": goal_id,
                "message": "Subtask added successfully",
                "description": description,
                "importance": importance.value,
                "status": subtask.status.value,
                "timestamp": subtask.created_timestamp,
                "beads_issue_id": beads_subtask_id  # Include BEADS link
            }
        else:
            result = {
                "ok": False,
                "task_id": None,
                "goal_id": goal_id,
                "message": "Failed to save subtask to database",
                "description": description,
                "importance": importance.value
            }

        # Format output
        if hasattr(args, 'output') and args.output == 'json':
            print(json.dumps(result, indent=2))
        else:
            print("✅ Subtask added successfully")
            print(f"   Task ID: {result['task_id']}")
            print(f"   Goal: {goal_id[:8]}...")
            print(f"   Description: {description[:80]}...")
            print(f"   Importance: {importance}")
            if estimated_tokens:
                print(f"   Estimated tokens: {estimated_tokens}")

        task_repo.close()
        # Return None to avoid exit code issues and duplicate output
        return None

    except Exception as e:
        handle_cli_error(e, "Add subtask", getattr(args, 'verbose', False))


def handle_goals_add_dependency_command(args):
    """Handle goals-add-dependency command - add goal-to-goal dependency"""
    try:
        import uuid

        from empirica.data.session_database import SessionDatabase

        # Parse arguments
        goal_id = args.goal_id
        depends_on_goal_id = args.depends_on
        dependency_type = getattr(args, 'type', 'blocks')
        description = getattr(args, 'description', None)
        output_format = getattr(args, 'output', 'human')

        db = SessionDatabase()
        cursor = db.conn.cursor()

        # Verify both goals exist
        cursor.execute("SELECT id, objective FROM goals WHERE id = ?", (goal_id,))
        goal_row = cursor.fetchone()
        if not goal_row:
            result = {
                "ok": False,
                "error": f"Goal not found: {goal_id}",
                "hint": "Use 'empirica goals-list-all' to see available goals"
            }
            print(json.dumps(result, indent=2) if output_format == 'json' else f"Error: {result['error']}")
            db.close()
            return 1

        cursor.execute("SELECT id, objective FROM goals WHERE id = ?", (depends_on_goal_id,))
        depends_row = cursor.fetchone()
        if not depends_row:
            result = {
                "ok": False,
                "error": f"Dependency goal not found: {depends_on_goal_id}",
                "hint": "Use 'empirica goals-list-all' to see available goals"
            }
            print(json.dumps(result, indent=2) if output_format == 'json' else f"Error: {result['error']}")
            db.close()
            return 1

        # Check for circular dependency (simple check: A depends on B, B depends on A)
        cursor.execute("""
            SELECT id FROM goal_dependencies
            WHERE goal_id = ? AND depends_on_goal_id = ?
        """, (depends_on_goal_id, goal_id))
        if cursor.fetchone():
            result = {
                "ok": False,
                "error": "Circular dependency detected",
                "detail": f"Goal {depends_on_goal_id[:8]}... already depends on {goal_id[:8]}..."
            }
            print(json.dumps(result, indent=2) if output_format == 'json' else f"Error: {result['error']}")
            db.close()
            return 1

        # Check if dependency already exists
        cursor.execute("""
            SELECT id FROM goal_dependencies
            WHERE goal_id = ? AND depends_on_goal_id = ?
        """, (goal_id, depends_on_goal_id))
        if cursor.fetchone():
            result = {
                "ok": False,
                "error": "Dependency already exists",
                "goal_id": goal_id,
                "depends_on": depends_on_goal_id
            }
            print(json.dumps(result, indent=2) if output_format == 'json' else f"Error: {result['error']}")
            db.close()
            return 1

        # Insert dependency
        dep_id = str(uuid.uuid4())
        cursor.execute("""
            INSERT INTO goal_dependencies (id, goal_id, depends_on_goal_id, dependency_type, description)
            VALUES (?, ?, ?, ?, ?)
        """, (dep_id, goal_id, depends_on_goal_id, dependency_type, description))
        db.conn.commit()

        result = {
            "ok": True,
            "dependency_id": dep_id,
            "goal_id": goal_id,
            "goal_objective": goal_row[1][:50] + "..." if len(goal_row[1]) > 50 else goal_row[1],
            "depends_on": depends_on_goal_id,
            "depends_on_objective": depends_row[1][:50] + "..." if len(depends_row[1]) > 50 else depends_row[1],
            "type": dependency_type,
            "description": description,
            "message": f"Dependency added: {goal_id[:8]}... {dependency_type} {depends_on_goal_id[:8]}..."
        }

        if output_format == 'json':
            print(json.dumps(result, indent=2))
        else:
            type_labels = {
                'blocks': 'is blocked by',
                'informs': 'is informed by',
                'extends': 'extends'
            }
            print("Goal dependency added")
            print(f"  {result['goal_objective']}")
            print(f"    {type_labels.get(dependency_type, dependency_type)}")
            print(f"  {result['depends_on_objective']}")
            if description:
                print(f"  Reason: {description}")

        db.close()
        return None

    except Exception as e:
        handle_cli_error(e, "Add goal dependency", getattr(args, 'verbose', False))


def handle_goals_complete_subtask_command(args):
    """Handle goals-complete-subtask command"""
    try:
        from empirica.core.tasks.repository import TaskRepository
        from empirica.core.tasks.types import TaskStatus

        # Parse arguments with backward compatibility
        # Priority: subtask_id (new) > task_id (deprecated)
        if hasattr(args, 'subtask_id') and args.subtask_id:
            task_id = args.subtask_id
            if hasattr(args, 'task_id') and args.task_id and args.task_id != args.subtask_id:
                print("⚠️  Warning: Both --subtask-id and --task-id provided. Using --subtask-id.", file=sys.stderr)
        elif hasattr(args, 'task_id') and args.task_id:
            task_id = args.task_id
            print("ℹ️  Note: --task-id is deprecated. Please use --subtask-id instead.", file=sys.stderr)
        else:
            print(json.dumps({
                "ok": False,
                "error": "Either --subtask-id or --task-id is required",
                "hint": "Preferred: empirica goals-complete-subtask --subtask-id <ID>"
            }))
            sys.exit(1)

        evidence = args.evidence

        # Use the Task repository
        task_repo = TaskRepository()

        # Complete the subtask in database
        success = task_repo.update_subtask_status(task_id, TaskStatus.COMPLETED, evidence)

        if success:
            result = {
                "ok": True,
                "task_id": task_id,
                "message": "Subtask marked as complete",
                "evidence": evidence,
                "timestamp": time.time()
            }
        else:
            result = {
                "ok": False,
                "task_id": task_id,
                "error": "subtask_not_found",
                "message": f"No subtask matches id '{task_id}' (full UUID or unambiguous prefix required)",
                "hint": "Verify with: empirica goals-get-subtasks --goal-id <goal-id>"
            }

        # Format output
        if hasattr(args, 'output') and args.output == 'json':
            print(json.dumps(result, indent=2))
        elif success:
            print("✅ Subtask marked as complete")
            print(f"   Task ID: {task_id}")
            if evidence:
                print(f"   Evidence: {evidence[:80]}...")
        else:
            print(f"❌ {result['message']}", file=sys.stderr)
            print(f"   {result['hint']}", file=sys.stderr)

        task_repo.close()
        if not success:
            sys.exit(1)
        return None

    except Exception as e:
        handle_cli_error(e, "Complete subtask", getattr(args, 'verbose', False))


def handle_goals_progress_command(args):
    """Handle goals-progress command"""
    try:
        from empirica.core.goals.repository import GoalRepository
        from empirica.core.tasks.repository import TaskRepository

        # Parse arguments
        goal_id = args.goal_id

        # Use the repositories to get real data
        goal_repo = GoalRepository()
        task_repo = TaskRepository()

        # Get the goal (supports short ID prefix matching)
        goal = goal_repo.get_goal(goal_id)
        if not goal:
            result = {
                "ok": False,
                "goal_id": goal_id,
                "message": "Goal not found (check ID or use longer prefix if ambiguous)",
                "timestamp": time.time()
            }
        else:
            # Get all subtasks using the resolved full goal ID
            subtasks = task_repo.get_goal_subtasks(goal.id)

            # Calculate real progress
            total_subtasks = len(subtasks)
            completed_subtasks = sum(1 for task in subtasks if task.status.value == "completed")
            completion_percentage = (completed_subtasks / total_subtasks * 100) if total_subtasks > 0 else 0.0

            result = {
                "ok": True,
                "goal_id": goal_id,
                "message": "Progress retrieved successfully",
                "completion_percentage": completion_percentage,
                "total_subtasks": total_subtasks,
                "completed_subtasks": completed_subtasks,
                "remaining_subtasks": total_subtasks - completed_subtasks,
                "timestamp": time.time()
            }

        # Format output
        if hasattr(args, 'output') and args.output == 'json':
            print(json.dumps(result, indent=2))
        else:
            if result.get('ok'):
                print("✅ Goal progress retrieved")
                print(f"   Goal: {goal_id[:8]}...")
                print(f"   Completion: {result['completion_percentage']:.1f}%")
                print(f"   Progress: {result['completed_subtasks']}/{result['total_subtasks']} subtasks")
                print(f"   Remaining: {result['remaining_subtasks']} subtasks")
            else:
                print(f"❌ {result.get('message', 'Error retrieving goal progress')}")
                print(f"   Goal ID: {goal_id}")

        goal_repo.close()
        # Return None to avoid exit code issues and duplicate output
        return None

    except Exception as e:
        handle_cli_error(e, "Get goal progress", getattr(args, 'verbose', False))




_DRIFT_PREDICATE = (
    "((g.status = 'completed' AND g.is_completed = 0) "
    "OR (g.status IN ('in_progress','planned') AND g.is_completed = 1))"
)


def _count_goal_drift(cursor, project_id, status_filter, show_completed) -> int:
    """Return drift count for the project, or 0 if not applicable to this view."""
    if not project_id or show_completed or status_filter in ('completed', 'drift', 'all'):
        return 0
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM goals WHERE project_id = ? "
            "AND ((status = 'completed' AND is_completed = 0) "
            "OR (status IN ('in_progress','planned') AND is_completed = 1))",
            (project_id,),
        )
        return cursor.fetchone()[0]
    except Exception:
        return 0


def _build_goals_status_filter(status_filter, show_completed):
    """Return (sql_fragment, extra_params) for the chosen status filter."""
    if status_filter == 'all':
        return "", []
    if status_filter == 'completed':
        return " AND g.is_completed = 1", []
    if status_filter == 'drift':
        return f" AND {_DRIFT_PREDICATE}", []
    if status_filter in ('in_progress', 'planned'):
        return " AND g.status = ? AND g.is_completed = 0", [status_filter]
    if show_completed:
        return " AND g.is_completed = 1", []
    return " AND g.is_completed = 0", []


def _handle_goals_list_command_helper(cursor, project_id, session_id):
    """Auto-derive project_id from session/context when not explicitly supplied.

    Returns the resolved project_id (caller must assign — strings don't pass by reference).
    Priority: explicit --project-id → --session-id → unified context resolver.
    """
    if project_id:
        return project_id
    # Priority 1: From session_id if provided
    if session_id:
        cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if row and row[0]:
            return row[0]

    # Priority 2: From unified context resolver (transaction → active_work)
    try:
        context = R.context()
        ctx_session = context.get('empirica_session_id')
        if ctx_session:
            cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (ctx_session,))
            row = cursor.fetchone()
            if row and row[0]:
                return row[0]
    except Exception:
        pass
    return None

def handle_goals_list_command(args):
    """Handle goals-list command - list goals with optional filters

    Scoping:
    - Goals are PROJECT-SCOPED (structural), not session-scoped (temporal)
    - --project-id: filter by project (primary filter)
    - --session-id: derives project_id from session (convenience)
    - --ai-id: filter by AI that created the goal
    - --completed: show completed goals instead of active

    Session_id on goals is metadata about creation context, not a filter boundary.
    """
    try:
        from empirica.data.session_database import SessionDatabase

        # Parse arguments
        session_id = getattr(args, 'session_id', None)
        project_id = getattr(args, 'project_id', None)
        transaction_id = getattr(args, 'transaction_id', None)
        ai_id = getattr(args, 'ai_id', None)
        show_completed = getattr(args, 'completed', False)
        status_filter = getattr(args, 'status', None)  # planned|in_progress|completed|all
        output_format = getattr(args, 'output', 'human')
        limit = getattr(args, 'limit', 20) if hasattr(args, 'limit') else 20

        db = SessionDatabase()
        cursor = db.conn.cursor()

        # GOALS ARE PROJECT-SCOPED: Auto-derive project_id from context if not provided
        project_id = _handle_goals_list_command_helper(cursor, project_id, session_id)

        # Build query based on filters
        base_query = """
            SELECT g.id, g.objective, g.status, g.is_completed,
                   g.created_timestamp, g.session_id, s.ai_id,
                   (SELECT COUNT(*) FROM subtasks WHERE goal_id = g.id) as total_subtasks,
                   (SELECT COUNT(*) FROM subtasks WHERE goal_id = g.id AND status = 'completed') as completed_subtasks
            FROM goals g
            LEFT JOIN sessions s ON g.session_id = s.session_id
            WHERE 1=1
        """
        params = []

        # Apply filters - PROJECT is the primary structural scope, TRANSACTION is measurement scope
        if project_id:
            base_query += " AND g.project_id = ?"
            params.append(project_id)

        if transaction_id:
            base_query += " AND g.transaction_id = ?"
            params.append(transaction_id)

        if ai_id:
            base_query += " AND s.ai_id = ?"
            params.append(ai_id)

        # Filter by completion status. is_completed BOOLEAN is canonical; status
        # text has historical drift. Helper keeps complexity down.
        status_sql, status_params = _build_goals_status_filter(status_filter, show_completed)
        base_query += status_sql
        params.extend(status_params)

        base_query += " ORDER BY g.created_timestamp DESC LIMIT ?"
        params.append(limit)

        cursor.execute(base_query, params)
        rows = cursor.fetchall()

        drift_count = _count_goal_drift(cursor, project_id, status_filter, show_completed)

        # Build results
        # Row: 0=id, 1=objective, 2=status, 3=is_completed, 4=created, 5=session_id, 6=ai_id, 7=total, 8=completed
        goals = []
        for row in rows:
            total = row[7] or 0
            completed = row[8] or 0
            progress_pct = (completed / total * 100) if total > 0 else 0.0

            goals.append({
                "goal_id": row[0],
                "objective": row[1],
                "status": row[2],
                "is_completed": bool(row[3]),
                "created_at": row[4],
                "session_id": row[5],
                "ai_id": row[6],
                "progress": f"{completed}/{total}",
                "progress_pct": progress_pct
            })

        db.close()

        # Build filter description for output
        filters_applied = []
        if project_id:
            filters_applied.append(f"project={project_id[:8]}...")
        if ai_id:
            filters_applied.append(f"ai={ai_id}")
        filter_desc = ", ".join(filters_applied) if filters_applied else "all"
        status_desc = "completed" if show_completed else "active"

        result = {
            "ok": True,
            "goals_count": len(goals),
            "goals": goals,
            "filters": {
                "project_id": project_id,
                "session_id": session_id,  # Keep for reference (used to derive project)
                "ai_id": ai_id,
                "status": status_desc
            },
            "timestamp": time.time()
        }
        if drift_count:
            result["drift_count"] = drift_count
            result["drift_hint"] = (
                f"{drift_count} goal(s) have status/is_completed mismatch — "
                "run `empirica goals-list --status drift` to inspect."
            )

        if output_format == 'json':
            # Return result - CLI core will print as JSON
            return result
        else:
            # Human format - print here and return None so CLI core doesn't double-print
            print(f"{'=' * 70}")
            print(f"🎯 GOALS ({status_desc.upper()}) - {len(goals)} found [{filter_desc}]")
            print(f"{'=' * 70}")
            print()

            if not goals:
                print("   (No goals found)")
            else:
                for i, g in enumerate(goals, 1):
                    status_emoji = "✅" if g['is_completed'] else ("🔄" if g['progress'] != "0/0" else "⏳")
                    print(f"{status_emoji} {i}. {g['objective'][:65]}")
                    ai_info = f" | AI: {g['ai_id']}" if g['ai_id'] else ""
                    print(f"   ID: {g['goal_id'][:8]}... | Progress: {g['progress']} ({g['progress_pct']:.0f}%){ai_info}")
                    print()

            return None  # Prevents CLI core from printing dict items

    except Exception as e:
        handle_cli_error(e, "List goals", getattr(args, 'verbose', False))


def handle_goals_get_subtasks_command(args):
    """Handle goals-get-subtasks command - get detailed subtask information"""
    try:
        from empirica.core.goals.repository import GoalRepository
        from empirica.core.tasks.repository import TaskRepository

        # Parse arguments
        goal_id = args.goal_id

        # Resolve short ID prefix to full ID first
        goal_repo = GoalRepository()
        goal = goal_repo.get_goal(goal_id)
        resolved_goal_id = goal.id if goal else goal_id
        goal_repo.close()

        # Use task repository to get subtasks with resolved ID
        task_repo = TaskRepository()
        subtasks = task_repo.get_goal_subtasks(resolved_goal_id)

        if not subtasks:
            result = {
                "ok": False,
                "goal_id": goal_id,
                "message": "No subtasks found for goal",
                "subtasks": [],
                "timestamp": time.time()
            }
        else:
            # Convert subtasks to dict format
            subtasks_dict = []
            for task in subtasks:
                subtasks_dict.append({
                    "task_id": task.id,
                    "description": task.description,
                    "status": task.status.value,
                    "importance": task.epistemic_importance.value,
                    "created_at": task.created_timestamp,
                    "completed_at": task.completed_timestamp if hasattr(task, 'completed_timestamp') else None,
                    "dependencies": task.dependencies if hasattr(task, 'dependencies') else [],
                    "estimated_tokens": task.estimated_tokens if hasattr(task, 'estimated_tokens') else None,
                    "actual_tokens": task.actual_tokens if hasattr(task, 'actual_tokens') else None,
                    "completion_evidence": task.completion_evidence if hasattr(task, 'completion_evidence') else None,
                    "notes": task.notes if hasattr(task, 'notes') else "",
                    "findings": task.findings if hasattr(task, 'findings') else [],
                    "unknowns": task.unknowns if hasattr(task, 'unknowns') else [],
                    "dead_ends": task.dead_ends if hasattr(task, 'dead_ends') else []
                })

            completed_count = sum(1 for t in subtasks if t.status.value == "completed")

            result = {
                "ok": True,
                "goal_id": goal_id,
                "message": "Subtasks retrieved successfully",
                "subtasks_count": len(subtasks),
                "completed_count": completed_count,
                "in_progress_count": len(subtasks) - completed_count,
                "subtasks": subtasks_dict,
                "timestamp": time.time()
            }

        # Format output
        if hasattr(args, 'output') and args.output == 'json':
            print(json.dumps(result, indent=2))
        else:
            if result.get('ok'):
                print(f"✅ Found {result['subtasks_count']} subtask(s) for goal {goal_id[:8]}...")
                print(f"   Progress: {result['completed_count']}/{result['subtasks_count']} completed")
                print()
                for i, task in enumerate(result['subtasks'], 1):
                    status_icon = "✅" if task['status'] == "completed" else "⏳"
                    print(f"{status_icon} {i}. {task['description']}")
                    print(f"   Status: {task['status']} | Importance: {task.get('importance', 'medium')}")
                    print(f"   Task ID: {task['task_id'][:8]}...")
                    if task.get('findings'):
                        print(f"   Findings: {len(task['findings'])} discovered")
                    if task.get('unknowns'):
                        print(f"   Unknowns: {len(task['unknowns'])} remaining")
                    if task.get('dead_ends'):
                        print(f"   Dead ends: {len(task['dead_ends'])} avoided")
            else:
                print(f"❌ {result.get('message', 'Error retrieving subtasks')}")

        task_repo.close()
        # Return None to avoid exit code issues and duplicate output
        return None

    except Exception as e:
        handle_cli_error(e, "Get subtasks", getattr(args, 'verbose', False))


def handle_sessions_resume_command(args):
    """Handle sessions-resume command"""
    try:
        from empirica.data.session_database import SessionDatabase

        # Parse arguments
        ai_id = getattr(args, 'ai_id', None)
        count = args.count
        detail_level = getattr(args, 'detail_level', 'summary')

        # Use real database queries
        db = SessionDatabase()

        # Query real sessions from database
        cursor = db.conn.cursor()

        if ai_id:
            # Get sessions for specific AI
            cursor.execute("""
                SELECT session_id, ai_id, start_time, end_time,
                       total_cascades, avg_confidence, session_notes
                FROM sessions
                WHERE ai_id = ?
                ORDER BY start_time DESC
                LIMIT ?
            """, (ai_id, count))
        else:
            # Get recent sessions for all AIs
            cursor.execute("""
                SELECT session_id, ai_id, start_time, end_time,
                       total_cascades, avg_confidence, session_notes
                FROM sessions
                ORDER BY start_time DESC
                LIMIT ?
            """, (count,))

        # Convert rows to real session data
        sessions = []
        for row in cursor.fetchall():
            session_data = dict(row)

            # Calculate current phase from cascades if available
            cascade_cursor = db.conn.cursor()
            cascade_cursor.execute("""
                SELECT preflight_completed, think_completed, plan_completed,
                       investigate_completed, check_completed, act_completed, postflight_completed
                FROM cascades
                WHERE session_id = ? ORDER BY started_at DESC LIMIT 1
            """, (session_data['session_id'],))

            cascade_row = cascade_cursor.fetchone()
            if cascade_row:
                # Determine current phase based on completion status
                if cascade_row[6]:  # postflight_completed
                    current_phase = "POSTFLIGHT"
                elif cascade_row[5]:  # act_completed
                    current_phase = "ACT"
                elif cascade_row[4]:  # check_completed
                    current_phase = "CHECK"
                elif cascade_row[3]:  # investigate_completed
                    current_phase = "INVESTIGATE"
                elif cascade_row[2]:  # plan_completed
                    current_phase = "PLAN"
                elif cascade_row[1]:  # think_completed
                    current_phase = "THINK"
                else:
                    current_phase = "PREFLIGHT"
            else:
                current_phase = "PREFLIGHT"

            sessions.append({
                "session_id": session_data['session_id'],  # Real UUID!
                "ai_id": session_data['ai_id'],
                "start_time": session_data['start_time'],
                "end_time": session_data['end_time'],
                "status": "completed" if session_data['end_time'] else "active",
                "phase": current_phase,
                "total_cascades": session_data['total_cascades'],
                "avg_confidence": session_data['avg_confidence'],
                "last_activity": session_data['start_time'],  # Real timestamp!
            })

        result = {
            "ok": True,
            "ai_id": ai_id,
            "sessions_count": len(sessions),
            "detail_level": detail_level,
            "sessions": sessions,
            "timestamp": time.time()
        }

        # Format output
        if hasattr(args, 'output') and args.output == 'json':
            print(json.dumps(result, indent=2))
        else:
            print(f"✅ Found {len(sessions)} session(s):")
            for i, session in enumerate(sessions, 1):
                print(f"\n{i}. {session['session_id']}")
                print(f"   AI: {session['ai_id']}")
                print(f"   Phase: {session['phase']}")
                print(f"   Status: {session['status']}")
                print(f"   Start time: {str(session['start_time'])[:16]}")
                if session['total_cascades'] > 0:
                    print(f"   Cascades: {session['total_cascades']}")

        db.close()
        # Return None to avoid exit code issues and duplicate output
        return None

    except Exception as e:
        handle_cli_error(e, "Resume sessions", getattr(args, 'verbose', False))


def handle_goals_search_command(args):
    """Handle goals-search command - semantic search for goals across sessions.

    Uses Qdrant vector search to find goals similar to a query.
    Enables post-compact context recovery and cross-session goal discovery.
    """
    try:
        from empirica.core.qdrant.vector_store import search_goals, sync_goals_to_qdrant
        from empirica.data.session_database import SessionDatabase

        query = args.query
        project_id = getattr(args, 'project_id', None)
        item_type = getattr(args, 'type', None)  # 'goal' or 'subtask'
        status = getattr(args, 'status', None)
        ai_id = getattr(args, 'ai_id', None)
        limit = getattr(args, 'limit', 10)
        sync_first = getattr(args, 'sync', False)
        output = getattr(args, 'output', 'human')

        # Auto-detect project_id if not provided
        if not project_id:
            db = SessionDatabase()
            cursor = db.conn.cursor()
            # Get the most recently active project
            cursor.execute("""
                SELECT DISTINCT project_id FROM sessions
                WHERE project_id IS NOT NULL
                ORDER BY start_time DESC LIMIT 1
            """)
            row = cursor.fetchone()
            project_id = row[0] if row else None
            db.close()

            if not project_id:
                result = {
                    "ok": False,
                    "error": "No project found. Run empirica session-create first.",
                    "hint": "Or specify --project-id explicitly"
                }
                print(json.dumps(result, indent=2) if output == 'json' else f"Error: {result['error']}")
                return 1

        # Optionally sync SQLite goals to Qdrant first
        if sync_first:
            synced = sync_goals_to_qdrant(project_id)
            if output != 'json':
                print(f"📦 Synced {synced} goals/subtasks to Qdrant")

        # Perform semantic search
        results = search_goals(
            project_id=project_id,
            query=query,
            item_type=item_type,
            status=status,
            ai_id=ai_id,
            include_subtasks=True,
            limit=limit,
        )

        if output == 'json':
            print(json.dumps({
                "ok": True,
                "query": query,
                "project_id": project_id,
                "results_count": len(results),
                "results": results
            }, indent=2))
        else:
            if not results:
                print(f"\n🔍 No goals found for: \"{query}\"")
                print(f"   Project: {project_id[:8]}...")
                print("\n💡 Tips:")
                print("   - Run with --sync to sync SQLite goals to Qdrant first")
                print("   - Try a different query")
                print("   - Check Qdrant is running (EMPIRICA_QDRANT_URL)")
                return 0

            print(f"\n🔍 Found {len(results)} result(s) for: \"{query}\"")
            print(f"   Project: {project_id[:8]}...\n")

            for i, r in enumerate(results, 1):
                score = r.get('score', 0)
                item_type = r.get('type', 'unknown')
                is_completed = r.get('is_completed', False)

                # Status icon
                if is_completed:
                    status_icon = "✅"
                else:
                    status_icon = "⏳"

                # Type badge
                type_badge = "📋" if item_type == 'goal' else "📝"

                if item_type == 'goal':
                    objective = r.get('objective', 'No objective')
                    print(f"{status_icon} {i}. {type_badge} {objective[:70]}")
                else:
                    description = r.get('description', 'No description')
                    goal_id = r.get('goal_id', '')
                    print(f"{status_icon} {i}. {type_badge} {description[:70]}")
                    if goal_id:
                        print(f"      Goal: {goal_id[:8]}...")

                print(f"      Score: {score:.2f} | Status: {r.get('status', 'unknown')}")
                if r.get('session_id'):
                    print(f"      Session: {r['session_id'][:8]}...")
                if r.get('ai_id'):
                    print(f"      AI: {r['ai_id']}")
                print()

        return None

    except Exception as e:
        handle_cli_error(e, "Search goals", getattr(args, 'verbose', False))


def handle_goals_mark_stale_command(args):
    """Handle goals-mark-stale command - Mark in_progress goals as stale during compaction

    Used by pre-compact hooks to signal that AI context about goals has been lost.
    Post-compact AI should re-evaluate these goals before continuing work.
    """
    try:
        from empirica.core.goals.repository import GoalRepository

        session_id = getattr(args, 'session_id', None)
        reason = getattr(args, 'reason', 'memory_compact')
        output_format = getattr(args, 'output', 'json')

        if not session_id:
            if output_format == 'json':
                print(json.dumps({"ok": False, "error": "Session ID required (--session-id)"}))
            else:
                print("Error: Session ID required (--session-id)")
            return 1

        # Mark goals stale
        repo = GoalRepository()
        try:
            count = repo.mark_goals_stale(session_id, stale_reason=reason)
        finally:
            repo.close()

        if output_format == 'json':
            print(json.dumps({
                "ok": True,
                "session_id": session_id,
                "goals_marked_stale": count,
                "reason": reason,
                "message": f"Marked {count} in_progress goal(s) as stale"
            }))
        else:
            if count > 0:
                print(f"✅ Marked {count} in_progress goal(s) as stale")
                print(f"   Reason: {reason}")
                print(f"   Session: {session_id[:8]}...")
            else:
                print(f"ℹ️  No in_progress goals to mark stale for session {session_id[:8]}...")

        return 0

    except Exception as e:
        handle_cli_error(e, "Mark goals stale", getattr(args, 'verbose', False))


def handle_goals_get_stale_command(args):
    """Handle goals-get-stale command - Get stale goals for session or project

    Returns goals that were marked stale during compaction and need re-evaluation.
    """
    try:
        from empirica.core.goals.repository import GoalRepository

        session_id = getattr(args, 'session_id', None)
        project_id = getattr(args, 'project_id', None)
        output_format = getattr(args, 'output', 'json')

        if not session_id and not project_id:
            if output_format == 'json':
                print(json.dumps({"ok": False, "error": "Session ID or Project ID required"}))
            else:
                print("Error: Session ID (--session-id) or Project ID (--project-id) required")
            return 1

        repo = GoalRepository()
        try:
            stale_goals = repo.get_stale_goals(session_id=session_id, project_id=project_id)
        finally:
            repo.close()

        if output_format == 'json':
            print(json.dumps({
                "ok": True,
                "stale_goals": stale_goals,
                "count": len(stale_goals)
            }))
        else:
            if stale_goals:
                print(f"⚠️  Found {len(stale_goals)} stale goal(s) needing re-evaluation:\n")
                for g in stale_goals:
                    print(f"  📋 {g['objective'][:60]}...")
                    print(f"     ID: {g['goal_id'][:8]}...")
                    if g.get('stale_reason'):
                        print(f"     Reason: {g['stale_reason']}")
                    print()
            else:
                print("✅ No stale goals found")

        return 0

    except Exception as e:
        handle_cli_error(e, "Get stale goals", getattr(args, 'verbose', False))


def handle_goals_activate_command(args):
    """Handle goals-activate command — transition a planned goal to in_progress.

    Links the goal to the current transaction so the Sentinel can track it.
    """
    try:
        from empirica.data.repositories.goals import GoalDataRepository
        from empirica.data.session_database import SessionDatabase

        goal_id = args.goal_id
        output_format = getattr(args, 'output', 'json')

        # Auto-derive transaction_id
        transaction_id = None
        try:
            transaction_id = R.transaction_id()
        except Exception:
            pass

        db = SessionDatabase()
        repo = GoalDataRepository(db.conn)
        activated = repo.activate_goal(goal_id, transaction_id=transaction_id)
        db.close()

        if activated:
            result = {
                "ok": True,
                "goal_id": goal_id,
                "status": "in_progress",
                "transaction_id": transaction_id,
                "message": f"Goal {goal_id[:8]} activated — now in_progress and linked to current transaction"
            }
            if output_format == 'json':
                print(json.dumps(result))
            else:
                print(f"✅ Activated goal: {goal_id[:8]}")
                if transaction_id:
                    print(f"   Linked to transaction: {transaction_id[:8]}")
        else:
            result = {
                "ok": False,
                "error": f"Goal {goal_id} not found or not in 'planned' status"
            }
            print(json.dumps(result))
            sys.exit(1)

    except Exception as e:
        from empirica.cli.cli_utils import handle_cli_error
        handle_cli_error(e, "Activate goal", getattr(args, 'verbose', False))


def handle_goals_refresh_command(args):
    """Handle goals-refresh command - Mark a stale goal as in_progress

    Called when AI has regained context about a stale goal and is ready to work on it.
    """
    try:
        from empirica.core.goals.repository import GoalRepository

        goal_id = getattr(args, 'goal_id', None)
        output_format = getattr(args, 'output', 'json')

        if not goal_id:
            if output_format == 'json':
                print(json.dumps({"ok": False, "error": "Goal ID required (--goal-id)"}))
            else:
                print("Error: Goal ID required (--goal-id)")
            return 1

        repo = GoalRepository()
        try:
            refreshed = repo.refresh_goal(goal_id)
        finally:
            repo.close()

        if output_format == 'json':
            print(json.dumps({
                "ok": refreshed,
                "goal_id": goal_id,
                "refreshed": refreshed,
                "message": "Goal refreshed to in_progress" if refreshed else "Goal not found or not stale"
            }))
        else:
            if refreshed:
                print(f"✅ Goal {goal_id[:8]}... refreshed to in_progress")
            else:
                print(f"❌ Goal {goal_id[:8]}... not found or not stale")

        return 0

    except Exception as e:
        handle_cli_error(e, "Refresh goal", getattr(args, 'verbose', False))

# --- Merged from goals_ready_command.py ---



def _handle_goals_ready_command_helper(beads_ready, cursor, db, max_uncertainty, min_confidence, ready_work, session_id):
    """Extracted from handle_goals_ready_command to reduce complexity."""
    for beads_issue in beads_ready:
        beads_id = beads_issue.get('id')

        # Find Empirica goal with this beads_issue_id
        cursor = db.conn.execute("""
            SELECT id, objective, scope, status
            FROM goals
            WHERE beads_issue_id = ? AND session_id = ?
        """, (beads_id, session_id))

        goal_row = cursor.fetchone()

        if not goal_row:
            # BEADS issue not linked to Empirica goal
            continue

        goal_id = goal_row[0]
        objective = goal_row[1]
        scope_json = goal_row[2]
        status = goal_row[3]

        # Parse scope
        scope = json.loads(scope_json) if scope_json else {}

        # Get epistemic state from latest CHECK or PREFLIGHT
        cursor = db.conn.execute("""
            SELECT phase, engagement, know, do, context, clarity, coherence,
                   signal, density, state, change, completion, impact, uncertainty
            FROM reflexes
            WHERE session_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (session_id,))

        reflex_row = cursor.fetchone()

        epistemic_ready = True
        last_confidence = None
        last_uncertainty = None
        why_not_ready = None

        if reflex_row:
            # Build vectors dict from individual columns
            vectors = {
                'engagement': reflex_row[1],
                'know': reflex_row[2],
                'do': reflex_row[3],
                'context': reflex_row[4],
                'clarity': reflex_row[5],
                'coherence': reflex_row[6],
                'signal': reflex_row[7],
                'density': reflex_row[8],
                'state': reflex_row[9],
                'change': reflex_row[10],
                'completion': reflex_row[11],
                'impact': reflex_row[12],
                'uncertainty': reflex_row[13]
            }
            reflex_row[0]

            # Extract epistemic state
            last_confidence = vectors.get('know', 0.5)
            last_uncertainty = vectors.get('uncertainty', 0.5)

            # Check epistemic readiness
            if last_confidence < min_confidence:
                epistemic_ready = False
                why_not_ready = f"Confidence too low ({last_confidence:.2f} < {min_confidence})"

            if last_uncertainty > max_uncertainty:
                epistemic_ready = False
                why_not_ready = f"Uncertainty too high ({last_uncertainty:.2f} > {max_uncertainty})"
        else:
            # No epistemic data available - assume not ready
            epistemic_ready = False
            why_not_ready = "No PREFLIGHT/CHECK data available"

        # Build ready work item
        ready_item = {
            "goal_id": goal_id,
            "beads_issue_id": beads_id,
            "objective": objective,
            "priority": beads_issue.get('priority', 2),
            "no_blockers": True,  # BEADS already filtered for this
            "epistemic_ready": epistemic_ready,
            "last_check_confidence": last_confidence,
            "preflight_uncertainty": last_uncertainty,
            "scope": scope,
            "status": status
        }

        if epistemic_ready:
            ready_item["why_ready"] = "High confidence, low uncertainty, no blockers"
        else:
            ready_item["why_not_ready"] = why_not_ready

        ready_work.append(ready_item)

def handle_goals_ready_command(args):
    """Query BEADS ready work + filter by Empirica epistemic criteria

    Returns tasks that are:
    1. Dependency-ready (BEADS: no open blockers)
    2. Epistemically-ready (Empirica: confidence/uncertainty thresholds)
    """
    try:
        from empirica.data.session_database import SessionDatabase
        from empirica.integrations.beads import BeadsAdapter

        # Session ID is optional - auto-detect active session if not provided
        session_id = getattr(args, 'session_id', None)
        min_confidence = getattr(args, 'min_confidence', 0.7)
        max_uncertainty = getattr(args, 'max_uncertainty', 0.3)
        min_priority = getattr(args, 'min_priority', None)
        output_format = getattr(args, 'output', 'json')

        # Initialize adapters
        beads = BeadsAdapter()
        db = SessionDatabase()

        # Auto-detect active session if not provided
        if not session_id:
            cursor = db.conn.cursor()
            cursor.execute("""
                SELECT session_id FROM sessions
                WHERE end_time IS NULL
                ORDER BY start_time DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                session_id = row['session_id']
                if getattr(args, 'verbose', False):
                    print(f"📍 Auto-detected active session: {session_id[:8]}...", file=sys.stderr)
            else:
                result = {
                    "ok": False,
                    "error": "No active session found",
                    "hint": "Create a session: empirica session-create --ai-id <YOUR_AI_ID>"
                }
                if output_format == 'json':
                    print(json.dumps(result, indent=2))
                else:
                    print("❌ No active session found")
                    print("   Hint: Create a session: empirica session-create --ai-id <YOUR_AI_ID>")
                db.close()
                return 0

        ready_work = []

        # Check if BEADS available
        if not beads.is_available():
            result = {
                "ok": False,
                "error": "BEADS not available",
                "hint": "Install bd CLI or use goals without --use-beads",
                "ready_work": []
            }

            if output_format == 'json':
                print(json.dumps(result, indent=2))
            else:
                print("❌ BEADS not available")
                print("   Hint: Install bd CLI: curl -fsSL https://raw.githubusercontent.com/steveyegge/beads/main/scripts/install.sh | bash")

            db.close()
            # Return 0 to indicate success
            return 0

        # Query BEADS for ready work
        beads_ready = beads.get_ready_work(limit=50, priority=min_priority)

        if not beads_ready:
            result = {
                "ok": True,
                "ready_work": [],
                "message": "No ready work found in BEADS"
            }

            if output_format == 'json':
                print(json.dumps(result, indent=2))
            else:
                print("📭 No ready work found")

            db.close()
            # Return 0 to indicate success
            return 0

        # Map BEADS issues to Empirica goals
        _handle_goals_ready_command_helper(beads_ready, cursor, db, max_uncertainty, min_confidence, ready_work, session_id)

        # Filter to only epistemically-ready items
        epistemically_ready_work = [item for item in ready_work if item["epistemic_ready"]]

        result = {
            "ok": True,
            "ready_work": epistemically_ready_work,
            "total_beads_ready": len(beads_ready),
            "total_mapped_to_goals": len(ready_work),
            "epistemically_ready_count": len(epistemically_ready_work),
            "filters": {
                "min_confidence": min_confidence,
                "max_uncertainty": max_uncertainty,
                "min_priority": min_priority
            }
        }

        # Format output
        if output_format == 'json':
            print(json.dumps(result, indent=2))
        else:
            print("📋 Ready Work (Dependency + Epistemic)")
            print(f"   BEADS ready: {len(beads_ready)}")
            print(f"   Mapped to goals: {len(ready_work)}")
            print(f"   Epistemically ready: {len(epistemically_ready_work)}")
            print()

            if epistemically_ready_work:
                for item in epistemically_ready_work:
                    print(f"✅ {item['beads_issue_id']}: {item['objective']}")
                    print(f"   Priority: {item['priority']}, Confidence: {item['last_check_confidence']:.2f}, Uncertainty: {item['preflight_uncertainty']:.2f}")
                    print(f"   Why ready: {item['why_ready']}")
                    print()
            else:
                print("📭 No epistemically-ready work found")
                print("   (Tasks may have BEADS blockers cleared but epistemic confidence too low)")

        db.close()
        print(json.dumps(result, indent=2))
        return 0

    except Exception as e:
        logger.error(f"goals-ready error: {e}", exc_info=True)
        result = {
            "ok": False,
            "error": str(e)
        }
        print(json.dumps(result, indent=2))
        return 1


# --- Merged from goal_claim_command.py ---



def _handle_goals_claim_command_helper(ai_id, beads_issue_id, create_branch, goal_id, result, session_id):
    """Extracted from handle_goals_claim_command to reduce complexity."""
    if create_branch:
        try:
            # Generate branch name
            if beads_issue_id:
                branch_name = f"epistemic/reasoning/issue-{beads_issue_id}"
            else:
                branch_name = f"epistemic/reasoning/goal-{goal_id[:8]}"

            # Check if branch already exists
            check_result = subprocess.run(
                ["git", "rev-parse", "--verify", branch_name],
                capture_output=True,
                text=True
            )

            if check_result.returncode == 0:
                # Branch exists, just checkout
                subprocess.run(
                    ["git", "checkout", branch_name],
                    check=True,
                    capture_output=True
                )
                result["branch_action"] = "checked_out_existing"
            else:
                # Create new branch
                subprocess.run(
                    ["git", "checkout", "-b", branch_name],
                    check=True,
                    capture_output=True
                )
                result["branch_action"] = "created_new"

            result["branch_name"] = branch_name
            result["branch_created"] = True

            # Add branch mapping
            try:
                from empirica.integrations.branch_mapping import get_branch_mapping
                branch_mapping = get_branch_mapping()
                branch_mapping.add_mapping(
                    branch_name=branch_name,
                    goal_id=goal_id,
                    beads_issue_id=beads_issue_id,
                    ai_id=ai_id,
                    session_id=session_id
                )
                result["branch_mapping_saved"] = True
            except Exception as e:
                logger.warning(f"Failed to save branch mapping: {e}")
                result["branch_mapping_saved"] = False

        except subprocess.CalledProcessError as e:
            result["branch_created"] = False
            result["branch_error"] = str(e)
    else:
        result["branch_created"] = False
        result["branch_skipped"] = True

def handle_goals_claim_command(args):
    """Handle goals-claim command - Claim goal and create git branch"""
    try:
        from empirica.core.goals.repository import GoalRepository
        from empirica.data.session_database import SessionDatabase

        goal_id = args.goal_id
        create_branch = getattr(args, 'create_branch', True)
        run_preflight = getattr(args, 'run_preflight', False)
        output_format = getattr(args, 'output', 'json')

        # Validate goal exists
        goal_repo = GoalRepository()
        goal = goal_repo.get_goal(goal_id)

        if not goal:
            result = {
                "ok": False,
                "error": f"Goal not found: {goal_id}"
            }
            print(json.dumps(result) if output_format == 'json' else f"❌ {result['error']}")
            sys.exit(1)

        # Get session_id from the database (not stored in the Goal object itself)
        db = SessionDatabase()
        cursor = db.conn.execute(
            "SELECT session_id FROM goals WHERE id = ?",
            (goal_id,)
        )
        row = cursor.fetchone()
        if not row:
            result = {
                "ok": False,
                "error": f"Goal session not found in database: {goal_id}"
            }
            print(json.dumps(result) if output_format == 'json' else f"❌ {result['error']}")
            db.close()
            sys.exit(1)
        session_id = row[0]

        # Get AI ID from session
        cursor = db.conn.execute(
            "SELECT ai_id FROM sessions WHERE session_id = ?",
            (session_id,)
        )
        row = cursor.fetchone()
        ai_id = row[0] if row else "unknown"

        # Get BEADS issue ID
        cursor = db.conn.execute(
            "SELECT beads_issue_id FROM goals WHERE id = ?",
            (goal_id,)
        )
        row = cursor.fetchone()
        beads_issue_id = row[0] if row and row[0] else None
        db.close()

        result = {
            "ok": True,
            "goal_id": goal_id,
            "session_id": session_id,
            "beads_issue_id": beads_issue_id
        }

        # Update BEADS status to in_progress
        if beads_issue_id:
            try:
                from empirica.integrations.beads import BeadsAdapter
                beads = BeadsAdapter()
                if beads.is_available():
                    beads.update_status(beads_issue_id, "in_progress")
                    result["beads_status_updated"] = True
            except Exception as e:
                logger.warning(f"Failed to update BEADS status: {e}")
                result["beads_status_updated"] = False

        # Create git branch
        _handle_goals_claim_command_helper(ai_id, beads_issue_id, create_branch, goal_id, result, session_id)

        # Run PREFLIGHT if requested
        if run_preflight:
            try:
                # Import preflight command
                from .cascade_commands import handle_preflight_command  # type: ignore[reportMissingImports]

                # Create mock args for preflight
                class MockArgs:
                    """Mock arguments for calling preflight handler."""

                    def __init__(self, session_id: str, prompt: str) -> None:
                        """Initialize mock args with session ID and prompt."""
                        self.session_id = session_id
                        self.prompt = prompt
                        self.prompt_only = False
                        self.output = 'json'

                preflight_args = MockArgs(
                    session_id=goal.session_id,  # type: ignore[reportAttributeAccessIssue]
                    prompt=f"Starting work on goal: {goal.objective}"
                )

                # Run preflight (this will print its own output)
                handle_preflight_command(preflight_args)
                result["preflight_started"] = True

            except Exception as e:
                logger.warning(f"Failed to run PREFLIGHT: {e}")
                result["preflight_started"] = False
                result["preflight_error"] = str(e)
        else:
            result["preflight_started"] = False

        # Output result
        if output_format == 'json':
            print(json.dumps(result, indent=2))
        else:
            print(f"✅ Claimed goal: {goal_id[:8]}")
            if beads_issue_id and result.get("beads_status_updated"):
                print("✅ Updated BEADS status: in_progress")
            if result.get("branch_created"):
                print(f"✅ {'Created' if result['branch_action'] == 'created_new' else 'Checked out'} branch: {result['branch_name']}")
            if result.get("branch_mapping_saved"):
                print("✅ Branch mapping saved")
            if result.get("preflight_started"):
                print("🧠 Running PREFLIGHT...")
            print("✅ Ready to start work!")

    except Exception as e:
        handle_cli_error(e, "goals-claim", getattr(args, 'output', 'json'))


# --- Merged from goal_complete_command.py ---


def _gc_resolve_goal(goal_id, output_format):
    """Resolve goal by exact or prefix match. Returns (goal_row, full_goal_id) or exits."""
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    cursor = db.conn.cursor()

    cursor.execute("SELECT * FROM goals WHERE id = ?", (goal_id,))
    goal = cursor.fetchone()

    if not goal:
        cursor.execute("SELECT * FROM goals WHERE id LIKE ?", (f"{goal_id}%",))
        matches = cursor.fetchall()

        if len(matches) == 0:
            result = {
                "ok": False,
                "error": f"Goal not found: {goal_id}"
            }
            print(json.dumps(result) if output_format == 'json' else f"❌ {result['error']}")
            sys.exit(1)
        elif len(matches) > 1:
            match_ids = [m['id'][:12] for m in matches]
            result = {
                "ok": False,
                "error": f"Ambiguous goal prefix '{goal_id}' matches {len(matches)} goals",
                "matches": match_ids,
                "hint": "Provide more characters to disambiguate"
            }
            if output_format == 'json':
                print(json.dumps(result))
            else:
                print(f"❌ Ambiguous prefix '{goal_id}' matches {len(matches)} goals:")
                for mid in match_ids:
                    print(f"   - {mid}...")
            sys.exit(1)
        else:
            goal = matches[0]
            goal_id = goal['id']

    # Get BEADS issue ID
    cursor = db.conn.execute(
        "SELECT beads_issue_id FROM goals WHERE id = ?",
        (goal_id,)
    )
    row = cursor.fetchone()
    beads_issue_id = row[0] if row and row[0] else None
    db.close()

    return goal, goal_id, beads_issue_id


def _gc_mark_completed(goal_id):
    """Update goal status to completed in the database."""
    from empirica.data.session_database import SessionDatabase

    db2 = SessionDatabase()
    db2.conn.execute(
        "UPDATE goals SET status = 'completed', is_completed = 1, completed_timestamp = ? WHERE id = ?",
        (time.time(), goal_id)
    )
    db2.conn.commit()
    db2.close()


def _gc_run_postflight(goal, result):
    """Run POSTFLIGHT if requested. Mutates result dict."""
    try:
        from .cascade_commands import handle_postflight_command  # type: ignore[reportMissingImports]

        class MockArgs:
            """Mock arguments for calling postflight handler."""

            def __init__(self, session_id: str, task_summary: str) -> None:
                """Initialize mock args with session ID and task summary."""
                self.session_id = session_id
                self.task_summary = task_summary
                self.output = 'json'

        postflight_args = MockArgs(
            session_id=goal['session_id'],
            task_summary=f"Completed goal: {goal['objective']}"
        )
        handle_postflight_command(postflight_args)
        result["postflight_started"] = True

    except Exception as e:
        logger.warning(f"Failed to run POSTFLIGHT: {e}")
        result["postflight_started"] = False
        result["postflight_error"] = str(e)


def _gc_close_beads(beads_issue_id, close_reason, result):
    """Close BEADS issue if linked. Mutates result dict."""
    if beads_issue_id:
        try:
            from empirica.integrations.beads import BeadsAdapter
            beads = BeadsAdapter()
            if beads.is_available():
                beads.close_issue(beads_issue_id, reason=close_reason)
                result["beads_issue_closed"] = True
        except Exception as e:
            logger.warning(f"Failed to close BEADS issue: {e}")
            result["beads_issue_closed"] = False
            result["beads_error"] = str(e)
    else:
        result["beads_issue_closed"] = False
        result["beads_not_linked"] = True


def _gc_handle_branch(goal_id, goal, args, get_branch_mapping, result):
    """Handle branch merge, delete, and mapping cleanup. Mutates result dict."""
    try:
        branch_mapping = get_branch_mapping()
        branch_name = branch_mapping.get_branch_for_goal(goal_id)
    except Exception as e:
        logger.debug(f"Git operations unavailable: {e}")
        branch_mapping = None
        branch_name = None
        result["git_available"] = False

    if not branch_name:
        result["branch_found"] = False
        result["branch_merged"] = False
        return None

    result["branch_name"] = branch_name
    merge_branch = getattr(args, 'merge_branch', False)

    if merge_branch:
        _gc_merge_branch(branch_name, goal, args, result)
    else:
        result["branch_merged"] = False
        result["merge_skipped"] = True

    try:
        if branch_mapping:
            branch_mapping.remove_mapping(branch_name, archive=True)
            result["branch_mapping_removed"] = True
    except Exception as e:
        logger.warning(f"Failed to remove branch mapping: {e}")
        result["branch_mapping_removed"] = False

    return branch_name


def _gc_merge_branch(branch_name, goal, args, result):
    """Perform the git merge operation. Mutates result dict."""
    try:
        current_branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True
        )
        current_branch = current_branch_result.stdout.strip()

        if current_branch == branch_name:
            subprocess.run(
                ["git", "checkout", "main"],
                check=True, capture_output=True
            )

        merge_result = subprocess.run(
            ["git", "merge", "--no-ff", branch_name, "-m", f"Merge goal: {goal['objective']}"],
            capture_output=True, text=True
        )

        if merge_result.returncode == 0:
            result["branch_merged"] = True
            result["merge_commit"] = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True
            ).stdout.strip()

            if getattr(args, 'delete_branch', False):
                subprocess.run(
                    ["git", "branch", "-d", branch_name],
                    check=True, capture_output=True
                )
                result["branch_deleted"] = True
        else:
            result["branch_merged"] = False
            result["merge_error"] = merge_result.stderr

    except subprocess.CalledProcessError as e:
        result["branch_merged"] = False
        result["merge_error"] = str(e)


def _gc_create_handoff(goal, result):
    """Create handoff report. Mutates result dict."""
    try:
        from .handoff_commands import handle_handoff_create_command

        class MockArgs:
            """Mock arguments for calling handoff create handler."""

            def __init__(self, session_id: str, task_summary: str) -> None:
                """Initialize mock args with session ID and task summary."""
                self.session_id = session_id
                self.task_summary = task_summary
                self.key_findings = None
                self.remaining_unknowns = None
                self.next_session_context = None
                self.artifacts_created = None
                self.output = 'json'

        handoff_args = MockArgs(
            session_id=goal.session_id,
            task_summary=f"Completed goal: {goal.objective}"
        )
        handle_handoff_create_command(handoff_args)
        result["handoff_created"] = True

    except Exception as e:
        logger.warning(f"Failed to create handoff: {e}")
        result["handoff_created"] = False
        result["handoff_error"] = str(e)


def _gc_print_human_output(result, goal_id, beads_issue_id, branch_name):
    """Print human-readable output for goals-complete."""
    print(f"✅ Completed goal: {goal_id[:8]}")
    if result.get("postflight_started"):
        print("🧠 POSTFLIGHT completed")
    if result.get("beads_issue_closed"):
        print(f"✅ Closed BEADS issue: {beads_issue_id}")
    if result.get("branch_merged"):
        print(f"✅ Merged branch: {branch_name}")
        if result.get("branch_deleted"):
            print(f"✅ Deleted branch: {branch_name}")
    if result.get("branch_mapping_removed"):
        print("✅ Branch mapping archived")
    if result.get("handoff_created"):
        print("✅ Handoff report created")
    print("✅ Goal complete!")


def handle_goals_complete_command(args):
    """Handle goals-complete command - Complete goal, merge branch, close BEADS"""
    try:
        from empirica.integrations.branch_mapping import get_branch_mapping

        goal_id = args.goal_id
        run_postflight = getattr(args, 'run_postflight', False)
        close_reason = getattr(args, 'reason', 'completed')
        output_format = getattr(args, 'output', 'json')

        goal, goal_id, beads_issue_id = _gc_resolve_goal(goal_id, output_format)

        _gc_mark_completed(goal_id)

        result = {
            "ok": True,
            "goal_id": goal_id,
            "objective": goal['objective'],
            "session_id": goal['session_id'],
            "beads_issue_id": beads_issue_id,
            "status_updated": True
        }

        if run_postflight:
            _gc_run_postflight(goal, result)
        else:
            result["postflight_started"] = False

        _gc_close_beads(beads_issue_id, close_reason, result)

        branch_name = _gc_handle_branch(goal_id, goal, args, get_branch_mapping, result)

        if getattr(args, 'create_handoff', False):
            _gc_create_handoff(goal, result)
        else:
            result["handoff_created"] = False

        if output_format == 'json':
            print(json.dumps(result, indent=2))
        else:
            _gc_print_human_output(result, goal_id, beads_issue_id, branch_name)

    except Exception as e:
        handle_cli_error(e, "goals-complete", getattr(args, 'output', 'json'))


# --- Merged from goal_discovery_commands.py ---

def handle_goals_discover_command(args):
    """Discover goals from other AIs via git notes"""
    try:
        from empirica.core.canonical.empirica_git import GitGoalStore

        goal_store = GitGoalStore()

        from_ai_id = getattr(args, 'from_ai_id', None)
        session_id = getattr(args, 'session_id', None)

        # Discover goals
        goals = goal_store.discover_goals(
            from_ai_id=from_ai_id,
            session_id=session_id
        )

        result = {
            "ok": True,
            "count": len(goals),
            "goals": goals,
            "filter": {
                "from_ai_id": from_ai_id,
                "session_id": session_id
            }
        }

        # Format output
        if hasattr(args, 'output') and args.output == 'json':
            print(json.dumps(result, indent=2))
        else:
            if not goals:
                print("🔍 No goals found")
                if from_ai_id:
                    print(f"   Searched for goals from: {from_ai_id}")
                if session_id:
                    print(f"   Searched in session: {session_id}")
                print("\n💡 Tip: Goals are stored in git notes when created")
                print("   Make sure you've run 'git fetch' to get latest goals")
            else:
                print(f"🔍 Discovered {len(goals)} goal(s):\n")
                for i, goal_data in enumerate(goals, 1):
                    print(f"{i}. Goal ID: {goal_data['goal_id'][:8]}...")
                    print(f"   Created by: {goal_data['ai_id']}")
                    print(f"   Session: {goal_data['session_id'][:8]}...")
                    print(f"   Objective: {goal_data['goal_data']['objective'][:80]}")
                    print(f"   Scope: {goal_data['goal_data']['scope']}")

                    # Show lineage
                    if 'lineage' in goal_data and len(goal_data['lineage']) > 1:
                        print(f"   Lineage: {len(goal_data['lineage'])} action(s)")
                        for entry in goal_data['lineage']:
                            print(f"     • {entry['ai_id']} - {entry['action']} at {entry['timestamp'][:10]}")

                    print()

                print("💡 To resume a goal, use:")
                print("   empirica goals-resume <goal-id> --ai-id <your-ai-id>")

        return result

    except Exception as e:
        handle_cli_error(e, "Goal discovery", getattr(args, 'verbose', False))
        # Error handler already manages output, return None to avoid duplicate output
        return None


def handle_goals_resume_command(args):
    """Resume another AI's goal with epistemic handoff"""
    try:
        from empirica.core.canonical.empirica_git import GitGoalStore

        goal_id = args.goal_id
        ai_id = getattr(args, 'ai_id', 'empirica_cli')

        goal_store = GitGoalStore()

        # Load goal from git
        goal_data = goal_store.load_goal(goal_id)

        if not goal_data:
            result = {
                "ok": False,
                "error": f"Goal {goal_id} not found in git notes"
            }

            if hasattr(args, 'output') and args.output == 'json':
                print(json.dumps(result, indent=2))
            else:
                print(f"❌ Goal {goal_id[:8]}... not found")
                print("\n💡 Try:")
                print("   1. empirica goals-discover --from-ai-id <other-ai>")
                print("   2. git fetch  # Pull latest goals from remote")

            return result

        # Add lineage entry
        goal_store.add_lineage(goal_id, ai_id, "resumed")

        result = {
            "ok": True,
            "goal_id": goal_id,
            "ai_id": ai_id,
            "original_ai": goal_data['ai_id'],
            "message": "Goal resumed successfully",
            "objective": goal_data['goal_data']['objective'],
            "epistemic_state": goal_data.get('epistemic_state', {})
        }

        # Format output
        if hasattr(args, 'output') and args.output == 'json':
            print(json.dumps(result, indent=2))
        else:
            print("✅ Goal resumed successfully")
            print(f"   Goal ID: {goal_id[:8]}...")
            print(f"   Original AI: {goal_data['ai_id']}")
            print(f"   Resuming as: {ai_id}")
            print(f"   Objective: {goal_data['goal_data']['objective'][:80]}")

            # Show epistemic handoff
            epistemic_state = goal_data.get('epistemic_state', {})
            if epistemic_state:
                print(f"\n📊 Epistemic State from {goal_data['ai_id']}:")
                for key, value in epistemic_state.items():
                    if isinstance(value, (int, float)):
                        print(f"   • {key.upper()}: {value:.2f}")

            print("\n💡 Next steps:")
            print("   1. Review original AI's epistemic state")
            print(f"   2. Run your own preflight: empirica preflight \"<task>\" --ai-id {ai_id}")
            print("   3. Compare your vectors with original AI's")

        return result

    except Exception as e:
        handle_cli_error(e, "Goal resume", getattr(args, 'verbose', False))
        # Error handler already manages output, return None to avoid duplicate output
        return None


# ─── goals prune ───────────────────────────────────────────────────────────

def _resolve_project_id_for_prune(args, db) -> str | None:
    """Resolve project_id for prune scope. Order: --project-id flag → resolver
    via session → workspace.db active project."""
    pid = getattr(args, 'project_id', None)
    if pid:
        return pid
    try:
        ctx = R.context()
        sid = ctx.get('empirica_session_id')
        if sid:
            session = db.get_session(sid)
            if session:
                return session.get('project_id')
    except Exception:
        pass
    return None


def _prune_by_status_planned(cursor, project_id: str, dry_run: bool) -> list[dict]:
    """Close all goals with status='planned' in the project. Goals that
    were planned but never activated are noise — collaboratively-defined
    futures that the team chose not to pursue."""
    cursor.execute(
        "SELECT id, objective, created_timestamp FROM goals "
        "WHERE is_completed = 0 AND status = 'planned' AND project_id = ?",
        (project_id,),
    )
    rows = cursor.fetchall()
    pruned = []
    for goal_id, objective, _created in rows:
        pruned.append({
            'id': goal_id, 'objective': objective[:80], 'mode': 'by-status:planned',
            'reason': 'Planned goal never activated — closed via goals-prune sweep',
        })
        if not dry_run:
            cursor.execute(
                "UPDATE goals SET is_completed = 1, status = 'completed', "
                "completed_timestamp = ? WHERE id = ?",
                (time.time(), goal_id),
            )
    return pruned


def _prune_test_pollution(cursor, project_id: str, dry_run: bool) -> list[dict]:
    """Close goals that look like test-runner pollution.

    Patterns: objectives starting with 'Test '/'test '/'E2E test',
    objectives containing 'test issue'/'test goal', ai_id starting with
    'test-' or exactly 'test'. Conservative — only matches obvious
    test-runner patterns; legitimate goals about testing (e.g.
    'Test cockpit TUI buttons + keys end-to-end') stay open.
    """
    # Test pollution often has NULL project_id (test runner doesn't set it).
    # Match on the test-pattern AND (project matches OR project is NULL).
    # Patterns split into two families:
    #   prefix forms — 'Test ...', 'E2E test ...', 'Final test ...'
    #   suffix forms — '... test', 'No X test', 'Post-fix test', 'Invalid X test'
    # Suffix forms catch the throwaway names test runners and old
    # development phases leave behind.
    cursor.execute(
        "SELECT id, objective, ai_id FROM goals "
        "WHERE is_completed = 0 "
        "AND (project_id = ? OR project_id IS NULL) "
        "AND ("
        "     objective LIKE 'Test %' OR objective LIKE 'test %' "
        "     OR objective LIKE 'E2E test%' "
        "     OR objective LIKE 'Final test%' "
        "     OR objective LIKE 'Post-fix test%' "
        "     OR objective LIKE 'Invalid % test%' "
        "     OR objective LIKE 'No % test%' "
        "     OR objective LIKE '% test goal%' "
        "     OR objective LIKE '% test issue%' "
        "     OR objective LIKE '%smoke test%' "
        "     OR ai_id LIKE 'test-%' OR ai_id = 'test'"
        ")",
        (project_id,),
    )
    rows = cursor.fetchall()
    pruned = []
    for goal_id, objective, ai_id in rows:
        # Exclude legitimate goals about testing — heuristic: real
        # objectives tend to be longer (≥80 chars). Pure test-runner
        # pollution like 'E2E test goal workflow' (22 chars) clears
        # the bar; 'Test cockpit TUI buttons + keys end-to-end...'
        # (200+ chars) doesn't.
        if len(objective or '') >= 80:
            continue
        pruned.append({
            'id': goal_id, 'objective': objective[:80], 'mode': 'test-pollution',
            'ai_id': ai_id or '(none)',
            'reason': 'Test-runner pollution pattern (E2E/test-prefixed objective or test ai_id) — closed via goals-prune sweep',
        })
        if not dry_run:
            cursor.execute(
                "UPDATE goals SET is_completed = 1, status = 'completed', "
                "completed_timestamp = ? WHERE id = ?",
                (time.time(), goal_id),
            )
    return pruned


def _prune_auto_stale(cursor, project_id: str, days: int, dry_run: bool) -> list[dict]:
    """Close in_progress goals older than N days with no activity.

    'Activity' here = created_timestamp (we don't track touch timestamps
    on goals directly). A goal that was created N+ days ago and never
    completed is effectively stale.
    """
    cutoff = time.time() - (days * 86400)
    # Match unscoped (NULL project_id) goals too — those accumulate from
    # one-off test runs and CLI invocations outside any project context,
    # and are exactly the noise this command exists to clean up.
    cursor.execute(
        "SELECT id, objective, created_timestamp FROM goals "
        "WHERE is_completed = 0 AND status = 'in_progress' "
        "AND created_timestamp < ? "
        "AND (project_id = ? OR project_id IS NULL)",
        (cutoff, project_id),
    )
    rows = cursor.fetchall()
    pruned = []
    for goal_id, objective, created in rows:
        age_days = int((time.time() - float(created or 0)) / 86400)
        pruned.append({
            'id': goal_id, 'objective': objective[:80], 'mode': f'auto-stale:{days}d',
            'age_days': age_days,
            'reason': f'No activity in {age_days} days (cutoff {days}d) — closed via goals-prune sweep',
        })
        if not dry_run:
            cursor.execute(
                "UPDATE goals SET is_completed = 1, status = 'completed', "
                "completed_timestamp = ? WHERE id = ?",
                (time.time(), goal_id),
            )
    return pruned


def _prune_duplicates_via_qdrant(cursor, project_id: str, threshold: float,
                                  dry_run: bool) -> list[dict]:
    """Detect goals with similar objectives via Qdrant embedding similarity.

    For each open goal, find others above similarity threshold; keep the
    OLDEST (first-described) and propose closing the others. Conservative:
    only same-status goals are considered duplicates.
    """
    try:
        from empirica.core.qdrant.connection import _get_qdrant_client
    except Exception as e:
        return [{'mode': 'duplicates', 'error': f'Qdrant unavailable: {e}'}]

    client = _get_qdrant_client()
    if client is None:
        return [{'mode': 'duplicates',
                 'error': 'Qdrant not running — start with empirica-server'}]

    cursor.execute(
        "SELECT id, objective, created_timestamp FROM goals "
        "WHERE is_completed = 0 AND project_id = ? "
        "ORDER BY created_timestamp ASC",
        (project_id,),
    )
    goals = list(cursor.fetchall())
    if len(goals) < 2:
        return []

    # Lazy embed via Qdrant collection if available; otherwise use a simple
    # token-overlap heuristic (Jaccard) as fallback. For v1 we use the
    # heuristic — Qdrant similarity here would require embedding queries
    # at runtime, which is heavier than the benefit.
    pruned: list[dict] = []
    seen_kept: list[tuple[str, set[str]]] = []
    for goal_id, objective, _ in goals:
        tokens = {t.lower() for t in (objective or '').split() if len(t) > 3}
        if not tokens:
            continue
        merged = False
        for kept_id, kept_tokens in seen_kept:
            if not kept_tokens:
                continue
            jaccard = len(tokens & kept_tokens) / max(1, len(tokens | kept_tokens))
            if jaccard >= threshold:
                pruned.append({
                    'id': goal_id, 'objective': objective[:80], 'mode': 'duplicate',
                    'similar_to': kept_id,
                    'similarity': round(jaccard, 2),
                    'reason': f'Duplicate of {kept_id[:8]} (Jaccard {jaccard:.2f}) — closed via goals-prune sweep',
                })
                if not dry_run:
                    cursor.execute(
                        "UPDATE goals SET is_completed = 1, status = 'completed', "
                        "completed_timestamp = ? WHERE id = ?",
                        (time.time(), goal_id),
                    )
                merged = True
                break
        if not merged:
            seen_kept.append((goal_id, tokens))
    return pruned


def _write_prune_receipt(pruned: list[dict], modes_run: list[str], dry_run: bool):
    """Write a transparent receipt of what got pruned to git notes."""
    try:
        receipt = {
            'event': 'goals-prune',
            'modes': modes_run,
            'dry_run': dry_run,
            'pruned_count': len([p for p in pruned if 'error' not in p]),
            'errors': [p for p in pruned if 'error' in p],
            'timestamp': time.time(),
        }
        subprocess.run(
            ['git', 'notes', '--ref=breadcrumbs', 'append', '-m', json.dumps(receipt)],
            capture_output=True, timeout=5, check=False,
        )
    except Exception:
        pass


def handle_goals_prune_command(args):
    """Handle goals-prune command — bulk cleanup of stale/duplicate/planned goals.

    Three modes (combinable; first-match wins per goal):
      --by-status planned   Close all planned goals (never activated)
      --auto-stale [N]      Close in_progress goals older than N days (default 30)
      --duplicates [thresh] Close goals whose objective text is ≥ threshold
                            similar to another (Jaccard token overlap, default 0.7)

    Dry-run is the DEFAULT — pass --apply to actually mutate. Receipt
    written to git notes (breadcrumbs ref) for audit trail.
    """
    try:
        from empirica.data.session_database import SessionDatabase

        modes_run: list[str] = []
        if getattr(args, 'test_pollution', False):
            modes_run.append('test-pollution')
        if getattr(args, 'by_status_planned', False):
            modes_run.append('by-status:planned')
        if getattr(args, 'auto_stale', None) is not None:
            modes_run.append(f'auto-stale:{args.auto_stale}d')
        if getattr(args, 'duplicates', None) is not None:
            modes_run.append(f'duplicates:{args.duplicates}')

        if not modes_run:
            print(json.dumps({
                'ok': False,
                'error': 'Specify at least one mode: --test-pollution, --by-status-planned, --auto-stale [N], --duplicates [thresh]',
                'hint': 'Quick start: `empirica goals-prune --test-pollution` (dry-run by default; pass --apply to mutate).',
            }))
            return 1

        # Default to dry-run unless --apply explicitly given.
        dry_run = not getattr(args, 'apply', False)

        db = SessionDatabase()
        project_id = _resolve_project_id_for_prune(args, db)
        if not project_id:
            db.close()
            print(json.dumps({
                'ok': False,
                'error': 'Could not resolve project_id. Pass --project-id explicitly.',
            }))
            return 1

        cursor = db.conn.cursor()
        all_pruned: list[dict] = []

        if getattr(args, 'test_pollution', False):
            all_pruned.extend(_prune_test_pollution(cursor, project_id, dry_run))
        if getattr(args, 'by_status_planned', False):
            all_pruned.extend(_prune_by_status_planned(cursor, project_id, dry_run))
        if getattr(args, 'auto_stale', None) is not None:
            all_pruned.extend(_prune_auto_stale(cursor, project_id, args.auto_stale, dry_run))
        if getattr(args, 'duplicates', None) is not None:
            all_pruned.extend(_prune_duplicates_via_qdrant(
                cursor, project_id, args.duplicates, dry_run,
            ))

        if not dry_run:
            db.conn.commit()
        db.close()

        _write_prune_receipt(all_pruned, modes_run, dry_run)

        successful = [p for p in all_pruned if 'error' not in p]
        errors = [p for p in all_pruned if 'error' in p]

        result = {
            'ok': True,
            'dry_run': dry_run,
            'modes_run': modes_run,
            'project_id': project_id,
            'pruned_count': len(successful),
            'pruned': successful[:50],  # Cap output
            'errors': errors,
        }
        if len(successful) > 50:
            result['truncated_count'] = len(successful) - 50
        print(json.dumps(result, indent=2, default=str))
        return 0

    except Exception as e:
        handle_cli_error(e, "Goals prune", getattr(args, 'verbose', False))
        return 1
