"""Copy one practitioner's local history from an old practice into this one (goal f357ecf4, David 2026-11-26).

Francisco joined the shared empirica-consult / empirica-portal practices and his artifacts and calibration stayed in his old
practices (empirica-ai-consulting, empirica-client-portal). project-register --reconcile re-keys ONE store in place and never
touches ai_id, and calibration keys on ai_id. This copies, never moves: rows of the old project and practitioner, their sessions
and what hangs off them, project_id and ai_id rewritten, ids kept, and a re-run adds nothing. The old store is never written.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.core import history_import as hi

OLD_PID, NEW_PID, OLD_AI, NEW_AI = "pid-old", "pid-new", "empirica-ai-consulting", "empirica-consult"

SCHEMA = """
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, ai_id TEXT, project_id TEXT);
CREATE TABLE project_findings (id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT, finding TEXT);
CREATE TABLE goals (id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT, ai_id TEXT, objective TEXT);
CREATE TABLE subtasks (id TEXT PRIMARY KEY, goal_id TEXT, description TEXT);
CREATE TABLE artifact_edges (from_id TEXT, to_id TEXT, relation TEXT);
CREATE TABLE grounded_beliefs (id TEXT PRIMARY KEY, session_id TEXT, ai_id TEXT, vector TEXT, value REAL);
CREATE TABLE reflexes (id INTEGER PRIMARY KEY, session_id TEXT, project_id TEXT, phase TEXT);
CREATE TABLE epistemic_events (id TEXT PRIMARY KEY, session_id TEXT, kind TEXT);
CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT);
"""


def _db(path, rows):
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    for table, values in rows:
        conn.execute(f"INSERT INTO {table} VALUES ({','.join('?' * len(values))})", values)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def stores(tmp_path):
    src = _db(
        tmp_path / "old.db",
        [
            ("sessions", ("s1", OLD_AI, OLD_PID)),
            ("sessions", ("s-other", "someone-else", "pid-elsewhere")),
            ("project_findings", ("f1", OLD_PID, "s1", "his finding")),
            ("project_findings", ("f-other", "pid-elsewhere", "s-other", "not his")),
            ("goals", ("g1", OLD_PID, "s1", OLD_AI, "his goal")),
            ("subtasks", ("t1", "g1", "his task")),
            ("subtasks", ("t-other", "g-elsewhere", "not his")),
            ("artifact_edges", ("f1", "g1", "evidence")),
            ("artifact_edges", ("f-other", "x", "related")),
            ("grounded_beliefs", ("b1", "s1", OLD_AI, "know", 0.7)),
            ("reflexes", (1, "s1", OLD_PID, "PREFLIGHT")),
            ("epistemic_events", ("e1", "s1", "check")),
            ("projects", (OLD_PID, "empirica-ai-consulting")),
        ],
    )
    dst = _db(
        tmp_path / "new.db", [("sessions", ("s-new", NEW_AI, NEW_PID)), ("projects", (NEW_PID, "empirica-consult"))]
    )
    return src, dst


def _rows(path, sql):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _snapshot(path):
    conn = sqlite3.connect(str(path))
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {t: sorted(map(repr, conn.execute(f"SELECT * FROM {t}").fetchall())) for t in tables}
    finally:
        conn.close()


def _copy(src, dst, apply, **kw):
    return hi.copy_history(
        src, dst, old_project_id=OLD_PID, new_project_id=NEW_PID, old_ai_id=OLD_AI, new_ai_id=NEW_AI, apply=apply, **kw
    )


def test_preview_counts_his_rows_and_writes_nothing(stores):
    src, dst = stores
    before = _snapshot(dst)
    plan = _copy(src, dst, apply=False)
    assert not plan["applied"] and _snapshot(dst) == before
    counts = plan["tables"]
    assert counts["sessions"] == 1 and counts["project_findings"] == 1 and counts["goals"] == 1
    assert counts["subtasks"] == 1 and counts["artifact_edges"] == 1 and counts["grounded_beliefs"] == 1
    assert counts["reflexes"] == 1 and counts["epistemic_events"] == 1
    assert "projects" in plan["skipped"]  # the project's own row is the target's, never copied


def test_apply_copies_with_project_and_practitioner_rewritten(stores):
    src, dst = stores
    _copy(src, dst, apply=True)
    assert _rows(dst, "SELECT session_id, ai_id, project_id FROM sessions WHERE session_id='s1'") == [
        ("s1", NEW_AI, NEW_PID)
    ]
    assert _rows(dst, "SELECT project_id FROM project_findings WHERE id='f1'") == [(NEW_PID,)]
    assert _rows(dst, "SELECT project_id, ai_id FROM goals WHERE id='g1'") == [(NEW_PID, NEW_AI)]
    assert _rows(dst, "SELECT goal_id FROM subtasks") == [("g1",)]  # ids kept, so the task still hangs off its goal
    assert _rows(dst, "SELECT from_id, to_id FROM artifact_edges") == [("f1", "g1")]
    assert _rows(dst, "SELECT ai_id FROM grounded_beliefs WHERE id='b1'") == [(NEW_AI,)]  # calibration follows him
    assert _rows(dst, "SELECT COUNT(*) FROM project_findings WHERE id='f-other'") == [(0,)]  # nobody else's rows


def test_the_old_store_is_never_written(stores):
    src, dst = stores
    before = _snapshot(src)
    _copy(src, dst, apply=True)
    assert _snapshot(src) == before


def test_a_rerun_adds_nothing(stores):
    src, dst = stores
    _copy(src, dst, apply=True)
    after_first = _snapshot(dst)
    again = _copy(src, dst, apply=True)
    assert _snapshot(dst) == after_first and sum(again["tables"].values()) == 0


def test_a_row_already_in_the_target_is_kept_not_overwritten(stores):
    src, dst = stores
    conn = sqlite3.connect(str(dst))
    conn.execute("INSERT INTO project_findings VALUES ('f1', ?, 's-new', 'target copy')", (NEW_PID,))
    conn.commit()
    conn.close()
    preview = _copy(src, dst, apply=False)
    assert preview["tables"]["project_findings"] == 0 and preview["kept_in_target"] == {"project_findings": 1}
    result = _copy(src, dst, apply=True)
    assert result["tables"] == preview["tables"]  # the preview is exact
    assert _rows(dst, "SELECT finding FROM project_findings WHERE id='f1'") == [("target copy",)]


def test_apply_keeps_a_backup_of_the_target(stores):
    src, dst = stores
    before = _snapshot(dst)
    result = _copy(src, dst, apply=True)
    assert _snapshot(result["backup"]) == before


def test_the_same_store_on_both_sides_is_refused(stores):
    src, _ = stores
    with pytest.raises(ValueError, match="same"):
        _copy(src, src, apply=False)


def test_a_table_the_target_lacks_is_reported_not_dropped_silently(stores):
    src, dst = stores
    conn = sqlite3.connect(str(src))
    conn.execute("CREATE TABLE only_old (id TEXT PRIMARY KEY, project_id TEXT)")
    conn.execute("INSERT INTO only_old VALUES ('o1', ?)", (OLD_PID,))
    conn.commit()
    conn.close()
    plan = _copy(src, dst, apply=False)
    assert plan["missing_in_target"] == {"only_old": 1}


def test_derived_code_and_concept_models_are_not_copied(stores):
    src, dst = stores
    for path in (src, dst):
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE concept_edges (id TEXT PRIMARY KEY, project_id TEXT)")
        conn.execute("CREATE TABLE codebase_entities (id TEXT PRIMARY KEY, project_id TEXT)")
        conn.commit()
        conn.close()
    conn = sqlite3.connect(str(src))
    conn.execute("INSERT INTO concept_edges VALUES ('c1', ?)", (OLD_PID,))
    conn.execute("INSERT INTO codebase_entities VALUES ('e1', ?)", (OLD_PID,))
    conn.commit()
    conn.close()
    plan = _copy(src, dst, apply=False)
    assert {"concept_edges", "codebase_entities"} <= set(plan["skipped"])
    assert "concept_edges" not in plan["tables"]


# ── the verb ────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _practice_dir(root, pid, ai_id, rows):
    (root / ".empirica" / "sessions").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text(f"project_id: {pid}\nai_id: {ai_id}\n")
    _db(root / ".empirica" / "sessions" / "sessions.db", rows)
    return root


def test_the_verb_previews_then_applies_from_project_yaml(tmp_path, capsys, monkeypatch):
    import json
    import types

    from empirica.cli.command_handlers.history_import_commands import handle_history_import_command

    old = _practice_dir(tmp_path / "old", OLD_PID, OLD_AI, [("project_findings", ("f1", OLD_PID, "s1", "x"))])
    new = _practice_dir(tmp_path / "new", NEW_PID, NEW_AI, [])
    monkeypatch.chdir(new)
    args = types.SimpleNamespace(from_path=str(old), into=None, apply=False, output="json", types="finding")
    assert handle_history_import_command(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["tables"]["project_findings"] == 1 and not out["applied"]
    assert out["from"]["ai_id"] == OLD_AI and out["into"]["ai_id"] == NEW_AI and out["types"] == ["finding"]
    args.apply = True
    assert handle_history_import_command(args) == 0
    capsys.readouterr()
    assert _rows(new / ".empirica/sessions/sessions.db", "SELECT project_id FROM project_findings") == [(NEW_PID,)]


def test_the_verb_refuses_copying_a_practice_into_itself(tmp_path, capsys, monkeypatch):
    import types

    from empirica.cli.command_handlers.history_import_commands import handle_history_import_command

    here = _practice_dir(tmp_path / "p", NEW_PID, NEW_AI, [])
    monkeypatch.chdir(here)
    assert (
        handle_history_import_command(types.SimpleNamespace(from_path=str(here), into=None, apply=False, output="json"))
        == 1
    )
    assert "this practice" in capsys.readouterr().out


# ── a mixed store (prop_aiaro7s5: one real store held six ai_ids and three project ids) ─────────────────────────────────


@pytest.fixture
def mixed(stores):
    src, dst = stores
    conn = sqlite3.connect(str(src))
    for table, values in [
        ("sessions", ("s-gui", "gui-dev", OLD_PID)),  # another practitioner wrote into this project
        ("project_findings", ("f-gui", OLD_PID, "s-gui", "a finding of the practice, written by gui-dev")),
        ("grounded_beliefs", ("b-gui", "s-gui", "gui-dev", "know", 0.4)),
        ("reflexes", (2, "s-gui", OLD_PID, "PREFLIGHT")),
        ("project_findings", ("f-stray", "pid-stray", "s1", "a stray project's finding")),
        ("goals", ("g-none", None, "s1", OLD_AI, "a goal with no project")),
        ("sessions", ("s-noproj", OLD_AI, None)),  # his own session with no project
        ("grounded_beliefs", ("b-noproj", "s-noproj", OLD_AI, "do", 0.6)),
    ]:
        conn.execute(f"INSERT INTO {table} VALUES ({','.join('?' * len(values))})", values)
    conn.commit()
    conn.close()
    return src, dst


def test_artifacts_go_by_project_whoever_wrote_them(mixed):
    src, dst = mixed
    _copy(src, dst, apply=True)
    assert _rows(dst, "SELECT id FROM project_findings ORDER BY id") == [("f-gui",), ("f1",)]


def test_another_practitioners_calibration_stays_behind(mixed):
    src, dst = mixed
    plan = _copy(src, dst, apply=True)
    assert _rows(dst, "SELECT COUNT(*) FROM sessions WHERE session_id = 's-gui'") == [(0,)]
    assert _rows(dst, "SELECT COUNT(*) FROM grounded_beliefs WHERE ai_id = 'gui-dev'") == [(0,)]
    assert _rows(dst, "SELECT COUNT(*) FROM reflexes WHERE session_id = 's-gui'") == [(0,)]
    assert plan["left_behind"]["grounded_beliefs"] == 1 and plan["left_behind"]["sessions"] == 2  # s-gui and s-other


def test_his_own_sessions_without_a_project_come_along(mixed):
    src, dst = mixed
    _copy(src, dst, apply=True)
    assert _rows(dst, "SELECT ai_id FROM grounded_beliefs WHERE id = 'b-noproj'") == [(NEW_AI,)]


def test_strays_of_other_projects_and_projectless_goals_stay_and_are_counted(mixed):
    src, dst = mixed
    plan = _copy(src, dst, apply=False)
    assert plan["left_behind"]["project_findings"] == 2  # f-other, f-stray
    assert plan["left_behind"]["goals"] == 1  # g-none
    _copy(src, dst, apply=True)
    assert _rows(dst, "SELECT COUNT(*) FROM goals WHERE id = 'g-none'") == [(0,)]


def test_a_type_filter_copies_only_those_types_and_no_dangling_edge(mixed):
    src, dst = mixed
    plan = _copy(src, dst, apply=True, types={"finding"})
    assert _rows(dst, "SELECT COUNT(*) FROM goals") == [(0,)] and _rows(dst, "SELECT COUNT(*) FROM subtasks") == [(0,)]
    assert _rows(dst, "SELECT COUNT(*) FROM artifact_edges") == [(0,)]  # f1 -> g1: g1 was not copied
    assert plan["types"] == ["finding"]


def test_an_edge_to_an_artifact_already_in_the_target_is_copied(mixed):
    src, dst = mixed
    _copy(src, dst, apply=True, types={"goal"})  # g1 first
    _copy(src, dst, apply=True, types={"finding"})  # then f1: its edge to g1 now has both ends
    assert _rows(dst, "SELECT from_id, to_id FROM artifact_edges") == [("f1", "g1")]


def test_artifacts_only_leaves_every_practitioner_row(mixed):
    src, dst = mixed
    plan = _copy(src, dst, apply=True, include_calibration=False)
    assert _rows(dst, "SELECT COUNT(*) FROM grounded_beliefs") == [(0,)]
    assert _rows(dst, "SELECT session_id FROM sessions") == [("s-new",)]
    assert plan["tables"]["project_findings"] == 2


def test_an_unknown_type_is_refused(stores):
    src, dst = stores
    with pytest.raises(ValueError, match="unknown artifact type"):
        _copy(src, dst, apply=False, types={"findings"})
