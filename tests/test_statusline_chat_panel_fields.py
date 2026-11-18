"""The chat statusline shows the fields the summary actually carries.

`StatuslinePanel._format_summary` read `clarity`, `open_unknowns` and friends as
attributes of `StatuslineSummary`, which has none of them: the full vectors live
in `summary.raw["vectors"]`, and unknowns were never counted. So `Cl` never
rendered and the unknowns count was a constant 0. Full mode also labelled the
open-goal count "artifacts".
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from empirica.cli.tui.chat.statusline import StatuslinePanel
from empirica.core.cockpit.enrichment import StatuslineSummary, statusline_summary

PROJECT = "proj-1"
SESSION = "sess-1"


def _summary(raw_vectors: dict) -> StatuslineSummary:
    return StatuslineSummary(
        instance_id="i",
        found=True,
        label=None,
        know=raw_vectors.get("know"),
        uncertainty=raw_vectors.get("uncertainty"),
        context=raw_vectors.get("context"),
        completion=raw_vectors.get("completion"),
        confidence=0.7,
        open_goals=2,
        artifact_count=2,
        raw={"vectors": raw_vectors},
    )


def test_clarity_from_raw_vectors_renders_cl():
    panel = StatuslinePanel()
    panel._mode = "learning"
    text = panel._format_summary(_summary({"know": 0.8, "uncertainty": 0.2, "context": 0.7, "clarity": 0.9}))
    assert "Cl" in text


def test_full_mode_labels_goal_count_as_goals():
    panel = StatuslinePanel()
    panel._mode = "full"
    text = panel._format_summary(_summary({"know": 0.8}))
    assert "artifacts" not in text
    assert "goals 2" in text


def _seed_db(project: Path, open_unknowns: int, resolved: int) -> None:
    db = project / ".empirica" / "sessions" / "sessions.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY, project_id TEXT);
        CREATE TABLE epistemic_snapshots (session_id TEXT, vectors TEXT, timestamp TEXT);
        CREATE TABLE goals (id TEXT, project_id TEXT, is_completed INTEGER, status TEXT);
        CREATE TABLE project_unknowns (id TEXT, project_id TEXT, goal_id TEXT, is_resolved BOOLEAN);
        """
    )
    conn.execute("INSERT INTO sessions VALUES (?, ?)", (SESSION, PROJECT))
    conn.execute(
        "INSERT INTO epistemic_snapshots VALUES (?, ?, ?)",
        (SESSION, json.dumps({"know": 0.7, "uncertainty": 0.3, "clarity": 0.6}), str(time.time())),
    )
    for i in range(open_unknowns):
        conn.execute("INSERT INTO project_unknowns VALUES (?, ?, NULL, 0)", (f"u{i}", PROJECT))
    for i in range(resolved):
        conn.execute("INSERT INTO project_unknowns VALUES (?, ?, NULL, 1)", (f"r{i}", PROJECT))
    conn.execute("INSERT INTO project_unknowns VALUES ('other', 'another-project', NULL, 0)")
    conn.commit()
    conn.close()


def test_one_open_unknown_in_sessions_db_renders_a_count_of_one(tmp_path):
    _seed_db(tmp_path, open_unknowns=1, resolved=2)
    summary = statusline_summary(instance_id="i", project_path=str(tmp_path), session_id=SESSION)
    assert summary.found
    panel = StatuslinePanel()
    panel._mode = "learning"
    text = panel._format_summary(summary)
    assert "❓1" in text, text
