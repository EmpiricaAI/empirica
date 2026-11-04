"""project-bootstrap slices 2 to 5 (goal 556bc6c0, empirica-extension's request): decisions, relationships, blindspot status, full depth.

Slice 2. Decisions are canonical in SQLite (a `decisions` table with created_timestamp), but the bootstrap read them from an unordered Qdrant scroll:
not the newest ten, and a Qdrant outage read as "no decisions". They now come from SQLite, newest first, with an exact total, and `source` says `sqlite`.
Slice 3. `artifact_edges` among the artifacts actually shown: semantic relations only (not `attached_to` or `related`), both ends shown, capped and counted.
Slice 4. `blindspot_status`: the CHECK-time scan telemetry for this practice, with when the last event was. It is not a fresh scan.
Slice 5. `depth="full"` lifts the caps, because a "full" that is not full was the defect.
"""

from __future__ import annotations

import types

import pytest

from empirica.data.session_database import SessionDatabase

SEMANTIC = ("evidence", "grounded_by", "caused_by", "raised_by", "invalidates", "resolves", "sourced_from")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "s.db"))
    db = SessionDatabase(db_path=str(tmp_path / "s.db"))
    pid = db.create_project(name="slices")
    sid = db.create_session(ai_id="t", project_id=pid)
    w = types.SimpleNamespace(db=db, pid=pid, sid=sid)
    w.load = lambda mode="session_start": db._load_breadcrumbs_for_mode(pid, mode)
    w.edge = lambda a, b, rel: (
        db.conn.execute("INSERT OR IGNORE INTO artifact_edges (from_id, to_id, relation) VALUES (?,?,?)", (a, b, rel)),
        db.conn.commit(),
    )
    yield w
    db.close()


# ── slice 2: decisions ─────────────────────────────────────────────────────────────────────────────────────────


def _decide(w, n, **kw):
    return [w.db.log_decision(w.pid, w.sid, f"choice {i}", "because", **kw) for i in range(n)]


def test_decisions_come_from_sqlite_newest_first_with_an_exact_total(world, monkeypatch):
    from empirica.core.qdrant import vector_store as vs

    def boom(*_a, **_k):
        raise AssertionError("the bootstrap must not read decisions from qdrant")

    monkeypatch.setattr(vs, "_get_qdrant_client", boom)
    _decide(world, 13)
    out = world.load()
    assert [d["choice"] for d in out["decisions"]][:2] == ["choice 12", "choice 11"]
    assert len(out["decisions"]) == 10
    assert out["coverage"]["decisions"] == {"shown": 10, "total": 13, "truncated": True, "source": "sqlite"}


def test_decisions_carry_their_id_and_time_and_the_old_fields(world):
    _decide(world, 1, reversibility="committal", confidence=0.9)
    d = world.load()["decisions"][0]
    assert {"id", "choice", "rationale", "reversibility", "confidence", "created_timestamp"} <= set(d)
    assert d["reversibility"] == "committal" and d["confidence"] == 0.9


def test_fewer_decisions_than_the_cap_is_complete(world):
    _decide(world, 3)
    assert world.load()["coverage"]["decisions"] == {"shown": 3, "total": 3, "truncated": False, "source": "sqlite"}


def test_no_decisions_is_zero_of_zero_not_unavailable(world):
    assert world.load()["coverage"]["decisions"] == {"shown": 0, "total": 0, "truncated": False, "source": "sqlite"}


def test_live_mode_returns_every_decision(world):
    _decide(world, 13)
    out = world.load("live")
    assert len(out["decisions"]) == 13 and out["coverage"]["decisions"]["truncated"] is False


def test_a_failed_read_is_unavailable_not_none(world, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(world.db.breadcrumbs, "get_project_decisions", boom)
    out = world.load()
    assert out["decisions"] == []
    assert out["coverage"]["decisions"] == {"shown": 0, "total": None, "truncated": False, "source": "unavailable"}


def test_another_projects_decisions_are_not_shown(world):
    other = world.db.create_project(name="other")
    osid = world.db.create_session(ai_id="t", project_id=other)
    world.db.log_decision(other, osid, "foreign", "x")
    _decide(world, 1)
    assert [d["choice"] for d in world.load()["decisions"]] == ["choice 0"]


# ── slice 3: artifact_edges ────────────────────────────────────────────────────────────────────────────────────


def test_a_semantic_edge_between_two_shown_artifacts_is_listed_with_types_and_full_ids(world):
    f = world.db.log_finding(world.pid, world.sid, "a finding", impact=0.5)
    u = world.db.log_unknown(world.pid, world.sid, "an unknown")
    world.edge(u, f, "raised_by")
    out = world.load()
    assert out["artifact_edges"] == [
        {"from": u, "to": f, "from_type": "unknown", "to_type": "finding", "relation": "raised_by"}
    ]
    assert out["coverage"]["artifact_edges"] == {"shown": 1, "total": 1, "truncated": False, "source": "sqlite"}


@pytest.mark.parametrize("relation", ["attached_to", "related"])
def test_structural_and_weak_relations_are_not_listed(world, relation):
    f = world.db.log_finding(world.pid, world.sid, "a finding", impact=0.5)
    u = world.db.log_unknown(world.pid, world.sid, "an unknown")
    world.edge(u, f, relation)
    out = world.load()
    assert out["artifact_edges"] == []
    assert out["coverage"]["artifact_edges"]["total"] == 0


def test_an_edge_to_an_artifact_that_is_not_shown_is_left_out(world):
    ids = [world.db.log_finding(world.pid, world.sid, f"finding {i}", impact=0.5) for i in range(12)]
    kept = ids[-1]  # newest, shown
    hidden = ids[0]  # oldest, past the cap of 10
    world.edge(kept, hidden, "evidence")
    assert world.load()["artifact_edges"] == []


def test_edges_are_capped_and_counted(world):
    f = world.db.log_finding(world.pid, world.sid, "a finding", impact=0.5)
    # 25 shown decisions are not possible at the session_start cap, so use live mode, whose edge cap is off, then the capped load
    us = [world.db.log_unknown(world.pid, world.sid, f"u{i}") for i in range(10)]
    ds = _decide(world, 10)
    for x in us + ds:
        world.edge(x, f, "evidence")
    for x in ds:
        world.edge(x, us[0], "grounded_by")
    out = world.load()
    assert len(out["artifact_edges"]) == 20
    assert out["coverage"]["artifact_edges"] == {"shown": 20, "total": 30, "truncated": True, "source": "sqlite"}
    assert len(world.load("live")["artifact_edges"]) == 30


def test_every_session_start_load_has_an_edges_entry_even_when_empty(world):
    assert world.load()["coverage"]["artifact_edges"] == {
        "shown": 0,
        "total": 0,
        "truncated": False,
        "source": "sqlite",
    }


def test_minimal_depth_drops_edges_and_says_it_cut_them(world):
    f = world.db.log_finding(world.pid, world.sid, "a finding", impact=0.5)
    u = world.db.log_unknown(world.pid, world.sid, "an unknown")
    world.edge(u, f, "raised_by")
    bc = world.load()
    cut = world.db._apply_depth_filter({**bc}, "minimal", None)
    world.db._reconcile_coverage(cut)
    assert "artifact_edges" not in cut
    assert cut["coverage"]["artifact_edges"]["truncated"] is True and cut["coverage"]["artifact_edges"]["shown"] == 0


# ── slice 4: blindspot status ──────────────────────────────────────────────────────────────────────────────────


def _blindspot(w, outcome, session=None, ts=1_000.0):
    w.db.conn.execute(
        "INSERT INTO blindspot_events (session_id, created_timestamp, kind, outcome) VALUES (?,?,?,?)",
        (session or w.sid, ts, "intent_gap", outcome),
    )
    w.db.conn.commit()


def test_blindspot_status_counts_outcomes_and_dates_the_last_event(world):
    _blindspot(world, "surfaced", ts=1_000.0)
    _blindspot(world, "acknowledged", ts=3_000.0)
    _blindspot(world, "acknowledged", ts=2_000.0)
    _blindspot(world, "dismissed", ts=500.0)
    st = world.load()["blindspot_status"]
    assert st["total"] == 4 and st["surfaced"] == 1 and st["acknowledged"] == 2 and st["dismissed"] == 1
    assert st["last_event_at"] == 3_000.0 and st["source"] == "sqlite"
    assert "not a fresh scan" in st["note"]


def test_blindspot_status_with_no_events_says_so(world):
    st = world.load()["blindspot_status"]
    assert st["total"] == 0 and st["last_event_at"] is None and st["source"] == "sqlite"


def test_another_projects_blindspot_events_are_not_counted(world):
    other = world.db.create_project(name="other")
    osid = world.db.create_session(ai_id="t", project_id=other)
    _blindspot(world, "surfaced", session=osid)
    assert world.load()["blindspot_status"]["total"] == 0


def test_a_missing_table_is_unavailable_not_zero(world):
    world.db.conn.execute("DROP TABLE blindspot_events")
    world.db.conn.commit()
    st = world.load()["blindspot_status"]
    assert st["source"] == "unavailable" and st["total"] is None


# ── slice 5: --depth full lifts the caps ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def no_project_root(monkeypatch):
    """Force the unresolved-root case. Without this the tests below passed or failed with the directory they ran in: a checkout with a
    resolvable project root hid a crash that CI, which has none, hit."""
    import empirica.data.session_database as sdb

    monkeypatch.setattr(sdb, "_resolve_canonical_project_root", lambda: None)


def test_a_bootstrap_with_no_resolvable_project_root_still_ships_the_core_breadcrumbs(world, no_project_root):
    """The code says so ("core breadcrumbs still ship") but build_situation(None) raised TypeError, so the whole bootstrap failed."""
    _decide(world, 2)
    out = world.db.bootstrap_project_breadcrumbs(world.pid, project_root=None)
    assert "error" not in out and len(out["decisions"]) == 2
    assert isinstance(out["situation"], dict)


def test_full_depth_lifts_the_caps_and_the_default_does_not(world, no_project_root):
    for i in range(13):
        world.db.log_finding(world.pid, world.sid, f"finding {i}", impact=0.5)
    _decide(world, 13)
    default = world.db.bootstrap_project_breadcrumbs(world.pid, project_root=None)
    assert len(default["findings"]) == 10 and default["coverage"]["findings"]["truncated"] is True
    full = world.db.bootstrap_project_breadcrumbs(world.pid, project_root=None, depth="full")
    assert len(full["findings"]) == 13 and len(full["decisions"]) == 13
    assert full["coverage"]["findings"]["truncated"] is False and full["coverage"]["decisions"]["truncated"] is False
