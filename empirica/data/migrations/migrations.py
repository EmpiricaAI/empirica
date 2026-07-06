"""Database schema migrations"""

import logging
import sqlite3
from collections.abc import Callable

from .migration_runner import add_column_if_missing

logger = logging.getLogger(__name__)


# Migration 1: Add CASCADE workflow columns to cascades table
def migration_001_cascade_workflow_columns(cursor: sqlite3.Cursor):
    """Add preflight/plan/postflight tracking columns to cascades"""
    add_column_if_missing(cursor, "cascades", "preflight_completed", "BOOLEAN", "0")
    add_column_if_missing(cursor, "cascades", "plan_completed", "BOOLEAN", "0")
    add_column_if_missing(cursor, "cascades", "postflight_completed", "BOOLEAN", "0")


# Migration 2: Add epistemic delta tracking to cascades
def migration_002_epistemic_delta(cursor: sqlite3.Cursor):
    """Add epistemic_delta JSON column to cascades"""
    add_column_if_missing(cursor, "cascades", "epistemic_delta", "TEXT")


# Migration 3: Add goal tracking to cascades
def migration_003_cascade_goal_tracking(cursor: sqlite3.Cursor):
    """Add goal_id and goal_json to cascades"""
    add_column_if_missing(cursor, "cascades", "goal_id", "TEXT")
    add_column_if_missing(cursor, "cascades", "goal_json", "TEXT")


# Migration 4: Add status column to goals
def migration_004_goals_status(cursor: sqlite3.Cursor):
    """Add status tracking to goals table"""
    add_column_if_missing(cursor, "goals", "status", "TEXT", "'in_progress'")


# Migration 5: Add project_id to sessions
def migration_005_sessions_project_id(cursor: sqlite3.Cursor):
    """Add project_id foreign key to sessions"""
    add_column_if_missing(cursor, "sessions", "project_id", "TEXT")


# Migration 6: Add subject filtering to sessions
def migration_006_sessions_subject(cursor: sqlite3.Cursor):
    """Add subject column to sessions for filtering"""
    add_column_if_missing(cursor, "sessions", "subject", "TEXT")


# Migration 7: Add impact scoring to project_findings
def migration_007_findings_impact(cursor: sqlite3.Cursor):
    """Add impact column to project_findings for importance weighting"""
    add_column_if_missing(cursor, "project_findings", "impact", "REAL")


# Migration 8: Migrate legacy tables to reflexes
def migration_008_migrate_legacy_to_reflexes(cursor: sqlite3.Cursor):
    """
    Migrate data from deprecated tables to reflexes table, then drop old tables.

    This runs automatically on database initialization. It's idempotent - safe to run multiple times.

    Migration mapping:
    - preflight_assessments → reflexes (phase='PREFLIGHT')
    - postflight_assessments → reflexes (phase='POSTFLIGHT')
    - check_phase_assessments → reflexes (phase='CHECK')
    - epistemic_assessments → (unused, just drop)
    """
    import logging

    logger = logging.getLogger(__name__)

    try:
        # Check if old tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='preflight_assessments'")
        if not cursor.fetchone():
            logger.debug("✓ Legacy tables already migrated or don't exist")
            return  # Already migrated

        logger.info("🔄 Migrating legacy epistemic tables to reflexes...")

        # Migrate preflight_assessments → reflexes
        cursor.execute("""
            INSERT INTO reflexes (session_id, cascade_id, phase, round, timestamp,
                                engagement, know, do, context, clarity, coherence, signal, density,
                                state, change, completion, impact, uncertainty, reflex_data, reasoning)
            SELECT session_id, cascade_id, 'PREFLIGHT', 1,
                   CAST(strftime('%s', assessed_at) AS REAL),
                   engagement, know, do, context, clarity, coherence, signal, density,
                   state, change, completion, impact, uncertainty,
                   vectors_json, initial_uncertainty_notes
            FROM preflight_assessments
            WHERE NOT EXISTS (
                SELECT 1 FROM reflexes r
                WHERE r.session_id = preflight_assessments.session_id
                AND r.phase = 'PREFLIGHT'
                AND r.cascade_id IS preflight_assessments.cascade_id
            )
        """)
        preflight_count = cursor.rowcount
        logger.info(f"  ✓ Migrated {preflight_count} preflight assessments")

        # Migrate postflight_assessments → reflexes
        cursor.execute("""
            INSERT INTO reflexes (session_id, cascade_id, phase, round, timestamp,
                                engagement, know, do, context, clarity, coherence, signal, density,
                                state, change, completion, impact, uncertainty, reflex_data, reasoning)
            SELECT session_id, cascade_id, 'POSTFLIGHT', 1,
                   CAST(strftime('%s', assessed_at) AS REAL),
                   engagement, know, do, context, clarity, coherence, signal, density,
                   state, change, completion, impact, uncertainty,
                   json_object('calibration_accuracy', calibration_accuracy,
                               'postflight_confidence', postflight_actual_confidence),
                   learning_notes
            FROM postflight_assessments
            WHERE NOT EXISTS (
                SELECT 1 FROM reflexes r
                WHERE r.session_id = postflight_assessments.session_id
                AND r.phase = 'POSTFLIGHT'
                AND r.cascade_id IS postflight_assessments.cascade_id
            )
        """)
        postflight_count = cursor.rowcount
        logger.info(f"  ✓ Migrated {postflight_count} postflight assessments")

        # Migrate check_phase_assessments → reflexes (confidence → uncertainty conversion)
        cursor.execute("""
            INSERT INTO reflexes (session_id, cascade_id, phase, round, timestamp,
                                uncertainty, reflex_data, reasoning)
            SELECT session_id, cascade_id, 'CHECK', investigation_cycle,
                   CAST(strftime('%s', assessed_at) AS REAL),
                   (1.0 - confidence),
                   json_object('decision', decision,
                               'gaps_identified', gaps_identified,
                               'next_investigation_targets', next_investigation_targets,
                               'confidence', confidence),
                   self_assessment_notes
            FROM check_phase_assessments
            WHERE NOT EXISTS (
                SELECT 1 FROM reflexes r
                WHERE r.session_id = check_phase_assessments.session_id
                AND r.phase = 'CHECK'
                AND r.cascade_id IS check_phase_assessments.cascade_id
                AND r.round = check_phase_assessments.investigation_cycle
            )
        """)
        check_count = cursor.rowcount
        logger.info(f"  ✓ Migrated {check_count} check phase assessments")

        # Drop old tables (no longer needed)
        logger.info("  🗑️  Dropping deprecated tables...")
        cursor.execute("DROP TABLE IF EXISTS epistemic_assessments")
        cursor.execute("DROP TABLE IF EXISTS preflight_assessments")
        cursor.execute("DROP TABLE IF EXISTS postflight_assessments")
        cursor.execute("DROP TABLE IF EXISTS check_phase_assessments")

        logger.info("✅ Migration complete: All data moved to reflexes table")

    except sqlite3.OperationalError as e:
        # Table doesn't exist or already migrated - this is fine
        logger.debug(f"Migration check: {e} (this is expected if tables don't exist)")
    except Exception as e:
        logger.error(f"⚠️  Migration failed: {e}")
        # Don't raise - allow database to continue working
        # Old tables will remain if migration fails


# All migrations in execution order
# Migration 9: Add project_id to goals
def migration_009_goals_project_id(cursor: sqlite3.Cursor):
    """Add project_id to goals table and populate from sessions"""
    import logging

    logger = logging.getLogger(__name__)

    # Add column
    add_column_if_missing(cursor, "goals", "project_id", "TEXT")

    # Populate project_id from sessions
    cursor.execute("""
        UPDATE goals
        SET project_id = (
            SELECT project_id FROM sessions WHERE sessions.session_id = goals.session_id
        )
        WHERE project_id IS NULL
    """)
    rows_updated = cursor.rowcount
    logger.info(f"✓ Updated {rows_updated} goals with project_id from sessions")


# Migration 10: Add bootstrap_level to sessions
def migration_010_sessions_bootstrap_level(cursor: sqlite3.Cursor):
    """Add bootstrap_level column to sessions table"""
    add_column_if_missing(cursor, "sessions", "bootstrap_level", "INTEGER", "1")


# Migration 11: Add project_id to mistakes_made
def migration_011_mistakes_project_id(cursor: sqlite3.Cursor):
    """Add project_id column to mistakes_made table"""
    add_column_if_missing(cursor, "mistakes_made", "project_id", "TEXT")


# Migration 12: Add impact column to project_unknowns
def migration_012_unknowns_impact(cursor: sqlite3.Cursor):
    """Add impact scoring to project_unknowns for importance weighting"""
    add_column_if_missing(cursor, "project_unknowns", "impact", "REAL", "0.5")


# Migration 13: Add session-scoped breadcrumb tables (dual-scope architecture)
def migration_013_session_scoped_breadcrumbs(cursor: sqlite3.Cursor):
    """Create session_* tables for session-scoped learning (dual-scope Phase 1)"""

    # session_findings (mirrors project_findings)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_findings (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            goal_id TEXT,
            subtask_id TEXT,
            finding TEXT NOT NULL,
            created_timestamp REAL NOT NULL,
            finding_data TEXT NOT NULL,
            subject TEXT,
            impact REAL,

            FOREIGN KEY (session_id) REFERENCES sessions(session_id),
            FOREIGN KEY (goal_id) REFERENCES goals(id),
            FOREIGN KEY (subtask_id) REFERENCES subtasks(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_findings_session ON session_findings(session_id)")

    # session_unknowns (mirrors project_unknowns)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_unknowns (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            goal_id TEXT,
            subtask_id TEXT,
            unknown TEXT NOT NULL,
            is_resolved BOOLEAN DEFAULT FALSE,
            resolved_by TEXT,
            created_timestamp REAL NOT NULL,
            resolved_timestamp REAL,
            unknown_data TEXT NOT NULL,
            subject TEXT,
            impact REAL DEFAULT 0.5,

            FOREIGN KEY (session_id) REFERENCES sessions(session_id),
            FOREIGN KEY (goal_id) REFERENCES goals(id),
            FOREIGN KEY (subtask_id) REFERENCES subtasks(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_unknowns_session ON session_unknowns(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_unknowns_resolved ON session_unknowns(is_resolved)")

    # session_dead_ends (mirrors project_dead_ends)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_dead_ends (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            goal_id TEXT,
            subtask_id TEXT,
            approach TEXT NOT NULL,
            why_failed TEXT NOT NULL,
            created_timestamp REAL NOT NULL,
            dead_end_data TEXT NOT NULL,
            subject TEXT,

            FOREIGN KEY (session_id) REFERENCES sessions(session_id),
            FOREIGN KEY (goal_id) REFERENCES goals(id),
            FOREIGN KEY (subtask_id) REFERENCES subtasks(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_dead_ends_session ON session_dead_ends(session_id)")

    # session_mistakes (mirrors mistakes_made structure)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_mistakes (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            goal_id TEXT,
            mistake TEXT NOT NULL,
            why_wrong TEXT NOT NULL,
            cost_estimate TEXT,
            root_cause_vector TEXT,
            prevention TEXT,
            created_timestamp REAL NOT NULL,
            mistake_data TEXT NOT NULL,

            FOREIGN KEY (session_id) REFERENCES sessions(session_id),
            FOREIGN KEY (goal_id) REFERENCES goals(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_mistakes_session ON session_mistakes(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_mistakes_goal ON session_mistakes(goal_id)")


# Migration 14: Add lessons and knowledge graph tables
def migration_014_lessons_and_knowledge_graph(cursor: sqlite3.Cursor):
    """
    Add tables for Empirica Lessons - Epistemic Procedural Knowledge.

    4-layer architecture:
    - HOT: In-memory (not stored)
    - WARM: lessons, lesson_steps, lesson_epistemic_deltas (this migration)
    - SEARCH: Qdrant vectors (external)
    - COLD: YAML files (filesystem)
    """
    import logging

    logger = logging.getLogger(__name__)

    # lessons - Core lesson metadata (WARM layer)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lessons (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            description TEXT,
            domain TEXT,
            tags TEXT,  -- Comma-separated

            -- Epistemic quality metrics
            source_confidence REAL NOT NULL,
            teaching_quality REAL NOT NULL,
            reproducibility REAL NOT NULL,

            -- Stats
            step_count INTEGER DEFAULT 0,
            prereq_count INTEGER DEFAULT 0,
            replay_count INTEGER DEFAULT 0,
            success_rate REAL DEFAULT 0.0,

            -- Marketplace
            suggested_tier TEXT DEFAULT 'free',  -- free, verified, pro, enterprise
            suggested_price REAL DEFAULT 0.0,

            -- Metadata
            created_by TEXT,
            created_timestamp REAL NOT NULL,
            updated_timestamp REAL NOT NULL,

            -- Full lesson data (JSON for cold storage reference)
            lesson_data TEXT NOT NULL,

            UNIQUE(name, version)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_domain ON lessons(domain)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_tier ON lessons(suggested_tier)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_created ON lessons(created_timestamp)")
    logger.info("✓ Created lessons table")

    # lesson_steps - Procedural steps (for fast lookup without full YAML)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lesson_steps (
            id TEXT PRIMARY KEY,
            lesson_id TEXT NOT NULL,
            step_order INTEGER NOT NULL,
            phase TEXT NOT NULL,  -- 'noetic' or 'praxic'
            action TEXT NOT NULL,
            target TEXT,
            code TEXT,
            critical BOOLEAN DEFAULT 0,
            expected_outcome TEXT,
            error_recovery TEXT,
            timeout_ms INTEGER,

            FOREIGN KEY (lesson_id) REFERENCES lessons(id) ON DELETE CASCADE,
            UNIQUE(lesson_id, step_order)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lesson_steps_lesson ON lesson_steps(lesson_id)")
    logger.info("✓ Created lesson_steps table")

    # lesson_epistemic_deltas - What vectors each lesson improves
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lesson_epistemic_deltas (
            id TEXT PRIMARY KEY,
            lesson_id TEXT NOT NULL,
            vector_name TEXT NOT NULL,  -- 'know', 'do', 'context', etc.
            delta_value REAL NOT NULL,  -- Positive = improvement, negative = reduction

            FOREIGN KEY (lesson_id) REFERENCES lessons(id) ON DELETE CASCADE,
            UNIQUE(lesson_id, vector_name)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lesson_deltas_lesson ON lesson_epistemic_deltas(lesson_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lesson_deltas_vector ON lesson_epistemic_deltas(vector_name)")
    logger.info("✓ Created lesson_epistemic_deltas table")

    # lesson_prerequisites - What's required before executing a lesson
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lesson_prerequisites (
            id TEXT PRIMARY KEY,
            lesson_id TEXT NOT NULL,
            prereq_type TEXT NOT NULL,  -- 'lesson', 'skill', 'tool', 'context', 'epistemic'
            prereq_id TEXT NOT NULL,
            prereq_name TEXT NOT NULL,
            required_level REAL DEFAULT 0.5,

            FOREIGN KEY (lesson_id) REFERENCES lessons(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lesson_prereqs_lesson ON lesson_prerequisites(lesson_id)")
    logger.info("✓ Created lesson_prerequisites table")

    # lesson_corrections - Human/AI corrections received during creation or replay
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lesson_corrections (
            id TEXT PRIMARY KEY,
            lesson_id TEXT NOT NULL,
            step_order INTEGER NOT NULL,
            original_action TEXT NOT NULL,
            corrected_action TEXT NOT NULL,
            reason TEXT NOT NULL,
            corrector_type TEXT NOT NULL,  -- 'human' or 'ai'
            corrector_id TEXT,
            created_timestamp REAL NOT NULL,

            FOREIGN KEY (lesson_id) REFERENCES lessons(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lesson_corrections_lesson ON lesson_corrections(lesson_id)")
    logger.info("✓ Created lesson_corrections table")

    # knowledge_graph - Relationships between all entities
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_graph (
            id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,  -- 'lesson', 'skill', 'domain', 'goal', 'session'
            source_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,  -- 'requires', 'enables', 'related_to', 'supersedes', 'derived_from', 'produced', 'discovered'
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            created_timestamp REAL NOT NULL,
            metadata TEXT,  -- JSON for additional context

            UNIQUE(source_type, source_id, relation_type, target_type, target_id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_source ON knowledge_graph(source_type, source_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_target ON knowledge_graph(target_type, target_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_relation ON knowledge_graph(relation_type)")
    logger.info("✓ Created knowledge_graph table")

    # lesson_replays - Track lesson execution history
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lesson_replays (
            id TEXT PRIMARY KEY,
            lesson_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            ai_id TEXT,
            started_timestamp REAL NOT NULL,
            completed_timestamp REAL,
            success BOOLEAN,
            steps_completed INTEGER DEFAULT 0,
            total_steps INTEGER NOT NULL,
            error_message TEXT,
            epistemic_before TEXT,  -- JSON of vectors before
            epistemic_after TEXT,   -- JSON of vectors after
            replay_data TEXT,       -- JSON for additional context

            FOREIGN KEY (lesson_id) REFERENCES lessons(id),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lesson_replays_lesson ON lesson_replays(lesson_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lesson_replays_session ON lesson_replays(session_id)")
    logger.info("✓ Created lesson_replays table")

    logger.info("✅ Migration 014 complete: Lessons and knowledge graph tables created")


# Migration 15: Add instance_id to sessions for multi-instance isolation
def migration_015_sessions_instance_id(cursor: sqlite3.Cursor):
    """
    Add instance_id column to sessions table for multi-instance isolation.

    This allows multiple Claude instances to run simultaneously without
    session cross-talk. The instance_id is derived from:
    1. EMPIRICA_INSTANCE_ID env var (explicit override)
    2. TMUX_PANE (tmux terminal pane ID)
    3. TERM_SESSION_ID (macOS Terminal.app)
    4. WINDOWID (X11 window ID)
    5. None (fallback to legacy behavior)
    """
    import logging

    logger = logging.getLogger(__name__)

    add_column_if_missing(cursor, "sessions", "instance_id", "TEXT")

    # Add index for efficient instance-scoped queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_instance ON sessions(ai_id, instance_id)")
    logger.info("✓ Added instance_id column and index to sessions table")


# Migration 16: Add auto_captured_issues table
def migration_016_auto_captured_issues(cursor: sqlite3.Cursor):
    """
    Add auto_captured_issues table for automatic issue detection.

    This table was previously only created when IssueCapture service initialized,
    causing 'no such table' errors during project-bootstrap for users upgrading
    from older versions. Now created via migration for all users.

    Fixes: GitHub Issue #21 (Issue 1: Missing Database Migration)
    """
    import logging

    logger = logging.getLogger(__name__)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS auto_captured_issues (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            category TEXT NOT NULL,
            code_location TEXT,
            message TEXT NOT NULL,
            stack_trace TEXT,
            context TEXT,
            status TEXT DEFAULT 'new',
            assigned_to_ai TEXT,
            root_cause_id TEXT,
            resolution TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_issues_session_status
        ON auto_captured_issues(session_id, status)
    """)

    logger.info("✓ Created auto_captured_issues table and index")


# Migration 17: Add project_type and project_tags for multi-project workspace management
def migration_017_project_type_and_tags(cursor: sqlite3.Cursor):
    """
    Add project classification fields for workspace management.

    project_type: Categorizes project (product, application, research, documentation, infrastructure, operations)
    project_tags: JSON array of free-form tags for flexible categorization
    parent_project_id: Optional hierarchy (e.g., empirica-autonomy → empirica)
    """
    import logging

    logger = logging.getLogger(__name__)

    add_column_if_missing(cursor, "projects", "project_type", "TEXT", "'product'")
    add_column_if_missing(cursor, "projects", "project_tags", "TEXT")  # JSON array
    add_column_if_missing(cursor, "projects", "parent_project_id", "TEXT")

    # Add index for type-based queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_type ON projects(project_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_parent ON projects(parent_project_id)")

    logger.info("✓ Added project_type, project_tags, and parent_project_id to projects table")


# Migration 18: Add project_relationships table for cross-project links
def migration_018_project_relationships(cursor: sqlite3.Cursor):
    """
    Create project_relationships table for explicit cross-project links.

    This complements knowledge_graph by providing a simpler, project-focused view.
    Types: depends_on, blocks, shares_domain, cross_learns, parent_of
    """
    import logging

    logger = logging.getLogger(__name__)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS project_relationships (
            id TEXT PRIMARY KEY,
            source_project_id TEXT NOT NULL,
            target_project_id TEXT NOT NULL,
            relationship_type TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            notes TEXT,
            created_at REAL NOT NULL,
            created_by_ai_id TEXT,

            FOREIGN KEY (source_project_id) REFERENCES projects(id),
            FOREIGN KEY (target_project_id) REFERENCES projects(id),
            UNIQUE(source_project_id, target_project_id, relationship_type)
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_proj_rel_source ON project_relationships(source_project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_proj_rel_target ON project_relationships(target_project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_proj_rel_type ON project_relationships(relationship_type)")

    logger.info("✓ Created project_relationships table")


# Migration 19: Add cross_project_finding_links for shared learnings
def migration_019_cross_project_finding_links(cursor: sqlite3.Cursor):
    """
    Create table to link findings across projects.

    Allows a finding from project A to be marked as relevant to project B.
    Pattern borrowed from CRM's client_findings table.
    """
    import logging

    logger = logging.getLogger(__name__)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cross_project_finding_links (
            id TEXT PRIMARY KEY,
            finding_id TEXT NOT NULL,
            source_project_id TEXT NOT NULL,
            target_project_id TEXT NOT NULL,
            relevance REAL DEFAULT 1.0,
            notes TEXT,
            created_at REAL NOT NULL,
            created_by_ai_id TEXT,

            FOREIGN KEY (finding_id) REFERENCES project_findings(id),
            FOREIGN KEY (source_project_id) REFERENCES projects(id),
            FOREIGN KEY (target_project_id) REFERENCES projects(id),
            UNIQUE(finding_id, target_project_id)
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_xproj_finding_src ON cross_project_finding_links(source_project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_xproj_finding_tgt ON cross_project_finding_links(target_project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_xproj_finding_id ON cross_project_finding_links(finding_id)")

    logger.info("✓ Created cross_project_finding_links table")


# Migration 20: Add client_projects junction table for client-project relationships
def migration_020_client_projects(cursor: sqlite3.Cursor):
    """
    Create client_projects junction table for many-to-many client-project relationships.

    This fixes the schema design where engagements linked to goals instead of projects.
    Clients should link directly to projects, with engagements scoped to the relationship.

    Relationship types:
    - customer: Client is paying for work on this project
    - sponsor: Client is funding/sponsoring this project
    - partner: Collaborative relationship
    - stakeholder: Has interest but not direct ownership
    """
    import logging

    logger = logging.getLogger(__name__)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS client_projects (
            id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            relationship_type TEXT DEFAULT 'customer',
            status TEXT DEFAULT 'active',
            started_at REAL NOT NULL,
            ended_at REAL,
            notes TEXT,
            created_at REAL NOT NULL,
            created_by_ai_id TEXT,

            FOREIGN KEY (client_id) REFERENCES clients(client_id),
            FOREIGN KEY (project_id) REFERENCES projects(id),
            UNIQUE(client_id, project_id, relationship_type)
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_client_projects_client ON client_projects(client_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_client_projects_project ON client_projects(project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_client_projects_status ON client_projects(status)")

    logger.info("✓ Created client_projects junction table")


# Migration 21: Add project_id to engagements table
def migration_021_engagements_project_id(cursor: sqlite3.Cursor):
    """
    Add project_id to engagements table for direct project scoping.

    This changes the relationship model:
    - Before: client → engagement → goal → project (inverted)
    - After: client → project (via client_projects), engagement has project_id

    The goal_id remains for optional fine-grained linking to specific goals.

    NOTE: The engagements table is part of empirica-crm, not core empirica.
    This migration gracefully skips if the table doesn't exist.
    """
    import logging

    logger = logging.getLogger(__name__)

    # Check if engagements table exists (it's part of empirica-crm)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='engagements'")
    if not cursor.fetchone():
        logger.info("⏭ Skipping migration 021: engagements table not present (empirica-crm not installed)")
        return

    add_column_if_missing(cursor, "engagements", "project_id", "TEXT")

    # Add index for project-based queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_engagements_project ON engagements(project_id)")

    logger.info("✓ Added project_id to engagements table")


# Migration 22: Add project_id to reflexes for project-aware PREFLIGHT tracking
def migration_022_reflexes_project_id(cursor: sqlite3.Cursor):
    """
    Add project_id to reflexes table for project-aware epistemic assessments.

    This enables the sentinel gate to detect when the AI switches between projects
    within the same session and require a new PREFLIGHT assessment for the new
    project context.

    Sessions are TEMPORAL (bounded by context windows/compactions).
    Goals are STRUCTURAL (persist across sessions).
    PREFLIGHT assessments are now PROJECT-SCOPED (valid for specific project context).
    """
    add_column_if_missing(cursor, "reflexes", "project_id", "TEXT")

    # Add index for project-based queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reflexes_project ON reflexes(project_id)")


# Migration 23: Add parent_session_id to sessions for sub-agent lineage tracking
def migration_023_sessions_parent_session_id(cursor: sqlite3.Cursor):
    """
    Add parent_session_id to sessions table for epistemic lineage tracking.

    When a sub-agent (e.g., test-goal-agent) creates its own session,
    parent_session_id links it back to the spawning session. This enables:
    - Epistemic lineage queries (who spawned whom)
    - Finding rollup from child sessions to parent
    - Preventing session file stomping (child sessions are explicitly linked)
    - Multi-agent coordination with clear provenance
    """
    import logging

    logger = logging.getLogger(__name__)

    add_column_if_missing(cursor, "sessions", "parent_session_id", "TEXT")

    # Index for parent-child queries (find all children of a session)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id)")

    logger.info("✓ Added parent_session_id to sessions table")


# Migration 24: Add attention_budgets and rollup_logs tables for epistemic attention budget
def migration_024_attention_budgets(cursor: sqlite3.Cursor):
    """
    Add tables for Epistemic Attention Budget system.

    attention_budgets: Track token/finding budgets allocated to parallel agent orchestration.
    rollup_logs: Record scored rollup decisions (accepted/rejected findings from sub-agents).
    """
    import logging

    logger = logging.getLogger(__name__)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attention_budgets (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            total_budget INTEGER NOT NULL,
            allocated INTEGER DEFAULT 0,
            remaining INTEGER NOT NULL,
            strategy TEXT DEFAULT 'information_gain',
            domain_allocations TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,

            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_attention_budgets_session ON attention_budgets(session_id)")
    logger.info("✓ Created attention_budgets table")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rollup_logs (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            budget_id TEXT,
            agent_name TEXT NOT NULL,
            finding_hash TEXT NOT NULL,
            finding_text TEXT,
            score REAL NOT NULL,
            accepted BOOLEAN NOT NULL,
            reason TEXT,
            novelty REAL,
            domain_relevance REAL,
            timestamp REAL NOT NULL,

            FOREIGN KEY (session_id) REFERENCES sessions(session_id),
            FOREIGN KEY (budget_id) REFERENCES attention_budgets(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rollup_logs_session ON rollup_logs(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rollup_logs_budget ON rollup_logs(budget_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rollup_logs_hash ON rollup_logs(finding_hash)")
    logger.info("✓ Created rollup_logs table")

    logger.info("✅ Migration 024 complete: Attention budget tables created")


def migration_025_transaction_id(cursor: sqlite3.Cursor):
    """
    Add transaction_id to epistemic artifact tables.

    Makes epistemic transactions first-class entities. A transaction_id (UUID)
    is generated at PREFLIGHT and links all artifacts (findings, unknowns,
    dead-ends, mistakes, assessments) created within that measurement window
    through to POSTFLIGHT.

    Enables:
    - Query all work within a transaction boundary
    - Explicit PREFLIGHT↔POSTFLIGHT linkage (replaces implicit timestamp ordering)
    - Cross-goal transaction boundaries for multi-goal sessions
    """
    # Core assessment table
    add_column_if_missing(cursor, "reflexes", "transaction_id", "TEXT")

    # Noetic artifact tables
    add_column_if_missing(cursor, "project_findings", "transaction_id", "TEXT")
    add_column_if_missing(cursor, "project_unknowns", "transaction_id", "TEXT")
    add_column_if_missing(cursor, "project_dead_ends", "transaction_id", "TEXT")
    add_column_if_missing(cursor, "mistakes_made", "transaction_id", "TEXT")

    # Praxic artifact table
    add_column_if_missing(cursor, "goals", "transaction_id", "TEXT")

    # Indexes for transaction queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reflexes_transaction ON reflexes(transaction_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_findings_transaction ON project_findings(transaction_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_unknowns_transaction ON project_unknowns(transaction_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_dead_ends_transaction ON project_dead_ends(transaction_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mistakes_transaction ON mistakes_made(transaction_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_goals_transaction ON goals(transaction_id)")

    logger.info("✓ Migration 025: Added transaction_id columns and indexes")


# Migration 26: Add post-test verification tables for grounded calibration
def migration_026_grounded_verification(cursor: sqlite3.Cursor):
    """
    Add tables for post-test verification system.

    Grounds epistemic calibration in objective evidence (test results,
    artifact counts, goal completion) rather than self-referential
    PREFLIGHT-to-POSTFLIGHT deltas.

    grounded_beliefs: Parallel Bayesian track using evidence as observations.
    verification_evidence: Raw evidence records per session.
    grounded_verifications: Per-session comparison of self-assessed vs grounded.
    calibration_trajectory: POSTFLIGHT-to-POSTFLIGHT evolution tracking.
    """
    import logging

    logger = logging.getLogger(__name__)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS grounded_beliefs (
            belief_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            ai_id TEXT NOT NULL,
            vector_name TEXT NOT NULL,
            mean REAL NOT NULL,
            variance REAL NOT NULL,
            evidence_count INTEGER DEFAULT 0,
            last_observation REAL,
            last_observation_source TEXT,
            self_referential_mean REAL,
            divergence REAL,
            last_updated REAL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_grounded_beliefs_ai_vector
            ON grounded_beliefs(ai_id, vector_name)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS verification_evidence (
            evidence_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            source TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            raw_value TEXT,
            normalized_value REAL NOT NULL,
            quality TEXT NOT NULL,
            supports_vectors TEXT NOT NULL,
            collected_at REAL NOT NULL,
            metadata TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_verification_evidence_session
            ON verification_evidence(session_id)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS grounded_verifications (
            verification_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            ai_id TEXT NOT NULL,
            self_assessed_vectors TEXT NOT NULL,
            grounded_vectors TEXT,
            calibration_gaps TEXT,
            grounded_coverage REAL,
            overall_calibration_score REAL,
            evidence_count INTEGER DEFAULT 0,
            sources_available TEXT,
            sources_failed TEXT,
            domain TEXT,
            goal_id TEXT,
            created_at REAL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_grounded_verifications_session
            ON grounded_verifications(session_id)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS calibration_trajectory (
            point_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            ai_id TEXT NOT NULL,
            vector_name TEXT NOT NULL,
            self_assessed REAL NOT NULL,
            grounded REAL,
            gap REAL,
            domain TEXT,
            goal_id TEXT,
            timestamp REAL NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_calibration_trajectory_ai_vector
            ON calibration_trajectory(ai_id, vector_name, timestamp)
    """)

    logger.info("✅ Migration 026 complete: Post-test verification tables created")


# Migration 27: Drop deprecated session-scoped noetic tables
def migration_027_drop_session_noetic_tables(cursor: sqlite3.Cursor):
    """
    Drop deprecated session-scoped noetic artifact tables.

    These tables were created in migration 013 as part of a "dual-scope" approach
    that stored breadcrumbs in both session_* and project_* tables. This design
    was superseded:

    1. All noetic artifacts now go to project_* tables (with session_id + transaction_id)
    2. The session_* methods in BreadcrumbRepository are deprecated stubs
    3. Sessions delineate compact windows only — not epistemic boundaries
    4. Transactions are the atomic unit for epistemic measurement

    Tables dropped:
    - session_findings → use project_findings
    - session_unknowns → use project_unknowns
    - session_dead_ends → use project_dead_ends
    - session_mistakes → use mistakes_made

    This enables cleaner cross-trajectory pattern matching since all artifacts
    live in project-scoped tables with transaction_id linkage.
    """
    import logging

    logger = logging.getLogger(__name__)

    tables_to_drop = [
        "session_findings",
        "session_unknowns",
        "session_dead_ends",
        "session_mistakes",
    ]

    for table in tables_to_drop:
        try:
            # Check if table exists before dropping
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
            if cursor.fetchone():
                # Check row count for logging
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                if count > 0:
                    logger.warning(f"⚠️  Dropping {table} with {count} rows (data migrated to project_* tables)")
                cursor.execute(f"DROP TABLE {table}")
                logger.info(f"✓ Dropped deprecated table: {table}")
            else:
                logger.debug(f"✓ Table {table} already dropped or never existed")
        except Exception as e:
            logger.warning(f"⚠️  Could not drop {table}: {e}")

    logger.info("✅ Migration 027 complete: Deprecated session noetic tables dropped")


def migration_028_investigation_branches_transaction_id(cursor: sqlite3.Cursor):
    """
    Add transaction_id to investigation_branches for epistemic continuity.

    Sub-agent branches should participate in the parent's epistemic transaction,
    allowing their learnings to contribute to the parent's POSTFLIGHT delta and
    grounded calibration.
    """
    import logging

    logger = logging.getLogger(__name__)

    add_column_if_missing(cursor, "investigation_branches", "transaction_id", "TEXT")
    logger.info("✅ Migration 028 complete: Added transaction_id to investigation_branches")


def migration_029_goals_transaction_index(cursor: sqlite3.Cursor):
    """
    Add index on goals.transaction_id for efficient transaction-scoped queries.

    Goals are structurally project-scoped but temporally transaction-scoped.
    Transactions (PREFLIGHT→POSTFLIGHT measurement windows) span compaction
    boundaries, making them the natural scope for epistemic measurement.

    This index enables:
    - get_transaction_goals(transaction_id)
    - query_goals_by_transaction()
    - Transaction-scoped goal completion tracking
    """
    import logging

    logger = logging.getLogger(__name__)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_goals_transaction_id
        ON goals(transaction_id)
    """)
    logger.info("✅ Migration 029 complete: Added index on goals.transaction_id")


# Migration 30: Entity-agnostic columns + assumptions/decisions tables (v0.6.0)
def migration_030_entity_agnostic_intent_layer(cursor: sqlite3.Cursor):
    """Add entity_type/entity_id to artifact tables, create assumptions and decisions tables."""
    # Add entity_type/entity_id to existing artifact tables
    for table in [
        "project_findings",
        "project_unknowns",
        "project_dead_ends",
        "mistakes_made",
        "epistemic_sources",
        "goals",
    ]:
        add_column_if_missing(cursor, table, "entity_type", "TEXT", "'project'")
        add_column_if_missing(cursor, table, "entity_id", "TEXT")

    # Backfill entity_id from project_id
    for table in [
        "project_findings",
        "project_unknowns",
        "project_dead_ends",
        "mistakes_made",
        "epistemic_sources",
        "goals",
    ]:
        cursor.execute(f"UPDATE {table} SET entity_id = project_id WHERE entity_id IS NULL")

    # assumptions and decisions tables created via SCHEMAS (CREATE IF NOT EXISTS)
    logger.info("✅ Migration 030 complete: Entity-agnostic intent layer columns added")


def migration_031_phase_aware_calibration(cursor: sqlite3.Cursor):
    """Add phase column to grounded verification tables for noetic/praxic calibration split."""
    add_column_if_missing(cursor, "grounded_beliefs", "phase", "TEXT", "'combined'")
    add_column_if_missing(cursor, "grounded_verifications", "phase", "TEXT", "'combined'")
    add_column_if_missing(cursor, "calibration_trajectory", "phase", "TEXT", "'combined'")

    # Index for phase-filtered trajectory queries (earned autonomy threshold computation)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_calibration_trajectory_phase
            ON calibration_trajectory(ai_id, phase, vector_name, timestamp)
    """)
    logger.info("✅ Migration 031 complete: Phase-aware calibration columns added")


def migration_032_calibration_disputes(cursor: sqlite3.Cursor):
    """Add calibration_disputes table for AI pushback on measurement artifacts."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS calibration_disputes (
            dispute_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            vector TEXT NOT NULL,
            reported_value REAL NOT NULL,
            expected_value REAL NOT NULL,
            reason TEXT NOT NULL,
            evidence TEXT,
            work_context TEXT,
            status TEXT DEFAULT 'open',
            resolution TEXT,
            created_at REAL DEFAULT (strftime('%s', 'now')),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_calibration_disputes_session
            ON calibration_disputes(session_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_calibration_disputes_vector_status
            ON calibration_disputes(vector, status)
    """)
    logger.info("✅ Migration 032 complete: calibration_disputes table created")


def migration_034_subagent_sessions(cursor: sqlite3.Cursor):
    """
    Move subagent child sessions out of the main `sessions` table into a
    dedicated `subagent_sessions` table.

    Background: SubagentStart hook used to call SessionDatabase.create_session
    for every Task spawn (Explore, general-purpose, superpowers:* etc.),
    polluting the main sessions table. Subagent rows are newer than parents,
    so post-compact and other "recent sessions" diagnostics surfaced only
    subagent children — masking missing parents and adding visual clutter
    to dashboards and queries that don't filter on parent_session_id.

    This migration:
      1. Creates the subagent_sessions table (no-op if SCHEMAS already
         created it on a fresh DB).
      2. Copies all sessions rows where parent_session_id IS NOT NULL into
         subagent_sessions.
      3. Deletes those rows from the main sessions table.

    Safe: only rows that were children of a Task spawn are touched. The
    parent's row (parent_session_id IS NULL) stays in `sessions`.
    """
    import logging

    logger = logging.getLogger(__name__)

    # Step 1: ensure subagent_sessions table exists (idempotent — fresh
    # installs already created it via SCHEMAS).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subagent_sessions (
            session_id TEXT PRIMARY KEY,
            agent_name TEXT NOT NULL,
            parent_session_id TEXT NOT NULL,
            project_id TEXT,
            instance_id TEXT,
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'active',
            rollup_summary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Step 2: count rows to migrate (for the log message + safety check)
    cursor.execute("""
        SELECT COUNT(*) FROM sessions WHERE parent_session_id IS NOT NULL
    """)
    to_migrate = cursor.fetchone()[0]

    if to_migrate == 0:
        logger.info("✅ Migration 034 complete: no subagent rows to move")
        return

    # Step 3: copy subagent children into the new table.
    # ai_id is repurposed as agent_name. Status defaults to 'completed'
    # because legacy rows we're migrating already had end_time set or are
    # orphaned (no live subagent will be using them).
    cursor.execute("""
        INSERT OR IGNORE INTO subagent_sessions (
            session_id, agent_name, parent_session_id,
            project_id, instance_id, start_time, end_time,
            status, created_at
        )
        SELECT
            session_id, ai_id, parent_session_id,
            project_id, instance_id, start_time, end_time,
            CASE WHEN end_time IS NOT NULL THEN 'completed' ELSE 'orphaned' END,
            created_at
        FROM sessions
        WHERE parent_session_id IS NOT NULL
    """)
    migrated = cursor.rowcount

    # Step 4: delete the migrated rows from the main sessions table.
    cursor.execute("""
        DELETE FROM sessions WHERE parent_session_id IS NOT NULL
    """)
    deleted = cursor.rowcount

    logger.info(
        f"✅ Migration 034 complete: moved {migrated}/{to_migrate} subagent "
        f"sessions to subagent_sessions table, deleted {deleted} from sessions"
    )


def migration_033_codebase_model(cursor: sqlite3.Cursor):
    """
    Add codebase model tables for temporal entity tracking.

    Tables are created via SCHEMAS (CREATE IF NOT EXISTS) so this migration
    is a no-op for fresh installs. For upgrades, it ensures the tables exist
    and adds FTS5 for fact full-text search.

    Inspired by world-model-mcp (MIT, github.com/Nubaeon/world-model-mcp).
    """
    import logging

    logger = logging.getLogger(__name__)

    # Tables are created via codebase_model_schema.py SCHEMAS (idempotent).
    # This migration adds FTS5 virtual table for fact search.
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS codebase_facts_fts USING fts5(
            fact_text,
            content='codebase_facts',
            content_rowid='rowid'
        )
    """)

    # Sync triggers for FTS5
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS codebase_facts_ai AFTER INSERT ON codebase_facts BEGIN
            INSERT INTO codebase_facts_fts(rowid, fact_text) VALUES (new.rowid, new.fact_text);
        END
    """)
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS codebase_facts_ad AFTER DELETE ON codebase_facts BEGIN
            DELETE FROM codebase_facts_fts WHERE rowid = old.rowid;
        END
    """)
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS codebase_facts_au AFTER UPDATE ON codebase_facts BEGIN
            UPDATE codebase_facts_fts SET fact_text = new.fact_text WHERE rowid = new.rowid;
        END
    """)

    logger.info("✅ Migration 033 complete: Codebase model tables and FTS5 index created")


ALL_MIGRATIONS: list[tuple[str, str, Callable]] = [
    (
        "001_cascade_workflow_columns",
        "Add CASCADE workflow tracking to cascades",
        migration_001_cascade_workflow_columns,
    ),
    ("002_epistemic_delta", "Add epistemic delta JSON to cascades", migration_002_epistemic_delta),
    ("003_cascade_goal_tracking", "Add goal tracking to cascades", migration_003_cascade_goal_tracking),
    ("004_goals_status", "Add status column to goals", migration_004_goals_status),
    ("005_sessions_project_id", "Add project_id to sessions", migration_005_sessions_project_id),
    ("006_sessions_subject", "Add subject filtering to sessions", migration_006_sessions_subject),
    ("007_findings_impact", "Add impact scoring to project_findings", migration_007_findings_impact),
    (
        "008_migrate_legacy_to_reflexes",
        "Migrate legacy epistemic tables to reflexes",
        migration_008_migrate_legacy_to_reflexes,
    ),
    ("009_goals_project_id", "Add project_id to goals table", migration_009_goals_project_id),
    ("010_sessions_bootstrap_level", "Add bootstrap_level to sessions", migration_010_sessions_bootstrap_level),
    ("011_mistakes_project_id", "Add project_id to mistakes_made", migration_011_mistakes_project_id),
    ("012_unknowns_impact", "Add impact scoring to project_unknowns", migration_012_unknowns_impact),
    (
        "013_session_scoped_breadcrumbs",
        "Add session-scoped breadcrumb tables (dual-scope Phase 1)",
        migration_013_session_scoped_breadcrumbs,
    ),
    (
        "014_lessons_and_knowledge_graph",
        "Add lessons and knowledge graph tables for epistemic procedural knowledge",
        migration_014_lessons_and_knowledge_graph,
    ),
    (
        "015_sessions_instance_id",
        "Add instance_id to sessions for multi-instance isolation",
        migration_015_sessions_instance_id,
    ),
    (
        "016_auto_captured_issues",
        "Add auto_captured_issues table for issue tracking",
        migration_016_auto_captured_issues,
    ),
    (
        "017_project_type_and_tags",
        "Add project_type, project_tags, parent_project_id for workspace management",
        migration_017_project_type_and_tags,
    ),
    (
        "018_project_relationships",
        "Add project_relationships table for cross-project links",
        migration_018_project_relationships,
    ),
    (
        "019_cross_project_finding_links",
        "Add cross_project_finding_links for shared learnings",
        migration_019_cross_project_finding_links,
    ),
    (
        "020_client_projects",
        "Add client_projects junction table for client-project relationships",
        migration_020_client_projects,
    ),
    (
        "021_engagements_project_id",
        "Add project_id to engagements for direct project scoping",
        migration_021_engagements_project_id,
    ),
    (
        "022_reflexes_project_id",
        "Add project_id to reflexes for project-aware PREFLIGHT tracking",
        migration_022_reflexes_project_id,
    ),
    (
        "023_sessions_parent_session_id",
        "Add parent_session_id to sessions for sub-agent lineage tracking",
        migration_023_sessions_parent_session_id,
    ),
    (
        "024_attention_budgets",
        "Add attention_budgets and rollup_logs tables for epistemic attention budget",
        migration_024_attention_budgets,
    ),
    (
        "025_transaction_id",
        "Add transaction_id to epistemic artifact tables for first-class transaction tracking",
        migration_025_transaction_id,
    ),
    (
        "026_grounded_verification",
        "Add post-test verification tables for grounded calibration",
        migration_026_grounded_verification,
    ),
    (
        "027_drop_session_noetic_tables",
        "Drop deprecated session-scoped noetic tables (sessions delineate compact windows only)",
        migration_027_drop_session_noetic_tables,
    ),
    (
        "028_investigation_branches_transaction_id",
        "Add transaction_id to investigation_branches for sub-agent epistemic continuity",
        migration_028_investigation_branches_transaction_id,
    ),
    (
        "029_goals_transaction_index",
        "Add index on goals.transaction_id for transaction-scoped queries",
        migration_029_goals_transaction_index,
    ),
    (
        "030_entity_agnostic_intent_layer",
        "Add entity_type/entity_id to artifact tables, assumptions and decisions tables (v0.6.0)",
        migration_030_entity_agnostic_intent_layer,
    ),
    (
        "031_phase_aware_calibration",
        "Add phase column to grounded verification tables for noetic/praxic calibration split",
        migration_031_phase_aware_calibration,
    ),
    (
        "032_calibration_disputes",
        "Add calibration_disputes table for AI pushback on measurement artifacts",
        migration_032_calibration_disputes,
    ),
    (
        "033_codebase_model",
        "Add codebase model tables for temporal entity tracking (world-model-mcp absorption)",
        migration_033_codebase_model,
    ),
    (
        "034_subagent_sessions",
        "Move subagent child sessions out of main sessions table to dedicated subagent_sessions table",
        migration_034_subagent_sessions,
    ),
    (
        "035_three_vector_storage",
        "Add three-vector storage schema for Sentinel reframe (A3 Wave 1)",
        lambda cursor: migration_035_three_vector_storage(cursor),
    ),
    (
        "036_provenance_graph",
        "Add provenance graph columns: source_refs on findings, evidence_refs on decisions, resolution_finding_id on unknowns",
        lambda cursor: migration_036_provenance_graph(cursor),
    ),
    (
        "037_composable_lessons",
        "Evolve lessons into composable epistemic patterns with abstraction levels, sharing, EKG connections, triggers, output renderers",
        lambda cursor: migration_037_composable_lessons(cursor),
    ),
    (
        "038_goal_lifecycle_simplify",
        "Simplify goal lifecycle: convert stale/blocked to in_progress, support planned status",
        lambda cursor: migration_038_goal_lifecycle_simplify(cursor),
    ),
    (
        "039_artifact_visibility",
        "Add visibility tier (public/shared/local, default shared) to artifact tables for Phase 0 visibility primitive (PROPOSAL_VISIBILITY_TIERS.md)",
        lambda cursor: migration_039_artifact_visibility(cursor),
    ),
    (
        "040_epistemic_source",
        "Add epistemic_source field (intuition/search/mixed/NULL) to artifact tables for source-aware Sentinel calibration substrate (PROMPT_FOR_EMPIRICA_CLAUDE_source_aware_sentinel.md)",
        lambda cursor: migration_040_epistemic_source(cursor),
    ),
    (
        "041_artifact_edges",
        "Add normalized artifact_edges table + backfill from data.edges JSON (v0.5 LOCAL-ARTIFACTS daemon — fixes silent edge-drop on assumptions/decisions, enables cheap inverse queries)",
        lambda cursor: migration_041_artifact_edges(cursor),
    ),
    (
        "042_impact_on_dead_ends_and_mistakes",
        "Add impact column to project_dead_ends and mistakes_made (long-lived DBs missed migrations 007/012 for these two tables — daemon /dead-ends endpoint 500s without this)",
        lambda cursor: migration_042_impact_on_dead_ends_and_mistakes(cursor),
    ),
    (
        "043_goal_description",
        "Add description TEXT column to goals (Linear/GitHub/Jira pattern: title-shaped objective + optional rich body) — extension Claude flagged the title-vs-context-rich tension after the 1000→2000 mitigation in 1.9.6",
        lambda cursor: migration_043_goal_description(cursor),
    ),
    (
        "044_source_lifecycle",
        "Add archive lifecycle columns (archived, archive_reason, archive_target_id, lifecycle_audit_log) to epistemic_sources for SOURCES_LIFECYCLE_SPEC Phase 1 (soft-delete + supersession). Empirica-Core CLI parity per the Cortex spec; empirica is the authoritative store.",
        lambda cursor: migration_044_source_lifecycle(cursor),
    ),
    (
        "045_assumption_decision_description",
        "Add description TEXT column to assumptions + decisions (markdown-first artifacts series — mirrors goals migration 043). Extension renders as prettified markdown.",
        lambda cursor: migration_045_assumption_decision_description(cursor),
    ),
    (
        "046_refdocs_to_sources",
        "Migrate project_reference_docs rows into epistemic_sources with source_type='pointer'. Phase 1 of refdocs→sources unification (goal 3d6aeb08). Idempotent — skips rows already migrated by id. The old table stays in place this phase; reader+writer switch over to sources, CLI drop + table drop in a follow-up.",
        lambda cursor: migration_046_refdocs_to_sources(cursor),
    ),
    (
        "047_drop_project_reference_docs",
        "Drop project_reference_docs table — Phase 3 of refdocs→sources unification (goal 3d6aeb08). All data was migrated to epistemic_sources(type='pointer') by migration 046; CLI surface was dropped in Phase 2 (no writers); reader was switched in Phase 1 (no readers). Final structural cleanup. Idempotent — skips when table doesn't exist (fresh DBs that never had it).",
        lambda cursor: migration_047_drop_project_reference_docs(cursor),
    ),
    (
        "048_beads_table",
        "Add beads v0 coordination-records table (HISTORICAL — the v0 bead concept retired 2026-06-02 / empirica 1.11.2; cross-practitioner coordination state lives in cortex-resident SER now per empirica-cortex SHARED_EPISTEMIC_RECORD.md). Table kept intact for legacy-row readability; no current code path writes to it.",
        lambda cursor: migration_048_beads_table(cursor),
    ),
    (
        "049_source_visibility",
        "Add visibility tier column to epistemic_sources (substrate prereq for cross-mesh epistemic source map). Sources missed migration 039's visibility wave because source-add uses a hand-rolled INSERT rather than the breadcrumbs repo path. Default 'shared' matches the artifact-table invariant.",
        lambda cursor: migration_049_source_visibility(cursor),
    ),
    (
        "050_source_content_identity",
        "Add content-identity columns (content_hash, size_bytes, canonical_path, mime_type) to epistemic_sources — empirica slice of the unified source-identity model: reconcile matching + sync-when-small both key on content identity; canonical_path ends the source_url path/URL overload behind the title-in-url bug class.",
        lambda cursor: migration_050_source_content_identity(cursor),
    ),
    (
        "051_goals_engagement_id",
        "Add nullable engagement_id column to goals — scopes a goal to an engagement (the artifact → goal → engagement linkage of the engagement substrate). Cross-db by-id reference (goals in sessions.db, engagements in workspace.db); no FK, enforced at the API layer.",
        lambda cursor: migration_051_goals_engagement_id(cursor),
    ),
    (
        "052_weave_enforce_events",
        "Create weave_enforce_events — durable telemetry for the artifact-graph gate. The weave-enforce verdict at CHECK (connectivity vs floor, band, block/override) was computed transiently and never persisted; this table is the durable record one row per CHECK, written fail-open. Feeds enforcement-report telemetry and the adaptive-enforcement (patience) consecutive-miss history.",
        lambda cursor: migration_052_weave_enforce_events(cursor),
    ),
    (
        "053_blindspot_events",
        "Create blindspot_events — durable substrate for blindspot detection: surfaced candidates + outcome (surfaced/acknowledged/dismissed/regretted). Written fail-open. Feeds blindspot-report telemetry and the POSTFLIGHT regret loop (a dismissed blindspot that later became a mistake/dead-end). Instrument-before-surface.",
        lambda cursor: migration_053_blindspot_events(cursor),
    ),
]


def migration_035_three_vector_storage(cursor: sqlite3.Cursor):
    """Add three-vector storage columns + compliance_checks table (A3 Wave 1).

    Additive only — no column renames, no type changes, no constraint changes.
    Legacy rows readable: NULL new columns map to legacy defaults at read time.

    New columns on grounded_verifications:
      - observed_vectors: the service-computed vectors (what was called 'grounded_vectors')
      - grounded_rationale: AI's reasoning for divergence from observed
      - criticality: domain criticality level
      - compliance_status: compliance loop state
      - parent_transaction_id: for iteration tracking

    New column on calibration_trajectory:
      - state_type: 'self_assessed' | 'observed' | 'grounded' (default 'grounded')

    New table:
      - compliance_checks: per-check results with Brier prediction fields
    """
    # grounded_verifications — additive columns
    add_column_if_missing(cursor, "grounded_verifications", "observed_vectors", "TEXT")
    add_column_if_missing(cursor, "grounded_verifications", "grounded_rationale", "TEXT")
    add_column_if_missing(cursor, "grounded_verifications", "criticality", "TEXT")
    add_column_if_missing(cursor, "grounded_verifications", "compliance_status", "TEXT")
    add_column_if_missing(cursor, "grounded_verifications", "parent_transaction_id", "TEXT")

    # calibration_trajectory — state_type for three-vector filtering
    add_column_if_missing(cursor, "calibration_trajectory", "state_type", "TEXT", "'grounded'")

    # compliance_checks table — per-check results
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS compliance_checks (
            check_record_id TEXT PRIMARY KEY,
            transaction_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            check_id TEXT NOT NULL,
            tool TEXT NOT NULL,
            passed INTEGER NOT NULL,
            details TEXT,
            summary TEXT NOT NULL,
            duration_ms INTEGER NOT NULL,
            ran_at REAL NOT NULL,
            predicted_pass REAL,
            predicted_at REAL,
            iteration_number INTEGER DEFAULT 1
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_compliance_checks_tx ON compliance_checks(transaction_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_compliance_checks_check_id ON compliance_checks(check_id)")

    logger.info("✅ Migration 035 complete: Three-vector storage schema added (A3 Wave 1)")


def migration_036_provenance_graph(cursor: sqlite3.Cursor):
    """Add provenance graph columns for source→finding→decision traceability.

    Additive only — all columns NULL-defaulted. Existing artifacts unaffected.

    New columns:
      - project_findings.source_refs: JSON array of source IDs (from source-add)
      - decisions.evidence_refs: JSON array of finding IDs (evidence for this decision)
      - project_unknowns.resolution_finding_id: finding ID that resolved this unknown
    """
    add_column_if_missing(cursor, "project_findings", "source_refs", "TEXT")
    add_column_if_missing(cursor, "decisions", "evidence_refs", "TEXT")
    add_column_if_missing(cursor, "project_unknowns", "resolution_finding_id", "TEXT")

    logger.info("✅ Migration 036 complete: Provenance graph columns added")


def migration_037_composable_lessons(cursor: sqlite3.Cursor):
    """Evolve lessons into composable epistemic patterns.

    Adds fields for:
    - Abstraction levels (personal → project → domain → cross-org)
    - Sharing policy (private → licensed)
    - EKG entity connections
    - Trigger model (schedule, state_change, event)
    - Output rendering (template, llm, notebooklm, google_workspace)
    - Feedback loop (execution count, feedback score)
    - Cross-org pattern matching (abstract_pattern canonical name)

    Also extends lesson_steps with query_pattern and cache_tier
    for Cortex cache integration.

    All columns NULL-defaulted. Existing lessons (if any) unaffected.
    """
    # ── lessons table: abstraction and sharing ──
    add_column_if_missing(cursor, "lessons", "abstraction_level", "TEXT", "'personal'")
    add_column_if_missing(cursor, "lessons", "sharing_policy", "TEXT", "'private'")
    add_column_if_missing(cursor, "lessons", "abstract_pattern", "TEXT")
    add_column_if_missing(cursor, "lessons", "parent_lesson_id", "TEXT")

    # ── lessons table: EKG connections ──
    add_column_if_missing(cursor, "lessons", "entity_ids", "TEXT")  # JSON array
    add_column_if_missing(cursor, "lessons", "project_id", "TEXT")
    add_column_if_missing(cursor, "lessons", "org_id", "TEXT")
    add_column_if_missing(cursor, "lessons", "user_id", "TEXT")

    # ── lessons table: trigger model ──
    add_column_if_missing(cursor, "lessons", "trigger_type", "TEXT")  # schedule|state_change|event|manual|suggestion
    add_column_if_missing(cursor, "lessons", "trigger_config", "TEXT")  # JSON

    # ── lessons table: output rendering ──
    add_column_if_missing(cursor, "lessons", "output_format", "TEXT", "'markdown'")
    add_column_if_missing(cursor, "lessons", "output_renderer", "TEXT", "'template'")
    add_column_if_missing(cursor, "lessons", "output_config", "TEXT")  # JSON

    # ── lessons table: feedback loop ──
    add_column_if_missing(cursor, "lessons", "execution_count", "INTEGER", "0")
    add_column_if_missing(cursor, "lessons", "feedback_score", "REAL", "0.0")
    add_column_if_missing(cursor, "lessons", "last_executed", "REAL")
    add_column_if_missing(cursor, "lessons", "last_feedback", "REAL")

    # ── lesson_steps table: Cortex integration ──
    add_column_if_missing(cursor, "lesson_steps", "query_pattern", "TEXT")  # JSON: Qdrant query spec
    add_column_if_missing(cursor, "lesson_steps", "cache_tier", "TEXT")  # frozen|cold|search|warm|hot
    add_column_if_missing(cursor, "lesson_steps", "requires_auth", "TEXT")  # what API keys needed

    # ── indexes for efficient queries ──
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_abstraction ON lessons(abstraction_level)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_sharing ON lessons(sharing_policy)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_pattern ON lessons(abstract_pattern)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_project ON lessons(project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_org ON lessons(org_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_domain ON lessons(domain)")

    logger.info("✅ Migration 037 complete: Composable epistemic patterns schema added")


def migration_038_goal_lifecycle_simplify(cursor: sqlite3.Cursor):
    """Simplify goal lifecycle: planned/in_progress/completed.

    Converts stale and blocked goals to in_progress (they stay active
    across compaction). The stale status was noise — goals should be
    either planned, in_progress, or completed.
    """
    cursor.execute("""
        UPDATE goals SET status = 'in_progress'
        WHERE status IN ('stale', 'blocked')
    """)
    rows = cursor.rowcount
    if rows:
        logger.info(f"  Converted {rows} stale/blocked goals to in_progress")
    logger.info("✅ Migration 038 complete: Goal lifecycle simplified")


def migration_039_artifact_visibility(cursor: sqlite3.Cursor):
    """Add visibility tier field to all artifact tables (Phase 0 visibility primitive).

    Visibility tiers:
      - 'public':  publicly shareable (generic technical content, public-RFC citations)
      - 'shared':  team-private, co-versioned (default — safest invariant)
      - 'local':   machine-local, never shared (raw secrets, session state)

    Tables affected (post-027 set; session_* mirrors were dropped in migration 027):
      - project_findings, project_unknowns, project_dead_ends
      - mistakes_made, assumptions, decisions, goals

    Phase 0 is metadata-only — no encryption. Validation of the enum happens at
    the CLI/repository layer in Python (the helper does not support CHECK
    constraints in ALTER TABLE ADD COLUMN). Default 'shared' on all existing rows.

    See docs/architecture/PROPOSAL_VISIBILITY_TIERS.md.
    """
    artifact_tables = [
        "project_findings",
        "project_unknowns",
        "project_dead_ends",
        "mistakes_made",
        "assumptions",
        "decisions",
        "goals",
    ]
    for table in artifact_tables:
        add_column_if_missing(cursor, table, "visibility", "TEXT", "'shared'")
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_visibility ON {table}(visibility)")

    logger.info(f"✅ Migration 039 complete: visibility column added to {len(artifact_tables)} artifact tables")


def migration_040_epistemic_source(cursor: sqlite3.Cursor):
    """Add epistemic_source field to artifact tables (source-aware Sentinel substrate).

    Source tags how the AI arrived at the artifact:
      - 'intuition': training data + already-loaded session context, no
        external retrieval since the goal opened
      - 'search':    external retrieval this session (file read, grep, glob,
        web fetch, MCP tool call, project_search, etc.)
      - 'mixed':     both intuition and search contributed
      - NULL:        legacy / not yet tagged (default for back-compat)

    Tables affected (same set as visibility migration 039, minus goals
    which describe intent rather than epistemic content):
      - project_findings, project_unknowns, project_dead_ends
      - mistakes_made, assumptions, decisions

    Per-goal source ratios become a calibration signal — vectors asserted
    high while every artifact is intuition-tagged is the gaming surface
    described in ecodex's brief. v0 is data-primitive only; the routing
    rule (gate route to "investigate" when claims are high but evidence
    is all-intuition) is deferred until calibration history accumulates.

    See docs/architecture/PROMPT_FOR_EMPIRICA_CLAUDE_source_aware_sentinel.md.
    """
    artifact_tables = [
        "project_findings",
        "project_unknowns",
        "project_dead_ends",
        "mistakes_made",
        "assumptions",
        "decisions",
    ]
    for table in artifact_tables:
        add_column_if_missing(cursor, table, "epistemic_source", "TEXT")
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_epistemic_source ON {table}(epistemic_source)")

    logger.info(f"✅ Migration 040 complete: epistemic_source column added to {len(artifact_tables)} artifact tables")


def migration_041_artifact_edges(cursor: sqlite3.Cursor):
    """Add normalized artifact_edges table + backfill from denormalized data.edges JSON.

    Replaces the denormalized edge storage (`{table}.{type}_data` JSON column with
    `data.edges = [{to, relation}, ...]`) with a real relational table:

        artifact_edges(from_id, to_id, relation, created_at, metadata)

    Why: inverse queries ("what points AT this finding?") were O(N tables × N rows)
    before — required scanning every artifact table's data column. Now: O(log n)
    via the (to_id, relation) index. Also fixes a silent bug where edges from
    `assumptions` and `decisions` (which had no data column) were silently dropped
    by the old `_store_edge` helper.

    metadata JSON is forward-compat — lets edges carry per-edge confidence,
    created_by, epistemic_source without future migrations.

    Backfill: scans data.edges JSON in tables that have it and inserts into
    artifact_edges. Idempotent via PRIMARY KEY (from_id, to_id, relation) +
    INSERT OR IGNORE. Safe to run multiple times.

    Companion to v0.5-LOCAL-ARTIFACTS spec — daemon `/api/v1/artifacts/graph`
    endpoint depends on this table for cheap recursive traversal.
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS artifact_edges (
            from_id    TEXT NOT NULL,
            to_id      TEXT NOT NULL,
            relation   TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata   TEXT,
            PRIMARY KEY (from_id, to_id, relation)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_artifact_edges_to ON artifact_edges(to_id, relation)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_artifact_edges_from ON artifact_edges(from_id)")

    # Backfill from existing data.edges JSON. Only tables that had a data column
    # before this migration — assumptions/decisions had none, so no edges to backfill.
    import json as _json

    backfill_tables = [
        ("project_findings", "id", "finding_data"),
        ("project_unknowns", "id", "unknown_data"),
        ("project_dead_ends", "id", "dead_end_data"),
        ("mistakes_made", "id", "mistake_data"),
        ("goals", "id", "goal_data"),
    ]
    backfilled_total = 0
    for table, id_col, data_col in backfill_tables:
        try:
            cursor.execute(f"SELECT {id_col}, {data_col} FROM {table} WHERE {data_col} IS NOT NULL")
            rows = cursor.fetchall()
        except sqlite3.OperationalError:
            continue  # table doesn't exist in this DB (fresh / partial schema)
        for row in rows:
            from_id, raw = row[0], row[1]
            if not raw:
                continue
            try:
                data = _json.loads(raw)
            except (_json.JSONDecodeError, TypeError):
                continue
            edges = data.get("edges") or []
            for edge in edges:
                to_id = edge.get("to")
                relation = edge.get("relation")
                if not (to_id and relation):
                    continue
                try:
                    cursor.execute(
                        "INSERT OR IGNORE INTO artifact_edges (from_id, to_id, relation) VALUES (?, ?, ?)",
                        (from_id, to_id, relation),
                    )
                    backfilled_total += cursor.rowcount
                except sqlite3.Error:
                    continue

    logger.info(f"✅ Migration 041 complete: artifact_edges table created, {backfilled_total} edges backfilled")


def migration_047_drop_project_reference_docs(cursor: sqlite3.Cursor):
    """Drop the project_reference_docs table.

    Phase 3 of refdocs → sources unification. Migration 046 moved all
    rows into epistemic_sources(source_type='pointer'); Phase 2 (CLI
    removal) ensured no writers remained; Phase 1 (reader switch)
    ensured no readers remained. This migration completes the unification
    by removing the now-empty/orphaned table.

    Idempotent — DROP TABLE IF EXISTS handles:
      - Long-lived DBs that have the table (drops it).
      - Fresh DBs initialized after schema 7 was removed (no-op).
      - Re-runs on already-migrated DBs (no-op).

    Note: this also auto-drops the idx_project_reference_docs_project
    index (SQLite drops dependent indexes when a table is dropped).
    """
    # Sanity log: row count before drop, so audit shows how many rows
    # were ever in the legacy table for this DB (useful for diffing
    # against the migration 046 'migrated' count).
    try:
        cursor.execute("SELECT COUNT(*) FROM project_reference_docs")
        count = cursor.fetchone()[0]
        had_table = True
    except sqlite3.OperationalError:
        # Table already absent
        count = 0
        had_table = False

    cursor.execute("DROP TABLE IF EXISTS project_reference_docs")
    if had_table:
        logger.info(
            f"✅ Migration 047 complete: dropped project_reference_docs "
            f"({count} rows freed; data already in epistemic_sources via 046)"
        )
    else:
        logger.info("✅ Migration 047 complete: project_reference_docs already absent (no-op)")


def migration_046_refdocs_to_sources(cursor: sqlite3.Cursor):
    """Copy project_reference_docs rows into epistemic_sources(type='pointer').

    Phase 1 of the refdocs → sources unification. Refdocs were a
    pre-sources artifact type that's effectively a subset of what
    epistemic_sources represents (a registered, citable reference).
    This migration moves the rows into the unified store; the
    breadcrumbs reader + add_reference_doc writer are switched to
    read/write epistemic_sources WHERE source_type='pointer' in
    the same change set.

    Field mapping:
      project_reference_docs       → epistemic_sources
      ───────────────────────────────────────────────────────
      id                           → id                  (preserved)
      project_id                   → project_id          (preserved)
      doc_path                     → source_url
      doc_type                     → source_metadata.doc_type
      description                  → description
      created_timestamp (epoch)    → discovered_at (datetime)
      doc_data (json)              → source_metadata.original_doc_data
      (synthesized)                  source_type        = 'pointer'
      (synthesized)                  title              = basename(doc_path) or doc_path
      (default)                      confidence         = 0.7
      (default)                      epistemic_layer    = 'noetic'

    Idempotent — skips rows whose id already exists in epistemic_sources.
    Safe to re-run; long-lived DBs missing the migration get backfilled
    on next SessionDatabase init.

    The old table is NOT dropped in this migration — keep both around
    during the transition so any consumer that hasn't switched yet
    still works. Cleanup migration (TODO 047) drops the old table.
    """
    import json as _json
    import os.path
    from datetime import datetime as _dt

    # Schema 7 was removed in Phase 3 (this same goal) so fresh DBs no
    # longer have project_reference_docs at all. That's a no-op — there's
    # nothing to migrate. Long-lived DBs still have the table; we migrate
    # rows out of it here, and migration 047 drops the table after.
    try:
        cursor.execute("SELECT * FROM project_reference_docs")
    except sqlite3.OperationalError:
        logger.info("✅ Migration 046 complete: project_reference_docs absent (fresh DB, no-op)")
        return

    rows = cursor.fetchall()
    column_names = [d[0] for d in cursor.description] if cursor.description else []
    if not rows:
        logger.info("✅ Migration 046 complete: no refdocs to migrate")
        return

    migrated = 0
    skipped = 0
    for row in rows:
        row_dict = dict(zip(column_names, row, strict=False))
        doc_id = row_dict["id"]

        # Idempotence: skip if already in sources
        cursor.execute(
            "SELECT 1 FROM epistemic_sources WHERE id = ? LIMIT 1",
            (doc_id,),
        )
        if cursor.fetchone():
            skipped += 1
            continue

        doc_path = row_dict.get("doc_path") or ""
        doc_type = row_dict.get("doc_type")
        description = row_dict.get("description")
        created_ts = row_dict.get("created_timestamp") or 0.0
        doc_data_raw = row_dict.get("doc_data")
        try:
            original_doc_data = _json.loads(doc_data_raw) if doc_data_raw else None
        except (ValueError, TypeError):
            original_doc_data = doc_data_raw  # leave as string

        # Reasonable title: filename, else doc_type-coded path, else the path itself
        basename = os.path.basename(doc_path) if doc_path else ""
        title = basename or doc_path or "refdoc"

        source_metadata = {
            "doc_type": doc_type,
            "original_doc_data": original_doc_data,
            "migrated_from": "project_reference_docs",
        }
        discovered_at = _dt.fromtimestamp(created_ts) if created_ts else _dt.now()

        cursor.execute(
            """
            INSERT INTO epistemic_sources (
                id, project_id, session_id,
                source_type, source_url, title, description,
                confidence, epistemic_layer,
                supports_vectors, related_findings,
                discovered_by_ai, discovered_at,
                source_metadata
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
        """,
            (
                doc_id,
                row_dict.get("project_id"),
                "pointer",
                doc_path,
                title,
                description,
                0.7,
                "noetic",
                discovered_at,
                _json.dumps(source_metadata),
            ),
        )
        migrated += 1

    logger.info(f"✅ Migration 046 complete: refdocs → sources ({migrated} migrated, {skipped} already present)")


def migration_045_assumption_decision_description(cursor: sqlite3.Cursor):
    """Add `description` TEXT (nullable) column to assumptions + decisions.

    Mirrors migration 043 (goal description) — title-shaped primary fields
    (`assumption`, `choice`/`rationale`) get a rich-markdown body alongside,
    rendered in the extension and skill surfaces. Part of the
    markdown-first artifacts series (Goal 6a07549c) extending the goals
    pattern to all `*-log` commands.

    Backwards-compat: existing rows get description=NULL. The bootstrap
    query returns it as a regular column; consumers treat NULL as
    "title-only" and show the primary field alone.

    Idempotent via add_column_if_missing — safe to re-run.
    """
    add_column_if_missing(cursor, "assumptions", "description", "TEXT", "NULL")
    add_column_if_missing(cursor, "decisions", "description", "TEXT", "NULL")
    logger.info("✅ Migration 045 complete: description column added to assumptions + decisions")


def migration_049_source_visibility(cursor: sqlite3.Cursor):
    """Add visibility tier column to epistemic_sources.

    Sources missed migration 039 (artifact_visibility) because source-add
    uses a hand-rolled INSERT into epistemic_sources rather than the
    breadcrumbs repo path that the other artifact types go through. This
    migration closes the gap so sources participate in the same
    visibility ladder as findings, unknowns, etc.

    Tiers (per data/visibility.py):
      - 'public':  publicly shareable
      - 'shared':  team-private, co-versioned (default — safe invariant)
      - 'local':   machine-local, never shared

    Substrate prereq for the cross-mesh epistemic source map (goal
    74d35435): a source can only be a cross-mesh reference if its
    visibility tier authorises it to leave the local project.

    Idempotent via add_column_if_missing.
    """
    add_column_if_missing(cursor, "epistemic_sources", "visibility", "TEXT", "'shared'")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_epistemic_sources_visibility ON epistemic_sources(visibility)")
    logger.info("✅ Migration 049 complete: visibility column added to epistemic_sources")


def migration_050_source_content_identity(cursor: sqlite3.Cursor):
    """Add content-identity columns to epistemic_sources (unified source identity).

    Empirica slice of the cross-component source-identity model: a source
    has ONE uuid shared with the central catalogue; matching local rows to
    catalogue rows (the `sources reconcile` verb) and body-sync-when-small
    both key on content identity, which the schema couldn't express:

      - content_hash (TEXT): 'sha256:<hex>' of the file body. Algorithm-
        prefixed so future agility doesn't need a second column. The
        catalogue dedupe + reconcile compound key half.
      - size_bytes (INTEGER): body size at ingest — drives the
        sync-when-small threshold decision without re-statting.
      - canonical_path (TEXT): resolved absolute path for file-backed
        sources. Until now source_url doubled as the path field; that
        overload is the schema root of the title-in-url data-quality bug
        class. New rows separate the concerns; source_url stays for
        actual URLs.
      - mime_type (TEXT): guessed at ingest, carried in catalogue +
        redirect-hint contracts.

    All nullable — URL-only and pointer rows legitimately carry NULLs.
    Idempotent via add_column_if_missing.
    """
    add_column_if_missing(cursor, "epistemic_sources", "content_hash", "TEXT", "NULL")
    add_column_if_missing(cursor, "epistemic_sources", "size_bytes", "INTEGER", "NULL")
    add_column_if_missing(cursor, "epistemic_sources", "canonical_path", "TEXT", "NULL")
    add_column_if_missing(cursor, "epistemic_sources", "mime_type", "TEXT", "NULL")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_epistemic_sources_content_hash ON epistemic_sources(content_hash)")
    logger.info("✅ Migration 050 complete: content-identity columns added to epistemic_sources")


def migration_051_goals_engagement_id(cursor: sqlite3.Cursor):
    """Add `engagement_id` TEXT (nullable) column to goals.

    Scopes a goal to an engagement — the canonical artifact → goal →
    engagement linkage of the engagement substrate. Most goals are unscoped
    (NULL); engagement-scoped goals carry the engagement_id.

    No FK constraint: sqlite can't ALTER-add one, and goals live in sessions.db
    while the engagements table lives in workspace.db — the link is a by-id
    convention enforced at the API layer. Indexed for "goals where engagement=X"
    lookups. Idempotent via add_column_if_missing.
    """
    add_column_if_missing(cursor, "goals", "engagement_id", "TEXT")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_goals_engagement_id ON goals(engagement_id)")
    logger.info("✅ Migration 051 complete: engagement_id column added to goals")


def migration_052_weave_enforce_events(cursor: sqlite3.Cursor):
    """Create `weave_enforce_events` — durable telemetry for the artifact-graph gate.

    The weave-enforce verdict at CHECK (connectivity vs the floor, response band,
    whether it blocked and whether it overrode the decision) was computed
    transiently in `_check_apply_weave_enforce` and returned to the caller, but
    never persisted — so there was no durable source for enforce block-rate /
    self-resolve-rate telemetry, nor the consecutive-miss history that adaptive
    `patience` needs. This table is that record: one row per CHECK that produced a
    weave verdict.

    Written fail-open — a persistence failure must never affect the CHECK decision
    (the CHECK path is fleet-critical since enforce-by-default shipped). Both the
    enforcement-report telemetry and the adaptive-enforcement work-stream read it.
    Idempotent via CREATE TABLE IF NOT EXISTS.
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weave_enforce_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            transaction_id TEXT,
            created_timestamp REAL NOT NULL,
            connectivity_ratio REAL,
            connectivity_floor REAL,
            strictness REAL,
            response_band TEXT,
            enforced INTEGER NOT NULL DEFAULT 0,
            decision_in TEXT,
            decision_out TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_weave_events_txn ON weave_enforce_events(transaction_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_weave_events_session ON weave_enforce_events(session_id)")
    logger.info("✅ Migration 052 complete: weave_enforce_events table created")


def migration_053_blindspot_events(cursor: sqlite3.Cursor):
    """Create `blindspot_events` — durable substrate for blindspot detection.

    One row per surfaced blindspot candidate, plus its outcome over time. This is
    the instrument-before-surface substrate: the ``blindspot-report`` telemetry
    (surfaced / acknowledged / dismissed / regretted) and the POSTFLIGHT regret
    loop (a dismissed blindspot that later became a mistake or dead-end) both read
    it. Written fail-open — a persistence failure must never affect CHECK/POSTFLIGHT.

    - ``kind``        — signal type (``intent_gap``; later ``co_occurrence`` / ``fossil``)
    - ``surfaced_at`` — where it was raised (``scan`` / ``check`` / ``postflight``)
    - ``outcome``     — ``surfaced`` → ``acknowledged`` (logged an unknown) / ``dismissed``
                        / ``regretted`` (dismissed then became a mistake/dead-end)

    Idempotent via CREATE TABLE IF NOT EXISTS.
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS blindspot_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            transaction_id TEXT,
            created_timestamp REAL NOT NULL,
            kind TEXT,
            goal_id TEXT,
            subtask_id TEXT,
            intent TEXT,
            surfaced_at TEXT,
            outcome TEXT NOT NULL DEFAULT 'surfaced',
            resolved_timestamp REAL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_blindspot_events_txn ON blindspot_events(transaction_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_blindspot_events_session ON blindspot_events(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_blindspot_events_subtask ON blindspot_events(subtask_id)")
    logger.info("✅ Migration 053 complete: blindspot_events table created")


def migration_044_source_lifecycle(cursor: sqlite3.Cursor):
    """Add lifecycle columns to epistemic_sources for SOURCES_LIFECYCLE_SPEC Phase 1.

    Soft-delete model: sources are never hard-deleted by default. They
    transition active → archived with a reason; the audit chain (edges from
    findings to sources) is preserved untouched.

    Columns added:
      - archived (BOOL, default 0): the lifecycle gate
      - archive_reason (TEXT): user_deleted | file_missing |
        url_unreachable | superseded
      - archive_target_id (TEXT): pointer to the replacement source when
        reason='superseded' (the chain forward)
      - archived_at (REAL): epoch when archived
      - lifecycle_audit_log (TEXT, JSON): append-only event log of state
        transitions for forensics

    NOT included in this slice (Phase 2+ in the spec):
      - relevance_score, relevance_factors, relevance_computed_at
      - last_accessed_at, last_validated_at, consecutive_validation_failures
    Those belong to the daily cron pass; Phase 1 ships only the soft-delete
    primitive and CLI verb so users can drag-to-trash and forensics works.

    Idempotent via add_column_if_missing — safe to re-run.
    """
    add_column_if_missing(cursor, "epistemic_sources", "archived", "BOOLEAN", "0")
    add_column_if_missing(cursor, "epistemic_sources", "archive_reason", "TEXT", "NULL")
    add_column_if_missing(cursor, "epistemic_sources", "archive_target_id", "TEXT", "NULL")
    add_column_if_missing(cursor, "epistemic_sources", "archived_at", "REAL", "NULL")
    add_column_if_missing(cursor, "epistemic_sources", "lifecycle_audit_log", "TEXT", "NULL")
    logger.info("✅ Migration 044 complete: source lifecycle columns added to epistemic_sources")


def migration_043_goal_description(cursor: sqlite3.Cursor):
    """Add `description` TEXT (nullable) column to goals.

    The single-`objective`-field design forces a tension between
    title-shaped (~256 chars) and context-rich (1000-8000 chars) usage.
    1.9.6 bumped 1000→2000 as short-term mitigation; this is the
    structural fix mirroring how Linear / GitHub Issues / Jira split
    title + body.

    Backwards-compat: existing rows have description=NULL. Display layer
    treats NULL description as "title-only goal" and shows objective only.
    Validation drops the objective cap to ~256 chars on NEW creates only —
    existing rows are grandfathered (no retroactive validation).

    Idempotent via add_column_if_missing — safe to re-run.
    """
    add_column_if_missing(cursor, "goals", "description", "TEXT", "NULL")
    logger.info("✅ Migration 043 complete: description column added to goals")


def migration_042_impact_on_dead_ends_and_mistakes(cursor: sqlite3.Cursor):
    """Add impact column to project_dead_ends and mistakes_made on long-lived DBs.

    Migrations 007 and 012 added impact to project_findings and project_unknowns
    but never extended it to project_dead_ends or mistakes_made. Fresh DBs
    created from the schema file's CREATE TABLE statements already have the
    column (the schema file has it inline), but long-lived DBs that were
    created before that schema update never got the ALTER.

    The daemon's GET /api/v1/dead-ends endpoint (v0.5 LOCAL-ARTIFACTS T2)
    queries `impact` directly — without this migration, it 500s against
    real long-lived DBs (extension Claude caught this in their integration test).

    Idempotent via add_column_if_missing — safe to re-run.
    """
    add_column_if_missing(cursor, "project_dead_ends", "impact", "REAL", "0.5")
    add_column_if_missing(cursor, "mistakes_made", "impact", "REAL", "0.5")
    logger.info("✅ Migration 042 complete: impact column added to project_dead_ends and mistakes_made")


def migration_048_beads_table(cursor: sqlite3.Cursor):
    """Add beads table — v0 coordination-records (3-way HYBRID, 2026-05-30).

    HISTORICAL CONTEXT — RETIRED 2026-06-02 (empirica 1.11.2): the v0
    bead-as-graph-node concept retired three-way (cortex/empirica/extension)
    on 2026-06-01. Cross-practitioner coordination state lives in
    cortex-resident SER (Shared Epistemic Record) now — see
    `empirica-cortex/docs/architecture/SHARED_EPISTEMIC_RECORD.md`. The
    `bead` node type + 4 v0 edges (`tracks`/`owned_by`/`about`/`worked_by`)
    were removed from graph_commands.py NODE_REQUIRED_FIELDS +
    VALID_RELATIONS in 1.11.2. This migration is kept intact for historical
    DB consistency — existing installs run it once and inherit the table
    with their pre-retirement rows readable. Future cleanup (DROP TABLE)
    deferred to a separate migration.

    Original v0 framing (preserved as historical record): the bead was a
    COURIER tracking an actionable (goal, bd-issue, email, publish) via the
    `tracks` edge + carrying the coordination layer (state + who/about/
    who-works-it). `coordination_state` (not bare `state`) kept that
    discipline visible; `updated_at` mandatory because triage feeds ordered
    by recency-of-change on a mutable artifact.

    Idempotent — CREATE TABLE IF NOT EXISTS.
    """
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS beads (
            id TEXT PRIMARY KEY,
            coordination_state TEXT NOT NULL CHECK(coordination_state IN (
                'open', 'in_progress', 'blocked', 'closed'
            )),
            updated_at REAL NOT NULL,
            last_transition_actor TEXT,
            beads_issue_id TEXT,
            scope TEXT CHECK(scope IS NULL OR scope IN (
                'local', 'org', 'cross_org'
            )),
            description TEXT,
            entity_type TEXT NOT NULL DEFAULT 'project',
            entity_id TEXT,
            project_id TEXT,
            session_id TEXT,
            transaction_id TEXT,
            goal_id TEXT,
            created_by_ai TEXT,
            created_timestamp REAL NOT NULL,
            visibility TEXT,
            epistemic_source TEXT,

            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_beads_entity ON beads(entity_type, entity_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_beads_project ON beads(project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_beads_coordination_state ON beads(coordination_state)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_beads_beads_issue_id ON beads(beads_issue_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_beads_transaction ON beads(transaction_id)")
    logger.info("✅ Migration 048 complete: beads table created (coordination-records v0)")
