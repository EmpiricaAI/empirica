"""`projects-unregister --local` removes project rows from the LOCAL registry safely (goal d36bf0b6, workspace prop_fvxtvowsu).

empirica-workspace asked core to remove ephemeral project rows from ``global_projects`` and the ``entity_registry`` mirror in ONE
transaction, because workspace re-seeds the mirror from ``global_projects`` on every writing open, so deleting on its side alone
refills it. The verb is a flag on the existing ``projects-unregister`` (which unregisters on cortex), not a new verb.

The contract: PREVIEW by default (rows and their references, nothing written); ``--apply`` removes both rows atomically and
leaves an audit row holding the snapshot; a row that something REFERENCES, or an id that matches nothing, refuses the whole batch
so a typo cannot half-apply. Every refusal is paired with the case that goes through.

Each test builds its own workspace.db under tmp_path and points ``EMPIRICA_WORKSPACE_DB`` at it.
"""

from __future__ import annotations

import json
import sqlite3
import time
import types
import uuid

import pytest

from empirica.cli.command_handlers.projects_commands import handle_projects_unregister_command
from empirica.data.repositories.workspace_db import _ensure_workspace_schema


@pytest.fixture
def ws(tmp_path, monkeypatch):
    path = tmp_path / "workspace.db"
    monkeypatch.setenv("EMPIRICA_WORKSPACE_DB", str(path))
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    _ensure_workspace_schema(conn)
    yield conn
    conn.close()


def _project(conn, tmp_path, name="scratch", exists=False) -> str:
    pid = str(uuid.uuid4())
    traj = tmp_path / name / ".empirica"
    if exists:
        traj.mkdir(parents=True)
    conn.execute(
        "INSERT INTO global_projects (id, name, trajectory_path, created_timestamp, updated_timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        (pid, name, str(traj), time.time(), time.time()),
    )
    conn.execute(
        "INSERT INTO entity_registry (entity_type, entity_id, display_name, source_db, source_table, created_at) "
        "VALUES ('project', ?, ?, 'workspace.db', 'global_projects', ?)",
        (pid, name, time.time()),
    )
    conn.commit()
    return pid


def _args(ids, **kw):
    base = {"project_id": ",".join(ids), "local": True, "apply": False, "output": "json", "slug": None, "purge": False}
    return types.SimpleNamespace(**{**base, **kw})


def _run(args, capsys):
    try:
        handle_projects_unregister_command(args)
        code = 0
    except SystemExit as e:
        code = e.code or 0
    return code, json.loads(capsys.readouterr().out)


def _count(conn, table, where="1=1", params=()):
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]


def _table_exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None


# --- preview ------------------------------------------------------------------------------------------------------------------


def test_preview_lists_the_rows_and_writes_nothing(ws, tmp_path, capsys):
    a, b = _project(ws, tmp_path, "a"), _project(ws, tmp_path, "b", exists=True)
    code, out = _run(_args([a, b]), capsys)
    assert code == 0 and out["ok"] is True and out["applied"] is False
    assert {r["id"]: r["exists_on_disk"] for r in out["rows"]} == {a: False, b: True}
    assert _count(ws, "global_projects") == 2 and _count(ws, "entity_registry") == 2
    assert not _table_exists(ws, "registry_audit"), "a preview writes no audit table either"


# --- apply --------------------------------------------------------------------------------------------------------------------


def test_apply_removes_both_rows_and_leaves_an_audit_snapshot(ws, tmp_path, capsys):
    keep, drop = _project(ws, tmp_path, "keep"), _project(ws, tmp_path, "drop")
    code, out = _run(_args([drop], apply=True), capsys)
    assert code == 0 and out["applied"] is True and out["removed"] == 1
    assert _count(ws, "global_projects", "id = ?", (drop,)) == 0
    assert _count(ws, "entity_registry", "entity_id = ?", (drop,)) == 0
    assert _count(ws, "global_projects", "id = ?", (keep,)) == 1, "control: the other project is untouched"
    assert _count(ws, "entity_registry", "entity_id = ?", (keep,)) == 1
    audit = ws.execute("SELECT action, entity_id, snapshot FROM registry_audit").fetchall()
    assert [(r["action"], r["entity_id"]) for r in audit] == [("projects-unregister --local", drop)]
    assert json.loads(audit[0]["snapshot"])["global_projects"]["name"] == "drop", "the snapshot can restore the row"


def test_a_batch_is_one_transaction(ws, tmp_path, capsys):
    """If the second delete fails nothing is removed: the first delete rolls back with it, and so does its audit row."""
    a, b = _project(ws, tmp_path, "a"), _project(ws, tmp_path, "b")
    ws.execute(
        "CREATE TRIGGER block_b BEFORE DELETE ON entity_registry WHEN OLD.entity_id = '" + b + "' "
        "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
    )
    ws.commit()
    code, out = _run(_args([a, b], apply=True), capsys)
    assert code == 1 and out["ok"] is False
    assert _count(ws, "global_projects") == 2 and _count(ws, "entity_registry") == 2, "nothing was removed"
    assert not _table_exists(ws, "registry_audit") or _count(ws, "registry_audit") == 0


# --- refusals ------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("apply", [False, True])
def test_a_referenced_project_refuses_the_whole_batch(ws, tmp_path, capsys, apply):
    free, held = _project(ws, tmp_path, "free"), _project(ws, tmp_path, "held")
    ws.execute(
        "INSERT INTO entity_artifacts (id, artifact_type, artifact_id, entity_type, entity_id) "
        "VALUES (?, 'finding', 'f1', 'project', ?)",
        (str(uuid.uuid4()), held),
    )
    ws.commit()
    code, out = _run(_args([free, held], apply=apply), capsys)
    assert code == 1 and out["ok"] is False
    assert out["referenced"][0]["id"] == held and out["referenced"][0]["entity_artifacts"] == 1
    assert _count(ws, "global_projects") == 2, "the free one is not removed either"


def test_a_membership_in_either_direction_counts_as_a_reference(ws, tmp_path, capsys):
    member, group = _project(ws, tmp_path, "member"), _project(ws, tmp_path, "group")
    ws.execute(
        "INSERT INTO entity_memberships (entity_type, entity_id, group_type, group_id, joined_at, created_at) "
        "VALUES ('project', ?, 'organization', 'o-1', 1, 1)",
        (member,),
    )
    ws.execute(
        "INSERT INTO entity_memberships (entity_type, entity_id, group_type, group_id, joined_at, created_at) "
        "VALUES ('contact', 'c-1', 'project', ?, 1, 1)",
        (group,),
    )
    ws.commit()
    for pid in (member, group):
        code, out = _run(_args([pid], apply=True), capsys)
        assert code == 1 and out["referenced"][0]["id"] == pid and out["referenced"][0]["entity_memberships"] == 1


def test_an_unknown_id_refuses_the_batch_so_a_typo_cannot_half_apply(ws, tmp_path, capsys):
    real = _project(ws, tmp_path, "real")
    code, out = _run(_args([real, str(uuid.uuid4())], apply=True), capsys)
    assert code == 1 and out["ok"] is False and len(out["not_found"]) == 1
    assert _count(ws, "global_projects") == 1


def test_a_project_whose_only_neighbours_are_unrelated_rows_is_not_blocked(ws, tmp_path, capsys):
    """Control for the reference check: rows about OTHER entities must not read as references."""
    pid = _project(ws, tmp_path, "free")
    ws.execute(
        "INSERT INTO entity_artifacts (id, artifact_type, artifact_id, entity_type, entity_id) "
        "VALUES (?, 'finding', 'f1', 'organization', 'o-1')",
        (str(uuid.uuid4()),),
    )
    ws.commit()
    code, out = _run(_args([pid], apply=True), capsys)
    assert code == 0 and out["removed"] == 1


# --- flag hygiene -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [{"purge": True}, {"slug": "x"}])
def test_local_does_not_combine_with_the_cortex_flags(ws, tmp_path, capsys, extra):
    pid = _project(ws, tmp_path, "p")
    code, out = _run(_args([pid], **extra), capsys)
    assert code == 2 and out["ok"] is False


def test_apply_without_local_is_refused(ws, capsys):
    code, out = _run(_args(["x"], local=False, apply=True), capsys)
    assert code == 2 and "--local" in out["error"]


def test_local_needs_an_explicit_project_id(ws, capsys):
    code, out = _run(_args([], project_id=None), capsys)
    assert code == 2 and out["ok"] is False
