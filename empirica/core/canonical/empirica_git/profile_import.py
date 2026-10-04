"""
Git Notes Profile Import - Rebuild SQLite from Git Notes

Reads all epistemic artifacts from git notes and imports them into SQLite.
This is the reverse of the dual-write pattern — given git notes as the
canonical portable format, reconstruct the working database.

Used by:
- `empirica profile-sync` — after fetching notes from remote
- `empirica profile-sync --import-only` — import from local notes without fetch
- Database recovery — rebuild after corruption or fresh machine setup

Design:
- INSERT OR IGNORE — deduplicates by primary key (artifact UUID)
- Preserves all metadata available in notes
- Handles schema gaps gracefully (transaction_id not in notes → NULL)
- Returns import statistics for verification
"""

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ProfileImporter:
    """Import epistemic artifacts from git notes into SQLite."""

    def __init__(self, workspace_root: str | None = None):
        self.workspace_root = workspace_root or str(Path.cwd())
        self._stats: dict[str, dict[str, int]] = {}

    def _git_cmd(self, args: list[str]) -> subprocess.CompletedProcess:
        """Run a git command in the workspace."""
        return subprocess.run(["git"] + args, cwd=self.workspace_root, capture_output=True, text=True, timeout=30)

    def _discover_refs(self, prefix: str) -> list[str]:
        """List all git notes refs under a prefix.

        Args:
            prefix: e.g. 'refs/notes/empirica/findings/'

        Returns:
            List of artifact IDs extracted from ref paths.
        """
        result = self._git_cmd(["for-each-ref", prefix])
        if result.returncode != 0:
            return []

        ids = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            ref = parts[1]
            artifact_id = ref.split("/")[-1]
            if artifact_id:
                ids.append(artifact_id)
        return ids

    def _load_note(self, ref: str) -> dict[str, Any] | None:
        """Load JSON payload from a git note ref."""
        # List which commit has the note
        result = self._git_cmd(["notes", f"--ref={ref}", "list"])
        if result.returncode != 0 or not result.stdout.strip():
            return None

        parts = result.stdout.strip().split()
        if len(parts) < 2:
            return None
        commit_hash = parts[1]

        # Load the note content
        result = self._git_cmd(["notes", f"--ref={ref}", "show", commit_hash])
        if result.returncode != 0:
            return None

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in note ref {ref}")
            return None

    def _parse_timestamp(self, iso_str: str | None) -> float | None:
        """Convert ISO timestamp string to epoch float."""
        if not iso_str:
            return None
        try:
            dt = datetime.fromisoformat(iso_str)
            return dt.timestamp()
        except (ValueError, TypeError):
            return None

    def import_findings(self, db) -> int:
        """Import findings from git notes into SQLite.

        Returns:
            Number of findings imported (new, not duplicates).
        """
        ids = self._discover_refs("refs/notes/empirica/findings/")
        imported = 0
        skipped = 0

        cursor = db.conn.cursor()
        for finding_id in ids:
            data = self._load_note(f"empirica/findings/{finding_id}")
            if not data:
                continue

            ts = self._parse_timestamp(data.get("created_at"))
            if not ts:
                ts = datetime.now(timezone.utc).timestamp()

            finding_data_json = json.dumps(
                data.get("finding_data", {"finding": data.get("finding"), "impact": data.get("impact")})
            )

            try:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO project_findings
                    (id, project_id, session_id, goal_id, subtask_id,
                     finding, created_timestamp, finding_data, subject, impact, transaction_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        data.get("finding_id", finding_id),
                        data.get("project_id"),
                        data.get("session_id"),
                        data.get("goal_id"),
                        data.get("subtask_id"),
                        data.get("finding", ""),
                        ts,
                        finding_data_json,
                        data.get("subject"),
                        data.get("impact", 0.5),
                        data.get("transaction_id"),  # May be None — notes don't always have this
                    ),
                )
                if cursor.rowcount > 0:
                    imported += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.warning(f"Failed to import finding {finding_id[:8]}: {e}")

        db.conn.commit()
        self._stats["findings"] = {"imported": imported, "skipped": skipped, "total": len(ids)}
        return imported

    def import_unknowns(self, db) -> int:
        """Import unknowns from git notes into SQLite."""
        ids = self._discover_refs("refs/notes/empirica/unknowns/")
        imported = 0
        skipped = 0

        cursor = db.conn.cursor()
        for unknown_id in ids:
            data = self._load_note(f"empirica/unknowns/{unknown_id}")
            if not data:
                continue

            ts = self._parse_timestamp(data.get("created_at"))
            if not ts:
                ts = datetime.now(timezone.utc).timestamp()

            resolved_ts = self._parse_timestamp(data.get("resolved_at"))
            unknown_data_json = json.dumps({"unknown": data.get("unknown", "")})

            try:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO project_unknowns
                    (id, project_id, session_id, goal_id, subtask_id,
                     unknown, is_resolved, resolved_by, created_timestamp,
                     resolved_timestamp, unknown_data, subject, impact, transaction_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        data.get("unknown_id", unknown_id),
                        data.get("project_id"),
                        data.get("session_id"),
                        data.get("goal_id"),
                        data.get("subtask_id"),
                        data.get("unknown", ""),
                        data.get("resolved", False),
                        data.get("resolved_by"),
                        ts,
                        resolved_ts,
                        unknown_data_json,
                        data.get("subject"),
                        data.get("impact", 0.5),
                        data.get("transaction_id"),
                    ),
                )
                if cursor.rowcount > 0:
                    imported += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.warning(f"Failed to import unknown {unknown_id[:8]}: {e}")

        db.conn.commit()
        self._stats["unknowns"] = {"imported": imported, "skipped": skipped, "total": len(ids)}
        return imported

    def import_dead_ends(self, db) -> int:
        """Import dead ends from git notes into SQLite."""
        ids = self._discover_refs("refs/notes/empirica/dead_ends/")
        imported = 0
        skipped = 0

        cursor = db.conn.cursor()
        for dead_end_id in ids:
            data = self._load_note(f"empirica/dead_ends/{dead_end_id}")
            if not data:
                continue

            ts = self._parse_timestamp(data.get("created_at"))
            if not ts:
                ts = datetime.now(timezone.utc).timestamp()

            dead_end_data_json = json.dumps(
                {"approach": data.get("approach", ""), "why_failed": data.get("why_failed", "")}
            )

            try:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO project_dead_ends
                    (id, project_id, session_id, goal_id, subtask_id,
                     approach, why_failed, created_timestamp, dead_end_data,
                     subject, transaction_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        data.get("dead_end_id", dead_end_id),
                        data.get("project_id"),
                        data.get("session_id"),
                        data.get("goal_id"),
                        data.get("subtask_id"),
                        data.get("approach", ""),
                        data.get("why_failed", ""),
                        ts,
                        dead_end_data_json,
                        data.get("subject"),
                        data.get("transaction_id"),
                    ),
                )
                if cursor.rowcount > 0:
                    imported += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.warning(f"Failed to import dead end {dead_end_id[:8]}: {e}")

        db.conn.commit()
        self._stats["dead_ends"] = {"imported": imported, "skipped": skipped, "total": len(ids)}
        return imported

    def import_mistakes(self, db) -> int:
        """Import mistakes from git notes into SQLite."""
        ids = self._discover_refs("refs/notes/empirica/mistakes/")
        imported = 0
        skipped = 0

        cursor = db.conn.cursor()
        for mistake_id in ids:
            data = self._load_note(f"empirica/mistakes/{mistake_id}")
            if not data:
                continue

            ts = self._parse_timestamp(data.get("created_at"))
            if not ts:
                ts = datetime.now(timezone.utc).timestamp()

            mistake_data_json = json.dumps(
                {
                    "mistake": data.get("mistake", ""),
                    "why_wrong": data.get("why_wrong", ""),
                    "prevention": data.get("prevention"),
                }
            )

            try:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO mistakes_made
                    (id, session_id, goal_id, project_id,
                     mistake, why_wrong, cost_estimate, root_cause_vector,
                     prevention, created_timestamp, mistake_data, transaction_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        data.get("mistake_id", mistake_id),
                        data.get("session_id"),
                        data.get("goal_id"),
                        data.get("project_id"),
                        data.get("mistake", ""),
                        data.get("why_wrong", ""),
                        data.get("cost_estimate"),
                        data.get("root_cause_vector"),
                        data.get("prevention"),
                        ts,
                        mistake_data_json,
                        data.get("transaction_id"),
                    ),
                )
                if cursor.rowcount > 0:
                    imported += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.warning(f"Failed to import mistake {mistake_id[:8]}: {e}")

        db.conn.commit()
        self._stats["mistakes"] = {"imported": imported, "skipped": skipped, "total": len(ids)}
        return imported

    @staticmethod
    def _text(value: Any) -> str | None:
        """A column value from a note field: strings as-is, structures as JSON, absent as NULL."""
        if value is None:
            return None
        return value if isinstance(value, str) else json.dumps(value)

    def import_decisions(self, db) -> int:
        """Import decisions from git notes into SQLite (the notes carry no transaction id).

        `skipped` means the id is already in SQLite. A note the table's constraints refuse is `failed`, not skipped:
        INSERT OR IGNORE reported both as "already there", so a constraint violation looked like a clean re-import.
        Imported rows are `local`: the notes carry no visibility and the table default is `shared`, which would publish
        a decision its author kept local.
        """
        ids = self._discover_refs("refs/notes/empirica/decisions/")
        imported = 0
        skipped = 0
        failed = 0

        cursor = db.conn.cursor()
        for decision_id in ids:
            data = self._load_note(f"empirica/decisions/{decision_id}")
            if not data:
                continue

            ts = self._parse_timestamp(data.get("created_at"))
            if not ts:
                ts = datetime.now(timezone.utc).timestamp()

            row_id = data.get("decision_id", decision_id)
            if cursor.execute("SELECT 1 FROM decisions WHERE id = ?", (row_id,)).fetchone():
                skipped += 1
                continue
            try:
                cursor.execute(
                    """
                    INSERT INTO decisions
                    (id, choice, alternatives, rationale, confidence_at_decision, reversibility,
                     project_id, session_id, transaction_id, goal_id, created_by_ai, created_timestamp, visibility)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'local')
                """,
                    (
                        row_id,
                        data.get("choice", ""),
                        self._text(data.get("alternatives")),
                        data.get("rationale", ""),
                        data.get("confidence_at_decision"),
                        data.get("reversibility"),
                        data.get("project_id"),
                        data.get("session_id"),
                        data.get("transaction_id"),
                        data.get("goal_id"),
                        data.get("ai_id"),
                        ts,
                    ),
                )
                imported += 1
            except Exception as e:
                failed += 1
                logger.warning(f"Failed to import decision {str(decision_id)[:8]}: {e}")

        db.conn.commit()
        self._stats["decisions"] = {"imported": imported, "skipped": skipped, "total": len(ids), "failed": failed}
        return imported

    def import_assumptions(self, db) -> int:
        """Import assumptions from git notes into SQLite (the notes carry no transaction id).

        Same accounting and visibility rule as import_decisions: `skipped` is an id already present, a refused note is
        `failed`, and imported rows are `local`. An invalid status or confidence is normalised, not refused.
        """
        ids = self._discover_refs("refs/notes/empirica/assumptions/")
        imported = 0
        skipped = 0
        failed = 0

        cursor = db.conn.cursor()
        for assumption_id in ids:
            data = self._load_note(f"empirica/assumptions/{assumption_id}")
            if not data:
                continue

            ts = self._parse_timestamp(data.get("created_at"))
            if not ts:
                ts = datetime.now(timezone.utc).timestamp()

            row_id = data.get("assumption_id", assumption_id)
            if cursor.execute("SELECT 1 FROM assumptions WHERE id = ?", (row_id,)).fetchone():
                skipped += 1
                continue
            conf, status = data.get("confidence"), data.get("status")
            try:
                cursor.execute(
                    """
                    INSERT INTO assumptions
                    (id, assumption, confidence, status, project_id, session_id, transaction_id,
                     goal_id, created_by_ai, created_timestamp, visibility)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'local')
                """,
                    (
                        row_id,
                        data.get("assumption", ""),
                        conf
                        if isinstance(conf, (int, float)) and not isinstance(conf, bool) and 0.0 <= conf <= 1.0
                        else None,
                        status if status in ("unverified", "verified", "falsified") else "unverified",
                        data.get("project_id"),
                        data.get("session_id"),
                        data.get("transaction_id"),
                        data.get("goal_id"),
                        data.get("ai_id"),
                        ts,
                    ),
                )
                imported += 1
            except Exception as e:
                failed += 1
                logger.warning(f"Failed to import assumption {str(assumption_id)[:8]}: {e}")

        db.conn.commit()
        self._stats["assumptions"] = {"imported": imported, "skipped": skipped, "total": len(ids), "failed": failed}
        return imported

    def import_goals(self, db) -> int:
        """Import goals from git notes into SQLite."""
        ids = self._discover_refs("refs/notes/empirica/goals/")
        imported = 0
        skipped = 0

        cursor = db.conn.cursor()
        for goal_id in ids:
            data = self._load_note(f"empirica/goals/{goal_id}")
            if not data:
                continue

            goal_data = data.get("goal_data", {})
            ts = self._parse_timestamp(data.get("created_at"))
            if not ts:
                ts = datetime.now(timezone.utc).timestamp()

            try:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO goals
                    (id, session_id, objective, scope, is_completed, status,
                     created_timestamp, project_id, transaction_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        data.get("goal_id", goal_id),
                        data.get("session_id"),
                        goal_data.get("objective", ""),
                        json.dumps(goal_data.get("scope", {})),
                        goal_data.get("is_completed", False),
                        goal_data.get("status", "in_progress"),
                        ts,
                        goal_data.get("project_id"),
                        goal_data.get("transaction_id"),
                    ),
                )
                if cursor.rowcount > 0:
                    imported += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.warning(f"Failed to import goal {goal_id[:8]}: {e}")

        db.conn.commit()
        self._stats["goals"] = {"imported": imported, "skipped": skipped, "total": len(ids)}
        return imported

    def import_all(self, db) -> dict[str, dict[str, int]]:
        """Import all artifact types from git notes.

        Args:
            db: SessionDatabase instance (must have open connection)

        Returns:
            Dict of stats per artifact type:
            {
                'findings': {'imported': N, 'skipped': N, 'total': N},
                'unknowns': {...},
                ...
            }
        """
        self._stats = {}

        self.import_findings(db)
        self.import_unknowns(db)
        self.import_dead_ends(db)
        self.import_mistakes(db)
        self.import_decisions(db)
        self.import_assumptions(db)
        self.import_goals(db)

        total_imported = sum(s["imported"] for s in self._stats.values())
        total_skipped = sum(s["skipped"] for s in self._stats.values())
        total_notes = sum(s["total"] for s in self._stats.values())

        self._stats["_summary"] = {
            "imported": total_imported,
            "skipped": total_skipped,
            "total": total_notes,
        }

        return self._stats

    @property
    def stats(self) -> dict[str, dict[str, int]]:
        """Get import statistics from last run."""
        return self._stats
