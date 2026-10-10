"""Parallel subagents of the same name keep their own sessions apart.

subagent-start wrote the mapping to {name}_{timestamp to the second}.json, so two same-name subagents started within a
second overwrote one another, and subagent-stop took the newest ACTIVE file for the name: the first of two parallel
same-name subagents to finish rolled up the OTHER one's child session (and marked its file completed). Each subagent
has its own claude session id in both hooks' payloads; the mapping is keyed by it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FrozenNow:
    """The same second for every start: the case that overwrote files."""

    @staticmethod
    def now():
        import datetime

        return datetime.datetime(2026, 10, 10, 12, 0, 0)


@pytest.fixture
def hooks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    start = _load("subagent_start_parallel", "subagent-start.py")
    stop = _load("subagent_stop_parallel", "subagent-stop.py")
    monkeypatch.setattr(start, "get_budget_allocation", lambda *a, **k: {"budget_id": "b"})
    monkeypatch.setattr(start, "_create_default_budget", lambda *a, **k: {})
    monkeypatch.setattr(start, "datetime", _FrozenNow)
    return start, stop


def _store(start, name, child, sid):
    return start.store_subagent_session(name, child, "parent-1", subagent_claude_session_id=sid)


def test_two_same_name_starts_in_one_second_keep_both_mappings(hooks, tmp_path):
    start, _stop = hooks
    first = _store(start, "general-purpose", "child-A", "sid-A-aaaa")
    second = _store(start, "general-purpose", "child-B", "sid-B-bbbb")
    assert first != second
    files = sorted((tmp_path / ".empirica" / "subagent_sessions").glob("*.json"))
    assert len(files) == 2
    assert {json.loads(f.read_text())["child_session_id"] for f in files} == {"child-A", "child-B"}


def test_each_subagent_stops_onto_its_own_child_session(hooks):
    start, stop = hooks
    _store(start, "general-purpose", "child-A", "sid-A-aaaa")
    _store(start, "general-purpose", "child-B", "sid-B-bbbb")  # newer than A
    assert stop.find_subagent_session("general-purpose", "sid-A-aaaa")["child_session_id"] == "child-A"
    assert stop.find_subagent_session("general-purpose", "sid-B-bbbb")["child_session_id"] == "child-B"


def test_a_subagent_never_takes_a_live_siblings_session(hooks):
    start, stop = hooks
    _store(start, "general-purpose", "child-A", "sid-A-aaaa")
    assert stop.find_subagent_session("general-purpose", "sid-unknown") == {}


def test_a_mapping_without_a_recorded_id_still_resolves_by_name(hooks, tmp_path):
    """Files written by an older start hook carry no subagent id: newest active by name, as before."""
    _start, stop = hooks
    directory = tmp_path / ".empirica" / "subagent_sessions"
    directory.mkdir(parents=True)
    (directory / "general-purpose_20261010_120000.json").write_text(
        json.dumps({"agent_name": "general-purpose", "child_session_id": "child-old", "status": "active"})
    )
    assert stop.find_subagent_session("general-purpose", "sid-anything")["child_session_id"] == "child-old"
    assert stop.find_subagent_session("general-purpose")["child_session_id"] == "child-old"
