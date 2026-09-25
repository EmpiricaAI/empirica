"""
Git Goal Store - Cross-AI Goal Discovery

Stores goals in git notes for seamless cross-AI collaboration.
Enables AI-1 to create goals that AI-2 can discover and resume.

Key Features:
- Store goals in git notes (refs/notes/empirica/goals/<goal-id>)
- Discover goals by ai_id
- Resume goals with epistemic state transfer
- Track goal lineage (which AI worked on what)
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class GitCatFileBatch:
    """One long-lived `git cat-file --batch`, queried object by object.

    A notes tree is read from its raw bytes, `mode name\\0<20-byte sha>` per
    entry, so the first note under a ref is reached in two or three lookups
    without a process per ref.
    """

    def __init__(self, cwd):
        self._cwd = cwd
        self._proc = None

    def __enter__(self):
        self._proc = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd=self._cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return self

    def __exit__(self, *_exc):
        if self._proc is not None:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.wait(timeout=30)

    def fetch(self, spec: str) -> tuple[str, bytes] | None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            return None
        proc.stdin.write((spec + "\n").encode())
        proc.stdin.flush()
        header = proc.stdout.readline().decode().split()
        if len(header) != 3:
            return None  # "<spec> missing"
        _sha, otype, size = header
        body = proc.stdout.read(int(size))
        proc.stdout.read(1)  # trailing newline
        return otype, body

    @staticmethod
    def first_entry(tree: bytes) -> tuple[str, bytes] | None:
        """(mode, raw sha) of a tree's first entry; entries are path-sorted."""
        nul = tree.find(b"\0")
        if nul < 0 or len(tree) < nul + 21:
            return None
        mode = tree[:nul].decode().split(" ", 1)[0]
        return mode, tree[nul + 1 : nul + 21]

    def first_note_blob(self, ref: str) -> bytes | None:
        """The first note under a notes ref, in `git notes list` order."""
        got = self.fetch(f"{ref}^{{tree}}")
        if not got or got[0] != "tree":
            return None
        entry = self.first_entry(got[1])
        if entry is None:
            return None
        mode, raw = entry
        if mode.startswith("40"):  # fanout directory: descend once
            sub = self.fetch(raw.hex())
            if not sub or sub[0] != "tree":
                return None
            entry = self.first_entry(sub[1])
            if entry is None:
                return None
            _mode, raw = entry
        blob = self.fetch(raw.hex())
        return blob[1] if blob and blob[0] == "blob" else None


class GitGoalStore:
    """
    Git-based goal storage for cross-AI coordination

    Storage Format (git notes):
        refs/notes/empirica/goals/<goal-id>

    Goal Data:
        {
            "goal_id": "uuid",
            "session_id": "abc123",
            "ai_id": "claude-code",
            "created_at": "2025-11-27T...",
            "objective": "Implement feature X",
            "scope": {"breadth": 0.8, "duration": 0.9, "coordination": 0.7},
            "success_criteria": [...],
            "estimated_complexity": 0.7,
            "subtasks": [...],
            "epistemic_state": {
                "engagement": 0.85,
                "know": 0.70,
                ...
            },
            "lineage": [
                {"ai_id": "claude-code", "timestamp": "...", "action": "created"},
                {"ai_id": "mini-agent", "timestamp": "...", "action": "resumed"}
            ]
        }
    """

    def __init__(self, workspace_root: str | None = None):
        """Initialize git goal store"""
        self.workspace_root = workspace_root or os.getcwd()
        self._git_available = self._check_git_repo()

    def _check_git_repo(self) -> bool:
        """Check if we're in a git repository"""
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
        """Check if repo has at least one commit (HEAD exists)"""
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

    def store_goal(
        self,
        goal_id: str,
        session_id: str,
        ai_id: str,
        goal_data: dict[str, Any],
        epistemic_state: dict[str, float] | None = None,
        lineage: list[dict[str, str]] | None = None,
    ) -> bool:
        """
        Store goal in git notes

        Args:
            goal_id: Goal UUID
            session_id: Session identifier
            ai_id: AI that created goal
            goal_data: Complete goal data (from database)
            epistemic_state: Current epistemic vectors
            lineage: Goal lineage (if None, creates initial lineage)

        Returns:
            bool: Success
        """
        if not self._git_available:
            logger.debug("Not in git repo, skipping goal storage")
            return False

        if not self._has_commits():
            logger.debug("Git repo has no commits yet, skipping goal storage (create initial commit first)")
            return False

        try:
            # Build goal payload
            payload = {
                "goal_id": goal_id,
                "session_id": session_id,
                "ai_id": ai_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "goal_data": goal_data,
                "epistemic_state": epistemic_state or {},
                "lineage": lineage
                or [{"ai_id": ai_id, "timestamp": datetime.now(timezone.utc).isoformat(), "action": "created"}],
            }

            # Serialize
            payload_json = json.dumps(payload, indent=2)

            # Get current commit
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.workspace_root, capture_output=True, text=True, check=True
            )
            commit_hash = result.stdout.strip()

            # Store in git notes (refs/notes/empirica/goals/<goal-id>)
            note_ref = f"empirica/goals/{goal_id}"
            subprocess.run(
                ["git", "notes", f"--ref={note_ref}", "add", "-f", "-F", "-", commit_hash],
                input=payload_json,
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True,
            )

            logger.info(f"✓ Stored goal {goal_id[:8]} in git notes (ai={ai_id})")
            return True

        except Exception as e:
            logger.warning(f"Failed to store goal in git: {e}")
            return False

    def load_goal(self, goal_id: str) -> dict[str, Any] | None:
        """
        Load goal from git notes

        Args:
            goal_id: Goal UUID

        Returns:
            Dict: Goal payload or None
        """
        if not self._git_available:
            return None

        if not self._has_commits():
            return None

        try:
            # Try to find goal in git notes
            note_ref = f"empirica/goals/{goal_id}"

            # List which commit has the note (notes can be on any commit, not just HEAD)
            result = subprocess.run(
                ["git", "notes", f"--ref={note_ref}", "list"], cwd=self.workspace_root, capture_output=True, text=True
            )

            if result.returncode != 0 or not result.stdout.strip():
                return None

            # Format is: <blob> <commit>
            parts = result.stdout.strip().split()
            if len(parts) < 2:
                return None
            commit_hash = parts[1]

            # Load note from the commit it's actually attached to
            result = subprocess.run(
                ["git", "notes", f"--ref={note_ref}", "show", commit_hash],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                return None

            return json.loads(result.stdout)

        except Exception as e:
            logger.warning(f"Failed to load goal from git: {e}")
            return None

    def discover_goals(self, from_ai_id: str | None = None, session_id: str | None = None) -> list[dict[str, Any]]:
        """
        Discover goals from other AIs

        Args:
            from_ai_id: Filter by AI creator
            session_id: Filter by session

        Returns:
            List[Dict]: Matching goals
        """
        if not self._git_available:
            return []

        try:
            # List all goal note refs using for-each-ref
            # This properly handles custom refs like refs/notes/empirica/goals/*
            result = subprocess.run(
                ["git", "for-each-ref", "refs/notes/empirica/goals/"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                return []

            goals = []

            # One reader for every ref, not two git processes per goal. With
            # 3167 goal refs on one store, load_goal per ref took about 25 s and
            # a CLI contract test timed out under parallel load. None here means
            # the batch reader failed and the per-goal path is used instead.
            batch = self._load_all_goal_notes(result.stdout)

            # Parse for-each-ref output
            # Format: <commit-hash> commit\trefs/notes/empirica/goals/<goal-id>
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue

                parts = line.split("\t")
                if len(parts) < 2:
                    continue

                ref = parts[1]  # refs/notes/empirica/goals/<goal-id>
                if not ref.startswith("refs/notes/empirica/goals/"):
                    continue

                # Extract goal ID from ref path
                goal_id = ref.split("/")[-1]
                goal_data = batch.get(goal_id) if batch is not None else self.load_goal(goal_id)

                if not goal_data:
                    continue

                # Apply filters
                if from_ai_id and goal_data.get("ai_id") != from_ai_id:
                    continue
                if session_id and goal_data.get("session_id") != session_id:
                    continue

                goals.append(goal_data)

            return goals

        except Exception as e:
            logger.warning(f"Failed to discover goals: {e}")
            return []

    def _load_all_goal_notes(self, for_each_ref_output: str) -> dict[str, dict] | None:
        """Read the first note under every goal ref through one `git cat-file --batch`.

        Mirrors load_goal's choice exactly: `git notes list` sorts by the
        annotated commit's path, and load_goal takes its first line, so this
        walks each ref's notes tree in order and reads the first blob it finds,
        descending one fanout level when the tree is fanned out. Trees are
        parsed from their raw bytes: `mode name\\0<20-byte sha>` per entry.
        Returns None on any failure so the caller falls back to load_goal.
        """
        # for-each-ref prints "<sha> commit\trefs/notes/empirica/goals/<id>"
        refs = [
            token
            for line in for_each_ref_output.strip().split("\n")
            for token in line.split()
            if token.startswith("refs/notes/empirica/goals/")
        ]
        if not refs:
            return {}
        try:
            with GitCatFileBatch(self.workspace_root) as cat:
                out: dict[str, dict] = {}
                for ref in refs:
                    blob = cat.first_note_blob(ref)
                    if blob is None:
                        continue
                    try:
                        out[ref.split("/")[-1]] = json.loads(blob.decode())
                    except (ValueError, UnicodeDecodeError):
                        continue
                return out
        except Exception as e:
            logger.debug(f"batch goal-note read failed, falling back per goal: {e}")
            return None

    def add_lineage(self, goal_id: str, ai_id: str, action: str) -> bool:
        """
        Add lineage entry when AI resumes goal

        Args:
            goal_id: Goal UUID
            ai_id: AI taking action
            action: Action type (resumed, completed, modified)

        Returns:
            bool: Success
        """
        goal_data = self.load_goal(goal_id)
        if not goal_data:
            return False

        # Add lineage entry
        goal_data["lineage"].append(
            {"ai_id": ai_id, "timestamp": datetime.now(timezone.utc).isoformat(), "action": action}
        )

        # Re-store with updated lineage
        return self.store_goal(
            goal_id=goal_id,
            session_id=goal_data["session_id"],
            ai_id=goal_data["ai_id"],  # Keep original creator
            goal_data=goal_data["goal_data"],
            epistemic_state=goal_data.get("epistemic_state"),
            lineage=goal_data["lineage"],  # Pass updated lineage
        )
