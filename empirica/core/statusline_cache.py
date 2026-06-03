"""
Empirica Statusline Cache - Persistent multi-instance cache for statusline state

Provides a central cache at ~/.empirica/statusline_cache/ that:
1. Survives system reboots (not in /tmp)
2. Supports multi-instance isolation (per tmux pane)
3. Supports multi-project isolation (per project)
4. Uses file locking for concurrent access safety

Used by:
- statusline_empirica.py (reads)
- CASCADE phase handlers (writes)
- empirica_action_hooks.py (writes)

Author: Claude Code
Date: 2026-02-04
"""

import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Platform-specific file locking
if sys.platform == 'win32':
    import msvcrt
    fcntl = None
else:
    import fcntl
    msvcrt = None


# Cache directory - persistent location
CACHE_DIR = Path.home() / '.empirica' / 'statusline_cache'


# Cross-platform file locking helpers
def _lock_file(f, exclusive: bool = True):
    """Lock a file (exclusive or shared) in a cross-platform way."""
    if sys.platform == 'win32':
        # Windows: use msvcrt
        mode = msvcrt.LK_NBLCK if exclusive else msvcrt.LK_NBRLCK
        try:
            msvcrt.locking(f.fileno(), mode, 1)
        except OSError:
            # If non-blocking lock fails, try blocking
            mode = msvcrt.LK_LOCK if exclusive else msvcrt.LK_RLCK
            msvcrt.locking(f.fileno(), mode, 1)
    else:
        # Unix: use fcntl
        lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(f.fileno(), lock_type)


def _unlock_file(f):
    """Unlock a file in a cross-platform way."""
    if sys.platform == 'win32':
        # Windows: use msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        # Unix: use fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


@dataclass
class StatuslineCacheEntry:
    """Single cache entry for a pane+project combination."""

    # Identification
    session_id: str
    ai_id: str
    instance_id: str  # tmux pane or process identifier
    project_id: str | None = None
    project_name: str | None = None
    project_path: str | None = None

    # Epistemic state
    phase: str | None = None  # PREFLIGHT, CHECK, POSTFLIGHT
    vectors: dict[str, float] | None = None
    gate_decision: str | None = None  # proceed, investigate
    deltas: dict[str, float] | None = None  # PREFLIGHT→POSTFLIGHT learning delta

    # Counts
    open_goals: int = 0
    open_unknowns: int = 0
    goal_linked_unknowns: int = 0

    # Computed metrics
    confidence: float | None = None

    # Timestamps
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'StatuslineCacheEntry':
        """Create from dictionary."""
        # Handle missing fields gracefully. Missing ai_id means the entry
        # was written by code older than the mesh ai_id convention — return
        # '' rather than substituting our own identity for the original
        # writer's. The statusline degrades gracefully on empty ai_id.
        return cls(
            session_id=data.get('session_id', ''),
            ai_id=data.get('ai_id', ''),
            instance_id=data.get('instance_id', ''),
            project_id=data.get('project_id'),
            project_name=data.get('project_name'),
            project_path=data.get('project_path'),
            phase=data.get('phase'),
            vectors=data.get('vectors'),
            gate_decision=data.get('gate_decision'),
            deltas=data.get('deltas'),
            open_goals=data.get('open_goals', 0),
            open_unknowns=data.get('open_unknowns', 0),
            goal_linked_unknowns=data.get('goal_linked_unknowns', 0),
            confidence=data.get('confidence'),
            updated_at=data.get('updated_at', 0.0),
        )


def get_instance_id() -> str:
    """
    Get a unique identifier for the current instance/pane.

    Delegates to session_resolver.get_instance_id() (the canonical implementation)
    with a PID-based fallback for environments where no isolation signal exists.
    """
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        result = R.instance_id()
        if result:
            return result
    except ImportError:
        pass

    # Fallback: process ID (doesn't persist, but always available)
    return f"pid_{os.getpid()}"


def get_project_hash(project_path: str) -> str:
    """Get a short hash for a project path."""
    if not project_path:
        return "global"
    return hashlib.sha256(project_path.encode()).hexdigest()[:8]


def get_cache_path(instance_id: str, project_path: str | None = None) -> Path:
    """Get the cache file path for an instance+project combination."""
    project_hash = get_project_hash(project_path or "")
    return CACHE_DIR / f"{instance_id}_{project_hash}.json"


class StatuslineCache:
    """
    Central cache for statusline state with file locking.

    Usage:
        cache = StatuslineCache()

        # Write (from CASCADE handlers)
        cache.write(StatuslineCacheEntry(
            session_id="...",
            ai_id="empirica",  # canonical: derive from InstanceResolver.ai_id()
            instance_id=get_instance_id(),
            phase="PREFLIGHT",
            vectors={...},
        ))

        # Read (from statusline)
        entry = cache.read()
        if entry:
            print(f"Phase: {entry.phase}, Know: {entry.vectors.get('know')}")
    """

    def __init__(self, instance_id: str | None = None, project_path: str | None = None):
        """
        Initialize cache for a specific instance and project.

        Args:
            instance_id: Pane/instance identifier. Auto-detected if None.
            project_path: Project root path. Auto-detected if None.
        """
        self.instance_id = instance_id or get_instance_id()
        self.project_path = project_path

        # Ensure cache directory exists
        CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

    @property
    def cache_file(self) -> Path:
        """Get the cache file path."""
        return get_cache_path(self.instance_id, self.project_path)

    def write(self, entry: StatuslineCacheEntry) -> bool:
        """
        Write cache entry with file locking.

        Args:
            entry: Cache entry to write

        Returns:
            True if write succeeded
        """
        try:
            # Update timestamp
            entry.updated_at = time.time()
            entry.instance_id = self.instance_id
            if self.project_path:
                entry.project_path = self.project_path

            # Write with exclusive lock
            with open(self.cache_file, 'w') as f:
                _lock_file(f, exclusive=True)
                try:
                    json.dump(entry.to_dict(), f, indent=2)
                finally:
                    _unlock_file(f)
            os.chmod(self.cache_file, 0o600)

            return True
        except Exception:
            # Silent fail - statusline should never crash
            return False

    def read(self, max_age: float = 300.0) -> StatuslineCacheEntry | None:
        """
        Read cache entry with file locking.

        Args:
            max_age: Maximum age in seconds. Returns None if older.

        Returns:
            Cache entry or None if not found/stale
        """
        if not self.cache_file.exists():
            return None

        try:
            with open(self.cache_file) as f:
                _lock_file(f, exclusive=False)
                try:
                    data = json.load(f)
                finally:
                    _unlock_file(f)

            entry = StatuslineCacheEntry.from_dict(data)

            # Check staleness
            age = time.time() - entry.updated_at
            if age > max_age:
                return None

            return entry
        except Exception:
            return None

    def update(self, **kwargs) -> bool:
        """
        Update specific fields in the cache.

        Args:
            **kwargs: Fields to update

        Returns:
            True if update succeeded
        """
        entry = self.read(max_age=float('inf'))  # Don't check staleness for updates
        if entry is None:
            # Create new entry — derive ai_id from the canonical mesh chain
            # (project.yaml → basename) rather than hardcoding a default.
            # We DO know our own identity here, unlike from_dict.
            ai_id = kwargs.get('ai_id')
            if not ai_id:
                try:
                    from empirica.utils.session_resolver import InstanceResolver
                    ai_id = InstanceResolver.ai_id() or ''
                except Exception:
                    ai_id = ''
            entry = StatuslineCacheEntry(
                session_id=kwargs.get('session_id', ''),
                ai_id=ai_id,
                instance_id=self.instance_id,
            )

        # Update provided fields
        for key, value in kwargs.items():
            if hasattr(entry, key):
                setattr(entry, key, value)

        return self.write(entry)

    def clear(self) -> bool:
        """Delete the cache file."""
        try:
            if self.cache_file.exists():
                self.cache_file.unlink()
            return True
        except Exception:
            return False

    @classmethod
    def clear_all(cls, older_than: float = 86400.0) -> int:
        """
        Clear stale cache files.

        Args:
            older_than: Delete files older than this (seconds). Default 24h.

        Returns:
            Number of files deleted
        """
        count = 0
        try:
            for cache_file in CACHE_DIR.glob("*.json"):
                try:
                    if time.time() - cache_file.stat().st_mtime > older_than:
                        cache_file.unlink()
                        count += 1
                except Exception:
                    pass
        except Exception:
            pass
        return count

    @classmethod
    def list_active(cls, max_age: float = 300.0) -> list:
        """
        List all active cache entries.

        Args:
            max_age: Maximum age in seconds

        Returns:
            List of (instance_id, project_path, entry) tuples
        """
        active = []
        try:
            for cache_file in CACHE_DIR.glob("*.json"):
                try:
                    age = time.time() - cache_file.stat().st_mtime
                    if age <= max_age:
                        with open(cache_file) as f:
                            data = json.load(f)
                        entry = StatuslineCacheEntry.from_dict(data)
                        active.append((entry.instance_id, entry.project_path, entry))
                except Exception:
                    pass
        except Exception:
            pass
        return active


# Convenience functions for direct usage

def write_statusline_cache(
    session_id: str,
    ai_id: str,
    phase: str | None = None,
    vectors: dict[str, float] | None = None,
    gate_decision: str | None = None,
    project_path: str | None = None,
    project_name: str | None = None,
    open_goals: int = 0,
    open_unknowns: int = 0,
    goal_linked_unknowns: int = 0,
    confidence: float | None = None,
    deltas: dict[str, float] | None = None,
) -> bool:
    """
    Write to statusline cache.

    Use this from CASCADE phase handlers and hooks.
    """
    cache = StatuslineCache(project_path=project_path)
    entry = StatuslineCacheEntry(
        session_id=session_id,
        ai_id=ai_id,
        instance_id=get_instance_id(),
        project_name=project_name,
        project_path=project_path,
        phase=phase,
        vectors=vectors,
        gate_decision=gate_decision,
        deltas=deltas,
        open_goals=open_goals,
        open_unknowns=open_unknowns,
        goal_linked_unknowns=goal_linked_unknowns,
        confidence=confidence,
    )
    return cache.write(entry)


def read_statusline_cache(
    project_path: str | None = None,
    max_age: float = 300.0
) -> StatuslineCacheEntry | None:
    """
    Read from statusline cache.

    Use this from statusline script for fast rendering.
    """
    cache = StatuslineCache(project_path=project_path)
    return cache.read(max_age=max_age)


def update_statusline_vectors(
    vectors: dict[str, float],
    project_path: str | None = None
) -> bool:
    """
    Update just the vectors in the cache.

    Use this for real-time vector updates.
    """
    cache = StatuslineCache(project_path=project_path)
    return cache.update(vectors=vectors)


def update_statusline_phase(
    phase: str,
    gate_decision: str | None = None,
    project_path: str | None = None
) -> bool:
    """
    Update the CASCADE phase in the cache.

    Use this after phase transitions.
    """
    cache = StatuslineCache(project_path=project_path)
    kwargs = {'phase': phase}
    if gate_decision:
        kwargs['gate_decision'] = gate_decision
    return cache.update(**kwargs)
