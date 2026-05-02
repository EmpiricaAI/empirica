"""
Memory Manager — manages the CC memory/*.md KV cache layer.

5-tier memory hierarchy:
  L1: MEMORY.md index (~200 lines, always loaded)
  L2: memory/*.md files (loaded on relevance by CC)
  L3: Qdrant eidetic/episodic (loaded at PREFLIGHT)
  L4: sessions.db (queried on demand)
  L5: git notes (portable, cold storage)

This module manages L1 and L2, driven by the transaction cycle:
  POSTFLIGHT → update hot cache, promote high-value facts, demote stale files

Extracted from session-end-postflight.py for shared use by both
POSTFLIGHT handler (workflow_commands.py) and session-end hook.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# Markers for auto-generated section in MEMORY.md
MEMORY_AUTO_START = "## EPISTEMIC FOCUS (Confidence-Ranked)"
MEMORY_AUTO_END = "---\n📊"
# Max lines for auto section (leaves room for manual notes within CC's 200 line cap)
MEMORY_AUTO_MAX_LINES = 100


def get_memory_dir(project_path: str | None = None) -> Path | None:
    """Resolve the CC memory directory for a project.

    CC stores memories at: ~/.claude/projects/{path-key}/memory/
    where path-key is the absolute path with / replaced by -

    Args:
        project_path: Explicit project path. If None, uses git root.

    Returns:
        Path to memory directory, or None if not found.
    """
    candidates = []

    if project_path:
        candidates.append(Path(project_path).resolve())

    # Try git root — Claude Code can reset CWD between hook invocations
    # (subprocess parent-CWD inheritance is unreliable here), so we
    # don't trust CWD as a project-resolution signal. Git root walks
    # up from the actual filesystem anchor and is stable.
    try:
        import subprocess
        result = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if result.returncode == 0:
            candidates.append(Path(result.stdout.strip()).resolve())
    except Exception:
        pass

    for candidate in candidates:
        project_key = str(candidate).replace('/', '-')
        memory_dir = Path.home() / '.claude' / 'projects' / project_key / 'memory'
        if memory_dir.exists():
            return memory_dir

    return None


def get_memory_md_path(project_path: str | None = None) -> Path | None:
    """Get the MEMORY.md path for the current project."""
    memory_dir = get_memory_dir(project_path)
    if memory_dir:
        return memory_dir / 'MEMORY.md'
    return None


def resolve_project_id(session_id: str, db_path: str | None = None) -> str | None:
    """Resolve project_id from session_id via DB lookup."""
    if not db_path:
        # Use canonical resolver instead of guessing from CWD —
        # CWD-relative `.empirica/sessions/sessions.db` could route to
        # a sibling project's DB under tmux/cwd-mismatch conditions,
        # which leaks artifacts across project boundaries.
        try:
            from empirica.config.path_resolver import get_session_db_path
            resolved = get_session_db_path()
            if resolved.exists():
                db_path = str(resolved)
        except Exception:
            pass
        if not db_path:
            return None

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT project_id FROM sessions WHERE session_id = ?",
            (session_id,)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return row[0]
    except Exception:
        pass
    return None


def fetch_ranked_artifacts(session_id: str, db_path: str | None = None,
                           limit: int = 20) -> dict:
    """Fetch recent artifacts from DB, scoped to project.

    Returns dict with findings, unknowns, dead_ends, goals, mistakes.
    """
    result = {'findings': [], 'unknowns': [], 'dead_ends': [], 'goals': [], 'mistakes': []}

    if not db_path:
        # Canonical resolver only — see resolve_project_id() above for why
        # CWD-relative DB lookup is unsafe.
        try:
            from empirica.config.path_resolver import get_session_db_path
            resolved = get_session_db_path()
            if resolved.exists():
                db_path = str(resolved)
        except Exception:
            pass
        if not db_path:
            return result

    try:
        project_id = resolve_project_id(session_id, db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Build project filter
        if project_id:
            pf = "WHERE project_id = ?"
            pf_args = (project_id,)
            uf = "WHERE is_resolved = 0 AND project_id = ?"
            uf_args = (project_id,)
            gf = "WHERE is_completed = 0 AND project_id = ?"
            gf_args = (project_id,)
        else:
            pf, pf_args = "", ()
            uf, uf_args = "WHERE is_resolved = 0", ()
            gf, gf_args = "WHERE is_completed = 0", ()

        # Findings
        cursor.execute(f"""
            SELECT finding, impact, created_timestamp FROM project_findings
            {pf} ORDER BY created_timestamp DESC LIMIT ?
        """, (*pf_args, limit))
        for row in cursor.fetchall():
            result['findings'].append({
                'finding': row[0], 'impact': row[1] or 0.5,
                'created_timestamp': row[2],
            })

        # Open unknowns
        cursor.execute(f"""
            SELECT unknown, created_timestamp FROM project_unknowns
            {uf} ORDER BY created_timestamp DESC LIMIT ?
        """, (*uf_args, 10))
        for row in cursor.fetchall():
            result['unknowns'].append({'unknown': row[0], 'created_timestamp': row[1]})

        # Dead-ends
        cursor.execute(f"""
            SELECT approach, why_failed, created_timestamp FROM project_dead_ends
            {pf} ORDER BY created_timestamp DESC LIMIT ?
        """, (*pf_args, 10))
        for row in cursor.fetchall():
            result['dead_ends'].append({
                'approach': row[0], 'why_failed': row[1],
                'created_timestamp': row[2],
            })

        # Open goals
        cursor.execute(f"""
            SELECT objective, status FROM project_goals
            {gf} ORDER BY created_timestamp DESC LIMIT ?
        """, (*gf_args, 10))
        for row in cursor.fetchall():
            result['goals'].append({'objective': row[0], 'status': row[1]})

        # Mistakes
        cursor.execute(f"""
            SELECT mistake, prevention, created_timestamp FROM project_mistakes
            {pf} ORDER BY created_timestamp DESC LIMIT ?
        """, (*pf_args, 5))
        for row in cursor.fetchall():
            result['mistakes'].append({
                'mistake': row[0], 'prevention': row[1],
                'created_timestamp': row[2],
            })

        conn.close()
    except Exception as e:
        logger.debug(f"fetch_ranked_artifacts failed: {e}")

    return result


def _format_auto_section(artifacts: dict, session_id: str) -> str:
    """Format ranked artifacts into the MEMORY.md auto-generated section."""
    auto_lines = []
    auto_lines.append(f"\n{MEMORY_AUTO_START}\n")

    ranked = sorted(artifacts['findings'], key=lambda f: f.get('impact', 0.5), reverse=True)

    critical = [f for f in ranked if f.get('impact', 0.5) > 0.7]
    if critical:
        auto_lines.append("### Critical (weight > 0.7)")
        for f in critical[:5]:
            impact = f.get('impact', 0.5)
            text = f['finding'][:100].replace('\n', ' ')
            auto_lines.append(f"- [{impact:.2f}] **Finding:** {text}...")

    important = [f for f in ranked if 0.4 <= f.get('impact', 0.5) <= 0.7]
    if important:
        auto_lines.append("\n### Important (weight 0.4-0.7)")
        for f in important[:5]:
            impact = f.get('impact', 0.5)
            text = f['finding'][:100].replace('\n', ' ')
            auto_lines.append(f"- [{impact:.2f}] **Finding:** {text}...")

    if artifacts['dead_ends']:
        auto_lines.append("\n### Dead-Ends (avoid re-trying)")
        for d in artifacts['dead_ends'][:3]:
            approach = d['approach'][:80].replace('\n', ' ')
            auto_lines.append(f"- [{0.5 + len(d.get('why_failed', '')) / 500:.2f}] **Dead-End:** {approach}")

    if artifacts['mistakes']:
        auto_lines.append("\n### Mistakes (prevention strategies)")
        for m in artifacts['mistakes'][:3]:
            text = m['mistake'][:80].replace('\n', ' ')
            auto_lines.append(f"- [{0.50:.2f}] **Mistake:** {text}")

    if artifacts['goals']:
        auto_lines.append(f"\n### Active Goals ({len(artifacts['goals'])})")
        for g in artifacts['goals'][:5]:
            obj = g['objective'][:80].replace('\n', ' ')
            auto_lines.append(f"- [{0.44:.2f}] **Goal:** {obj}...")

    total_items = sum(len(v) for v in artifacts.values())
    auto_lines.append("\n---")
    auto_lines.append(f"📊 **{total_items} items ranked** | For deeper context:")
    auto_lines.append(f"- `empirica project-bootstrap --session-id {session_id[:8]}` (full load + subtasks)")
    auto_lines.append("- `empirica project-search --task \"<query>\"` (Qdrant semantic)")
    auto_lines.append("- `git notes show --ref=breadcrumbs HEAD` (session narrative)")

    return '\n'.join(auto_lines) + '\n'


def _strip_stale_markers(content: str) -> str:
    """Remove stale HTML comment markers from previous auto-section formats."""
    import re
    return re.sub(r'<!--\s*empirica-auto-\w+\s*-->\n?', '', content)


def _collapse_blank_runs(content: str, max_consecutive: int = 2) -> str:
    """Collapse runs of blank lines down to max_consecutive."""
    import re
    pattern = r'\n{' + str(max_consecutive + 2) + r',}'
    return re.sub(pattern, '\n' * (max_consecutive + 1), content)


def _replace_auto_section(existing: str, auto_section: str) -> str:
    """Replace or append the auto-generated section in MEMORY.md content."""
    # Clean stale markers from previous formats
    existing = _strip_stale_markers(existing)

    if MEMORY_AUTO_START not in existing:
        manual = existing.rstrip('\n') + '\n\n'
        return _collapse_blank_runs(manual + auto_section)

    start_idx = existing.index(MEMORY_AUTO_START)
    end_marker = "📊 **"
    if end_marker not in existing[start_idx:]:
        manual = existing[:start_idx].rstrip('\n') + '\n\n'
        return _collapse_blank_runs(manual + auto_section)

    end_search = existing.index(end_marker, start_idx)
    end_idx = existing.find('\n', end_search)
    if end_idx == -1:
        end_idx = len(existing)
    else:
        while end_idx < len(existing) - 1:
            next_line_end = existing.find('\n', end_idx + 1)
            if next_line_end == -1:
                next_line_end = len(existing)
            next_line = existing[end_idx + 1:next_line_end]
            if next_line.startswith('- `empirica') or next_line.startswith('- `git'):
                end_idx = next_line_end
            else:
                break
        end_idx += 1

    manual = existing[:start_idx].rstrip('\n') + '\n\n'
    after = existing[end_idx:]
    return _collapse_blank_runs(manual + auto_section + after)


def update_hot_cache(session_id: str, project_path: str | None = None,
                     db_path: str | None = None) -> bool:
    """Update MEMORY.md auto-generated section with ranked artifacts.

    Preserves manual content. Auto section is delimited by markers.
    Called at POSTFLIGHT (primary) and session-end (fallback).

    Returns True if MEMORY.md was updated.
    """
    memory_path = get_memory_md_path(project_path)
    if not memory_path:
        logger.debug("update_hot_cache: no memory path found")
        return False

    artifacts = fetch_ranked_artifacts(session_id, db_path)
    auto_section = _format_auto_section(artifacts, session_id)

    # Enforce line cap
    auto_section_lines = auto_section.count('\n')
    if auto_section_lines > MEMORY_AUTO_MAX_LINES:
        lines = auto_section.split('\n')
        auto_section = '\n'.join(lines[:MEMORY_AUTO_MAX_LINES]) + '\n'

    try:
        import fcntl
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = memory_path.parent / '.memory_lock'
        with open(lock_path, 'w') as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            existing = memory_path.read_text() if memory_path.exists() else "# Empirica Project Memory\n"
            updated = _replace_auto_section(existing, auto_section)
            memory_path.write_text(updated)
            logger.debug(f"Updated MEMORY.md hot cache ({auto_section_lines} lines)")
        return True
    except Exception as e:
        logger.warning(f"Failed to write MEMORY.md: {e}")
        return False


def get_memory_stats(project_path: str | None = None) -> dict:
    """Get stats about the CC memory layer for memory-report.

    Returns:
        dict with file_count, total_size_bytes, memory_md_lines,
        oldest_file, newest_file, auto_section_lines
    """
    memory_dir = get_memory_dir(project_path)
    if not memory_dir:
        return {"error": "Memory directory not found"}

    md_files = list(memory_dir.glob('*.md'))
    file_list: list[dict] = []
    stats: dict = {
        "memory_dir": str(memory_dir),
        "file_count": len(md_files),
        "total_size_bytes": sum(f.stat().st_size for f in md_files),
        "files": file_list,
    }

    # MEMORY.md specific stats
    memory_md = memory_dir / 'MEMORY.md'
    if memory_md.exists():
        content = memory_md.read_text()
        stats["memory_md_lines"] = content.count('\n')
        stats["memory_md_has_auto_section"] = MEMORY_AUTO_START in content
        if MEMORY_AUTO_START in content:
            auto_start = content.index(MEMORY_AUTO_START)
            auto_content = content[auto_start:]
            stats["auto_section_lines"] = auto_content.count('\n')
    else:
        stats["memory_md_lines"] = 0

    # Individual file stats
    for f in sorted(md_files, key=lambda p: p.stat().st_mtime, reverse=True):
        if f.name == 'MEMORY.md':
            continue
        file_list.append({
            "name": f.name,
            "size": f.stat().st_size,
            "modified": f.stat().st_mtime,
        })

    return stats


# =============================================================================
# Promotion: Qdrant eidetic facts → CC memory/*.md files
# =============================================================================

# Promotion thresholds
PROMOTE_MIN_CONFIDENCE = 0.7


def _slugify(text: str, max_len: int = 40) -> str:
    """Convert text to a filename-safe slug."""
    import re
    slug = text.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '_', slug)
    slug = slug.strip('_')
    return slug[:max_len]


def _get_promoted_tracker(memory_dir: Path) -> set[str]:
    """Read the set of already-promoted content hashes to avoid duplicates."""
    tracker_file = memory_dir / '.promoted_hashes'
    if tracker_file.exists():
        try:
            return set(tracker_file.read_text().strip().split('\n'))
        except Exception:
            pass
    return set()


def _save_promoted_tracker(memory_dir: Path, hashes: set[str]) -> None:
    """Save the promoted content hashes."""
    tracker_file = memory_dir / '.promoted_hashes'
    try:
        tracker_file.write_text('\n'.join(sorted(hashes)))
    except Exception:
        pass


def _try_promote_point(point, memory_dir: Path, promoted_hashes: set[str]) -> str | None:
    """Try to promote a single eidetic point to a memory file.

    Returns the filename if promoted, None otherwise.
    """
    import hashlib

    payload = point.payload or {}
    content = payload.get('content', '')
    if not content:
        return None

    content_hash = hashlib.md5(content.encode()).hexdigest()[:12]
    if content_hash in promoted_hashes:
        return None

    domain = payload.get('domain', '')
    confidence = payload.get('confidence', 0.5)
    confirmations = payload.get('confirmation_count', 1)

    first_line = content.split('\n')[0][:80]
    name = f"{domain}: {first_line}" if domain else first_line

    slug = _slugify(name)
    if not slug:
        slug = f"eidetic_{content_hash}"

    filename = f"promoted_{slug}.md"
    filepath = memory_dir / filename

    if filepath.exists():
        promoted_hashes.add(content_hash)
        return None

    memory_content = f"""---
name: {name[:80]}
description: Auto-promoted from eidetic memory (confidence: {confidence:.2f}, confirmed: {confirmations}x)
type: project
---

{content[:500]}

**Source:** Eidetic memory (auto-promoted at POSTFLIGHT)
**Confidence:** {confidence:.2f} | **Confirmations:** {confirmations}
"""

    try:
        filepath.write_text(memory_content)
        promoted_hashes.add(content_hash)
        logger.debug(f"Promoted eidetic fact to memory: {filename}")
        return filename
    except Exception as e:
        logger.warning(f"Failed to write promoted memory file: {e}")
        return None


def promote_eidetic_to_memory(
    project_id: str,
    project_path: str | None = None,
    min_confidence: float = PROMOTE_MIN_CONFIDENCE,
    max_promote: int = 3,
) -> list[str]:
    """Promote high-confidence eidetic facts to CC memory/*.md files.

    Queries Qdrant for eidetic facts meeting confidence threshold, checks
    against already-promoted hashes, and creates new memory files.

    Spam prevention: max 3 promotions per call + content hash dedup tracker.

    Args:
        project_id: Project UUID for Qdrant collection
        project_path: Explicit project path for memory dir resolution
        min_confidence: Minimum confidence to promote (default 0.7)
        max_promote: Max new files to create per call (prevents spam)

    Returns:
        List of created memory file names
    """
    if not project_id:
        logger.debug("promote_eidetic_to_memory: no project_id, skipping")
        return []

    memory_dir = get_memory_dir(project_path)
    if not memory_dir:
        return []

    # Query Qdrant for promotable facts
    try:
        from empirica.core.qdrant.collections import _eidetic_collection
        from empirica.core.qdrant.connection import _get_qdrant_client
    except ImportError:
        logger.info("Qdrant package not installed — promotion disabled")
        return []

    try:
        client = _get_qdrant_client()
        if not client:
            logger.warning("Qdrant connection failed — promotion skipped")
            return []

        collection = _eidetic_collection(project_id)

        # Scroll for high-confidence facts
        # Filter: confidence >= threshold AND type=fact
        # Note: confirmation_count filter removed — confirm_eidetic_fact
        # requires exact content hash match, but findings never repeat
        # verbatim, so confirmation_count stays at 1 for all facts.
        # Spam prevention via max_promote cap + content hash dedup.
        from qdrant_client.models import FieldCondition, Filter, MatchValue, Range

        results = client.scroll(
            collection_name=collection,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="confidence", range=Range(gte=min_confidence)),
                    FieldCondition(key="type", match=MatchValue(value="fact")),
                ]
            ),
            limit=20,
            with_payload=True,
            with_vectors=False,
        )

        if not results or not results[0]:
            return []

        points = results[0]
    except Exception as e:
        logger.debug(f"Qdrant scroll for promotion failed: {e}")
        return []

    # Check against already-promoted hashes
    promoted_hashes = _get_promoted_tracker(memory_dir)
    promoted_files = []

    for point in points:
        if len(promoted_files) >= max_promote:
            break
        result = _try_promote_point(point, memory_dir, promoted_hashes)
        if result:
            promoted_files.append(result)

    # Save updated tracker
    if promoted_files:
        _save_promoted_tracker(memory_dir, promoted_hashes)

    return promoted_files


# =============================================================================
# Demotion: Archive stale memory/*.md files
# =============================================================================

DEMOTE_STALE_DAYS = 30  # Days without modification before demotion


def demote_stale_memories(
    project_path: str | None = None,
    stale_days: int = DEMOTE_STALE_DAYS,
    dry_run: bool = False,
) -> list[str]:
    """Archive stale promoted memory files.

    Only auto-demotes promoted_*.md files (auto-managed).
    Manual memory files (user-created) are never auto-archived.

    Moves stale files to memory/_archive/ (reversible).

    Args:
        project_path: Explicit project path
        stale_days: Days without modification to consider stale
        dry_run: If True, return what would be archived without acting

    Returns:
        List of archived file names
    """
    import time

    memory_dir = get_memory_dir(project_path)
    if not memory_dir:
        return []

    archive_dir = memory_dir / '_archive'
    now = time.time()
    cutoff = now - (stale_days * 86400)
    archived = []

    for f in memory_dir.glob('promoted_*.md'):
        if f.stat().st_mtime < cutoff:
            if dry_run:
                archived.append(f.name)
            else:
                try:
                    archive_dir.mkdir(exist_ok=True)
                    dest = archive_dir / f.name
                    f.rename(dest)
                    archived.append(f.name)
                    logger.debug(f"Demoted stale memory file: {f.name}")
                except Exception as e:
                    logger.warning(f"Failed to archive {f.name}: {e}")

    # Update MEMORY.md index — remove references to archived files
    if archived and not dry_run:
        _remove_from_memory_index(memory_dir, archived)

    return archived


def _remove_from_memory_index(memory_dir: Path, filenames: list[str]) -> None:
    """Remove references to demoted files from MEMORY.md."""
    memory_md = memory_dir / 'MEMORY.md'
    if not memory_md.exists():
        return

    try:
        content = memory_md.read_text()
        for fname in filenames:
            # Remove lines that contain a markdown link or reference to this file
            # Match: [text](filename) or (filename) patterns, not bare substrings
            lines = content.split('\n')
            content = '\n'.join(
                line for line in lines
                if f'({fname})' not in line and f'({fname.replace(".md", "")}.md)' not in line
            )
        memory_md.write_text(content)
    except Exception as e:
        logger.warning(f"Failed to update MEMORY.md index after demotion: {e}")


# =============================================================================
# MEMORY.md Eviction: Keep auto-section under cap
# =============================================================================

def enforce_memory_md_cap(
    project_path: str | None = None,
    max_total_lines: int = 180,
) -> int:
    """Enforce line cap on MEMORY.md by trimming auto-generated section.

    Never touches manual content. Only trims the auto-generated section
    (between MEMORY_AUTO_START and the end markers).

    Args:
        project_path: Explicit project path
        max_total_lines: Max total lines for MEMORY.md (default 180, CC cap is 200)

    Returns:
        Number of lines evicted
    """
    memory_path = get_memory_md_path(project_path)
    if not memory_path or not memory_path.exists():
        return 0

    content = memory_path.read_text()

    # Strip stale markers and blank runs before measuring
    content = _strip_stale_markers(content)
    content = _collapse_blank_runs(content)

    total_lines = content.count('\n')

    if total_lines <= max_total_lines:
        # Write cleaned version back even if under cap
        try:
            memory_path.write_text(content)
        except Exception:
            pass
        return 0

    # Find auto section boundaries
    if MEMORY_AUTO_START not in content:
        return 0  # No auto section to trim

    start_idx = content.index(MEMORY_AUTO_START)
    manual_section = content[:start_idx]
    auto_section = content[start_idx:]

    manual_lines = manual_section.count('\n')
    auto_lines = auto_section.count('\n')

    # Calculate how many auto lines to keep
    available_for_auto = max_total_lines - manual_lines
    if available_for_auto < 10:
        available_for_auto = 10  # Always keep at least 10 auto lines

    if auto_lines <= available_for_auto:
        return 0

    # Trim auto section from the bottom (lowest-ranked items)
    auto_lines_list = auto_section.split('\n')
    trimmed = '\n'.join(auto_lines_list[:available_for_auto])
    evicted = auto_lines - available_for_auto

    # Write back
    try:
        memory_path.write_text(manual_section + trimmed + '\n')
        logger.debug(f"Evicted {evicted} lines from MEMORY.md auto section")
    except Exception as e:
        logger.warning(f"Failed to enforce MEMORY.md cap: {e}")
        return 0

    return evicted
