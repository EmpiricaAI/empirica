"""doctor finds lessons whose YAML says superseded_by while the store still serves them.

Asked for by cortex (2026-09-28) after retiring one by hand: a revision marked
before `lesson-create --supersedes` existed carries `superseded_by:` in its YAML,
which the store never reads, so the old version is served beside its successor
and nothing shows it.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
from pathlib import Path

from empirica.cli.command_handlers.doctor import PASS, SKIP, WARN, check_lesson_supersession_orphans


def _project(root: Path, lessons: dict[str, dict], edges: list[tuple[str, str]]) -> Path:
    (root / ".empirica" / "lessons").mkdir(parents=True)
    for lid, doc in lessons.items():
        (root / ".empirica" / "lessons" / f"{lid}.yaml").write_text(
            "\n".join(f"{k}: {v}" for k, v in {"id": lid, **doc}.items()) + "\n"
        )
    db = root / ".empirica" / "sessions" / "sessions.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE knowledge_graph (id TEXT, source_type TEXT, source_id TEXT, "
            "relation_type TEXT, target_type TEXT, target_id TEXT)"
        )
        for src, tgt in edges:
            conn.execute(
                "INSERT INTO knowledge_graph VALUES (?, 'lesson', ?, 'supersedes', 'lesson', ?)", (src + tgt, src, tgt)
            )
    return root


LESSONS = {"v1": {"name": "x", "superseded_by": "v2"}, "v2": {"name": "x"}, "v3": {"name": "x"}}


def test_an_orphan_is_flagged_with_a_repair_that_parses(tmp_path):
    root = _project(tmp_path, LESSONS, edges=[("v3", "v2")])  # v3 retired v2; v1's YAML marker was never recorded

    c = check_lesson_supersession_orphans(root)

    assert c.status == WARN
    assert c.data["orphans"] == {"v1": "v2"}
    assert "3 lesson file(s) read" in c.detail
    payload = c.hint.split("echo '", 1)[1].split("' | empirica", 1)[0]
    assert json.loads(payload) == {"resolutions": [{"type": "lesson", "id": "v1", "superseded_by": "v2"}]}


def test_recorded_supersessions_pass_and_say_how_much_was_read(tmp_path):
    root = _project(tmp_path, LESSONS, edges=[("v2", "v1"), ("v3", "v2")])

    c = check_lesson_supersession_orphans(root)

    assert c.status == PASS
    assert "3 lesson file(s) read" in c.detail and "1 declare superseded_by" in c.detail


def test_no_lessons_directory_is_a_skip_not_a_pass(tmp_path):
    """Zero files read must not read like a clean store."""
    assert check_lesson_supersession_orphans(tmp_path).status == SKIP


def test_the_printed_repair_is_accepted_by_resolve_artifacts(tmp_path, monkeypatch, capsys):
    """Close the loop: the payload doctor prints must retire the orphan when fed to the verb."""
    import empirica.core.lessons as lessons_mod
    import empirica.data.session_database as sdb
    from empirica.cli.command_handlers.graph_commands import handle_resolve_artifacts_command

    root = _project(tmp_path / "p", LESSONS, edges=[("v3", "v2")])
    payload = check_lesson_supersession_orphans(root).hint.split("echo '", 1)[1].split("' | empirica", 1)[0]

    written = []

    class _Store:
        def get_lesson(self, lid, layer="auto"):
            return object() if lid in LESSONS else None

        def add_edge(self, s, t, rel, **_):
            written.append((s, t, rel))
            return "e"

    real = sdb.SessionDatabase
    monkeypatch.setattr(sdb, "SessionDatabase", lambda *a, **k: real(db_path=str(tmp_path / "t.db")))
    monkeypatch.setattr(lessons_mod, "get_lesson_storage", lambda: _Store())

    class _Args:
        input = "-"
        output = "json"
        verbose = False

    stdin, sys.stdin = sys.stdin, io.StringIO(payload)
    try:
        handle_resolve_artifacts_command(_Args())
    finally:
        sys.stdin = stdin
    out = json.loads(capsys.readouterr().out)
    assert out["resolved"] == 1 and written == [("v2", "v1", "supersedes")]
