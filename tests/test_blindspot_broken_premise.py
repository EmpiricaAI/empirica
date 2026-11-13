"""Broken premise: an open artifact still resting on one that has since failed (goal 9e676510, detector 1).

David 2026-11-13: unknown unknowns should emerge from the links between artifacts, not only from goal coverage. Probe that
day: 17 open artifacts in the core practice rested on a finding later retracted or superseded, and nothing surfaced them.
Some were the refutation itself linked with `evidence` where `invalidates` belongs, so `evidence` is weak and a dependent
created after its premise broke is marked as probably the refutation.
"""

from __future__ import annotations

import sqlite3

from empirica.core.blindspots.broken_premise import detect_broken_premises

T0, BROKE, LATER = 1000.0, 2000.0, 3000.0


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.executescript(
        """
        CREATE TABLE project_findings (id TEXT, finding TEXT, created_timestamp REAL, is_resolved INT,
                                       resolved_timestamp REAL, resolution_kind TEXT, superseded_by TEXT,
                                       resolution TEXT);
        CREATE TABLE project_unknowns (id TEXT, unknown TEXT, created_timestamp REAL, is_resolved INT);
        CREATE TABLE assumptions (id TEXT, assumption TEXT, created_timestamp REAL, status TEXT, resolved_timestamp REAL);
        CREATE TABLE decisions (id TEXT, choice TEXT, created_timestamp REAL, outcome TEXT);
        CREATE TABLE project_dead_ends (id TEXT, approach TEXT, created_timestamp REAL, is_invalidated INT, invalidated_at REAL);
        CREATE TABLE mistakes_made (id TEXT, mistake TEXT, created_timestamp REAL, is_invalidated INT, invalidated_at REAL);
        CREATE TABLE artifact_edges (from_id TEXT, to_id TEXT, relation TEXT, created_at REAL, metadata TEXT);
        """
    )
    return c


def _finding(c, fid, *, resolved=0, kind=None, created=T0, broke=None, superseded_by=None, resolution=None):
    c.execute(
        "INSERT INTO project_findings VALUES (?,?,?,?,?,?,?,?)",
        (fid, f"text {fid}", created, resolved, broke, kind, superseded_by, resolution),
    )


def _edge(c, a, b, rel):
    c.execute("INSERT INTO artifact_edges VALUES (?,?,?,?,?)", (a, b, rel, T0, None))


def test_an_open_finding_resting_on_a_retracted_one_is_a_candidate():
    c = _db()
    _finding(c, "p", resolved=1, kind="retracted", broke=BROKE)
    _finding(c, "d")
    _edge(c, "d", "p", "grounded_by")
    [hit] = detect_broken_premises(c)
    assert (hit["artifact_id"], hit["artifact_type"], hit["premise_id"], hit["premise_state"]) == (
        "d",
        "finding",
        "p",
        "retracted",
    )
    assert hit["strength"] == "strong" and hit["derived_from"] == ["p"]


def test_an_open_decision_on_a_superseded_finding_by_evidence_is_weak():
    c = _db()
    _finding(c, "p", resolved=1, kind="superseded", broke=BROKE)
    c.execute("INSERT INTO decisions VALUES ('d', 'choice', ?, NULL)", (T0,))
    _edge(c, "d", "p", "evidence")
    [hit] = detect_broken_premises(c)
    assert hit["artifact_type"] == "decision" and hit["strength"] == "weak" and hit["premise_state"] == "superseded"


def test_a_falsified_assumption_and_an_invalidated_dead_end_are_broken_premises():
    c = _db()
    c.execute("INSERT INTO assumptions VALUES ('a', 'x', ?, 'falsified', ?)", (T0, BROKE))
    c.execute("INSERT INTO project_dead_ends VALUES ('de', 'x', ?, 1, ?)", (T0, BROKE))
    _finding(c, "f1")
    _finding(c, "f2")
    _edge(c, "f1", "a", "grounded_by")
    _edge(c, "f2", "de", "caused_by")
    states = sorted(h["premise_state"] for h in detect_broken_premises(c))
    assert states == ["falsified", "invalidated"]


def test_a_dependent_created_after_the_break_is_probably_the_refutation():
    c = _db()
    _finding(c, "p", resolved=1, kind="retracted", broke=BROKE)
    _finding(c, "refuter", created=LATER)
    _edge(c, "refuter", "p", "evidence")
    [hit] = detect_broken_premises(c)
    assert hit["created_after_break"] is True


def test_closed_dependents_aged_premises_and_closing_relations_are_not_candidates():
    c = _db()
    _finding(c, "retracted", resolved=1, kind="retracted", broke=BROKE)
    _finding(c, "aged", resolved=1, kind="stale", broke=BROKE)
    _finding(c, "closed", resolved=1, kind="stale", broke=BROKE)
    _finding(c, "open")
    _edge(c, "closed", "retracted", "grounded_by")  # the dependent is closed itself
    _edge(c, "open", "aged", "grounded_by")  # stale is ageing, not failure
    _edge(c, "open", "retracted", "invalidates")  # this edge IS the refutation
    _edge(c, "open", "retracted", "related")  # asserts nothing
    assert detect_broken_premises(c) == []


def test_a_store_without_the_tables_gives_nothing_rather_than_raising():
    assert detect_broken_premises(sqlite3.connect(":memory:")) == []


# ── precision refinements from the first read (2026-11-13: 5 of 12 strong candidates real) ─────────────────────────────


def test_a_mistake_caused_by_a_wrong_finding_still_stands():
    """4 of the 7 misses: the mistake IS having believed the finding, so the finding's retraction confirms it."""
    c = _db()
    _finding(c, "p", resolved=1, kind="retracted", broke=BROKE)
    c.execute("INSERT INTO mistakes_made VALUES ('m', 'believed p', ?, 0, NULL)", (T0,))
    _edge(c, "m", "p", "caused_by")
    assert detect_broken_premises(c) == []


def test_the_premise_s_named_successor_is_not_resting_on_it():
    """1 miss: the finding that superseded the premise linked back to it. Named in superseded_by, or by id in the text."""
    c = _db()
    _finding(c, "p1", resolved=1, kind="superseded", broke=BROKE, superseded_by="succ1")
    _finding(c, "p2", resolved=1, kind="superseded", broke=BROKE, resolution="gardening: abcd1234 names the root cause")
    _finding(c, "succ1")
    _finding(c, "abcd1234-0000-0000-0000-000000000000")
    _edge(c, "succ1", "p1", "caused_by")
    _edge(c, "abcd1234-0000-0000-0000-000000000000", "p2", "caused_by")
    assert detect_broken_premises(c) == []


def test_a_mistake_resting_on_a_premise_by_another_relation_is_still_a_candidate():
    c = _db()
    _finding(c, "p", resolved=1, kind="retracted", broke=BROKE)
    c.execute("INSERT INTO mistakes_made VALUES ('m', 'x', ?, 0, NULL)", (T0,))
    _edge(c, "m", "p", "grounded_by")
    assert len(detect_broken_premises(c)) == 1


def test_the_scan_prints_strong_candidates_and_counts_the_weak(capsys):
    from empirica.cli.command_handlers.blindspot_commands import _print_broken_premises

    base = {
        "artifact_type": "finding",
        "relation": "grounded_by",
        "premise_type": "finding",
        "premise_state": "retracted",
        "text": "t",
        "derived_from": ["p"],
    }
    _print_broken_premises(
        [
            {**base, "artifact_id": "strong01", "premise_id": "p1", "strength": "strong", "created_after_break": False},
            {**base, "artifact_id": "weak0001", "premise_id": "p2", "strength": "weak", "created_after_break": False},
        ]
    )
    out = capsys.readouterr().out
    assert "strong01" in out and "weak0001" not in out and "1 weaker link(s) omitted" in out
