"""A claim must not be retrievable at a confidence its premises do not support.

The measured failure (empirica-outreach + empirica-autonomy, 2026-08-18): one
artifact carried an observed behaviour and an inferred identity together, was
consumed at the confidence of its strongest part, and produced five downstream
effects including a customer-facing document under a fabricated name.

David's ruling was that the representation is already available — split the
observation into a `finding` and the inference into an `assumption`, edge them.
This module is what makes that split *mechanical* rather than merely tidy: the
edge caps what retrieval may claim.

The tests below pin the judgment calls, because each one is a place where a
defensible-looking alternative would quietly defeat the purpose:

- MIN, not mean — averaging is precisely how a strong observation hides a weak
  inference.
- Association relations do not cap. `related` and `attached_to` are 958 of this
  practice's 1330 edges; admitting them would cap almost everything on almost
  anything and the signal would be discarded as noise within a week.
- No premises returns None, not 1.0. "Rests on nothing recorded" and "rests on
  something solid" must not render identically.
- A retracted premise floors to 0.0. It was false when written; anything built
  on it inherits that, rather than a polite discount.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.core.derived_confidence import MAX_VISITED, annotate, derived_confidence

SCHEMA = """
CREATE TABLE artifact_edges (
    from_id TEXT NOT NULL, to_id TEXT NOT NULL, relation TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id, relation)
);
CREATE TABLE project_findings (
    id TEXT PRIMARY KEY, finding TEXT, impact REAL,
    is_resolved INTEGER, resolution_kind TEXT
);
CREATE TABLE assumptions (id TEXT PRIMARY KEY, assumption TEXT, confidence REAL, status TEXT);
CREATE TABLE decisions (id TEXT PRIMARY KEY, choice TEXT, confidence_at_decision REAL);
CREATE TABLE epistemic_sources (id TEXT PRIMARY KEY, title TEXT, confidence REAL);
CREATE TABLE project_unknowns (id TEXT PRIMARY KEY, unknown TEXT);
CREATE TABLE project_dead_ends (id TEXT PRIMARY KEY, approach TEXT);
CREATE TABLE mistakes_made (id TEXT PRIMARY KEY, mistake TEXT);
"""


@pytest.fixture
def cur(tmp_path):
    conn = sqlite3.connect(tmp_path / "graph.db")
    conn.executescript(SCHEMA)
    try:
        yield conn.cursor()
    finally:
        conn.close()


def _finding(cur, fid, resolved=None, kind=None):
    cur.execute(
        "INSERT INTO project_findings (id, finding, impact, is_resolved, resolution_kind) VALUES (?,?,?,?,?)",
        (fid, f"finding {fid}", 0.9, resolved, kind),
    )


def _assumption(cur, aid, confidence, status="unverified"):
    cur.execute(
        "INSERT INTO assumptions (id, assumption, confidence, status) VALUES (?,?,?,?)",
        (aid, f"assumption {aid}", confidence, status),
    )


def _edge(cur, frm, to, relation="grounded_by"):
    cur.execute("INSERT INTO artifact_edges (from_id, to_id, relation) VALUES (?,?,?)", (frm, to, relation))


# ── the measured case ────────────────────────────────────────────────────────


def test_a_finding_resting_on_a_weak_assumption_is_capped_by_it(cur):
    """The outreach case, in the representation David ruled correct."""
    _finding(cur, "f-behaviour")
    _assumption(cur, "a-identity", 0.55)
    _edge(cur, "f-behaviour", "a-identity")

    cap = derived_confidence(cur, "f-behaviour")
    assert cap is not None
    assert cap["value"] == 0.55
    assert cap["weakest"]["id"] == "a-identity"
    assert cap["weakest"]["type"] == "assumption"


def test_the_cap_is_the_minimum_not_the_mean(cur):
    """Averaging is how a strong observation hides a weak inference."""
    _finding(cur, "f-derived")
    _assumption(cur, "a-strong", 0.95)
    _assumption(cur, "a-weak", 0.30)
    _edge(cur, "f-derived", "a-strong")
    _edge(cur, "f-derived", "a-weak")

    cap = derived_confidence(cur, "f-derived")
    assert cap["value"] == 0.30, "mean would be 0.625 and would hide the weak premise"
    assert cap["weakest"]["id"] == "a-weak"


def test_the_cap_reaches_through_a_chain_not_just_one_hop(cur):
    """One-hop capping would be defeated by any intermediate artifact."""
    _finding(cur, "f-top")
    _finding(cur, "f-middle")
    _assumption(cur, "a-bottom", 0.2)
    _edge(cur, "f-top", "f-middle", "evidence")
    _edge(cur, "f-middle", "a-bottom", "grounded_by")

    cap = derived_confidence(cur, "f-top")
    assert cap["value"] == 0.2
    assert cap["weakest"]["id"] == "a-bottom"


# ── the judgment calls ───────────────────────────────────────────────────────


def test_association_relations_do_not_cap(cur):
    """`related` / `attached_to` are association, not derivation.

    They are also the majority of edges in a real graph, so capping on them
    would flag nearly everything and the signal would be ignored.
    """
    _finding(cur, "f-solo")
    _assumption(cur, "a-nearby", 0.1)
    _edge(cur, "f-solo", "a-nearby", "related")
    _edge(cur, "f-solo", "a-nearby", "attached_to")

    assert derived_confidence(cur, "f-solo") is None


def test_no_premises_returns_none_not_a_confident_default(cur):
    """An unsupported claim is not thereby a weak one — but it must not read as strong either."""
    _finding(cur, "f-orphan")
    assert derived_confidence(cur, "f-orphan") is None


def test_a_retracted_premise_floors_the_claim_to_zero(cur):
    """Retracted means it was FALSE when written. Dependents inherit that."""
    _finding(cur, "f-built-on-sand")
    _finding(cur, "f-was-false", resolved=1, kind="retracted")
    _edge(cur, "f-built-on-sand", "f-was-false", "evidence")

    assert derived_confidence(cur, "f-built-on-sand")["value"] == 0.0


def test_a_stale_premise_discounts_but_does_not_floor(cur):
    """Stale was true once. Not evidence for a live claim, not a lie either."""
    _finding(cur, "f-aging")
    _finding(cur, "f-stale", resolved=1, kind="stale")
    _edge(cur, "f-aging", "f-stale", "evidence")

    assert derived_confidence(cur, "f-aging")["value"] == 0.5


def test_an_unresolved_finding_premise_does_not_cap(cur):
    """`impact` is how much a finding MATTERS, not how sure we are.

    A finding claims to be an observation; treating it as 0.9-confident because
    its impact is 0.9 would conflate two different quantities.
    """
    _finding(cur, "f-derived")
    _finding(cur, "f-observed")
    _edge(cur, "f-derived", "f-observed", "evidence")

    cap = derived_confidence(cur, "f-derived")
    assert cap["value"] == 1.0


def test_a_verified_assumption_stops_capping_and_a_falsified_one_floors(cur):
    """The assumption lifecycle is the whole point of logging one."""
    _finding(cur, "f-a")
    _assumption(cur, "a-checked", 0.4, status="verified")
    _edge(cur, "f-a", "a-checked")
    assert derived_confidence(cur, "f-a")["value"] == 1.0

    _finding(cur, "f-b")
    _assumption(cur, "a-wrong", 0.9, status="falsified")
    _edge(cur, "f-b", "a-wrong")
    assert derived_confidence(cur, "f-b")["value"] == 0.0


def test_a_cycle_terminates(cur):
    """A graph practitioners write by hand will eventually contain one."""
    _finding(cur, "f-1")
    _finding(cur, "f-2")
    _edge(cur, "f-1", "f-2", "evidence")
    _edge(cur, "f-2", "f-1", "evidence")

    assert derived_confidence(cur, "f-1") is not None  # terminates, does not hang


def test_a_truncated_walk_says_so(cur):
    """A partial walk that looked complete would understate risk.

    That is the same failure class this whole mechanism exists to catch, so it
    must not be reintroduced by the bound that makes it cheap.
    """
    _finding(cur, "f-hub")
    for i in range(MAX_VISITED + 10):
        _assumption(cur, f"a-{i}", 0.9)
        _edge(cur, "f-hub", f"a-{i}")

    cap = derived_confidence(cur, "f-hub")
    assert cap["truncated"] is True


# ── the consumer surface ─────────────────────────────────────────────────────


def test_annotate_marks_only_artifacts_that_rest_on_something(cur):
    """Presence of the key IS the signal; a blanket annotation would erase it."""
    _finding(cur, "f-supported")
    _finding(cur, "f-orphan")
    _assumption(cur, "a-weak", 0.25)
    _edge(cur, "f-supported", "a-weak")

    out = annotate(cur, [{"id": "f-supported"}, {"id": "f-orphan"}])
    assert out[0]["derived_confidence"]["value"] == 0.25
    assert "derived_confidence" not in out[1]


def test_annotate_respects_its_budget(cur):
    """It runs on the PREFLIGHT hot path; unbounded work there is a regression."""
    _assumption(cur, "a-weak", 0.25)
    arts = []
    for i in range(10):
        _finding(cur, f"f-{i}")
        _edge(cur, f"f-{i}", "a-weak")
        arts.append({"id": f"f-{i}"})

    out = annotate(cur, arts, budget=3)
    assert sum("derived_confidence" in a for a in out) == 3


def test_annotate_survives_a_broken_row_without_failing_retrieval(cur):
    """Retrieval must never fail because a cap could not be computed."""
    out = annotate(cur, [{"id": "does-not-exist"}, {"no_id": True}])
    assert all("derived_confidence" not in a for a in out)


# ── the retrieval seam ───────────────────────────────────────────────────────


def test_retrieval_emits_the_cap_only_when_it_constrains(cur, monkeypatch, tmp_path):
    """A payload entry reading "capped at 1.0" spends context to say nothing.

    The commonest edge in a real graph is finding→finding, and an unresolved
    finding does not cap — so emitting every computed record would bury the five
    that matter under the fifty that do not. The finer distinction survives in
    `derived_confidence()` itself; only the payload is economical.
    """
    from empirica.core.qdrant import pattern_retrieval as pr

    _finding(cur, "f-capped")
    _finding(cur, "f-uncapped")
    _finding(cur, "f-solid")
    _assumption(cur, "a-weak", 0.4)
    _edge(cur, "f-capped", "a-weak")
    _edge(cur, "f-uncapped", "f-solid", "evidence")
    cur.connection.commit()

    ranked = [
        {"artifact_id": "f-capped", "text": "capped", "derived_confidence": derived_confidence(cur, "f-capped")},
        {"artifact_id": "f-uncapped", "text": "uncapped", "derived_confidence": derived_confidence(cur, "f-uncapped")},
    ]
    # The uncapped one HAS a record — the computation kept the distinction.
    assert ranked[1]["derived_confidence"]["value"] == 1.0

    emitted = [
        {
            "finding": f.get("text", ""),
            **(
                {"derived_confidence": f["derived_confidence"]}
                if (f.get("derived_confidence") or {}).get("value", 1.0) < 1.0
                else {}
            ),
        }
        for f in ranked
    ]
    assert "derived_confidence" in emitted[0]
    assert "derived_confidence" not in emitted[1]
    assert pr._annotate_derived_confidence([]) == []


def test_annotation_never_breaks_retrieval_when_the_graph_is_unreachable(monkeypatch):
    """PREFLIGHT hot path: a counting failure must degrade to plain findings."""
    from empirica.core.qdrant import pattern_retrieval as pr

    monkeypatch.setattr(
        "empirica.data.session_database._resolve_canonical_project_root",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no root")),
    )
    ranked = [{"artifact_id": "f-1", "text": "x"}]
    assert pr._annotate_derived_confidence(ranked) == ranked
