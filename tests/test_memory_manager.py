"""Tests for empirica.core.memory_manager — Claude Code's memory folder, read and cleaned, never filled (goal ea3335ec).

POSTFLIGHT and session end used to write an EPISTEMIC FOCUS block into MEMORY.md and promote facts into promoted_*.md files.
The block duplicated the session-start block, and no MEMORY.md ever indexed a promoted file, so Claude Code never loaded one
(0 references across 20 memory folders, 2026-11-13). What is left: stats for memory-report, the manual archive of promoted
files (profile-prune --scope memory), and a one-time removal of the old block, run by setup-claude-code.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import empirica.core.memory_manager as mm
from empirica.core.memory_manager import (
    _remove_from_memory_index,
    demote_stale_memories,
    get_memory_stats,
    strip_legacy_focus_block,
    strip_legacy_focus_blocks,
)

MANUAL_TOP = """# Empirica Project Memory

## Working agreements

- [Patch releases by default](feedback_patch.md) — 1.16.x for fixes
"""

# The block as update_hot_cache wrote it, footer included (shape copied from a live MEMORY.md, 2026-11-13).
BLOCK = """
## EPISTEMIC FOCUS (Confidence-Ranked)

### Important (weight 0.4-0.7)
- [0.60] **Finding:** 1.16.2 pre-publish pass, in progress...
- [0.55] **Finding:** Change-without-knowledge, measured on core...

### Dead-Ends (avoid re-trying)
- [1.06] **Dead-End:** Bash: set -o pipefail && python3 - <<'EOF'

### Mistakes (prevention strategies)
- [0.50] **Mistake:** Saved a schema migration into the live tree

### Active Goals (10)
- [0.44] **Goal:** 1.16.2 pre-publish pass...

---
📊 **55 items ranked** | For deeper context:
- `empirica project-bootstrap --session-id 020720fc` (full load + subtasks)
- `empirica project-search --task "<query>"` (Qdrant semantic)
- `git notes show --ref=breadcrumbs HEAD` (session narrative)
"""


def _memory(tmp_path, text):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    path = memory_dir / "MEMORY.md"
    path.write_text(text)
    return path


# ── the one-time removal ────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_block_is_removed_and_the_manual_lines_kept(tmp_path):
    path = _memory(tmp_path, MANUAL_TOP + BLOCK)
    assert strip_legacy_focus_block(path) is True
    assert path.read_text() == MANUAL_TOP
    assert (path.parent / ".memory_lock").exists()  # written under the lock the old writers used


def test_lines_written_below_the_block_survive(tmp_path):
    after = "\n## Later\n\n- [Added after](feedback_later.md) — kept\n"
    path = _memory(tmp_path, MANUAL_TOP + BLOCK + after)
    strip_legacy_focus_block(path)
    assert path.read_text() == MANUAL_TOP + after


def test_a_block_whose_footer_the_cap_cut_off_ends_at_the_first_line_that_is_not_its_own(tmp_path):
    # enforce_memory_md_cap trimmed the block from the bottom, so the footer could be gone; an index line ("- [Title](f.md)")
    # right after it must not be read as one of the block's "- [0.60]" lines.
    truncated = BLOCK.split("\n\n### Mistakes")[0] + "\n"
    after = "- [Index line](feedback_x.md) — kept\n"
    path = _memory(tmp_path, MANUAL_TOP + truncated + after)
    strip_legacy_focus_block(path)
    assert path.read_text() == MANUAL_TOP + after


def test_a_file_without_the_block_is_not_rewritten(tmp_path):
    path = _memory(tmp_path, MANUAL_TOP)
    old = time.time() - 3600
    os.utime(path, (old, old))
    assert strip_legacy_focus_block(path) is False
    assert path.stat().st_mtime == old
    assert path.read_text() == MANUAL_TOP


def test_a_second_run_changes_nothing(tmp_path):
    path = _memory(tmp_path, MANUAL_TOP + BLOCK)
    strip_legacy_focus_block(path)
    assert strip_legacy_focus_block(path) is False
    assert path.read_text() == MANUAL_TOP


def test_a_missing_file_is_not_an_error(tmp_path):
    assert strip_legacy_focus_block(tmp_path / "memory" / "MEMORY.md") is False


def test_every_project_memory_folder_is_cleaned_and_counted(tmp_path):
    root = tmp_path / "projects"
    a = _memory(root / "-w-a", MANUAL_TOP + BLOCK)
    b = _memory(root / "-w-b", MANUAL_TOP)
    (root / "-w-c").mkdir(parents=True)  # a project with no memory folder
    assert strip_legacy_focus_blocks(root) == 1
    assert a.read_text() == MANUAL_TOP
    assert b.read_text() == MANUAL_TOP
    assert strip_legacy_focus_blocks(tmp_path / "absent") == 0


# ── nothing fills the folder any more ───────────────────────────────────────────────────────────────────────────────────


def test_the_writers_are_gone():
    for name in ("update_hot_cache", "promote_eidetic_to_memory", "enforce_memory_md_cap", "fetch_ranked_artifacts"):
        assert not hasattr(mm, name), name


def test_postflight_and_session_end_do_not_touch_the_memory_folder():
    import empirica.cli.command_handlers._workflow_postflight as wp

    hook = (
        Path(mm.__file__).parent.parent / "plugins" / "claude-code-integration" / "hooks" / "session-end-postflight.py"
    )
    assert "memory_manager" not in hook.read_text()
    assert "MEMORY.md" not in hook.read_text()
    assert not hasattr(wp, "_postflight_update_memory_hot_cache")


# ── kept: stats and the manual archive ──────────────────────────────────────────────────────────────────────────────────


def test_stats_report_the_folder(tmp_path):
    path = _memory(tmp_path, MANUAL_TOP + BLOCK)
    (path.parent / "feedback_patch.md").write_text("x")
    with patch("empirica.core.memory_manager.get_memory_dir", return_value=path.parent):
        stats = get_memory_stats()
    assert stats["file_count"] == 2
    assert stats["memory_md_has_auto_section"] is True
    assert [f["name"] for f in stats["files"]] == ["feedback_patch.md"]


def test_demote_never_touches_hand_written_files(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "manual_note.md").write_text("manual content")
    (memory_dir / "MEMORY.md").write_text("# Memory\n")
    with patch("empirica.core.memory_manager.get_memory_dir", return_value=memory_dir):
        assert demote_stale_memories(stale_days=0) == []
    assert (memory_dir / "manual_note.md").exists()


def test_demote_archives_an_old_promoted_file(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    promoted = memory_dir / "promoted_old_fact.md"
    promoted.write_text("old content")
    (memory_dir / "MEMORY.md").write_text("# Memory\n- [Old](promoted_old_fact.md)\n")
    old = time.time() - 31 * 86400
    os.utime(promoted, (old, old))
    with patch("empirica.core.memory_manager.get_memory_dir", return_value=memory_dir):
        assert demote_stale_memories(stale_days=30) == ["promoted_old_fact.md"]
    assert (memory_dir / "_archive" / "promoted_old_fact.md").exists()
    assert "(promoted_old_fact.md)" not in (memory_dir / "MEMORY.md").read_text()


def test_removing_an_index_line_leaves_lines_that_merely_mention_the_name(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text(
        "# Memory\n\n- [Link](promoted_test.md) — auto\n- Test results show improvement\n"
    )
    _remove_from_memory_index(memory_dir, ["promoted_test.md"])
    result = (memory_dir / "MEMORY.md").read_text()
    assert "Test results show improvement" in result
    assert "(promoted_test.md)" not in result
