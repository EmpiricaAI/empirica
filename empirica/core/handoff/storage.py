#!/usr/bin/env python3
"""
Handoff Report Storage

Dual storage strategy:
1. Git Notes - Distributed, version-controlled, travels with repo
2. Database - Fast queries, relational integrity

Uses:
- Git notes for distributed storage (namespace: refs/notes/empirica/handoff/{session_id})
- SQLite for queryable history
"""

import json
import logging
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class GitHandoffStorage:
    """Store handoff reports in git notes"""

    def __init__(self, repo_path: str | None = None):
        """
        Initialize git storage

        Args:
            repo_path: Path to git repository (default: current directory)
        """
        self.repo_path = Path(repo_path) if repo_path else Path.cwd()

        # Verify git repo
        if not (self.repo_path / ".git").exists():
            logger.warning(f"Not a git repository: {self.repo_path}")

    def store_handoff(self, session_id: str, report: dict) -> str:
        """
        Store handoff report in git notes

        Args:
            session_id: Session UUID
            report: Full handoff report dict

        Returns:
            Note SHA or 'stored' on success
        """
        try:
            # Store compressed JSON as primary note
            note_ref = f"empirica/handoff/{session_id}"
            compressed = report["compressed_json"]

            result = subprocess.run(
                ["git", "notes", "--ref", note_ref, "add", "-f", "-F", "-", "HEAD"],
                input=compressed,
                capture_output=True,
                timeout=5,
                cwd=str(self.repo_path),
                text=True,
            )

            if result.returncode != 0:
                # If no commits yet, create an empty commit
                if "No commits yet" in result.stderr or "HEAD" in result.stderr:
                    logger.debug("Creating initial commit for git notes...")
                    subprocess.run(
                        ["git", "commit", "--allow-empty", "-m", "Initial commit for Empirica handoff reports"],
                        capture_output=True,
                        timeout=5,
                        cwd=str(self.repo_path),
                        text=True,
                    )
                    # Retry
                    result = subprocess.run(
                        ["git", "notes", "--ref", note_ref, "add", "-f", "-F", "-", "HEAD"],
                        input=compressed,
                        capture_output=True,
                        timeout=5,
                        cwd=str(self.repo_path),
                        text=True,
                    )

                if result.returncode != 0:
                    raise Exception(f"Git notes failed: {result.stderr}")

            # Store full markdown as separate note (for human reading)
            markdown_ref = f"empirica/handoff/{session_id}/markdown"
            markdown = report["markdown"]

            md = subprocess.run(
                ["git", "notes", "--ref", markdown_ref, "add", "-f", "-F", "-", "HEAD"],
                input=markdown,
                capture_output=True,
                timeout=5,
                cwd=str(self.repo_path),
                text=True,
            )
            # The JSON half above raises on failure; this half used to return
            # success regardless, so a handoff could report stored with its
            # human-readable copy silently absent. It is the larger payload of
            # the two, so it is the one more likely to be refused.
            if md.returncode != 0:
                logger.warning("handoff markdown note refused: %s", md.stderr.strip()[:200])

            logger.info(f"📝 Stored handoff in git notes: {note_ref}")

            return self._get_note_sha(note_ref) or "stored"

        except Exception as e:
            logger.error(f"Failed to store handoff in git: {e}")
            raise

    def load_handoff(self, session_id: str, format: str = "json") -> dict | None:
        """
        Load handoff report from git notes

        Args:
            session_id: Session UUID
            format: 'json' or 'markdown'

        Returns:
            Handoff report dict or None if not found
        """
        try:
            note_ref = f"empirica/handoff/{session_id}"
            if format == "markdown":
                note_ref += "/markdown"

            result = subprocess.run(
                ["git", "notes", "--ref", note_ref, "show", "HEAD"],
                capture_output=True,
                timeout=2,
                cwd=str(self.repo_path),
                text=True,
            )

            if result.returncode != 0:
                return None

            if format == "json":
                return json.loads(result.stdout)
            else:
                return {"markdown": result.stdout}

        except Exception as e:
            logger.debug(f"Failed to load handoff from git: {e}")
            return None

    def list_handoffs(self) -> list[str]:
        """
        List all handoff session IDs stored in git notes

        Uses git for-each-ref to scan refs/notes/empirica/handoff/* pattern.
        Each handoff is stored at refs/notes/empirica/handoff/{session_id}.

        Returns:
            List of session IDs
        """
        try:
            # Use for-each-ref to find all handoff note refs
            result = subprocess.run(
                ["git", "for-each-ref", "--format=%(refname)", "refs/notes/empirica/handoff/"],
                capture_output=True,
                timeout=5,
                cwd=str(self.repo_path),
                text=True,
            )

            if result.returncode != 0:
                return []

            # Parse session IDs from refs
            # Format: refs/notes/empirica/handoff/{session_id}
            # Or: refs/notes/empirica/handoff/{session_id}/markdown
            session_ids = set()
            for line in result.stdout.strip().splitlines():
                if not line:
                    continue
                # Extract session ID (UUID after handoff/)
                parts = line.split("refs/notes/empirica/handoff/")
                if len(parts) > 1:
                    # Take first part before any slash (handles /markdown suffix)
                    session_id = parts[1].split("/")[0]
                    # Validate it looks like a UUID (36 chars with dashes)
                    if len(session_id) == 36 and session_id.count("-") == 4:
                        session_ids.add(session_id)

            return sorted(session_ids)

        except Exception as e:
            logger.debug(f"Failed to list handoffs from git: {e}")
            return []

    def _get_note_sha(self, note_ref: str) -> str | None:
        """Get SHA of note"""
        try:
            result = subprocess.run(
                ["git", "rev-parse", f"refs/notes/{note_ref}"],
                capture_output=True,
                timeout=2,
                cwd=str(self.repo_path),
                text=True,
            )

            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:  # noqa: S110 — git subprocess may fail; None fallback is correct
            pass

        return None


class DatabaseHandoffStorage:
    """Store handoff reports in session database"""

    def __init__(self, db_path: str | None = None):
        """
        Initialize database storage

        Args:
            db_path: Path to session database (default: uses canonical path resolver)
        """
        if db_path is None:
            from empirica.config.path_resolver import get_session_db_path

            db_path = get_session_db_path()

        self.db_path = Path(db_path)
        # Enable timeout and WAL mode for better concurrency
        self.conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.row_factory = sqlite3.Row

        self._create_table()
        logger.info(f"📊 Database handoff storage initialized: {self.db_path} (WAL mode enabled)")

    def _create_table(self):
        """Create handoff_reports table"""
        cursor = self.conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS handoff_reports (
                session_id TEXT PRIMARY KEY,
                ai_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                task_summary TEXT,
                duration_seconds REAL,
                epistemic_deltas TEXT,
                key_findings TEXT,
                knowledge_gaps_filled TEXT,
                remaining_unknowns TEXT,
                noetic_tools TEXT,
                next_session_context TEXT,
                recommended_next_steps TEXT,
                artifacts_created TEXT,
                calibration_status TEXT,
                overall_confidence_delta REAL,
                compressed_json TEXT,
                markdown_report TEXT,
                created_at REAL NOT NULL
            )
        """)

        # Indexes for common queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_handoff_ai
            ON handoff_reports(ai_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_handoff_timestamp
            ON handoff_reports(timestamp)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_handoff_created
            ON handoff_reports(created_at)
        """)

        self.conn.commit()

    def store_handoff(self, session_id: str, report: dict):
        """
        Store handoff report in database

        Args:
            session_id: Session UUID
            report: Full handoff report dict
        """
        cursor = self.conn.cursor()

        try:
            cursor.execute(
                """
                INSERT OR REPLACE INTO handoff_reports
                (session_id, ai_id, timestamp, task_summary, duration_seconds,
                 epistemic_deltas, key_findings, knowledge_gaps_filled,
                 remaining_unknowns, noetic_tools, next_session_context,
                 recommended_next_steps, artifacts_created, calibration_status,
                 overall_confidence_delta, compressed_json, markdown_report, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    session_id,
                    report["ai_id"],
                    report["timestamp"],
                    report["task_summary"],
                    report["duration_seconds"],
                    json.dumps(report["epistemic_deltas"]),
                    json.dumps(report["key_findings"]),
                    json.dumps(report["knowledge_gaps_filled"]),
                    json.dumps(report["remaining_unknowns"]),
                    json.dumps(report["noetic_tools"]),
                    report["next_session_context"],
                    json.dumps(report["recommended_next_steps"]),
                    json.dumps(report["artifacts_created"]),
                    report["calibration_status"],
                    report["overall_confidence_delta"],
                    report["compressed_json"],
                    report["markdown"],
                    datetime.now().timestamp(),
                ),
            )

            self.conn.commit()
            logger.info(f"💾 Stored handoff in database: {session_id[:8]}...")

        except Exception as e:
            logger.error(f"Failed to store handoff in database: {e}")
            raise

    def load_handoff(self, session_id: str) -> dict | None:
        """
        Load handoff report from database

        Args:
            session_id: Session UUID

        Returns:
            Handoff report dict or None if not found
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM handoff_reports WHERE session_id = ?
        """,
            (session_id,),
        )

        row = cursor.fetchone()
        if not row:
            return None

        return self._row_to_dict(row)

    def query_handoffs(self, ai_id: str | None = None, since: str | None = None, limit: int = 10) -> list[dict]:
        """
        Query handoff reports by AI or date

        Args:
            ai_id: Filter by AI agent
            since: ISO timestamp filter (e.g., '2025-11-01')
            limit: Max results (default: 10)

        Returns:
            List of handoff report dicts
        """
        query = "SELECT * FROM handoff_reports WHERE 1=1"
        params = []

        if ai_id:
            query += " AND ai_id = ?"
            params.append(ai_id)

        if since:
            query += " AND timestamp >= ?"
            params.append(since)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = self.conn.cursor()
        cursor.execute(query, params)

        return [self._row_to_dict(row) for row in cursor.fetchall()]

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        """Convert database row to dict"""
        return {
            "session_id": row["session_id"],
            "ai_id": row["ai_id"],
            "timestamp": row["timestamp"],
            "task_summary": row["task_summary"],
            "duration_seconds": row["duration_seconds"],
            "epistemic_deltas": json.loads(row["epistemic_deltas"]) if row["epistemic_deltas"] else {},
            "key_findings": json.loads(row["key_findings"]) if row["key_findings"] else [],
            "knowledge_gaps_filled": json.loads(row["knowledge_gaps_filled"]) if row["knowledge_gaps_filled"] else [],
            "remaining_unknowns": json.loads(row["remaining_unknowns"]) if row["remaining_unknowns"] else [],
            "noetic_tools": json.loads(row["noetic_tools"]) if row["noetic_tools"] else [],
            "next_session_context": row["next_session_context"],
            "recommended_next_steps": json.loads(row["recommended_next_steps"])
            if row["recommended_next_steps"]
            else [],
            "artifacts_created": json.loads(row["artifacts_created"]) if row["artifacts_created"] else [],
            "calibration_status": row["calibration_status"],
            "overall_confidence_delta": row["overall_confidence_delta"],
            "compressed_json": row["compressed_json"],
            "markdown": row["markdown_report"],
            "created_at": row["created_at"],
        }

    def list_handoffs(self) -> list[str]:
        """
        List all handoff session IDs

        Returns:
            List of session IDs
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT session_id FROM handoff_reports ORDER BY created_at DESC")

        return [row[0] for row in cursor.fetchall()]


class HybridHandoffStorage:
    """
    Dual storage for handoff reports: Git notes + Database

    Strategy:
    - Git notes: Distributed, repo-portable, survives repo clones
    - Database: Fast queries, AI ID indexing, relational integrity

    Both stores are kept in sync. Reads prefer database (faster).
    """

    def __init__(self, repo_path: str | None = None, db_path: str | None = None):
        """
        Initialize hybrid storage with both backends

        Args:
            repo_path: Path to git repository (default: current directory)
            db_path: Path to session database (default: .empirica/sessions/sessions.db)
        """
        self.git_storage = GitHandoffStorage(repo_path)
        self.db_storage = DatabaseHandoffStorage(db_path)

        logger.info("🔄 Hybrid handoff storage initialized (git + database)")

    def store_handoff(self, session_id: str, report: dict) -> dict[str, bool]:
        """
        Store handoff in BOTH git notes and database

        Args:
            session_id: Session UUID
            report: Full handoff report dict

        Returns:
            {
                'git_stored': bool,
                'db_stored': bool,
                'fully_synced': bool
            }
        """
        result = {"git_stored": False, "db_stored": False, "fully_synced": False}

        # Store in git notes
        try:
            self.git_storage.store_handoff(session_id, report)
            result["git_stored"] = True
            logger.info(f"✅ Git notes storage: {session_id[:8]}...")
        except Exception as e:
            logger.error(f"❌ Git notes storage failed: {e}")

        # Store in database
        try:
            self.db_storage.store_handoff(session_id, report)
            result["db_stored"] = True
            logger.info(f"✅ Database storage: {session_id[:8]}...")
        except Exception as e:
            logger.error(f"❌ Database storage failed: {e}")

        # Check sync status
        result["fully_synced"] = result["git_stored"] and result["db_stored"]

        if not result["fully_synced"]:
            logger.warning(
                f"⚠️ Partial storage for {session_id[:8]}... (git={result['git_stored']}, db={result['db_stored']})"
            )

        return result

    def load_handoff(self, session_id: str, format: str = "json", prefer: str = "database") -> dict | None:
        """
        Load handoff from preferred storage, fallback to alternative

        Args:
            session_id: Session UUID
            format: 'json' or 'markdown'
            prefer: 'database' or 'git' (default: database for speed)

        Returns:
            Handoff report dict or None if not found
        """
        if prefer == "database":
            # Try database first (faster)
            handoff = self.db_storage.load_handoff(session_id)
            if handoff:
                logger.debug(f"📊 Loaded from database: {session_id[:8]}...")
                return handoff

            # Fallback to git notes
            handoff = self.git_storage.load_handoff(session_id, format)
            if handoff:
                logger.debug(f"📝 Loaded from git notes: {session_id[:8]}...")
            return handoff

        else:  # prefer == 'git'
            # Try git notes first
            handoff = self.git_storage.load_handoff(session_id, format)
            if handoff:
                logger.debug(f"📝 Loaded from git notes: {session_id[:8]}...")
                return handoff

            # Fallback to database
            handoff = self.db_storage.load_handoff(session_id)
            if handoff:
                logger.debug(f"📊 Loaded from database: {session_id[:8]}...")
            return handoff

    def query_handoffs(
        self, ai_id: str | None = None, since: str | None = None, limit: int = 5, include_git: bool = True
    ) -> list[dict]:
        """
        Query handoffs with filters (merges database + git notes)

        Args:
            ai_id: Filter by AI ID
            since: Filter by timestamp (ISO format)
            limit: Max results to return
            include_git: Also scan git notes for handoffs not in database

        Returns:
            List of handoff report dicts
        """
        # Get database results (indexed, fast)
        db_results = self.db_storage.query_handoffs(ai_id, since, limit * 2)  # Over-fetch for merge
        db_session_ids = {h["session_id"] for h in db_results}

        # Merge git notes not in database (for cross-repo portability)
        if include_git:
            git_session_ids = self.git_storage.list_handoffs()
            for session_id in git_session_ids:
                if session_id not in db_session_ids:
                    handoff = self.git_storage.load_handoff(session_id)
                    if handoff:
                        # Apply filters
                        if ai_id and handoff.get("ai") != ai_id and handoff.get("ai_id") != ai_id:
                            continue
                        if since and handoff.get("ts", "") < since and handoff.get("timestamp", "") < since:
                            continue
                        db_results.append(handoff)
                        logger.debug(f"📝 Merged from git notes: {session_id[:8]}...")

        # Sort by timestamp descending and apply limit
        db_results.sort(key=lambda h: h.get("timestamp") or h.get("ts") or "", reverse=True)
        return db_results[:limit]

    def list_handoffs(self, source: str = "database") -> list[str]:
        """
        List all handoff session IDs

        Args:
            source: 'database' or 'git' or 'both'

        Returns:
            List of session IDs
        """
        if source == "database":
            return self.db_storage.list_handoffs()
        elif source == "git":
            return self.git_storage.list_handoffs()
        else:  # both
            db_ids = set(self.db_storage.list_handoffs())
            git_ids = set(self.git_storage.list_handoffs())
            return sorted(db_ids | git_ids)

    def check_sync_status(self, session_id: str) -> dict[str, bool]:
        """
        Check if handoff exists in both stores

        Returns:
            {
                'in_git': bool,
                'in_database': bool,
                'synced': bool
            }
        """
        git_handoff = self.git_storage.load_handoff(session_id)
        db_handoff = self.db_storage.load_handoff(session_id)

        return {
            "in_git": git_handoff is not None,
            "in_database": db_handoff is not None,
            "synced": git_handoff is not None and db_handoff is not None,
        }
