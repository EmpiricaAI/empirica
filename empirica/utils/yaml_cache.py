"""Re-parsing an unchanged file is work that buys nothing, and at 2 Hz it is a defect.

Measured on David's box: `empirica tui` (PID 173117) had held **54.3% of a core for
17.8 days**. One `aggregate_all()` cost 1.7–2.0 s wall, and a cProfile run attributed
**3.24 s of 4.08 s (79%) to 417 `yaml.safe_load` calls** — for 11 instances. Four
independent call sites each open the SAME `<project>/.empirica/project.yaml`, on every
instance, on every refresh:

    session_resolver.ai_id            -> ai_id
    compliance_view._project_id_from_path -> project_id
    services_view._project_id_from_path   -> project_id   (a copy of the line above)
    project_cockpit_config.project_cockpit  -> cockpit

Nothing was wrong with any one of them. The cost is that none of them knew the other
three existed, and the file had not changed in days.

**The key is (mtime_ns, size), not a TTL.** A TTL answers *"is this old?"*; the caller's
real question is *"has this changed?"* — and those come apart exactly where it matters:
a TTL re-parses a file nobody touched (the whole cost here) and serves a file someone
just edited (the whole risk). Keying on the stat means an edit is picked up on the very
next call, and an untouched file is never parsed twice.

**Non-raising, like every site it replaces.** A missing or malformed project.yaml
returns `{}` — same contract the four call sites already had, so this cannot change
behaviour, only cost. The parse failure is logged at debug rather than swallowed
outright, because a project.yaml that has been unparseable for a week should be
findable by someone who goes looking.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Bound on distinct files held. The cockpit sees one project.yaml per instance
#: (~11 here), so this is generous. It exists because the TUI is a long-lived
#: process and an unbounded dict in a 17-day process is a leak, not a cache.
_MAX_ENTRIES = 256

_lock = threading.Lock()
#: path -> (mtime_ns, size, parsed)
_cache: dict[str, tuple[int, int, dict[str, Any]]] = {}


def load_yaml_cached(path: str | Path) -> dict[str, Any]:
    """Parse a YAML file, reusing the previous parse while it is unchanged.

    Returns ``{}`` for a missing file, a parse error, or a document that is not a
    mapping — the contract every caller here already had.

    The cache is invalidated by the file's own ``(mtime_ns, size)``, so a write is
    visible to the next call. There is no time-based expiry to wait out.
    """
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        # Missing (or unreadable) — drop any entry so a later re-creation is not
        # served from a cache the file no longer backs.
        with _lock:
            _cache.pop(str(p), None)
        return {}

    key = str(p)
    stamp = (st.st_mtime_ns, st.st_size)

    with _lock:
        hit = _cache.get(key)
        if hit is not None and (hit[0], hit[1]) == stamp:
            return hit[2]

    # Parse outside the lock: yaml.safe_load on a large file is the expensive part
    # and holding the lock across it would serialise every cockpit thread behind
    # the slowest file.
    try:
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug(f"yaml_cache: {p} did not parse, treating as empty: {e}")
        data = None

    parsed: dict[str, Any] = data if isinstance(data, dict) else {}

    with _lock:
        if len(_cache) >= _MAX_ENTRIES and key not in _cache:
            # Cheap eviction — this is a working-set cache over a handful of
            # project files, not an LRU workload worth the bookkeeping.
            _cache.clear()
        _cache[key] = (stamp[0], stamp[1], parsed)
    return parsed


def load_project_yaml(project_path: str | Path | None) -> dict[str, Any]:
    """`<project_path>/.empirica/project.yaml`, cached. ``{}`` when absent."""
    if not project_path:
        return {}
    return load_yaml_cached(Path(project_path) / ".empirica" / "project.yaml")


def clear_cache() -> None:
    """Drop everything. For tests, and for any caller that has reason to distrust
    the stat (a network filesystem with coarse timestamps, say)."""
    with _lock:
        _cache.clear()
