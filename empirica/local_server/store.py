"""empirica-local's proposal store: SQLite, one person, every proposal lands accepted.

Spec phase 1.2 (goal 92fe2c88). The local edition has no ECO: the person running it is the only reviewer, so a
proposal is ``accepted`` on arrival. Everything else follows the contract Cortex serves:

- **Idempotency is scoped to the sender** (Cortex 849e4ef1). A key reused with the same parent|title|summary returns
  the original proposal as a replay; a key reused with a different body raises :class:`IdempotencyConflict`.
- **Archive is per recipient** (``archived_by``), so one practice dismissing its copy leaves the others' inbox alone.
- **Completion** of a single-target proposal sets the completion fields; a multi-target one goes through core's
  ``target_acks.ack_or_complete`` and turns terminal only when every target has acked. One local user owns every
  practice, which is what the tenant shims below say.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from empirica.core.orchestration.audit import AuditEntry, append_audit
from empirica.core.orchestration.schemas import RESULT_TO_STATUS, Proposal, ProposalStatus
from empirica.core.orchestration.target_acks import ack_or_complete

LOCAL_USER_ID = "local"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    idem_key TEXT,
    idem_fingerprint TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS proposals_idem ON proposals(source, idem_key) WHERE idem_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS targets (
    proposal_id TEXT NOT NULL,
    ai_id TEXT NOT NULL,
    PRIMARY KEY (proposal_id, ai_id)
);
"""


class IdempotencyConflict(Exception):
    """A key this sender already used, sent again with a different body (the contract's 409)."""

    def __init__(self, original_id: str):
        super().__init__(f"idempotency key already used for {original_id} with a different body")
        self.original_id = original_id


def default_db_path() -> Path:
    override = os.environ.get("EMPIRICA_LOCAL_DB")
    return Path(override) if override else Path.home() / ".empirica" / "local_server.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fingerprint(p: Proposal) -> str:
    return hashlib.sha256(f"{p.parent_id or ''}|{p.title}|{p.summary}".encode()).hexdigest()


class CompletionRefused(Exception):
    """core's ack logic refused the call (not a target, or ambiguous); ``status_code`` is the HTTP answer."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class _LocalTenantDb:
    """``find_users_for_ai_id`` for core's ack logic: on a local server one user owns every practice."""

    def find_users_for_ai_id(self, ai_id):
        return [SimpleNamespace(id=LOCAL_USER_ID)]


class ProposalStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    # ── writes ───────────────────────────────────────────────────────────────────────────────────────────────

    def propose(self, proposal: Proposal, *, idempotency_key: str | None = None) -> tuple[Proposal, bool]:
        """Store ``proposal`` accepted. Returns ``(stored, replayed)``; raises :class:`IdempotencyConflict`."""
        fp = _fingerprint(proposal)
        with self._lock, self._connect() as db:
            if idempotency_key:
                row = db.execute(
                    "SELECT id, idem_fingerprint FROM proposals WHERE source = ? AND idem_key = ?",
                    (proposal.source_claude, idempotency_key),
                ).fetchone()
                if row:
                    if row[1] != fp:
                        raise IdempotencyConflict(row[0])
                    original = self._get(db, row[0])
                    if original is not None:
                        return original, True
            proposal.status = ProposalStatus.ACCEPTED.value
            proposal.org_id = proposal.org_id or "local"
            proposal.updated_at = _now()
            proposal.audit_log = append_audit(
                proposal.audit_log, AuditEntry(action="accepted", actor="local", note="local edition: no review gate")
            )
            db.execute(
                "INSERT INTO proposals (id, source, payload, created_at, idem_key, idem_fingerprint) VALUES (?,?,?,?,?,?)",
                (
                    proposal.id,
                    proposal.source_claude,
                    json.dumps(proposal.to_payload()),
                    proposal.created_at,
                    idempotency_key or None,
                    fp,
                ),
            )
            db.executemany(
                "INSERT OR IGNORE INTO targets (proposal_id, ai_id) VALUES (?, ?)",
                [(proposal.id, t) for t in proposal.distinct_targets()],
            )
        return proposal, False

    def update(self, proposal: Proposal) -> None:
        proposal.updated_at = _now()
        with self._connect() as db:
            db.execute(
                "UPDATE proposals SET payload = ? WHERE id = ?", (json.dumps(proposal.to_payload()), proposal.id)
            )

    def archive(self, proposal_id: str, ai_id: str) -> tuple[bool, bool]:
        """Archive ``ai_id``'s copy. Returns ``(found, already_archived)``."""
        with self._lock:
            p = self.get(proposal_id)
            if p is None:
                return False, False
            if ai_id in p.archived_by:
                return True, True
            p.archived_by = [*p.archived_by, ai_id]
            self.update(p)
            return True, False

    def complete(
        self,
        proposal_id: str,
        ai_id: str,
        *,
        result: str = "shipped",
        note: str | None = None,
        commit_sha: str | None = None,
        artifact_ids: list[str] | None = None,
    ) -> Proposal | None:
        """Record ``ai_id``'s completion. Returns the updated proposal, or None when the id is unknown."""
        if result not in RESULT_TO_STATUS:
            raise ValueError(f"result must be one of {sorted(RESULT_TO_STATUS)}")
        with self._lock:
            p = self.get(proposal_id)
            if p is None:
                return None
            arts = list(artifact_ids or [])
            outcome = ack_or_complete(
                p,
                self,
                SimpleNamespace(user_id=LOCAL_USER_ID),
                _LocalTenantDb(),
                claimed_ai_id=ai_id,
                result=result,
                note=note,
                commit_sha=commit_sha,
                artifact_ids=arts,
                actor=ai_id,
            )
            if outcome is not None and outcome.error:
                raise CompletionRefused(outcome.error, outcome.status_code)
            if outcome is not None and not outcome.terminal:
                return self.get(proposal_id)
            if outcome is not None:
                result, note, commit_sha, arts = outcome.result, outcome.note, outcome.commit_sha, outcome.artifact_ids
            status, action = RESULT_TO_STATUS[result]
            p.status = status
            p.completed_at = _now()
            p.completion_result, p.completion_note = result, note
            p.completion_commit_sha, p.completion_artifact_ids = commit_sha, arts
            p.audit_log = append_audit(p.audit_log, AuditEntry(action=action, actor=ai_id, note=note))
            self.update(p)
            return p

    # ── reads ────────────────────────────────────────────────────────────────────────────────────────────────

    def _get(self, db: sqlite3.Connection, proposal_id: str) -> Proposal | None:
        row = db.execute("SELECT payload FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        return Proposal.from_payload(json.loads(row[0])) if row else None

    def get(self, proposal_id: str) -> Proposal | None:
        with self._connect() as db:
            return self._get(db, proposal_id)

    def _list(self, sql: str, args: tuple, ai_id: str, include_archived: bool) -> list[Proposal]:
        with self._connect() as db:
            rows = db.execute(sql, args).fetchall()
        out = [Proposal.from_payload(json.loads(r[0])) for r in rows]
        return out if include_archived else [p for p in out if ai_id not in p.archived_by]

    def inbox(self, ai_id: str, *, include_archived: bool = False) -> list[Proposal]:
        return self._list(
            "SELECT p.payload FROM proposals p JOIN targets t ON t.proposal_id = p.id WHERE t.ai_id = ? "
            "ORDER BY p.created_at DESC",
            (ai_id,),
            ai_id,
            include_archived,
        )

    def outbox(self, ai_id: str, *, include_archived: bool = False) -> list[Proposal]:
        return self._list(
            "SELECT payload FROM proposals WHERE source = ? ORDER BY created_at DESC", (ai_id,), ai_id, include_archived
        )
