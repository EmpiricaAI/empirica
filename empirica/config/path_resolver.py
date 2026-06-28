#!/usr/bin/env python3
"""
Empirica Path Resolver - Single Source of Truth for All Paths

Resolves paths in priority order:
1. Environment variables (EMPIRICA_WORKSPACE_ROOT for Docker, EMPIRICA_DATA_DIR for explicit paths)
2. .empirica/config.yaml in git root
3. Fallback to CWD/.empirica (legacy behavior)

Environment Variables:
    EMPIRICA_WORKSPACE_ROOT: For Docker/multi-AI environments. Points to workspace root.
                            System will look for <workspace>/.empirica/
    EMPIRICA_DATA_DIR:      Explicit path to empirica data directory
    EMPIRICA_SESSION_DB:    Explicit path to sessions database file

Usage:
    from empirica.config.path_resolver import get_empirica_root, get_session_db_path

    root = get_empirica_root()  # Returns Path object
    db_path = get_session_db_path()  # Returns full path to sessions.db

Docker Example:
    Set in docker-compose.yml:
      environment:
        - EMPIRICA_WORKSPACE_ROOT=/workspace

    This ensures all containers use the same workspace for empirica data.

Author: Claude Code
Date: 2025-12-03
Version: 1.1.0 (Added EMPIRICA_WORKSPACE_ROOT support)
"""

import logging
import os
import subprocess
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Cache for git root (expensive to compute repeatedly)
_git_root_cache: Path | None = None

# Forbidden system paths for workspace/data directories
FORBIDDEN_PATH_PREFIXES = ["/etc", "/var/log", "/usr", "/bin", "/sbin", "/root", "/boot", "/proc", "/sys"]


def _validate_user_path(path_str: str, env_var_name: str) -> Path:
    """
    Validate that a user-provided path is safe.

    Args:
        path_str: The path string from environment variable
        env_var_name: Name of the env var (for error messages)

    Returns:
        Validated and resolved Path

    Raises:
        ValueError: If path is in a forbidden system directory
    """
    resolved = Path(path_str).expanduser().resolve()
    resolved_str = str(resolved)

    for prefix in FORBIDDEN_PATH_PREFIXES:
        if resolved_str.startswith(prefix):
            raise ValueError(f"{env_var_name} cannot point to system directory: {prefix}. Got: {resolved_str}")

    return resolved


def get_git_root() -> Path | None:
    """
    Get git repository root directory.

    Returns:
        Path to git root, or None if not in a git repo
    """
    global _git_root_cache

    if _git_root_cache is not None:
        return _git_root_cache

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=2, check=False
        )

        if result.returncode == 0:
            _git_root_cache = Path(result.stdout.strip())
            return _git_root_cache

    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None


def load_empirica_config() -> dict | None:
    """
    Load .empirica/config.yaml from git root.

    Returns:
        Config dict or None if not found
    """
    git_root = get_git_root()
    if not git_root:
        return None

    config_path = git_root / ".empirica" / "config.yaml"
    if not config_path.exists():
        return None

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
        logger.debug(f"✅ Loaded Empirica config from {config_path}")
        return config
    except Exception as e:
        logger.warning(f"⚠️  Failed to load {config_path}: {e}")
        return None


def get_empirica_root() -> Path:
    """
    Get Empirica root data directory.

    Priority:
    1. EMPIRICA_WORKSPACE_ROOT environment variable (for Docker/workspace environments)
    2. EMPIRICA_DATA_DIR environment variable (explicit data dir)
    3. .empirica/config.yaml -> root
    4. <git-root>/.empirica (if in git repo)
    5. <cwd>/.empirica (fallback)

    Returns:
        Path to .empirica root directory

    Raises:
        ValueError: If no .empirica root can be determined (not in git repo and no env vars set).
    """
    # 1. Check workspace root (Docker/multi-AI environments)
    if workspace_root := os.getenv("EMPIRICA_WORKSPACE_ROOT"):
        try:
            workspace_path = _validate_user_path(workspace_root, "EMPIRICA_WORKSPACE_ROOT")
            empirica_root = workspace_path / ".empirica"
            if empirica_root.exists() or workspace_path.exists():
                logger.debug(f"📍 Using EMPIRICA_WORKSPACE_ROOT: {empirica_root}")
                return empirica_root
        except ValueError as e:
            logger.warning(f"⚠️  Invalid EMPIRICA_WORKSPACE_ROOT: {e}")
            # Fall through to next option

    # 2. Check explicit data dir environment variable
    if env_root := os.getenv("EMPIRICA_DATA_DIR"):
        try:
            root = _validate_user_path(env_root, "EMPIRICA_DATA_DIR")
            logger.debug(f"📍 Using EMPIRICA_DATA_DIR: {root}")
            return root
        except ValueError as e:
            logger.warning(f"⚠️  Invalid EMPIRICA_DATA_DIR: {e}")
            # Fall through to next option

    # 3. Check .empirica/config.yaml
    config = load_empirica_config()
    if config and "root" in config:
        root = Path(config["root"]).expanduser().resolve()
        logger.debug(f"📍 Using config.yaml root: {root}")
        return root

    # 4. Use git root if available
    git_root = get_git_root()
    if git_root:
        root = git_root / ".empirica"
        logger.debug(f"📍 Using git root: {root}")
        return root

    # 5. No fallback - CWD is unreliable (can be reset by Claude Code)
    # Caller should handle None or use instance-aware resolution
    logger.warning("📍 No .empirica root found via env, config, or git root")
    raise ValueError("Cannot determine .empirica root - not in a git repo and no env vars set")


def _has_open_transaction(context_project_path: str) -> bool:
    """Check if the context project has an open transaction."""
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        suffix = R.instance_suffix()
    except Exception:
        suffix = ""
    tx_file = Path(context_project_path) / ".empirica" / f"active_transaction{suffix}.json"
    try:
        if tx_file.exists():
            import json as _json

            with open(tx_file) as _f:
                return _json.load(_f).get("status") == "open"
    except Exception:
        pass
    return False


def _try_context_project_db(context_project_path: str, git_root) -> Path | None:
    """Try to use the context project's sessions.db, with cross-project bleed check.

    Returns the db_path if valid, None if context should be skipped.
    """
    cwd_reliable = os.getenv("EMPIRICA_CWD_RELIABLE", "").lower() == "true"
    context_is_local = True

    if cwd_reliable and git_root and str(git_root) != context_project_path:
        if not _has_open_transaction(context_project_path):
            local_db = Path(str(git_root)) / ".empirica" / "sessions" / "sessions.db"
            if local_db.exists():
                logger.debug(
                    f"📍 Cross-project bleed detected: context={context_project_path}, git_root={git_root}. Preferring git root."
                )
                context_is_local = False

    if context_is_local:
        db_path = Path(context_project_path) / ".empirica" / "sessions" / "sessions.db"
        if db_path.exists():
            logger.debug(f"📍 Using unified context resolver: {db_path}")
            return db_path

    return None


def get_session_db_path() -> Path:
    """
    Get full path to sessions database.

    Priority:
    0. EMPIRICA_SESSION_DB env var (explicit override — tests, CI, Docker)
    1. Unified context resolver (transaction → active_work → TTY → instance_projects)
    2. Workspace.db lookup (git root → project via global registry)
    3. Git root based (for unregistered projects in a git repo)

    Note: CWD-based fallbacks removed - CWD is unreliable with Claude Code.

    Returns:
        Path to sessions.db

    Raises:
        ValueError: If no sessions.db path can be determined (not in git repo,
            no context found, and no env vars set).
    """
    import sqlite3

    # 0. EMPIRICA_SESSION_DB env var (explicit override — wins over everything)
    # Used by: tests (subprocess isolation), CI/CD, Docker containers.
    # If someone explicitly sets this, they want THIS database, period.
    # Not instance-aware — intentionally bypasses multi-instance resolution.
    if env_db := os.getenv("EMPIRICA_SESSION_DB"):
        try:
            db_path = _validate_user_path(env_db, "EMPIRICA_SESSION_DB")
            logger.debug(f"📍 Using EMPIRICA_SESSION_DB (explicit override): {db_path}")
            return db_path
        except ValueError as e:
            logger.warning(f"⚠️  Invalid EMPIRICA_SESSION_DB: {e}")

    # 1. Use unified context resolver (canonical source of truth)
    # This respects: transaction file (survives compaction) → active_work → TTY → instance_projects
    context_project_path = None
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        context = R.context()
        context_project_path = context.get("project_path")
    except Exception as e:
        logger.debug(f"📍 Unified context lookup failed: {e}")

    # Get git root early — needed for cross-check and step 2
    git_root = None
    try:
        git_root = get_git_root()
    except Exception:
        pass

    if context_project_path:
        db_path = _try_context_project_db(context_project_path, git_root)
        if db_path:
            return db_path

    # 2. Check workspace.db for git root → project mapping (global registry)
    # Git root is stable even when CWD changes within the project
    try:
        if not git_root:
            git_root = get_git_root()
        if git_root:
            workspace_db = Path.home() / ".empirica" / "workspace" / "workspace.db"
            if workspace_db.exists():
                conn = sqlite3.connect(str(workspace_db))
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT trajectory_path FROM global_projects WHERE trajectory_path = ? AND status = 'active'",
                    (str(git_root),),
                )
                row = cursor.fetchone()
                conn.close()
                if row:
                    project_path = Path(row[0])
                    db_path = project_path / ".empirica" / "sessions" / "sessions.db"
                    if db_path.exists():
                        logger.debug(f"📍 Using workspace.db lookup: {db_path}")
                        return db_path
    except Exception as e:
        logger.debug(f"📍 workspace.db lookup failed: {e}")

    # 3. Git root based (for projects not yet registered in workspace but in a git repo)
    try:
        root = get_empirica_root()
        db_path = root / "sessions" / "sessions.db"
        if db_path.exists():
            logger.debug(f"📍 Using git-root based path: {db_path}")
            return db_path
    except ValueError:
        # Not in a git repo and no env vars set - continue to next option
        pass

    # No valid path found - raise error instead of guessing
    raise ValueError(
        "Cannot determine sessions.db path - not in a git repo, no context found, and no env vars set.\n"
        "Options:\n"
        "  1. Run 'empirica project-init' to initialize this repo\n"
        "  2. Use 'empirica session-create --ai-id <name> --auto-init' for first-time setup\n"
        "  3. Set EMPIRICA_SESSION_DB environment variable explicitly"
    )


def resolve_session_db_path(session_id: str) -> Path | None:
    """
    Resolve which database contains a given session.

    Priority:
    0. EMPIRICA_SESSION_DB env var (explicit override — tests, CI, Docker)
    1. instance_projects mapping (TMUX_PANE-based, works in subprocesses)
    2. TTY session's project_path
    3. get_session_db_path() (unified context → workspace.db → git root)

    Args:
        session_id: UUID of the session to find

    Returns:
        Path to the sessions.db containing this session, or None if not found
    """
    import json

    # Priority 0: EMPIRICA_SESSION_DB (explicit override — same as get_session_db_path)
    if env_db := os.getenv("EMPIRICA_SESSION_DB"):
        try:
            db_path = _validate_user_path(env_db, "EMPIRICA_SESSION_DB")
            logger.debug(f"📍 resolve_session_db_path: Using EMPIRICA_SESSION_DB override: {db_path}")
            return db_path
        except ValueError as e:
            logger.warning(f"⚠️  Invalid EMPIRICA_SESSION_DB: {e}")

    # Priority 1: instance_projects mapping (uses TMUX_PANE, works in subprocesses)
    try:
        from empirica.core.statusline_cache import get_instance_id as get_inst_id

        inst_id = get_inst_id()
        if inst_id:
            instance_file = Path.home() / ".empirica" / "instance_projects" / f"{inst_id}.json"
            if instance_file.exists():
                with open(instance_file) as f:
                    instance_data = json.load(f)
                instance_project_path = instance_data.get("project_path")
                if instance_project_path:
                    db_path = Path(instance_project_path) / ".empirica" / "sessions" / "sessions.db"
                    if db_path.exists():
                        return db_path
    except Exception:
        pass

    # Priority 2: Try TTY session's project_path
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        tty_session = R.tty_session(warn_if_stale=False)
        if tty_session:
            tty_project_path = tty_session.get("project_path")
            if tty_project_path:
                db_path = Path(tty_project_path) / ".empirica" / "sessions" / "sessions.db"
                if db_path.exists():
                    return db_path
    except Exception:
        pass

    # Priority 3: Fall back to get_session_db_path() (uses unified context → workspace.db → git root)
    try:
        db_path = get_session_db_path()
        if db_path.exists():
            return db_path
    except ValueError:
        pass
    return None


def get_global_empirica_home() -> Path:
    """
    Get the global Empirica home directory (~/.empirica).

    This is ALWAYS the user's home directory, regardless of project context.
    Used for cross-project data like CRM, global lessons, and credentials.

    Returns:
        Path to ~/.empirica/
    """
    return Path.home() / ".empirica"


def ensure_empirica_structure() -> None:
    """
    Ensure .empirica directory structure exists.
    Creates directories if they don't exist.
    """
    root = get_empirica_root()

    # Create subdirectories
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    (root / "identity").mkdir(parents=True, exist_ok=True)
    (root / "metrics").mkdir(parents=True, exist_ok=True)
    (root / "messages").mkdir(parents=True, exist_ok=True)
    (root / "personas").mkdir(parents=True, exist_ok=True)

    logger.debug(f"✅ Ensured .empirica structure at {root}")


def create_default_config() -> None:
    """
    Create default .empirica/config.yaml if it doesn't exist.
    Only creates in git repos.
    """
    git_root = get_git_root()
    if not git_root:
        logger.debug("Not in git repo, skipping config.yaml creation")
        return

    config_path = git_root / ".empirica" / "config.yaml"
    if config_path.exists():
        logger.debug(f"Config already exists: {config_path}")
        return

    # Ensure .empirica directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Create default config
    default_config = {
        "version": "2.0",
        "root": str(git_root / ".empirica"),
        "paths": {
            "sessions": "sessions/sessions.db",
            "identity": "identity/",
            "messages": "messages/",
            "metrics": "metrics/",
            "personas": "personas/",
        },
        "settings": {"auto_checkpoint": True, "git_integration": True, "log_level": "info"},
        "env_overrides": ["EMPIRICA_DATA_DIR", "EMPIRICA_SESSION_DB"],
    }

    with open(config_path, "w") as f:
        yaml.dump(default_config, f, default_flow_style=False, sort_keys=False)

    logger.info(f"✅ Created default config: {config_path}")


def debug_paths() -> dict:
    """
    Get all resolved paths for debugging.

    Returns:
        Dict with all path information
    """
    root = get_empirica_root()
    return {
        "git_root": str(get_git_root()) if get_git_root() else None,
        "empirica_root": str(root),
        "session_db": str(get_session_db_path()),
        "identity_dir": str(root / "identity"),
        "metrics_dir": str(root / "metrics"),
        "messages_dir": str(root / "messages"),
        "global_home": str(get_global_empirica_home()),
        "env_vars": {
            "EMPIRICA_DATA_DIR": os.getenv("EMPIRICA_DATA_DIR"),
            "EMPIRICA_SESSION_DB": os.getenv("EMPIRICA_SESSION_DB"),
        },
        "config_loaded": load_empirica_config() is not None,
    }


if __name__ == "__main__":
    # Test/debug mode
    import json

    logging.basicConfig(level=logging.DEBUG)

    print("🔍 Empirica Path Resolver Debug\n")
    print(json.dumps(debug_paths(), indent=2))

    print("\n📋 Ensuring structure...")
    ensure_empirica_structure()

    print("\n📝 Creating default config...")
    create_default_config()
