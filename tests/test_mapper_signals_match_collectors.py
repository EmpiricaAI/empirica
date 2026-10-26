"""The noetic and sentinel signals fire on the evidence their collectors actually write (goal 0b644a20, second half).

`_signal_noetic_activity` read `noetic["file_reads"]` and `["searches"]`, `_signal_sentinel` read `sentinel["blocks"]`. Nothing in the post-test package
writes any of the three: the noetic collector emits `unknowns_surfaced` and `investigation_thoroughness {investigate_rounds, total_checks}`, the sentinel
collector emits `check_proceed_ratio {proceed, investigate}`. Both signals were dead the way the test signal was. They now say what the data supports:
how many CHECK rounds were held at `investigate`. The collectors run for real here, on a minimal real database, into the real `summarize_evidence`.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from empirica.core.post_test.collector import EvidenceBundle, PostTestCollector
from empirica.core.post_test.mapper import _signal_noetic_activity, _signal_sentinel, summarize_evidence


class _DB:
    def __init__(self, decisions: list[str]):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE reflexes (session_id TEXT, phase TEXT, reflex_data TEXT, timestamp REAL);
            CREATE TABLE project_unknowns (session_id TEXT, transaction_id TEXT, created_timestamp REAL);
            CREATE TABLE project_dead_ends (session_id TEXT, transaction_id TEXT, created_timestamp REAL);
            CREATE TABLE project_findings (session_id TEXT, transaction_id TEXT, created_timestamp REAL);
            """
        )
        for i, decision in enumerate(decisions):
            self.conn.execute(
                "INSERT INTO reflexes VALUES ('s', 'CHECK', ?, ?)", (json.dumps({"decision": decision}), float(i))
            )

    def close(self):
        self.conn.close()


def _signals(decisions: list[str]) -> list[str]:
    collector = PostTestCollector("s", db=_DB(decisions))
    items = collector._collect_noetic_metrics() + collector._collect_sentinel_metrics()
    return summarize_evidence(EvidenceBundle(session_id="s", items=items))["signals"]


def test_checks_held_at_investigate_are_named_by_the_sentinel_signal():
    assert "Sentinel held 2 of 3 CHECKs at investigate before proceeding" in _signals(
        ["investigate", "investigate", "proceed"]
    )


def test_a_first_time_proceed_gives_no_sentinel_signal():
    assert not any(s.startswith("Sentinel") for s in _signals(["proceed"]))


def test_three_investigate_rounds_is_repeated_investigation_and_two_is_not():
    assert "Repeated investigation (3 of 4 CHECKs held at investigate)" in _signals(
        ["investigate", "investigate", "investigate", "proceed"]
    )
    assert not any(s.startswith("Repeated investigation") for s in _signals(["investigate", "investigate", "proceed"]))


def test_no_checks_at_all_gives_neither_signal():
    assert not any(s.startswith(("Sentinel", "Repeated")) for s in _signals([]))


@pytest.mark.parametrize(
    "summary",
    [
        {"sentinel": {"check_proceed_ratio": 0.5}},
        {"sentinel": {"check_proceed_ratio": {"proceed": "x", "investigate": None}}},
        {"sentinel": {}},
        {"noetic": {"investigation_thoroughness": 0.9}},
        {"noetic": {"investigation_thoroughness": {"investigate_rounds": "many"}}},
        {"noetic": {"unknowns_surfaced": {"count": 4}}},
        {},
    ],
)
def test_evidence_of_another_shape_never_crashes_either_signal(summary):
    assert _signal_sentinel(summary) is None
    assert _signal_noetic_activity(summary) is None
