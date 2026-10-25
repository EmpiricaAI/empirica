"""R7 (sweep, goal b78396ad): memory_swap read the harness cwd after the hook had already chdir'd away from it.

post-compact chdirs to the resolved practice before it swaps, so `Path.cwd()` inside maybe_swap_for_active_transaction was the
active project, the two paths compared equal, and the swap never fired. Returning to the original project also left the swapped
files in place (equal paths were a bare noop), and a failed restore at session end was swallowed by `except Exception: pass`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from empirica.utils.memory_swap import (
    _claude_memory_dir,
    is_swap_active,
    maybe_swap_for_active_transaction,
    restore_if_swapped,
    swap_memory,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _project(tmp_path, name: str, files: dict[str, str]) -> Path:
    project = tmp_path / name
    project.mkdir()
    memory = _claude_memory_dir(project)
    memory.mkdir(parents=True)
    for fname, content in files.items():
        (memory / fname).write_text(content)
    return project


def _context(project: Path):
    return patch(
        "empirica.utils.session_resolver.InstanceResolver.context",
        return_value={"project_path": str(project), "transaction_id": "tx-1"},
    )


def test_the_swap_uses_the_harness_cwd_it_was_given_not_the_process_cwd(fake_home, tmp_path, monkeypatch):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "active content"})
    monkeypatch.chdir(active)  # what the hook has done by the time it swaps
    with _context(active):
        result = maybe_swap_for_active_transaction(claude_session_id="cs", harness_cwd=harness)
    assert result["action"] == "swapped", result
    assert (_claude_memory_dir(harness) / "MEMORY.md").read_text() == "active content"


def test_negative_control_without_harness_cwd_the_process_cwd_is_still_used(fake_home, tmp_path, monkeypatch):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "active content"})
    monkeypatch.chdir(harness)
    with _context(active):
        assert maybe_swap_for_active_transaction(claude_session_id="cs")["action"] == "swapped"


def test_returning_to_the_original_project_restores_its_memory(fake_home, tmp_path):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "active content"})
    assert swap_memory(harness, active)["action"] == "swapped"
    assert is_swap_active(harness)
    result = swap_memory(harness, harness)  # the transaction is back in the harness project
    assert result["action"] == "restored", result
    assert not is_swap_active(harness)
    assert (_claude_memory_dir(harness) / "MEMORY.md").read_text() == "harness original"


def test_equal_paths_with_no_swap_active_stay_a_noop(fake_home, tmp_path):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "x"})
    assert swap_memory(harness, harness)["action"] == "noop"


def test_restore_if_swapped_is_none_when_nothing_was_swapped(fake_home, tmp_path):
    assert restore_if_swapped(_project(tmp_path, "harness", {"MEMORY.md": "x"})) is None


def test_restore_if_swapped_restores_an_active_swap(fake_home, tmp_path):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "active content"})
    swap_memory(harness, active)
    result = restore_if_swapped(harness)
    assert result and result["ok"] is True and result["action"] == "restored"
    assert (_claude_memory_dir(harness) / "MEMORY.md").read_text() == "harness original"


def test_a_failed_restore_is_returned_not_swallowed(fake_home, tmp_path):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "active content"})
    swap_memory(harness, active)
    with patch("empirica.utils.memory_swap.restore_memory", side_effect=OSError("disk full")):
        result = restore_if_swapped(harness)
    assert result is not None and result["ok"] is False
    assert result["action"] == "restore_failed" and "disk full" in result["message"]


# ── T1 (sweep 2): the session-end hook restores from the launch cwd, and a restore keeps what was written while swapped ──

import importlib.util  # noqa: E402

HOOKS = Path(__file__).resolve().parents[1] / "empirica" / "plugins" / "claude-code-integration" / "hooks"


def _hook(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_session_end_restores_from_the_launch_cwd_not_the_cwd_after_its_own_chdir(fake_home, tmp_path, monkeypatch):
    """main() chdirs to the resolved practice before it cleans up, so Path.cwd() there was the active project: no manifest, no restore."""
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "active content"})
    swap_memory(harness, active)
    hook = _hook("session-end-postflight.py", "session_end_t1")
    monkeypatch.chdir(active)  # what _resolve_session_and_project has done by the time cleanup runs
    monkeypatch.setattr(hook, "_LAUNCH_CWD", harness)
    hook._cleanup_session_files("cs-1")
    assert not is_swap_active(harness)
    assert (_claude_memory_dir(harness) / "MEMORY.md").read_text() == "harness original"


def test_session_end_with_no_launch_cwd_skips_the_restore_and_does_not_raise(fake_home, tmp_path, monkeypatch, capsys):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "active content"})
    swap_memory(harness, active)
    hook = _hook("session-end-postflight.py", "session_end_t1b")
    monkeypatch.chdir(active)
    monkeypatch.setattr(hook, "_LAUNCH_CWD", None)
    hook._cleanup_session_files("cs-1")
    assert is_swap_active(harness), "nothing may be guessed from the post-chdir cwd"
    assert "launch" in capsys.readouterr().err.lower(), "the skipped restore must be visible"


def test_a_deleted_launch_cwd_is_read_as_none_not_a_crash(monkeypatch):
    hook = _hook("session-end-postflight.py", "session_end_t1c")
    monkeypatch.setattr(hook.os, "getcwd", lambda: (_ for _ in ()).throw(FileNotFoundError("gone")))
    assert hook._launch_cwd() is None


def test_post_compact_with_no_harness_cwd_does_not_swap_and_says_so(fake_home, monkeypatch, capsys):
    monkeypatch.setenv("EMPIRICA_MEMORY_SWAP", "1")
    hook = _hook("post-compact.py", "post_compact_t1")
    import empirica.utils.memory_swap as ms

    calls = []
    monkeypatch.setattr(ms, "maybe_swap_for_active_transaction", lambda **kw: calls.append(kw) or {"ok": True})
    hook._try_memory_swap("cs-1", None)
    assert calls == [], "must not swap against a guessed cwd"
    assert "skipped" in capsys.readouterr().err
    hook._try_memory_swap("cs-1", "/somewhere")
    assert len(calls) == 1, "positive control: a real cwd does reach the swap"


def _memory(project: Path) -> Path:
    return _claude_memory_dir(project)


def test_an_edit_made_while_swapped_is_written_back_to_the_project_it_belongs_to(fake_home, tmp_path):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "active index", "keep.md": "unchanged"})
    swap_memory(harness, active)
    (_memory(harness) / "MEMORY.md").write_text("active index + a new line")  # what the harness's auto-memory does
    result = restore_if_swapped(harness)
    assert result and result["ok"] is True
    assert (_memory(harness) / "MEMORY.md").read_text() == "harness original"
    assert (_memory(active) / "MEMORY.md").read_text() == "active index + a new line", "the edit was lost"
    assert (_memory(active) / "keep.md").read_text() == "unchanged"
    assert result.get("written_back") == ["MEMORY.md"], result


def test_a_new_file_written_while_swapped_goes_to_the_active_project_not_the_harness_project(fake_home, tmp_path):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "active index"})
    swap_memory(harness, active)
    (_memory(harness) / "feedback_new.md").write_text("learned during the swap")
    restore_if_swapped(harness)
    assert (_memory(active) / "feedback_new.md").read_text() == "learned during the swap"
    assert not (_memory(harness) / "feedback_new.md").exists(), "an orphan was left in the harness project"


def test_a_source_that_changed_meanwhile_is_not_overwritten_and_the_conflict_is_reported(fake_home, tmp_path):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "active index"})
    swap_memory(harness, active)
    (_memory(harness) / "MEMORY.md").write_text("edited in the swap")
    (_memory(active) / "MEMORY.md").write_text("edited by another session in the active project")
    result = restore_if_swapped(harness)
    assert (_memory(active) / "MEMORY.md").read_text() == "edited by another session in the active project"
    assert result and result.get("write_back_conflicts") == ["MEMORY.md"], result
    assert (_memory(harness) / "MEMORY.md").read_text() == "harness original", "the restore itself still completes"


def test_nothing_is_written_back_when_nothing_changed(fake_home, tmp_path):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "active index"})
    swap_memory(harness, active)
    result = restore_if_swapped(harness)
    assert result and not result.get("written_back") and not result.get("write_back_conflicts")


def test_a_manifest_from_before_hashes_were_recorded_restores_without_writing_back(fake_home, tmp_path):
    import json

    from empirica.utils.memory_swap import MANIFEST_NAME

    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "active index"})
    swap_memory(harness, active)
    manifest_path = _memory(harness) / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("copied_hashes", None)
    manifest_path.write_text(json.dumps(manifest))
    (_memory(harness) / "MEMORY.md").write_text("edited")
    result = restore_if_swapped(harness)
    assert result and result["ok"] is True
    assert (_memory(harness) / "MEMORY.md").read_text() == "harness original"
    assert (_memory(active) / "MEMORY.md").read_text() == "active index"


def test_a_failed_write_back_stops_the_restore_so_the_edit_survives(fake_home, tmp_path):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "active index"})
    swap_memory(harness, active)
    (_memory(harness) / "MEMORY.md").write_text("an edit that exists nowhere else")
    with patch("empirica.utils.memory_swap._write_back", side_effect=OSError("disk full")):
        result = restore_if_swapped(harness)
    assert result and result["ok"] is False and result["action"] == "write_back_failed"
    assert (_memory(harness) / "MEMORY.md").read_text() == "an edit that exists nowhere else"
    assert is_swap_active(harness), "the swap stays active so the restore can be retried"


# ── second-pass sweep: a write-back loses nothing, in directories or on conflict ──


def _sub(project: Path, rel: str, content: str | None = None) -> Path:
    path = _memory(project) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if content is not None:
        path.write_text(content)
    return path


def test_a_conflicting_edit_is_kept_beside_the_source_file_not_destroyed(fake_home, tmp_path):
    """The conflict was counted and then removed with the swapped-in files, so the edit existed nowhere afterwards."""
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"x.md": "original"})
    swap_memory(harness, active)
    (_memory(harness) / "x.md").write_text("edited in the swap")
    (_memory(active) / "x.md").write_text("other-session")
    result = restore_if_swapped(harness)
    assert (_memory(active) / "x.md").read_text() == "other-session", "the source is never overwritten"
    kept = [p for p in _memory(active).iterdir() if p.name != "x.md"]
    assert [p.read_text() for p in kept] == ["edited in the swap"], f"the edit must survive somewhere: {kept}"
    assert "swap-conflict" in kept[0].name
    assert result and result["write_back_conflicts"] == ["x.md"]
    assert not (_memory(harness) / "x.md").exists()


def test_an_edit_inside_a_swapped_in_directory_is_carried_back(fake_home, tmp_path):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "idx"})
    _sub(active, "sub/y.md", "original y")
    swap_memory(harness, active)
    (_memory(harness) / "sub" / "y.md").write_text("edited y")
    result = restore_if_swapped(harness)
    assert (_memory(active) / "sub" / "y.md").read_text() == "edited y"
    assert result and result["written_back"] == ["sub/y.md"]
    assert not (_memory(harness) / "sub").exists()


def test_a_new_file_inside_a_swapped_in_directory_is_carried_back(fake_home, tmp_path):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "idx"})
    _sub(active, "sub/y.md", "original y")
    swap_memory(harness, active)
    (_memory(harness) / "sub" / "z.md").write_text("new z")
    restore_if_swapped(harness)
    assert (_memory(active) / "sub" / "z.md").read_text() == "new z"


def test_a_new_file_whose_name_is_taken_is_kept_beside_it_and_leaves_the_harness_dir(fake_home, tmp_path):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "idx"})
    swap_memory(harness, active)
    (_memory(harness) / "new.md").write_text("made during the swap")
    (_memory(active) / "new.md").write_text("made by someone else meanwhile")
    result = restore_if_swapped(harness)
    assert (_memory(active) / "new.md").read_text() == "made by someone else meanwhile"
    assert "made during the swap" in [p.read_text() for p in _memory(active).iterdir()]
    assert not (_memory(harness) / "new.md").exists(), "it stayed behind in the harness project's own memory"
    assert result and result["write_back_conflicts"] == ["new.md"]


def test_a_retry_after_a_partial_write_back_does_not_report_its_own_earlier_copies_as_conflicts(fake_home, tmp_path):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "idx"})
    swap_memory(harness, active)
    (_memory(harness) / "n.md").write_text("same content")
    (_memory(active) / "n.md").write_text("same content")  # already copied by an interrupted earlier restore
    result = restore_if_swapped(harness)
    assert result and result["write_back_conflicts"] == [] and result["written_back"] == ["n.md"]
    assert sorted(p.name for p in _memory(active).iterdir()) == ["MEMORY.md", "n.md"], "no stray conflict copy"


def test_session_end_says_what_was_written_back_and_what_conflicted(fake_home, tmp_path, monkeypatch, capsys):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "idx"})
    swap_memory(harness, active)
    (_memory(harness) / "MEMORY.md").write_text("idx + note")
    hook = _hook("session-end-postflight.py", "session_end_surface")
    monkeypatch.chdir(active)
    monkeypatch.setattr(hook, "_LAUNCH_CWD", harness)
    hook._cleanup_session_files("cs-1")
    assert "wrote back 1" in capsys.readouterr().err


# ── third-pass sweep: symlinks, a broken backup, an unreadable original, a retry, and the opt-in ──

import os  # noqa: E402
import shutil  # noqa: E402


def test_a_swapped_in_symlink_is_never_written_through_to_the_file_it_points_at(fake_home, tmp_path):
    """The source's link.md -> real.md is copied in as a plain file; writing the edit back followed the link and overwrote real.md."""
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original"})
    active = _project(tmp_path, "active", {"real.md": "REAL"})
    (_memory(active) / "link.md").symlink_to("real.md")
    swap_memory(harness, active)
    (_memory(harness) / "link.md").write_text("EDIT_LINK")
    result = restore_if_swapped(harness)
    assert (_memory(active) / "real.md").read_text() == "REAL", "a different file than the one edited was changed"
    assert (_memory(active) / "link.md").is_symlink()
    assert result and result["write_back_conflicts"] == ["link.md"]
    assert "EDIT_LINK" in [p.read_text() for p in _memory(active).iterdir() if "swap-conflict" in p.name]


def test_a_forced_replace_over_a_swap_whose_backup_is_gone_refuses_instead_of_adopting_the_old_files(
    fake_home, tmp_path
):
    """restore_memory answers 'broken' (manifest cleared, swapped-in files left) and its return was ignored, so the next swap backed
    the OLD project's files up as the harness project's originals."""
    harness = _project(tmp_path, "harness", {"a.md": "harness a"})
    first = _project(tmp_path, "first", {"a.md": "first a"})
    second = _project(tmp_path, "second", {"a.md": "second a"})
    swap_memory(harness, first)
    from empirica.utils.memory_swap import BACKUP_SUBDIR

    shutil.rmtree(_memory(harness) / BACKUP_SUBDIR)
    result = swap_memory(harness, second)
    assert result["ok"] is False and result["action"] == "restore_before_swap_failed", result
    assert not is_swap_active(second) and (_memory(harness) / "a.md").read_text() == "first a", "nothing may be layered"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 file")
def test_a_harness_original_that_cannot_be_backed_up_stops_the_swap_before_anything_is_removed(fake_home, tmp_path):
    harness = _project(tmp_path, "harness", {"MEMORY.md": "harness original", "secret.md": "keep me"})
    active = _project(tmp_path, "active", {"MEMORY.md": "active"})
    (_memory(harness) / "secret.md").chmod(0)
    try:
        result = swap_memory(harness, active)
    finally:
        (_memory(harness) / "secret.md").chmod(0o644)
    assert result["ok"] is False and result["action"] == "backup_incomplete", result
    assert (_memory(harness) / "MEMORY.md").read_text() == "harness original", (
        "nothing may be removed after a failed backup"
    )
    assert (_memory(harness) / "secret.md").read_text() == "keep me"
    assert not is_swap_active(harness)


def test_originals_already_restored_by_an_interrupted_restore_are_not_copied_into_the_active_project(
    fake_home, tmp_path
):
    from empirica.utils.memory_swap import BACKUP_SUBDIR

    harness = _project(tmp_path, "harness", {"orig.md": "harness original"})
    active = _project(tmp_path, "active", {"MEMORY.md": "active"})
    swap_memory(harness, active)
    # What an interrupted restore leaves: the originals already copied back from the backup, the manifest still present.
    shutil.copy2(_memory(harness) / BACKUP_SUBDIR / "orig.md", _memory(harness) / "orig.md")
    restore_if_swapped(harness)
    assert not (_memory(active) / "orig.md").exists(), "the harness project's own file leaked into the active project"
    assert (_memory(harness) / "orig.md").read_text() == "harness original"


def test_post_compact_swaps_only_when_the_operator_opted_in(fake_home, monkeypatch):
    """The swap never fired before this release (it compared a cwd with itself). Switching it on for everyone, with the edge cases a
    first real use exposes, is a decision, not a bug fix: it is opt-in."""
    hook = _hook("post-compact.py", "post_compact_optin")
    import empirica.utils.memory_swap as ms

    calls = []
    monkeypatch.setattr(ms, "maybe_swap_for_active_transaction", lambda **kw: calls.append(kw) or {"ok": True})
    monkeypatch.delenv("EMPIRICA_MEMORY_SWAP", raising=False)
    hook._try_memory_swap("cs-1", "/somewhere")
    assert calls == [], "off by default"
    monkeypatch.setenv("EMPIRICA_MEMORY_SWAP", "1")
    hook._try_memory_swap("cs-1", "/somewhere")
    assert len(calls) == 1
