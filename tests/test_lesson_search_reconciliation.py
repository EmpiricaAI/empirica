"""Semantic lesson search must not serve lessons that cannot be loaded.

`search_lessons`' semantic branch built results straight from the Qdrant point
payload and never touched the store — while the improves-vector and domain
branches both resolve through `get_lesson` and skip misses. The payload is
frozen at embed time and the collection outlives deletions, so the one
unreconciled branch was the one a practitioner queries.

Measured on this practice 2026-08-17 before the fix: WARM(sqlite)=24,
COLD(yaml)=18, SEARCH(qdrant)=60 — **43 of 60 embedded ids had no store
record**, 17 payloads carried an empty description, and those ghosts outranked
real lessons (0.703 vs 0.663 on a query the real lesson should have won).

Same shape as the goal reconciler (`pattern_retrieval._reconcile_goals_*`).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from empirica.core.lessons.storage import LessonStorageManager


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolated store — never the practice's real lessons (see
    test_lesson_create_rejects_silently_dropped_fields for why this matters)."""
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "lessons-sessions.db"))
    return LessonStorageManager(cold_storage_path=tmp_path / "lessons")


class _FakeQdrant:
    """Returns points whose payloads name lessons that may or may not exist."""

    def __init__(self, points):
        self._points = points
        self.last_limit: int | None = None

    def query_points(self, collection_name, query, limit):
        self.last_limit = limit
        return SimpleNamespace(points=self._points[:limit])

    # search_lessons' __init__ path calls this; keep it inert
    def collection_exists(self, *_a, **_kw):
        return True


def _point(lesson_id, score, name="payload name", description="payload desc"):
    return SimpleNamespace(
        payload={"lesson_id": lesson_id, "name": name, "description": description},
        score=score,
    )


def _real_lesson(store, lesson_id, name, description):
    """Write a lesson through the store so get_lesson() resolves it."""
    from empirica.core.lessons import EpistemicDelta, Lesson, LessonEpistemic

    lesson = Lesson(
        id=lesson_id,
        name=name,
        version="1.0",
        description=description,
        epistemic=LessonEpistemic(
            source_confidence=0.8,
            teaching_quality=0.8,
            reproducibility=0.8,
            expected_delta=EpistemicDelta(),
        ),
    )
    store.create_lesson(lesson)
    return lesson


def test_unloadable_hits_are_dropped(store, monkeypatch):
    _real_lesson(store, "real0001", "Real lesson", "a real description")
    store._qdrant = _FakeQdrant([_point("ghost001", 0.90), _point("real0001", 0.60)])
    monkeypatch.setattr(store, "_generate_embedding", lambda _q: [0.0] * 8)

    out = store.search_lessons(query="anything", limit=5)
    assert [r["id"] for r in out] == ["real0001"], "a hit with no store record must not be served"


def test_name_and_description_come_from_the_record_not_the_payload(store, monkeypatch):
    """A payload frozen at embed time (here: empty description, stale name) must
    not be what the practitioner reads — 17 live payloads had empty descriptions."""
    _real_lesson(store, "real0002", "Authoritative name", "authoritative description")
    store._qdrant = _FakeQdrant([_point("real0002", 0.7, name="STALE name", description="")])
    monkeypatch.setattr(store, "_generate_embedding", lambda _q: [0.0] * 8)

    (hit,) = store.search_lessons(query="anything", limit=5)
    assert hit["name"] == "Authoritative name"
    assert hit["description"] == "authoritative description"


def test_limit_is_honoured_despite_dropped_ghosts(store, monkeypatch):
    """Over-fetch then trim: a collection full of orphans must still fill the
    requested limit from the lessons that do resolve."""
    for i in range(3):
        _real_lesson(store, f"real100{i}", f"Real {i}", f"desc {i}")
    points = [_point(f"ghost{i:03d}", 0.99 - i / 100) for i in range(10)]
    points += [_point(f"real100{i}", 0.5 - i / 100) for i in range(3)]
    store._qdrant = _FakeQdrant(points)
    monkeypatch.setattr(store, "_generate_embedding", lambda _q: [0.0] * 8)

    out = store.search_lessons(query="anything", limit=2)
    assert len(out) == 2
    assert all(r["id"].startswith("real") for r in out)


def test_overfetch_asks_for_more_than_limit(store, monkeypatch):
    """The fix must over-fetch, or every orphan in the collection shortens the
    result list by one."""
    _real_lesson(store, "real2001", "Real", "desc")
    fake = _FakeQdrant([_point("real2001", 0.5)])
    store._qdrant = fake
    monkeypatch.setattr(store, "_generate_embedding", lambda _q: [0.0] * 8)

    store.search_lessons(query="anything", limit=5)
    assert (fake.last_limit or 0) > 5


def test_payload_without_lesson_id_is_skipped(store, monkeypatch):
    store._qdrant = _FakeQdrant([SimpleNamespace(payload={"name": "no id"}, score=0.9)])
    monkeypatch.setattr(store, "_generate_embedding", lambda _q: [0.0] * 8)
    assert store.search_lessons(query="anything", limit=5) == []


# ── amend-in-place is the model, and it must be legible ──────────────────────
#
# There is no lesson-update verb and none is needed: the id is deterministic
# from (name, version) and create_lesson upserts every layer, so re-publishing
# the same name+version amends in place. That is also how you clobber a lesson
# by reusing a name — so the response has to say which happened.


def _create(payload, monkeypatch, tmp_path):
    import json as _json
    from argparse import Namespace

    import empirica.core.lessons as lessons_pkg
    from empirica.cli.command_handlers.lesson_commands import handle_lesson_create_command

    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "amend-sessions.db"))
    store = LessonStorageManager(cold_storage_path=tmp_path / "lessons")
    monkeypatch.setattr(lessons_pkg, "get_lesson_storage", lambda *_a, **_kw: store)
    return handle_lesson_create_command(Namespace(json=_json.dumps(payload), input=None, output="json"))


def test_first_publish_reports_not_replaced(tmp_path, monkeypatch):
    out = _create({"name": "Amendable", "version": "1.0", "description": "first"}, monkeypatch, tmp_path)
    assert out["ok"] is True
    assert out["replaced"] is False


def test_republishing_same_name_and_version_amends_in_place(tmp_path, monkeypatch):
    _create({"name": "Amendable", "version": "1.0", "description": "first"}, monkeypatch, tmp_path)
    out = _create(
        {"name": "Amendable", "version": "1.0", "description": "amended with a cross-reference"},
        monkeypatch,
        tmp_path,
    )
    assert out["replaced"] is True, "same name+version must be reported as a replacement, not a silent clobber"
    assert out["stored"]["description_chars"] == len("amended with a cross-reference")


def test_bumping_version_publishes_alongside(tmp_path, monkeypatch):
    a = _create({"name": "Amendable", "version": "1.0", "description": "first"}, monkeypatch, tmp_path)
    b = _create({"name": "Amendable", "version": "1.1", "description": "revised"}, monkeypatch, tmp_path)
    assert b["replaced"] is False
    assert a["lesson_id"] != b["lesson_id"]


# ── get_lesson must not report a warm-held lesson as missing ────────────────


def test_auto_read_falls_back_to_warm_when_cold_is_missing(store):
    """The hot cache is populated from WARM rows, so a lesson whose YAML is
    gone takes the hot branch, reads cold, gets None — and was reported
    not-found while SQLite still held the complete record (7 real lessons,
    steps intact, unreachable this way)."""
    _real_lesson(store, "warmonly01", "Warm survivor", "content lives in sqlite")
    (store._cold_path / "warmonly01.yaml").unlink()

    assert store.get_lesson("warmonly01", layer="cold") is None, "precondition: cold really is gone"
    assert store.get_lesson("warmonly01", layer="warm") is not None, "precondition: warm still has it"

    recovered = store.get_lesson("warmonly01")
    assert recovered is not None, "auto read must fall back to warm rather than report not-found"
    assert recovered.name == "Warm survivor"


def test_auto_read_still_returns_none_for_a_truly_absent_lesson(store):
    assert store.get_lesson("nosuchlesson") is None
