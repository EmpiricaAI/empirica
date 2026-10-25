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
