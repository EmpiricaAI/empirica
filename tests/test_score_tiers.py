"""score_tiers.py: per-tier precision, pooled recall and the mechanical anchor count from a tier workflow's records.

The synthetic thread has two real events (clusters 1 and 2). The strong tier finds both, correctly typed. The weak tier finds one,
mistypes it, adds an unsupported claim and quotes a sentence that is not in the thread. Each number below is therefore known in advance.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "empirica/plugins/claude-code-integration/skills/agent-review/scripts/score_tiers.py"
)


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("score_tiers", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _art(kind, text, anchor, quote):
    return {"type": kind, "text": text, "anchor": anchor, "quote": quote, "grounding": "read", "needs_hindsight": False}


@pytest.fixture
def records(tmp_path):
    turns = [
        {"n": 1, "role": "assistant", "text": "I will read the loader."},
        {"n": 2, "role": "tool_result", "text": "loader.py:10: data.get('trigger', 0.5)"},
        {"n": 3, "role": "tool_result", "text": "ls: cannot access 'policy.yaml': No such file or directory"},
    ]
    (tmp_path / "t1").mkdir()
    (tmp_path / "t1" / "turns.json").write_text(json.dumps(turns))
    strong = {
        "artifacts": [
            _art("finding", "loader reads top-level keys", 2, "data.get('trigger', 0.5)"),
            _art("unknown", "policy file is missing", 3, "cannot access 'policy.yaml'"),
        ],
        "edges": [],
    }
    weak = {
        "artifacts": [
            _art("assumption", "loader reads top-level keys", 2, "data.get('trigger', 0.5)"),
            _art("finding", "loader validates every key", 2, "data.validate(all_keys)"),
        ],
        "edges": [],
    }
    pool = [
        {"id": "P1", "tier": "strong", "k": 0},
        {"id": "P2", "tier": "weak", "k": 0},
        {"id": "P3", "tier": "strong", "k": 1},
        {"id": "P4", "tier": "weak", "k": 1},
    ]
    adj = {
        "items": [
            {"id": "P1", "supported": True, "type_correct": True, "cluster": 1},
            {"id": "P2", "supported": True, "type_correct": False, "cluster": 1},
            {"id": "P3", "supported": True, "type_correct": True, "cluster": 2},
            {"id": "P4", "supported": False, "type_correct": False, "cluster": 3},
        ]
    }
    return tmp_path, [{"id": "t1", "tiers": ["strong", "weak"], "stamps": [strong, weak], "pool": pool, "adj": adj}]


def test_the_strong_tier_scores_perfectly(mod, records):
    traces, recs = records
    r = mod.score(recs, traces)["strong"]
    assert (r["anchors_valid"], r["artifacts"], r["precision"], r["pooled_recall"]) == (2, 2, 1.0, 1.0)


def test_the_weak_tier_loses_on_each_axis_it_should(mod, records):
    traces, recs = records
    r = mod.score(recs, traces)["weak"]
    assert r["anchors_valid"] == 1 and r["artifacts"] == 2  # the invented quote fails the mechanical check
    assert r["precision"] == 0.0  # one mistyped, one unsupported
    assert r["supported_share"] == 0.5 and r["mistyped_share_of_supported"] == 1.0
    assert r["pooled_recall"] == 0.5  # found cluster 1 of {1, 2}


def test_by_type_counts_good_over_total(mod, records):
    traces, recs = records
    assert mod.score(recs, traces)["strong"]["by_type"] == {"finding": [1, 1], "unknown": [1, 1]}


def test_the_cli_refuses_wrong_arguments(mod, capsys):
    assert mod.main([]) == 2
