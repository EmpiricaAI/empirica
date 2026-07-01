"""
Session ID Resolver - Resolve session aliases to UUIDs

Supports magic aliases for easy session resumption:
- "latest", "last", or "auto" - Most recent session
- "latest:active" - Most recent active session (not ended)
- "latest:<ai_id>" - Most recent session for specific AI
- "latest:active:<ai_id>" - Most recent active session for specific AI

Also provides TTY-based session isolation for multi-instance support:
- get_tty_key() - Get TTY-based key for current terminal
- get_tty_session() - Read Claude session mapping from TTY-keyed file
- write_tty_session() - Write Claude session mapping (called by hooks)

Examples:
    resolve_session_id("latest")
    resolve_session_id("latest:active")
    resolve_session_id("latest:claude-code")
    resolve_session_id("latest:active:claude-code")
    resolve_session_id("88dbf132")  # Partial UUID still works
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# InstanceResolver — Unified API for project/session/transaction resolution
#
# This class groups all resolution functions into a single importable API.
# Hooks, CLI commands, sentinel, and statusline should all use this class.
#
# For backward compatibility, all methods are also available as module-level
# functions (the originals). The class delegates to them — no logic is
# duplicated.
#
# Usage:
#     from empirica.utils.session_resolver import InstanceResolver
#     resolver = InstanceResolver()
#     project_path = resolver.project_path()
#     session_id = resolver.session_id(claude_session_id="...")
#     suffix = resolver.instance_suffix()
# =============================================================================


class InstanceResolver:
    """Unified context resolution for all Empirica components.

    Groups instance, project, session, and transaction resolution into a
    single class. Every method delegates to the canonical module-level
    function — this class is organizational, not a reimplementation.

    Designed to be the single import for hooks and CLI:
        from empirica.utils.session_resolver import InstanceResolver
    """

    # --- Instance Identity ---

    @staticmethod
    def instance_id() -> "str | None":
        """Get current instance ID (TMUX_PANE, WINDOWID, TTY, etc.)."""
        return get_instance_id()

    @staticmethod
    def instance_suffix() -> str:
        """Get sanitized filename suffix for this instance.
        e.g. '_tmux_0', '_x11_78940210', or '' if no instance.
        """
        return _get_instance_suffix()

    # --- Project Resolution ---

    @staticmethod
    def project_path(claude_session_id: str | None = None) -> "str | None":
        """Resolve the active project path.

        Priority: instance_projects > active_work_{uuid} > active_work.json
        Returns None (not CWD) if unresolvable.
        """
        return get_active_project_path(claude_session_id)

    # --- Session Resolution ---

    @staticmethod
    def session_id(claude_session_id: str | None = None) -> "str | None":
        """Resolve the active Empirica session ID.

        Priority: transaction > active_work_{uuid} > instance_projects >
                  tty_session > active_work.json > DB fallback
        """
        return get_active_empirica_session_id(claude_session_id)

    @staticmethod
    def resolve_session(session_id_or_alias: str, ai_id: str | None = None) -> str:
        """Resolve a partial session ID, alias, or 'latest' to full UUID."""
        return resolve_session_id(session_id_or_alias, ai_id)

    @staticmethod
    def latest_session_id(ai_id: str | None = None, active_only: bool = False) -> "str | None":
        """Get the most recent session ID, optionally filtered by ai_id."""
        return get_latest_session_id(ai_id, active_only)

    # --- Context ---

    @staticmethod
    def context(claude_session_id: str | None = None) -> dict:
        """Get the full active context (project_path, session_id, instance_id, etc.)."""
        return get_active_context(claude_session_id)

    @staticmethod
    def engagement(claude_session_id: str | None = None) -> "str | None":
        """Get the active engagement ID."""
        return get_active_engagement(claude_session_id)

    # --- Transaction Lifecycle ---

    @staticmethod
    def transaction_id(claude_session_id: str | None = None) -> "str | None":
        """Read just the active transaction ID."""
        return read_active_transaction(claude_session_id)

    @staticmethod
    def transaction_read(claude_session_id: str | None = None) -> "dict | None":
        """Read the full active transaction state from filesystem."""
        return read_active_transaction_full(claude_session_id)

    @staticmethod
    def transaction_write(
        transaction_id: str,
        session_id: str | None = None,
        preflight_timestamp: float | None = None,
        status: str = "open",
        project_path: str | None = None,
        claude_session_id: str | None = None,
    ) -> None:
        """Write (create or update) the active transaction file."""
        write_active_transaction(
            transaction_id=transaction_id,
            session_id=session_id,
            preflight_timestamp=preflight_timestamp,
            status=status,
            project_path=project_path,
            claude_session_id=claude_session_id,
        )

    @staticmethod
    def transaction_clear(claude_session_id: str | None = None) -> None:
        """Delete the active transaction file."""
        clear_active_transaction(claude_session_id)

    @staticmethod
    def transaction_increment(claude_session_id: str | None = None) -> "dict | None":
        """Increment the tool call counter in the active transaction."""
        return increment_transaction_tool_count(claude_session_id)

    # --- Hook Counters (separate from transaction lifecycle) ---

    @staticmethod
    def counters_read(claude_session_id: str | None = None) -> "dict | None":
        """Read the hook counters file."""
        return read_hook_counters(claude_session_id)

    @staticmethod
    def counters_write(data: dict, claude_session_id: str | None = None) -> bool:
        """Atomically write the hook counters file."""
        return write_hook_counters(data, claude_session_id)

    @staticmethod
    def counters_clear(claude_session_id: str | None = None) -> None:
        """Delete the hook counters file."""
        clear_hook_counters(claude_session_id)

    # --- TTY Session ---

    @staticmethod
    def tty_key() -> "str | None":
        """Get the TTY device key for this terminal."""
        return get_tty_key()

    @staticmethod
    def tty_session(warn_if_stale: bool = True) -> "dict | None":
        """Read the TTY session file for this terminal."""
        return get_tty_session(warn_if_stale=warn_if_stale)

    @staticmethod
    def tty_write(
        claude_session_id: str | None = None, empirica_session_id: str | None = None, project_path: str | None = None
    ) -> bool:
        """Write session mapping to TTY + instance_projects files."""
        return write_tty_session(
            claude_session_id=claude_session_id,
            empirica_session_id=empirica_session_id,
            project_path=project_path,
        )

    # --- Project Helpers ---

    @staticmethod
    def project_id_from_db(project_path) -> "str | None":
        """Get project_id from a project's local sessions.db."""
        return _get_project_id_from_local_db(project_path)

    @staticmethod
    def ai_id(
        claude_session_id: str | None = None,
        project_path: "str | Path | None" = None,
    ) -> "str | None":
        """Resolve the canonical ai_id.

        Strict-canonical convention: the AI is identified by its home
        project's basename — **prefix KEPT**. `empirica-cortex` stays
        `empirica-cortex`, not `cortex`. Short aliases (`cortex`,
        `mesh-support`) are a human-conversational layer that lives in
        skills + system prompt; code paths must use the full basename
        so cortex orchestration + ntfy event filtering stay consistent.

        Source-of-truth is `.empirica/project.yaml`'s `ai_id` field
        (written by setup-claude-code at project init). The basename
        fallback only fires when project.yaml is missing the field.

        ai_id is THE anchor for cross-machine portability. cwd is just
        working-context. See docs/architecture/AI_ID_AS_ANCHOR.md.

        Resolution chain:
          1. explicit project_path argument (skip resolver chain — used
             when caller already has the path, e.g. iterating cockpit
             instances each with their own project_path)
          2. otherwise resolve via get_active_project_path(claude_session_id)
          3. project.yaml `ai_id` field at that path
          4. basename(project_path) — exact, prefix kept
          5. None — caller falls back to tmux pane id or 'claude-code'

        Used by:
          - session-monitor-arm hook (Monitor grep-filter lookup)
          - TUI install path (handle_loop_enable --instance <ai_id>)
          - cortex_propose source_claude / target_claudes routing
          - cockpit per-instance ai_id rendering
          - project_init at provisioning time
        """
        from pathlib import Path

        resolved_path = str(project_path) if project_path is not None else get_active_project_path(claude_session_id)
        if not resolved_path:
            return None
        # Priority: explicit ai_id in project.yaml
        try:
            import yaml

            proj_yaml = Path(resolved_path) / ".empirica" / "project.yaml"
            if proj_yaml.exists():
                data = yaml.safe_load(proj_yaml.read_text()) or {}
                aid = data.get("ai_id")
                if aid:
                    return str(aid)
        except Exception:
            pass
        # Fallback: exact basename, prefix kept.
        basename = Path(resolved_path).name
        return basename or None

    @staticmethod
    def resolve_workspace_project(identifier: str) -> "dict | None":
        """Resolve a project name/path/id via the workspace database."""
        return _resolve_via_workspace_db(identifier)

    # --- Cleanup ---

    @staticmethod
    def cleanup_stale_instances() -> int:
        """Remove orphaned instance_projects files (tmux only)."""
        return cleanup_stale_instance_projects()

    @staticmethod
    def cleanup_stale_files(current_claude_session_id: str | None = None) -> int:
        """Remove stale active_work, instance_projects, and active_session files."""
        return cleanup_stale_active_work_files(current_claude_session_id)

    # --- Mode Detection ---

    @staticmethod
    def is_headless() -> bool:
        """Check if running in headless/containerized mode."""
        return is_headless()


# =============================================================================
# Headless Mode Detection
# =============================================================================


def is_headless() -> bool:
    """Detect headless/containerized mode.

    Returns True when no terminal identity exists (no TMUX_PANE, WINDOWID,
    TTY, etc.). Can be forced via EMPIRICA_HEADLESS=true env var.

    In headless mode:
    - active_work.json is primary (no instance_projects)
    - Statusline is disabled
    - Single-instance assumption (no multi-pane isolation)
    """
    # Explicit override
    explicit = os.environ.get("EMPIRICA_HEADLESS", "").lower()
    if explicit in ("true", "1", "yes"):
        return True
    if explicit in ("false", "0", "no"):
        return False

    # Auto-detect: headless if get_instance_id() returns None
    return get_instance_id() is None


# =============================================================================
# TTY-based Session Isolation (Multi-Instance Support)
# =============================================================================


def get_tty_key() -> str | None:
    """Get a TTY-based key for session isolation. Returns None if no TTY.

    Walks up the process tree to find the controlling TTY. This handles
    cases where CLI commands run via bash (which may not have a TTY) but
    the grandparent Claude process does.

    Returns sanitized string like 'pts-2' or None if no TTY found.

    CRITICAL: No PPID fallback. If TTY detection fails, return None to signal
    that instance isolation cannot be guaranteed. Callers must handle None
    by failing safely rather than risking cross-instance bleed.
    """
    try:
        # Walk up process tree looking for a TTY
        pid = os.getppid()
        for _ in range(5):  # Max 5 levels up
            if pid <= 1:
                break

            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "tty=,ppid="], capture_output=True, text=True, timeout=2
            )
            if result.returncode != 0:
                break

            parts = result.stdout.strip().split()
            if not parts:
                break

            tty = parts[0]
            # macOS ps returns '??' for no-TTY processes (not '?' like Linux)
            if tty and not tty.startswith("?"):
                return tty.replace("/", "-")

            # Move to parent
            if len(parts) > 1:
                try:
                    pid = int(parts[1])
                except ValueError:
                    break
            else:
                break
    except Exception:
        pass
    return None  # No fallback - fail safely


def get_tty_session(warn_if_stale: bool = True) -> dict[str, Any] | None:
    """Read session mapping from TTY-keyed file.

    Returns dict with:
        - claude_session_id: Claude Code conversation UUID
        - empirica_session_id: Empirica session UUID
        - project_path: Project directory path
        - tty_key: The TTY key used
        - instance_id: TMUX pane instance identifier (e.g. 'tmux_3')
        - timestamp: When the mapping was written
        - pid: Process ID that wrote the session
        - ppid: Parent process ID

    Args:
        warn_if_stale: If True, logs warnings for potentially stale sessions

    Returns None if no TTY key available, no session file exists, or on read error.
    """
    tty_key = get_tty_key()
    if not tty_key:
        return None  # No TTY - cannot determine instance

    tty_sessions_dir = Path.home() / ".empirica" / "tty_sessions"
    session_file = tty_sessions_dir / f"{tty_key}.json"

    if not session_file.exists():
        return None

    try:
        with open(session_file) as f:
            session = json.load(f)

        # Validate and warn if stale
        if warn_if_stale:
            validation = validate_tty_session(session)
            for warning in validation.get("warnings", []):
                logger.warning(f"TTY session warning: {warning}")

            # If TTY device is gone, return None (session is invalid)
            if not validation.get("valid", True):
                logger.warning("TTY session is invalid, ignoring")
                return None

        return session
    except Exception as e:
        logger.debug(f"Failed to read TTY session file: {e}")
        return None


def write_tty_session(
    claude_session_id: str | None = None, empirica_session_id: str | None = None, project_path: str | None = None
) -> bool:
    """Write session mapping to TTY-keyed file for CLI commands to read.

    This bridges the gap between hooks (which receive claude_session_id) and
    CLI commands (which don't). Both run in the same TTY context.

    Can be called from:
    - Claude Code hooks (have claude_session_id, may have empirica_session_id)
    - CLI session-create (no claude_session_id, has empirica_session_id)

    CRITICAL: Returns False if no TTY available - does not use PPID fallback
    to avoid cross-instance bleed risk.

    Also writes an instance mapping file keyed by instance_id (if available).
    This enables hook context lookups where `tty` command fails but instance_id
    is available (via TMUX_PANE, WINDOWID, etc.).

    Args:
        claude_session_id: Claude Code conversation UUID (optional for CLI)
        empirica_session_id: Empirica session UUID (optional)
        project_path: Project directory path (optional)

    Returns:
        True if at least one session file was written (TTY or instance_projects),
        False if neither TTY nor instance_id is available.
    """
    from datetime import datetime

    tty_key = get_tty_key()
    # Use canonical get_instance_id() which supports tmux, X11, macOS Terminal, TTY
    instance_id = get_instance_id()

    wrote_something = False

    try:
        # Write instance_projects FIRST - works via Bash tool where tty fails
        # This is the PRIMARY mechanism for multi-instance isolation
        if instance_id and project_path:
            instance_dir = Path.home() / ".empirica" / "instance_projects"
            instance_dir.mkdir(parents=True, exist_ok=True)
            instance_file = instance_dir / f"{instance_id}.json"
            instance_data = {
                "project_path": project_path,
                "tty_key": tty_key,  # May be None if via Bash tool
                "claude_session_id": claude_session_id,  # Key for active_work lookup
                "empirica_session_id": empirica_session_id,
                "timestamp": datetime.now().isoformat(),
            }
            with open(instance_file, "w") as f:
                json.dump(instance_data, f, indent=2)
            logger.debug(f"Wrote instance mapping: {instance_file}")
            wrote_something = True

        # Write TTY session file if TTY is available (direct terminal context)
        if tty_key:
            tty_sessions_dir = Path.home() / ".empirica" / "tty_sessions"
            tty_sessions_dir.mkdir(parents=True, exist_ok=True)
            session_file = tty_sessions_dir / f"{tty_key}.json"

            data = {
                "claude_session_id": claude_session_id,
                "empirica_session_id": empirica_session_id,
                "project_path": project_path,
                "tty_key": tty_key,
                "instance_id": instance_id,  # Store for cross-reference
                "timestamp": datetime.now().isoformat(),
                "pid": os.getpid(),
                "ppid": os.getppid(),
            }

            with open(session_file, "w") as f:
                json.dump(data, f, indent=2)
            wrote_something = True

        if not wrote_something:
            logger.debug("No TTY or instance_id available - cannot write session files")
            return False

        return True
    except Exception as e:
        logger.debug(f"Failed to write session file: {e}")
        return False


def get_claude_session_id() -> str | None:
    """Get the Claude Code session ID for the current terminal.

    Convenience function that reads the TTY session file and returns
    just the claude_session_id.

    Returns:
        Claude Code conversation UUID or None if not available.
    """
    session = get_tty_session()
    return session.get("claude_session_id") if session else None


def validate_tty_session(session: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate a TTY session for staleness and warn if issues detected.

    Checks:
    1. Process still exists (PID that wrote the session)
    2. TTY device still exists (if real TTY, not ppid-based)
    3. Timestamp not too old (default: 4 hours)

    Args:
        session: TTY session dict (if None, reads current TTY session)

    Returns:
        Dict with:
            - valid: bool - True if session appears valid
            - warnings: list[str] - Warning messages if any
            - session: dict - The session data (if valid)
    """
    result: dict[str, Any] = {"valid": True, "warnings": [], "session": None}

    if session is None:
        session = get_tty_session()

    if not session:
        result["valid"] = False
        result["warnings"].append("No TTY session file found")
        return result

    result["session"] = session

    # Note: We don't check if the original PID exists because the hook that writes
    # the TTY session file always exits immediately after writing. The PID check
    # would always warn for valid sessions. TTY device check is the meaningful validation.

    # Check 1: TTY device exists (for real TTYs)
    tty_key = session.get("tty_key", "")
    if tty_key.startswith("pts-"):
        tty_device = f"/dev/{tty_key.replace('-', '/')}"
        if not Path(tty_device).exists():
            result["valid"] = False
            result["warnings"].append(f"TTY device {tty_device} no longer exists - terminal closed?")

    # Note: No timestamp staleness check. The timestamp in the TTY session file
    # is the WRITE time (from session-init), not a last-active time — it's never
    # refreshed during a session's lifetime. A long-running active session
    # would falsely flag stale. TTY device presence (check 1 above) is the
    # authoritative signal: if /dev/pts/X exists, the terminal is alive.

    # Only mark invalid if TTY device is gone (terminal closed)
    # PID being gone is just a warning - project_path can still be valid
    # (e.g., project-switch updates the file after original process exits)
    if any("TTY device" in w and "no longer exists" in w for w in result["warnings"]):
        result["valid"] = False

    return result


# =============================================================================
# Session ID Resolution
# =============================================================================


def resolve_session_id(session_id_or_alias: str, ai_id: str | None = None) -> str:
    """
    Resolve session ID from alias or return original UUID.

    Args:
        session_id_or_alias: UUID (full or partial), "latest", "last", "auto", or compound alias
        ai_id: Optional AI identifier for scoped resolution (used as fallback filter)

    Returns:
        Resolved full UUID

    Raises:
        ValueError: If alias doesn't match any session

    Examples:
        >>> resolve_session_id("88dbf132-cc7c-4a4b-9b59-77df3b13dbd2")
        '88dbf132-cc7c-4a4b-9b59-77df3b13dbd2'

        >>> resolve_session_id("88dbf132")  # Partial UUID
        '88dbf132-cc7c-4a4b-9b59-77df3b13dbd2'

        >>> resolve_session_id("latest")
        '88dbf132-cc7c-4a4b-9b59-77df3b13dbd2'  # Most recent session

        >>> resolve_session_id("latest:active")
        'fc87adfc-...'  # Most recent active session

        >>> resolve_session_id("latest:claude-code")
        '20586d3b-...'  # Most recent claude-code session

        >>> resolve_session_id("latest:active:claude-code")
        '88dbf132-...'  # Most recent active claude-code session
    """
    # Guard against None input
    if not session_id_or_alias:
        raise ValueError("session_id is required (got None or empty)")

    # Check if it's an alias
    if not session_id_or_alias.startswith("latest") and session_id_or_alias not in ("last", "auto"):
        # Regular UUID (partial or full) - resolve via database
        return _resolve_partial_uuid(session_id_or_alias)

    # Parse alias
    alias = session_id_or_alias
    if alias in ("last", "auto"):
        alias = "latest"  # Normalize to "latest"

    parts = alias.split(":")

    # Extract filters from alias parts
    filters = {"active_only": False, "ai_id": None}

    for part in parts[1:]:  # Skip first part ("latest")
        if part == "active":
            filters["active_only"] = True
        else:
            # Assume it's an AI identifier
            filters["ai_id"] = part

    # Use provided ai_id as fallback if no AI specified in alias
    if not filters["ai_id"] and ai_id:
        filters["ai_id"] = ai_id
        logger.debug(f"Using provided ai_id as fallback filter: {ai_id}")

    # Query database
    try:
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()

        # Build query
        query = "SELECT session_id FROM sessions WHERE 1=1"
        params = []

        if filters["active_only"]:
            query += " AND end_time IS NULL"
            logger.debug("Filtering for active sessions only")

        if filters["ai_id"]:
            query += " AND ai_id = ?"
            params.append(filters["ai_id"])
            logger.debug(f"Filtering for ai_id: {filters['ai_id']}")

        # Multi-instance isolation: filter by instance_id
        current_instance_id = get_instance_id()
        if current_instance_id:
            # Match exact instance_id OR sessions without instance_id (legacy)
            query += " AND (instance_id = ? OR instance_id IS NULL)"
            params.append(current_instance_id)
            logger.debug(f"Filtering for instance_id: {current_instance_id}")

        query += " ORDER BY start_time DESC LIMIT 1"

        logger.debug(f"Executing query: {query} with params: {params}")

        cursor = db.conn.cursor()
        cursor.execute(query, params)
        result = cursor.fetchone()

        db.close()

        if result:
            resolved_id = result[0]
            logger.info(f"Resolved alias '{session_id_or_alias}' to session: {resolved_id[:8]}")
            return resolved_id
        else:
            error_msg = f"No session found for alias: {session_id_or_alias}"
            if filters["ai_id"]:
                error_msg += f" (ai_id: {filters['ai_id']})"
            if filters["active_only"]:
                error_msg += " (active only)"
            if current_instance_id:
                error_msg += f" (instance: {current_instance_id})"
            logger.warning(error_msg)
            raise ValueError(error_msg)

    except ImportError as e:
        logger.error(f"Failed to import SessionDatabase: {e}")
        raise ValueError(f"Cannot resolve session alias - database unavailable: {e}") from e


def _resolve_partial_uuid(partial_or_full_uuid: str) -> str:
    """
    Resolve partial UUID (8 chars) to full UUID, or validate full UUID.

    Args:
        partial_or_full_uuid: Partial (8+ chars) or full UUID string

    Returns:
        Full UUID

    Raises:
        ValueError: If UUID not found or ambiguous
    """
    # If it looks like a full UUID (contains hyphens), return as-is
    if "-" in partial_or_full_uuid:
        logger.debug(f"Full UUID provided: {partial_or_full_uuid}")
        return partial_or_full_uuid

    # Partial UUID - query database
    try:
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        cursor = db.conn.cursor()

        # Match beginning of session_id
        cursor.execute(
            "SELECT session_id FROM sessions WHERE session_id LIKE ? ORDER BY start_time DESC",
            (f"{partial_or_full_uuid}%",),
        )

        results = cursor.fetchall()
        db.close()

        if not results:
            raise ValueError(f"No session found matching: {partial_or_full_uuid}")

        if len(results) > 1:
            logger.warning(f"Multiple sessions match '{partial_or_full_uuid}' - using most recent")

        resolved = results[0][0]
        logger.debug(f"Resolved partial UUID '{partial_or_full_uuid}' to {resolved}")
        return resolved

    except ImportError as e:
        logger.error(f"Failed to import SessionDatabase: {e}")
        # Fallback: assume it's a full UUID if it's 36 chars
        if len(partial_or_full_uuid) == 36:
            logger.debug("Database unavailable, assuming full UUID")
            return partial_or_full_uuid
        raise ValueError(f"Cannot resolve partial UUID - database unavailable: {e}") from e


def get_latest_session_id(ai_id: str | None = None, active_only: bool = False) -> str:
    """
    Get the most recent session ID.

    Convenience function equivalent to resolve_session_id("latest:...").

    Args:
        ai_id: Optional AI identifier to filter by
        active_only: If True, only return active (not ended) sessions

    Returns:
        Most recent session UUID

    Raises:
        ValueError: If no session found

    Examples:
        >>> get_latest_session_id()
        '88dbf132-cc7c-4a4b-9b59-77df3b13dbd2'

        >>> get_latest_session_id(ai_id="claude-code")
        '20586d3b-...'

        >>> get_latest_session_id(ai_id="claude-code", active_only=True)
        '88dbf132-...'
    """
    # Build alias string
    alias_parts = ["latest"]

    if active_only:
        alias_parts.append("active")

    if ai_id:
        alias_parts.append(ai_id)

    alias = ":".join(alias_parts)

    return resolve_session_id(alias)


def is_session_alias(session_id_or_alias: str) -> bool:
    """
    Check if string is a session alias (not a UUID).

    Args:
        session_id_or_alias: String to check

    Returns:
        True if it's an alias, False if it's a UUID

    Examples:
        >>> is_session_alias("latest")
        True

        >>> is_session_alias("latest:active:claude-code")
        True

        >>> is_session_alias("88dbf132-cc7c-4a4b-9b59-77df3b13dbd2")
        False
    """
    return session_id_or_alias.startswith("latest") or session_id_or_alias in ("last", "auto")


def get_instance_id() -> str | None:
    """
    Get a unique instance identifier for multi-instance isolation.

    This allows multiple Claude instances to run simultaneously without
    session cross-talk. Each instance gets its own session namespace.

    Priority order:
    1. EMPIRICA_INSTANCE_ID env var (explicit override)
    2. TMUX_PANE (tmux terminal pane ID, e.g., "%0", "%1")
    3. TERM_SESSION_ID (macOS Terminal.app session ID)
    4. WINDOWID (X11 window ID)
    5. TTY device (e.g., pts-6 → term_pts_6) - persists across CLI calls
    6. None (fallback to legacy behavior - first match wins)

    Returns:
        Instance identifier string, or None for legacy behavior

    Examples:
        # In tmux pane %0
        >>> get_instance_id()
        'tmux_0'

        # With explicit env var
        >>> os.environ['EMPIRICA_INSTANCE_ID'] = 'my-instance'
        >>> get_instance_id()
        'my-instance'

        # Outside tmux, no special env
        >>> get_instance_id()
        None
    """
    import os

    # Priority 1: Explicit override (EMPIRICA or CLAUDE)
    # WARNING: This overrides TMUX_PANE. Only use in non-tmux environments (CI, containers).
    # Setting this globally (e.g. in .bashrc) while using tmux will collapse all panes
    # to one instance, breaking pane isolation.
    explicit_id = os.environ.get("EMPIRICA_INSTANCE_ID") or os.environ.get("CLAUDE_INSTANCE_ID")
    if explicit_id:
        tmux_pane = os.environ.get("TMUX_PANE")
        if tmux_pane:
            # Slot-shaped IDs (lowercase alphanumeric + dashes, no special chars)
            # are the cockpit-blessed override pattern — stable per pane across
            # restart. Log at debug, not warning, since this is intentional.
            # Other shapes (e.g. set globally in .bashrc, contains %, ':', etc.)
            # still get the warning because they break per-pane isolation.
            import re as _re

            looks_like_cockpit_slot = bool(_re.fullmatch(r"[a-z][a-z0-9_-]*", explicit_id))
            if looks_like_cockpit_slot:
                logger.debug(
                    f"EMPIRICA_INSTANCE_ID='{explicit_id}' overrides TMUX_PANE='{tmux_pane}' "
                    f"(cockpit-style slot override — intentional)"
                )
            else:
                logger.warning(
                    f"EMPIRICA_INSTANCE_ID='{explicit_id}' overrides TMUX_PANE='{tmux_pane}'. "
                    f"This breaks per-pane isolation. Unset EMPIRICA_INSTANCE_ID if using tmux."
                )
        logger.debug(f"Using explicit instance_id: {explicit_id}")
        return explicit_id

    # Priority 2: tmux pane (most common for multi-instance work)
    # IMPORTANT: Use tmux_{N} format (not tmux:%N) to match file naming convention
    # Files are named: instance_projects/tmux_4.json, active_transaction_tmux_4.json
    tmux_pane = os.environ.get("TMUX_PANE")
    if tmux_pane:
        instance_id = f"tmux_{tmux_pane.lstrip('%')}"
        logger.debug(f"Using tmux pane as instance_id: {instance_id}")
        return instance_id

    # Priority 3: macOS Terminal.app session
    term_session = os.environ.get("TERM_SESSION_ID")
    if term_session:
        # Truncate to reasonable length (full ID is very long)
        instance_id = f"term:{term_session[:16]}"
        logger.debug(f"Using Terminal.app session as instance_id: {instance_id}")
        return instance_id

    # Priority 4: X11 window ID
    window_id = os.environ.get("WINDOWID")
    if window_id:
        instance_id = f"x11:{window_id}"
        logger.debug(f"Using X11 window ID as instance_id: {instance_id}")
        return instance_id

    # Priority 5: TTY device (persists across CLI invocations in same terminal)
    tty_key = get_tty_key()
    if tty_key:
        instance_id = f"term_{tty_key}"
        logger.debug(f"Using TTY as instance_id: {instance_id}")
        return instance_id

    # Priority 6: No isolation (legacy behavior)
    logger.debug("No instance_id available - using legacy behavior")
    return None


def _get_instance_suffix() -> str:
    """Get the instance-specific filename suffix for file-based tracking."""
    instance_id = get_instance_id()
    if instance_id:
        safe = instance_id.replace(":", "_").replace("%", "")
        return f"_{safe}"
    return ""


def get_active_project_path(claude_session_id: str | None = None) -> "str | None":
    """Get the active project path for the current instance.

    CANONICAL function for project resolution. All components should use this
    instead of implementing their own priority chain.

    Priority chain:
    -1. CWD when EMPIRICA_CWD_RELIABLE=true (caller verified CWD is the project root)
    0. instance_projects/{instance_id}.json - AUTHORITATIVE (updated by hooks AND project-switch CLI)
    1. active_work_{claude_session_id}.json - fallback (only hooks can update, not CLI)
    2. active_work.json - generic fallback (written by project-switch and session-init)

    Rationale: instance_projects is updated by BOTH hooks (session-init, post-compact)
    AND the project-switch CLI command. active_work is ONLY updated by hooks (which
    have claude_session_id from stdin). project-switch CLI can't update active_work
    because it doesn't know claude_session_id. Therefore instance_projects is more current.

    The CWD override (priority -1) exists because session-init.py chdir's to the
    resolved project root BEFORE spawning session-create. Without it, session-create
    reads stale instance_projects data from the previous session.

    Args:
        claude_session_id: Optional Claude Code conversation UUID (from hook input)

    Returns:
        Absolute path to the project, or None if cannot be resolved.
        Returns None rather than falling back to CWD to fail explicitly.

    Usage:
        # In hooks (have claude_session_id from stdin):
        project_path = get_active_project_path(claude_session_id=hook_input.get('session_id'))

        # In CLI commands (no claude_session_id):
        project_path = get_active_project_path()
    """
    from pathlib import Path

    # Priority -1: CWD when caller has verified it (session-init sets this after chdir)
    if os.environ.get("EMPIRICA_CWD_RELIABLE", "").lower() == "true":
        cwd = str(Path.cwd())
        if (Path(cwd) / ".empirica" / "project.yaml").exists():
            logger.debug(f"get_active_project_path: from CWD (EMPIRICA_CWD_RELIABLE): {cwd}")
            return cwd

    active_work_path = None
    instance_path = None

    # Read active_work file (if claude_session_id provided)
    if claude_session_id:
        active_work_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
        if active_work_file.exists():
            try:
                with open(active_work_file) as f:
                    data = json.load(f)
                    active_work_path = data.get("project_path")
            except Exception:
                pass

    # Read instance_projects (instance_id-based) - AUTHORITATIVE source
    instance_id = get_instance_id()
    if instance_id:
        instance_file = Path.home() / ".empirica" / "instance_projects" / f"{instance_id}.json"
        if instance_file.exists():
            try:
                with open(instance_file) as f:
                    data = json.load(f)
                    instance_path = data.get("project_path")
            except Exception:
                pass

    # PRIORITY 0: instance_projects wins (updated by BOTH hooks AND project-switch CLI)
    # active_work is only updated by hooks (which have claude_session_id).
    # project-switch CLI updates instance_projects but CAN'T update active_work
    # (doesn't know claude_session_id). So instance_projects is more current.
    if instance_path:
        logger.debug(f"get_active_project_path: from instance_projects: {instance_path}")
        return instance_path

    # Fallback: active_work_{claude_session_id} (for non-TMUX environments where instance_id is None)
    if active_work_path:
        logger.debug(f"get_active_project_path: from active_work_{claude_session_id}: {active_work_path}")
        return active_work_path

    # Priority 2: Generic active_work.json — HEADLESS MODE ONLY
    # In interactive mode (terminal exists), instance_projects + active_work_{uuid}
    # handle everything. The generic file would only cause pollution by returning
    # a stale project from a different terminal/session.
    # In headless mode (containers, CI), there's no terminal identity — the generic
    # file IS the primary source.
    if is_headless():
        generic_work_file = Path.home() / ".empirica" / "active_work.json"
        if generic_work_file.exists():
            try:
                with open(generic_work_file) as f:
                    data = json.load(f)
                    generic_path = data.get("project_path")
                if generic_path:
                    logger.debug(f"get_active_project_path: from active_work.json (headless): {generic_path}")
                    return generic_path
            except Exception:
                pass

    # NO CWD FALLBACK - fail explicitly
    logger.debug(
        "get_active_project_path: could not resolve (no instance_projects, no active_work_{id}%s)"
        % (", headless=no active_work.json" if is_headless() else "")
    )
    return None


def write_active_transaction(
    transaction_id: str,
    session_id: str | None = None,
    preflight_timestamp: float | None = None,
    status: str = "open",
    project_path: str | None = None,
    claude_session_id: str | None = None,
) -> None:
    """Atomically write the active transaction state to JSON file.

    This file is read by Sentinel to track transaction state across sessions.
    Transactions survive compaction - the session_id here is the one that
    opened the transaction, which may differ from the current session.

    IMPORTANT: Uses instance suffix for multi-instance isolation. Each Claude
    instance writes to its own transaction file (e.g., active_transaction_pts-6.json).

    Dual-key (B3): the file also carries ``claude_session_id`` — the DURABLE
    practitioner key. The empirica ``session_id`` rotates per compact window, so
    resolving a transaction by it breaks across compaction; ``claude_session_id``
    survives. ``_find_transaction_file`` prefers the claude_session_id match.
    When ``claude_session_id`` is None on a status-update write (e.g. POSTFLIGHT),
    the existing field is preserved so the durable key isn't dropped mid-cutover.

    Args:
        transaction_id: UUID of the epistemic transaction
        session_id: Session that opened this transaction (for PREFLIGHT lookup)
        preflight_timestamp: When PREFLIGHT was submitted
        status: "open" or "closed"
        project_path: Absolute path to the project (git root) this transaction belongs to
        claude_session_id: Durable practitioner key (survives compaction)
    """
    import os
    import tempfile
    import time

    # Use instance-aware filename for multi-instance isolation
    suffix = _get_instance_suffix()
    from pathlib import Path

    # Determine project_path if not provided (use CWD's git root or CWD itself)
    if not project_path:
        local_empirica = Path.cwd() / ".empirica"
        if local_empirica.exists():
            project_path = str(Path.cwd())

    # Write transaction file to the project's .empirica directory
    if project_path:
        path = Path(project_path) / ".empirica" / f"active_transaction{suffix}.json"
    else:
        path = Path.home() / ".empirica" / f"active_transaction{suffix}.json"

    path.parent.mkdir(parents=True, exist_ok=True)

    # Preserve an existing claude_session_id when this write doesn't supply one
    # (status-update writes like POSTFLIGHT mustn't drop the durable key).
    if claude_session_id is None and path.exists():
        try:
            with open(path) as existing_f:
                claude_session_id = json.load(existing_f).get("claude_session_id")
        except Exception:
            pass

    tx_data = {
        "transaction_id": transaction_id,
        "session_id": session_id,
        "claude_session_id": claude_session_id,  # durable key (B3 dual-key)
        "preflight_timestamp": preflight_timestamp or time.time(),
        "status": status,
        "project_path": project_path,  # Essential for cross-CWD operations
        "updated_at": time.time(),
    }

    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(tmp_fd, "w") as tmp_f:
            json.dump(tx_data, tmp_f, indent=2)
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def increment_transaction_tool_count(claude_session_id: str | None = None) -> dict | None:
    """Atomically increment tool_call_count in the active transaction file.

    Called by Sentinel on every PreToolUse (both noetic and praxic) to track
    how much work has been done in this transaction. Returns the updated
    transaction data including current count and avg_turns for nudge calculation.

    Returns None if no active transaction exists.
    """
    import tempfile
    from pathlib import Path

    suffix = _get_instance_suffix()

    # Resolve the empirica session_id for the suffix-mismatch fallback scan.
    session_id = None
    if claude_session_id:
        try:
            aw_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
            if aw_file.exists():
                with open(aw_file) as f:
                    session_id = json.load(f).get("empirica_session_id")
        except Exception:
            pass

    # Find the transaction file with dual-key resilience (durable
    # claude_session_id preferred, so the counter survives suffix rotation).
    project_path = get_active_project_path(claude_session_id)
    tx_path = None
    if project_path:
        tx_path = _find_transaction_file(Path(project_path) / ".empirica", suffix, session_id, claude_session_id)
    if tx_path is None:
        tx_path = _find_transaction_file(Path.home() / ".empirica", suffix, session_id, claude_session_id)

    if tx_path is None or not tx_path.exists():
        return None

    try:
        with open(tx_path) as f:
            tx_data = json.load(f)

        if tx_data.get("status") != "open":
            return None

        # Increment count
        tx_data["tool_call_count"] = tx_data.get("tool_call_count", 0) + 1
        tx_data["updated_at"] = __import__("time").time()

        # Atomic write
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(tx_path.parent))
        try:
            with __import__("os").fdopen(tmp_fd, "w") as tmp_f:
                json.dump(tx_data, tmp_f, indent=2)
            __import__("os").replace(tmp_path, str(tx_path))
        except BaseException:
            try:
                __import__("os").unlink(tmp_path)
            except OSError:
                pass
            raise

        return tx_data
    except Exception:
        return None


def _find_transaction_file(
    empirica_dir: "Path",
    suffix: str,
    session_id: str | None = None,
    claude_session_id: str | None = None,
) -> "Path | None":
    """Find the active transaction file, with suffix-mismatch fallback.

    Primary: Look for the exact file matching the current instance suffix.
    Fallback: When the exact file doesn't exist (e.g., hook context where
    TMUX_PANE is not inherited, OR the ephemeral tmux_N rotated across
    compaction), scan for any active_transaction_*.json matching — preferring
    the DURABLE claude_session_id, then the (rotating) empirica session_id.

    This handles the environment-mismatch scenario where:
    - CLI writes active_transaction_tmux_5.json (TMUX_PANE available)
    - Hook looks for active_transaction.json (no TMUX_PANE in hook context)

    And the compaction scenario (B3) where:
    - The empirica session_id rotated, so a session_id scan no longer matches,
      but claude_session_id (the practitioner key) is stable across the rotation.

    The scan is safe because it's scoped by a durable/session key — it won't
    cross-talk between instances. With neither key, only the exact suffix match
    is attempted (no scan).

    See: docs/architecture/instance_isolation/KNOWN_ISSUES.md (11.21)

    Args:
        empirica_dir: The .empirica directory to search in
        suffix: The instance suffix from _get_instance_suffix()
        session_id: Optional empirica session_id to match (rotates on compaction)
        claude_session_id: Optional durable practitioner key (preferred match)

    Returns:
        Path to the transaction file, or None
    """
    # Primary: exact suffix match
    exact = empirica_dir / f"active_transaction{suffix}.json"
    if exact.exists():
        return exact

    # Fallback: scan for suffix-mismatched files. Rank candidates and return the
    # best rather than the first sorted match — claude_session_id is stable
    # across the whole CC session, so many files (one per past transaction, each
    # POSTFLIGHT-closed) share it; returning the first would resolve a STALE
    # CLOSED transaction and make the firewall block praxic after a valid CHECK.
    # Rank by (cc_match, is_open, updated_at) descending: prefer the durable
    # claude_session_id over the (rotating) empirica session_id, an OPEN
    # transaction over a closed one, and the most recent over the old. Either
    # key scopes the scan to prevent cross-instance cross-talk.
    if not claude_session_id and not session_id:
        return None
    best_rank: tuple | None = None
    best_file: Path | None = None
    try:
        for tx_file in sorted(empirica_dir.glob("active_transaction*.json")):
            try:
                with open(tx_file) as f:
                    tx_data = json.load(f)
            except Exception:
                continue
            cc_match = bool(claude_session_id and tx_data.get("claude_session_id") == claude_session_id)
            sess_match = bool(session_id and tx_data.get("session_id") == session_id)
            if not (cc_match or sess_match):
                continue
            rank = (cc_match, tx_data.get("status") == "open", tx_data.get("updated_at") or 0.0)
            if best_rank is None or rank > best_rank:
                best_rank, best_file = rank, tx_file
    except Exception:
        return None
    if best_file is not None:
        logger.debug(
            f"Transaction resolved by scan: expected suffix '{suffix}', "
            f"found '{best_file.name}' (rank cc/open/recent={best_rank})"
        )
    return best_file


def read_active_transaction_full(claude_session_id: str | None = None) -> dict | None:
    """Read the full active transaction data from the tracking file.

    Returns the complete transaction dict including:
    - transaction_id: The transaction UUID
    - session_id: The session where PREFLIGHT was run (CRITICAL for cross-compact continuity)
    - preflight_timestamp: When PREFLIGHT was submitted
    - status: "open" or "closed"
    - project_path: Project this transaction belongs to

    Uses get_active_project_path() to find the correct project, then reads transaction from there.
    Uses _find_transaction_file() for suffix-mismatch resilience (see KNOWN_ISSUES 11.21).
    """
    from pathlib import Path

    suffix = _get_instance_suffix()

    # Self-source the durable practitioner key when the caller didn't pass one.
    # CLI verbs (check-submit, postflight, status) call R.transaction_id() /
    # transaction_read() with no args, leaving claude_session_id=None — which
    # skips the suffix-mismatch fallback below, so the active transaction is lost
    # across a compaction that rotated the instance suffix (CHECK then stores
    # UNBOUND → firewall blocks praxic despite an OPEN transaction). The
    # tty-anchored session file is stable across compaction (same terminal), so
    # get_claude_session_id() recovers the key. Exact-suffix primary is unchanged
    # (it runs first in _find_transaction_file), so the common case never regresses.
    if claude_session_id is None:
        claude_session_id = get_claude_session_id()

    # Resolve session_id for fallback scanning
    session_id = None
    if claude_session_id:
        try:
            aw_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
            if aw_file.exists():
                with open(aw_file) as f:
                    session_id = json.load(f).get("empirica_session_id")
        except Exception:
            pass

    # Use canonical project resolution
    project_path = get_active_project_path(claude_session_id)
    if project_path:
        empirica_dir = Path(project_path) / ".empirica"
        tx_file = _find_transaction_file(empirica_dir, suffix, session_id, claude_session_id)
        if tx_file:
            try:
                with open(tx_file) as f:
                    return json.load(f)
            except Exception:
                pass

    # Fallback: Global ~/.empirica/
    global_dir = Path.home() / ".empirica"
    tx_file = _find_transaction_file(global_dir, suffix, session_id, claude_session_id)
    if tx_file:
        try:
            with open(tx_file) as f:
                return json.load(f)
        except Exception:
            pass

    return None


def read_active_transaction(claude_session_id: str | None = None) -> str | None:
    """Read the active transaction ID from the tracking file. Returns None if no active transaction.

    For full transaction data including session_id, use read_active_transaction_full().
    """
    data = read_active_transaction_full(claude_session_id)
    if data:
        return data.get("transaction_id")
    return None


def set_active_engagement(engagement_id: str, claude_session_id: str | None = None) -> bool:
    """Set the active engagement on the current transaction file.

    When set, artifact logging (finding-log, decision-log, etc.) auto-inherits
    this engagement as entity context. Cleared on transaction close.

    Returns True if set, False if no active transaction.
    """
    import os
    import tempfile
    from pathlib import Path

    suffix = _get_instance_suffix()

    project_path = get_active_project_path(claude_session_id)
    if project_path:
        tx_path = Path(project_path) / ".empirica" / f"active_transaction{suffix}.json"
    else:
        tx_path = Path.home() / ".empirica" / f"active_transaction{suffix}.json"

    if not tx_path.exists():
        return False

    try:
        with open(tx_path) as f:
            tx_data = json.load(f)

        if tx_data.get("status") != "open":
            return False

        tx_data["active_engagement"] = engagement_id
        tx_data["updated_at"] = __import__("time").time()

        # Atomic write
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(tx_path.parent))
        try:
            with os.fdopen(tmp_fd, "w") as tmp_f:
                json.dump(tx_data, tmp_f, indent=2)
            os.replace(tmp_path, str(tx_path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        return True
    except Exception:
        return False


def get_active_engagement(claude_session_id: str | None = None) -> str | None:
    """Read active_engagement from the current transaction file.

    Returns engagement ID or None if no engagement is focused.
    """
    data = read_active_transaction_full(claude_session_id)
    if data:
        return data.get("active_engagement")
    return None


def _validate_session_in_db(session_id: str, project_path: str | None = None) -> bool:
    """Check if a session_id exists in the sessions table.

    Prevents stale session IDs (surviving compaction) from propagating
    through the resolution chain. Without this, post-compact hooks can
    write a pre-compact session_id that doesn't exist in the current DB.

    Args:
        session_id: Empirica session UUID to validate
        project_path: Project path to locate the correct sessions.db.
            If provided, uses the project-local DB directly instead of
            SessionDatabase() default resolution (which may point to
            a different project's DB in multi-project setups).

    Returns:
        True if session exists in sessions table, False otherwise.
    """
    if not session_id:
        return False
    try:
        import sqlite3
        from pathlib import Path

        # Use project-local DB when project_path is known
        if project_path:
            db_path = Path(project_path) / ".empirica" / "sessions" / "sessions.db"
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,))
                row = cursor.fetchone()
                conn.close()
                found = row is not None
                if not found:
                    logger.warning(
                        f"_validate_session_in_db: session {session_id[:8]}... NOT FOUND in project-local DB: {db_path}"
                    )
                    # Diagnostic: list recent sessions in this DB
                    try:
                        conn2 = sqlite3.connect(str(db_path))
                        c2 = conn2.cursor()
                        c2.execute("SELECT session_id, ai_id FROM sessions ORDER BY start_time DESC LIMIT 5")
                        recent = c2.fetchall()
                        conn2.close()
                        logger.warning(f"  Recent sessions in DB: {[(r[0][:8], r[1]) for r in recent]}")
                    except Exception:
                        pass
                return found
            else:
                logger.warning(f"_validate_session_in_db: DB does not exist at {db_path}")
            # DB doesn't exist at project path — fall through to default

        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        logger.debug(
            f"_validate_session_in_db: fallback to SessionDatabase default, db_path={getattr(db, 'db_path', '?')}"
        )
        cursor = db.conn.cursor()
        cursor.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        db.close()
        return row is not None
    except Exception as e:
        logger.debug(f"_validate_session_in_db: DB check failed ({e}), allowing session")
        return True  # Fail open — don't block if DB is unavailable


def _find_session_for_project(project_path: str) -> str | None:
    """Find the latest valid session_id for a project path.

    Fallback when the resolved session_id is stale (not in sessions table).
    Queries for the most recent active session matching the project.

    Uses the project-local sessions.db directly when available, avoiding
    SessionDatabase() default resolution which may point to a different DB.

    Args:
        project_path: Filesystem path to the project

    Returns:
        Valid session_id, or None if no matching session found.
    """
    if not project_path:
        return None
    try:
        import sqlite3
        from pathlib import Path

        folder_name = Path(project_path).name

        # Use project-local DB directly
        db_path = Path(project_path) / ".empirica" / "sessions" / "sessions.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
        else:
            # Fallback to SessionDatabase default resolution
            from empirica.data.session_database import SessionDatabase

            db = SessionDatabase()
            conn = db.conn
            cursor = conn.cursor()

        # Look up project_id from projects table (columns: id, name)
        cursor.execute("SELECT id FROM projects WHERE name = ?", (folder_name,))
        row = cursor.fetchone()

        if not row:
            # Fallback: check workspace.db for trajectory_path mapping
            import sqlite3

            workspace_db = Path.home() / ".empirica" / "workspace" / "workspace.db"
            if workspace_db.exists():
                wconn = sqlite3.connect(str(workspace_db))
                wcursor = wconn.cursor()
                wcursor.execute(
                    "SELECT id FROM global_projects WHERE trajectory_path LIKE ? AND status = 'active'",
                    (f"%/{folder_name}%",),
                )
                wrow = wcursor.fetchone()
                wconn.close()
                if wrow:
                    row = wrow

        if not row:
            conn.close()
            return None

        project_id = row[0]

        # Find latest session for this project
        cursor.execute(
            "SELECT session_id FROM sessions WHERE project_id = ? ORDER BY start_time DESC LIMIT 1", (project_id,)
        )
        session_row = cursor.fetchone()
        conn.close()

        if session_row:
            logger.info(
                f"_find_session_for_project: resolved stale session to {session_row[0][:8]}... via project {folder_name}"
            )
            return session_row[0]
        return None
    except Exception as e:
        logger.debug(f"_find_session_for_project: failed ({e})")
        return None


def _try_session_source(data: dict, source_name: str, project_path_hint: str | None = None) -> tuple:
    """Try to validate a session_id from a data source. Returns (session_id_or_None, project_path_or_None)."""
    session_id = data.get("empirica_session_id")
    project_path = data.get("project_path") or project_path_hint
    if session_id:
        if _validate_session_in_db(session_id, project_path=project_path):
            logger.debug(f"get_active_empirica_session_id: from {source_name}: {session_id[:8]}...")
            return session_id, project_path
        else:
            logger.warning(f"get_active_empirica_session_id: stale session in {source_name}: {session_id[:8]}...")
    return None, project_path


def _collect_session_sources(claude_session_id: str | None) -> list[tuple]:
    """Build ordered list of (data_dict, source_name) for session resolution."""
    from pathlib import Path

    sources = []
    if claude_session_id:
        aw_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
        if aw_file.exists():
            data = _read_json_file_safe(aw_file)
            if data:
                sources.append((data, "active_work"))
    instance_id = get_instance_id()
    if instance_id:
        inst_file = Path.home() / ".empirica" / "instance_projects" / f"{instance_id}.json"
        if inst_file.exists():
            data = _read_json_file_safe(inst_file)
            if data:
                sources.append((data, "instance_projects"))
    tty_session = get_tty_session()
    if tty_session:
        sources.append((tty_session, "tty_session"))
    if is_headless():
        generic_work = Path.home() / ".empirica" / "active_work.json"
        if generic_work.exists():
            data = _read_json_file_safe(generic_work)
            if data:
                sources.append((data, "active_work.json (headless)"))
    return sources


def get_active_empirica_session_id(claude_session_id: str | None = None) -> str | None:
    """Get the active Empirica session ID for CLI commands.

    CANONICAL function for session_id resolution. CLI commands should use this
    instead of implementing their own transaction-first logic.

    Priority chain:
    1. Active transaction (TRANSACTION-FIRST - transaction survives compaction)
    2. active_work_{claude_session_id}.json (session-init writes this)
    3. instance_projects file (TMUX-based fallback)
    4. TTY session (written by session-create, project-switch)
    5. Generic active_work.json (written by project-switch, session-init)

    Each resolved session_id is validated against the sessions table.
    If stale (post-compaction mismatch), falls back to finding the latest
    session for the same project.

    Args:
        claude_session_id: Optional Claude Code conversation UUID (from hook input)

    Returns:
        Empirica session UUID, or None if no active session found.

    Usage:
        session_id = get_active_empirica_session_id()
        if not session_id:
            print("No active transaction - run PREFLIGHT first")
            return
    """
    project_path_for_fallback = None

    # Priority 1: Active transaction (AUTHORITATIVE during transaction)
    tx_data = read_active_transaction_full(claude_session_id)
    if tx_data and tx_data.get("status") == "open":
        session_id = tx_data.get("session_id")
        tx_project_path = tx_data.get("project_path")
        if tx_project_path:
            project_path_for_fallback = tx_project_path
        if session_id:
            if _validate_session_in_db(session_id, project_path=tx_project_path):
                logger.debug(f"get_active_empirica_session_id: from transaction: {session_id[:8]}...")
                return session_id
            else:
                logger.warning(f"get_active_empirica_session_id: stale session in transaction: {session_id[:8]}...")

    # Priorities 2-5: file-based sources
    for data, source_name in _collect_session_sources(claude_session_id):
        sid, pp = _try_session_source(data, source_name, project_path_for_fallback)
        if pp and not project_path_for_fallback:
            project_path_for_fallback = pp
        if sid:
            return sid

    # Fallback: all sources returned stale session_ids — try to find valid session for project
    if project_path_for_fallback:
        fallback_session = _find_session_for_project(project_path_for_fallback)
        if fallback_session:
            logger.info(f"get_active_empirica_session_id: recovered via project fallback: {fallback_session[:8]}...")
            return fallback_session

    logger.debug("get_active_empirica_session_id: no active session found")
    return None


def clear_active_transaction(claude_session_id: str | None = None) -> None:
    """Remove the active transaction tracking file (called on POSTFLIGHT).

    Uses get_active_project_path() to find correct project, NOT CWD.
    """
    from pathlib import Path

    suffix = _get_instance_suffix()

    # Use canonical project resolution
    project_path = get_active_project_path(claude_session_id)
    if project_path:
        candidate = Path(project_path) / ".empirica" / f"active_transaction{suffix}.json"
        if candidate.exists():
            try:
                candidate.unlink()
                return  # Found and cleared
            except Exception:
                pass

    # Priority 2: Global fallback
    global_candidate = Path.home() / ".empirica" / f"active_transaction{suffix}.json"
    if global_candidate.exists():
        try:
            global_candidate.unlink()
        except Exception:
            pass


def _hook_counters_path(project_path: str | None = None, suffix: str | None = None) -> "Path":
    """Compute the path to the hook counters file.

    Co-located with the transaction file — same directory, same suffix.
    Hooks write counters here; POSTFLIGHT reads then deletes.
    """
    from pathlib import Path

    if suffix is None:
        suffix = _get_instance_suffix()
    if project_path:
        return Path(project_path) / ".empirica" / f"hook_counters{suffix}.json"
    return Path.home() / ".empirica" / f"hook_counters{suffix}.json"


def read_hook_counters(claude_session_id: str | None = None) -> dict | None:
    """Read the hook counters file. Returns None if it doesn't exist."""
    project_path = get_active_project_path(claude_session_id)
    path = _hook_counters_path(project_path)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def write_hook_counters(data: dict, claude_session_id: str | None = None) -> bool:
    """Atomically write the hook counters file.

    Called by hooks (sentinel, context-shift-tracker, subagent-stop).
    """
    import os
    import tempfile

    project_path = get_active_project_path(claude_session_id)
    path = _hook_counters_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(tmp_fd, "w") as tmp_f:
            json.dump(data, tmp_f, indent=2)
        os.rename(tmp_path, str(path))
        return True
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False


def clear_hook_counters(claude_session_id: str | None = None) -> None:
    """Delete the hook counters file (called by POSTFLIGHT after reading)."""
    project_path = get_active_project_path(claude_session_id)
    path = _hook_counters_path(project_path)
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass


def cleanup_stale_instance_projects() -> int:
    """Remove instance_projects entries for tmux panes that no longer exist.

    Tmux pane IDs are monotonic — once a pane is destroyed, its ID is never
    reused. This detects dead panes and removes their stale mapping files.

    Returns number of files removed.
    """
    import subprocess
    from pathlib import Path

    instance_dir = Path.home() / ".empirica" / "instance_projects"
    if not instance_dir.exists():
        return 0

    # Get current tmux pane IDs
    live_panes = set()
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_id}"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                pane_id = line.strip().lstrip("%")
                if pane_id:
                    live_panes.add(f"tmux_{pane_id}")
    except Exception:
        return 0  # Can't verify — don't remove anything

    if not live_panes:
        return 0  # No tmux running or no panes found — bail

    removed = 0
    for instance_file in instance_dir.glob("tmux_*.json"):
        instance_id = instance_file.stem  # e.g., "tmux_25"
        if instance_id not in live_panes:
            try:
                instance_file.unlink()
                removed += 1
                logger.debug(f"Removed stale instance_projects: {instance_file.name}")
            except Exception:
                pass

    # Also clean up stale compact_handoff files for dead panes
    for handoff_file in (Path.home() / ".empirica").glob("compact_handoff_tmux_*.json"):
        handoff_instance = handoff_file.stem.replace("compact_handoff_", "")
        if handoff_instance not in live_panes:
            try:
                handoff_file.unlink()
                removed += 1
                logger.debug(f"Removed stale compact_handoff: {handoff_file.name}")
            except Exception:
                pass

    return removed


def _collect_open_transaction_sessions(marker_dir: "Path") -> set:
    """Scan marker_dir and project directories for open transaction session_ids.

    These sessions are load-bearing even if the session itself has ended
    (compaction carry-forward scenario).
    """
    from pathlib import Path

    open_tx_sessions: set[str] = set()

    def _extract_open_sid(tx_file: Path) -> None:
        try:
            with open(tx_file) as f:
                tx_data = json.load(f)
            if tx_data.get("status") == "open":
                sid = tx_data.get("session_id")
                if sid:
                    open_tx_sessions.add(sid)
        except Exception:
            pass

    for tx_file in marker_dir.glob("**/active_transaction_*.json"):
        _extract_open_sid(tx_file)

    instance_dir = marker_dir / "instance_projects"
    if instance_dir.exists():
        for ip_file in instance_dir.glob("*.json"):
            try:
                with open(ip_file) as f:
                    pp = json.load(f).get("project_path")
                if pp:
                    for tx_file in Path(pp).glob(".empirica/active_transaction_*.json"):
                        _extract_open_sid(tx_file)
            except Exception:
                continue

    return open_tx_sessions


def _is_session_ended_in_db(
    session_id: str,
    project_path: "str | None",
    open_tx_sessions: set,
    marker_dir: "Path",
) -> bool:
    """Check if a session has end_time set in the DB.

    Returns False (keep the file) when the session can't be verified.
    """
    import sqlite3
    from pathlib import Path

    if not session_id:
        return False
    if session_id in open_tx_sessions:
        return False  # Open transaction keeps it alive

    db_candidates = []
    if project_path:
        db_candidates.append(Path(project_path) / ".empirica" / "sessions" / "sessions.db")
    db_candidates.append(marker_dir / "sessions" / "sessions.db")

    for db_path in db_candidates:
        if not db_path.exists():
            continue
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute("SELECT end_time FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            conn.close()
            if row is None:
                return True  # Session not in DB at all — orphan
            return row[0] is not None  # end_time IS NOT NULL means ended
        except Exception:
            continue
    return False  # Can't check — keep it safe


def _clean_active_work_files(
    marker_dir: "Path",
    current_claude_session_id: "str | None",
    open_tx_sessions: set,
) -> int:
    """Remove active_work_{uuid}.json files for ended sessions."""
    removed = 0
    for aw_file in marker_dir.glob("active_work_*.json"):
        claude_sid = aw_file.stem.replace("active_work_", "")
        if claude_sid == current_claude_session_id:
            continue  # Never delete current conversation

        try:
            with open(aw_file) as f:
                data = json.load(f)
            session_id = data.get("empirica_session_id")
            project_path = data.get("project_path")

            if _is_session_ended_in_db(session_id, project_path, open_tx_sessions, marker_dir):
                aw_file.unlink()
                removed += 1
                logger.debug(
                    f"Removed stale active_work: {aw_file.name} (session {session_id[:8] if session_id else '?'} ended)"
                )
        except Exception:
            continue
    return removed


def _clean_non_tmux_instance_files(
    instance_dir: "Path",
    open_tx_sessions: set,
    marker_dir: "Path",
) -> int:
    """Remove non-tmux instance_projects files for ended sessions."""
    if not instance_dir.exists():
        return 0

    removed = 0
    for ip_file in instance_dir.glob("*.json"):
        if ip_file.stem.startswith("tmux_"):
            continue  # Tmux cleanup handled by cleanup_stale_instance_projects()

        try:
            with open(ip_file) as f:
                data = json.load(f)
            session_id = data.get("empirica_session_id")
            project_path = data.get("project_path")

            inst_id = get_instance_id()
            if inst_id and ip_file.stem == inst_id:
                continue

            if _is_session_ended_in_db(session_id, project_path, open_tx_sessions, marker_dir):
                ip_file.unlink()
                removed += 1
                logger.debug(
                    f"Removed stale instance_projects: {ip_file.name} (session {session_id[:8] if session_id else '?'} ended)"
                )
        except Exception:
            continue
    return removed


def _clean_active_session_files(
    marker_dir: "Path",
    open_tx_sessions: set,
) -> int:
    """Remove stale active_session files for ended sessions."""
    removed = 0
    for as_file in marker_dir.glob("active_session_*"):
        try:
            with open(as_file) as f:
                data = json.load(f)
            session_id = data.get("session_id")
            project_path = data.get("project_path")

            suffix = _get_instance_suffix()
            if suffix and as_file.name == f"active_session{suffix}":
                continue

            if _is_session_ended_in_db(session_id, project_path, open_tx_sessions, marker_dir):
                as_file.unlink()
                removed += 1
                logger.debug(f"Removed stale active_session: {as_file.name}")
        except Exception:
            continue
    return removed


def cleanup_stale_active_work_files(current_claude_session_id: str | None = None) -> int:
    """Remove active_work_{uuid}.json files for ended sessions.

    Runs at session-init startup. Checks the DB for each file's session:
    - Session has end_time (ended) → candidate for removal
    - BUT if an open transaction references that session → keep it (compaction carry-forward)
    - Never delete the current conversation's file (current_claude_session_id)

    Also cleans up non-tmux instance_projects files (x11:*, term_*) using the
    same DB-based check. Tmux cleanup is handled separately by
    cleanup_stale_instance_projects() which uses tmux list-panes.

    Returns number of files removed.
    """
    from pathlib import Path

    marker_dir = Path.home() / ".empirica"
    instance_dir = marker_dir / "instance_projects"

    open_tx_sessions = _collect_open_transaction_sessions(marker_dir)

    removed = _clean_active_work_files(marker_dir, current_claude_session_id, open_tx_sessions)
    removed += _clean_non_tmux_instance_files(instance_dir, open_tx_sessions, marker_dir)
    removed += _clean_active_session_files(marker_dir, open_tx_sessions)

    return removed


# ============================================================================
# Unified Context Resolver
# ============================================================================


def _read_json_file_safe(path) -> dict | None:
    """Read a JSON file, returning None on any error."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _supplement_context(context: dict, data: dict, include_claude_session: bool = False) -> None:
    """Fill missing context fields from a data source dict (non-destructive)."""
    if not context["project_path"]:
        context["project_path"] = data.get("project_path")
    if not context["empirica_session_id"]:
        context["empirica_session_id"] = data.get("empirica_session_id")
    if include_claude_session and not context["claude_session_id"]:
        context["claude_session_id"] = data.get("claude_session_id")


def get_active_context(claude_session_id: str | None = None) -> dict:
    """Get the complete active epistemic context.

    CANONICAL function for getting the full context. All components should
    use this instead of reading individual files.

    Returns a dict with:
        - claude_session_id: Claude Code conversation UUID (if available)
        - empirica_session_id: Empirica session UUID
        - transaction_id: Active transaction UUID (if in a transaction)
        - project_path: Project directory path
        - instance_id: Instance identifier (TMUX_PANE, etc.)

    Priority chain for resolution (matches ARCHITECTURE.md):
    0. Active transaction file (survives compaction — authoritative during transaction)
    1. instance_projects/{instance_id}.json (updated by hooks AND project-switch)
    2. active_work_{claude_session_id}.json (updated by hooks only)
    3. TTY session file (fallback)

    Args:
        claude_session_id: Optional Claude Code conversation UUID (from hook input)

    Returns:
        Dict with context fields. Missing fields are None, not absent.
    """
    from pathlib import Path

    context = {
        "claude_session_id": claude_session_id,
        "empirica_session_id": None,
        "transaction_id": None,
        "project_path": None,
        "instance_id": get_instance_id(),
    }

    # Priority 0: Active transaction file (AUTHORITATIVE during transaction)
    # Transaction files survive compaction and contain the correct project_path,
    # session_id, and transaction_id. This MUST be checked first because after
    # compact, active_work and instance_projects may reference stale data or
    # a new claude_session_id that doesn't match the pre-compact files.
    tx_data = read_active_transaction_full(claude_session_id)
    if tx_data and tx_data.get("status") == "open":
        tx_project = tx_data.get("project_path")
        tx_session = tx_data.get("session_id")
        tx_id = tx_data.get("transaction_id")
        if tx_project:
            context["project_path"] = tx_project
        if tx_session:
            context["empirica_session_id"] = tx_session
        if tx_id:
            context["transaction_id"] = tx_id
        logger.debug("get_active_context: from transaction file (P0)")

    # Priority 1: Instance projects (updated by hooks AND project-switch CLI)
    if context["instance_id"] and (not context["empirica_session_id"] or not context["project_path"]):
        instance_file = Path.home() / ".empirica" / "instance_projects" / f"{context['instance_id']}.json"
        if instance_file.exists():
            data = _read_json_file_safe(instance_file)
            if data:
                _supplement_context(context, data, include_claude_session=True)
                logger.debug("get_active_context: supplemented from instance_projects (P1)")

    # Priority 2: Active work file by Claude session_id
    if claude_session_id and (not context["empirica_session_id"] or not context["project_path"]):
        active_work_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
        if active_work_file.exists():
            data = _read_json_file_safe(active_work_file)
            if data:
                _supplement_context(context, data)
                logger.debug("get_active_context: supplemented from active_work (P2)")

    # Priority 3: TTY session (fallback - may be stale after project-switch)
    if not context["empirica_session_id"] or not context["project_path"]:
        tty_session = get_tty_session(warn_if_stale=False)
        if tty_session:
            _supplement_context(context, tty_session, include_claude_session=True)

    return context


def update_active_context(
    claude_session_id: str, empirica_session_id: str | None = None, project_path: str | None = None, **extra_fields
) -> bool:
    """Update the active_work file with new context values.

    CANONICAL function for updating context. PREFLIGHT, project-switch,
    and other state-changing operations should use this.

    Only updates fields that are provided (non-None). Existing values
    are preserved for fields not specified.

    Args:
        claude_session_id: Claude Code conversation UUID (required)
        empirica_session_id: New Empirica session UUID
        project_path: New project directory path
        **extra_fields: Additional fields to store

    Returns:
        True if successfully updated, False otherwise.
    """
    import time
    from pathlib import Path

    if not claude_session_id:
        logger.warning("update_active_context: claude_session_id required")
        return False

    active_work_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
    active_work_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Load existing data
        data = {}
        if active_work_file.exists():
            try:
                with open(active_work_file) as f:
                    data = json.load(f)
            except Exception:
                pass

        # Update with new values (only if provided)
        if empirica_session_id is not None:
            data["empirica_session_id"] = empirica_session_id
        if project_path is not None:
            data["project_path"] = project_path

        # Add extra fields
        for key, value in extra_fields.items():
            if value is not None:
                data[key] = value

        # Always update timestamp
        data["updated_at"] = time.time()

        # Atomic write
        with open(active_work_file, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(active_work_file, 0o600)

        logger.debug(f"update_active_context: updated {active_work_file.name}")
        return True

    except Exception as e:
        logger.warning(f"update_active_context failed: {e}")
        return False


# ============================================================================
# Project Identifier Resolution
# ============================================================================


def _is_uuid_format(value: str) -> bool:
    """Check if a string looks like a UUID (8-4-4-4-12 hex format)."""
    import re

    if not value or not isinstance(value, str):
        return False
    uuid_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    return bool(re.match(uuid_pattern, value.lower()))


def resolve_project_identifier(identifier: str) -> dict | None:
    """Resolve project identifier to canonical project info.

    CANONICAL function for project resolution. All CLI commands that accept
    --project-id should use this to normalize the input.

    Accepts:
        - UUID: e.g., "748a81a2-ac14-45b8-a185-994997b76828"
        - Folder name: e.g., "empirica", "my-project"
        - Path: e.g., "/path/to/projects/empirica"

    Resolution priority:
        1. If UUID format: validate in workspace.db or local sessions.db
        2. If path: extract folder_name and resolve
        3. If folder_name: lookup in workspace.db

    Args:
        identifier: Project UUID, folder name, or path

    Returns:
        Dict with:
            - project_id: Canonical UUID
            - folder_name: Project folder name (ground truth)
            - project_path: Absolute path to project directory
            - source: Where the resolution came from ('workspace', 'local', 'path')
        Or None if project cannot be resolved.

    Example:
        >>> resolve_project_identifier("empirica")
        {'project_id': '748a81a2-...', 'folder_name': 'empirica',
         'project_path': '/path/to/empirica', 'source': 'workspace'}

        >>> resolve_project_identifier("748a81a2-ac14-45b8-a185-994997b76828")
        {'project_id': '748a81a2-...', 'folder_name': 'empirica',
         'project_path': '/path/to/empirica', 'source': 'workspace'}
    """
    from pathlib import Path as P

    if not identifier:
        return None

    identifier = identifier.strip()

    # Normalize path input to folder_name
    if identifier.startswith("/") or identifier.startswith("~") or "/" in identifier:
        # It's a path - extract folder name and resolve
        path = P(identifier).expanduser().resolve()
        if path.exists() and path.is_dir():
            folder_name = path.name
            # Require a project marker, not just bare .empirica/ — stray
            # cache-only .empirica/ dirs in subdirs would otherwise match.
            if is_project_root(path):
                # Try to get UUID from local sessions.db
                project_id = _get_project_id_from_local_db(path)
                return {
                    "project_id": project_id,
                    "folder_name": folder_name,
                    "project_path": str(path),
                    "source": "path",
                }
        # Path doesn't exist or isn't a valid project
        identifier = path.name  # Fall through to folder_name resolution

    # Try workspace.db lookup first (enhanced resolution)
    workspace_result = _resolve_via_workspace_db(identifier)
    if workspace_result:
        return workspace_result

    # Fallback: local .empirica lookup (basic resolution)
    # This handles projects not yet registered in workspace
    local_result = _resolve_via_local_empirica(identifier)
    if local_result:
        return local_result

    return None


def _get_project_id_from_local_db(project_path: "Path") -> str | None:
    """Extract project_id from a project's local config or sessions.db.

    Priority: project.yaml (authoritative) > sessions.db (can be corrupted
    by phantom project_ids from historical sessions).
    """
    from pathlib import Path as P

    project_path = P(project_path)

    # Priority 1: project.yaml is the authoritative source
    project_yaml = project_path / ".empirica" / "project.yaml"
    if project_yaml.exists():
        try:
            import yaml

            with open(project_yaml) as f:
                config = yaml.safe_load(f)
                if config and config.get("project_id"):
                    return config["project_id"]
        except Exception:
            pass

    # Priority 2: Fall back to sessions.db most recent session
    import sqlite3

    db_path = project_path / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        return None

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT project_id FROM sessions
            WHERE project_id IS NOT NULL AND project_id != ''
            ORDER BY created_at DESC LIMIT 1
        """)
        row = cursor.fetchone()
        conn.close()
        if row:
            return row[0]
    except Exception:
        pass
    return None


def _resolve_via_workspace_db(identifier: str) -> dict | None:
    """Resolve project via workspace.db (global registry).

    Workspace.db stores all registered projects with:
    - id: UUID
    - name: Project name (may differ from folder)
    - trajectory_path: Absolute path to project folder

    Args:
        identifier: UUID, folder_name, or project name

    Returns:
        Project info dict or None
    """
    import sqlite3
    from pathlib import Path as P

    workspace_db = P.home() / ".empirica" / "workspace" / "workspace.db"
    if not workspace_db.exists():
        return None

    try:
        conn = sqlite3.connect(str(workspace_db))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Strategy 1: Check if it's a UUID - look up by id
        if _is_uuid_format(identifier):
            cursor.execute(
                """
                SELECT id, name, trajectory_path FROM global_projects
                WHERE id = ? AND status = 'active'
            """,
                (identifier,),
            )
        else:
            # Strategy 2: Look up by folder name (from trajectory_path)
            # or by project name
            cursor.execute(
                """
                SELECT id, name, trajectory_path FROM global_projects
                WHERE (
                    trajectory_path LIKE ? OR
                    name = ?
                ) AND status = 'active'
            """,
                (f"%/{identifier}", identifier),
            )

        row = cursor.fetchone()
        conn.close()

        if row:
            trajectory_path = row["trajectory_path"]
            # Normalize: strip /.empirica suffix if present (legacy data format)
            if trajectory_path and trajectory_path.endswith("/.empirica"):
                trajectory_path = trajectory_path[:-10]  # Remove /.empirica
            folder_name = P(trajectory_path).name if trajectory_path else None
            return {
                "project_id": row["id"],
                "folder_name": folder_name,
                "project_path": trajectory_path,
                "source": "workspace",
            }
    except Exception as e:
        logger.debug(f"workspace.db lookup failed: {e}")

    return None


def _resolve_via_local_empirica(identifier: str) -> dict | None:
    """Resolve project via local .empirica discovery.

    Fallback when workspace.db is not available or doesn't have the project.
    Searches common project locations.

    Args:
        identifier: folder_name (UUID lookup not supported in fallback)

    Returns:
        Project info dict or None
    """
    from pathlib import Path as P

    # If it's a UUID, we can't resolve without workspace.db
    if _is_uuid_format(identifier):
        return None

    # Search common locations for the folder
    search_paths = [
        P.home() / identifier,
        P.home() / "projects" / identifier,
        P.home() / "code" / identifier,
        P.home() / "empirical-ai" / identifier,
        P.cwd().parent / identifier,  # Sibling directory
    ]

    for path in search_paths:
        if path.exists() and path.is_dir() and is_project_root(path):
            project_id = _get_project_id_from_local_db(path)
            return {
                "project_id": project_id,  # May be None if no sessions yet
                "folder_name": path.name,
                "project_path": str(path),
                "source": "local",
            }

    return None


# =============================================================================
# Project Root Resolution (canonical — moved from plugin lib/project_resolver.py)
# =============================================================================
# These were previously duplicated in the hook-side mirror. Consolidated here
# as the single source of truth (goal 7ca0877c, v1.8.14).


def has_valid_db(project_path: Path) -> bool:
    """Check if a project path has a valid .empirica/sessions/sessions.db."""
    import sqlite3 as _sqlite3

    db_path = project_path / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        return False
    try:
        conn = _sqlite3.connect(str(db_path))
        conn.execute("SELECT 1 FROM sessions LIMIT 1")
        conn.close()
        return True
    except Exception:
        return False


def is_project_root(path: Path | str) -> bool:
    """True iff `path` looks like a real Empirica project root.

    Requires a marker file (`.empirica/project.yaml` OR `.empirica/sessions/sessions.db`).
    Bare existence of `.empirica/` is not enough — stray cache-only `.empirica/`
    directories get created in subdirs when CLI commands run from a non-canonical
    CWD. Resolvers that accept any `.empirica/` mis-identify those strays as
    projects and then try to open a non-existent sessions.db.
    """
    p = Path(path)
    return (p / ".empirica" / "project.yaml").exists() or (p / ".empirica" / "sessions" / "sessions.db").exists()


def _find_git_root() -> Path | None:
    """Find the git repo root from CWD."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except Exception:
        pass
    return None


def _read_json_file(path: Path) -> dict | None:
    """Read a JSON file, returning None on any error."""
    try:
        if path.exists():
            import json as _json

            with open(path) as f:
                return _json.load(f)
    except Exception:
        pass
    return None


def _check_tx_file_for_project(tx_file: Path, proj_path: Path, best_mtime: float) -> tuple:
    """Check a transaction file. Returns (project_path, mtime) or (None, best_mtime)."""
    try:
        mtime = tx_file.stat().st_mtime
        if mtime <= best_mtime:
            return None, best_mtime
        tx_data = _read_json_file(tx_file)
        if tx_data and tx_data.get("status") == "open":
            tx_project = tx_data.get("project_path", str(proj_path))
            if has_valid_db(Path(tx_project)):
                return Path(tx_project), mtime
    except Exception:
        pass
    return None, best_mtime


def _scan_workspace_for_project(instance_id: str | None) -> Path | None:
    """Scan registered projects in workspace.db for one with an open transaction."""
    import sqlite3 as _sqlite3

    workspace_db = Path.home() / ".empirica" / "workspace" / "workspace.db"
    if not workspace_db.exists():
        return None
    try:
        conn = _sqlite3.connect(str(workspace_db))
        cursor = conn.execute("SELECT trajectory_path FROM global_projects")
        rows = cursor.fetchall()
        conn.close()
    except Exception:
        return None

    suffix = _get_instance_suffix()
    best_match = None
    best_mtime = 0

    for (traj_path,) in rows:
        if not traj_path:
            continue
        proj_path = Path(traj_path)
        empirica_dir = proj_path / ".empirica"
        if not empirica_dir.exists():
            continue

        tx_file = empirica_dir / f"active_transaction{suffix}.json"
        if tx_file.exists():
            result, best_mtime = _check_tx_file_for_project(tx_file, proj_path, best_mtime)
            if result:
                best_match = result

        try:
            for tx_candidate in empirica_dir.glob("active_transaction*.json"):
                if tx_candidate == tx_file:
                    continue
                result, best_mtime = _check_tx_file_for_project(tx_candidate, proj_path, best_mtime)
                if result:
                    best_match = result
        except Exception:
            pass

    return best_match


def _ssh_env_is_stale() -> bool:
    """True when SSH_* env vars are present but reflect a DEAD session.

    SSH_CONNECTION/SSH_CLIENT/SSH_TTY are inherited by tmux/screen servers, so a
    pane whose multiplexer server was started over an SSH login that has since
    logged out carries those vars forever — falsely reading as "remote" on a
    now-local machine (reported: a local Mac tripping REMOTE:SSH:UNTRUSTED). A
    LIVE interactive SSH session's SSH_TTY device exists; an inherited/stale one
    points at a pty that was torn down when the login ended. Device-existence is
    the discriminator that works in the hook subprocess (which has no controlling
    tty of its own to compare SSH_TTY against). SSH_CONNECTION/SSH_CLIENT without
    any SSH_TTY is left as-is (non-interactive remote, e.g. scp/forced command).
    """
    ssh_tty = os.environ.get("SSH_TTY")
    if ssh_tty:
        try:
            return not os.path.exists(ssh_tty)
        except OSError:
            return True
    return False


def detect_environment() -> dict:
    """Detect execution environment for Sentinel context awareness.

    Returns dict with hostname, is_remote, is_container, is_ci,
    is_trusted, trust_source.
    """
    import fnmatch
    import socket

    hostname = socket.gethostname()
    _ssh_env = os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT") or os.environ.get("SSH_TTY")
    # Stale, multiplexer-inherited SSH_* env on a local machine is NOT a remote
    # session — don't emit REMOTE:UNTRUSTED for it (advisory annotation only).
    is_remote = bool(_ssh_env) and not _ssh_env_is_stale()
    is_container = os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")
    is_ci = bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS") or os.environ.get("GITLAB_CI"))

    is_trusted = None
    trust_source = None

    if is_remote or is_container or is_ci:
        trusted_file = Path.home() / ".empirica" / "trusted_hosts"
        if trusted_file.exists():
            try:
                lines = trusted_file.read_text().splitlines()
                patterns = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
                for pattern in patterns:
                    if fnmatch.fnmatch(hostname, pattern):
                        is_trusted = True
                        trust_source = f"matched '{pattern}' in trusted_hosts"
                        break
                if is_trusted is None:
                    is_trusted = False
                    trust_source = f"hostname '{hostname}' not in trusted_hosts"
            except Exception:
                is_trusted = False
                trust_source = "trusted_hosts unreadable"
        else:
            is_trusted = False
            trust_source = "no trusted_hosts file"

    return {
        "hostname": hostname,
        "is_remote": is_remote,
        "is_container": is_container,
        "is_ci": is_ci,
        "is_trusted": is_trusted,
        "trust_source": trust_source,
    }


def _find_project_from_open_transaction(claude_session_id, instance_id, suffix):
    """Check candidate paths for an open transaction file."""
    candidate_paths = set()
    if claude_session_id:
        data = _read_json_file(Path.home() / ".empirica" / f"active_work_{claude_session_id}.json")
        if data and data.get("project_path"):
            candidate_paths.add(data["project_path"])
    if instance_id:
        data = _read_json_file(Path.home() / ".empirica" / "instance_projects" / f"{instance_id}.json")
        if data and data.get("project_path"):
            candidate_paths.add(data["project_path"])
    for cpath in candidate_paths:
        tx_data = _read_json_file(Path(cpath) / ".empirica" / f"active_transaction{suffix}.json")
        if tx_data and tx_data.get("status") == "open":
            tx_project = tx_data.get("project_path", cpath)
            if has_valid_db(Path(tx_project)):
                return Path(tx_project)
    return None


def _find_project_from_state_files(claude_session_id, instance_id):
    """Check instance_projects and active_work files for a valid project root."""
    if instance_id:
        data = _read_json_file(Path.home() / ".empirica" / "instance_projects" / f"{instance_id}.json")
        if data and data.get("project_path"):
            p = Path(data["project_path"])
            if has_valid_db(p):
                return p
    if claude_session_id:
        data = _read_json_file(Path.home() / ".empirica" / f"active_work_{claude_session_id}.json")
        if data and data.get("project_path"):
            p = Path(data["project_path"])
            if has_valid_db(p):
                return p
    return None


def find_project_root(
    claude_session_id: str | None = None,
    *,
    check_compact_handoff: bool = False,
    allow_workspace_scan: bool = True,
    allow_cwd_fallback: bool = False,
    allow_git_root: bool = False,
) -> Path | None:
    """Comprehensive project root resolution.

    Unified priority chain (highest to lowest):
    1. Compact handoff file (only if check_compact_handoff=True)
    2. Open transaction file (AUTHORITATIVE during transaction)
    3. active_work_{claude_session_id}.json
    4. instance_projects/{instance_id}.json
    5. Workspace scan (if allow_workspace_scan=True)
    6. EMPIRICA_WORKSPACE_ROOT env var
    7. Git repo root (if allow_git_root=True)
    8. CWD (if allow_cwd_fallback=True)
    """
    instance_id = get_instance_id()
    suffix = _get_instance_suffix()

    # Priority 1: Compact handoff
    if check_compact_handoff and instance_id:
        handoff_file = Path.home() / ".empirica" / f"compact_handoff{suffix}.json"
        data = _read_json_file(handoff_file)
        if data:
            project_path = data.get("project_path")
            if project_path and has_valid_db(Path(project_path)):
                return Path(project_path)

    # Priority 2: Open transaction file
    tx_result = _find_project_from_open_transaction(claude_session_id, instance_id, suffix)
    if tx_result:
        return tx_result

    # Priority 3-4: instance_projects then active_work
    state_result = _find_project_from_state_files(claude_session_id, instance_id)
    if state_result:
        return state_result

    # Priority 5: Workspace scan
    if allow_workspace_scan:
        ws_result = _scan_workspace_for_project(instance_id)
        if ws_result:
            return ws_result

    # Priority 6: EMPIRICA_WORKSPACE_ROOT
    ws_root = os.environ.get("EMPIRICA_WORKSPACE_ROOT")
    if ws_root and has_valid_db(Path(ws_root)):
        return Path(ws_root)

    # Priority 7: Git root
    if allow_git_root:
        git_root = _find_git_root()
        if git_root and has_valid_db(git_root):
            return git_root

    # Priority 8: CWD
    if allow_cwd_fallback:
        cwd = Path.cwd()
        if has_valid_db(cwd):
            return cwd
        git_root = _find_git_root()
        if git_root and has_valid_db(git_root):
            return git_root
        return cwd

    return None
