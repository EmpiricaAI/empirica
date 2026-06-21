#!/usr/bin/env python3
"""
Auto Issue Capture System

Automatically captures bugs, errors, warnings, and other issues during work
without interrupting flow state. Issues can be handed off to other AIs.

Architecture:
  - Hooks into logging system
  - Captures context (stack trace, code location, variables)
  - Stores in SQLite + Git for portability
  - Exposes handoff API for other AIs

Benefits:
  - Maintain continuous work flow
  - Enable seamless AI-to-AI handoffs
  - Build audit trail of issues and resolutions
  - Pattern discovery across sessions
"""

import inspect
import json
import logging
import sqlite3
import sys
import traceback
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


class IssueSeverity(Enum):
    """Issue severity levels"""
    BLOCKER = "blocker"      # Prevents work
    HIGH = "high"            # Significantly impacts work
    MEDIUM = "medium"        # Notable but workaround possible
    LOW = "low"              # Minor or cosmetic


class IssueCategory(Enum):
    """Issue categories for classification"""
    BUG = "bug"                        # Code defect
    ERROR = "error"                    # Runtime error
    WARNING = "warning"                # Potential problem
    DEPRECATION = "deprecation"        # Deprecated API/pattern
    TODO = "todo"                      # Incomplete work
    PERFORMANCE = "performance"        # Performance issue
    COMPATIBILITY = "compatibility"    # Platform/version issue
    DESIGN = "design"                  # Architectural issue
    OTHER = "other"


class IssueStatus(Enum):
    """Issue status lifecycle"""
    NEW = "new"                    # Just captured
    INVESTIGATING = "investigating"  # AI working on it
    HANDOFF = "handoff"            # Ready for other AI
    RESOLVED = "resolved"          # Fixed
    WONTFIX = "wontfix"            # Intentional/acceptable


class AutoIssueCaptureService:
    """
    Central service for capturing and managing auto-discovered issues.

    Integrates with logging system to capture errors/warnings without
    interrupting work flow. Enables handoff to other AIs with full context.
    """

    def __init__(self, session_id: str, db_path: str | None = None, enable: bool = True):
        """
        Initialize issue capture service.

        Args:
            session_id: Current session ID for tracking
            db_path: Path to SQLite database (uses session DB by default)
            enable: Whether to enable auto-capture
        """
        self.session_id = session_id
        self.enable = enable
        self.db_path = Path(db_path) if db_path else self._get_default_db_path()

        # Create table if needed
        self._ensure_schema()

        # Issue stats for this session
        self._issue_count = 0
        self._handoff_ready_count = 0

    @staticmethod
    def _get_default_db_path() -> Path:
        """Get default database path from session DB"""
        from empirica.config.path_resolver import get_session_db_path
        return get_session_db_path()

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection with proper timeout and WAL configuration"""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        # Enable WAL mode for better concurrency
        conn.execute("PRAGMA journal_mode=WAL")
        # Set busy timeout as additional protection
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _ensure_schema(self) -> None:
        """Create auto_captured_issues table if it doesn't exist"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

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

            # Create index for fast lookup
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_issues_session_status
                ON auto_captured_issues(session_id, status)
            """)

            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to create issues table: {e}")

    def capture_error(
        self,
        message: str,
        severity: IssueSeverity = IssueSeverity.HIGH,
        category: IssueCategory = IssueCategory.ERROR,
        context: dict[str, Any] | None = None,
        exc_info: Exception | None = None
    ) -> str:
        """
        Capture an error/exception without raising it.

        Args:
            message: Error description
            severity: How critical is this issue?
            category: What type of issue?
            context: Additional context (locals, state, etc.)
            exc_info: Exception object if applicable

        Returns:
            Issue ID for tracking
        """
        if not self.enable:
            return ""

        # Get code location from caller
        frame = inspect.currentframe()
        caller_frame = frame.f_back
        code_location = f"{caller_frame.f_code.co_filename}:{caller_frame.f_lineno}"

        # Capture stack trace if exception provided
        stack_trace = None
        if exc_info:
            stack_trace = "".join(traceback.format_exception(type(exc_info), exc_info, exc_info.__traceback__))
        else:
            stack_trace = "".join(traceback.format_stack(caller_frame))

        # Capture context if provided, otherwise try to get locals
        if context is None:
            context = self._extract_safe_context(caller_frame.f_locals)

        # Create issue record
        issue_id = str(uuid.uuid4())
        issue = {
            "id": issue_id,
            "session_id": self.session_id,
            "severity": severity.value,
            "category": category.value,
            "code_location": code_location,
            "message": message,
            "stack_trace": stack_trace,
            "context": json.dumps(context),
            "status": IssueStatus.NEW.value,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        # Store in database
        self._store_issue(issue)

        # Log for monitoring
        logger.info(f"Auto-captured issue {issue_id}: {message} ({category.value}/{severity.value})")

        self._issue_count += 1
        return issue_id

    def capture_assertion_failure(
        self,
        condition: str,
        message: str = "",
        severity: IssueSeverity = IssueSeverity.MEDIUM
    ) -> str:
        """
        Capture assertion failure without interrupting flow.

        Usage:
            if not expected_condition:
                auto_capture.capture_assertion_failure("expected_condition", "Why it matters")
        """
        full_message = f"Assertion failed: {condition}. {message}" if message else f"Assertion failed: {condition}"
        return self.capture_error(
            message=full_message,
            severity=severity,
            category=IssueCategory.BUG
        )

    def capture_performance_issue(
        self,
        operation: str,
        actual_ms: float,
        expected_ms: float,
        severity: IssueSeverity = IssueSeverity.MEDIUM
    ) -> str:
        """
        Capture performance degradation.

        Args:
            operation: What operation was slow?
            actual_ms: Actual time in milliseconds
            expected_ms: Expected/acceptable time in milliseconds
        """
        message = f"Performance issue: {operation} took {actual_ms:.1f}ms (expected <{expected_ms:.1f}ms)"
        return self.capture_error(
            message=message,
            severity=severity,
            category=IssueCategory.PERFORMANCE
        )

    def capture_warning(
        self,
        message: str,
        category: IssueCategory = IssueCategory.WARNING,
        context: dict[str, Any] | None = None
    ) -> str:
        """Capture a warning without raising"""
        return self.capture_error(
            message=message,
            severity=IssueSeverity.LOW,
            category=category,
            context=context
        )

    def capture_todo(self, description: str, priority: str = "medium") -> str:
        """Capture incomplete work for later"""
        message = f"TODO ({priority}): {description}"
        severity_map = {"high": IssueSeverity.HIGH, "medium": IssueSeverity.MEDIUM, "low": IssueSeverity.LOW}
        return self.capture_error(
            message=message,
            severity=severity_map.get(priority, IssueSeverity.MEDIUM),
            category=IssueCategory.TODO
        )

    @staticmethod
    def classify_issue_type(message: str, category: str) -> str:
        """
        Classify issue into type: 'genuine_mistake', 'system_prompt', or 'project_specific'

        This is important for general-purpose usage - allows filtering what's worth learning from.

        Classifications:
        - genuine_mistake: Real bugs, performance issues, etc. (applicable to any repo)
        - system_prompt: TODOs, documentation, known limitations (from instructions)
        - project_specific: Framework/tool-specific design decisions (not general)

        Args:
            message: Issue message
            category: Issue category

        Returns:
            Issue type: 'genuine_mistake', 'system_prompt', or 'project_specific'
        """
        msg_lower = message.lower()
        cat_lower = category.lower()

        # System prompt indicators
        system_prompt_keywords = [
            "todo", "fixme", "hack", "refactor", "limitation", "known issue",
            "documentation", "docstring", "comment needed", "system prompt"
        ]

        if any(kw in msg_lower for kw in system_prompt_keywords) or cat_lower == "todo":
            return "system_prompt"

        # Genuine mistakes (bugs, errors, performance, compatibility)
        genuine_keywords = [
            "error", "exception", "timeout", "failed", "crash", "traceback",
            "performance", "slow", "memory", "cpu", "database", "connection",
            "bug", "defect", "regression", "broken"
        ]

        if any(kw in msg_lower for kw in genuine_keywords) or cat_lower in ["error", "bug", "performance", "compatibility"]:
            return "genuine_mistake"

        # Project-specific (architecture, design, framework features)
        # Default: if can't classify as genuine_mistake or system_prompt, it's likely project-specific
        return "project_specific"

    def update_issue_classification(self, issue_id: str) -> None:
        """Update issue_category field based on classification logic"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Get issue details
            cursor.execute("""
                SELECT message, category FROM auto_captured_issues WHERE id = ?
            """, (issue_id,))

            row = cursor.fetchone()
            if not row:
                return

            message, category = row
            issue_type = self.classify_issue_type(message, category)

            # Update issue_category field
            cursor.execute("""
                UPDATE auto_captured_issues
                SET category = ?
                WHERE id = ?
            """, (issue_type, issue_id))

            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to update issue classification: {e}")

    def _extract_safe_context(self, locals_dict: dict[str, Any]) -> dict[str, str]:
        """Extract safe context from locals (avoid sensitive/large objects)"""
        safe_context = {}

        for key, value in locals_dict.items():
            # Skip private/system variables
            if key.startswith("_"):
                continue

            # Only capture simple types and limit size
            if isinstance(value, (str, int, float, bool, type(None))):
                safe_context[key] = str(value)[:200]  # Limit string length
            elif isinstance(value, (list, dict)):
                try:
                    safe_context[key] = str(value)[:500]
                except Exception:
                    logger.debug("Failed to stringify local variable %s", key)

        return safe_context

    def _store_issue(self, issue: dict[str, Any]) -> None:
        """Store issue in SQLite database"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO auto_captured_issues
                (id, session_id, severity, category, code_location, message, stack_trace, context, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                issue["id"],
                issue["session_id"],
                issue["severity"],
                issue["category"],
                issue["code_location"],
                issue["message"],
                issue["stack_trace"],
                issue["context"],
                issue["status"],
                issue["created_at"],
                issue["created_at"]
            ))

            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to store issue: {e}")

    def list_issues(
        self,
        status: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        limit: int = 100
    ) -> list[dict[str, Any]]:
        """
        List captured issues with optional filtering.

        Args:
            status: Filter by status (new, investigating, handoff, resolved, wontfix)
            category: Filter by category
            severity: Filter by severity
            limit: Maximum results

        Returns:
            List of issue dictionaries
        """
        try:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT * FROM auto_captured_issues WHERE session_id = ?"
            params = [self.session_id]

            if status:
                query += " AND status = ?"
                params.append(status)
            if category:
                query += " AND category = ?"
                params.append(category)
            if severity:
                query += " AND severity = ?"
                params.append(severity)

            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            issues = []
            for row in rows:
                issues.append(dict(row))

            conn.close()
            return issues
        except Exception as e:
            logger.warning(f"Failed to list issues: {e}")
            return []

    def mark_for_handoff(self, issue_id: str, assigned_to_ai: str) -> bool:
        """
        Mark issue as ready for handoff to another AI.

        Args:
            issue_id: Issue to hand off
            assigned_to_ai: AI ID to receive the issue

        Returns:
            Success status
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Note: Don't filter by session_id - issues can be handed off from any session
            cursor.execute("""
                UPDATE auto_captured_issues
                SET status = ?, assigned_to_ai = ?, updated_at = ?
                WHERE id = ?
            """, (
                IssueStatus.HANDOFF.value,
                assigned_to_ai,
                datetime.now(timezone.utc).isoformat(),
                issue_id
            ))

            conn.commit()
            conn.close()

            self._handoff_ready_count += 1
            return True
        except Exception as e:
            logger.warning(f"Failed to mark issue for handoff: {e}")
            return False

    def resolve_issue(self, issue_id: str, resolution: str) -> bool:
        """
        Mark issue as resolved with explanation.

        Args:
            issue_id: Issue that was fixed
            resolution: How it was resolved

        Returns:
            Success status
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Note: Don't filter by session_id - issues can be resolved from any session
            cursor.execute("""
                UPDATE auto_captured_issues
                SET status = ?, resolution = ?, updated_at = ?
                WHERE id = ?
            """, (
                IssueStatus.RESOLVED.value,
                resolution,
                datetime.now(timezone.utc).isoformat(),
                issue_id
            ))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.warning(f"Failed to resolve issue: {e}")
            return False

    def export_for_handoff(self, assigned_to_ai: str) -> dict[str, Any]:
        """
        Export all issues assigned to another AI in portable format.

        Perfect for passing to another AI agent with full context.
        """
        # Get all handoff issues for this AI
        try:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM auto_captured_issues
                WHERE session_id = ? AND status = ? AND assigned_to_ai = ?
                ORDER BY created_at DESC
            """, (self.session_id, "handoff", assigned_to_ai))

            rows = cursor.fetchall()
            issues = [dict(row) for row in rows]
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to export issues: {e}")
            issues = []

        return {
            "from_session": self.session_id,
            "assigned_to_ai": assigned_to_ai,
            "export_timestamp": datetime.now(timezone.utc).isoformat(),
            "issue_count": len(issues),
            "issues": issues
        }

    def get_stats(self) -> dict[str, Any]:
        """Get capture statistics for this session"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT status, COUNT(*) as count
                FROM auto_captured_issues
                WHERE session_id = ?
                GROUP BY status
            """, (self.session_id,))

            stats = {"session_id": self.session_id}
            for row in cursor.fetchall():
                stats[f"{row[0]}_count"] = row[1]

            conn.close()
            return stats
        except Exception as e:
            logger.warning(f"Failed to get stats: {e}")
            return {"session_id": self.session_id, "error": str(e)}


# Global instance for easy access
_auto_capture_instance: AutoIssueCaptureService | None = None


class AutoCaptureLoggingHandler(logging.Handler):
    """
    Logging handler that automatically captures ERROR/CRITICAL logs
    and warnings with error patterns.

    Integrates with Python's logging system to capture errors without
    modifying every try/except block.
    """

    # Patterns that indicate an error even at WARNING level
    ERROR_PATTERNS: ClassVar[list[str]] = [
        'failed', 'error', 'exception', 'traceback', 'attribute error',
        'not found', 'missing', 'invalid', 'timeout', 'connection',
        'pydantic', 'validation', 'type error', 'value error', 'key error',
        'import error', 'module not found', 'no attribute', 'cannot', "doesn't exist"
    ]

    def __init__(self, capture_service: AutoIssueCaptureService):
        """
        Initialize handler for WARNING and above (with pattern filtering).

        Args:
            capture_service: AutoIssueCaptureService instance to use
        """
        super().__init__(level=logging.WARNING)  # Capture warnings too
        self.capture_service = capture_service

    def emit(self, record: logging.LogRecord) -> None:
        """
        Capture log record as an issue.

        For WARNING level, only captures if message matches error patterns.
        For ERROR/CRITICAL, always captures.

        Args:
            record: LogRecord to capture
        """
        if not self.capture_service or not self.capture_service.enable:
            return

        try:
            message = record.getMessage().lower()

            # For WARNING level, only capture if matches error patterns
            if record.levelno == logging.WARNING:
                if not any(pattern in message for pattern in self.ERROR_PATTERNS):
                    return  # Skip non-error warnings

            # Map logging levels to severity
            severity_map = {
                logging.ERROR: IssueSeverity.HIGH,
                logging.CRITICAL: IssueSeverity.BLOCKER,
                logging.WARNING: IssueSeverity.MEDIUM
            }

            # Determine category based on exception info and message
            category = IssueCategory.ERROR
            if record.exc_info:
                exc_type = record.exc_info[0]
                if exc_type and issubclass(exc_type, DeprecationWarning):
                    category = IssueCategory.DEPRECATION
            elif 'pydantic' in message or 'validation' in message:
                category = IssueCategory.COMPATIBILITY
            elif 'not found' in message or 'missing' in message:
                category = IssueCategory.BUG

            # Build context from log record
            context = {
                'logger': record.name,
                'module': record.module,
                'function': record.funcName,
                'line': record.lineno,
                'thread': record.thread,
                'process': record.process
            }

            # Capture the error
            self.capture_service.capture_error(
                message=record.getMessage(),
                severity=severity_map.get(record.levelno, IssueSeverity.MEDIUM),
                category=category,
                context=context,
                exc_info=record.exc_info[1] if record.exc_info else None
            )
        except Exception:
            # Don't let handler errors break logging
            self.handleError(record)


def install_auto_capture_hooks(service: AutoIssueCaptureService) -> None:
    """
    Install logging handler and exception hook for automatic error capture.

    This enables true "auto-capture" by hooking into Python's logging system
    and sys.excepthook. Errors will be captured automatically without
    explicit calls to capture_error().

    Args:
        service: AutoIssueCaptureService instance to use for capture
    """
    # Install logging handler for ERROR and above
    handler = AutoCaptureLoggingHandler(service)
    logging.root.addHandler(handler)
    logger.debug("Auto-capture logging handler installed (level=ERROR)")

    # Install exception hook for uncaught exceptions
    original_hook = sys.excepthook

    def auto_capture_excepthook(exc_type, exc_value, exc_traceback):
        """Capture uncaught exceptions then call original hook"""
        if service and service.enable:
            try:
                service.capture_error(
                    message=f"Uncaught {exc_type.__name__}: {exc_value}",
                    severity=IssueSeverity.BLOCKER,
                    category=IssueCategory.ERROR,
                    exc_info=exc_value
                )
            except Exception:  # noqa: S110 — capture errors must not break exception handling
                pass

        # Always call original hook
        original_hook(exc_type, exc_value, exc_traceback)

    sys.excepthook = auto_capture_excepthook
    logger.debug("Auto-capture exception hook installed")

    logger.info("✓ Auto-capture hooks installed (logging.Handler + sys.excepthook)")


def initialize_auto_capture(session_id: str, enable: bool = True) -> AutoIssueCaptureService:
    """Initialize the global auto-capture service"""
    global _auto_capture_instance
    _auto_capture_instance = AutoIssueCaptureService(session_id, enable=enable)
    return _auto_capture_instance


def get_auto_capture() -> AutoIssueCaptureService | None:
    """Get the global auto-capture service instance"""
    return _auto_capture_instance
