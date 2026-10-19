"""`doctor --scrub-secrets` must not report a store it could not read as a clean one (finding 5 of the release sweep).

It exited 0 with ``ok: true`` and ``total: 0`` when sessions.db was missing, the directory was not a git repo or no project_id
was found, so "nothing to scrub" and "could not look" were the same output. Run from a subdirectory of the project it did not
find the project at all. Two flags were also silently ignored: `--include-global` without `--scrub-secrets`, and
`--scrub-secrets` together with `--reconcile-notes`.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import types

import pytest
import yaml

from empirica.cli.command_handlers import doctor
from empirica.core import secret_scrub as ss


def _args(**kw):
    base = {"apply": False, "reconcile_notes": False, "scrub_secrets": False, "include_global": False, "output": "json"}
    base.update(kw)
    return types.SimpleNamespace(**base)


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".empirica" / "sessions").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text(yaml.safe_dump({"project_id": "pid-1"}))
    sqlite3.connect(root / ".empirica" / "sessions" / "sessions.db").close()
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *cmd], cwd=root, check=True, capture_output=True)
    (root / "f").write_text("x")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=root, check=True, capture_output=True)
    monkeypatch.setattr(ss, "scrub_qdrant", lambda *a, **k: {"points": {}, "applied": False})
    return root


def _run(capsys, **kw):
    rc = doctor.handle_doctor_command(_args(**kw))
    out = capsys.readouterr()
    return rc, (json.loads(out.out) if out.out.strip().startswith("{") else {}), out.err


def test_a_complete_clean_scan_is_ok_and_exits_zero(project, monkeypatch, capsys):
    monkeypatch.chdir(project)
    rc, report, _ = _run(capsys, scrub_secrets=True)
    assert rc == 0 and report["ok"] is True and report["total"] == 0 and report["incomplete"] == {}


def test_run_from_a_subdirectory_still_finds_the_project(project, monkeypatch, capsys):
    sub = project / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    rc, report, _ = _run(capsys, scrub_secrets=True)
    assert rc == 0 and report["project_root"] == str(project) and report["incomplete"] == {}


def test_a_missing_sessions_db_is_incomplete_not_clean(project, monkeypatch, capsys):
    (project / ".empirica" / "sessions" / "sessions.db").unlink()
    monkeypatch.chdir(project)
    rc, report, _ = _run(capsys, scrub_secrets=True)
    assert rc == 2 and report["ok"] is False
    assert "sqlite" in report["incomplete"]


def test_no_project_id_is_incomplete_not_clean(project, monkeypatch, capsys):
    (project / ".empirica" / "project.yaml").write_text(yaml.safe_dump({"name": "x"}))
    monkeypatch.chdir(project)
    rc, report, _ = _run(capsys, scrub_secrets=True)
    assert rc == 2 and "qdrant" in report["incomplete"]


def test_an_unreachable_qdrant_is_incomplete_not_clean(project, monkeypatch, capsys):
    monkeypatch.setattr(
        ss, "scrub_qdrant", lambda *a, **k: {"points": {}, "applied": False, "error": "URLError: refused"}
    )
    monkeypatch.chdir(project)
    rc, report, _ = _run(capsys, scrub_secrets=True)
    assert rc == 2 and report["ok"] is False and "qdrant" in report["incomplete"]


def test_include_global_alone_is_refused_not_ignored(project, monkeypatch, capsys):
    monkeypatch.chdir(project)
    rc, report, err = _run(capsys, include_global=True)
    assert rc == 2
    assert "include-global" in (json.dumps(report) + err)


def test_scrub_secrets_with_reconcile_notes_is_refused_not_half_run(project, monkeypatch, capsys):
    monkeypatch.chdir(project)
    rc, report, err = _run(capsys, scrub_secrets=True, reconcile_notes=True)
    assert rc == 2
    assert "one at a time" in (json.dumps(report) + err)
