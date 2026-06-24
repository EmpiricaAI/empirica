"""
Workspace Database Repository — centralized access to ~/.empirica/workspace/workspace.db

Tables managed:
- global_projects: Cross-project registry (trajectory_path is the stable key)
- instance_bindings: TMUX pane → project mapping for multi-instance support
- global_sessions: Cross-project session tracking
- entity_artifacts: CRM entity-artifact cross-references

Usage:
    with WorkspaceDBRepository.open() as repo:
        project = repo.get_project_by_path('/path/to/myrepo')
        repo.upsert_project(project_id, name, trajectory_path, ...)
"""

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .base import BaseRepository

logger = logging.getLogger(__name__)


def _get_workspace_db_path() -> Path:
    """Get path to workspace database.

    `EMPIRICA_WORKSPACE_DB` overrides the default HOME-derived location —
    used by per-org daemon deployments where one box runs N isolated
    `empirica serve` instances, each rooted in its own workspace.db.
    """
    override = os.getenv("EMPIRICA_WORKSPACE_DB")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".empirica" / "workspace" / "workspace.db"


def _ensure_workspace_schema(conn: sqlite3.Connection) -> None:
    """Create workspace tables if they don't exist."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS global_projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            trajectory_path TEXT NOT NULL UNIQUE,
            git_remote_url TEXT,
            git_branch TEXT DEFAULT 'main',
            total_transactions INTEGER DEFAULT 0,
            total_findings INTEGER DEFAULT 0,
            total_unknowns INTEGER DEFAULT 0,
            total_dead_ends INTEGER DEFAULT 0,
            total_goals INTEGER DEFAULT 0,
            last_transaction_id TEXT,
            last_transaction_timestamp REAL,
            last_sync_timestamp REAL,
            status TEXT DEFAULT 'active',
            project_type TEXT DEFAULT 'product',
            project_tags TEXT,
            created_timestamp REAL NOT NULL,
            updated_timestamp REAL NOT NULL,
            metadata TEXT
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_global_projects_status
        ON global_projects(status)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_global_projects_type
        ON global_projects(project_type)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_global_projects_last_tx
        ON global_projects(last_transaction_timestamp)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS instance_bindings (
            instance_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            project_path TEXT,
            bound_timestamp REAL NOT NULL,
            FOREIGN KEY (project_id) REFERENCES global_projects(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS global_sessions (
            session_id TEXT PRIMARY KEY,
            ai_id TEXT,
            origin_project_id TEXT,
            current_project_id TEXT,
            instance_id TEXT,
            status TEXT DEFAULT 'active',
            parent_session_id TEXT,
            created_at REAL,
            last_activity REAL,
            FOREIGN KEY (origin_project_id) REFERENCES global_projects(id),
            FOREIGN KEY (current_project_id) REFERENCES global_projects(id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_global_sessions_instance
        ON global_sessions(instance_id, status)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_global_sessions_project
        ON global_sessions(current_project_id)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entity_artifacts (
            id TEXT PRIMARY KEY,
            artifact_type TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            artifact_source TEXT,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            relationship TEXT DEFAULT 'about',
            relevance REAL DEFAULT 1.0,
            discovered_via TEXT,
            engagement_id TEXT,
            transaction_id TEXT,
            created_at REAL,
            created_by_ai TEXT,
            UNIQUE(artifact_type, artifact_id, entity_type, entity_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_entity_artifacts_entity
        ON entity_artifacts(entity_type, entity_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_entity_artifacts_transaction
        ON entity_artifacts(transaction_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_entity_artifacts_engagement
        ON entity_artifacts(engagement_id)
    """)
    # entity_registry: the global directory of first-class entities
    # (project, contact, organization, engagement, user, …). Backs the
    # Practice Model surface (entity-list / entity-show / entity-walk /
    # entity-search).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entity_registry (
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            description TEXT,
            source_db TEXT NOT NULL,
            source_table TEXT NOT NULL,
            emoji_state TEXT,
            status TEXT DEFAULT 'active',
            created_at REAL NOT NULL,
            updated_at REAL,
            metadata TEXT,
            PRIMARY KEY (entity_type, entity_id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entity_registry_type ON entity_registry(entity_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entity_registry_status ON entity_registry(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entity_registry_emoji ON entity_registry(emoji_state)")
    # entity_memberships: M:N typed relationships between entities
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entity_memberships (
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            group_type TEXT NOT NULL,
            group_id TEXT NOT NULL,
            role TEXT,
            joined_at REAL NOT NULL,
            left_at REAL,
            created_at REAL NOT NULL,
            notes TEXT,
            PRIMARY KEY (entity_type, entity_id, group_type, group_id)
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_entity_memberships_member ON entity_memberships(entity_type, entity_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_entity_memberships_group ON entity_memberships(group_type, group_id)"
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entity_memberships_active ON entity_memberships(left_at)")

    # Engagement substrate — vendored so a fresh install without
    # empirica-workspace still gets the tables the engagement CLI + daemon read.
    _apply_engagement_substrate(cursor)
    conn.commit()


# ── Engagement substrate ─────────────────────────────────────────────────────
# Vendored from empirica-workspace's canonical schema so empirica core can stand
# the engagement substrate up on a fresh, workspace-less install (empirica core
# does not depend on the empirica-workspace package). Canonical source of truth:
#   - empirica_workspace/data/workspace_schema.py   (the 3 definition tables)
#   - empirica_workspace/data/workspace_database.py  (_seed_engagement_domains)
# Parity is asserted by tests/test_engagement_substrate_schema.py (drift-guard).

_DEFAULT_ENGAGEMENT_DOMAINS = [
    ("outreach", "Outreach", "Platform publishing, audience cultivation, content engagement"),
    ("sales", "Sales", "Commercial pipeline, qualification to close"),
    ("support", "Support", "Customer-reported issues, ticket triage and resolution"),
    ("security", "Security", "Vulnerability reports, incident response, mitigation"),
    ("infra", "Infra", "Infrastructure work, capacity, observability"),
    ("onboarding", "Onboarding", "Customer kickoff to provisioning to live"),
]

_DEFAULT_ENGAGEMENT_STAGES = [
    ("outreach.lead", "outreach", "Lead", 10),
    ("outreach.qualified", "outreach", "Qualified", 20),
    ("outreach.engaged", "outreach", "Engaged", 30),
    ("outreach.proposing", "outreach", "Proposing", 40),
    ("outreach.negotiating", "outreach", "Negotiating", 50),
    ("sales.lead", "sales", "Lead", 10),
    ("sales.qualified", "sales", "Qualified", 20),
    ("sales.proposal", "sales", "Proposal", 30),
    ("sales.negotiation", "sales", "Negotiation", 40),
    ("sales.closed", "sales", "Closed", 50),
    ("support.new", "support", "New", 10),
    ("support.triaged", "support", "Triaged", 20),
    ("support.in_progress", "support", "In progress", 30),
    ("support.waiting_customer", "support", "Waiting customer", 40),
    ("security.reported", "security", "Reported", 10),
    ("security.triaged", "security", "Triaged", 20),
    ("security.mitigating", "security", "Mitigating", 30),
    ("security.verified", "security", "Verified", 40),
    ("infra.planned", "infra", "Planned", 10),
    ("infra.in_progress", "infra", "In progress", 20),
    ("infra.deployed", "infra", "Deployed", 30),
    ("onboarding.kickoff", "onboarding", "Kickoff", 10),
    ("onboarding.provisioning", "onboarding", "Provisioning", 20),
    ("onboarding.live", "onboarding", "Live", 30),
]

# Engagement enums — enforced app-side. The engagement is an OPERATIONAL row
# (sqlite ALTER can't add CHECK), so lifecycle/outcome validity lives at the repo
# layer; domain/stage validity is checked against the definition tables.
ENGAGEMENT_LIFECYCLE_STATES = frozenset({"open", "in_progress", "blocked", "closed"})
ENGAGEMENT_OUTCOMES = frozenset({"won", "lost", "resolved", "wont_fix", "defer", "superseded"})


def _seed_engagement_domains(cursor: sqlite3.Cursor) -> None:
    """Seed the 6 default engagement domains + 24 stages (idempotent INSERT OR
    IGNORE). Mirrors empirica-workspace WorkspaceDatabase._seed_engagement_domains."""
    now = time.time()
    for did, dn, desc in _DEFAULT_ENGAGEMENT_DOMAINS:
        cursor.execute(
            "INSERT OR IGNORE INTO domain_definitions "
            "(domain_id, display_name, description, visibility, created_at) VALUES (?, ?, ?, ?, ?)",
            (did, dn, desc, "public", now),
        )
    for sid, dom, dn, ordi in _DEFAULT_ENGAGEMENT_STAGES:
        cursor.execute(
            "INSERT OR IGNORE INTO stage_definitions "
            "(stage_id, domain, display_name, ordinal, created_at) VALUES (?, ?, ?, ?, ?)",
            (sid, dom, dn, ordi, now),
        )


def _apply_engagement_substrate(cursor: sqlite3.Cursor) -> None:
    """Create the engagement-substrate tables + seed default domains/stages.

    Idempotent: CREATE TABLE IF NOT EXISTS (first-wins → converges with
    empirica-workspace's ALTER-based evolution if both run) + INSERT OR IGNORE
    seeds. The minimal engagements CREATE inlines the sidecar cols
    (lifecycle_state/stage/domain/updated_at) and omits the contacts FK; the
    lifecycle_state / outcome / domain enums are enforced at the API layer
    (sqlite ALTER can't add CHECK)."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS domain_definitions (
            domain_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            description TEXT,
            visibility TEXT DEFAULT 'shared' CHECK (visibility IN ('local', 'shared', 'public')),
            created_at REAL NOT NULL,
            created_by_ai_id TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS stage_definitions (
            stage_id TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            display_name TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            is_terminal INTEGER DEFAULT 0,
            expected_outcomes TEXT,
            created_at REAL NOT NULL,
            UNIQUE(domain, ordinal),
            UNIQUE(domain, display_name)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS practice_domains (
            practice_id TEXT NOT NULL,
            domain_id TEXT NOT NULL,
            joined_at REAL NOT NULL,
            left_at REAL,
            PRIMARY KEY (practice_id, domain_id),
            FOREIGN KEY (domain_id) REFERENCES domain_definitions(domain_id)
        )
        """
    )
    # Minimal engagements (sidecar cols inline, no contacts FK).
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS engagements (
            engagement_id TEXT PRIMARY KEY,
            contact_id TEXT,
            project_id TEXT,
            title TEXT NOT NULL,
            description TEXT,
            engagement_type TEXT DEFAULT 'outreach',
            started_at REAL,
            ended_at REAL,
            status TEXT DEFAULT 'active',
            outcome TEXT,
            lifecycle_state TEXT DEFAULT 'open',
            stage TEXT,
            domain TEXT,
            created_at REAL,
            created_by_ai_id TEXT,
            updated_at REAL
        )
        """
    )
    for idx in (
        "CREATE INDEX IF NOT EXISTS idx_stage_def_domain ON stage_definitions(domain, ordinal)",
        "CREATE INDEX IF NOT EXISTS idx_practice_domains_practice ON practice_domains(practice_id)",
        "CREATE INDEX IF NOT EXISTS idx_practice_domains_active ON practice_domains(left_at)",
        "CREATE INDEX IF NOT EXISTS idx_engagements_lifecycle ON engagements(lifecycle_state)",
        "CREATE INDEX IF NOT EXISTS idx_engagements_domain ON engagements(domain)",
        "CREATE INDEX IF NOT EXISTS idx_engagements_stage ON engagements(stage)",
    ):
        cursor.execute(idx)
    _seed_engagement_domains(cursor)


class WorkspaceDBRepository(BaseRepository):
    """Repository for workspace.db — the global project registry."""

    def __init__(self, conn: sqlite3.Connection):
        super().__init__(conn)

    @classmethod
    def open(cls, ensure_schema: bool = True) -> "WorkspaceDBRepository":
        """Open workspace.db and return a repository instance.

        Creates the database directory and schema if needed.
        The caller should close the connection when done (or use as context manager).

        Args:
            ensure_schema: If True, create tables if they don't exist.

        Returns:
            WorkspaceDBRepository instance
        """
        db_path = _get_workspace_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        if ensure_schema:
            _ensure_workspace_schema(conn)
        return cls(conn)

    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        self.close()
        return False

    # --- global_projects ---

    def get_project_by_path(self, trajectory_path: str) -> dict[str, Any] | None:
        """Look up a project by its filesystem path (the stable key)."""
        cursor = self._execute(
            "SELECT * FROM global_projects WHERE trajectory_path = ? AND status = 'active'", (str(trajectory_path),)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_project_by_id(self, project_id: str) -> dict[str, Any] | None:
        """Look up a project by UUID."""
        cursor = self._execute("SELECT * FROM global_projects WHERE id = ?", (project_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_project_by_name(self, name: str) -> dict[str, Any] | None:
        """Look up a project by name (case-insensitive)."""
        cursor = self._execute(
            "SELECT * FROM global_projects WHERE LOWER(name) = LOWER(?) AND status = 'active'", (name,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_projects(self, status: str = "active") -> list[dict[str, Any]]:
        """List all projects with given status."""
        cursor = self._execute(
            "SELECT * FROM global_projects WHERE status = ? ORDER BY updated_timestamp DESC", (status,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def upsert_project(
        self,
        project_id: str,
        name: str,
        trajectory_path: str,
        description: str = "",
        git_remote_url: str = "",
        git_branch: str = "main",
        status: str = "active",
        project_type: str = "product",
        metadata: str | None = None,
    ) -> None:
        """Insert or update a project in the global registry."""
        now = time.time()
        self._execute(
            """INSERT INTO global_projects
               (id, name, description, trajectory_path, git_remote_url, git_branch,
                status, project_type, metadata, created_timestamp, updated_timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   name = excluded.name,
                   description = excluded.description,
                   trajectory_path = excluded.trajectory_path,
                   git_remote_url = excluded.git_remote_url,
                   git_branch = excluded.git_branch,
                   status = excluded.status,
                   project_type = excluded.project_type,
                   metadata = excluded.metadata,
                   updated_timestamp = excluded.updated_timestamp
            """,
            (
                project_id,
                name,
                description,
                str(trajectory_path),
                git_remote_url,
                git_branch,
                status,
                project_type,
                metadata,
                now,
                now,
            ),
        )
        self.commit()

    def update_project_stats(
        self,
        project_id: str,
        total_transactions: int | None = None,
        total_findings: int | None = None,
        total_unknowns: int | None = None,
        total_dead_ends: int | None = None,
        total_goals: int | None = None,
        last_transaction_id: str | None = None,
        last_transaction_timestamp: float | None = None,
    ) -> None:
        """Update project statistics (transaction counts, last activity).

        Only non-None parameters are updated. Also sets updated_timestamp.

        Args:
            project_id: UUID of the project to update.
            total_transactions: Cumulative transaction count.
            total_findings: Cumulative finding count.
            total_unknowns: Cumulative unknown count.
            total_dead_ends: Cumulative dead-end count.
            total_goals: Cumulative goal count.
            last_transaction_id: UUID of the most recent transaction.
            last_transaction_timestamp: Epoch timestamp of the most recent transaction.
        """
        updates = []
        params = []
        if total_transactions is not None:
            updates.append("total_transactions = ?")
            params.append(total_transactions)
        if total_findings is not None:
            updates.append("total_findings = ?")
            params.append(total_findings)
        if total_unknowns is not None:
            updates.append("total_unknowns = ?")
            params.append(total_unknowns)
        if total_dead_ends is not None:
            updates.append("total_dead_ends = ?")
            params.append(total_dead_ends)
        if total_goals is not None:
            updates.append("total_goals = ?")
            params.append(total_goals)
        if last_transaction_id is not None:
            updates.append("last_transaction_id = ?")
            params.append(last_transaction_id)
        if last_transaction_timestamp is not None:
            updates.append("last_transaction_timestamp = ?")
            params.append(last_transaction_timestamp)

        if not updates:
            return

        updates.append("updated_timestamp = ?")
        params.append(time.time())
        params.append(project_id)

        self._execute(f"UPDATE global_projects SET {', '.join(updates)} WHERE id = ?", tuple(params))
        self.commit()

    # --- instance_bindings ---

    def get_instance_binding(self, instance_id: str) -> dict[str, Any] | None:
        """Get the project binding for a TMUX pane instance."""
        cursor = self._execute("SELECT * FROM instance_bindings WHERE instance_id = ?", (instance_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def set_instance_binding(self, instance_id: str, project_id: str, project_path: str) -> None:
        """Bind a TMUX pane instance to a project."""
        self._execute(
            """INSERT INTO instance_bindings (instance_id, project_id, project_path, bound_timestamp)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(instance_id) DO UPDATE SET
                   project_id = excluded.project_id,
                   project_path = excluded.project_path,
                   bound_timestamp = excluded.bound_timestamp
            """,
            (instance_id, project_id, str(project_path), time.time()),
        )
        self.commit()

    # --- global_sessions ---

    def register_session(
        self,
        session_id: str,
        ai_id: str,
        project_id: str,
        instance_id: str | None = None,
        parent_session_id: str | None = None,
    ) -> None:
        """Register a session in the global session registry."""
        now = time.time()
        self._execute(
            """INSERT INTO global_sessions
               (session_id, ai_id, origin_project_id, current_project_id,
                instance_id, status, parent_session_id, created_at, last_activity)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                   last_activity = excluded.last_activity,
                   current_project_id = excluded.current_project_id,
                   instance_id = excluded.instance_id
            """,
            (session_id, ai_id, project_id, project_id, instance_id, parent_session_id, now, now),
        )
        self.commit()

    # --- entity_artifacts ---

    def add_entity_artifact(
        self,
        artifact_id: str,
        artifact_type: str,
        artifact_source: str,
        entity_type: str,
        entity_id: str,
        relationship: str = "about",
        relevance: float = 1.0,
        discovered_via: str | None = None,
        engagement_id: str | None = None,
        transaction_id: str | None = None,
        created_by_ai: str | None = None,
    ) -> str | None:
        """Link an artifact to a CRM entity. Returns the link ID or None on conflict."""
        import uuid

        link_id = str(uuid.uuid4())
        try:
            self._execute(
                """INSERT INTO entity_artifacts
                   (id, artifact_type, artifact_id, artifact_source, entity_type, entity_id,
                    relationship, relevance, discovered_via, engagement_id, transaction_id,
                    created_at, created_by_ai)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link_id,
                    artifact_type,
                    artifact_id,
                    artifact_source,
                    entity_type,
                    entity_id,
                    relationship,
                    relevance,
                    discovered_via,
                    engagement_id,
                    transaction_id,
                    time.time(),
                    created_by_ai,
                ),
            )
            self.commit()
            return link_id
        except sqlite3.IntegrityError:
            return None

    def get_entity_artifacts_by_transaction(self, transaction_id: str) -> list[dict[str, Any]]:
        """Get all entity-artifact links for a given transaction."""
        cursor = self._execute("SELECT * FROM entity_artifacts WHERE transaction_id = ?", (transaction_id,))
        return [dict(row) for row in cursor.fetchall()]

    def get_entity_artifacts_by_entity(
        self,
        entity_type: str,
        entity_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get all artifact links for a specific entity."""
        cursor = self._execute(
            """SELECT * FROM entity_artifacts
               WHERE entity_type = ? AND entity_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (entity_type, entity_id, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_entity_artifacts_by_engagement(
        self,
        engagement_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get all artifact links for a specific engagement."""
        cursor = self._execute(
            """SELECT * FROM entity_artifacts
               WHERE engagement_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (engagement_id, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def count_entity_artifacts(self, entity_type: str, entity_id: str) -> int:
        """Count artifact links for an entity (list projection linked_artifact_count).

        Uses idx_entity_artifacts_entity.
        """
        cursor = self._execute(
            "SELECT COUNT(*) AS n FROM entity_artifacts WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        )
        row = cursor.fetchone()
        return int(row["n"]) if row else 0

    # --- entity_registry / entity_memberships (CLI surface backing) ---

    def list_entities(
        self,
        entity_type: str | None = None,
        status: str = "active",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List entities from the registry.

        Args:
            entity_type: Optional filter by entity_type (project, contact, ...).
                         None = all types.
            status: 'active' (default), 'inactive', 'archived', or 'all'.
            limit: Max rows to return.
        """
        params: list[Any] = []
        where: list[str] = []
        if entity_type:
            where.append("entity_type = ?")
            params.append(entity_type)
        if status != "all":
            where.append("status = ?")
            params.append(status)
        where_clause = ("WHERE " + " AND ".join(where)) if where else ""
        params.append(limit)
        cursor = self._execute(
            f"SELECT * FROM entity_registry {where_clause} ORDER BY updated_at DESC, created_at DESC LIMIT ?",
            tuple(params),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_entity(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        """Get a single entity by (type, id). Returns None if not found.

        Supports prefix-match on entity_id (8+ chars) when no exact match —
        same convention as subtask UUID resolution.
        """
        cursor = self._execute(
            "SELECT * FROM entity_registry WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        if len(entity_id) >= 4:
            cursor = self._execute(
                "SELECT * FROM entity_registry WHERE entity_type = ? AND entity_id LIKE ? "
                "ORDER BY created_at DESC LIMIT 2",
                (entity_type, f"{entity_id}%"),
            )
            rows = cursor.fetchall()
            if len(rows) == 1:
                return dict(rows[0])
        return None

    def upsert_entity(
        self,
        entity_type: str,
        entity_id: str,
        display_name: str,
        source_db: str,
        source_table: str,
        description: str | None = None,
        emoji_state: str | None = None,
        status: str = "active",
        metadata: str | None = None,
    ) -> None:
        """Insert or update an entity_registry row by (entity_type, entity_id).

        Used by sync paths that mirror authoritative data from external
        systems (e.g. cortex's mesh_sharing_agreements → entity_registry).
        Idempotent: calling twice with the same values is a no-op on the
        second call other than the updated_at timestamp.
        """
        now = time.time()
        self._execute(
            """
            INSERT INTO entity_registry
                (entity_type, entity_id, display_name, description, source_db,
                 source_table, emoji_state, status, created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                display_name = excluded.display_name,
                description = excluded.description,
                source_db = excluded.source_db,
                source_table = excluded.source_table,
                emoji_state = excluded.emoji_state,
                status = excluded.status,
                updated_at = excluded.updated_at,
                metadata = excluded.metadata
            """,
            (
                entity_type,
                entity_id,
                display_name,
                description,
                source_db,
                source_table,
                emoji_state,
                status,
                now,
                now,
                metadata,
            ),
        )
        self.commit()

    def mark_entity_status(
        self,
        entity_type: str,
        entity_id: str,
        status: str,
    ) -> bool:
        """Set the status field on an entity_registry row. Returns True if a
        row was updated, False if no matching row existed.

        Used for soft-state transitions like 'agreement no longer in cortex
        response → mark revoked locally' without rewriting the metadata.
        """
        cursor = self._execute(
            "UPDATE entity_registry SET status = ?, updated_at = ? WHERE entity_type = ? AND entity_id = ?",
            (status, time.time(), entity_type, entity_id),
        )
        self.commit()
        return cursor.rowcount > 0

    def search_entities(
        self,
        query: str,
        entity_type: str | None = None,
        status: str = "active",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Text-search entities by display_name + description.

        Uses LIKE %query% — case-insensitive. For semantic search across
        artifacts, use project-search / workspace-search instead.
        """
        like = f"%{query}%"
        params: list[Any] = [like, like]
        where = ["(display_name LIKE ? COLLATE NOCASE OR description LIKE ? COLLATE NOCASE)"]
        if entity_type:
            where.append("entity_type = ?")
            params.append(entity_type)
        if status != "all":
            where.append("status = ?")
            params.append(status)
        params.append(limit)
        cursor = self._execute(
            f"SELECT * FROM entity_registry WHERE {' AND '.join(where)} "
            f"ORDER BY updated_at DESC, created_at DESC LIMIT ?",
            tuple(params),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_entity_memberships(self, entity_type: str, entity_id: str) -> dict[str, list[dict[str, Any]]]:
        """Get incoming + outgoing membership edges for an entity.

        Returns:
            {"member_of": [...], "members": [...]}
            - member_of: groups this entity belongs to
            - members: entities that belong to this entity (when it's a group)

            Only active edges (left_at IS NULL) are returned.
        """
        out_cursor = self._execute(
            """SELECT * FROM entity_memberships
               WHERE entity_type = ? AND entity_id = ? AND left_at IS NULL
               ORDER BY joined_at DESC""",
            (entity_type, entity_id),
        )
        member_of = [dict(row) for row in out_cursor.fetchall()]
        in_cursor = self._execute(
            """SELECT * FROM entity_memberships
               WHERE group_type = ? AND group_id = ? AND left_at IS NULL
               ORDER BY joined_at DESC""",
            (entity_type, entity_id),
        )
        members = [dict(row) for row in in_cursor.fetchall()]
        return {"member_of": member_of, "members": members}

    def get_org_parent_map(self) -> dict[str, str]:
        """Map child_org_id → parent_org_id from active org→org membership edges.

        Org→org parentage is an active ``entity_membership`` where both ends are
        organizations (the child org is member_of the parent org). The ``role``
        column is a free-text verb in ``entity-link`` (existing edges use
        'member', 'context', 'ticket_of'), so parentage keys on the STRUCTURAL
        org→org edge, not a brittle role string — a role filter would miss real
        parentage. One row per child (the most recent active edge wins). Single
        query; the org set (umbrella + brands) is small, so this preserves the
        list endpoint's single-query intent (the org-parent slice of the
        deferred v1.1 membership enrichment).
        """
        cursor = self._execute(
            """SELECT entity_id, group_id FROM entity_memberships
               WHERE entity_type = 'organization' AND group_type = 'organization'
                 AND left_at IS NULL
               ORDER BY joined_at ASC"""
        )
        # ASC + dict overwrite → the most recent (latest joined_at) edge wins.
        return {row["entity_id"]: row["group_id"] for row in cursor.fetchall()}

    def upsert_entity_membership(
        self,
        entity_type: str,
        entity_id: str,
        group_type: str,
        group_id: str,
        role: str | None = None,
        notes: str | None = None,
    ) -> None:
        """Insert (or re-activate) a typed membership edge between two entities.

        The write peer to ``get_entity_memberships`` — mirrors
        ``upsert_entity``: idempotent on the membership PK
        (entity_type, entity_id, group_type, group_id). Re-writing the same
        edge updates ``role``/``notes`` and clears ``left_at`` (re-activating a
        soft-closed edge) rather than duplicating; the original ``joined_at`` /
        ``created_at`` are preserved on conflict. Used by the ERM graduation
        path, e.g. ``engagement`` member_of ``organization`` with
        ``role='ticket_of'``.

        Edges are never deleted — closing a membership is a soft-close via
        ``close_entity_membership`` (sets ``left_at``), so the history stays
        auditable.
        """
        now = time.time()
        self._execute(
            """
            INSERT INTO entity_memberships
                (entity_type, entity_id, group_type, group_id,
                 role, joined_at, left_at, created_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(entity_type, entity_id, group_type, group_id) DO UPDATE SET
                role = excluded.role,
                left_at = NULL,
                notes = excluded.notes
            """,
            (entity_type, entity_id, group_type, group_id, role, now, now, notes),
        )
        self.commit()

    def close_entity_membership(
        self,
        entity_type: str,
        entity_id: str,
        group_type: str,
        group_id: str,
    ) -> bool:
        """Soft-close an active membership edge by stamping ``left_at``.

        Returns True if an active edge was closed, False if no matching
        active edge existed. Never deletes the row — a closed edge stays in
        the table (excluded from ``get_entity_memberships``, which filters on
        ``left_at IS NULL``) so the relationship history remains auditable.
        Idempotent: closing an already-closed edge is a no-op returning False.
        """
        cursor = self._execute(
            """UPDATE entity_memberships SET left_at = ?
               WHERE entity_type = ? AND entity_id = ?
                 AND group_type = ? AND group_id = ? AND left_at IS NULL""",
            (time.time(), entity_type, entity_id, group_type, group_id),
        )
        self.commit()
        return cursor.rowcount > 0

    # --- engagement substrate (operational SQL CRUD) ------------------------
    # The engagement is the OPERATIONAL projection — a plain SQL row with no
    # confidence/epistemic fields. Diagnostic findings stay EPISTEMIC and link
    # in via entity_artifacts (artifact → goal → engagement); the two
    # projections must not collapse. Enums are enforced here (app-side), and
    # domain/stage are validated against the definition tables.

    def create_engagement(
        self,
        engagement_id: str,
        title: str,
        *,
        domain: str | None = None,
        stage: str | None = None,
        engagement_type: str = "outreach",
        description: str | None = None,
        contact_id: str | None = None,
        project_id: str | None = None,
        created_by_ai_id: str | None = None,
    ) -> dict[str, Any]:
        """Create an engagement sidecar row (lifecycle_state='open').

        Validates ``domain`` against domain_definitions and ``stage`` against
        stage_definitions (for that domain) when provided — raises ValueError on
        an unknown domain/stage. Returns the created row.
        """
        if domain is not None:
            self._require_domain(domain)
        if stage is not None:
            self._require_stage(stage, domain)
        now = time.time()
        self._execute(
            """
            INSERT INTO engagements
                (engagement_id, contact_id, project_id, title, description,
                 engagement_type, status, lifecycle_state, stage, domain,
                 started_at, created_at, created_by_ai_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', 'open', ?, ?, ?, ?, ?, ?)
            """,
            (
                engagement_id,
                contact_id,
                project_id,
                title,
                description,
                engagement_type,
                stage,
                domain,
                now,
                now,
                created_by_ai_id,
                now,
            ),
        )
        self.commit()
        created = self.get_engagement(engagement_id)
        if created is None:  # pragma: no cover — row was just inserted
            raise RuntimeError(f"engagement {engagement_id!r} not found immediately after insert")
        return created

    def get_engagement(self, engagement_id: str) -> dict[str, Any] | None:
        """Fetch a single engagement by id. Returns None if not found."""
        cursor = self._execute("SELECT * FROM engagements WHERE engagement_id = ?", (engagement_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_engagements(
        self,
        *,
        domain: str | None = None,
        lifecycle_state: str | None = None,
        org_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List engagements, optionally filtered.

        ``org_id`` scopes to engagements that are members of that organization
        with role='ticket_of' (the canonical org→ticket linkage), joining
        entity_memberships. ``lifecycle_state`` must be a valid state.
        """
        if lifecycle_state is not None and lifecycle_state not in ENGAGEMENT_LIFECYCLE_STATES:
            raise ValueError(
                f"invalid lifecycle_state '{lifecycle_state}' — must be one of {sorted(ENGAGEMENT_LIFECYCLE_STATES)}"
            )
        params: list[Any] = []
        where: list[str] = []
        join = ""
        if org_id is not None:
            join = " JOIN entity_memberships m ON m.entity_type = 'engagement' AND m.entity_id = e.engagement_id"
            where.append(
                "m.group_type = 'organization' AND m.group_id = ? AND m.role = 'ticket_of' AND m.left_at IS NULL"
            )
            params.append(org_id)
        if domain is not None:
            where.append("e.domain = ?")
            params.append(domain)
        if lifecycle_state is not None:
            where.append("e.lifecycle_state = ?")
            params.append(lifecycle_state)
        where_clause = ("WHERE " + " AND ".join(where)) if where else ""
        params.append(limit)
        cursor = self._execute(
            f"SELECT e.* FROM engagements e{join} {where_clause} ORDER BY e.updated_at DESC, e.created_at DESC LIMIT ?",
            tuple(params),
        )
        return [dict(row) for row in cursor.fetchall()]

    def update_engagement(
        self,
        engagement_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        stage: str | None = None,
        domain: str | None = None,
        lifecycle_state: str | None = None,
        outcome: str | None = None,
    ) -> dict[str, Any] | None:
        """Update mutable engagement fields. Enforces the lifecycle + outcome
        enums and validates domain/stage against the definition tables. Returns
        the updated row, or None if the engagement doesn't exist. Bumps
        updated_at. Passing no fields is a no-op read.
        """
        if lifecycle_state is not None and lifecycle_state not in ENGAGEMENT_LIFECYCLE_STATES:
            raise ValueError(
                f"invalid lifecycle_state '{lifecycle_state}' — must be one of {sorted(ENGAGEMENT_LIFECYCLE_STATES)}"
            )
        if outcome is not None and outcome not in ENGAGEMENT_OUTCOMES:
            raise ValueError(f"invalid outcome '{outcome}' — must be one of {sorted(ENGAGEMENT_OUTCOMES)}")
        if domain is not None:
            self._require_domain(domain)
        if stage is not None:
            self._require_stage(stage, domain)
        sets: list[str] = []
        params: list[Any] = []
        for col, val in (
            ("title", title),
            ("description", description),
            ("stage", stage),
            ("domain", domain),
            ("lifecycle_state", lifecycle_state),
            ("outcome", outcome),
        ):
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)
        if not sets:
            return self.get_engagement(engagement_id)
        sets.append("updated_at = ?")
        params.append(time.time())
        params.append(engagement_id)
        cursor = self._execute(f"UPDATE engagements SET {', '.join(sets)} WHERE engagement_id = ?", tuple(params))
        self.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_engagement(engagement_id)

    # --- engagement domain/stage definitions + practice membership ----------

    def list_domains(self) -> list[dict[str, Any]]:
        """List the engagement domain definitions."""
        cursor = self._execute("SELECT * FROM domain_definitions ORDER BY domain_id")
        return [dict(row) for row in cursor.fetchall()]

    def list_stages(self, domain: str | None = None) -> list[dict[str, Any]]:
        """List stage definitions, optionally for one domain, ordered by ordinal."""
        if domain is not None:
            cursor = self._execute("SELECT * FROM stage_definitions WHERE domain = ? ORDER BY ordinal", (domain,))
        else:
            cursor = self._execute("SELECT * FROM stage_definitions ORDER BY domain, ordinal")
        return [dict(row) for row in cursor.fetchall()]

    def join_practice_domain(self, practice_id: str, domain_id: str) -> None:
        """Register a practice as active in a domain (practice_domains).

        Idempotent: re-joining an already-active (practice, domain) is a no-op;
        re-joining one that was previously left clears left_at. Validates the
        domain exists.
        """
        self._require_domain(domain_id)
        now = time.time()
        self._execute(
            """
            INSERT INTO practice_domains (practice_id, domain_id, joined_at, left_at)
            VALUES (?, ?, ?, NULL)
            ON CONFLICT(practice_id, domain_id) DO UPDATE SET left_at = NULL
            """,
            (practice_id, domain_id, now),
        )
        self.commit()

    def get_practice_domains(self, practice_id: str) -> list[dict[str, Any]]:
        """List the domains a practice is currently active in (left_at IS NULL)."""
        cursor = self._execute(
            "SELECT * FROM practice_domains WHERE practice_id = ? AND left_at IS NULL ORDER BY domain_id",
            (practice_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def _require_domain(self, domain_id: str) -> None:
        cursor = self._execute("SELECT 1 FROM domain_definitions WHERE domain_id = ?", (domain_id,))
        if cursor.fetchone() is None:
            raise ValueError(f"unknown engagement domain '{domain_id}'")

    def _require_stage(self, stage_id: str, domain: str | None) -> None:
        cursor = self._execute("SELECT domain FROM stage_definitions WHERE stage_id = ?", (stage_id,))
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"unknown engagement stage '{stage_id}'")
        if domain is not None and row[0] != domain:
            raise ValueError(f"stage '{stage_id}' belongs to domain '{row[0]}', not '{domain}'")

    def walk_entity_graph(
        self,
        start_type: str,
        start_id: str,
        max_depth: int = 2,
    ) -> dict[str, Any]:
        """BFS the entity membership graph from a starting node.

        Walks edges in both directions (member_of + members) with cycle
        protection. Returns a tree-shaped result for human/JSON rendering.

        Args:
            start_type: Starting entity_type.
            start_id: Starting entity_id (full or unambiguous prefix).
            max_depth: How many edges to traverse before stopping. 0 = just
                       the starting node + its 1-hop edges in the response
                       (depth=0 returns the node alone, no traversal).

        Returns:
            {
                "root": {entity dict + "depth": 0},
                "nodes": [list of all visited entities with their depth],
                "edges": [list of membership rows traversed],
                "truncated": bool,  # True if max_depth limited the walk
            }
            Returns {"root": None} if the start entity doesn't exist.
        """
        start = self.get_entity(start_type, start_id)
        if not start:
            return {"root": None, "nodes": [], "edges": [], "truncated": False}
        resolved_id = start["entity_id"]
        seen: set[tuple[str, str]] = {(start_type, resolved_id)}
        nodes = [{**start, "depth": 0}]
        edges: list[dict[str, Any]] = []
        frontier: list[tuple[str, str, int]] = [(start_type, resolved_id, 0)]
        truncated = False
        while frontier:
            ntype, nid, depth = frontier.pop(0)
            if depth >= max_depth:
                if (
                    depth == max_depth
                    and self.get_entity_memberships(ntype, nid)["member_of"]
                    + self.get_entity_memberships(ntype, nid)["members"]
                ):
                    truncated = True
                continue
            memberships = self.get_entity_memberships(ntype, nid)
            for edge in memberships["member_of"]:
                edges.append({**edge, "direction": "outgoing"})
                neighbor = (edge["group_type"], edge["group_id"])
                if neighbor not in seen:
                    seen.add(neighbor)
                    n_ent = self.get_entity(*neighbor)
                    if n_ent:
                        nodes.append({**n_ent, "depth": depth + 1})
                        frontier.append((*neighbor, depth + 1))
            for edge in memberships["members"]:
                edges.append({**edge, "direction": "incoming"})
                neighbor = (edge["entity_type"], edge["entity_id"])
                if neighbor not in seen:
                    seen.add(neighbor)
                    n_ent = self.get_entity(*neighbor)
                    if n_ent:
                        nodes.append({**n_ent, "depth": depth + 1})
                        frontier.append((*neighbor, depth + 1))
        return {
            "root": {**start, "depth": 0},
            "nodes": nodes,
            "edges": edges,
            "truncated": truncated,
        }
