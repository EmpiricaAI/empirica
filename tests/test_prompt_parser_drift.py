"""Tests for scripts/check_prompt_parser_drift.py (T2 — parser↔prompt drift).

The check must (a) catch a prompt referencing a verb the parser no longer has
(the #348 failure mode), and (b) NOT false-positive on prose that happens to
follow the word `empirica` inside a code span.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_prompt_parser_drift.py"
_spec = importlib.util.spec_from_file_location("check_prompt_parser_drift", _SCRIPT)
drift = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drift)


def test_mentions_matches_real_invocations():
    text = "```\nempirica finding-log --finding x\nempirica status\nempirica goals-create --objective y | jq\n```"
    assert drift.mentions_in(text) == {"finding-log", "status", "goals-create"}


def test_mentions_rejects_prose_in_code_spans():
    # nouns/product-names after `empirica` inside code spans must NOT match
    text = "the `empirica fleet` product; an `empirica transaction is open` note"
    assert drift.mentions_in(text) == set()


def test_mentions_ignores_prose_outside_code_spans():
    assert drift.mentions_in("run empirica finding-log --x described in prose") == set()


def test_scan_flags_removed_verb(tmp_path):
    f = tmp_path / "skill.md"
    f.write_text("```\nempirica agent-spawn --turtle\nempirica finding-log --x\n```")
    drift_map, mentioned = drift.scan([f], {"finding-log", "status"})
    assert "agent-spawn" in drift_map
    assert str(f) in drift_map["agent-spawn"] or f.name in drift_map["agent-spawn"][0]
    assert "finding-log" in mentioned  # a live verb is counted as covered, not drift


def test_scan_clean_when_all_verbs_live(tmp_path):
    f = tmp_path / "skill.md"
    f.write_text("```\nempirica finding-log --x\nempirica goals-create --objective y\n```")
    drift_map, mentioned = drift.scan([f], {"finding-log", "goals-create"})
    assert drift_map == {}
    assert mentioned == {"finding-log", "goals-create"}


def test_live_verbs_reflects_parser():
    verbs = drift.live_verbs()
    assert "finding-log" in verbs
    assert "goals-create" in verbs
    assert "agent-spawn" not in verbs  # pruned in #348 — the drift this guards against


def test_repo_corpus_is_clean():
    """The shipped skills + prompt template must not drift from the parser."""
    verbs = drift.live_verbs()
    files = drift.corpus_files(include_private=False)
    drift_map, _ = drift.scan(files, verbs)
    assert drift_map == {}, f"prompt→parser drift in shipped corpus: {drift_map}"
