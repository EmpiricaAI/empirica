"""Tests for the bootstrap aggregator (PROPOSAL_BOOTSTRAP_AGGREGATOR.md).

Three-circle surfacing model: active_state (recency-decayed) +
persistent_reference (no decay) + topic_relevant_backlog (similarity-pulled).

Focus areas:
- Decay: per-type half-lives produce expected weights
- Topic detection: transaction → recent-findings → none
- Circle 1: active_state queries pull in-progress goals + recent within-goal artifacts
- Circle 2: persistent_reference surfaces decisions/assumptions/sources without recency
- Circle 3: similarity-pulled backlog + Qdrant fallback
- Edge fold: depth=1 walk populates related_to[]
- Wire shape: schema_version "2", three top-level circles, surface_reason set
"""

from __future__ import annotations

import math
import sqlite3
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from empirica.core.bootstrap import build_bootstrap_payload
from empirica.core.bootstrap.decay import (
    TYPE_HALF_LIFE_HOURS,
    circle_1_weight,
    circle_2_weight,
    recency_decay,
)
from empirica.core.bootstrap.edges import attach_edges_to_payload
from empirica.core.bootstrap.render import render_v2_supplemental
from empirica.core.bootstrap.topic import detect_active_topic

# ── Decay primitives ──────────────────────────────────────────────────


def test_recency_decay_returns_1_for_infinity():
    assert recency_decay(time.time() - 999999, math.inf) == 1.0


def test_recency_decay_returns_1_for_missing_timestamp():
    assert recency_decay(None, 24) == 1.0


def test_recency_decay_halves_at_half_life():
    """Item exactly half-life-old should weight ~0.5."""
    half_life = 24
    ts = time.time() - half_life * 3600
    assert 0.49 < recency_decay(ts, half_life) < 0.51


def test_recency_decay_iso_string_timestamp():
    """ISO 8601 strings parsed correctly."""
    from datetime import datetime, timedelta, timezone

    one_day_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    weight = recency_decay(one_day_ago, 24)
    assert 0.49 < weight < 0.51


def test_circle_1_weight_combines_factors():
    """impact × type_confidence × recency."""
    fresh_ts = time.time()
    weight = circle_1_weight(0.8, "finding", fresh_ts)
    # finding type_confidence is 0.85; fresh recency = ~1.0
    expected = 0.8 * 0.85 * 1.0
    assert abs(weight - expected) < 0.01


def test_circle_2_weight_no_recency():
    """Circle 2 weight is impact × type_confidence only — no recency factor."""
    old_ts = time.time() - 365 * 24 * 3600  # 1 year old
    fresh_ts = time.time()
    # Both should produce the same circle_2 weight (recency excluded)
    old_weight = circle_2_weight(0.8, "decision")
    fresh_weight = circle_2_weight(0.8, "decision")
    assert old_weight == fresh_weight
    assert old_weight == circle_2_weight(0.8, "decision")
    assert old_ts != fresh_ts  # Sanity: timestamps differ
    assert old_weight > 0


def test_per_type_half_lives_match_spec():
    """Spec invariants: open goals/subtasks ∞, findings 30d, dead_end/mistake 14d."""
    assert math.isinf(TYPE_HALF_LIFE_HOURS["goal_open"])
    assert math.isinf(TYPE_HALF_LIFE_HOURS["subtask_open"])
    assert TYPE_HALF_LIFE_HOURS["finding"] == 30 * 24
    assert TYPE_HALF_LIFE_HOURS["dead_end"] == 14 * 24
    assert TYPE_HALF_LIFE_HOURS["mistake"] == 14 * 24


# ── Test fixture ──────────────────────────────────────────────────────


def _build_test_project(tmp_path: Path, name: str = "test-proj") -> tuple[Path, str]:
    """Create a project tree with a sqlite that has all v0.5+v0.6 tables."""
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".empirica").mkdir()
    project_uuid = str(uuid.uuid4())
    (proj / ".empirica" / "project.yaml").write_text(f"name: {name}\nproject_id: {project_uuid}\n", encoding="utf-8")
    db_dir = proj / ".empirica" / "sessions"
    db_dir.mkdir()
    db_path = db_dir / "sessions.db"

    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE project_findings (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, subtask_id TEXT, transaction_id TEXT,
            finding TEXT NOT NULL, finding_data TEXT,
            subject TEXT, impact REAL DEFAULT 0.5, epistemic_source TEXT,
            -- Mirrors project_unknowns below. This fixture gave unknowns an
            -- is_resolved column and findings none, which encoded the exact
            -- production asymmetry it was testing around: findings retrieval
            -- ignored resolution while unknowns honoured it.
            is_resolved INTEGER DEFAULT 0, resolved_timestamp REAL, superseded_by TEXT,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE project_unknowns (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, subtask_id TEXT, transaction_id TEXT,
            unknown TEXT NOT NULL, unknown_data TEXT,
            is_resolved INTEGER DEFAULT 0, resolved_by TEXT, resolved_timestamp REAL,
            impact REAL DEFAULT 0.5, epistemic_source TEXT,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE project_dead_ends (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, subtask_id TEXT, transaction_id TEXT,
            approach TEXT NOT NULL, why_failed TEXT, dead_end_data TEXT,
            impact REAL DEFAULT 0.5, epistemic_source TEXT,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE mistakes_made (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, transaction_id TEXT,
            mistake TEXT NOT NULL, why_wrong TEXT, prevention TEXT,
            mistake_data TEXT, impact REAL DEFAULT 0.5, epistemic_source TEXT,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE assumptions (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, transaction_id TEXT,
            assumption TEXT NOT NULL, confidence REAL DEFAULT 0.5,
            status TEXT DEFAULT 'unverified', resolution_finding_id TEXT,
            epistemic_source TEXT,
            created_timestamp REAL NOT NULL, resolved_timestamp REAL
        );
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, transaction_id TEXT,
            choice TEXT NOT NULL, rationale TEXT, alternatives TEXT,
            confidence_at_decision REAL, reversibility TEXT,
            outcome TEXT, regret_score REAL, epistemic_source TEXT,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE epistemic_sources (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            source_type TEXT, source_url TEXT, title TEXT,
            description TEXT, confidence REAL DEFAULT 0.5,
            epistemic_layer TEXT, discovered_by_ai TEXT,
            discovered_at TIMESTAMP NOT NULL
        );
        CREATE TABLE goals (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            transaction_id TEXT, objective TEXT NOT NULL,
            status TEXT DEFAULT 'in_progress', is_completed INTEGER DEFAULT 0,
            goal_data TEXT, created_timestamp REAL NOT NULL,
            completed_timestamp REAL
        );
        CREATE TABLE artifact_edges (
            from_id TEXT NOT NULL, to_id TEXT NOT NULL, relation TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT,
            PRIMARY KEY (from_id, to_id, relation)
        );
    """)
    conn.execute("INSERT INTO projects (id, name) VALUES (?, ?)", (project_uuid, name))
    conn.commit()
    conn.close()
    return proj, project_uuid


def _insert_finding(
    db_path: Path, project_id: str, text: str, *, goal_id: str | None = None, age_hours: float = 0, impact: float = 0.5
) -> str:
    art_id = str(uuid.uuid4())
    ts = time.time() - age_hours * 3600
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO project_findings (id, project_id, session_id, goal_id, finding, "
        "finding_data, impact, created_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (art_id, project_id, "sess-1", goal_id, text, "{}", impact, ts),
    )
    conn.commit()
    conn.close()
    return art_id


def _insert_goal(
    db_path: Path, project_id: str, objective: str, *, status: str = "in_progress", is_completed: int = 0
) -> str:
    art_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO goals (id, project_id, session_id, objective, status, is_completed, "
        "goal_data, created_timestamp, completed_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            art_id,
            project_id,
            "sess-1",
            objective,
            status,
            is_completed,
            "{}",
            time.time(),
            time.time() if is_completed else None,
        ),
    )
    conn.commit()
    conn.close()
    return art_id


def _insert_decision(db_path: Path, project_id: str, choice: str, *, outcome: str | None = None) -> str:
    art_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO decisions (id, project_id, session_id, choice, rationale, "
        "confidence_at_decision, outcome, created_timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (art_id, project_id, "sess-1", choice, "rationale", 0.7, outcome, time.time()),
    )
    conn.commit()
    conn.close()
    return art_id


def _insert_assumption(
    db_path: Path, project_id: str, assumption: str, *, status: str = "unverified", confidence: float = 0.5
) -> str:
    art_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO assumptions (id, project_id, session_id, assumption, confidence, "
        "status, created_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (art_id, project_id, "sess-1", assumption, confidence, status, time.time()),
    )
    conn.commit()
    conn.close()
    return art_id


def _insert_source(db_path: Path, project_id: str, title: str, url: str = "https://x") -> str:
    art_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO epistemic_sources (id, project_id, source_type, source_url, title, "
        "confidence, discovered_at) VALUES (?, ?, 'doc', ?, ?, ?, datetime('now'))",
        (art_id, project_id, url, title, 0.9),
    )
    conn.commit()
    conn.close()
    return art_id


def _insert_unknown(db_path: Path, project_id: str, unknown: str, *, is_resolved: int = 0, impact: float = 0.5) -> str:
    art_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO project_unknowns (id, project_id, session_id, unknown, unknown_data, "
        "is_resolved, impact, created_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (art_id, project_id, "sess-1", unknown, "{}", is_resolved, impact, time.time()),
    )
    conn.commit()
    conn.close()
    return art_id


def _insert_edge(db_path: Path, from_id: str, to_id: str, relation: str = "evidence"):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR IGNORE INTO artifact_edges (from_id, to_id, relation) VALUES (?, ?, ?)",
        (from_id, to_id, relation),
    )
    conn.commit()
    conn.close()


# ── Wire shape ────────────────────────────────────────────────────────


def test_payload_has_v2_schema(tmp_path):
    proj, _ = _build_test_project(tmp_path)
    payload = build_bootstrap_payload(proj)
    assert payload["schema_version"] == "2"


def test_payload_has_three_circle_keys(tmp_path):
    proj, _ = _build_test_project(tmp_path)
    payload = build_bootstrap_payload(proj)
    assert "active_state" in payload
    assert "persistent_reference" in payload
    assert "topic_relevant_backlog" in payload
    assert "active_topic" in payload


def test_empty_project_returns_valid_shape(tmp_path):
    proj, _ = _build_test_project(tmp_path)
    payload = build_bootstrap_payload(proj)
    # All circle sub-arrays exist and are empty
    for circle in ("active_state", "persistent_reference", "topic_relevant_backlog"):
        for sub in payload[circle].values():
            assert isinstance(sub, list)
            assert sub == []


def test_payload_includes_limits_block(tmp_path):
    proj, _ = _build_test_project(tmp_path)
    payload = build_bootstrap_payload(proj)
    limits = payload["limits"]
    assert "active_state" in limits
    assert "persistent_reference" in limits
    assert "topic_relevant_backlog" in limits


# ── Circle 1: active state ────────────────────────────────────────────


def test_circle_1_surfaces_in_progress_goals(tmp_path):
    proj, pid = _build_test_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    g1 = _insert_goal(db, pid, "Active goal A")
    g2 = _insert_goal(db, pid, "Active goal B")
    _insert_goal(db, pid, "Done goal", status="completed", is_completed=1)

    payload = build_bootstrap_payload(proj)
    goals = payload["active_state"]["in_progress_goals"]
    ids = {g["id"] for g in goals}
    assert g1 in ids
    assert g2 in ids
    # Completed goal NOT in active_state (filter applies)
    assert all(not g.get("is_completed") for g in goals)


def test_circle_1_only_surfaces_findings_within_active_goals(tmp_path):
    """Recent findings show in active_state IF they're tied to an active goal."""
    proj, pid = _build_test_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    g1 = _insert_goal(db, pid, "Active")
    _insert_finding(db, pid, "Within-goal", goal_id=g1, age_hours=2)
    _insert_finding(db, pid, "Orphan finding", goal_id=None, age_hours=2)

    payload = build_bootstrap_payload(proj)
    findings = payload["active_state"]["recent_findings"]
    contents = {f["body"] for f in findings}
    assert "Within-goal" in contents
    # Orphan (no goal_id) doesn't qualify for circle 1
    assert "Orphan finding" not in contents


def test_circle_1_surface_reason_is_active(tmp_path):
    proj, pid = _build_test_project(tmp_path)
    _insert_goal(proj / ".empirica" / "sessions" / "sessions.db", pid, "G1")
    payload = build_bootstrap_payload(proj)
    for g in payload["active_state"]["in_progress_goals"]:
        assert g["surface_reason"] == "active"


# ── Circle 2: persistent reference ────────────────────────────────────


def test_circle_2_surfaces_decisions_with_active_outcome(tmp_path):
    """Decisions where outcome IS NULL are still load-bearing."""
    proj, pid = _build_test_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    d_active = _insert_decision(db, pid, "Use SQLite", outcome=None)
    d_settled = _insert_decision(db, pid, "Old choice", outcome="went well")

    payload = build_bootstrap_payload(proj)
    items = payload["persistent_reference"]["decisions_with_active_outcome"]
    ids = {d["id"] for d in items}
    assert d_active in ids
    # Outcome-recorded decisions filtered out of circle 2 (move toward decay)
    assert d_settled not in ids


def test_circle_2_surfaces_verified_assumptions(tmp_path):
    proj, pid = _build_test_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    a_verified = _insert_assumption(db, pid, "Verified assumption", status="verified")
    a_unverified = _insert_assumption(db, pid, "Open assumption", status="unverified")

    payload = build_bootstrap_payload(proj)
    items = payload["persistent_reference"]["verified_assumptions"]
    ids = {a["id"] for a in items}
    assert a_verified in ids
    assert a_unverified not in ids  # Unverified is circle 3, not 2


def test_circle_2_no_recency_decay(tmp_path):
    """Decisions/sources surface regardless of age — no recency factor."""
    proj, pid = _build_test_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    # Decision with no outcome — fresh
    d_id = _insert_decision(db, pid, "Architectural choice")

    # Backdate it artificially to 1 year old via direct sqlite update
    one_year_ago = time.time() - 365 * 24 * 3600
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE decisions SET created_timestamp = ? WHERE id = ?", (one_year_ago, d_id))
    conn.commit()
    conn.close()

    payload = build_bootstrap_payload(proj)
    items = payload["persistent_reference"]["decisions_with_active_outcome"]
    ids = {d["id"] for d in items}
    assert d_id in ids  # Old decision still surfaces — no decay


def test_circle_2_sources_always_visible(tmp_path):
    proj, pid = _build_test_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    s = _insert_source(db, pid, "RFC 7519")

    payload = build_bootstrap_payload(proj)
    sources = payload["persistent_reference"]["sources"]
    assert any(src["id"] == s for src in sources)


# ── Topic detection ──────────────────────────────────────────────────


def test_topic_from_transaction(tmp_path):
    proj, pid = _build_test_project(tmp_path)
    transaction_state = {
        "active": True,
        "transaction_id": str(uuid.uuid4()),
        "task_context": "Implement authentication middleware",
    }
    topic = detect_active_topic(proj, pid, transaction_state=transaction_state)
    assert topic["detected"] is True
    assert topic["source"] == "transaction"
    assert "authentication" in topic["text"].lower()


def test_topic_from_recent_findings_when_no_transaction(tmp_path):
    proj, pid = _build_test_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    _insert_finding(db, pid, "JWT token validation pattern", impact=0.9)

    topic = detect_active_topic(proj, pid, transaction_state=None)
    assert topic["detected"] is True
    assert topic["source"] == "recent_findings"
    assert "jwt" in topic["text"].lower()


def test_topic_none_when_no_signal(tmp_path):
    proj, pid = _build_test_project(tmp_path)
    topic = detect_active_topic(proj, pid, transaction_state=None)
    assert topic["detected"] is False
    assert topic["source"] == "none"


# ── Circle 3: topic-relevant backlog (Qdrant fallback path) ──────────


def test_circle_3_falls_back_to_open_backlog_when_qdrant_unreachable(tmp_path):
    """Qdrant unavailable → fallback returns open unknowns by impact."""
    proj, pid = _build_test_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    _insert_finding(db, pid, "Topic seed", impact=0.9)  # Trigger topic detection
    u1 = _insert_unknown(db, pid, "Open question A", impact=0.8)
    _insert_unknown(db, pid, "Resolved question", is_resolved=1)

    # Force Qdrant unavailable: patch the import
    with patch("empirica.core.bootstrap.circles._qdrant_similarity_pull", return_value=None):
        payload = build_bootstrap_payload(proj)

    unknowns = payload["topic_relevant_backlog"]["open_unknowns"]
    ids = {u["id"] for u in unknowns}
    assert u1 in ids
    # Resolved unknowns NEVER appear in fallback (no anti-clobber w/o Qdrant)
    for u in unknowns:
        assert u.get("status") != "resolved"


def test_circle_3_skipped_when_no_topic(tmp_path):
    """No topic detected → circle 3 entirely empty even with backlog present."""
    proj, pid = _build_test_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    _insert_unknown(db, pid, "Some open question")
    # No transaction, no recent findings → no topic

    payload = build_bootstrap_payload(proj)
    backlog = payload["topic_relevant_backlog"]
    assert all(len(v) == 0 for v in backlog.values())


# ── Edge fold ─────────────────────────────────────────────────────────


def test_edge_walker_populates_related_to(tmp_path):
    proj, pid = _build_test_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    g1 = _insert_goal(db, pid, "G1")
    f1 = _insert_finding(db, pid, "Finding A", goal_id=g1, age_hours=1)
    d1 = _insert_decision(db, pid, "Decision X")
    _insert_edge(db, f1, d1, "evidence")

    payload = build_bootstrap_payload(proj)
    findings = payload["active_state"]["recent_findings"]
    f1_item = next((f for f in findings if f["id"] == f1), None)
    assert f1_item is not None
    related = f1_item["related_to"]
    assert any(e["id"] == d1 and e["relation"] == "evidence" for e in related)


def test_edge_walker_handles_missing_artifact_edges_table(tmp_path):
    """Pre-migration DB without artifact_edges table — payload still builds."""
    proj, pid = _build_test_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE artifact_edges")
    conn.commit()
    conn.close()

    _insert_goal(db, pid, "G1")
    payload = build_bootstrap_payload(proj)
    # Just shouldn't throw — items just won't have related_to populated
    assert payload["schema_version"] == "2"


# ── render_v2_supplemental ────────────────────────────────────────────


def test_render_returns_empty_for_empty_payload():
    payload = {
        "persistent_reference": {"decisions_with_active_outcome": [], "verified_assumptions": [], "sources": []},
        "topic_relevant_backlog": {
            "open_unknowns": [],
            "open_assumptions": [],
            "planned_goals": [],
            "completed_goals_relevant": [],
            "resolved_unknowns_relevant": [],
            "dead_ends_relevant": [],
        },
        "active_topic": {"detected": False},
    }
    assert render_v2_supplemental(payload) == ""


def test_render_includes_persistent_reference_when_populated():
    payload = {
        "persistent_reference": {
            "decisions_with_active_outcome": [{"id": "d1", "choice": "Use SQLite", "rationale": "single user"}],
            "verified_assumptions": [],
            "sources": [],
        },
        "topic_relevant_backlog": {
            "open_unknowns": [],
            "open_assumptions": [],
            "planned_goals": [],
            "completed_goals_relevant": [],
            "resolved_unknowns_relevant": [],
            "dead_ends_relevant": [],
        },
        "active_topic": {"detected": False},
    }
    md = render_v2_supplemental(payload)
    assert "PERSISTENT REFERENCE" in md
    assert "Use SQLite" in md


def test_render_marks_completed_goals_with_anti_clobber_warning():
    payload = {
        "persistent_reference": {"decisions_with_active_outcome": [], "verified_assumptions": [], "sources": []},
        "topic_relevant_backlog": {
            "open_unknowns": [],
            "open_assumptions": [],
            "planned_goals": [],
            "completed_goals_relevant": [{"id": "g1", "objective": "Implement auth", "completed_at": "2026-04-15"}],
            "resolved_unknowns_relevant": [],
            "dead_ends_relevant": [],
        },
        "active_topic": {"detected": True, "source": "transaction", "similarity_threshold": 0.65},
    }
    md = render_v2_supplemental(payload)
    assert "anti-clobber" in md.lower() or "completed" in md.lower()
    assert "Implement auth" in md


# ── attach_edges_to_payload smoke ─────────────────────────────────────


def test_attach_edges_idempotent(tmp_path):
    """Calling attach_edges twice doesn't duplicate or corrupt."""
    proj, pid = _build_test_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    g1 = _insert_goal(db, pid, "G1")
    f1 = _insert_finding(db, pid, "F1", goal_id=g1, age_hours=1)
    d1 = _insert_decision(db, pid, "D1")
    _insert_edge(db, f1, d1, "evidence")

    payload = build_bootstrap_payload(proj)
    first_edges = sum(
        len(item.get("related_to", []))
        for c in ("active_state", "persistent_reference", "topic_relevant_backlog")
        for items in payload[c].values()
        for item in items
    )

    attach_edges_to_payload(proj, payload)
    second_edges = sum(
        len(item.get("related_to", []))
        for c in ("active_state", "persistent_reference", "topic_relevant_backlog")
        for items in payload[c].values()
        for item in items
    )

    assert first_edges == second_edges
