"""
Project Resolver - Unified context resolution for Claude Code hooks

This module provides project/session resolution functions for hooks.
It wraps empirica.utils.session_resolver when available, with fallbacks
for standalone operation.

IMPORTANT: This module uses ONLY stdlib imports. Hooks run before the
empirica package is guaranteed available. The empirica imports inside
functions are optional (try/except ImportError).

Preferred usage (when empirica is available):
    from project_resolver import InstanceResolver
    resolver = InstanceResolver()
    project_path = resolver.project_path()

Fallback usage (always available):
    from project_resolver import get_instance_id, _get_instance_suffix
    instance_id = get_instance_id()

Functions:
    get_instance_id() - Instance identifier for multi-instance isolation
    _get_instance_suffix() - Sanitized filename suffix
    get_active_project_path(claude_session_id) - Active project path
    get_active_session_id(claude_session_id) - Active Empirica session ID
    find_project_root(claude_session_id, **) - Comprehensive project resolution
    has_valid_db(project_path) - Check if project has valid sessions.db

Class:
    InstanceResolver - Delegates to empirica.utils.session_resolver.InstanceResolver
                       with local fallback methods
"""

import json
import os
import sqlite3
import subprocess
from pathlib import Path

# =============================================================================
# InstanceResolver — Hook-side facade that delegates to canonical
# =============================================================================

class InstanceResolver:
    """Hook-compatible resolver that delegates to the canonical InstanceResolver.

    Tries to import from empirica.utils.session_resolver first.
    Falls back to local functions if empirica is not importable.

    Usage in hooks:
        from project_resolver import InstanceResolver
        resolver = InstanceResolver()
        project = resolver.project_path(claude_session_id)
    """

    def __init__(self):
        self._canonical = None
        try:
            from empirica.utils.session_resolver import InstanceResolver as _Canonical
            self._canonical = _Canonical()
        except ImportError:
            pass

    def instance_id(self) -> str | None:
        if self._canonical:
            return self._canonical.instance_id()
        return get_instance_id()

    def instance_suffix(self) -> str:
        if self._canonical:
            return self._canonical.instance_suffix()
        return _get_instance_suffix()

    def project_path(self, claude_session_id: str | None = None) -> str | None:
        if self._canonical:
            return self._canonical.project_path(claude_session_id)
        return get_active_project_path(claude_session_id)

    def session_id(self, claude_session_id: str | None = None) -> str | None:
        if self._canonical:
            return self._canonical.session_id(claude_session_id)
        return get_active_session_id(claude_session_id)

    def transaction_read(self, claude_session_id: str | None = None) -> dict | None:
        if self._canonical:
            return self._canonical.transaction_read(claude_session_id)
        # No local fallback for transaction read — hooks should use canonical
        return None

    def transaction_write(self, **kwargs) -> None:
        if self._canonical:
            self._canonical.transaction_write(**kwargs)

    def tty_key(self) -> str | None:
        if self._canonical:
            return self._canonical.tty_key()
        return None

    def tty_session(self, warn_if_stale: bool = True) -> dict | None:
        if self._canonical:
            return self._canonical.tty_session(warn_if_stale=warn_if_stale)
        return None


def get_instance_id() -> str | None:
    """
    Get a unique instance identifier for multi-instance isolation.

    Priority order:
    1. EMPIRICA_INSTANCE_ID env var (explicit override)
    2. TMUX_PANE (tmux terminal pane ID, e.g., "%0", "%1")
    3. TERM_SESSION_ID (macOS Terminal.app session ID)
    4. WINDOWID (X11 window ID)
    5. None (fallback to legacy behavior)

    Returns:
        Instance identifier string, or None for legacy behavior
    """
    # Try empirica import first
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        return R.instance_id()
    except ImportError:
        pass

    # Fallback implementation
    # Priority 1: Explicit override
    explicit_id = os.environ.get('EMPIRICA_INSTANCE_ID')
    if explicit_id:
        return explicit_id

    # Priority 2: tmux pane (most common for multi-instance work)
    tmux_pane = os.environ.get('TMUX_PANE')
    if tmux_pane:
        return f"tmux_{tmux_pane.lstrip('%')}"

    # Priority 3: macOS Terminal.app session
    term_session = os.environ.get('TERM_SESSION_ID')
    if term_session:
        return f"term:{term_session[:16]}"

    # Priority 4: X11 window ID
    window_id = os.environ.get('WINDOWID')
    if window_id:
        return f"x11:{window_id}"

    return None


def _get_instance_suffix() -> str:
    """Get sanitized instance suffix for file names.

    Mirrors empirica.utils.session_resolver._get_instance_suffix().
    Replaces ':' and '%' which are invalid in filenames on some systems.
    e.g. 'x11:78940210' -> '_x11_78940210', 'tmux_0' -> '_tmux_0'
    """
    instance_id = get_instance_id()
    if instance_id:
        safe = instance_id.replace(":", "_").replace("%", "")
        return f"_{safe}"
    return ""


def detect_environment() -> dict:
    """
    Detect execution environment for Sentinel context awareness.

    Delegates to canonical when available (v1.8.14 resolver dedup).

    Returns dict with:
        hostname: str - machine hostname
        is_remote: bool - SSH session detected
        is_container: bool - Docker/Podman container detected
        is_ci: bool - CI/CD environment detected
        is_trusted: bool|None - True if in trusted_hosts, False if remote+untrusted, None if local
        trust_source: str|None - why trusted/untrusted
    """
    try:
        from empirica.utils.session_resolver import detect_environment as _canonical_de
        return _canonical_de()
    except ImportError:
        pass

    import fnmatch
    import socket

    hostname = socket.gethostname()
    is_remote = bool(os.environ.get('SSH_CONNECTION') or os.environ.get('SSH_CLIENT') or os.environ.get('SSH_TTY'))
    is_container = os.path.exists('/.dockerenv') or os.path.exists('/run/.containerenv')
    is_ci = bool(os.environ.get('CI') or os.environ.get('GITHUB_ACTIONS') or os.environ.get('GITLAB_CI'))

    # Determine trust
    is_trusted = None
    trust_source = None

    if is_remote or is_container or is_ci:
        # Check trusted_hosts file
        trusted_file = Path.home() / '.empirica' / 'trusted_hosts'
        if trusted_file.exists():
            try:
                lines = trusted_file.read_text().splitlines()
                patterns = [
                    line.strip() for line in lines
                    if line.strip() and not line.strip().startswith('#')
                ]
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
        'hostname': hostname,
        'is_remote': is_remote,
        'is_container': is_container,
        'is_ci': is_ci,
        'is_trusted': is_trusted,
        'trust_source': trust_source,
    }


def get_active_project_path(claude_session_id: str | None = None) -> str | None:
    """
    Get the active project path for the current instance.

    Priority chain (NO CWD FALLBACK):
    1. instance_projects/{instance_id}.json - AUTHORITATIVE (updated by project-switch)
    2. active_work_{claude_session_id}.json - fallback (may be stale after project-switch)

    Args:
        claude_session_id: Optional Claude Code conversation UUID (from hook input)

    Returns:
        Absolute path to the project, or None if cannot be resolved.
    """
    # Try empirica import first
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        return R.project_path(claude_session_id)
    except ImportError:
        pass

    # Fallback implementation
    active_work_path = None
    instance_path = None

    # Read active_work file (if claude_session_id provided)
    if claude_session_id:
        active_work_file = Path.home() / '.empirica' / f'active_work_{claude_session_id}.json'
        if active_work_file.exists():
            try:
                with open(active_work_file) as f:
                    data = json.load(f)
                    active_work_path = data.get('project_path')
            except Exception:
                pass

    # Read instance_projects (TMUX_PANE-based) - AUTHORITATIVE source
    instance_id = get_instance_id()
    if instance_id:
        instance_file = Path.home() / '.empirica' / 'instance_projects' / f'{instance_id}.json'
        if instance_file.exists():
            try:
                with open(instance_file) as f:
                    data = json.load(f)
                    instance_path = data.get('project_path')
            except Exception:
                pass

    # PRIORITY: instance_projects wins (updated by project-switch)
    if instance_path:
        return instance_path

    # Fallback: active_work
    if active_work_path:
        return active_work_path

    return None


def _session_from_transaction(project_path):
    """Try to get session_id from active transaction file. Returns session_id or None."""
    if not project_path:
        return None
    suffix = _get_instance_suffix()
    tx_file = Path(project_path) / '.empirica' / f'active_transaction{suffix}.json'
    if not tx_file.exists():
        return None
    try:
        with open(tx_file) as f:
            tx_data = json.load(f)
        if tx_data.get('status') == 'open':
            return tx_data.get('session_id')
    except Exception:
        pass
    return None


def _session_from_json_file(file_path, key='empirica_session_id'):
    """Try to get session_id from a JSON file. Returns session_id or None."""
    if not file_path.exists():
        return None
    try:
        with open(file_path) as f:
            return json.load(f).get(key) or None
    except Exception:
        return None


def get_active_session_id(claude_session_id: str | None = None) -> str | None:
    """
    Get the active Empirica session ID for the current instance.

    Priority chain:
    1. Active transaction (TRANSACTION-FIRST - transaction survives compaction)
    2. active_work file (from project-switch/PREFLIGHT)
    3. instance_projects file (TMUX-based fallback)
    4. TTY session
    5. Generic active_work.json

    Args:
        claude_session_id: Optional Claude Code conversation UUID (from hook input)

    Returns:
        Empirica session UUID, or None if no active session found.
    """
    # Try empirica import first
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        return R.session_id(claude_session_id)
    except ImportError:
        pass

    # Fallback implementation
    project_path = get_active_project_path(claude_session_id)

    # Priority 1: Active transaction
    result = _session_from_transaction(project_path)
    if result:
        return result

    # Priority 2: active_work file
    if claude_session_id:
        result = _session_from_json_file(
            Path.home() / '.empirica' / f'active_work_{claude_session_id}.json')
        if result:
            return result

    # Priority 3: instance_projects (TMUX-based)
    instance_id = get_instance_id()
    if instance_id:
        result = _session_from_json_file(
            Path.home() / '.empirica' / 'instance_projects' / f'{instance_id}.json')
        if result:
            return result

    # Priority 4: TTY session
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        tty_session = R.tty_session()
        if tty_session:
            session_id = tty_session.get('empirica_session_id')
            if session_id:
                return session_id
    except Exception:
        pass

    # Priority 5: Generic active_work.json
    return _session_from_json_file(Path.home() / '.empirica' / 'active_work.json')


# ---------------------------------------------------------------------------
# Delegated to canonical session_resolver.py (v1.8.14 resolver dedup)
# Inline fallbacks removed — these are now single-source-of-truth in
# empirica.utils.session_resolver. If empirica is not importable (bare
# plugin without pip install), the InstanceResolver fallback at the top
# of this file already handles it.
# ---------------------------------------------------------------------------

try:
    from empirica.utils.session_resolver import (
        _find_git_root,
        _read_json_file,
        _scan_workspace_for_project,
        has_valid_db,
    )
except ImportError:
    # Minimal fallbacks for bare-plugin environments
    def has_valid_db(project_path: Path) -> bool:
        db_path = project_path / '.empirica' / 'sessions' / 'sessions.db'
        if not db_path.exists():
            return False
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("SELECT 1 FROM sessions LIMIT 1")
            conn.close()
            return True
        except Exception:
            return False

    def _find_git_root() -> Path | None:
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--show-toplevel'],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return Path(result.stdout.strip())
        except Exception:
            pass
        return None

    def _read_json_file(path: Path) -> dict | None:
        try:
            if path.exists():
                with open(path) as f:
                    return json.load(f)
        except Exception:
            pass
        return None

    def _scan_workspace_for_project(instance_id: str | None) -> Path | None:
        return None  # Minimal fallback — workspace scan needs full empirica


def _validated_project_path(path_str):
    """Return Path if path_str points to a valid project, else None."""
    if not path_str:
        return None
    p = Path(path_str)
    return p if has_valid_db(p) else None


def _collect_candidate_paths(claude_session_id, instance_id):
    """Collect project paths from active_work and instance_projects files.

    Returns a set of path strings.
    """
    candidates = set()
    if claude_session_id:
        aw_file = Path.home() / '.empirica' / f'active_work_{claude_session_id}.json'
        data = _read_json_file(aw_file)
        if data and data.get('project_path'):
            candidates.add(data['project_path'])
    if instance_id:
        ip_file = Path.home() / '.empirica' / 'instance_projects' / f'{instance_id}.json'
        data = _read_json_file(ip_file)
        if data and data.get('project_path'):
            candidates.add(data['project_path'])
    return candidates


def _find_open_transaction_project(candidate_paths, suffix):
    """Check candidates for an open transaction file. Returns Path or None."""
    for cpath in candidate_paths:
        tx_file = Path(cpath) / '.empirica' / f'active_transaction{suffix}.json'
        tx_data = _read_json_file(tx_file)
        if tx_data and tx_data.get('status') == 'open':
            tx_project = tx_data.get('project_path', cpath)
            result = _validated_project_path(tx_project)
            if result:
                return result
    return None


def _find_from_file_sources(claude_session_id, instance_id):
    """Priority 3-4: instance_projects then active_work. Returns Path or None."""
    if instance_id:
        ip_file = Path.home() / '.empirica' / 'instance_projects' / f'{instance_id}.json'
        data = _read_json_file(ip_file)
        result = _validated_project_path(data.get('project_path') if data else None)
        if result:
            return result

    if claude_session_id:
        aw_file = Path.home() / '.empirica' / f'active_work_{claude_session_id}.json'
        data = _read_json_file(aw_file)
        result = _validated_project_path(data.get('project_path') if data else None)
        if result:
            return result

    return None


def _find_cwd_fallback():
    """CWD fallback: check CWD and git root. Returns Path."""
    cwd = Path.cwd()
    if has_valid_db(cwd):
        return cwd
    git_root = _find_git_root()
    if git_root and has_valid_db(git_root):
        return git_root
    return cwd  # Return CWD even without valid DB (session-init creates it)


def find_project_root(
    claude_session_id: str | None = None,
    *,
    check_compact_handoff: bool = False,
    allow_workspace_scan: bool = True,
    allow_cwd_fallback: bool = False,
    allow_git_root: bool = False,
) -> Path | None:
    """
    Comprehensive project root resolution for hooks.

    Delegates to canonical empirica.utils.session_resolver.find_project_root
    when available (v1.8.14 resolver dedup). Falls back to inline implementation
    for bare-plugin environments.

    Unified priority chain (highest to lowest):
    1. Compact handoff file (only if check_compact_handoff=True, for post-compact)
    2. Open transaction file (AUTHORITATIVE during transaction)
    3. active_work_{claude_session_id}.json
    4. instance_projects/{instance_id}.json
    5. Workspace scan (if allow_workspace_scan=True)
    6. EMPIRICA_WORKSPACE_ROOT env var
    7. Git repo root (if allow_git_root=True)
    8. CWD (if allow_cwd_fallback=True, for session-init only)

    Args:
        claude_session_id: Claude Code conversation UUID from hook input
        check_compact_handoff: Check compact handoff file (post-compact only)
        allow_workspace_scan: Scan workspace.db for projects with open transactions
        allow_cwd_fallback: Fall back to CWD as last resort
        allow_git_root: Try git repo root before CWD

    Returns:
        Path to project root, or None if cannot be resolved.
    """
    # Delegate to canonical when available (v1.8.14 resolver dedup)
    try:
        from empirica.utils.session_resolver import find_project_root as _canonical_fpr
        return _canonical_fpr(
            claude_session_id,
            check_compact_handoff=check_compact_handoff,
            allow_workspace_scan=allow_workspace_scan,
            allow_cwd_fallback=allow_cwd_fallback,
            allow_git_root=allow_git_root,
        )
    except ImportError:
        pass  # Fall through to inline implementation

    instance_id = get_instance_id()
    suffix = _get_instance_suffix()

    # Priority 1: Compact handoff (post-compact only)
    if check_compact_handoff and instance_id:
        data = _read_json_file(Path.home() / '.empirica' / f'compact_handoff{suffix}.json')
        result = _validated_project_path(data.get('project_path') if data else None)
        if result:
            return result

    # Priority 2: Open transaction file
    candidate_paths = _collect_candidate_paths(claude_session_id, instance_id)
    result = _find_open_transaction_project(candidate_paths, suffix)
    if result:
        return result

    # Priority 3-4: instance_projects / active_work
    result = _find_from_file_sources(claude_session_id, instance_id)
    if result:
        return result

    # Priority 5: Workspace scan
    if allow_workspace_scan:
        ws_result = _scan_workspace_for_project(instance_id)
        if ws_result:
            return ws_result

    # Priority 6: EMPIRICA_WORKSPACE_ROOT env var
    ws_root = os.environ.get('EMPIRICA_WORKSPACE_ROOT')
    if ws_root and has_valid_db(Path(ws_root)):
        return Path(ws_root)

    # Priority 7: Git repo root
    if allow_git_root:
        git_root = _find_git_root()
        if git_root and has_valid_db(git_root):
            return git_root

    # Priority 8: CWD (last resort)
    if allow_cwd_fallback:
        return _find_cwd_fallback()

    return None
