"""A lesson revised before `--supersedes` existed could never be retired.

Reported by cortex (prop_6cfjxruyona7ndpcksdvdsiyrm, 2026-09-25): v1.0 of a lesson
carried a hand-written ``superseded_by`` in its YAML that the store never read, and
after v3.0 was published with ``--supersedes <v2.0>`` the default ``lesson-list``
served v3.0 AND v1.0. ``--supersedes`` runs only at create time for one id, and
``resolve-artifacts`` refused the type, so no verb could write the missing edge.

``resolve-artifacts`` now takes ``{"type": "lesson", "id": OLD, "superseded_by": NEW}``
and writes the same edge ``lesson-create`` does. Both ids must exist: an edge from
a lesson that does not exist would suppress the old one while nothing replaces it.
"""

from __future__ import annotations

import io
import json
import sys

import pytest


class _FakeStore:
    def __init__(self, existing):
        self._existing = set(existing)
        self.edges: list[tuple] = []

    def get_lesson(self, lid, layer="auto"):
        return object() if lid in self._existing else None

    def add_edge(self, source_id, target_id, relation_type, **_):
        self.edges.append((source_id, target_id, relation_type))
        return "edge-id"


@pytest.fixture
def store(tmp_path, monkeypatch):
    import empirica.core.lessons as _lessons
    import empirica.data.session_database as _sdb

    db_file = str(tmp_path / "t.db")
    real = _sdb.SessionDatabase
    monkeypatch.setattr(_sdb, "SessionDatabase", lambda *a, **k: real(db_path=db_file))
    fake = _FakeStore(existing={"old1", "new3"})
    monkeypatch.setattr(_lessons, "get_lesson_storage", lambda: fake)
    return fake


def _resolve(payload: dict, capsys) -> dict:
    from empirica.cli.command_handlers.graph_commands import handle_resolve_artifacts_command

    class _Args:
        input = "-"
        output = "json"
        verbose = False

    stdin, sys.stdin = sys.stdin, io.StringIO(json.dumps(payload))
    try:
        handle_resolve_artifacts_command(_Args())
    finally:
        sys.stdin = stdin
    return json.loads(capsys.readouterr().out)


def test_an_existing_pair_is_retired_after_the_fact(store, capsys):
    out = _resolve({"resolutions": [{"type": "lesson", "id": "old1", "superseded_by": "new3"}]}, capsys)

    assert out["resolved"] == 1 and out["errors"] == []
    assert store.edges == [("new3", "old1", "supersedes")], "successor supersedes the old lesson, not the reverse"


def test_a_lesson_without_a_named_successor_is_refused(store, capsys):
    out = _resolve({"resolutions": [{"type": "lesson", "id": "old1", "resolution": "stale"}]}, capsys)

    assert out["resolved"] == 0
    assert "superseded_by" in out["errors"][0]
    assert store.edges == []


def test_a_successor_that_does_not_exist_writes_nothing(store, capsys):
    """Otherwise the old lesson is withheld and nothing is served in its place."""
    out = _resolve({"resolutions": [{"type": "lesson", "id": "old1", "superseded_by": "ghost"}]}, capsys)

    assert out["resolved"] == 0
    assert "ghost" in out["errors"][0]
    assert store.edges == []


def test_a_superseded_lesson_that_does_not_exist_writes_nothing(store, capsys):
    out = _resolve({"resolutions": [{"type": "lesson", "id": "nope", "superseded_by": "new3"}]}, capsys)

    assert out["resolved"] == 0
    assert "nope" in out["errors"][0]
    assert store.edges == []
