"""
Git Message Store - Inter-Agent Messaging via Git Notes

Stores messages in git notes for async inter-agent communication.
Messages persist in git, travel with the repo, sync via push/pull.

Key Features:
- Store messages in git notes (refs/notes/empirica/messages/<channel>/<message-id>)
- Channel-based compartmentalization (crosscheck, direct, broadcast, custom)
- Inbox filtering by recipient, channel, status
- TTL-based message expiry
- Thread support for conversations
- Delta polling via get_inbox_since()
- Pull-based subscription via subscribe()
"""

import json
import logging
import os
import socket
import subprocess
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class GitMessageStore:
    """
    Git-based message storage for inter-agent communication

    Storage Format (git notes):
        refs/notes/empirica/messages/<channel>/<message-id>

    Each message gets its own ref (consistent with findings, goals, etc.).
    Channel is encoded in the ref path for efficient discovery.
    """

    # At most one opportunistic prune per hour per repo, however many messages
    # are sent in between. Bounds the write path: a burst of sends triggers one
    # prune, not one per send.
    PRUNE_INTERVAL_SECONDS = 3600

    def __init__(self, workspace_root: str | None = None):
        """Initialize git message store"""
        self.workspace_root = workspace_root or os.getcwd()
        self._git_available = self._check_git_repo()
        self._machine_id = socket.gethostname()

    def _check_git_repo(self) -> bool:
        """Check if we're in a git repository"""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"], cwd=self.workspace_root, capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
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
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _get_head_commit(self) -> str | None:
        """Get current HEAD commit hash"""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.workspace_root, capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except Exception:
            return None

    def _is_expired(self, message: dict) -> bool:
        """Check if a message has expired based on TTL"""
        ttl = message.get("ttl", 86400)
        if ttl == 0:
            return False  # No expiry
        try:
            created = datetime.fromisoformat(message["timestamp"])
            return datetime.now(timezone.utc) > created + timedelta(seconds=ttl)
        except (KeyError, ValueError):
            return False

    def send_message(
        self,
        from_ai_id: str,
        to_ai_id: str,
        channel: str,
        subject: str,
        body: str,
        message_type: str = "request",
        to_machine: str | None = None,
        from_session_id: str | None = None,
        reply_to: str | None = None,
        thread_id: str | None = None,
        ttl: int = 86400,
        priority: str = "normal",
        metadata: dict | None = None,
    ) -> str | None:
        """
        Send a message to another agent.

        Returns message_id on success, None on failure.
        """
        if not self._git_available or not self._has_commits():
            logger.debug("Git not available, skipping message send")
            return None

        try:
            message_id = str(uuid.uuid4())

            payload = {
                "message_id": message_id,
                "channel": channel,
                "from": {
                    "ai_id": from_ai_id,
                    "machine": self._machine_id,
                    "session_id": from_session_id,
                },
                "to": {
                    "ai_id": to_ai_id,
                    "machine": to_machine,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": message_type,
                "subject": subject,
                "body": body,
                "reply_to": reply_to,
                "thread_id": thread_id or message_id,
                "ttl": ttl,
                "priority": priority,
                "status": "unread",
                "read_by": [],
                "metadata": metadata or {},
            }

            payload_json = json.dumps(payload, indent=2)

            commit_hash = self._get_head_commit()
            if not commit_hash:
                return None

            note_ref = f"empirica/messages/{channel}/{message_id}"
            subprocess.run(
                ["git", "notes", f"--ref={note_ref}", "add", "-f", "-m", payload_json, commit_hash],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True,
            )

            logger.info(f"Sent message {message_id[:8]} on #{channel} to {to_ai_id}")

            # Enforce the TTL from the event that grows the ref set. Interval-gated
            # and best-effort — see _maybe_prune_expired. Deliberately AFTER the
            # send has succeeded, so a prune failure can never cost a message.
            self._maybe_prune_expired()

            return message_id

        except Exception as e:
            logger.warning(f"Failed to send message: {e}")
            return None

    def load_message(self, channel: str, message_id: str) -> dict[str, Any] | None:
        """Load a single message by channel and ID."""
        if not self._git_available:
            return None

        try:
            note_ref = f"empirica/messages/{channel}/{message_id}"

            # List which commit has the note
            result = subprocess.run(
                ["git", "notes", f"--ref={note_ref}", "list"], cwd=self.workspace_root, capture_output=True, text=True
            )

            if result.returncode != 0 or not result.stdout.strip():
                return None

            parts = result.stdout.strip().split()
            if len(parts) < 2:
                return None
            commit_hash = parts[1]

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
            logger.warning(f"Failed to load message: {e}")
            return None

    def _batch_load_notes(self, refs: list[str]) -> dict[str, dict[str, Any]]:
        """Read every message note in THREE git calls, whatever N is.

        #394 problem 1 (FrancisFerrero): get_inbox called load_message per ref,
        and load_message spawns two subprocesses — `git notes list` to find the
        annotated commit, then `git notes show` for content. A 500-message
        mailbox cost 1001 process spawns to return 5.

        The shape here:

          1. `for-each-ref --format='%(refname) %(objectname)'` — every ref AND
             its notes-commit object, one call (the caller already made it).
          2. `cat-file --batch` fed `<commit>^{tree}` — each message ref holds
             exactly one note, so each tree has one entry; parse its blob sha.
          3. `cat-file --batch` fed those blob shas — all contents, one call.

        Trees are BINARY (`<mode> <name>\\0<20 raw bytes>`), so stdout is read as
        bytes and only the note payload is decoded. Returns {refname: message}
        and silently skips anything malformed — a single unreadable note must
        not take the whole inbox down, which is the behaviour load_message had.
        """
        if not refs:
            return {}

        def _batch(requests: list[str]) -> list[tuple[str, str, bytes]]:
            """Run `cat-file --batch` and frame the output into (sha, type, payload)."""
            proc = subprocess.run(
                ["git", "cat-file", "--batch"],
                cwd=self.workspace_root,
                input="\n".join(requests).encode() + b"\n",
                capture_output=True,
            )
            if proc.returncode != 0:
                return []
            out, pos, framed = proc.stdout, 0, []
            while pos < len(out):
                nl = out.find(b"\n", pos)
                if nl == -1:
                    break
                header = out[pos:nl].decode("utf-8", "replace").split()
                pos = nl + 1
                # "<sha> missing" for anything unresolvable — no payload follows.
                if len(header) < 3:
                    continue
                sha, otype, size = header[0], header[1], int(header[2])
                framed.append((sha, otype, out[pos : pos + size]))
                pos += size + 1  # payload is followed by a newline
            return framed

        # Phase 1: ref -> notes-commit tree -> the single note blob inside it.
        trees = _batch([f"{r}^{{tree}}" for r in refs])
        blob_for_ref: dict[str, str] = {}
        for ref, (_sha, otype, payload) in zip(refs, trees, strict=False):
            if otype != "tree":
                continue
            nul = payload.find(b"\x00")
            if nul == -1 or len(payload) < nul + 21:
                continue
            blob_for_ref[ref] = payload[nul + 1 : nul + 21].hex()

        # Phase 2: every note payload in one call.
        contents = _batch(list(blob_for_ref.values()))
        by_sha = {sha: payload for sha, otype, payload in contents if otype == "blob"}

        messages: dict[str, dict[str, Any]] = {}
        for ref, blob in blob_for_ref.items():
            raw = by_sha.get(blob)
            if raw is None:
                continue
            try:
                messages[ref] = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
        return messages

    def get_inbox(
        self,
        ai_id: str,
        machine: str | None = None,
        channel: str | None = None,
        status: str = "unread",
        include_expired: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Get messages addressed to this agent.

        Scans channels for messages where to.ai_id matches or is "*" (broadcast).
        Filters by status and TTL.
        """
        if not self._git_available:
            return []

        try:
            # Scope the search by channel if specified
            search_prefix = f"refs/notes/empirica/messages/{channel}/" if channel else "refs/notes/empirica/messages/"

            result = subprocess.run(
                ["git", "for-each-ref", search_prefix, "--format=%(refname)"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                return []

            # Collect the refs first, then read every note in ONE batch. This
            # used to call load_message per ref, and load_message spawns two
            # subprocesses (`git notes list`, then `git notes show`) — 2N+1
            # spawns to answer a question about N messages (#394 problem 1).
            refs = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                ref_parts = line.strip().split("/")
                if len(ref_parts) < 6:
                    continue
                refs.append(line.strip())

            messages = []
            for msg in self._batch_load_notes(refs).values():
                if not msg:
                    continue
                if not self._matches_inbox_filters(msg, ai_id, machine, status, include_expired):
                    continue
                messages.append(msg)

            # Sort THEN slice. Reported by FrancisFerrero (#394, problem 2): the break
            # used to run inside the loop, before this sort. Iteration order is ref
            # name — a message UUID — so a limited fetch returned an arbitrary subset
            # which was then sorted, not the newest N.
            #
            # Their symptom chain is the part worth keeping: with a backlog above the
            # limit, marking messages read let OLDER messages enter the window and be
            # reported as new. They chased that as a notification bug before finding it
            # here, which is what a wrong-subset bug looks like from the outside — not
            # obviously wrong data, just data that keeps changing for no reason.
            #
            # The early break did bound work when many messages match. Correctness wins:
            # the ref scan is O(total) regardless because the filter needs each message's
            # content, and that cost is problem 1 of the same issue — a real fix (one
            # for-each-ref plus a single `git cat-file --batch`) rather than a break that
            # only looked like one.
            messages.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
            return messages[:limit]

        except Exception as e:
            logger.warning(f"Failed to get inbox: {e}")
            return []

    def _matches_inbox_filters(
        self,
        msg: dict,
        ai_id: str,
        machine: str | None,
        status: str,
        include_expired: bool,
    ) -> bool:
        """Check if a message passes all inbox filters."""
        to_info = msg.get("to", {})
        if to_info.get("ai_id") != "*" and to_info.get("ai_id") != ai_id:
            return False

        if machine and to_info.get("machine") and to_info["machine"] != machine:
            return False

        if not include_expired and self._is_expired(msg):
            return False

        if status == "unread":
            read_ids = [r.get("ai_id") for r in msg.get("read_by", [])]
            if ai_id in read_ids:
                return False
        elif status == "read":
            read_ids = [r.get("ai_id") for r in msg.get("read_by", [])]
            if ai_id not in read_ids:
                return False

        return True

    def mark_read(
        self,
        channel: str,
        message_id: str,
        ai_id: str,
        machine: str | None = None,
    ) -> bool:
        """Mark a message as read by this agent."""
        msg = self.load_message(channel, message_id)
        if not msg:
            return False

        try:
            # Add to read_by if not already present
            read_by = msg.get("read_by", [])
            read_ids = [r.get("ai_id") for r in read_by]
            if ai_id not in read_ids:
                read_by.append(
                    {
                        "ai_id": ai_id,
                        "machine": machine or self._machine_id,
                        "read_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                msg["read_by"] = read_by
                msg["status"] = "read"

            # Re-store with force flag
            payload_json = json.dumps(msg, indent=2)
            note_ref = f"empirica/messages/{channel}/{message_id}"

            # Find the commit it's attached to
            result = subprocess.run(
                ["git", "notes", f"--ref={note_ref}", "list"], cwd=self.workspace_root, capture_output=True, text=True
            )
            parts = result.stdout.strip().split()
            commit_hash = parts[1] if len(parts) >= 2 else (self._get_head_commit() or "HEAD")

            subprocess.run(
                ["git", "notes", f"--ref={note_ref}", "add", "-f", "-m", payload_json, commit_hash],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True,
            )

            return True

        except Exception as e:
            logger.warning(f"Failed to mark message read: {e}")
            return False

    def reply(
        self,
        original_message_id: str,
        original_channel: str,
        from_ai_id: str,
        body: str,
        message_type: str = "response",
        from_session_id: str | None = None,
        ttl: int = 86400,
        metadata: dict | None = None,
    ) -> str | None:
        """Reply to an existing message."""
        original = self.load_message(original_channel, original_message_id)
        if not original:
            logger.warning(f"Cannot reply: original message {original_message_id[:8]} not found")
            return None

        # Reverse from/to, inherit thread_id
        return self.send_message(
            from_ai_id=from_ai_id,
            to_ai_id=original["from"]["ai_id"],
            channel=original_channel,
            subject=f"Re: {original.get('subject', '')}",
            body=body,
            message_type=message_type,
            to_machine=original["from"].get("machine"),
            from_session_id=from_session_id,
            reply_to=original_message_id,
            thread_id=original.get("thread_id", original_message_id),
            ttl=ttl,
            metadata=metadata,
        )

    def get_thread(
        self,
        thread_id: str,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get all messages in a thread, ordered by timestamp."""
        if not self._git_available:
            return []

        try:
            search_prefix = f"refs/notes/empirica/messages/{channel}/" if channel else "refs/notes/empirica/messages/"

            result = subprocess.run(
                ["git", "for-each-ref", search_prefix, "--format=%(refname)"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                return []

            messages = []

            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue

                ref_parts = line.strip().split("/")
                if len(ref_parts) < 6:
                    continue

                msg_channel = ref_parts[4]
                msg_id = ref_parts[5]
                msg = self.load_message(msg_channel, msg_id)

                if msg and msg.get("thread_id") == thread_id:
                    messages.append(msg)

            messages.sort(key=lambda m: m.get("timestamp", ""))
            return messages

        except Exception as e:
            logger.warning(f"Failed to get thread: {e}")
            return []

    def discover_channels(self) -> list[str]:
        """List all channels with messages."""
        if not self._git_available:
            return []

        try:
            result = subprocess.run(
                ["git", "for-each-ref", "refs/notes/empirica/messages/", "--format=%(refname)"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                return []

            channels = set()
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                ref_parts = line.strip().split("/")
                if len(ref_parts) >= 5:
                    channels.add(ref_parts[4])

            return sorted(channels)

        except Exception:
            return []

    def count_unread(self, ai_id: str, machine: str | None = None) -> dict[str, int]:
        """Count unread messages per channel."""
        channels = self.discover_channels()
        counts = {}
        for ch in channels:
            msgs = self.get_inbox(ai_id, machine=machine, channel=ch, status="unread")
            if msgs:
                counts[ch] = len(msgs)
        return counts

    def _batch_delete_refs(self, refs: list[str]) -> bool:
        """Delete every ref in ONE `git update-ref --stdin`, not one spawn each."""
        if not refs:
            return True
        commands = "".join(f"delete {ref}\n" for ref in refs)
        try:
            proc = subprocess.run(
                ["git", "update-ref", "--stdin"],
                cwd=self.workspace_root,
                input=commands.encode(),
                capture_output=True,
                timeout=30,
            )
            if proc.returncode != 0:
                logger.warning(f"Batch ref delete failed: {proc.stderr.decode(errors='replace')[:200]}")
                return False
            return True
        except Exception as e:
            logger.warning(f"Batch ref delete failed: {e}")
            return False

    def _maybe_prune_expired(self) -> None:
        """Prune expired messages on write, at most once per PRUNE_INTERVAL.

        #394's "Adjacent" note: the default TTL is 86400 but NOTHING enforces it
        unless someone schedules `message-cleanup`. The reporter's repo had 306
        refs of which 150 were already expired — a TTL that expires nothing is a
        setting, not a policy.

        A scheduled loop is the wrong mechanism here: cron is opt-in-only in this
        project and never installed by default, so a fix that depends on one is a
        fix that does not run for anybody who did not read the docs — the same
        leave-it-to-operator-memory shape as the docs index's second step.

        So it hangs off a real event instead. Sending is the natural one: it is
        the act that GROWS the ref set, so the cost is paid by whoever creates
        it, and a repo nobody writes to does not need pruning. The interval
        marker keeps it amortized — a burst of 100 sends triggers one prune, not
        100 — and best-effort throughout, because failing to prune must never
        fail the send that triggered it.
        """
        # workspace_root is a str (it is handed straight to subprocess cwd=),
        # so build the path rather than assuming Path semantics.
        marker = Path(self.workspace_root) / ".git" / "empirica-last-message-prune"
        now = time.time()
        try:
            if marker.exists() and (now - marker.stat().st_mtime) < self.PRUNE_INTERVAL_SECONDS:
                return
            if not marker.parent.is_dir():
                return
            # Stamp BEFORE pruning, not after: if the prune raises or the process
            # dies mid-way, a marker written only on success would let every
            # subsequent send retry it, turning a broken prune into a permanent
            # tax on the write path.
            marker.touch()
        except OSError:
            return

        try:
            self.cleanup_expired()
        except Exception as e:
            logger.debug(f"Opportunistic prune skipped: {e}")

    def cleanup_expired(self, dry_run: bool = False) -> list[dict[str, Any]]:
        """
        Remove expired messages.

        Returns list of removed (or would-be-removed) messages.
        """
        if not self._git_available:
            return []

        try:
            result = subprocess.run(
                ["git", "for-each-ref", "refs/notes/empirica/messages/", "--format=%(refname)"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                return []

            # Batch-read, batch-delete. This function had the SAME 2N defect #394
            # reported in get_inbox — load_message per ref, two spawns each — and
            # it survived the original fix because that fix addressed the reported
            # instance rather than the class. Deleting was worse than reading: one
            # `update-ref -d` spawn per expired message, and cleanup is exactly the
            # path where the expired count is largest (the reporter's repo: 150 of
            # 306). Now three reads and one write, whatever N is.
            refs = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
            refs = [r for r in refs if len(r.split("/")) >= 6]

            expired_refs = []
            removed = []
            for refname, msg in self._batch_load_notes(refs).items():
                if msg and self._is_expired(msg):
                    removed.append(msg)
                    expired_refs.append(refname)

            if expired_refs and not dry_run:
                self._batch_delete_refs(expired_refs)
                logger.info(f"Removed {len(expired_refs)} expired message(s)")

            return removed

        except Exception as e:
            logger.warning(f"Failed to cleanup messages: {e}")
            return []

    def get_inbox_since(
        self,
        ai_id: str,
        since_timestamp: float,
        channel: str | None = None,
        machine: str | None = None,
        include_expired: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get messages addressed to this agent that arrived after a given time.

        Delta-polling primitive used by pull-based subscription. Filters by
        message timestamp (creation time) rather than read status.

        Args:
            ai_id: Recipient agent identifier
            since_timestamp: Unix timestamp — only return messages newer than this
            channel: Restrict to a single channel (None = all channels)
            machine: Match only messages targeting this machine (None = any)
            include_expired: Whether to return TTL-expired messages
            limit: Maximum number of messages to return

        Returns:
            List of message dicts, newest first
        """
        if not self._git_available:
            return []

        since_dt = datetime.fromtimestamp(since_timestamp, tz=timezone.utc)
        since_iso = since_dt.isoformat()

        # Reuse get_inbox with status='all' and filter by timestamp
        all_msgs = self.get_inbox(
            ai_id=ai_id,
            machine=machine,
            channel=channel,
            status="all",
            include_expired=include_expired,
            limit=limit * 2,  # Over-fetch since we filter after
        )

        # Filter by creation timestamp
        filtered = [m for m in all_msgs if m.get("timestamp", "") > since_iso]

        return filtered[:limit]

    def subscribe(
        self,
        ai_id: str,
        channel: str | None = None,
        callback: Callable[[dict[str, Any]], None] | None = None,
        poll_interval: float = 2.0,
        mark_read: bool = True,
        stop_event: Any = None,
        machine: str | None = None,
    ) -> None:
        """
        Poll for new messages on a channel and invoke callback.

        Blocks the caller — intended to run in a dedicated thread or subprocess.
        Uses `get_inbox_since` to fetch deltas since the last poll timestamp.

        Args:
            ai_id: Recipient identifier
            channel: Channel to poll (None = all channels)
            callback: Function called for each new message
            poll_interval: Seconds between polls
            mark_read: Mark messages as read after processing
            stop_event: Optional threading.Event-like object; subscription
                        stops when stop_event.is_set() returns True
            machine: Filter by recipient machine

        Note:
            This is a simple pull-based subscription. For production use
            consider running in a thread and passing a stop_event to allow
            clean shutdown.
        """
        if callback is None:
            logger.warning("subscribe() called without callback — no-op")
            return

        last_poll = time.time()

        def _should_stop() -> bool:
            if stop_event is None:
                return False
            is_set = getattr(stop_event, "is_set", None)
            return bool(is_set() if callable(is_set) else False)

        while not _should_stop():
            try:
                new_msgs = self.get_inbox_since(
                    ai_id=ai_id,
                    since_timestamp=last_poll,
                    channel=channel,
                    machine=machine,
                )
                for msg in new_msgs:
                    try:
                        callback(msg)
                    except Exception as e:
                        logger.warning(f"Subscription callback failed: {e}")
                        continue
                    if mark_read:
                        self.mark_read(
                            channel=msg.get("channel", ""),
                            message_id=msg.get("message_id", ""),
                            ai_id=ai_id,
                            machine=machine,
                        )
                last_poll = time.time()
            except Exception as e:
                logger.warning(f"Subscription poll failed: {e}")

            # Sleep in small increments so stop_event can interrupt quickly
            elapsed = 0.0
            while elapsed < poll_interval and not _should_stop():
                time.sleep(min(0.5, poll_interval - elapsed))
                elapsed += 0.5
