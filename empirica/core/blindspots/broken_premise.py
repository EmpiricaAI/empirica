"""Broken premise: an open artifact still resting on one that has since failed (goal 9e676510, detector 1).

The intent gap reads goal coverage. This reads the links between artifacts, which is where David (2026-11-13) said unknown
unknowns should come from: a finding, decision, assumption, unknown, dead end or mistake that is still open while something it
rests on was retracted, superseded, falsified or invalidated. Nobody re-read it after its ground moved, so it is not known to be
wrong and not known to be right.

Pure over a sqlite connection, practice-wide (premises outlive sessions), and fail-open: a store without the tables gives no
candidates. Report-only until its precision is read: ``created_after_break`` marks a dependent made after its premise broke,
which is usually the refutation itself linked with ``evidence`` where ``invalidates`` belongs.
"""

from __future__ import annotations

import sqlite3

#: Relations that say "this rests on that". ``evidence`` is weak: practitioners also used it for "this refutes that".
STRONG = ("grounded_by", "caused_by", "raised_by")
WEAK = ("evidence",)

#: (type, table, text column, open condition)
_DEPENDENTS = (
    ("finding", "project_findings", "finding", "COALESCE(is_resolved, 0) = 0"),
    ("unknown", "project_unknowns", "unknown", "COALESCE(is_resolved, 0) = 0"),
    ("assumption", "assumptions", "assumption", "status = 'unverified'"),
    ("decision", "decisions", "choice", "outcome IS NULL"),
    ("dead_end", "project_dead_ends", "approach", "COALESCE(is_invalidated, 0) = 0"),
    ("mistake", "mistakes_made", "mistake", "COALESCE(is_invalidated, 0) = 0"),
)

#: (type, queries returning id, state, when it broke, named successor, resolution text). The first query a store can answer
#: is used: older stores lack superseded_by and resolution, and must still give their retracted findings.
_PREMISES = (
    (
        "finding",
        (
            "SELECT id, resolution_kind, resolved_timestamp, superseded_by, resolution FROM project_findings"
            " WHERE resolution_kind IN ('retracted', 'superseded')",
            "SELECT id, resolution_kind, resolved_timestamp, NULL, NULL FROM project_findings"
            " WHERE resolution_kind IN ('retracted', 'superseded')",
        ),
    ),
    (
        "assumption",
        ("SELECT id, 'falsified', resolved_timestamp, NULL, NULL FROM assumptions WHERE status = 'falsified'",),
    ),
    (
        "dead_end",
        (
            "SELECT id, 'invalidated', invalidated_at, NULL, NULL FROM project_dead_ends"
            " WHERE COALESCE(is_invalidated, 0) = 1",
        ),
    ),
    (
        "mistake",
        (
            "SELECT id, 'invalidated', invalidated_at, NULL, NULL FROM mistakes_made"
            " WHERE COALESCE(is_invalidated, 0) = 1",
        ),
    ),
)


def _rows(conn: sqlite3.Connection, query: str) -> list[tuple]:
    try:
        return conn.execute(query).fetchall()
    except sqlite3.Error:
        return []  # a table this store does not have: no candidates from it


def _first_rows(conn: sqlite3.Connection, queries: tuple[str, ...]) -> list[tuple]:
    for query in queries:
        try:
            return conn.execute(query).fetchall()
        except sqlite3.Error:
            continue
    return []


def _is_successor(dependent_id: str, successor: object, resolution: object) -> bool:
    """The dependent replaced the premise: it is named in superseded_by, or by its 8-character id in the resolution."""
    if successor and str(successor) == dependent_id:
        return True
    return bool(resolution) and dependent_id[:8] in str(resolution)


def detect_broken_premises(conn: sqlite3.Connection) -> list[dict]:
    """Open artifacts linked by a resting relation to a premise that has failed, strongest first.

    Not candidates (first precision read, 2026-11-13: 5 of 12 strong candidates were real): a mistake ``caused_by`` the
    premise, since the mistake is having believed it and stands when it is retracted; and the premise's named successor,
    which replaced it rather than resting on it."""
    premises: dict[str, tuple[str, str, float | None, object, object]] = {}
    for ptype, queries in _PREMISES:
        for pid, state, broke, successor, resolution in _first_rows(conn, queries):
            premises[str(pid)] = (ptype, str(state), broke, successor, resolution)
    if not premises:
        return []

    dependents: dict[str, tuple[str, str, float | None]] = {}
    for dtype, table, column, open_condition in _DEPENDENTS:
        for did, text, created in _rows(
            conn, f"SELECT id, {column}, created_timestamp FROM {table} WHERE {open_condition}"
        ):
            dependents[str(did)] = (dtype, str(text or ""), created)

    relations = STRONG + WEAK
    marks = ", ".join("?" for _ in relations)
    try:
        edges = conn.execute(
            f"SELECT from_id, to_id, relation FROM artifact_edges WHERE relation IN ({marks})", relations
        ).fetchall()
    except sqlite3.Error:
        return []

    out = []
    for from_id, to_id, relation in edges:
        dep, prem = dependents.get(str(from_id)), premises.get(str(to_id))
        if dep is None or prem is None:
            continue
        dtype, text, created = dep
        ptype, state, broke, successor, resolution = prem
        if dtype == "mistake" and relation == "caused_by":
            continue
        if _is_successor(str(from_id), successor, resolution):
            continue
        out.append(
            {
                "artifact_id": str(from_id),
                "artifact_type": dtype,
                "text": text[:200],
                "relation": relation,
                "premise_id": str(to_id),
                "premise_type": ptype,
                "premise_state": state,
                "strength": "strong" if relation in STRONG else "weak",
                "created_after_break": bool(
                    created is not None and broke is not None and float(created) >= float(broke)
                ),
                "derived_from": [str(to_id)],
            }
        )
    out.sort(key=lambda h: (h["strength"] != "strong", h["created_after_break"], h["artifact_type"], h["artifact_id"]))
    return out
