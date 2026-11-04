"""project-bootstrap says what it left out (goal 556bc6c0, empirica-extension's code_change_request, slice 1).

Measured at HEAD on a practice with 94 unverified assumptions: `breadcrumbs` had no assumptions at all; findings (10), dead ends (5), mistakes (5)
and decisions (10) were capped with no total and no warning (only unknowns warned, in prose); decisions came from an unordered Qdrant scroll whose
failure was swallowed, so "none" and "unavailable" read the same; and the depth filter, which runs last, cuts the lists again. These tests build a real
SessionDatabase and pin the new contract:

    breadcrumbs["assumptions"]  open (unverified) assumptions, newest first, capped
    breadcrumbs["coverage"]     {type: {"shown": int, "total": int | None, "truncated": bool, "source": "sqlite" | "qdrant" | "unavailable"}}

`total` is None when the cap hid an unknown number (a getter that applies its limit after filtering cannot be counted without a second read).
"""

from __future__ import annotations

import types

import pytest

from empirica.data.session_database import SessionDatabase

TYPES = ("findings", "unknowns", "dead_ends", "mistakes_to_avoid", "decisions", "assumptions")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "s.db"))
    db = SessionDatabase(db_path=str(tmp_path / "s.db"))
    pid = db.create_project(name="cov")
    sid = db.create_session(ai_id="t", project_id=pid)

    w = types.SimpleNamespace()
    w.db, w.pid, w.sid = db, pid, sid

    def fill(findings=0, unknowns=0, dead_ends=0, mistakes=0, assumptions=0):
        for i in range(findings):
            db.log_finding(pid, sid, f"finding {i}", impact=0.5)
        for i in range(unknowns):
            db.log_unknown(pid, sid, f"unknown {i}")
        for i in range(dead_ends):
            db.log_dead_end(pid, sid, f"approach {i}", "why")
        for i in range(mistakes):
            db.log_mistake(sid, f"mistake {i}", "why", project_id=pid)
        for i in range(assumptions):
            db.log_assumption(pid, sid, f"assumption {i}", confidence=0.5)

    w.fill = fill
    w.load = lambda mode="session_start": db._load_breadcrumbs_for_mode(pid, mode)
    yield w
    db.close()


def test_open_assumptions_are_listed_capped_with_their_total(world):
    world.fill(assumptions=14)
    out = world.load()
    assert len(out["assumptions"]) == 10
    assert out["coverage"]["assumptions"] == {"shown": 10, "total": 14, "truncated": True, "source": "sqlite"}
    assert {"id", "assumption", "confidence", "status"} <= set(out["assumptions"][0])


def test_a_verified_assumption_is_not_open(world):
    world.fill(assumptions=3)
    first = world.db.conn.execute("SELECT id FROM assumptions LIMIT 1").fetchone()[0]
    world.db.conn.execute("UPDATE assumptions SET status = 'verified' WHERE id = ?", (first,))
    world.db.conn.commit()
    out = world.load()
    assert len(out["assumptions"]) == 2 and out["coverage"]["assumptions"]["total"] == 2
    assert all(a["status"] == "unverified" for a in out["assumptions"])


def test_the_newest_assumptions_come_first(world):
    world.fill(assumptions=12)
    texts = [a["assumption"] for a in world.load()["assumptions"]]
    assert texts[0] == "assumption 11" and "assumption 0" not in texts


def test_a_capped_type_says_so_and_a_complete_one_does_not(world):
    world.fill(findings=13, unknowns=4, dead_ends=2, mistakes=6)
    cov = world.load()["coverage"]
    assert cov["findings"]["truncated"] is True and cov["findings"]["shown"] == 10
    assert cov["findings"]["total"] is None, "the getter cuts after filtering: the cap hid an unknown number"
    assert cov["mistakes_to_avoid"]["truncated"] is True and cov["mistakes_to_avoid"]["shown"] == 5
    assert cov["unknowns"] == {"shown": 4, "total": 4, "truncated": False, "source": "sqlite"}
    assert cov["dead_ends"] == {"shown": 2, "total": 2, "truncated": False, "source": "sqlite"}


def test_unknowns_keep_their_exact_total_when_capped(world):
    world.fill(unknowns=13)
    cov = world.load()["coverage"]["unknowns"]
    assert cov == {"shown": 10, "total": 13, "truncated": True, "source": "sqlite"}


def test_exactly_at_the_cap_is_not_truncated(world):
    """Control for the limit+1 read: ten findings with a cap of ten hid nothing."""
    world.fill(findings=10)
    cov = world.load()["coverage"]["findings"]
    assert cov["shown"] == 10 and cov["truncated"] is False and cov["total"] == 10


def test_live_mode_loads_everything_and_says_complete(world):
    world.fill(findings=13, unknowns=13, assumptions=14)
    out = world.load("live")
    assert len(out["findings"]) == 13 and len(out["assumptions"]) == 14
    for kind in ("findings", "unknowns", "assumptions"):
        assert (
            out["coverage"][kind]["truncated"] is False
            and out["coverage"][kind]["total"] == out["coverage"][kind]["shown"]
        )


def test_every_type_has_a_coverage_entry_even_when_empty(world):
    cov = world.load()["coverage"]
    assert set(cov) == set(TYPES) | {"artifact_edges"}
    assert cov["findings"] == {"shown": 0, "total": 0, "truncated": False, "source": "sqlite"}


# Decisions are read from SQLite, newest first, since slice 2: see tests/test_bootstrap_slices.py.


# ── the depth filter runs last and cuts again ──────────────────────────────────────────────────────────────────


def test_a_depth_filter_that_cuts_further_is_reflected_in_coverage(world):
    world.fill(findings=9, unknowns=9)
    bc = world.load()
    assert bc["coverage"]["findings"]["truncated"] is False
    cut = world.db._apply_depth_filter({**bc}, "minimal", None)
    world.db._reconcile_coverage(cut)
    assert len(cut["findings"]) == 5
    assert cut["coverage"]["findings"] == {"shown": 5, "total": 9, "truncated": True, "source": "sqlite"}


def test_a_list_the_depth_filter_removed_is_reported_as_cut_not_as_empty(world):
    world.fill(assumptions=3)
    bc = world.load()
    bc.pop("assumptions")
    world.db._reconcile_coverage(bc)
    assert bc["coverage"]["assumptions"]["shown"] == 0 and bc["coverage"]["assumptions"]["truncated"] is True
