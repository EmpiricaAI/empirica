"""The verification summary carries what its readers read, and says whether the breadcrumbs export happened.

J. _build_verification_summary returned only _internal_* keys (gaps keyed
   'praxic:know'), while POSTFLIGHT's readers index `gaps` by vector name and
   read `grounded_coverage`: they always saw {} and 0.
K. A failed .breadcrumbs.yaml write was logged at DEBUG and the export's return
   value dropped, so a persistent failure looked exactly like success.
L. The export found .breadcrumbs.yaml with `git rev-parse` from cwd, so a
   POSTFLIGHT run from another repo wrote that repo's file.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time

import pytest

from empirica.core.post_test import grounded_calibration as gc
from empirica.data.session_database import SessionDatabase


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://127.0.0.1:9")
    d = SessionDatabase(db_path=str(tmp_path / "sessions.db"))
    d.conn.execute(
        "INSERT INTO sessions (session_id, ai_id, start_time, components_loaded) VALUES ('s1', 'prac', ?, 0)",
        (time.time(),),
    )
    for v in ("know", "do"):
        d.conn.execute(
            "INSERT INTO grounded_beliefs (belief_id, session_id, ai_id, vector_name, mean, variance, evidence_count,"
            " last_updated) VALUES (?, 's1', 'prac', ?, 0.6, 0.05, 3, ?)",
            (f"b-{v}", v, time.time()),
        )
    d.conn.commit()
    yield d
    d.close()


def _phase(gaps, coverage, score):
    return {
        "verification_id": "v1",
        "evidence_count": 4,
        "sources": ["artifacts"],
        "sources_failed": [],
        "grounded_coverage": coverage,
        "calibration_score": score,
        "calibration_status": "grounded",
        "evidence_summary": {"sources": {}, "signals": []},
        "_internal_gaps": gaps,
        "_internal_updates": {},
    }


def _git_repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
    return path


def test_the_summary_carries_gaps_by_vector_name_and_coverage(db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    results = {"praxic": _phase({"know": 0.2, "do": -0.1}, 0.62, 0.8)}
    s = gc._build_verification_summary(results, "s1", db, git_root=str(tmp_path))
    assert s["gaps"] == {"know": 0.2, "do": -0.1}
    assert s["grounded_coverage"] == 0.62
    assert s["_internal_gaps"] == {"praxic:know": 0.2, "praxic:do": -0.1}  # kept
    assert s["_internal_calibration_score"] == 0.8  # kept
    # Deliberately absent: a top-level calibration_score wakes _workflow_postflight's cortex
    # cache-feedback POST (gated on calibration_score < 0.3), a new network call per POSTFLIGHT.
    assert "calibration_score" not in s


def test_with_two_phases_the_gaps_are_the_praxic_ones(db, tmp_path, monkeypatch):
    """POSTFLIGHT's readers subtract the gaps from the POSTFLIGHT (praxic) vectors."""
    monkeypatch.chdir(tmp_path)
    results = {
        "noetic": _phase({"know": 0.5}, 0.4, 0.5),
        "praxic": _phase({"know": 0.1, "do": 0.3}, 0.7, 0.9),
    }
    s = gc._build_verification_summary(results, "s1", db, git_root=str(tmp_path))
    assert s["gaps"] == {"know": 0.1, "do": 0.3}
    assert s["grounded_coverage"] == 0.7


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_a_read_only_breadcrumbs_file_reports_not_exported(db, tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    crumbs = tmp_path / ".breadcrumbs.yaml"
    crumbs.write_text("calibration:\n  x: 1\n")
    crumbs.chmod(0o444)
    try:
        with caplog.at_level(logging.WARNING, logger=gc.logger.name):
            s = gc._build_verification_summary(
                {"praxic": _phase({"know": 0.2}, 0.6, 0.8)}, "s1", db, git_root=str(tmp_path)
            )
    finally:
        crumbs.chmod(0o644)
    assert s["breadcrumbs_exported"] is False
    assert any(r.levelno == logging.WARNING and "grounded calibration" in r.getMessage() for r in caplog.records)
    # Positive control: writable -> exported
    s = gc._build_verification_summary({"praxic": _phase({"know": 0.2}, 0.6, 0.8)}, "s1", db, git_root=str(tmp_path))
    assert s["breadcrumbs_exported"] is True
    assert "grounded_calibration:" in crumbs.read_text()


def test_the_export_writes_the_given_projects_breadcrumbs_not_cwds(db, tmp_path, monkeypatch):
    other = _git_repo(tmp_path / "other")
    (other / ".breadcrumbs.yaml").write_text("calibration: {}\n")
    project = _git_repo(tmp_path / "project")
    (project / ".breadcrumbs.yaml").write_text("calibration: {}\n")
    monkeypatch.chdir(other)
    monkeypatch.setattr(gc, "_run_single_phase_verification", lambda *a, **kw: _phase({"know": 0.2}, 0.6, 0.8))
    s = gc.run_grounded_verification("s1", {"know": 0.8}, db, project_path=str(project))
    assert s is not None and s["breadcrumbs_exported"] is True
    assert "grounded_calibration:" in (project / ".breadcrumbs.yaml").read_text()
    assert "grounded_calibration:" not in (other / ".breadcrumbs.yaml").read_text()


def test_without_a_project_path_the_old_cwd_resolution_still_applies(db, tmp_path, monkeypatch):
    repo = _git_repo(tmp_path / "repo")
    (repo / ".breadcrumbs.yaml").write_text("calibration: {}\n")
    monkeypatch.chdir(repo)
    monkeypatch.setattr(gc, "_run_single_phase_verification", lambda *a, **kw: _phase({"know": 0.2}, 0.6, 0.8))
    s = gc.run_grounded_verification("s1", {"know": 0.8}, db)
    assert s is not None
    assert "grounded_calibration:" in (repo / ".breadcrumbs.yaml").read_text()
