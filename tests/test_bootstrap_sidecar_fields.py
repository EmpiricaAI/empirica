"""Tests for the daemon HTTP sidecar blocks.

The HTTP /api/v1/bootstrap path attaches 4 CLI-only blocks per proposal
prop_sf63hrj7xvd3je2gcbzitwsnbi (accepted 2026-05-15 by eco-phone):

  - project (description + total_* counters + repos)
  - flow_metrics (current_flow + flow_scores[])
  - git_status (current_branch + recent_commits[])
  - reference_docs_count (integer)

All four are best-effort: present when groundable, omitted on failure.
"""

from __future__ import annotations

import sqlite3
import subprocess
import time
import uuid
from pathlib import Path


def _build_full_project(tmp_path: Path, name: str = "sidecar-proj") -> tuple[Path, str]:
    """Reuses the bootstrap aggregator fixture + adds situation/sidecar extras.

    SessionDatabase init requires the full projects schema (it creates indexes
    on `status`) so we drop+recreate `projects` with all columns before adding
    the supplementary tables. The base fixture creates a minimal `(id, name)`
    projects table which would break SessionDatabase's index creation.
    """
    from tests.test_bootstrap_aggregator import _build_test_project

    proj, pid = _build_test_project(tmp_path, name)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    conn = sqlite3.connect(str(db))
    # Replace minimal projects with the full schema SessionDatabase expects.
    conn.executescript("""
        DROP TABLE IF EXISTS projects;
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            repos TEXT,
            created_timestamp REAL NOT NULL DEFAULT 0,
            last_activity_timestamp REAL,
            status TEXT DEFAULT 'active',
            metadata TEXT,
            total_sessions INTEGER DEFAULT 0,
            total_goals INTEGER DEFAULT 0,
            total_epistemic_deltas TEXT,
            project_data TEXT NOT NULL DEFAULT '{}',
            project_type TEXT DEFAULT 'product',
            project_tags TEXT,
            parent_project_id TEXT
        );
        ALTER TABLE goals ADD COLUMN description TEXT;
        ALTER TABLE goals ADD COLUMN scope TEXT;
        CREATE TABLE IF NOT EXISTS subtasks (
            id TEXT PRIMARY KEY, goal_id TEXT NOT NULL,
            description TEXT, epistemic_importance REAL DEFAULT 0.5,
            status TEXT DEFAULT 'pending',
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY, project_id TEXT, ai_id TEXT,
            created_timestamp REAL
        );
        -- Reference docs live in epistemic_sources as source_type='pointer'
        -- (migration 046; the base fixture builds that table). This fixture
        -- used to build project_reference_docs, dropped by 047 and 072, which
        -- is why it kept passing while every real store reported 0.
    """)
    conn.execute(
        "INSERT INTO projects (id, name, description, status, project_data) VALUES (?, ?, ?, 'active', '{}')",
        (pid, name, f"Test fixture for {name}"),
    )
    conn.commit()
    conn.close()
    # Init git so get_git_status grounds.
    subprocess.run(["git", "init", "-q"], cwd=proj, check=False)
    subprocess.run(["git", "-C", str(proj), "config", "user.email", "t@t"], check=False)
    subprocess.run(["git", "-C", str(proj), "config", "user.name", "t"], check=False)
    subprocess.run(["git", "-C", str(proj), "config", "commit.gpgsign", "false"], check=False)
    (proj / "README.md").write_text("test", encoding="utf-8")
    subprocess.run(["git", "-C", str(proj), "add", "."], check=False)
    subprocess.run(["git", "-C", str(proj), "commit", "-q", "-m", "init"], check=False)
    return proj, pid


def _add_reference_doc(db_path: Path, project_id: str, path: str) -> str:
    rid = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO epistemic_sources (id, project_id, source_type, source_url, title, discovered_at) "
        "VALUES (?, ?, 'pointer', ?, ?, ?)",
        (rid, project_id, path, Path(path).name, str(time.time())),
    )
    conn.commit()
    conn.close()
    return rid


# ── project block ───────────────────────────────────────────────────────


def test_payload_includes_project_block(tmp_path):
    """project block carries name + counters + (optional) description."""
    proj, pid = _build_full_project(tmp_path)
    from empirica.core.bootstrap import build_bootstrap_payload

    payload = build_bootstrap_payload(project_path=proj, project_id=pid)
    assert "project" in payload, "project block must be on the HTTP wire"
    p = payload["project"]
    assert p["id"] == pid
    assert p["name"] == "sidecar-proj"
    # Counters present even when zero
    assert "total_sessions" in p
    assert "total_transactions" in p
    assert "total_goals" in p
    # repos defaults to empty list, not missing
    assert isinstance(p.get("repos", []), list)


# ── git_status block ────────────────────────────────────────────────────


def test_payload_includes_git_status_for_git_init_project(tmp_path):
    proj, pid = _build_full_project(tmp_path)
    from empirica.core.bootstrap import build_bootstrap_payload

    payload = build_bootstrap_payload(project_path=proj, project_id=pid)
    assert "git_status" in payload
    gs = payload["git_status"]
    assert "current_branch" in gs
    assert "recent_commits" in gs
    assert isinstance(gs["recent_commits"], list)


def test_payload_git_status_absent_for_non_git_project(tmp_path):
    """If the project root has no .git/, git_status should be absent (graceful)."""
    proj, pid = _build_full_project(tmp_path, name="no-git-proj")
    # Strip the .git/ dir that the fixture created
    git_dir = proj / ".git"
    if git_dir.exists():
        import shutil

        shutil.rmtree(git_dir)
    from empirica.core.bootstrap import build_bootstrap_payload

    payload = build_bootstrap_payload(project_path=proj, project_id=pid)
    # Either absent (preferred) or present with no branch — both acceptable
    if "git_status" in payload:
        # If present, recent_commits must be empty (no git history)
        assert (
            payload["git_status"].get("recent_commits", []) == [] or payload["git_status"].get("current_branch") is None
        )


# ── reference_docs_count ────────────────────────────────────────────────


def test_payload_reference_docs_count_zero_when_no_refs(tmp_path):
    proj, pid = _build_full_project(tmp_path)
    from empirica.core.bootstrap import build_bootstrap_payload

    payload = build_bootstrap_payload(project_path=proj, project_id=pid)
    assert payload.get("reference_docs_count") == 0


def test_payload_reference_docs_count_reflects_added_refs(tmp_path):
    proj, pid = _build_full_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    _add_reference_doc(db, pid, "docs/architecture.md")
    _add_reference_doc(db, pid, "docs/api.md")
    _add_reference_doc(db, pid, "docs/runbook.md")
    from empirica.core.bootstrap import build_bootstrap_payload

    payload = build_bootstrap_payload(project_path=proj, project_id=pid)
    assert payload.get("reference_docs_count") == 3


# ── flow_metrics block ──────────────────────────────────────────────────


def test_payload_flow_metrics_absent_when_no_assessment_data(tmp_path):
    """No postflight assessments → flow_metrics either absent or empty.
    Either is acceptable per best-effort contract."""
    proj, pid = _build_full_project(tmp_path)
    from empirica.core.bootstrap import build_bootstrap_payload

    payload = build_bootstrap_payload(project_path=proj, project_id=pid)
    # flow_metrics is attached only when current_flow is grounded
    fm = payload.get("flow_metrics")
    assert fm is None or isinstance(fm, dict)


# ── Graceful degradation ────────────────────────────────────────────────


def test_attach_sidecar_blocks_skips_when_project_id_none(tmp_path):
    """Direct call: project_id=None → all 4 blocks omitted (no DB query attempted)."""
    proj, _pid = _build_full_project(tmp_path)
    from empirica.core.bootstrap.payload import _attach_sidecar_blocks

    payload: dict = {}
    _attach_sidecar_blocks(payload, proj, None)
    assert "project" not in payload
    assert "flow_metrics" not in payload
    assert "git_status" not in payload
    assert "reference_docs_count" not in payload


def test_attach_sidecar_does_not_raise_on_missing_db(tmp_path):
    """Test the sidecar helper in isolation against a missing DB path.
    Full build_bootstrap_payload going through circles.py has its own DB
    dependency — that's a separate concern not in scope for this proposal."""
    proj = tmp_path / "no-db"
    proj.mkdir()
    from empirica.core.bootstrap.payload import _attach_sidecar_blocks

    payload: dict = {}
    _attach_sidecar_blocks(payload, proj, "nonexistent-id")
    assert "project" not in payload
    assert "flow_metrics" not in payload
    assert "git_status" not in payload
    assert "reference_docs_count" not in payload


# ── Schema-v2 contract: existing fields unchanged ──────────────────────


def test_payload_existing_schema_v2_fields_preserved(tmp_path):
    """Per acceptance criteria: no regression to existing fields."""
    proj, pid = _build_full_project(tmp_path)
    from empirica.core.bootstrap import build_bootstrap_payload

    payload = build_bootstrap_payload(project_path=proj, project_id=pid)
    for key in (
        "schema_version",
        "project_id",
        "project_path",
        "project_name",
        "session_id",
        "ai_id",
        "transaction_state",
        "active_topic",
        "active_state",
        "persistent_reference",
        "topic_relevant_backlog",
        "calibration",
        "limits",
        "situation",
    ):
        assert key in payload, f"v2 contract field missing: {key}"
    assert payload["schema_version"] == "2"
