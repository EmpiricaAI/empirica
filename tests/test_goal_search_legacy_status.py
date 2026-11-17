"""goals-search --status completed also finds goals Qdrant still holds as 'complete' (goal 967f104c, 1.14.20 sweep A5).

Migration 078 rewrote the legacy status in SQLite only, so points written before it kept 'complete' and a completed filter
missed them. The search reports each match with the current vocabulary.
"""

from __future__ import annotations

from types import SimpleNamespace

from empirica.core.qdrant import goals as qg


class _Client:
    def __init__(self):
        self.filter = None

    def collection_exists(self, _name):
        return True

    def query_points(self, collection_name, query, query_filter, limit, with_payload):
        self.filter = query_filter
        point = SimpleNamespace(score=0.9, payload={"type": "goal", "goal_id": "g1", "status": "complete"})
        return SimpleNamespace(points=[point])


def test_a_completed_filter_matches_the_legacy_status_and_reports_the_current_one(monkeypatch):
    client = _Client()
    monkeypatch.setattr(qg, "_check_qdrant_available", lambda: True)
    monkeypatch.setattr(qg, "_get_qdrant_client", lambda: client)
    monkeypatch.setattr(qg, "_get_embedding_safe", lambda _q: [0.0])
    found = qg.search_goals("p1", "auth", status="completed")
    status_condition = next(c for c in client.filter.must if c.key == "status")
    assert set(status_condition.match.any) == {"completed", "complete"}
    assert found[0]["status"] == "completed"
