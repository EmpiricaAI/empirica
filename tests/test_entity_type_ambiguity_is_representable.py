"""A colliding entity id must be reported as ambiguous, not resolved arbitrarily.

`get_entity_type` ran `SELECT entity_type FROM entity_registry WHERE entity_id = ?
LIMIT 1` — no `ORDER BY` — documented itself as returning the "first match", and
had one caller: `GET /entities/{id}/artifacts`, which echoed the result back as
the response's `entity_type` whenever `?type=` was omitted.

**Why that is worse than a wrong answer.** The artifacts returned alongside are
correctly scoped to whichever type got picked, so the response is internally
consistent. A consumer rendering it has nothing to compare against. An
arbitrary-but-coherent answer is the hardest kind of wrong to notice — there is no
seam where it contradicts itself.

The fix does not choose better. It makes the ambiguity sayable:

  * `get_entity_types` (plural) returns every registered type, sorted
  * `get_entity_type` returns None on a collision instead of a guess
  * the route reports `entity_type_ambiguous` + candidates + a hint

`entity_type: null` alone would not have been enough: that is the same answer an
absent id gives, so a collision would still be indistinguishable from a miss.
"""

from __future__ import annotations

import pytest

from empirica.data.repositories.workspace_db import WorkspaceDBRepository


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throwaway workspace DB with a registry we control."""
    db = tmp_path / "workspace.db"
    monkeypatch.setenv("EMPIRICA_WORKSPACE_DB", str(db))

    r = WorkspaceDBRepository(str(db)) if _accepts_path() else WorkspaceDBRepository.open().__enter__()
    r._execute(
        """CREATE TABLE IF NOT EXISTS entity_registry (
               entity_id TEXT, entity_type TEXT, display_name TEXT,
               status TEXT, metadata TEXT, updated_at TEXT)"""
    )
    yield r


def _accepts_path() -> bool:
    import inspect

    return "db_path" in inspect.signature(WorkspaceDBRepository.__init__).parameters


def _register(repo, entity_id: str, entity_type: str):
    """Supply every NOT NULL column — the real DDL has five, and a short INSERT
    fails with IntegrityError rather than a missing-column error, which reads as
    a constraint violation on the data instead of on the test."""
    repo._execute(
        """INSERT INTO entity_registry
               (entity_type, entity_id, display_name, source_db, source_table, created_at)
           VALUES (?, ?, ?, 'test', 'test', 0.0)""",
        (entity_type, entity_id, f"{entity_id}-{entity_type}"),
    )


def test_a_unique_id_still_resolves(repo):
    """Positive control, and the common case.

    If this broke, the fix would have traded a rare wrong answer for a constant
    one — every single-type id reading as ambiguous.
    """
    _register(repo, "o-acme", "organization")

    assert repo.get_entity_types("o-acme") == ["organization"]
    assert repo.get_entity_type("o-acme") == "organization"


def test_a_colliding_id_returns_every_type(repo):
    """The plural accessor is what makes ambiguity expressible at all."""
    _register(repo, "o-imperial", "organization")
    _register(repo, "o-imperial", "engagement")

    assert repo.get_entity_types("o-imperial") == ["engagement", "organization"], (
        "sorted, so the answer is deterministic — the old query had no ORDER BY"
    )


def test_the_singular_accessor_refuses_to_guess(repo):
    """The defect, as an assertion.

    It used to return whichever row SQLite happened to hand back first. None is
    the honest answer to "what is THE type" when there is more than one.
    """
    _register(repo, "o-imperial", "organization")
    _register(repo, "o-imperial", "engagement")

    assert repo.get_entity_type("o-imperial") is None


def test_an_absent_id_is_empty_not_ambiguous(repo):
    """Absent and ambiguous must stay distinguishable at the repo layer too."""
    assert repo.get_entity_types("o-nothing") == []
    assert repo.get_entity_type("o-nothing") is None


def test_the_schema_permits_the_collision_this_guards(repo):
    """Guards the premise. PRIMARY KEY (entity_type, entity_id) is the reason this
    defect is reachable at all: the same id under DIFFERENT types is legal, while
    the same id under the SAME type is not.

    If the PK were on entity_id alone, collisions would be impossible and every
    assertion here would be guarding nothing — a green suite over an unreachable
    branch.
    """
    import sqlite3

    _register(repo, "o-pk", "organization")
    with pytest.raises(sqlite3.IntegrityError):
        _register(repo, "o-pk", "organization")  # same type — refused by the PK

    _register(repo, "o-pk", "engagement")  # different type — permitted
    assert repo.get_entity_types("o-pk") == ["engagement", "organization"]


# ─── the route contract, called for real ──────────────────────────────


def _call_route(monkeypatch, tmp_path, rows, *, explicit_type=None):
    """Invoke the async route with the repo pointed at a throwaway DB.

    Calls the real handler rather than grepping its source. The original defect
    was invisible PRECISELY because the response looked fine, so a source grep
    would be asserting the wrong artifact — it passes on a branch that never runs.
    """
    import asyncio

    from empirica.api.routes import entities as mod

    db = tmp_path / "route.db"
    real_open = WorkspaceDBRepository.open

    class _Ctx:
        def __enter__(self):
            self.r = WorkspaceDBRepository(str(db)) if _accepts_path() else real_open().__enter__()
            self.r._execute(
                """CREATE TABLE IF NOT EXISTS entity_registry (
                       entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
                       display_name TEXT NOT NULL, description TEXT,
                       source_db TEXT NOT NULL, source_table TEXT NOT NULL,
                       emoji_state TEXT, status TEXT DEFAULT 'active',
                       created_at REAL NOT NULL, updated_at REAL, metadata TEXT,
                       PRIMARY KEY (entity_type, entity_id))"""
            )
            for eid, etype in rows:
                _register(self.r, eid, etype)
            return self.r

        def __exit__(self, *_):
            # Close it. Leaving the handle open leaked a connection per test and
            # the NEXT test failed with "database is locked" — a failure that
            # points at the DB rather than at the fixture that caused it.
            try:
                self.r.close()
            except Exception:
                pass
            return False

    monkeypatch.setattr(
        "empirica.data.repositories.workspace_db.WorkspaceDBRepository.open",
        staticmethod(lambda *a, **k: _Ctx()),
    )
    monkeypatch.setattr(mod, "_enrich_source_artifacts", lambda _a: None, raising=False)
    return asyncio.run(mod.list_entity_artifacts("o-imperial", explicit_type, 100))


def test_the_route_reports_ambiguity_in_its_response(monkeypatch, tmp_path):
    """The defect at the surface a consumer actually reads."""
    payload = _call_route(monkeypatch, tmp_path, [("o-imperial", "organization"), ("o-imperial", "engagement")])

    assert payload["entity_type"] is None, "no guess may be asserted as the type"
    assert payload["entity_type_ambiguous"] is True
    assert payload["entity_type_candidates"] == ["engagement", "organization"]
    assert "?type=" in payload["hint"], "the hint must say what the caller can DO"


def test_a_unique_id_carries_no_ambiguity_keys(monkeypatch, tmp_path):
    """Positive control at the route layer.

    Emitting the keys unconditionally would satisfy the test above while telling
    every caller their unambiguous id is ambiguous.
    """
    payload = _call_route(monkeypatch, tmp_path, [("o-imperial", "organization")])

    assert payload["entity_type"] == "organization"
    assert "entity_type_ambiguous" not in payload
    assert "hint" not in payload


def test_an_explicit_type_is_honoured_over_the_registry(monkeypatch, tmp_path):
    """`?type=` given means the caller has already disambiguated."""
    payload = _call_route(
        monkeypatch,
        tmp_path,
        [("o-imperial", "organization"), ("o-imperial", "engagement")],
        explicit_type="engagement",
    )

    assert payload["entity_type"] == "engagement"
    assert "entity_type_ambiguous" not in payload, "no warning about a question the caller answered"
