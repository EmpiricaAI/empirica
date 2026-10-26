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


# ── a backup that could not be completed must not become an orphan (pre-publish sweep, C4) ─────────────────────


def _flaky_first_backup(monkeypatch, victim: str):
    """The first 'Backup' copy silently misses `victim` (a transient copy error); later copies are normal."""
    from empirica.utils import memory_swap as ms

    real = ms._copy_dir_entries
    state = {"failed": False}

    def flaky(src, dest, label):
        done = real(src, dest, label)
        if label == "Backup" and not state["failed"]:
            state["failed"] = True
            (Path(dest) / victim).unlink()
            return [d for d in done if d != victim]
        return done

    monkeypatch.setattr(ms, "_copy_dir_entries", flaky)


def test_an_incomplete_backup_leaves_no_partial_backup_and_the_originals_untouched(fake_home, tmp_path, monkeypatch):
    harness = _project(tmp_path, "harness", {"a.md": "A", "b.md": "B"})
    other = _project(tmp_path, "other", {"MEMORY.md": "OTHER"})
    _flaky_first_backup(monkeypatch, "b.md")

    first = swap_memory(harness, other)

    memory = _claude_memory_dir(harness)
    assert first["ok"] is False and first["action"] == "backup_incomplete", first
    assert (memory / "a.md").read_text() == "A" and (memory / "b.md").read_text() == "B", "nothing was removed"
    backup = memory / BACKUP_SUBDIR
    assert not backup.exists() or not any(backup.iterdir()), (
        "a partial backup is what the next swap would call an orphan"
    )


def test_after_an_incomplete_backup_the_next_swap_succeeds_instead_of_being_refused_forever(
    fake_home, tmp_path, monkeypatch
):
    harness = _project(tmp_path, "harness", {"a.md": "A", "b.md": "B"})
    other = _project(tmp_path, "other", {"MEMORY.md": "OTHER"})
    _flaky_first_backup(monkeypatch, "b.md")
    assert swap_memory(harness, other)["action"] == "backup_incomplete"

    second = swap_memory(harness, other)

    assert second["ok"] is True and second["action"] == "swapped", second
    assert restore_memory(harness)["ok"] is True
    memory = _claude_memory_dir(harness)
    assert (memory / "a.md").read_text() == "A" and (memory / "b.md").read_text() == "B"


def test_an_incomplete_backup_never_deletes_an_earlier_backups_originals(fake_home, tmp_path, monkeypatch):
    """The replacement path skips the orphan guard (a manifest exists). If the old swap's restore cannot remove its backup, that backup still
    holds the project's ORIGINALS; a later incomplete backup must discard only the copies it made, not the directory."""
    from empirica.utils import memory_swap as ms

    harness = _project(tmp_path, "harness", {"a.md": "ORIGINAL A", "b.md": "ORIGINAL B"})
    first = _project(tmp_path, "first", {"MEMORY.md": "FIRST"})
    second = _project(tmp_path, "second", {"MEMORY.md": "SECOND"})
    assert swap_memory(harness, first)["action"] == "swapped"
    backup = _claude_memory_dir(harness) / BACKUP_SUBDIR
    assert {p.name for p in backup.iterdir()} >= {"a.md", "b.md"}

    # the old swap's restore fails to take its backup away (the rmtree it does can fail): nothing is restored, the backup stays
    monkeypatch.setattr(ms, "restore_memory", lambda *a, **k: {"ok": True, "action": "restored"})
    _flaky_first_backup(monkeypatch, "MEMORY.md")
    result = swap_memory(harness, second)

    assert result["action"] == "backup_incomplete", result
    assert (backup / "a.md").read_text() == "ORIGINAL A" and (backup / "b.md").read_text() == "ORIGINAL B", (
        "the originals in the earlier backup were deleted"
    )
