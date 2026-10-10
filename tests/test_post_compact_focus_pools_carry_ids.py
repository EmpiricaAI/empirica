"""EPISTEMIC FOCUS ranks real items: the post-compact pools carry the id and timestamp the summarizer reads.

epistemic_summarizer scores relevance by `item["id"]` (artifacts the task query matched) and recency by
`created_timestamp`. post-compact built its pools as {"finding", "impact", "when"}, {"unknown", "impact"} and a dead-end
dict with no id, so every real item scored relevance 0.0 and findings/unknowns had neutral recency: the block read as
task-ranked while ranking the same top-impact items every time. The summarizer's own tests used items that carried ids and
timestamps, which production never produced. These build a real database and ask the real loader.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture
def pools(tmp_path, monkeypatch):
    sys.path.insert(0, str(HOOKS.parent / "lib"))
    spec = importlib.util.spec_from_file_location("post_compact_pools", HOOKS / "post-compact.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from empirica.data.session_database import SessionDatabase

    path = str(tmp_path / "sessions.db")
    db = SessionDatabase(db_path=path)
    project_id = db.create_project(name="pools")
    session_id = db.create_session(ai_id="pools-test", project_id=project_id)
    ids = {
        "finding": db.log_finding(project_id=project_id, session_id=session_id, finding="a finding", impact=0.9),
        "unknown": db.log_unknown(project_id=project_id, session_id=session_id, unknown="an unknown", impact=0.6),
        "dead_end": db.log_dead_end(project_id=project_id, session_id=session_id, approach="x", why_failed="y"),
    }
    db.close()
    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", lambda *a, **k: SessionDatabase(db_path=path))
    return module._load_dynamic_context(session_id, "pools-test", {}), ids


def test_every_pool_item_carries_its_artifact_id(pools):
    context, ids = pools
    assert [f["id"] for f in context["recent_findings"]] == [ids["finding"]]
    assert [u["id"] for u in context["open_unknowns"]] == [ids["unknown"]]
    assert [d["id"] for d in context["critical_dead_ends"]] == [ids["dead_end"]]


def test_findings_and_unknowns_carry_a_timestamp_key_the_summarizer_reads(pools):
    context, _ = pools
    assert context["recent_findings"][0]["created_timestamp"]
    assert context["open_unknowns"][0]["created_timestamp"]
    assert context["critical_dead_ends"][0]["created_timestamp"]


def test_the_summarizer_ranks_a_matched_real_item_above_an_unmatched_one(pools):
    context, ids = pools
    spec = importlib.util.spec_from_file_location("summarizer_pools", HOOKS / "epistemic_summarizer.py")
    assert spec and spec.loader
    summarizer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(summarizer)
    finding, unknown = context["recent_findings"][0], context["open_unknowns"][0]
    # Same impact on both, so only relevance can separate them: the id is what the lookup keys on.
    finding["impact"] = unknown["impact"] = 0.8
    ranked = summarizer.rank_items([(unknown, "unknown"), (finding, "finding")], relevance_by_id={ids["finding"]: 1.0})
    weights = {item["id"]: weight for weight, item, _type in ranked}
    assert weights[ids["finding"]] > weights[ids["unknown"]]
