"""Inline --related-to / --edge endpoint resolution (autonomy prop_nejysxsl).

The inline edge path used to store the raw ``to`` verbatim — a short prefix
(``--related-to 32933cda``) landed as a literal 8-char dangling row, and a
non-existent UUID stored silently. ``_resolve_edge_target`` now:

  - exact-matches a full id,
  - unique-prefix-resolves a short hex id to its full UUID (the ergonomic win),
  - refuses an ambiguous prefix or a no-match endpoint (→ skipped, not stored).

This is the inline-flag twin of #268's graph-path endpoint validation.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from empirica.cli.command_handlers import artifact_log_commands as alc

_FULL = "1bfb481c-e70c-4948-b7db-4d30e64176a5"


def _db() -> SimpleNamespace:
    conn = sqlite3.connect(":memory:")
    for t in ("project_findings", "project_unknowns", "project_dead_ends", "mistakes_made", "assumptions", "decisions"):
        conn.execute(f"CREATE TABLE {t} (id TEXT)")
    conn.execute("CREATE TABLE goals (id TEXT)")
    conn.execute("INSERT INTO project_findings (id) VALUES (?)", (_FULL,))
    conn.execute("INSERT INTO decisions (id) VALUES ('dddddddd-1111-2222-3333-444444444444')")
    return SimpleNamespace(conn=conn)


def test_exact_match_returns_id_unchanged():
    resolved, reason = alc._resolve_edge_target(_db(), _FULL)
    assert resolved == _FULL
    assert reason is None


def test_exact_match_across_tables():
    resolved, _ = alc._resolve_edge_target(_db(), "dddddddd-1111-2222-3333-444444444444")
    assert resolved == "dddddddd-1111-2222-3333-444444444444"  # decisions table too


def test_unique_prefix_resolves_to_full_uuid():
    # autonomy's exact case: an 8-char prefix of a real artifact.
    resolved, reason = alc._resolve_edge_target(_db(), "1bfb481c")
    assert resolved == _FULL  # not the literal prefix
    assert reason is None


def test_ambiguous_prefix_refuses():
    db = _db()
    # A second finding sharing the "1bfb481c" prefix makes it ambiguous.
    db.conn.execute("INSERT INTO project_unknowns (id) VALUES ('1bfb481c-ffff-ffff-ffff-ffffffffffff')")
    resolved, reason = alc._resolve_edge_target(db, "1bfb481c")
    assert resolved is None
    assert reason is not None and "ambiguous" in reason


def test_no_match_dangling_uuid_refused():
    resolved, reason = alc._resolve_edge_target(_db(), "cafebabe-9999-9999-9999-999999999999")
    assert resolved is None
    assert reason is not None and "no artifact matches" in reason


def test_non_hex_junk_refused():
    resolved, reason = alc._resolve_edge_target(_db(), "not-an-id")
    assert resolved is None
    assert reason is not None


def test_empty_endpoint_refused():
    resolved, reason = alc._resolve_edge_target(_db(), "")
    assert resolved is None
    assert reason is not None


def test_short_prefix_below_threshold_not_prefix_resolved():
    # Guard: a <6-char string never prefix-resolves (too broad); it must be
    # an exact match or nothing.
    resolved, reason = alc._resolve_edge_target(_db(), "1bfb")
    assert resolved is None
    assert reason is not None
