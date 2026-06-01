#!/usr/bin/env python3
"""
Session Database - SQLite storage for epistemic states and reasoning cascades

Stores:
- Sessions (AI sessions with metadata)
- Cascades (reasoning cascade executions)
- Epistemic Assessments (12D vector measurements)
- Divergence Tracking (delegate vs trustee, sycophancy detection)
- Drift Monitoring (long-term behavioral patterns)
- Bayesian Beliefs (evidence-based belief tracking)
- Investigation Tools (tool usage tracking)

Design:
- SQLite as source of truth (structured queries, relational integrity)
- JSON exports for AI-readable format (easy parsing)
- Temporal logging (prevents recursion)
- Session continuity (load previous session context)

Location (Canonical):
- Project-local: ./.empirica/sessions/sessions.db (relative to CWD)
- NOT home directory: ~/ (config/credentials are global, data is project-scoped)
- See: docs/reference/STORAGE_LOCATIONS.md for rationale

Storage Architecture:
- Global (~/.empirica/): config.yaml, credentials.yaml, calibration/
- Project-local (./.empirica/): sessions.db (this file)
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Import canonical structures
try:
    from canonical.reflex_frame import Action, EpistemicAssessment, VectorState  # noqa: F401, I001 — availability check for CANONICAL_AVAILABLE  # pyright: ignore[reportUnusedImport,reportMissingImports]
    CANONICAL_AVAILABLE = True
except ImportError:
    CANONICAL_AVAILABLE = False

# Import formatters
from .formatters import generate_context_markdown  # noqa: E402 — after conditional canonical import


def _resolve_canonical_project_root() -> str | None:
    """Resolve the canonical project root path without trusting CWD.

    Returns None if unresolvable — never falls back to os.getcwd() because
    a CWD inside a subdirectory pollutes that subdir with a stray .empirica/
    when downstream code (e.g. dependency-graph cache) blindly mkdirs there.
    """
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        path = R.project_path()
        if path:
            return str(path)
    except Exception:
        pass
    try:
        from empirica.utils.session_resolver import find_project_root
        p = find_project_root(allow_git_root=True)
        if p:
            return str(p)
    except Exception:
        pass
    return None


class SessionDatabase:
    """Central database for all session data (supports SQLite and PostgreSQL)"""

    def __init__(self, db_path: str | None = None, db_type: str | None = None):
        """
        Initialize session database with pluggable backend

        Args:
            db_path: Path to database (SQLite only, ignored for PostgreSQL)
            db_type: "sqlite" or "postgresql" (defaults to config or "sqlite")
        """
        # Import adapter layer
        from empirica.config.database_config import get_database_config
        from empirica.data.db_adapter import DatabaseAdapter

        # If db_type not specified, load from config
        if db_type is None:
            db_config = get_database_config()
            db_type = db_config.get("type", "sqlite")
        else:
            db_config = {"type": db_type}

        # Create appropriate adapter
        if db_type == "sqlite":
            # Use provided path or default
            if db_path is None:
                from empirica.config.path_resolver import get_session_db_path
                db_path = str(get_session_db_path())

            self.db_path = Path(db_path)
            self.adapter = DatabaseAdapter.create(db_type="sqlite", db_path=str(self.db_path))
            logger.info(f"📊 Session Database initialized (SQLite): {self.db_path}")

        elif db_type == "postgresql":
            pg_config = db_config.get("postgresql", {})
            self.adapter = DatabaseAdapter.create(db_type="postgresql", **pg_config)
            self.db_path = None  # N/A for PostgreSQL
            logger.info("📊 Session Database initialized (PostgreSQL)")

        else:
            raise ValueError(f"Unsupported database type: {db_type}")

        # Expose raw connection for backward compatibility with repositories
        self.conn = self.adapter.conn

        # Initialize retry policy for resilience
        from empirica.data.connection_pool import RetryPolicy
        self.retry_policy = RetryPolicy(
            max_retries=5,
            base_delay=0.1,
            max_delay=10.0
        )
        logger.debug("✓ Retry policy initialized (exponential backoff, max 5 retries)")

        self._create_tables()

        # Initialize domain repositories (sharing same connection)
        from empirica.data.repositories import (
            BranchRepository,
            BreadcrumbRepository,
            CascadeRepository,
            CodebaseModelRepository,
            CommandRepository,
            GoalDataRepository,
            MetricsRepository,
            ProjectRepository,
            SessionRepository,
            TokenRepository,
            VectorRepository,
            WorkspaceRepository,
        )
        self.sessions = SessionRepository(self.conn)
        self.cascades = CascadeRepository(self.conn)
        self.goals = GoalDataRepository(self.conn)
        self.branches = BranchRepository(self.conn)
        self.breadcrumbs = BreadcrumbRepository(self.conn)
        self.projects = ProjectRepository(self.conn)
        self.tokens = TokenRepository(self.conn)
        self.commands = CommandRepository(self.conn)
        self.workspace = WorkspaceRepository(self.conn)
        self.vectors = VectorRepository(self.conn)
        self.metrics = MetricsRepository(self.conn)
        self.codebase_model = CodebaseModelRepository(self.conn)

        # Core repositories need special handling to avoid circular dependency
        # We'll initialize them lazily when first accessed
        self._tasks = None
        self._core_goals = None
        self._tasks_db_path = str(self.db_path) if self.db_path else None

    @property
    def tasks(self):
        """Lazy-load TaskRepository to avoid circular dependency"""
        if self._tasks is None:
            from empirica.core.tasks.repository import TaskRepository
            self._tasks = TaskRepository(db_path=self._tasks_db_path)
        return self._tasks

    @property
    def core_goals(self):
        """Lazy-load core GoalRepository for query methods"""
        if self._core_goals is None:
            from empirica.core.goals.repository import GoalRepository
            self._core_goals = GoalRepository()
        return self._core_goals

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        """
        Validate session_id is a proper UUID format.

        This ensures:
        - Session IDs are globally unique
        - Git notes refs are valid paths
        - Session aliases work correctly
        - Multi-AI coordination is safe

        Args:
            session_id: Session ID to validate

        Raises:
            ValueError: If session_id is not a valid UUID
        """
        try:
            uuid.UUID(session_id)
        except (ValueError, AttributeError, TypeError) as e:
            raise ValueError(
                f"Invalid session_id: '{session_id}'. "
                f"Session IDs must be valid UUIDs (e.g., '550e8400-e29b-41d4-a716-446655440000')"
            ) from e

    def _create_tables(self):
        """Create all database tables from schema modules (dialect-aware)"""
        from empirica.data.schema import ALL_SCHEMAS
        from empirica.data.schema.dialect import adapt_all_schemas

        dialect = self.adapter.dialect
        cursor = self.conn.cursor()

        # Adapt and execute all table schemas for current dialect
        adapted_schemas = adapt_all_schemas(list(ALL_SCHEMAS), dialect)
        for schema_sql in adapted_schemas:
            cursor.execute(schema_sql)

        # Run tracked migrations (SQLite only — PostgreSQL gets fresh schema)
        if dialect == "sqlite":
            from empirica.data.migrations import ALL_MIGRATIONS, MigrationRunner
            migration_runner = MigrationRunner(self.conn)
            migration_runner.run_all(ALL_MIGRATIONS)

        # Create indexes for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_ai ON sessions(ai_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cascades_session ON cascades(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cascades_confidence ON cascades(final_confidence)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_beliefs_cascade ON bayesian_beliefs(cascade_id)")
        # BEADS integration index (check if column exists first)
        if self.adapter.column_exists("goals", "beads_issue_id"):
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_goals_beads_issue_id ON goals(beads_issue_id)")
        # Index for reflexes table (replaces old cascade_metadata index)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_session ON epistemic_snapshots(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_ai ON epistemic_snapshots(ai_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_cascade ON epistemic_snapshots(cascade_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_created ON epistemic_snapshots(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reflexes_session ON reflexes(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reflexes_phase ON reflexes(phase)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_goals_session ON goals(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_subtasks_goal ON subtasks(goal_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_subtasks_status ON subtasks(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mistakes_session ON mistakes_made(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mistakes_goal ON mistakes_made(goal_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_activity ON projects(last_activity_timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_project_handoffs_project ON project_handoffs(project_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_project_handoffs_timestamp ON project_handoffs(created_timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_project_findings_project ON project_findings(project_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_project_findings_session ON project_findings(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_project_unknowns_project ON project_unknowns(project_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_project_unknowns_resolved ON project_unknowns(is_resolved)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_project_dead_ends_project ON project_dead_ends(project_id)")
        # idx_project_reference_docs_project dropped in goal 3d6aeb08 Phase 3 —
        # the project_reference_docs table is dropped by migration 047.
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_epistemic_sources_project ON epistemic_sources(project_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_epistemic_sources_session ON epistemic_sources(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_epistemic_sources_type ON epistemic_sources(source_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_epistemic_sources_confidence ON epistemic_sources(confidence)")

        # Indexes for investigation_branches
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_investigation_branches_session ON investigation_branches(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_investigation_branches_status ON investigation_branches(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_investigation_branches_winner ON investigation_branches(is_winner)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_investigation_branches_merge_score ON investigation_branches(merge_score)")

        # Indexes for merge_decisions
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_merge_decisions_session ON merge_decisions(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_merge_decisions_round ON merge_decisions(investigation_round)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_merge_decisions_winning_branch ON merge_decisions(winning_branch_id)")

        # Indexes for token_savings
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_token_savings_session ON token_savings(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_token_savings_type ON token_savings(saving_type)")

        # transaction_id indexes — must run AFTER migrations that add the column (#44)
        for table, idx_name in [
            ("assumptions", "idx_assumptions_transaction"),
            ("decisions", "idx_decisions_transaction"),
            ("project_findings", "idx_findings_transaction"),
            ("project_unknowns", "idx_unknowns_transaction"),
            ("project_dead_ends", "idx_dead_ends_transaction"),
        ]:
            if self.adapter.column_exists(table, "transaction_id"):
                cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}(transaction_id)")

        self.conn.commit()

    def create_session(self, ai_id: str, components_loaded: int = 0,
                      user_id: str | None = None, subject: str | None = None,
                      bootstrap_level: int = 1,
                      parent_session_id: str | None = None,
                      project_id: str | None = None) -> str:
        """
        Create new session, return session_id (delegates to SessionRepository)

        Args:
            ai_id: AI identifier (required)
            components_loaded: Number of components loaded - default 0 (components created on-demand)
            user_id: Optional user identifier
            subject: Optional subject/workstream identifier for filtering
            bootstrap_level: Bootstrap configuration level (1-3, default 1)
            parent_session_id: Optional parent session UUID for sub-agent lineage
            project_id: Optional project UUID for global registry tracking

        Returns:
            session_id: UUID string
        """
        return self.sessions.create_session(
            ai_id, components_loaded, user_id, subject, bootstrap_level,
            parent_session_id=parent_session_id,
            project_id=project_id
        )

    def end_session(self, session_id: str, avg_confidence: float | None = None,
                   drift_detected: bool = False, notes: str | None = None):
        """Mark session as ended (delegates to SessionRepository)"""
        self._validate_session_id(session_id)
        return self.sessions.end_session(session_id, avg_confidence, drift_detected, notes)

    # ------------------------------------------------------------------
    # Subagent session facade (migration 034 — kept separate from main
    # `sessions` table). See SessionRepository for full docs.
    # ------------------------------------------------------------------

    def create_subagent_session(
        self,
        agent_name: str,
        parent_session_id: str,
        project_id: str | None = None,
        instance_id: str | None = None,
    ) -> str:
        """Create a child session for a Task tool spawn (delegates to repo)."""
        return self.sessions.create_subagent_session(
            agent_name=agent_name,
            parent_session_id=parent_session_id,
            project_id=project_id,
            instance_id=instance_id,
        )

    def end_subagent_session(
        self,
        session_id: str,
        rollup_summary: str | None = None,
    ):
        """Mark a subagent session as completed (delegates to repo)."""
        return self.sessions.end_subagent_session(session_id, rollup_summary)

    def get_subagent_session(self, session_id: str) -> dict | None:
        """Get a subagent session row by ID (delegates to repo)."""
        return self.sessions.get_subagent_session(session_id)

    def list_subagents_for_parent(
        self,
        parent_session_id: str,
        status: str | None = None,
    ) -> list[dict]:
        """List subagent children for a parent (delegates to repo)."""
        return self.sessions.list_subagents_for_parent(parent_session_id, status)

    def ensure_session_exists(
        self,
        session_id: str,
        ai_id: str = "claude-code",
        project_id: str | None = None,
        instance_id: str | None = None,
    ) -> bool:
        """Auto-heal: insert minimal session row if missing (delegates to repo)."""
        return self.sessions.ensure_session_exists(
            session_id=session_id,
            ai_id=ai_id,
            project_id=project_id,
            instance_id=instance_id,
        )

    def heal_session_project_id(
        self,
        session_id: str,
        expected_project_id: str,
    ) -> str:
        """Validate-and-heal session.project_id against expected (delegates to repo).

        Returns "healed" | "ok" | "missing".
        """
        return self.sessions.heal_session_project_id(
            session_id=session_id,
            expected_project_id=expected_project_id,
        )

    def create_cascade(self, session_id: str, task: str, context: dict[str, Any],
                      goal_id: str | None = None, goal: dict[str, Any] | None = None) -> str:
        """Create cascade record (delegates to CascadeRepository)"""
        self._validate_session_id(session_id)
        return self.cascades.create_cascade(session_id, task, context, goal_id, goal)

    def update_cascade_phase(self, cascade_id: str, phase: str, completed: bool = True):
        """Mark cascade phase as completed (delegates to CascadeRepository)"""
        return self.cascades.update_cascade_phase(cascade_id, phase, completed)

    def complete_cascade(self, cascade_id: str, final_action: str, final_confidence: float,
                        investigation_rounds: int, duration_ms: int,
                        engagement_gate_passed: bool, bayesian_active: bool = False,
                        drift_monitored: bool = False):
        """Mark cascade as completed with final results (delegates to CascadeRepository)"""
        return self.cascades.complete_cascade(
            cascade_id, final_action, final_confidence, investigation_rounds,
            duration_ms, engagement_gate_passed, bayesian_active, drift_monitored
        )

    def log_epistemic_assessment(self, cascade_id: str, assessment: Any,
                                phase: str):
        """
        DEPRECATED: Use store_vectors() instead.

        This method is kept for backward compatibility with canonical structures.
        """
        if not CANONICAL_AVAILABLE:
            logger.warning("[DB] Canonical structures not available, skipping epistemic assessment")
            return

        # Extract vectors from assessment object
        vectors = {
            'engagement': assessment.engagement.score,
            'know': assessment.know.score,
            'do': assessment.do.score,
            'context': assessment.context.score,
            'clarity': assessment.clarity.score,
            'coherence': assessment.coherence.score,
            'signal': assessment.signal.score,
            'density': assessment.density.score,
            'state': assessment.state.score,
            'change': assessment.change.score,
            'completion': assessment.completion.score,
            'impact': assessment.impact.score,
            'uncertainty': assessment.uncertainty.score
        }

        # Build metadata from rationales
        metadata = {
            'assessment_id': assessment.assessment_id,
            'engagement_gate_passed': assessment.engagement_gate_passed,
            'foundation_confidence': assessment.foundation_confidence,
            'comprehension_confidence': assessment.comprehension_confidence,
            'execution_confidence': assessment.execution_confidence,
            'overall_confidence': assessment.overall_confidence,
            'recommended_action': assessment.recommended_action.value
        }

        # Get session_id from cascade
        cursor = self.conn.cursor()
        cursor.execute("SELECT session_id FROM cascades WHERE cascade_id = ?", (cascade_id,))
        row = cursor.fetchone()
        if not row:
            logger.error(f"Cascade {cascade_id} not found")
            return

        session_id = row[0]

        # Store using reflexes table
        self.store_vectors(
            session_id=session_id,
            phase=phase.upper(),
            vectors=vectors,
            cascade_id=cascade_id,
            metadata=metadata
        )

    def log_bayesian_belief(self, cascade_id: str, vector_name: str, mean: float,
                           variance: float, evidence_count: int,
                           prior_mean: float, prior_variance: float):
        """Track Bayesian belief updates"""
        belief_id = str(uuid.uuid4())

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO bayesian_beliefs (
                belief_id, cascade_id, vector_name,
                mean, variance, evidence_count,
                prior_mean, prior_variance, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            belief_id, cascade_id, vector_name,
            mean, variance, evidence_count,
            prior_mean, prior_variance, datetime.now()
        ))

        self.conn.commit()

    def get_session(self, session_id: str) -> dict | None:
        """Get session data (delegates to SessionRepository)"""
        return self.sessions.get_session(session_id)

    def get_session_cascades(self, session_id: str) -> list[dict]:
        """Get all cascades for a session (delegates to SessionRepository)"""
        return self.sessions.get_session_cascades(session_id)

    def get_cascade_assessments(self, cascade_id: str) -> list[dict]:
        """
        DEPRECATED: Use reflexes table queries instead.

        Get all assessments for a cascade from reflexes table.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM reflexes WHERE cascade_id = ? ORDER BY timestamp
        """, (cascade_id,))
        return [dict(row) for row in cursor.fetchall()]

    def log_preflight_assessment(self, session_id: str, cascade_id: str | None,
                                 prompt_summary: str, vectors: dict[str, float],
                                 uncertainty_notes: str = "") -> int:
        """
        DEPRECATED: Use store_vectors() instead.

        This method redirects to store_vectors() for backward compatibility.
        """
        # Store metadata in reflex_data
        metadata = {
            "prompt_summary": prompt_summary,
            "uncertainty_notes": uncertainty_notes
        }

        return self.store_vectors(
            session_id=session_id,
            phase="PREFLIGHT",
            vectors=vectors,
            cascade_id=cascade_id,
            metadata=metadata,
            reasoning=uncertainty_notes
        )

    def log_check_phase_assessment(self, session_id: str, cascade_id: str | None,
                                   investigation_cycle: int, confidence: float,
                                   decision: str, gaps: list[str],
                                   next_targets: list[str],
                                   notes: str = "",
                                   vectors: dict[str, float] | None = None,
                                   findings: list[str] | None = None,
                                   remaining_unknowns: list[str] | None = None) -> int:
        """
        DEPRECATED: Use store_vectors() instead.

        This method redirects to store_vectors() for backward compatibility.
        """
        # Store CHECK-specific data in metadata
        metadata = {
            "decision": decision,
            "confidence": confidence,
            "gaps_identified": gaps,
            "next_investigation_targets": next_targets,
            "findings": findings,
            "remaining_unknowns": remaining_unknowns
        }

        # If vectors provided, use them; otherwise create minimal vector with uncertainty
        if not vectors:
            vectors = {"uncertainty": 1.0 - confidence}

        return self.store_vectors(
            session_id=session_id,
            phase="CHECK",
            vectors=vectors,
            cascade_id=cascade_id,
            round_num=investigation_cycle,
            metadata=metadata,
            reasoning=notes
        )

    def log_postflight_assessment(self, session_id: str, cascade_id: str | None,
                                  task_summary: str, vectors: dict[str, float],
                                  postflight_confidence: float,
                                  calibration_accuracy: str,
                                  learning_notes: str = "") -> int:
        """
        DEPRECATED: Use store_vectors() instead.

        This method redirects to store_vectors() for backward compatibility.
        """
        # Store postflight-specific data in metadata
        metadata = {
            "task_summary": task_summary,
            "postflight_confidence": postflight_confidence,
            "calibration_accuracy": calibration_accuracy
        }

        return self.store_vectors(
            session_id=session_id,
            phase="POSTFLIGHT",
            vectors=vectors,
            cascade_id=cascade_id,
            metadata=metadata,
            reasoning=learning_notes
        )

    def get_preflight_assessment(self, session_id: str) -> dict | None:
        """
        DEPRECATED: Use get_latest_vectors(session_id, phase='PREFLIGHT') instead.

        This method redirects to reflexes table for backward compatibility.
        """
        return self.get_latest_vectors(session_id, phase="PREFLIGHT")

    def get_check_phase_assessments(self, session_id: str) -> list[dict]:
        """
        DEPRECATED: Use get_vectors_by_phase(session_id, phase='CHECK') instead.

        This method redirects to reflexes table for backward compatibility.
        """
        return self.get_vectors_by_phase(session_id, phase="CHECK")

    def get_postflight_assessment(self, session_id: str) -> dict | None:
        """
        DEPRECATED: Use get_latest_vectors(session_id, phase='POSTFLIGHT') instead.

        This method redirects to reflexes table for backward compatibility.
        """
        return self.get_latest_vectors(session_id, phase="POSTFLIGHT")

    def get_preflight_vectors(self, session_id: str) -> dict | None:
        """Get latest PREFLIGHT vectors for session (delegates to VectorRepository)"""
        return self.vectors.get_preflight_vectors(session_id)

    def get_check_vectors(self, session_id: str, cycle: int | None = None) -> list[dict]:
        """Get CHECK phase vectors (delegates to VectorRepository)"""
        return self.vectors.get_check_vectors(session_id, cycle)

    def get_postflight_vectors(self, session_id: str) -> dict | None:
        """Get latest POSTFLIGHT vectors for session (delegates to VectorRepository)"""
        return self.vectors.get_postflight_vectors(session_id)

    def get_vectors_by_phase(self, session_id: str, phase: str) -> list[dict]:
        """Get all vectors for a specific phase (delegates to VectorRepository)"""
        return self.vectors.get_vectors_by_phase(session_id, phase)

    def store_epistemic_delta(self, cascade_id: str, delta: dict[str, float]):
        """Store epistemic delta for calibration tracking (delegates to CascadeRepository)"""
        return self.cascades.store_epistemic_delta(cascade_id, delta)

    def get_last_session_by_ai(self, ai_id: str) -> dict | None:
        """Get most recent session for an AI agent"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM sessions
            WHERE ai_id = ?
            ORDER BY start_time DESC
            LIMIT 1
        """, (ai_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_session_snapshot(self, session_id: str) -> dict | None:
        """
        Get git-native session snapshot showing where you left off

        Args:
            session_id: Session identifier

        Returns:
            Dictionary with git state, epistemic trajectory, learning delta, goals, sources
        """
        import subprocess

        session = self.get_session(session_id)
        if not session:
            return None

        # Get git state
        git_state = {}
        try:
            # Current branch
            branch = subprocess.run(['git', 'branch', '--show-current'],
                                   capture_output=True, text=True, check=True)
            git_state['branch'] = branch.stdout.strip()

            # Current commit
            commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'],
                                   capture_output=True, text=True, check=True)
            git_state['commit'] = commit.stdout.strip()

            # Last 5 commits
            log = subprocess.run(['git', 'log', '--oneline', '-5'],
                                capture_output=True, text=True, check=True)
            git_state['last_5_commits'] = log.stdout.strip().split('\n')

            # Diff stat (lightweight)
            diff_stat = subprocess.run(['git', 'diff', '--stat', 'HEAD'],
                                      capture_output=True, text=True, check=True)
            git_state['diff_stat'] = diff_stat.stdout.strip() if diff_stat.stdout.strip() else "No uncommitted changes"

        except subprocess.CalledProcessError as e:
            git_state['error'] = f"Git error: {e}"

        # Get epistemic trajectory (PREFLIGHT → CHECK → POSTFLIGHT)
        trajectory = {}

        # PREFLIGHT
        preflight = self.get_latest_vectors(session_id, phase='PREFLIGHT')
        if preflight:
            trajectory['preflight'] = preflight['vectors']

        # CHECK gates (all of them)
        check_gates = self.get_vectors_by_phase(session_id, phase='CHECK')
        if check_gates:
            trajectory['check_gates'] = [c['vectors'] for c in check_gates]

        # POSTFLIGHT
        postflight = self.get_latest_vectors(session_id, phase='POSTFLIGHT')
        if postflight:
            trajectory['postflight'] = postflight['vectors']

        # Calculate learning delta
        learning_delta = {}
        if preflight and postflight:
            pre_vectors = preflight['vectors']
            post_vectors = postflight['vectors']
            for key in post_vectors:
                if key in pre_vectors:
                    if pre_vectors[key] is not None and post_vectors[key] is not None:
                        learning_delta[key] = round(post_vectors[key] - pre_vectors[key], 3)

        # Get active goals
        active_goals = []
        goal_tree = self.get_goal_tree(session_id)
        for goal in goal_tree:
            if goal.get('status') != 'completed':
                active_goals.append({
                    'id': goal['goal_id'],
                    'objective': goal['objective'],
                    'progress': f"{goal.get('completed_subtasks', 0)}/{goal.get('total_subtasks', 0)}"
                })

        # Get sources referenced in this session
        project_id = session.get('project_id')
        sources_referenced = []
        if project_id:
            sources = self.get_epistemic_sources(project_id, session_id=session_id, limit=10)
            sources_referenced = [{
                'title': s['title'],
                'type': s['source_type'],
                'confidence': s['confidence'],
                'url': s.get('source_url')
            } for s in sources]

        # Get findings, unknowns, mistakes, dead-ends for this session (DUAL-SCOPE)
        findings = []
        unknowns = []
        mistakes = []
        dead_ends = []

        cursor_local = self.conn.cursor()

        # Get findings for this session (project_findings is canonical)
        cursor_local.execute("""
            SELECT id, finding, impact, created_timestamp
            FROM project_findings WHERE session_id = ?
            ORDER BY created_timestamp DESC
        """, (session_id,))
        findings = [{'id': row[0], 'finding': row[1], 'impact': row[2], 'timestamp': row[3]}
                   for row in cursor_local.fetchall()]

        # Get unknowns for this session
        cursor_local.execute("""
            SELECT id, unknown, is_resolved, created_timestamp
            FROM project_unknowns WHERE session_id = ?
            ORDER BY created_timestamp DESC
        """, (session_id,))
        unknowns = [{'id': row[0], 'unknown': row[1], 'resolved': row[2], 'timestamp': row[3]}
                   for row in cursor_local.fetchall()]

        # Get mistakes for this session
        cursor_local.execute("""
            SELECT id, mistake, cost_estimate, created_timestamp
            FROM mistakes_made WHERE session_id = ?
            ORDER BY created_timestamp DESC
        """, (session_id,))
        mistakes = [{'id': row[0], 'mistake': row[1], 'cost': row[2], 'timestamp': row[3]}
                   for row in cursor_local.fetchall()]

        # Get dead-ends for this session
        cursor_local.execute("""
            SELECT id, approach, why_failed, created_timestamp
            FROM project_dead_ends WHERE session_id = ?
            ORDER BY created_timestamp DESC
        """, (session_id,))
        dead_ends = [{'id': row[0], 'approach': row[1], 'why_failed': row[2], 'timestamp': row[3]}
                    for row in cursor_local.fetchall()]

        return {
            'session_id': session_id,
            'ai_id': session['ai_id'],
            'git_state': git_state,
            'epistemic_trajectory': trajectory,
            'learning_delta': learning_delta,
            'active_goals': active_goals,
            'sources_referenced': sources_referenced,
            'findings': findings,
            'unknowns': unknowns,
            'mistakes': mistakes,
            'dead_ends': dead_ends,
            'subject': session.get('subject')
        }

    def get_session_summary(self, session_id: str, detail_level: str = "summary") -> dict | None:
        """Generate session summary (delegates to SessionRepository)"""
        return self.sessions.get_session_summary(session_id, detail_level)

    def calculate_flow_metrics(self, project_id: str, limit: int = 5) -> dict:
        """Calculate flow state metrics (delegates to MetricsRepository)"""
        return self.metrics.calculate_flow_metrics(project_id, limit)

    def calculate_health_score(self, project_id: str, limit: int = 5) -> dict:
        """Calculate epistemic health score (delegates to MetricsRepository)"""
        return self.metrics.calculate_health_score(project_id, limit)

    def _load_feature_status(self, project_root: str) -> dict | None:
        """
        Load feature completion status from FEATURE_STATUS.md.

        Parses the markdown file to extract:
        - Completed features count
        - In-progress goals count
        - Completion percentage

        Args:
            project_root: Path to project root

        Returns:
            Dict with feature completion metrics or None if file not found
        """
        import re
        from pathlib import Path

        feature_file = Path(project_root) / "docs" / "FEATURE_STATUS.md"
        if not feature_file.exists():
            return None

        try:
            content = feature_file.read_text()

            # Count completed features (lines with ✅ COMPLETE)
            completed = len(re.findall(r'✅ COMPLETE', content))

            # Count in-progress goals (lines starting with - ` in IN-PROGRESS section)
            in_progress_match = re.search(r'In-Progress Goals \((\d+)\)', content)
            in_progress = int(in_progress_match.group(1)) if in_progress_match else 0

            # Count assigned goals
            assigned_match = re.search(r'Assigned to Rovo \((\d+)\)', content)
            assigned = int(assigned_match.group(1)) if assigned_match else 0

            total = completed + in_progress
            completion_pct = (completed / total * 100) if total > 0 else 0

            return {
                'completed': completed,
                'in_progress': in_progress,
                'assigned': assigned,
                'total': total,
                'completion_percentage': round(completion_pct, 1),
                'status': 'healthy' if completion_pct >= 50 else 'developing'
            }
        except Exception as e:
            logger.debug(f"Failed to parse FEATURE_STATUS.md: {e}")
            return None

    def get_git_checkpoint(self, session_id: str, phase: str | None = None) -> dict | None:
        """
        Retrieve checkpoint from git notes with SQLite fallback (Phase 2).

        Priority:
        1. Try git notes first (via GitEnhancedReflexLogger)
        2. Fall back to SQLite reflexes if git unavailable

        Args:
            session_id: Session identifier
            phase: Optional phase filter (PREFLIGHT, CHECK, POSTFLIGHT)

        Returns:
            Checkpoint dict or None if not found
        """
        try:
            from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger

            git_logger = GitEnhancedReflexLogger(session_id=session_id, enable_git_notes=True)

            if git_logger.git_available:
                checkpoint = git_logger.get_last_checkpoint(phase=phase)
                if checkpoint:
                    logger.debug(f"✅ Loaded git checkpoint for session {session_id}")
                    return checkpoint
        except Exception as e:
            logger.debug(f"Git checkpoint retrieval failed, using SQLite fallback: {e}")

        # Fallback to SQLite reflexes
        return self._get_checkpoint_from_reflexes(session_id, phase)

    def list_git_checkpoints(self, session_id: str, limit: int = 10, phase: str | None = None) -> list[dict]:
        """
        List all checkpoints for session from git notes (Phase 2).

        Args:
            session_id: Session identifier
            limit: Maximum number of checkpoints to return
            phase: Optional phase filter

        Returns:
            List of checkpoint dicts
        """
        try:
            from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger

            git_logger = GitEnhancedReflexLogger(session_id=session_id, enable_git_notes=True)

            if git_logger.git_available:
                checkpoints = git_logger.list_checkpoints(limit=limit, phase=phase)
                logger.debug(f"✅ Listed {len(checkpoints)} git checkpoints for session {session_id}")
                return checkpoints
        except Exception as e:
            logger.warning(f"Git checkpoint listing failed: {e}")

        # Fallback: return empty list (SQLite doesn't have checkpoint history in same format)
        return []

    def get_checkpoint_diff(self, session_id: str, threshold: float = 0.15) -> dict:
        """
        Calculate vector differences between current state and last checkpoint (Phase 2).

        Args:
            session_id: Session identifier
            threshold: Significance threshold for reporting changes

        Returns:
            Dict with vector diffs and significant changes
        """
        from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger

        git_logger = GitEnhancedReflexLogger(session_id=session_id, enable_git_notes=True)

        last_checkpoint = git_logger.get_last_checkpoint()
        if not last_checkpoint:
            return {"error": "No checkpoint found for comparison"}

        # Get current state from latest assessment
        current_state = self.get_latest_vectors(session_id)

        if not current_state:
            return {"error": "No current state found"}

        current_vectors = current_state.get('vectors', {})

        # Calculate diffs
        diffs = {}
        significant_changes = []

        checkpoint_vectors = last_checkpoint.get('vectors', {})

        for key in current_vectors:
            old_val = checkpoint_vectors.get(key, 0.5)
            new_val = current_vectors[key]
            if old_val is not None and new_val is not None:
                diff = new_val - old_val
            else:
                diff = 0.0

            diffs[key] = {
                'old': old_val,
                'new': new_val,
                'diff': diff,
                'abs_diff': abs(diff)
            }

            if abs(diff) >= threshold:
                significant_changes.append({
                    'vector': key,
                    'change': diff,
                    'direction': 'increased' if diff > 0 else 'decreased'
                })

        return {
            'checkpoint_id': last_checkpoint.get('checkpoint_id'),
            'checkpoint_phase': last_checkpoint.get('phase'),
            'checkpoint_timestamp': last_checkpoint.get('timestamp'),
            'diffs': diffs,
            'significant_changes': significant_changes,
            'threshold': threshold
        }

    def _get_checkpoint_from_reflexes(self, session_id: str, phase: str | None = None) -> dict | None:
        """SQLite fallback for checkpoint retrieval using reflexes table"""
        cursor = self.conn.cursor()

        # Query reflexes table (unified storage)
        query = """
            SELECT
                id,
                phase,
                engagement,
                know, do, context,
                clarity, coherence, signal, density,
                state, change, completion, impact,
                uncertainty,
                timestamp,
                reasoning
            FROM reflexes
            WHERE session_id = ?
        """
        params = [session_id]

        if phase:
            query += " AND phase = ?"
            params.append(phase.upper())

        query += " ORDER BY timestamp DESC LIMIT 1"

        cursor.execute(query, params)
        result = cursor.fetchone()

        if result:
            # Build vectors dict from individual columns
            vectors = {
                "engagement": result['engagement'],
                "foundation": {
                    "know": result['know'],
                    "do": result['do'],
                    "context": result['context']
                },
                "comprehension": {
                    "clarity": result['clarity'],
                    "coherence": result['coherence'],
                    "signal": result['signal'],
                    "density": result['density']
                },
                "execution": {
                    "state": result['state'],
                    "change": result['change'],
                    "completion": result['completion'],
                    "impact": result['impact']
                },
                "uncertainty": result['uncertainty']
            }

            return {
                "checkpoint_id": str(result['id']),  # Use id as checkpoint_id
                "vectors": vectors,
                "phase": result['phase'],
                "timestamp": result['timestamp'],
                "reasoning": result['reasoning'],
                "round": 0,  # SQLite doesn't track rounds in checkpoint
                "source": "sqlite_fallback",
                "token_count": None  # Not tracked in SQLite
            }

        return None

    def store_vectors(self, session_id: str, phase: str, vectors: dict[str, float], cascade_id: str | None = None, round_num: int = 1, metadata: dict | None = None, reasoning: str | None = None, transaction_id: str | None = None):
        """Store epistemic vectors (delegates to VectorRepository)"""
        return self.vectors.store_vectors(session_id, phase, vectors, cascade_id, round_num, metadata, reasoning, transaction_id=transaction_id)

    def get_latest_vectors(self, session_id: str, phase: str | None = None) -> dict | None:
        """Get latest epistemic vectors for session (delegates to VectorRepository)"""
        return self.vectors.get_latest_vectors(session_id, phase)

    # =========================================================================
    # Goal and Subtask Management (for decision quality + continuity + audit)
    # =========================================================================

    def create_goal(self, session_id: str, objective: str, scope_breadth: float | None = None,
                   scope_duration: float | None = None, scope_coordination: float | None = None) -> str:
        """Create a new goal for this session (delegates to GoalRepository)

        Args:
            session_id: Session UUID
            objective: What are you trying to accomplish?
            scope_breadth: 0.0-1.0 (0=single file, 1=entire codebase)
            scope_duration: 0.0-1.0 (0=minutes, 1=months)
            scope_coordination: 0.0-1.0 (0=solo, 1=heavy multi-agent)

        Returns:
            goal_id (UUID string)
        """
        return self.goals.create_goal(session_id, objective, scope_breadth,
                                      scope_duration, scope_coordination)

    def create_subtask(self, goal_id: str, description: str, importance: str = 'medium') -> str:
        """Create a subtask within a goal (delegates to GoalRepository)

        Args:
            goal_id: Parent goal UUID
            description: What are you investigating/implementing?
            importance: 'critical' | 'high' | 'medium' | 'low'

        Returns:
            subtask_id (UUID string)
        """
        return self.goals.create_subtask(goal_id, description, importance)

    def update_subtask_findings(self, subtask_id: str, findings: list[str]):
        """Update findings for a subtask (delegates to GoalRepository)

        Args:
            subtask_id: Subtask UUID
            findings: List of finding strings
        """
        return self.goals.update_subtask_findings(subtask_id, findings)

    def update_subtask_unknowns(self, subtask_id: str, unknowns: list[str]):
        """Update unknowns for a subtask (delegates to GoalRepository)

        Args:
            subtask_id: Subtask UUID
            unknowns: List of unknown strings
        """
        return self.goals.update_subtask_unknowns(subtask_id, unknowns)

    def update_subtask_dead_ends(self, subtask_id: str, dead_ends: list[str]):
        """Update dead ends for a subtask (delegates to GoalRepository)

        Args:
            subtask_id: Subtask UUID
            dead_ends: List of dead end strings (e.g., "Attempted X - blocked by Y")
        """
        return self.goals.update_subtask_dead_ends(subtask_id, dead_ends)

    def complete_subtask(self, subtask_id: str, evidence: str):
        """Mark subtask as completed with evidence (delegates to GoalRepository)

        Args:
            subtask_id: Subtask UUID
            evidence: Evidence of completion (e.g., "Documented in design doc", "PR merged")
        """
        return self.goals.complete_subtask(subtask_id, evidence)

    def get_all_sessions(self, ai_id: str | None = None, limit: int = 50) -> list[dict]:
        """List all sessions, optionally filtered by ai_id (delegates to SessionRepository)

        Args:
            ai_id: Optional AI identifier to filter by
            limit: Maximum number of sessions to return (default 50)

        Returns:
            List of session dictionaries
        """
        return self.sessions.get_all_sessions(ai_id, limit)

    def get_goal_tree(self, session_id: str) -> list[dict]:
        """Get complete goal tree for a session (delegates to GoalRepository)

        Returns list of goals with nested subtasks

        Args:
            session_id: Session UUID

        Returns:
            List of goal dicts, each with 'subtasks' list
        """
        return self.goals.get_goal_tree(session_id)

    def query_unknowns_summary(self, session_id: str) -> dict:
        """Get summary of all unknowns in a session (delegates to GoalRepository)

        Args:
            session_id: Session UUID

        Returns:
            Dict with total_unknowns count and breakdown by goal
        """
        return self.goals.query_unknowns_summary(session_id)

    def query_goals(self, session_id: str | None = None, is_completed: bool | None = None) -> list:
        """Query goals with optional filters (delegates to core GoalsRepository)

        Args:
            session_id: Optional session UUID to filter by
            is_completed: Optional completion status filter (True/False/None for all)

        Returns:
            List of Goal objects matching filters
        """
        return self.core_goals.query_goals(
            session_id=session_id,
            is_completed=is_completed
        )

    def query_subtasks(self, goal_id: str | None = None, status: str | None = None) -> list:
        """Query subtasks with optional filters (delegates to TaskRepository)

        Args:
            goal_id: Optional goal UUID to filter by
            status: Optional status filter (string like 'pending', 'completed')

        Returns:
            List of SubTask objects matching filters
        """
        # Convert status string to TaskStatus enum if provided
        task_status = None
        if status:
            from empirica.core.tasks.types import TaskStatus
            try:
                task_status = TaskStatus(status)
            except ValueError:
                pass  # Invalid status, ignore

        return self.tasks.query_subtasks(
            goal_id=goal_id,
            status=task_status
        )

    def get_goal_subtasks(self, goal_id: str) -> list:
        """Get all subtasks for a specific goal (delegates to TaskRepository)

        Args:
            goal_id: Goal UUID

        Returns:
            List of SubTask objects for this goal
        """
        return self.tasks.get_goal_subtasks(goal_id)

    def query_goal_progress(self, goal_id: str) -> dict:
        """Get goal completion statistics

        Args:
            goal_id: Goal UUID

        Returns:
            Dict with total, completed, remaining counts and percentage
        """
        subtasks = self.tasks.get_goal_subtasks(goal_id)
        total = len(subtasks)
        completed = sum(1 for st in subtasks if st.status.value == "completed")

        return {
            "goal_id": goal_id,
            "total": total,
            "completed": completed,
            "remaining": total - completed,
            "percentage": round((completed / total * 100), 1) if total > 0 else 0.0
        }

    # ========== Investigation Branches (Epistemic Auto-Merge) ==========

    def create_branch(self, session_id: str, branch_name: str, investigation_path: str,
                     git_branch_name: str, preflight_vectors: dict) -> str:
        """Create a new investigation branch (delegates to BranchRepository)

        Args:
            session_id: Session UUID
            branch_name: Human-readable branch name
            investigation_path: What is being investigated (e.g., 'oauth2')
            git_branch_name: Git branch name
            preflight_vectors: Epistemic vectors at branch start

        Returns:
            Branch ID
        """
        return self.branches.create_branch(session_id, branch_name, investigation_path,
                                           git_branch_name, preflight_vectors)

    def checkpoint_branch(self, branch_id: str, postflight_vectors: dict,
                         tokens_spent: int, time_spent_minutes: int) -> bool:
        """Checkpoint a branch after investigation (delegates to BranchRepository)

        Args:
            branch_id: Branch ID
            postflight_vectors: Epistemic vectors after investigation
            tokens_spent: Tokens used in investigation
            time_spent_minutes: Time spent in investigation

        Returns:
            Success boolean
        """
        return self.branches.checkpoint_branch(branch_id, postflight_vectors,
                                               tokens_spent, time_spent_minutes)

    def calculate_branch_merge_score(self, branch_id: str) -> dict:
        """Calculate epistemic merge score for a branch (delegates to BranchRepository)

        Score = (learning_delta × quality × confidence) / cost_penalty
        Where: confidence = 1 - uncertainty (uncertainty is a DAMPENER)

        Returns:
            Dict with merge_score, quality, and rationale
        """
        return self.branches.calculate_branch_merge_score(branch_id)

    def merge_branches(self, session_id: str, investigation_round: int = 1) -> dict:
        """Auto-merge best branch based on epistemic scores (delegates to BranchRepository)

        Returns:
            Dict with winning_branch_id, merge_decision_id, rationale
        """
        return self.branches.merge_branches(session_id, investigation_round)

    def create_project(
        self,
        name: str,
        description: str | None = None,
        repos: list[str] | None = None,
        project_type: str | None = None,
        project_tags: list[str] | None = None,
        parent_project_id: str | None = None
    ) -> str:
        """Create a new project (delegates to ProjectRepository)

        Args:
            name: Project name (e.g., "Empirica Core")
            description: Project description
            repos: List of repository names (e.g., ["empirica", "empirica-dev"])
            project_type: Category (product, application, feature, research, documentation, infrastructure, operations)
            project_tags: List of tags for flexible categorization
            parent_project_id: Optional parent project for hierarchy

        Returns:
            project_id: UUID string
        """
        return self.projects.create_project(
            name, description, repos,
            project_type=project_type,
            project_tags=project_tags,
            parent_project_id=parent_project_id
        )

    def get_project(self, project_id: str) -> dict | None:
        """Get project data (delegates to ProjectRepository)"""
        return self.projects.get_project(project_id)

    def resolve_project_id(self, project_id_or_name: str) -> str | None:
        """Resolve project name or UUID to UUID (delegates to ProjectRepository)"""
        return self.projects.resolve_project_id(project_id_or_name)

    def link_session_to_project(self, session_id: str, project_id: str):
        """Link a session to a project (delegates to ProjectRepository)"""
        self._validate_session_id(session_id)
        return self.projects.link_session_to_project(session_id, project_id)

    def get_project_sessions(self, project_id: str) -> list[dict]:
        """Get all sessions for a project (delegates to ProjectRepository)"""
        return self.projects.get_project_sessions(project_id)

    def aggregate_project_learning_deltas(self, project_id: str) -> dict[str, float]:
        """Compute total epistemic learning across all project sessions (delegates to ProjectRepository)"""
        return self.projects.aggregate_project_learning_deltas(project_id)

    def create_project_handoff(
        self,
        project_id: str,
        project_summary: str,
        key_decisions: list[str] | None = None,
        patterns_discovered: list[str] | None = None,
        remaining_work: list[str] | None = None
    ) -> str:
        """Create project-level handoff report (delegates to ProjectRepository)"""
        return self.projects.create_project_handoff(
            project_id, project_summary, key_decisions, patterns_discovered, remaining_work
        )

    def get_latest_project_handoff(self, project_id: str) -> dict | None:
        """Get the most recent project handoff (delegates to ProjectRepository)"""
        return self.projects.get_latest_project_handoff(project_id)

    def get_ai_epistemic_handoff(self, project_id: str, ai_id: str) -> dict | None:
        """Get latest epistemic handoff (POSTFLIGHT checkpoint) for a specific AI.

        Enables epistemic continuity by loading the previous session's ending epistemic state.
        """
        return self.projects.get_ai_epistemic_handoff(project_id, ai_id)

    def get_auto_captured_issues(self, project_id: str, limit: int = 10) -> list[dict]:
        """Get auto-captured issues for project.

        Returns list of issues sorted by severity and recency.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT id, session_id, severity, category, message, status, created_at, code_location
            FROM auto_captured_issues
            WHERE session_id IN (
                SELECT session_id FROM sessions WHERE project_id = ?
            )
            AND status NOT IN ('resolved', 'wontfix')
            ORDER BY
                CASE severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                             WHEN 'medium' THEN 3 WHEN 'low' THEN 4 END,
                created_at DESC
            LIMIT ?
        """, (project_id, limit))

        rows = cursor.fetchall()
        issues = []
        for row in rows:
            issues.append({
                'id': row[0],
                'session_id': row[1],
                'severity': row[2],
                'category': row[3],
                'message': row[4],
                'status': row[5],
                'created_at': row[6],
                'code_location': row[7]
            })

        # Deduplicate by message (same error may occur in multiple sessions)
        seen_messages = set()
        unique_issues = []
        for issue in issues:
            msg = issue.get('message', '')
            if msg not in seen_messages:
                seen_messages.add(msg)
                unique_issues.append(issue)

        return unique_issues

    def get_git_status(self, project_root: str) -> dict | None:
        """Get git status information for the project.

        Returns dict with:
        - current_branch: Current branch name
        - commits_ahead: Number of commits ahead of remote
        - uncommitted_changes: Number of uncommitted changes
        - untracked_files: Number of untracked files
        - recent_commits: List of recent commit messages
        """
        import subprocess

        try:
            # Get current branch
            branch_result = subprocess.run(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=2
            )
            current_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else 'unknown'

            # Get status short summary
            status_result = subprocess.run(
                ['git', 'status', '--short'],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=2
            )
            uncommitted = len(status_result.stdout.strip().split('\n')) if status_result.returncode == 0 and status_result.stdout.strip() else 0

            # Get untracked files
            untracked_result = subprocess.run(
                ['git', 'ls-files', '--others', '--exclude-standard'],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=2
            )
            untracked = len(untracked_result.stdout.strip().split('\n')) if untracked_result.returncode == 0 and untracked_result.stdout.strip() else 0

            # Get recent commits (last 3)
            commits_result = subprocess.run(
                ['git', 'log', '--oneline', '-3'],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=2
            )
            recent_commits = commits_result.stdout.strip().split('\n') if commits_result.returncode == 0 and commits_result.stdout.strip() else []

            return {
                'current_branch': current_branch,
                'uncommitted_changes': uncommitted,
                'untracked_files': untracked,
                'recent_commits': recent_commits
            }
        except Exception as e:
            logger.debug(f"Error getting git status: {e}")
            return None

    @staticmethod
    def _analyze_py_file_imports(py_file, root_path, package_prefixes, reverse_graph, external_deps, entry_points):
        """Analyze a single Python file for imports and entry points."""
        import ast
        try:
            file_content = py_file.read_text(errors='replace')
            tree = ast.parse(file_content)
        except (SyntaxError, UnicodeDecodeError):
            return
        try:
            rel_parts = list(py_file.relative_to(root_path).parts)
            if rel_parts[-1] == "__init__.py":
                rel_parts = rel_parts[:-1]
            else:
                rel_parts[-1] = rel_parts[-1].replace(".py", "")
            module_name = ".".join(rel_parts)
        except ValueError:
            return
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split('.')[0]
                    if top in package_prefixes:
                        reverse_graph[alias.name].add(module_name)
                    else:
                        external_deps.add(top)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split('.')[0]
                if top in package_prefixes:
                    reverse_graph[node.module].add(module_name)
                elif node.level == 0:
                    external_deps.add(top)
        for node in ast.walk(tree):
            if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and len(node.test.comparators) == 1):
                left = node.test.left
                comp = node.test.comparators[0]
                if isinstance(left, ast.Name) and left.id == '__name__' and isinstance(comp, ast.Constant) and comp.value == '__main__':
                    entry_points.append(module_name)
                    break

    def _generate_dependency_summary(self, project_root: str) -> dict | None:
        """Generate lightweight dependency summary using AST import analysis.

        Produces a compact summary of project dependencies suitable for context
        injection. Replaces the old file tree with semantically useful information.

        Uses 5-minute cache since import structure changes less often than files.

        Returns dict with:
          - packages: Top-level Python packages found
          - hotspots: Modules with highest coupling (most importers)
          - external_deps: Third-party imports
          - entry_points: CLI/main modules
          - module_count: Total .py files analyzed
        """
        import json
        import time
        from collections import defaultdict
        from pathlib import Path

        root_path = Path(project_root)

        # Belt-and-suspenders: refuse to mkdir .empirica/ under a path that
        # isn't a real project root. Prevents stray .empirica/cache/ in subdirs
        # when a future caller passes a non-canonical project_root.
        if not ((root_path / ".empirica" / "project.yaml").exists() or
                (root_path / ".empirica" / "sessions" / "sessions.db").exists()):
            return None

        # Check cache (5-minute TTL)
        cache_dir = root_path / ".empirica" / "cache"
        cache_file = cache_dir / f"dep_graph_{root_path.name}.json"
        if cache_file.exists():
            try:
                age = time.time() - cache_file.stat().st_mtime
                if age < 300:  # 5 minutes
                    return json.loads(cache_file.read_text())
            except Exception:
                pass

        skip_exact = {'.git', '__pycache__', 'dist', 'build',
                      'node_modules', '.empirica', '.qdrant_data', '.tox', '.mypy_cache'}
        skip_prefixes = ('.venv', 'venv', '.eggs')

        def _should_skip(part: str) -> bool:
            return part in skip_exact or part.startswith(skip_prefixes)

        # Discover top-level packages (dirs with __init__.py)
        packages = []
        for d in sorted(root_path.iterdir()):
            if d.is_dir() and not _should_skip(d.name) and not d.name.startswith('.'):
                if (d / '__init__.py').exists():
                    packages.append(d.name)

        package_prefixes = set(packages)

        # Walk .py files and parse imports
        reverse_graph = defaultdict(set)  # module -> set of importers
        external_deps = set()
        entry_points = []
        module_count = 0
        all_module_basenames = set()  # Track all file basenames for filtering

        for py_file in root_path.rglob("*.py"):
            # Skip unwanted directories
            if any(_should_skip(part) for part in py_file.relative_to(root_path).parts):
                continue

            module_count += 1
            all_module_basenames.add(py_file.stem)

            self._analyze_py_file_imports(py_file, root_path, package_prefixes, reverse_graph, external_deps, entry_points)

        # Build hotspots (top 10 by importer count)
        hotspots = sorted(
            [{"module": mod, "importers": len(importers)}
             for mod, importers in reverse_graph.items()],
            key=lambda x: x["importers"],
            reverse=True
        )[:10]

        # Filter out stdlib, project-internal, and private modules
        import sys
        stdlib_modules = getattr(sys, 'stdlib_module_names', set())
        external_deps -= stdlib_modules
        external_deps -= package_prefixes
        external_deps -= all_module_basenames  # Remove local script imports
        external_deps = {d for d in external_deps
                         if not d.startswith('_')}

        result = {
            "packages": sorted(packages),
            "hotspots": hotspots,
            "external_deps": sorted(external_deps),
            "entry_points": sorted(entry_points),
            "module_count": module_count,
        }

        # Cache result
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(result, indent=2))
        except Exception:
            pass

        return result


    def _resolve_and_validate_project(self, project_id: str) -> dict | None:
        """Resolve project name to UUID and validate it exists"""
        resolved_id = self.resolve_project_id(project_id)
        if not resolved_id:
            return None

        project = self.get_project(resolved_id)
        return project

    def _load_breadcrumbs_for_mode(self, project_id: str, mode: str, subject: str | None = None) -> dict:
        """Load all breadcrumbs (findings, unknowns, dead_ends, mistakes, reference_docs) based on mode

        SEARCH-FIRST ARCHITECTURE: Limits items to prevent context bloat.
        Warns when items are truncated - AI should use Qdrant search for full context.
        """
        truncation_warnings = []

        # Define limits for session_start mode
        FINDINGS_LIMIT = 10
        UNKNOWNS_LIMIT = 10
        DEAD_ENDS_LIMIT = 5
        MISTAKES_LIMIT = 5

        if mode == "session_start":
            # FAST: Recent items only with limits
            findings = self.breadcrumbs.get_project_findings(project_id, limit=FINDINGS_LIMIT, subject=subject)

            # Get unknowns with limit, count total for warning
            all_unknowns = self.breadcrumbs.get_project_unknowns(project_id, resolved=False, subject=subject)
            total_unknowns = len(all_unknowns)
            unknowns = all_unknowns[:UNKNOWNS_LIMIT]
            if total_unknowns > UNKNOWNS_LIMIT:
                truncation_warnings.append(f"unknowns: showing {UNKNOWNS_LIMIT} of {total_unknowns} unresolved (use 'empirica project-search' for full list)")

            dead_ends = self.breadcrumbs.get_project_dead_ends(project_id, limit=DEAD_ENDS_LIMIT, subject=subject)
            recent_mistakes = self.breadcrumbs.get_project_mistakes(project_id, limit=MISTAKES_LIMIT)
        else:  # mode == "live"
            # COMPLETE: All items (no limits)
            findings = self.breadcrumbs.get_project_findings(project_id, subject=subject)
            unknowns = self.breadcrumbs.get_project_unknowns(project_id, subject=subject)
            dead_ends = self.breadcrumbs.get_project_dead_ends(project_id, subject=subject)
            recent_mistakes = self.breadcrumbs.get_project_mistakes(project_id)

        reference_docs = self.breadcrumbs.get_project_reference_docs(project_id)

        # Decisions are permanent audit trail — load from Qdrant (static, no decay)
        decisions = []
        DECISIONS_LIMIT = 10
        try:
            from empirica.core.qdrant.vector_store import (
                _check_qdrant_available,
                _decisions_collection,
                _get_qdrant_client,
            )
            if _check_qdrant_available():
                client = _get_qdrant_client()
                coll = _decisions_collection(project_id)
                if client and client.collection_exists(coll):
                    results = client.scroll(collection_name=coll, limit=DECISIONS_LIMIT, with_payload=True)
                    for pt in results[0]:
                        p = pt.payload or {}
                        decisions.append({
                            'choice': p.get('choice', ''),
                            'rationale': p.get('rationale', ''),
                            'reversibility': p.get('reversibility', 'exploratory'),
                            'confidence': p.get('confidence_at_decision'),
                        })
        except Exception:
            pass  # Qdrant unavailable — decisions won't load (non-fatal)

        result = {
            'findings': findings,
            'unknowns': unknowns,
            'dead_ends': dead_ends,
            'decisions': decisions,
            'mistakes_to_avoid': recent_mistakes,
            'reference_docs': reference_docs
        }

        if truncation_warnings:
            result['truncation_warnings'] = truncation_warnings

        return result

    def _load_goals_for_project(self, project_id: str, limit: int = 10) -> dict:
        """Load goals for project with limit to prevent context bloat

        SEARCH-FIRST ARCHITECTURE: Limits goals shown, warns if truncated.
        """
        goals_data = self.goals.get_project_goals(project_id)

        # Apply limits to goals and incomplete_work
        truncation_warnings = []

        # Truncate 'goals' (active goals with subtask counts)
        if 'goals' in goals_data:
            total_goals = len(goals_data['goals'])
            if total_goals > limit:
                goals_data['goals'] = goals_data['goals'][:limit]
                truncation_warnings.append(f"goals: showing {limit} of {total_goals} (use 'empirica goals-list' for full list)")

        # Truncate 'incomplete_work'
        if 'incomplete_work' in goals_data:
            total_incomplete = len(goals_data['incomplete_work'])
            if total_incomplete > limit:
                goals_data['incomplete_work'] = goals_data['incomplete_work'][:limit]
                truncation_warnings.append(f"incomplete_work: showing {limit} of {total_incomplete}")

        if truncation_warnings:
            goals_data['truncation_warnings'] = truncation_warnings

        return goals_data

    def _capture_live_state_if_requested(
        self,
        session_id: str | None,
        project_id: str,
        include_live_state: bool,
        fresh_assess: bool,
        trigger: str | None
    ) -> dict | None:
        """Capture live epistemic state if requested"""
        if not include_live_state:
            return None

        # Auto-resolve session_id if not provided
        if not session_id:
            session_id = self._auto_resolve_session(project_id, trigger)

        if not session_id:
            return None

        # Capture or load live state
        if fresh_assess:
            result = self._capture_fresh_state(session_id, project_id)
        else:
            result = self._load_latest_checkpoint_state(session_id)

        # CRITICAL: Include session_id in result so bootstrap can use it
        if result:
            result['session_id'] = session_id

        return result


    def _build_situation(self, project_id: str, project_root: str) -> dict:
        """Compaction-recovery narrative.

        Delegates to `empirica.core.bootstrap.situation.build_situation` so
        both the CLI bootstrap (this caller) and the daemon HTTP route
        (`/api/v1/bootstrap` via `build_bootstrap_payload`) emit the same
        shape from the same code. See situation.py for field semantics.
        """
        from empirica.core.bootstrap.situation import build_situation
        return build_situation(project_root, project_id)

    def _count_project_artifacts(self, project_id: str) -> dict:
        """Live counts for a project — sessions + transactions + goals.

        The `projects.total_sessions` / `total_goals` columns are
        denormalized counters that were never wired to insert triggers
        and have drifted to 0. Until we either add triggers or drop the
        columns, callers that want accurate counts should use this helper
        instead of reading those columns directly.

        `total_transactions` is the more meaningful unit-of-work measure
        (sessions are compaction-window boundaries; transactions are
        actual PREFLIGHT→POSTFLIGHT cycles). Counted via distinct
        transaction_ids on reflexes — each transaction creates ≥1
        reflex row at PREFLIGHT.

        Cost: 3 indexed COUNT queries. ~3ms on a warm cache.
        """
        try:
            sessions_count = self._execute(
                "SELECT COUNT(*) FROM sessions WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
        except Exception:
            sessions_count = 0
        try:
            transactions_count = self._execute(
                "SELECT COUNT(DISTINCT transaction_id) FROM reflexes "
                "WHERE project_id = ? AND transaction_id IS NOT NULL",
                (project_id,),
            ).fetchone()[0]
        except Exception:
            transactions_count = 0
        try:
            # Goals are session-scoped — count via the sessions join
            goals_count = self._execute(
                "SELECT COUNT(*) FROM goals WHERE session_id IN "
                "(SELECT session_id FROM sessions WHERE project_id = ?)",
                (project_id,),
            ).fetchone()[0]
        except Exception:
            goals_count = 0
        return {
            'total_sessions': int(sessions_count),
            'total_transactions': int(transactions_count),
            'total_goals': int(goals_count),
        }

    def _execute(self, sql: str, params: tuple = ()):
        """Convenience wrapper for self.conn.cursor().execute() in this module."""
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return cur

    def _build_project_metadata(self, project: dict) -> dict:
        """Build project metadata dict from a resolved project row.

        Counter fields (`total_sessions`, `total_goals`) are populated
        from live queries via `_count_project_artifacts` since the
        stored denormalized columns are stale (never wired to insert
        triggers).
        """
        repos = project.get('repos', []) or []
        if isinstance(repos, str):
            try:
                repos = json.loads(repos)
            except Exception:
                repos = []

        counts = self._count_project_artifacts(project['id'])

        return {
            'id': project['id'],
            'name': project.get('name', 'Unknown'),
            'description': project.get('description', ''),
            'status': project.get('status', 'active'),
            'repos': repos,
            'total_sessions': counts['total_sessions'],
            'total_transactions': counts['total_transactions'],
            'total_goals': counts['total_goals'],
        }

    @staticmethod
    def _enrich_project_from_yaml(project_meta: dict) -> dict:
        """Enrich project metadata from project.yaml (v2.0 identity fields).

        Returns the enriched dict. Non-fatal — returns original on any error.
        """
        try:
            from empirica.config.project_config_loader import load_project_config
            project_config = load_project_config()
            if not project_config:
                return project_meta

            yaml_enrichment = {'type': project_config.type}
            for attr in ('domain', 'languages', 'tags', 'contacts', 'engagements', 'edges'):
                value = getattr(project_config, attr, None)
                if value:
                    yaml_enrichment[attr] = value
            if project_config.classification != 'internal':
                yaml_enrichment['classification'] = project_config.classification
            yaml_enrichment['evidence_profile'] = project_config.evidence_profile
            project_meta.update(yaml_enrichment)
        except Exception:
            pass  # project.yaml enrichment is non-fatal
        return project_meta

    def _attach_optional_metrics(self, breadcrumbs: dict, resolved_id: str, project_root: str) -> None:
        """Compute and attach optional metrics (flow, health, feature status) to breadcrumbs."""
        # Flow state metrics (AI productivity patterns)
        try:
            flow_metrics = self.calculate_flow_metrics(resolved_id, limit=5)
            if flow_metrics and flow_metrics.get('current_flow'):
                breadcrumbs['flow_metrics'] = flow_metrics
        except Exception as e:
            logger.debug(f"Flow metrics calculation skipped: {e}")

        # Health score (epistemic quality and completeness)
        try:
            health_score = self.calculate_health_score(resolved_id, limit=5)
            if health_score:
                breadcrumbs['health_score'] = health_score
        except Exception as e:
            logger.debug(f"Health score calculation skipped: {e}")

        # Feature status from FEATURE_STATUS.md
        try:
            feature_status = self._load_feature_status(project_root)
            if feature_status:
                breadcrumbs['feature_status'] = feature_status
                if 'health_score' in breadcrumbs:
                    breadcrumbs['health_score']['feature_completion'] = feature_status
        except Exception as e:
            logger.debug(f"Feature status load skipped: {e}")

    def _attach_supplementary_data(
        self,
        breadcrumbs: dict,
        resolved_id: str,
        project_root: str,
        session_id: str | None,
        include_live_state: bool,
        fresh_assess: bool,
        trigger: str | None,
    ) -> None:
        """Attach dependency graph, git status, issues, and live state to breadcrumbs."""
        dep_graph = self._generate_dependency_summary(project_root)
        if dep_graph:
            breadcrumbs['dependency_graph'] = dep_graph

        git_status = self.get_git_status(project_root)
        if git_status:
            breadcrumbs['git_status'] = git_status

        auto_issues = self.get_auto_captured_issues(resolved_id, limit=10)
        if auto_issues:
            breadcrumbs['auto_captured_issues'] = auto_issues

        live_state = self._capture_live_state_if_requested(
            session_id, resolved_id, include_live_state, fresh_assess, trigger
        )
        if live_state:
            breadcrumbs['live_state'] = live_state
            breadcrumbs['session_id'] = session_id or live_state.get('session_id')

    def bootstrap_project_breadcrumbs(
        self,
        project_id: str,
        mode: str = "session_start",
        project_root: str | None = None,
        check_integrity: bool = False,
        task_description: str | None = None,
        epistemic_state: dict[str, float] | None = None,
        context_to_inject: bool = False,
        subject: str | None = None,
        session_id: str | None = None,
        include_live_state: bool = False,
        fresh_assess: bool = False,
        trigger: str | None = None,
        depth: str = "auto",
        ai_id: str | None = None
    ) -> dict:
        """
        Generate epistemic breadcrumbs for starting a new session on existing project.

        Args:
            project_id: Project identifier (UUID or project name)
            mode: "session_start" (fast, recent items) or "live" (complete, all items)
            project_root: Optional path to project root (defaults to cwd)
            check_integrity: If True, analyze doc-code integrity (adds ~2s)
            task_description: Task description for context load balancing (optional)
            epistemic_state: Epistemic vectors for intelligent routing (optional)
            context_to_inject: If True, generate markdown context string (optional)
            subject: Filter breadcrumbs by subject (optional)
            session_id: Session ID for live state capture (optional, auto-resolved if needed)
            include_live_state: Include current epistemic state (optional)
            fresh_assess: Use fresh self-assessment vs loading checkpoint (optional)
            trigger: Trigger context for session resolution (pre_compact/post_compact/manual)
            depth: Context depth (minimal/moderate/full/auto)
            ai_id: AI identifier to load epistemic handoff for (e.g., 'claude-code')

        Returns quick context: findings, unknowns, dead_ends, mistakes, decisions, incomplete work.
        """
        if project_root is None:
            project_root = _resolve_canonical_project_root()
            # If still unresolved, supplementary data (dep graph, git status) is skipped.
            # Never fall back to os.getcwd() — that pollutes subdirs with stray .empirica/.

        # 1. Resolve and validate project
        project = self._resolve_and_validate_project(project_id)
        if not project:
            return {"error": f"Project not found: {project_id}"}

        resolved_id = project['id']

        # 2. Get latest handoff
        latest_handoff = self.get_latest_project_handoff(resolved_id)

        # 2b. Get AI-specific epistemic handoff if ai_id provided
        ai_epistemic_handoff = None
        if ai_id:
            ai_epistemic_handoff = self.get_ai_epistemic_handoff(resolved_id, ai_id)

        # 3. Load all breadcrumbs based on mode
        breadcrumbs = self._load_breadcrumbs_for_mode(resolved_id, mode, subject)

        # 4. Load goals and merge truncation warnings
        goals_data = self._load_goals_for_project(resolved_id)
        all_warnings = breadcrumbs.pop('truncation_warnings', []) + goals_data.pop('truncation_warnings', [])
        breadcrumbs.update(goals_data)
        if all_warnings:
            breadcrumbs['truncation_warnings'] = all_warnings

        # 5. Attach supplementary data (deps, git, issues, live state) — only when
        # project_root is resolved. Otherwise core breadcrumbs still ship.
        if project_root:
            self._attach_supplementary_data(
                breadcrumbs, resolved_id, project_root,
                session_id, include_live_state, fresh_assess, trigger,
            )

        # 6. Build and enrich project metadata
        breadcrumbs['project'] = self._enrich_project_from_yaml(
            self._build_project_metadata(project)
        )

        if latest_handoff:
            breadcrumbs['latest_handoff'] = latest_handoff
        if ai_epistemic_handoff:
            breadcrumbs['ai_epistemic_handoff'] = ai_epistemic_handoff

        # 7. Synthesize 'situation' — the compaction-recovery narrative.
        # Replaces the template-only `last_activity` field; carries
        # active_transaction + active_goal + subtasks + last_commit + next_focus.
        situation = self._build_situation(resolved_id, project_root)

        # last_activity kept for backward-compat consumers; new narrative
        # lives in `situation` and is the recommended source.
        last_commit_msg = (
            situation.get('last_praxic_action', {}).get('message')
            if isinstance(situation.get('last_praxic_action'), dict) else None
        )
        last_at = (
            situation.get('last_praxic_action', {}).get('at')
            if isinstance(situation.get('last_praxic_action'), dict) else None
        )
        breadcrumbs['last_activity'] = {
            'summary': (
                f"Last commit: {last_commit_msg} (at {last_at})"
                if last_commit_msg else
                f"Last activity: {project.get('last_activity_timestamp', 'Unknown')}"
            ),
            'next_focus': situation.get(
                'next_focus',
                'Continue with incomplete work and unknown resolutions',
            ),
        }

        # 8. Generate context markdown if requested
        if context_to_inject:
            breadcrumbs['context_markdown'] = generate_context_markdown(breadcrumbs)

        # 9. Attach optional metrics (flow, health, feature status)
        self._attach_optional_metrics(breadcrumbs, resolved_id, project_root)

        # 10. Apply adaptive depth filtering LAST (after all fields populated)
        if depth != "auto" or trigger == "post_compact":
            breadcrumbs = self._apply_depth_filter(breadcrumbs, depth, trigger)

        # 11. Reorder so `situation` appears first in JSON output
        # (attention-decay-aware — AI sees state-narrative before deep lists).
        return {'situation': situation, **breadcrumbs}


    def _auto_resolve_session(self, project_id: str, trigger: str | None) -> str | None:
        """
        Auto-resolve session_id from project and trigger context.

        Resolution order:
        1. If trigger='post_compact': Load from latest pre-summary snapshot
        2. If trigger='pre_compact': Get latest session for project (even if ended)
        3. Otherwise: Get latest ACTIVE session for project
        """
        if trigger == 'post_compact':
            # Load session from pre-compact snapshot
            try:
                import json
                from pathlib import Path
                ref_docs_dir = Path.cwd() / ".empirica" / "ref-docs"
                snapshots = sorted(ref_docs_dir.glob("pre_summary_*.json"), reverse=True)
                if snapshots:
                    with open(snapshots[0]) as f:
                        snapshot = json.load(f)
                        return snapshot.get('session_id')
            except Exception:
                logger.debug("Failed to read pre_summary snapshot for session_id")

        # Get latest session for project
        cursor = self.conn.cursor()
        if trigger == 'pre_compact':
            # Include ended sessions (POSTFLIGHT just ran)
            cursor.execute("""
                SELECT session_id FROM sessions
                WHERE project_id = ?
                ORDER BY start_time DESC
                LIMIT 1
            """, (project_id,))
        else:
            # Only active sessions (end_time IS NULL)
            cursor.execute("""
                SELECT session_id FROM sessions
                WHERE project_id = ? AND end_time IS NULL
                ORDER BY start_time DESC
                LIMIT 1
            """, (project_id,))

        row = cursor.fetchone()
        return row['session_id'] if row else None

    def _capture_fresh_state(self, session_id: str, project_id: str) -> dict:
        """
        Capture fresh epistemic state via self-assessment.

        Returns current state (not from old checkpoint):
        - Vectors from latest available checkpoint OR minimal self-assess
        - Current git state
        - Reasoning
        - Investigation context
        """
        from datetime import datetime

        # Get git state
        git_state = self._get_current_git_state()

        # Try to get vectors from latest checkpoint
        try:
            from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger
            git_logger = GitEnhancedReflexLogger(session_id=session_id, enable_git_notes=True)
            checkpoints = git_logger.list_checkpoints(limit=1)

            if checkpoints:
                latest = checkpoints[0]
                vectors = latest.get('vectors', {})
                reasoning = latest.get('meta', {}).get('reasoning', 'Latest checkpoint reasoning')
                phase = latest.get('phase', 'UNKNOWN')
            else:
                # No checkpoints - use minimal default
                vectors = {
                    "engagement": 0.5,
                    "know": 0.5,
                    "uncertainty": 0.5,
                    "impact": 0.5,
                    "completion": 0.0
                }
                reasoning = "Fresh assessment (no prior checkpoints)"
                phase = None
        except Exception as e:
            # Fallback to minimal state
            vectors = {
                "engagement": 0.5,
                "know": 0.5,
                "uncertainty": 0.5,
                "impact": 0.5,
                "completion": 0.0
            }
            reasoning = f"Fresh assessment (error loading checkpoint: {e})"
            phase = None

        return {
            "vectors": vectors,
            "git": git_state,
            "reasoning": reasoning,
            "phase": phase,
            "timestamp": datetime.now().isoformat(),
            "fresh": True  # Flag to indicate this is fresh, not loaded checkpoint
        }

    def _load_latest_checkpoint_state(self, session_id: str) -> dict | None:
        """Load latest checkpoint from session (via GitEnhancedReflexLogger)"""
        try:

            from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger

            git_logger = GitEnhancedReflexLogger(session_id=session_id, enable_git_notes=True)
            checkpoints = git_logger.list_checkpoints(limit=1)

            if not checkpoints:
                return None

            checkpoint = checkpoints[0]
            return {
                "vectors": checkpoint.get('vectors', {}),
                "git": self._get_current_git_state(),  # Current git state, not from checkpoint
                "reasoning": checkpoint.get('meta', {}).get('reasoning', ''),
                "phase": checkpoint.get('phase'),
                "timestamp": checkpoint.get('timestamp'),
                "fresh": False  # Loaded from checkpoint, not fresh
            }
        except Exception as e:
            return {"error": str(e)}

    def _get_current_git_state(self) -> dict:
        """Get current git state (HEAD, branch, dirty flag, uncommitted changes)"""
        import subprocess

        try:
            # Get HEAD commit
            head = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                capture_output=True,
                text=True,
                timeout=2
            )
            head_commit = head.stdout.strip() if head.returncode == 0 else None

            # Get branch
            branch = subprocess.run(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                capture_output=True,
                text=True,
                timeout=2
            )
            branch_name = branch.stdout.strip() if branch.returncode == 0 else None

            # Check if dirty (uncommitted changes)
            status = subprocess.run(
                ['git', 'status', '--porcelain'],
                capture_output=True,
                text=True,
                timeout=2
            )
            dirty = len(status.stdout.strip()) > 0 if status.returncode == 0 else None
            uncommitted_count = len(status.stdout.strip().split('\n')) if dirty else 0

            return {
                "head": head_commit,
                "branch": branch_name,
                "dirty": dirty,
                "uncommitted_files": uncommitted_count
            }
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def _resolve_auto_depth(breadcrumbs: dict) -> str:
        """Resolve 'auto' depth by computing drift from pre-compact snapshot."""
        try:
            import json
            from pathlib import Path
            ref_docs_dir = Path.cwd() / ".empirica" / "ref-docs"
            snapshots = sorted(ref_docs_dir.glob("pre_summary_*.json"), reverse=True)
            if snapshots and breadcrumbs.get('live_state'):
                with open(snapshots[0]) as f:
                    pre_snapshot = json.load(f)
                pre_vectors = pre_snapshot.get('checkpoint', {}).get('vectors', {})
                post_vectors = breadcrumbs['live_state'].get('vectors', {})
                drift, count = 0.0, 0
                for key in ['know', 'uncertainty', 'engagement', 'impact', 'completion']:
                    if key in pre_vectors and key in post_vectors:
                        if pre_vectors[key] is not None and post_vectors[key] is not None:
                            drift += abs(pre_vectors[key] - post_vectors[key])
                            count += 1
                drift = drift / count if count > 0 else 0.0
                if drift > 0.3:
                    return "full"
                elif drift > 0.1:
                    return "moderate"
                else:
                    return "minimal"
        except Exception:
            pass
        return "moderate"

    def _apply_depth_filter(self, breadcrumbs: dict, depth: str, trigger: str | None) -> dict:
        """
        Apply adaptive depth filtering to breadcrumbs based on drift or explicit depth.

        Depth levels:
        - minimal: Essentials only (~2k tokens) - findings/unknowns/goals limited, no dependency_graph
        - moderate: Balanced context (~8k tokens) - limited lists, no verbose metadata
        - full: Complete context (unlimited) - all fields included
        - auto: Determine depth based on drift (if post_compact trigger)

        Key optimizations:
        - Strip redundant 'finding_data' JSON from findings (duplicates text)
        - Remove dependency_graph for minimal/moderate (rarely needed by MCP)
        - Remove verbose metadata like database_summary, structure_health
        - Limit active_goals (often stale accumulation)
        """
        if depth == "auto" and trigger == "post_compact":
            depth = self._resolve_auto_depth(breadcrumbs)

        # Shared slimming helpers for depth filtering
        def _slim_finding(f: dict) -> dict:
            return {'finding': f.get('finding'), 'impact': f.get('impact')}

        def _slim_unknown(u: dict) -> dict:
            return {'unknown': u.get('unknown'), 'is_resolved': u.get('is_resolved', False)}

        def _slim_goal(g: dict) -> dict:
            return {
                'id': g.get('id', '')[:8],  # Short ID
                'objective': g.get('objective'),
                'status': g.get('status')
            }

        def _slim_dead_end(d: dict) -> dict:
            return {'approach': d.get('approach'), 'why_failed': d.get('why_failed')}

        def _slim_decision(d: dict) -> dict:
            return {'choice': d.get('choice'), 'rationale': d.get('rationale')}

        # Apply depth filter
        if depth == "minimal":
            # ~2k tokens target: essentials only
            breadcrumbs['findings'] = [_slim_finding(f) for f in breadcrumbs.get('findings', [])[:5]]
            breadcrumbs['unknowns'] = [_slim_unknown(u) for u in breadcrumbs.get('unknowns', [])[:5]]
            breadcrumbs['dead_ends'] = [_slim_dead_end(d) for d in breadcrumbs.get('dead_ends', [])[:3]]
            breadcrumbs['decisions'] = [_slim_decision(d) for d in breadcrumbs.get('decisions', [])[:3]]
            breadcrumbs['mistakes_to_avoid'] = breadcrumbs.get('mistakes_to_avoid', [])[:2]
            breadcrumbs['goals'] = [_slim_goal(g) for g in breadcrumbs.get('goals', [])[:1]]
            breadcrumbs['active_goals'] = [_slim_goal(g) for g in breadcrumbs.get('active_goals', [])[:2]]
            breadcrumbs['reference_docs'] = breadcrumbs.get('reference_docs', [])[:2]
            # Remove expensive/verbose fields entirely
            for key in ['dependency_graph', 'git_status', 'database_summary', 'structure_health',
                       'flow_metrics', 'health_score', 'recent_artifacts', 'active_sessions',
                       'integrity_analysis', 'feature_status', 'findings_with_goals',
                       'epistemic_artifacts', 'ai_activity', 'semantic_docs', 'available_skills',
                       'key_decisions', 'incomplete_work', 'auto_captured_issues', 'truncation_warnings']:
                breadcrumbs.pop(key, None)

        elif depth == "moderate":
            # ~8k tokens target: balanced context for MCP
            breadcrumbs['findings'] = [_slim_finding(f) for f in breadcrumbs.get('findings', [])[:10]]
            breadcrumbs['unknowns'] = [_slim_unknown(u) for u in breadcrumbs.get('unknowns', [])[:10]]
            breadcrumbs['dead_ends'] = [_slim_dead_end(d) for d in breadcrumbs.get('dead_ends', [])[:5]]
            breadcrumbs['mistakes_to_avoid'] = breadcrumbs.get('mistakes_to_avoid', [])[:5]
            breadcrumbs['goals'] = [_slim_goal(g) for g in breadcrumbs.get('goals', [])[:5]]
            breadcrumbs['decisions'] = [_slim_decision(d) for d in breadcrumbs.get('decisions', [])[:5]]
            breadcrumbs['active_goals'] = [_slim_goal(g) for g in breadcrumbs.get('active_goals', [])[:5]]
            breadcrumbs['reference_docs'] = breadcrumbs.get('reference_docs', [])[:5]
            breadcrumbs['incomplete_work'] = breadcrumbs.get('incomplete_work', [])[:3]
            breadcrumbs['auto_captured_issues'] = breadcrumbs.get('auto_captured_issues', [])[:3]
            # Remove expensive/verbose fields
            for key in ['dependency_graph', 'database_summary', 'structure_health',
                       'integrity_analysis', 'epistemic_artifacts', 'semantic_docs',
                       'available_skills', 'findings_with_goals', 'recent_artifacts',
                       'active_sessions', 'ai_activity', 'key_decisions']:
                breadcrumbs.pop(key, None)
            # Remove flow_metrics/health_score entirely for moderate (still verbose)
            breadcrumbs.pop('flow_metrics', None)
            breadcrumbs.pop('health_score', None)

        # depth == "full": no filtering (keep everything)

        return breadcrumbs

    def _get_latest_impact_score(self, session_id: str) -> float:
        """Get impact score from latest CASCADE assessment (PREFLIGHT/CHECK/POSTFLIGHT)"""
        try:
            result = self.adapter.execute("""
                SELECT impact FROM reflexes
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, (session_id,))
            row = result.fetchone()
            if row is None:
                return 0.5
            # Handle both dict (PostgreSQL RealDictCursor) and tuple (SQLite) rows
            if isinstance(row, dict):
                return row.get('impact', 0.5) or 0.5
            return row[0] if row[0] is not None else 0.5
        except Exception:
            return 0.5

    def log_finding(
        self,
        project_id: str,
        session_id: str,
        finding: str,
        goal_id: str | None = None,
        subtask_id: str | None = None,
        subject: str | None = None,
        impact: float | None = None,
        transaction_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        source_ids: list[str] | None = None,
        visibility: str | None = None,
        epistemic_source: str | None = None,
        description: str | None = None,
    ) -> str:
        """Log a project finding (delegates to BreadcrumbRepository)"""
        # Auto-derive impact from latest CASCADE if not provided
        if impact is None:
            impact = self._get_latest_impact_score(session_id)

        return self.breadcrumbs.log_finding(
            project_id, session_id, finding, goal_id, subtask_id, subject, impact,
            transaction_id=transaction_id,
            entity_type=entity_type,
            entity_id=entity_id,
            source_ids=source_ids,
            visibility=visibility,
            epistemic_source=epistemic_source,
            description=description,
        )

    def log_session_finding(
        self,
        session_id: str,
        finding: str,
        goal_id: str | None = None,
        subtask_id: str | None = None,
        subject: str | None = None,
        impact: float | None = None
    ) -> str:
        """Log a session-scoped finding (ephemeral, session-specific learning)"""
        # Auto-derive impact from latest CASCADE if not provided
        if impact is None:
            impact = self._get_latest_impact_score(session_id)

        return self.breadcrumbs.log_session_finding(
            session_id, finding, goal_id, subtask_id, subject, impact
        )

    def log_session_unknown(
        self,
        session_id: str,
        unknown: str,
        goal_id: str | None = None,
        subtask_id: str | None = None,
        subject: str | None = None,
        impact: float | None = None
    ) -> str:
        """Log a session-scoped unknown"""
        if impact is None:
            impact = self._get_latest_impact_score(session_id)

        return self.breadcrumbs.log_session_unknown(
            session_id, unknown, goal_id, subtask_id, subject, impact
        )

    def log_session_dead_end(
        self,
        session_id: str,
        approach: str,
        why_failed: str,
        goal_id: str | None = None,
        subtask_id: str | None = None,
        subject: str | None = None,
        impact: float | None = None
    ) -> str:
        """Log a session-scoped dead end"""
        if impact is None:
            impact = self._get_latest_impact_score(session_id)

        return self.breadcrumbs.log_session_dead_end(
            session_id, approach, why_failed, goal_id, subtask_id, subject, impact
        )

    def log_session_mistake(
        self,
        session_id: str,
        mistake: str,
        why_wrong: str,
        cost_estimate: str | None = None,
        root_cause_vector: str | None = None,
        prevention: str | None = None,
        goal_id: str | None = None
    ) -> str:
        """Log a session-scoped mistake"""
        return self.breadcrumbs.log_session_mistake(
            session_id, mistake, why_wrong, cost_estimate,
            root_cause_vector, prevention, goal_id
        )

    def log_unknown(
        self,
        project_id: str,
        session_id: str,
        unknown: str,
        goal_id: str | None = None,
        subtask_id: str | None = None,
        subject: str | None = None,
        impact: float | None = None,
        transaction_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        visibility: str | None = None,
        epistemic_source: str | None = None,
        description: str | None = None,
    ) -> str:
        """Log a project unknown (delegates to BreadcrumbRepository)"""
        # Auto-derive impact from latest CASCADE if not provided
        if impact is None:
            impact = self._get_latest_impact_score(session_id)

        return self.breadcrumbs.log_unknown(
            project_id, session_id, unknown, goal_id, subtask_id, subject, impact,
            transaction_id=transaction_id,
            entity_type=entity_type,
            entity_id=entity_id,
            visibility=visibility,
            epistemic_source=epistemic_source,
            description=description,
        )

    def resolve_unknown(self, unknown_id: str, resolved_by: str,
                        resolution_finding_id: str | None = None):
        """Mark an unknown as resolved (delegates to BreadcrumbRepository)"""
        return self.breadcrumbs.resolve_unknown(
            unknown_id, resolved_by,
            resolution_finding_id=resolution_finding_id)

    def log_dead_end(
        self,
        project_id: str,
        session_id: str,
        approach: str,
        why_failed: str,
        goal_id: str | None = None,
        subtask_id: str | None = None,
        subject: str | None = None,
        impact: float | None = None,
        transaction_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        visibility: str | None = None,
        epistemic_source: str | None = None,
        description: str | None = None,
    ) -> str:
        """Log a project dead end (delegates to BreadcrumbRepository)

        Args:
            impact: Impact score 0.0-1.0 (importance). If None, auto-derives from latest CASCADE.
            transaction_id: Optional epistemic transaction ID.
        """
        # Auto-derive impact from latest CASCADE if not provided
        if impact is None:
            impact = self._get_latest_impact_score(session_id)

        return self.breadcrumbs.log_dead_end(project_id, session_id, approach,
                                             why_failed, goal_id, subtask_id, subject, impact,
                                             transaction_id=transaction_id,
                                             entity_type=entity_type,
                                             entity_id=entity_id,
                                             visibility=visibility,
                                             epistemic_source=epistemic_source,
                                             description=description)

    def add_reference_doc(
        self,
        project_id: str,
        doc_path: str,
        doc_type: str | None = None,
        description: str | None = None
    ) -> str:
        """Add a reference document to project (delegates to BreadcrumbRepository)"""
        return self.breadcrumbs.add_reference_doc(project_id, doc_path, doc_type, description)

    def get_project_findings(
        self,
        project_id: str,
        limit: int | None = None,
        subject: str | None = None,
        depth: str = "moderate",
        uncertainty: float | None = None
    ) -> list[dict]:
        """
        Get all findings for a project with deprecation filtering.

        Args:
            project_id: Project identifier
            limit: Optional limit on results
            subject: Optional subject filter
            depth: Relevance depth ("minimal", "moderate", "full", "complete", "auto")
            uncertainty: Epistemic uncertainty for auto-depth (0.0-1.0)

        Returns:
            Filtered list of findings
        """
        return self.breadcrumbs.get_project_findings(
            project_id,
            limit=limit,
            subject=subject,
            depth=depth,
            uncertainty=uncertainty
        )

    def get_project_unknowns(self, project_id: str, resolved: bool | None = None, subject: str | None = None, limit: int | None = None) -> list[dict]:
        """Get unknowns for a project (delegates to BreadcrumbRepository)"""
        return self.breadcrumbs.get_project_unknowns(project_id, resolved, subject, limit)

    def get_project_dead_ends(self, project_id: str, limit: int | None = None, subject: str | None = None) -> list[dict]:
        """Get all dead ends for a project (delegates to BreadcrumbRepository)"""
        return self.breadcrumbs.get_project_dead_ends(project_id, limit, subject)

    def get_project_reference_docs(self, project_id: str) -> list[dict]:
        """Get all reference docs for a project (delegates to BreadcrumbRepository)"""
        return self.breadcrumbs.get_project_reference_docs(project_id)

    def add_epistemic_source(
        self,
        project_id: str,
        source_type: str,
        title: str,
        session_id: str | None = None,
        source_url: str | None = None,
        description: str | None = None,
        confidence: float = 0.5,
        epistemic_layer: str | None = None,
        supports_vectors: dict[str, float] | None = None,
        related_findings: list[str] | None = None,
        discovered_by_ai: str | None = None,
        source_metadata: dict | None = None
    ) -> str:
        """Add an epistemic source to ground project knowledge

        Args:
            project_id: Project identifier
            source_type: Type of source ('url', 'doc', 'code_ref', 'paper', 'api_doc', 'git_commit', 'chat_transcript', 'epistemic_snapshot')
            title: Source title
            session_id: Optional session that discovered this source
            source_url: Optional URL or path
            description: Optional description
            confidence: Confidence in this source (0.0-1.0, default 0.5)
            epistemic_layer: Optional layer ('noetic', 'epistemic', 'action')
            supports_vectors: Optional dict of epistemic vectors this source supports
            related_findings: Optional list of finding IDs
            discovered_by_ai: Optional AI identifier
            source_metadata: Optional metadata dict

        Returns:
            source_id: UUID string
        """
        source_id = str(uuid.uuid4())

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO epistemic_sources (
                id, project_id, session_id,
                source_type, source_url, title, description,
                confidence, epistemic_layer,
                supports_vectors, related_findings,
                discovered_by_ai, discovered_at,
                source_metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            source_id, project_id, session_id,
            source_type, source_url, title, description,
            confidence, epistemic_layer,
            json.dumps(supports_vectors) if supports_vectors else None,
            json.dumps(related_findings) if related_findings else None,
            discovered_by_ai, datetime.now(),
            json.dumps(source_metadata) if source_metadata else None
        ))

        self.conn.commit()
        logger.info(f"📚 Epistemic source added: {title}")

        return source_id

    def get_epistemic_sources(
        self,
        project_id: str,
        session_id: str | None = None,
        source_type: str | None = None,
        min_confidence: float = 0.0,
        limit: int | None = None
    ) -> list[dict]:
        """Get epistemic sources for a project

        Args:
            project_id: Project identifier
            session_id: Optional filter by session
            source_type: Optional filter by type
            min_confidence: Minimum confidence threshold (default 0.0)
            limit: Optional limit on results

        Returns:
            List of source dictionaries
        """
        cursor = self.conn.cursor()

        query = """
            SELECT * FROM epistemic_sources
            WHERE project_id = ? AND confidence >= ?
        """
        params = [project_id, min_confidence]

        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)

        if source_type:
            query += " AND source_type = ?"
            params.append(source_type)

        query += " ORDER BY confidence DESC, discovered_at DESC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor.execute(query, params)

        results = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            # Parse JSON fields
            if row_dict.get('supports_vectors'):
                row_dict['supports_vectors'] = json.loads(row_dict['supports_vectors'])
            if row_dict.get('related_findings'):
                row_dict['related_findings'] = json.loads(row_dict['related_findings'])
            if row_dict.get('source_metadata'):
                row_dict['source_metadata'] = json.loads(row_dict['source_metadata'])
            results.append(row_dict)

        return results

    def log_mistake(
        self,
        session_id: str,
        mistake: str,
        why_wrong: str,
        cost_estimate: str | None = None,
        root_cause_vector: str | None = None,
        prevention: str | None = None,
        goal_id: str | None = None,
        project_id: str | None = None,
        transaction_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        visibility: str | None = None,
        epistemic_source: str | None = None,
        description: str | None = None,
    ) -> str:
        """Log a mistake for learning (delegates to BreadcrumbRepository)

        Args:
            session_id: Session identifier
            mistake: What was done wrong
            why_wrong: Explanation of why it was wrong
            cost_estimate: Estimated time/effort wasted (e.g., "2 hours")
            root_cause_vector: Epistemic vector that caused the mistake (e.g., "KNOW", "CONTEXT")
            prevention: How to prevent this mistake in the future
            goal_id: Optional goal identifier this mistake relates to
            transaction_id: Optional epistemic transaction ID.

        Returns:
            mistake_id: UUID string
        """
        return self.breadcrumbs.log_mistake(session_id, mistake, why_wrong,
                                           cost_estimate, root_cause_vector, prevention, goal_id, project_id,
                                           transaction_id=transaction_id,
                                           entity_type=entity_type,
                                           entity_id=entity_id,
                                           visibility=visibility,
                                           epistemic_source=epistemic_source,
                                           description=description)

    def get_mistakes(
        self,
        session_id: str | None = None,
        goal_id: str | None = None,
        limit: int = 10
    ) -> list[dict]:
        """Retrieve logged mistakes (delegates to BreadcrumbRepository)

        Args:
            session_id: Optional filter by session
            goal_id: Optional filter by goal
            limit: Maximum number of results

        Returns:
            List of mistake dictionaries
        """
        return self.breadcrumbs.get_mistakes(session_id, goal_id, limit)

    def log_assumption(
        self,
        project_id: str,
        session_id: str,
        assumption: str,
        confidence: float = 0.5,
        domain: str | None = None,
        goal_id: str | None = None,
        transaction_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        visibility: str | None = None,
        epistemic_source: str | None = None,
        description: str | None = None,
    ) -> str:
        """Log an unverified belief (delegates to BreadcrumbRepository)."""
        return self.breadcrumbs.log_assumption(
            project_id, session_id, assumption, confidence,
            domain=domain, goal_id=goal_id, transaction_id=transaction_id,
            entity_type=entity_type, entity_id=entity_id,
            visibility=visibility, epistemic_source=epistemic_source,
            description=description)

    def log_decision(
        self,
        project_id: str,
        session_id: str,
        choice: str,
        rationale: str,
        alternatives: str | None = None,
        confidence: float = 0.7,
        reversibility: str = 'exploratory',
        goal_id: str | None = None,
        transaction_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        evidence_refs: list[str] | None = None,
        visibility: str | None = None,
        epistemic_source: str | None = None,
        description: str | None = None,
    ) -> str:
        """Log a decision choice point (delegates to BreadcrumbRepository)."""
        return self.breadcrumbs.log_decision(
            project_id, session_id, choice, rationale,
            alternatives=alternatives, confidence=confidence,
            reversibility=reversibility, goal_id=goal_id,
            transaction_id=transaction_id,
            entity_type=entity_type, entity_id=entity_id,
            evidence_refs=evidence_refs,
            visibility=visibility, epistemic_source=epistemic_source,
            description=description)

    def log_bead(
        self,
        project_id: str,
        session_id: str,
        coordination_state: str = 'open',
        updated_at: float | None = None,
        last_transition_actor: str | None = None,
        beads_issue_id: str | None = None,
        scope: str | None = None,
        goal_id: str | None = None,
        transaction_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        visibility: str | None = None,
        epistemic_source: str | None = None,
        description: str | None = None,
    ) -> str:
        """Log a bead v0 coordination-record (delegates to BreadcrumbRepository).

        RETIRED 2026-06-02 (empirica 1.11.2). The v0 bead concept retired
        three-way (cortex/empirica/extension) 2026-06-01; cross-practitioner
        coordination state lives in cortex-resident SER now — see
        `empirica-cortex/docs/architecture/SHARED_EPISTEMIC_RECORD.md`.
        Kept as an inert legacy path for backward read of pre-retirement
        rows. New callers should use `cortex_propose(payload.action='create_ser')`.
        """
        return self.breadcrumbs.log_bead(
            project_id, session_id,
            coordination_state=coordination_state,
            updated_at=updated_at,
            last_transition_actor=last_transition_actor,
            beads_issue_id=beads_issue_id,
            scope=scope,
            goal_id=goal_id,
            transaction_id=transaction_id,
            entity_type=entity_type, entity_id=entity_id,
            visibility=visibility, epistemic_source=epistemic_source,
            description=description)

    def log_token_saving(
        self,
        session_id: str,
        saving_type: str,
        tokens_saved: int,
        evidence: str
    ) -> str:
        """Log token saving (delegates to TokenRepository)"""
        return self.tokens.log_token_saving(session_id, saving_type, tokens_saved, evidence)

    def get_session_token_savings(self, session_id: str) -> dict:
        """Get token savings summary (delegates to TokenRepository)"""
        return self.tokens.get_session_token_savings(session_id)

    def get_workspace_overview(self) -> dict[str, Any]:
        """Get workspace overview (delegates to WorkspaceRepository)"""
        return self.workspace.get_workspace_overview()

    def _get_workspace_stats(self) -> dict[str, Any]:
        """Get workspace stats (delegates to WorkspaceRepository)"""
        return self.workspace._get_workspace_aggregate_stats()

    def close(self):
        """Close database connection and all repositories"""
        if hasattr(self, '_tasks') and self._tasks is not None:
            self._tasks.close()
        if hasattr(self, '_core_goals') and self._core_goals is not None:
            self._core_goals.close()
        self.conn.commit()
        self.conn.close()



if __name__ == "__main__":
    # Test the database
    logger.info("🧪 Testing Session Database...")
    db = SessionDatabase()
    db.close()
    logger.info("✅ Session Database ready")
