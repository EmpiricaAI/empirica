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
