"""subagent-stop extracts what a subagent reported, not what it quoted, and rolls it up against the parent's transaction.

`_extract_matching_sentence` tested the marker ("Found:") on the whole message but returned the FIRST sentence holding the
bare word, split text on every '.', and counted markers inside fenced code and mid-sentence ("not Found: anything").
`rollup_to_parent` logged findings, unknowns and dead ends with no transaction_id, so POSTFLIGHT's grounded calibration
could not attribute them to the transaction that spawned the subagent.
"""

from __future__ import annotations

import importlib.util
import json
import types
from pathlib import Path

import pytest

HOOK = (
    Path(__file__).resolve().parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "subagent-stop.py"
)


@pytest.fixture(scope="module")
def stop():
    spec = importlib.util.spec_from_file_location("subagent_stop_rollup", HOOK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _transcript(tmp_path, *texts):
    path = tmp_path / "t.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"message": {"role": "assistant", "content": [{"type": "text", "text": t}]}}) for t in texts
        )
    )
    return str(path)


def test_a_marker_at_the_start_of_a_line_is_extracted_without_its_neighbours(stop, tmp_path):
    out = stop.extract_findings_from_transcript(
        _transcript(tmp_path, "Found 3 files in the tree.\nFound: the cache key omits the tenant id.\nDone.")
    )
    assert out["findings"] == ["the cache key omits the tenant id."]


def test_bullets_and_emphasis_around_a_marker_are_looked_through(stop, tmp_path):
    out = stop.extract_findings_from_transcript(
        _transcript(tmp_path, "- **Found:** the retry loop never backs off\n> Unknown: whether the lock is re-entrant")
    )
    assert out["findings"] == ["the retry loop never backs off"]
    assert out["unknowns"] == ["whether the lock is re-entrant"]


def test_a_marker_in_the_middle_of_a_sentence_is_not_a_report(stop, tmp_path):
    out = stop.extract_findings_from_transcript(
        _transcript(tmp_path, "I have not Found: anything about it, and the Result: was empty in the logs.")
    )
    assert out == {"findings": [], "unknowns": [], "dead_ends": []}


def test_a_marker_after_a_sentence_end_on_the_same_line_is_extracted(stop, tmp_path):
    out = stop.extract_findings_from_transcript(
        _transcript(tmp_path, "Checked a.py and b.py. Result: both pass the guard")
    )
    assert out["findings"] == ["both pass the guard"]


def test_markers_inside_fenced_code_are_ignored(stop, tmp_path):
    text = "Here is the diff:\n```python\n# TODO: refactor this later\nResult: x\n```\nTODO: add a regression test for the lock"
    out = stop.extract_findings_from_transcript(_transcript(tmp_path, text))
    assert out["unknowns"] == ["add a regression test for the lock"]
    assert out["findings"] == []


def test_dots_inside_a_report_do_not_cut_it_short(stop, tmp_path):
    out = stop.extract_findings_from_transcript(
        _transcript(tmp_path, "Found: sentinel-gate.py:5811 reads config.v2.yaml twice")
    )
    assert out["findings"] == ["sentinel-gate.py:5811 reads config.v2.yaml twice"]


class _FakeDB:
    def __init__(self):
        self.calls = []
        self.sessions = types.SimpleNamespace(get_session=lambda sid: {"project_id": "proj"})

    def log_finding(self, **kw):
        self.calls.append(("finding", kw))

    def log_unknown(self, **kw):
        self.calls.append(("unknown", kw))

    def log_dead_end(self, **kw):
        self.calls.append(("dead_end", kw))

    def close(self):
        pass


def _roll(stop, monkeypatch, **kwargs):
    fake = _FakeDB()
    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", lambda *a, **k: fake)
    monkeypatch.setattr(stop, "_gated_rollup", lambda *a, **k: None)  # the naive path: no gate scoring in this test
    extracted = {
        "findings": ["a finding"],
        "unknowns": ["an unknown"],
        "dead_ends": [{"approach": "x", "why_failed": "y"}],
    }
    stop.rollup_to_parent("parent-1", "agent", extracted, **kwargs)
    return fake.calls


def test_every_rolled_up_artifact_carries_the_parents_transaction(stop, monkeypatch):
    calls = _roll(stop, monkeypatch, transaction_id="tx-parent")
    assert {kind for kind, _ in calls} == {"finding", "unknown", "dead_end"}
    assert all(kw.get("transaction_id") == "tx-parent" for _, kw in calls)


def test_with_no_open_transaction_nothing_is_invented(stop, monkeypatch):
    calls = _roll(stop, monkeypatch)
    assert calls and all(kw.get("transaction_id") is None for _, kw in calls)
