"""Claude Code's memory folder: read for memory-report, cleaned on request, never filled.

Claude Code owns ``~/.claude/projects/<path-key>/memory/`` and its MEMORY.md index: the user and feedback memories it
writes there are how-to-work guidance the epistemic graph does not hold. Project knowledge reaches the model through the
session-start and post-compact blocks and through ``project-search``, not through this folder.

Until goal ea3335ec, POSTFLIGHT and session end wrote here too: an ``## EPISTEMIC FOCUS (Confidence-Ranked)`` block in
MEMORY.md, and up to three ``promoted_*.md`` fact files a time. The block duplicated the session-start block with invented
weights and no ids, and no MEMORY.md on the measured machine ever indexed a promoted file (0 references across 20 memory
folders, 2026-11-13), so Claude Code never loaded one. What remains:

- ``get_memory_stats`` for ``memory-report``;
- ``demote_stale_memories``, the manual archive behind ``profile-prune --scope memory``, for promoted files left behind;
- ``strip_legacy_focus_block(s)``, which ``setup-claude-code`` runs to take the old block out of MEMORY.md, once.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

#: The header of the block POSTFLIGHT used to write into MEMORY.md.
MEMORY_AUTO_START = "## EPISTEMIC FOCUS (Confidence-Ranked)"

# A line of the old block, in the exact formats its writer used: one of its five sub-headings, a ranked item
# ("- [0.60] **Finding:** ..."), the footer ("📊 **55 items ranked**" and its three command lines) or a blank. Anything a person
# wrote, a "- [2026 plan](plan.md)" index line, a "### Notes" heading or a "- `git push` ..." tip, is not one (1.16.3 sweep).
_BLOCK_LINE = re.compile(
    r"^(?:$"
    r"|### (?:Critical \(weight > 0\.7\)|Important \(weight 0\.4-0\.7\)|Dead-Ends \(avoid re-trying\)"
    r"|Mistakes \(prevention strategies\)|Active Goals \(\d+\))$"
    r"|- \[\d\.\d\d\] \*\*(?:Finding|Dead-End|Mistake|Goal):\*\* "
    r"|📊 \*\*\d+ items ranked\*\*"
    r"|- `empirica project-(?:bootstrap|search) |- `git notes show --ref=breadcrumbs HEAD`)"
)


def get_memory_dir(project_path: str | None = None) -> Path | None:
    """Claude Code's memory folder for a project, or None when there is none.

    Claude Code keeps it at ``~/.claude/projects/{path-key}/memory/``, the key being the absolute path with ``/`` replaced
    by ``-``. Without ``project_path`` the git root is used: the working directory is not reliable inside hooks.
    """
    candidates = []
    if project_path:
        candidates.append(Path(project_path).resolve())
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=2, check=False
        )
        if result.returncode == 0:
            candidates.append(Path(result.stdout.strip()).resolve())
    except Exception:
        pass

    for candidate in candidates:
        memory_dir = Path.home() / ".claude" / "projects" / str(candidate).replace("/", "-") / "memory"
        if memory_dir.exists():
            return memory_dir
    return None


def get_memory_md_path(project_path: str | None = None) -> Path | None:
    """The MEMORY.md path for a project, or None when it has no memory folder."""
    memory_dir = get_memory_dir(project_path)
    return memory_dir / "MEMORY.md" if memory_dir else None


def get_memory_stats(project_path: str | None = None) -> dict:
    """File count, sizes and MEMORY.md line counts for ``memory-report``."""
    memory_dir = get_memory_dir(project_path)
    if not memory_dir:
        return {"error": "Memory directory not found"}

    md_files = list(memory_dir.glob("*.md"))
    stats: dict = {
        "memory_dir": str(memory_dir),
        "file_count": len(md_files),
        "total_size_bytes": sum(f.stat().st_size for f in md_files),
        "files": [
            {"name": f.name, "size": f.stat().st_size, "modified": f.stat().st_mtime}
            for f in sorted(md_files, key=lambda p: p.stat().st_mtime, reverse=True)
            if f.name != "MEMORY.md"
        ],
    }

    memory_md = memory_dir / "MEMORY.md"
    if memory_md.exists():
        content = memory_md.read_text()
        stats["memory_md_lines"] = content.count("\n")
        stats["memory_md_has_auto_section"] = MEMORY_AUTO_START in content
        if MEMORY_AUTO_START in content:
            stats["auto_section_lines"] = content[content.index(MEMORY_AUTO_START) :].count("\n")
    else:
        stats["memory_md_lines"] = 0
    return stats


# =============================================================================
# The one-time removal of the old block (run by setup-claude-code)
# =============================================================================


def _without_focus_block(text: str) -> str | None:
    """The text with the old block taken out, or None when it has none."""
    lines = text.split("\n")
    bare = [line.rstrip() for line in lines]  # a CRLF file or a trailing space must not hide the block
    try:
        start = bare.index(MEMORY_AUTO_START)
    except ValueError:
        return None

    end = start + 1
    while end < len(lines):
        line = bare[end]
        # The footer's rule ("---") belongs to the block only when the "📊" line follows it.
        if line == "---" and end + 1 < len(lines) and bare[end + 1].startswith("📊"):
            end += 1
        elif not _BLOCK_LINE.match(line):
            break
        end += 1
    # Blank lines at the block's end separate it from what follows: give them back.
    while end > start + 1 and bare[end - 1] == "":
        end -= 1
    # Blank lines before the header were the block's own spacing.
    while start > 0 and bare[start - 1] == "":
        start -= 1

    kept = lines[:start] + lines[end:]
    result = "\n".join(kept)
    if not result.endswith("\n"):
        result += "\n"
    return result


def strip_legacy_focus_block(memory_md: Path) -> bool:
    """Take the old EPISTEMIC FOCUS block out of one MEMORY.md. True when the file changed.

    Every other line is kept as it was, including lines written below the block. A file without the block is not
    rewritten. The write holds the ``.memory_lock`` the old writers used, so it cannot interleave with one still running.
    """
    if not memory_md.is_file():
        return False
    try:
        import fcntl
    except ImportError:  # Windows: no flock; the window is one read and one write
        fcntl = None  # type: ignore[assignment]

    try:
        with open(memory_md.parent / ".memory_lock", "w") as lock_fd:
            if fcntl is not None:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
            updated = _without_focus_block(memory_md.read_text())
            if updated is None:
                return False
            memory_md.write_text(updated)
            return True
    except OSError as e:
        logger.warning(f"Could not remove the old EPISTEMIC FOCUS block from {memory_md}: {e}")
        return False


def strip_legacy_focus_blocks(projects_root: Path | None = None) -> int:
    """Take the old block out of every project's MEMORY.md under ``~/.claude/projects``. Returns how many changed."""
    root = projects_root if projects_root is not None else Path.home() / ".claude" / "projects"
    if not root.is_dir():
        return 0
    return sum(strip_legacy_focus_block(md) for md in sorted(root.glob("*/memory/MEMORY.md")))


# =============================================================================
# Manual archive of promoted files left behind (profile-prune --scope memory)
# =============================================================================

DEMOTE_STALE_DAYS = 30  # Days without modification before demotion


def demote_stale_memories(
    project_path: str | None = None,
    stale_days: int = DEMOTE_STALE_DAYS,
    dry_run: bool = False,
) -> list[str]:
    """Move promoted_*.md files untouched for ``stale_days`` to ``memory/_archive/`` (reversible).

    Only the files the old promotion step wrote are considered; files Claude Code or a person wrote are never moved.
    Returns the names archived, or that would be with ``dry_run``.
    """
    import time

    memory_dir = get_memory_dir(project_path)
    if not memory_dir:
        return []

    archive_dir = memory_dir / "_archive"
    cutoff = time.time() - stale_days * 86400
    archived = []
    for f in memory_dir.glob("promoted_*.md"):
        if f.stat().st_mtime >= cutoff:
            continue
        if dry_run:
            archived.append(f.name)
            continue
        try:
            archive_dir.mkdir(exist_ok=True)
            f.rename(archive_dir / f.name)
            archived.append(f.name)
        except Exception as e:
            logger.warning(f"Failed to archive {f.name}: {e}")

    if archived and not dry_run:
        _remove_from_memory_index(memory_dir, archived)
    return archived


def _remove_from_memory_index(memory_dir: Path, filenames: list[str]) -> None:
    """Remove MEMORY.md index lines that link to the archived files; a line that merely mentions a name stays."""
    memory_md = memory_dir / "MEMORY.md"
    if not memory_md.exists():
        return
    try:
        content = memory_md.read_text()
        links = [f"({name})" for name in filenames]
        kept = [line for line in content.split("\n") if not any(link in line for link in links)]
        updated = "\n".join(kept)
        if updated != content:
            memory_md.write_text(updated)
    except Exception as e:
        logger.warning(f"Failed to update MEMORY.md index after demotion: {e}")
