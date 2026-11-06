"""After compaction the open transaction keeps what PREFLIGHT declared.

The post-compact hook rewrites the active transaction file for the new conversation. It wrote seven fixed keys and dropped the
rest, so a transaction that was open across a compaction lost its work type, work context, domain and criticality. Seen on
2026-11-06: a PREFLIGHT with work_type `code`, then a compaction, then "No work_type set in PREFLIGHT" on every gated call, and
the work-type expansion of the read list gone for the rest of the transaction.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture
def post_compact(monkeypatch):
    spec = importlib.util.spec_from_file_location("post_compact_keeps_fields", HOOKS / "post-compact.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_get_instance_suffix", lambda: "_t")
    return mod


BEFORE = {
    "transaction_id": "57f47c07",
    "session_id": "sess-before",
    "claude_session_id": "cc-before",
    "preflight_timestamp": 1794000000.0,
    "status": "open",
    "project_path": "/somewhere/else",
    "updated_at": 1.0,
    "work_type": "infra",
    "work_context": "iteration",
    "domain": "default",
    "criticality": "medium",
    "cascade_profile": "default",
    "avg_turns": 12,
    "tool_call_count": 41,
}


def _written(post_compact, tmp_path, snapshot=BEFORE, claude_session_id="cc-after"):
    project = tmp_path / "proj"
    (project / ".empirica").mkdir(parents=True)
    assert post_compact._write_active_transaction_for_new_conversation(
        snapshot, str(project), "inst", claude_session_id
    )
    return json.loads((project / ".empirica" / "active_transaction_t.json").read_text()), project


def test_what_preflight_declared_survives_the_rewrite(post_compact, tmp_path):
    after, _ = _written(post_compact, tmp_path)
    for key in (
        "work_type",
        "work_context",
        "domain",
        "criticality",
        "cascade_profile",
        "avg_turns",
        "tool_call_count",
    ):
        assert after[key] == BEFORE[key], key


def test_the_fields_the_new_conversation_owns_are_still_set_by_it(post_compact, tmp_path):
    after, project = _written(post_compact, tmp_path)
    assert after["claude_session_id"] == "cc-after"
    assert after["project_path"] == str(project)
    assert after["updated_at"] > BEFORE["updated_at"]
    assert after["transaction_id"] == "57f47c07" and after["status"] == "open"


def test_the_snapshots_session_key_is_kept_when_the_caller_has_none(post_compact, tmp_path):
    after, _ = _written(post_compact, tmp_path, claude_session_id=None)
    assert after["claude_session_id"] == "cc-before"


def test_a_snapshot_without_a_status_is_written_as_open(post_compact, tmp_path):
    after, _ = _written(post_compact, tmp_path, snapshot={"transaction_id": "tx"})
    assert after["status"] == "open"
