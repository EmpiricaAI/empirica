"""A swap whose manifest write failed must not let the NEXT swap destroy the original memory (goal 74e7a298, unknown 72a0c525).

`swap_memory` backs the harness project's memory up, removes the originals, copies the other project's files in, and only then writes the
manifest. If that last write fails (disk full), the swapped-in files stay, the originals sit in the backup dir, and nothing records that a
swap happened. `restore_memory` then has no manifest and does nothing, and the next `swap_memory` saw no active swap, backed the SWAPPED-IN
files up over the originals in the backup dir, and restored them as the project's own for good.

Opt-in feature (the memory swap is off by default), so the guard is deliberately simple: an orphan backup blocks a new swap and says where
the originals are.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from empirica.utils.memory_swap import BACKUP_SUBDIR, MANIFEST_NAME, _claude_memory_dir, restore_memory, swap_memory


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _project(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    project = tmp_path / name
    project.mkdir()
    memory = _claude_memory_dir(project)
    memory.mkdir(parents=True, exist_ok=True)
    for file_name, content in files.items():
        (memory / file_name).write_text(content)
    return project


def _lose_the_manifest(harness: Path) -> None:
    (_claude_memory_dir(harness) / MANIFEST_NAME).unlink()


def test_a_second_swap_over_a_lost_manifest_keeps_the_original_in_the_backup(fake_home, tmp_path):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "ORIGINAL"})
    other = _project(tmp_path, "other", {"MEMORY.md": "OTHER PROJECT"})
    assert swap_memory(harness, other)["action"] == "swapped"
    _lose_the_manifest(harness)  # the state a failed manifest write leaves behind

    second = swap_memory(harness, other)

    backup = _claude_memory_dir(harness) / BACKUP_SUBDIR / "MEMORY.md"
    assert backup.read_text() == "ORIGINAL", "the swapped-in file was backed up over the original"
    assert second["ok"] is False and second["action"] == "orphan_backup", second
    assert BACKUP_SUBDIR in second["message"], "the message must say where the originals are"


def test_a_normal_swap_restore_swap_cycle_is_not_blocked(fake_home, tmp_path):
    """Control: the guard fires only on a backup with no manifest, not after a clean restore."""
    harness = _project(tmp_path, "harness", {"MEMORY.md": "ORIGINAL"})
    other = _project(tmp_path, "other", {"MEMORY.md": "OTHER PROJECT"})
    assert swap_memory(harness, other)["action"] == "swapped"
    assert restore_memory(harness)["ok"] is True
    assert (_claude_memory_dir(harness) / "MEMORY.md").read_text() == "ORIGINAL"
    assert swap_memory(harness, other)["action"] == "swapped"


def test_a_swap_with_a_live_manifest_is_still_idempotent(fake_home, tmp_path):
    """Control: an active swap to the same source reports already_active rather than orphan_backup."""
    harness = _project(tmp_path, "harness", {"MEMORY.md": "ORIGINAL"})
    other = _project(tmp_path, "other", {"MEMORY.md": "OTHER PROJECT"})
    swap_memory(harness, other)
    assert swap_memory(harness, other)["action"] == "already_active"
