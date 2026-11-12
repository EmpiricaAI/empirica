"""
Git Source Store - Epistemic Sources in Git Notes

Stores epistemic sources (references, documents, URLs) in git notes for sync.
Sources track what evidence was consulted or produced during work.

Storage: refs/notes/empirica/sources/<source-id>
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class GitSourceStore:
    """Git-based source storage for epistemic sync."""

    def __init__(self, workspace_root: str | None = None):
        self.workspace_root = workspace_root or os.getcwd()
        self._git_available = self._check_git_repo()

    def _check_git_repo(self) -> bool:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"], cwd=self.workspace_root, capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            # Not "not a repo": git could not answer. Reading this as absence
            # skipped the note write with a DEBUG line nobody sees, leaving a
            # SQLite row with no note for `rebuild` to import.
            logger.warning(
                "git could not answer in %s (%s); the git-notes write is skipped for this call",
                self.workspace_root,
                type(exc).__name__,
            )
            return False

    def _has_commits(self) -> bool:
        if not self._git_available:
            return False
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.workspace_root, capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            # Not "not a repo": git could not answer. Reading this as absence
            # skipped the note write with a DEBUG line nobody sees, leaving a
            # SQLite row with no note for `rebuild` to import.
            logger.warning(
                "git could not answer in %s (%s); the git-notes write is skipped for this call",
                self.workspace_root,
                type(exc).__name__,
            )
            return False

    def store_source(
        self,
        source_id: str,
        project_id: str,
        session_id: str,
        title: str,
        source_type: str = "document",
        source_url: str | None = None,
        doc_path: str | None = None,
        description: str | None = None,
        confidence: float = 0.7,
        direction: str = "noetic",
        ai_id: str = "",  # canonical: caller passes ai_id from InstanceResolver.ai_id()
        created_at: str | None = None,
        extra: dict | None = None,
    ) -> bool:
        """Write the source's note. ``extra`` carries the lifecycle (archived, reason, target, time) when the note is
        rewritten after one, and ``created_at`` keeps the original date (goal 25f4ab9a)."""
        if not self._git_available or not self._has_commits():
            return False

        try:
            payload = {
                **(extra or {}),
                "source_id": source_id,
                "project_id": project_id,
                "session_id": session_id,
                "ai_id": ai_id,
                "created_at": created_at or datetime.now(timezone.utc).isoformat(),
                "title": title,
                "source_type": source_type,
                "source_url": source_url,
                "doc_path": doc_path,
                "description": description,
                "confidence": confidence,
                "direction": direction,
            }

            note_ref = f"empirica/sources/{source_id}"
            # Rewrite where the current note lives, not at HEAD: a second note on a newer commit forks the ref (1.16.0 sweep).
            from empirica.core.canonical.empirica_git.note_lifecycle import note_target

            commit_hash = note_target(note_ref, self.workspace_root)
            subprocess.run(
                ["git", "notes", f"--ref={note_ref}", "add", "-f", "-F", "-", commit_hash],
                input=json.dumps(payload, indent=2),
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True,
            )

            logger.info(f"Source {source_id[:8]} stored in git notes")
            return True

        except Exception as e:
            logger.warning(f"Failed to store source in git: {e}")
            return False

    def load_source(self, source_id: str) -> dict | None:
        """The source's note, or None (no git, no note, unreadable)."""
        if not self._git_available:
            return None
        ref = f"empirica/sources/{source_id}"
        try:
            listed = subprocess.run(
                ["git", "notes", f"--ref={ref}", "list"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            parts = listed.stdout.split()
            if listed.returncode != 0 or len(parts) < 2:
                return None
            shown = subprocess.run(
                ["git", "notes", f"--ref={ref}", "show", parts[1]],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return json.loads(shown.stdout) if shown.returncode == 0 else None
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as exc:
            logger.warning("source note %s not readable: %s", source_id[:8], exc)
            return None

    def discover_sources(self) -> list[dict]:
        """Every source note in this repository (the rebuild reads these, goal 25f4ab9a)."""
        if not self._git_available:
            return []
        try:
            refs = subprocess.run(
                ["git", "for-each-ref", "--format=%(refname)", "refs/notes/empirica/sources/"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning("source notes not listed: %s", exc)
            return []
        out = []
        for ref in refs.stdout.split():
            note = self.load_source(ref.rsplit("/", 1)[-1])
            if note:
                out.append(note)
        return out


def insert_source_from_note(conn, note: dict, created_ts: float) -> int:
    """Insert one source row from its note, archived ones as archived (goal 25f4ab9a); returns 1 when new, 0 when the id
    exists or the note lacks what a row needs. Shared by the rebuild and profile import so the two cannot drift."""
    sid, title = note.get("source_id"), note.get("title")
    if not sid or not title or not note.get("project_id"):
        return 0
    conf = note.get("confidence")
    conf = conf if isinstance(conf, (int, float)) and not isinstance(conf, bool) and 0.0 <= conf <= 1.0 else None
    archived = note.get("archived") is True
    cur = conn.execute(
        "INSERT OR IGNORE INTO epistemic_sources (id, project_id, session_id, source_type, source_url, title, "
        "description, confidence, epistemic_layer, discovered_by_ai, discovered_at, archived, archive_reason, "
        "archive_target_id, archived_at, visibility) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'local')",
        (
            sid,
            note.get("project_id"),
            note.get("session_id"),
            note.get("source_type") or "document",
            note.get("source_url") or note.get("doc_path"),
            title,
            note.get("description"),
            conf,
            note.get("direction") or "noetic",
            note.get("ai_id"),
            created_ts,
            1 if archived else 0,
            note.get("archive_reason") if archived else None,
            note.get("archive_target_id") if archived else None,
            note.get("archived_at") if archived else None,
        ),
    )
    conn.commit()
    return cur.rowcount


def rename_source_note(old_id: str, new_id: str, workspace_root: str | None) -> str:
    """Move a source's note to its new id and archive the old ref, after the row's id was swapped (sources reconcile).
    Without it a rebuild restored the old id and a later archive restamp found no note. Returns what happened; never raises."""
    try:
        store = GitSourceStore(workspace_root=workspace_root)
        note = store.load_source(old_id)
        if not note:
            return "no note"
        moved = store.store_source(
            source_id=new_id,
            project_id=note.get("project_id") or "",
            session_id=note.get("session_id") or "",
            title=note.get("title") or "",
            source_type=note.get("source_type") or "document",
            source_url=note.get("source_url"),
            doc_path=note.get("doc_path"),
            description=note.get("description"),
            confidence=note.get("confidence", 0.7),
            direction=note.get("direction") or "noetic",
            ai_id=note.get("ai_id") or "",
            created_at=note.get("created_at"),
            extra={**note, "source_id": new_id, "renamed_from": old_id},
        )
        if not moved:
            return "not moved"
        from empirica.core.canonical.empirica_git.note_lifecycle import archive_note

        archived = archive_note("sources", old_id, workspace_root)
        return "moved" if archived.get("archived") else f"moved, old note kept: {archived.get('reason')}"
    except Exception as e:  # the row swap is done; a note that did not follow is reported, not raised
        logger.warning("source note %s not moved to %s: %s", old_id[:8], new_id[:8], e)
        return f"not moved: {type(e).__name__}"
