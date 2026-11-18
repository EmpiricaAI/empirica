"""The stylometry collector runs in production, and the gate it backs is named after what it emits.

PostTestCollector built the prose profile's collector list without
prose_stylometry, and ProseEvidenceCollector.collect_all (the only list that had
it) has no callers, so no POSTFLIGHT ever produced stylometry evidence while
goals-create advertised a prose_stylometry gate. The fingerprint costs ~4 ms on
2,000 words (median of 10, measured 2026-11-18), under the 20 ms bar, so it is
wired in rather than withdrawn. The advertised metric name was also one nothing
emits (`prose_stylometry_adherence`); the example now names the real metric.
"""

from __future__ import annotations

import sqlite3

from empirica.core.post_test.collector import PostTestCollector


class _DB:
    def __init__(self, finding: str):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE project_findings (session_id TEXT, finding TEXT)")
        self.conn.execute("CREATE TABLE goals (session_id TEXT, objective TEXT)")
        self.conn.execute("CREATE TABLE handoff_reports (session_id TEXT, task_summary TEXT)")
        self.conn.execute("INSERT INTO project_findings VALUES ('s1', ?)", (finding,))

    def close(self):
        self.conn.close()


_TEXT = " ".join(
    f"The practice measured transaction {i} and it's clear that we don't yet know why the gap held. "
    "However, the evidence suggests a pattern worth checking — and the next run will tell."
    for i in range(20)
)


def test_the_prose_profile_runs_the_stylometry_collector(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("EMPIRICA_VOICE", raising=False)
    c = PostTestCollector(session_id="s1", db=_DB(_TEXT))
    collectors = dict(c._get_prose_collectors())
    assert "prose_stylometry" in collectors
    items = collectors["prose_stylometry"]()
    assert [i.metric_name for i in items] == ["prose_stylometry_fingerprint"]


def test_the_advertised_gate_names_a_metric_the_collector_emits():
    import inspect

    from empirica.cli.command_handlers import goal_commands
    from empirica.core.post_test import prose_collector

    src = inspect.getsource(goal_commands)
    assert "prose_stylometry_adherence" not in src
    assert "quality_gate:prose_stylometry_composite_drift@<=0.25" in src
    assert '"prose_stylometry_composite_drift"' in inspect.getsource(prose_collector)
