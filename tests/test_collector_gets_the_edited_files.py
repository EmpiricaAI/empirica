"""Grounded verification sees the files the transaction edited.

The post-test collector read `edited_files` from the hook counters file, but
POSTFLIGHT deletes that file (in its close step) before grounded verification
runs. So the list was always empty, and every edit outside the git repository,
which the collector exists to catch because git metrics cannot see it, went
unseen by grounded calibration. POSTFLIGHT now captures the list first and hands
it down: `_run_grounded_verification` -> `run_grounded_verification` ->
`_run_single_phase_verification` -> `PostTestCollector`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from empirica.core.post_test import grounded_calibration as gc
from empirica.core.post_test.collector import PostTestCollector


def test_a_supplied_list_is_the_answer_and_outside_edits_are_gathered(tmp_path):
    inside = tmp_path / "repo" / "a.py"
    outside = tmp_path / "elsewhere" / "notes.md"
    collector = PostTestCollector(session_id="s", edited_files=[str(inside), str(outside)])
    assert collector._get_transaction_edited_files() == [str(inside), str(outside)]
    assert str(outside) in collector._gather_non_git_files(str(tmp_path / "repo"))
    assert str(inside) not in collector._gather_non_git_files(str(tmp_path / "repo"))


def test_without_a_supplied_list_it_still_looks_for_the_counters_file(tmp_path, monkeypatch):
    """Positive control: callers outside POSTFLIGHT keep the file lookup."""
    import json

    from empirica.utils.session_resolver import InstanceResolver as R

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".empirica").mkdir()
    counters = tmp_path / ".empirica" / f"hook_counters{R.instance_suffix()}.json"
    counters.write_text(json.dumps({"edited_files": ["from-file.py"]}))
    collector = PostTestCollector(session_id="s")
    monkeypatch.setattr(collector, "_resolve_project_root", lambda: None)
    assert collector._get_transaction_edited_files() == ["from-file.py"]


def test_run_grounded_verification_hands_the_list_to_the_collector(monkeypatch):
    seen: dict = {}

    class _Collector:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def collect_all(self):
            raise RuntimeError("stop after construction")

    monkeypatch.setattr(gc, "PostTestCollector", _Collector)
    gc.run_grounded_verification(
        session_id="s",
        postflight_vectors={"know": 0.5},
        db=MagicMock(),
        work_type="code",
        edited_files=["x.py"],
    )
    assert seen.get("edited_files") == ["x.py"]
