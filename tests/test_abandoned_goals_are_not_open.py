"""An abandoned goal is not open work, in every reader that says "open" (pre-publish sweep of 1.14.20, goals area).

`abandoned` keeps `is_completed = 0` on purpose: abandoned is not done, and every `status = 'completed'` query counts as delivered work, calibration
included (test_goal_abandoned_state.py). The cost is that any reader filtering "open" on `is_completed = 0` alone lists abandoned goals as live. Three
injection paths excluded the status already, and the `goals-list` default was fixed on 2026-11-04; the sweep found the same filter in about a dozen more
readers: the bootstrap's active circle, the artifact-to-goal attach tiers, the serve route, the memory and brief sections, the cockpit counts and the
POSTFLIGHT criteria readers.

Three of them are tested by behaviour below. The last test is a guard over the class: every `is_completed = 0` in the package either excludes abandoned near
the filter or carries an `open-goal-ok: <why>` comment, so the next reader added cannot repeat it silently.
"""

from __future__ import annotations

import json
import re
import time
import types
from pathlib import Path

import pytest

from empirica.data.session_database import SessionDatabase

ROOT = Path(__file__).parent.parent


@pytest.fixture
def world(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    (project / ".empirica" / "sessions").mkdir(parents=True)
    db_path = str(project / ".empirica" / "sessions" / "sessions.db")
    monkeypatch.setenv("EMPIRICA_SESSION_DB", db_path)
    db = SessionDatabase(db_path=db_path)
    pid = db.create_project(name="abandon")
    sid = db.create_session(ai_id="t", project_id=pid)
    w = types.SimpleNamespace(db=db, pid=pid, sid=sid, project=project)
    counter = [0]

    def goal(status, transaction_id=None):
        counter[0] += 1
        gid = f"goal-{counter[0]:03d}-{status}"
        db.conn.execute(
            "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, goal_data, status, is_completed, project_id, transaction_id) "
            "VALUES (?,?,?,?,?,?,?,0,?,?)",
            (
                gid,
                sid,
                f"obj {gid}",
                "{}",
                time.time() + counter[0],
                json.dumps({"subtasks": []}),
                status,
                pid,
                transaction_id,
            ),
        )
        db.conn.commit()
        return gid

    w.goal = goal
    yield w
    db.close()


def test_the_bootstrap_active_circle_does_not_list_an_abandoned_goal(world):
    from empirica.core.bootstrap.circles import circle_1_active_state

    live = world.goal("in_progress")
    world.goal("abandoned")  # newer than the live one: it would have taken the first slot
    out = circle_1_active_state(world.project, world.pid, limits={"in_progress_goals": 1})
    assert [g["id"] for g in out["in_progress_goals"]] == [live]


def test_a_new_artifact_is_not_attached_to_an_abandoned_goal(world):
    from empirica.cli.command_handlers.artifact_log_commands import _resolve_goal_for_artifact

    live = world.goal("in_progress", transaction_id="tx-1")
    world.goal("abandoned", transaction_id="tx-1")  # newer, same transaction and session
    got = _resolve_goal_for_artifact(None, world.sid, world.db, transaction_id="tx-1", project_id=world.pid)
    assert got == live


def test_the_serve_route_active_filter_leaves_out_abandoned_goals(world):
    from empirica.api.routes.artifacts import _list_goals

    live = world.goal("in_progress")
    world.goal("abandoned")
    assert [g["id"] for g in _list_goals(world.db, world.pid, "active", 50)] == [live]


# ── the guard over the class ───────────────────────────────────────────────────────────────────────────────────

PATTERN = re.compile(r"is_completed\s*=\s*0")


def _sites():
    for path in sorted((ROOT / "empirica").rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        if rel.endswith("migrations.py"):
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
        for i, line in enumerate(lines):
            if PATTERN.search(line):
                window = "\n".join(lines[max(0, i - 10) : i + 11])
                yield rel, i + 1, line, window


def test_every_open_goal_reader_excludes_abandoned_or_is_a_reviewed_exception():
    """A reviewed exception carries a comment `open-goal-ok: <why>` within a few lines of the filter."""
    loose = []
    for rel, lineno, line, window in _sites():
        if line.lstrip().startswith("#") or "abandoned" in window or "open-goal-ok" in window:
            continue
        loose.append(f"{rel}:{lineno}: {line.strip()[:110]}")
    assert not loose, (
        "readers that list abandoned goals as open (exclude them with COALESCE(status, '') != 'abandoned', "
        "or mark a reviewed exception with a comment 'open-goal-ok: <why>'):\n" + "\n".join(loose)
    )
