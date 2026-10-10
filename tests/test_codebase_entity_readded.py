"""An entity that was removed and then re-added is active again.

`upsert_entity` found the existing (name, type, file, project) row and updated its signature, but left `last_seen` set, so
an entity invalidated when its file changed stayed invalidated after the function came back: `find_entities` (active
only) never returned it. The lookup also had no ORDER BY, so with an invalidated and an active twin it could update the
wrong row.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.data.repositories.codebase_model import CodebaseModelRepository
from empirica.data.schema.codebase_model_schema import SCHEMAS


@pytest.fixture
def repo():
    conn = sqlite3.connect(":memory:")
    for statement in SCHEMAS:
        conn.execute(statement)
    return CodebaseModelRepository(conn)


def _active_names(repo):
    return [e["name"] for e in repo.find_entities(project_id="p", active_only=True)]


def test_a_removed_then_readded_entity_is_active_again(repo):
    entity_id = repo.upsert_entity("parse", "function", file_path="a.py", signature="parse(x)", project_id="p")
    repo.invalidate_entity(entity_id)
    assert _active_names(repo) == []
    again = repo.upsert_entity("parse", "function", file_path="a.py", signature="parse(x, y)", project_id="p")
    assert again == entity_id  # the same entity, not a duplicate
    assert _active_names(repo) == ["parse"]
    assert repo.find_entities(project_id="p")[0]["signature"] == "parse(x, y)"


def test_an_entity_that_stayed_active_is_unchanged_by_a_repeat_upsert(repo):
    entity_id = repo.upsert_entity("parse", "function", file_path="a.py", project_id="p")
    repo.upsert_entity("parse", "function", file_path="a.py", signature="parse(x)", project_id="p")
    assert _active_names(repo) == ["parse"] and len(repo.find_entities(project_id="p", active_only=False)) == 1
    assert repo.find_entities(project_id="p")[0]["id"] == entity_id


def test_an_invalidated_entity_stays_invalidated_until_it_is_upserted(repo):
    entity_id = repo.upsert_entity("gone", "function", file_path="a.py", project_id="p")
    repo.invalidate_entity(entity_id)
    assert _active_names(repo) == []
    assert repo.count_entities("p", active_only=True) == 0


def test_with_an_invalidated_and_an_active_twin_the_active_one_is_updated(repo):
    old = repo.upsert_entity("dup", "function", file_path="a.py", project_id="p")
    repo.invalidate_entity(old)
    # a twin row inserted directly, as an older writer could have left behind
    repo.conn.execute(
        "INSERT INTO codebase_entities (id, entity_type, name, file_path, first_seen, last_seen, project_id) "
        "VALUES ('twin', 'function', 'dup', 'a.py', 2.0, NULL, 'p')"
    )
    repo.conn.commit()
    updated = repo.upsert_entity("dup", "function", file_path="a.py", signature="dup()", project_id="p")
    assert updated == "twin"
    assert [e["id"] for e in repo.find_entities(project_id="p")] == ["twin"]
