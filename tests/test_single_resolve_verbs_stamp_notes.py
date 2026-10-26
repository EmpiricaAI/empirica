"""The single-artifact resolve verbs must reach the git note, as the batch and REST paths do (goal 4039b3c5, reconcile 2026-10-26).

Notes are the canonical log and a from-notes rebuild re-creates the row from them, so a resolution left only in sqlite is undone by the
next rebuild. `resolve-artifacts` (batch) and the REST routes stamp the note; `unknown-resolve` and `finding-resolve`, the verbs a
practitioner actually types, called only `db.resolve_*` and did not. Measured: `doctor --reconcile-notes` on core found 19 resolved
unknowns whose notes still read open, among them ones resolved that same day with `unknown-resolve`.
"""

from __future__ import annotations

import argparse
import json
import subprocess

import pytest


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A git repo with one active note per artifact, and a database wired to the verbs under test."""
    import empirica.data.session_database as sdb

    def run(*a):
        return subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, text=True, check=True)

    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (tmp_path / "f").write_text("x")
    run("add", "f")
    run("commit", "-qm", "i")
    head = run("rev-parse", "HEAD").stdout.strip()

    db_file = str(tmp_path / "t.db")
    real = sdb.SessionDatabase
    seed = real(db_path=db_file)
    for uid in ("u-1111111-0000-4000-8000-000000000001", "u-2222222-0000-4000-8000-000000000002"):
        seed.conn.execute(
            "INSERT INTO project_unknowns (id, project_id, session_id, unknown, created_timestamp, unknown_data) "
            "VALUES (?, 'p', 's', 'a question', 0.0, '{}')",
            (uid,),
        )
        run(
            "notes",
            f"--ref=empirica/unknowns/{uid}",
            "add",
            "-f",
            "-m",
            json.dumps({"unknown_id": uid, "unknown": "a question", "resolved": False}),
            head,
        )
    for fid in ("f-3333333-0000-4000-8000-000000000003", "f-4444444-0000-4000-8000-000000000004"):
        seed.conn.execute(
            "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, finding_data) "
            "VALUES (?, 'p', 's', 'a claim', 0.0, '{}')",
            (fid,),
        )
        run(
            "notes",
            f"--ref=empirica/findings/{fid}",
            "add",
            "-f",
            "-m",
            json.dumps({"finding_id": fid, "finding": "a claim", "is_resolved": False}),
            head,
        )
    seed.conn.commit()
    seed.close()

    monkeypatch.setattr(sdb, "SessionDatabase", lambda *a, **k: real(db_path=db_file))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _note(repo, namespace, aid):
    ref = f"empirica/{namespace}/{aid}"
    listing = subprocess.run(
        ["git", "notes", f"--ref={ref}", "list"], cwd=repo, capture_output=True, text=True
    ).stdout.split()
    body = subprocess.run(["git", "cat-file", "-p", listing[0]], cwd=repo, capture_output=True, text=True).stdout
    return json.loads(body)


U1 = "u-1111111-0000-4000-8000-000000000001"
U2 = "u-2222222-0000-4000-8000-000000000002"
F3 = "f-3333333-0000-4000-8000-000000000003"
F4 = "f-4444444-0000-4000-8000-000000000004"


def test_unknown_resolve_stamps_the_note(project):
    from empirica.cli.command_handlers.artifact_log_commands import handle_unknown_resolve_command

    args = argparse.Namespace(
        unknown_id=U1, resolved_by="answered in finding X", resolution_finding_id=None, output="json"
    )
    assert handle_unknown_resolve_command(args) == 0
    note = _note(project, "unknowns", U1)
    assert note["resolved"] is True and note["resolved_by"] == "answered in finding X"
    assert note["unknown"] == "a question", "the question text survives"


def test_finding_resolve_stamps_the_note_with_its_kind(project):
    from empirica.cli.command_handlers.artifact_log_commands import handle_finding_resolve_command

    args = argparse.Namespace(
        finding_id=F3, resolution="was false", superseded_by=None, resolution_kind="retracted", output="json"
    )
    assert handle_finding_resolve_command(args) == 0
    note = _note(project, "findings", F3)
    assert note["is_resolved"] is True and note["resolution_kind"] == "retracted"
    assert note["finding"] == "a claim"


def test_the_artifacts_nobody_resolved_keep_open_notes(project):
    """Control: only the named artifact is stamped."""
    from empirica.cli.command_handlers.artifact_log_commands import (
        handle_finding_resolve_command,
        handle_unknown_resolve_command,
    )

    handle_unknown_resolve_command(
        argparse.Namespace(unknown_id=U1, resolved_by="x", resolution_finding_id=None, output="json")
    )
    handle_finding_resolve_command(
        argparse.Namespace(finding_id=F3, resolution="x", superseded_by=None, resolution_kind=None, output="json")
    )
    assert _note(project, "unknowns", U2)["resolved"] is False
    assert not _note(project, "findings", F4).get("is_resolved")


def test_an_artifact_with_no_note_still_resolves_in_sqlite(project, tmp_path):
    """Control: the stamp is best-effort. A resolve must not fail because there is nothing to stamp."""
    import sqlite3

    from empirica.cli.command_handlers.artifact_log_commands import handle_unknown_resolve_command

    lonely = "u-5555555-0000-4000-8000-000000000005"
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.execute(
        "INSERT INTO project_unknowns (id, project_id, session_id, unknown, created_timestamp, unknown_data) "
        "VALUES (?, 'p', 's', 'no note', 0.0, '{}')",
        (lonely,),
    )
    conn.commit()
    conn.close()
    args = argparse.Namespace(unknown_id=lonely, resolved_by="x", resolution_finding_id=None, output="json")
    assert handle_unknown_resolve_command(args) == 0
