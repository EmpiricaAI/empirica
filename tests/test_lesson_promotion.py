"""A lesson authored `private` had no way to become shared.

The lesson store defaults to `private`, so sharing is an act, not an omission —
and no verb performed it. Measured 2026-08-21: **7 of this practice's 24 lessons
were cross-practice patterns permanently invisible to every peer**, because the
only route to a different policy was re-authoring the lesson under the same name
and version. Federation could publish new knowledge and could not promote
existing knowledge.

`update-artifacts` is the right home — it exists precisely for *the artifact is
true and correctly typed but its metadata is wrong* — but it resolved every type
through `ARTIFACT_TABLES` against `sessions.db`, and lessons live in their own
database. Hence `FOREIGN_STORE_TYPES` and a delegating writer.

**The defect this file exists to prevent** was found live, after the code looked
right: `update_metadata` wrote SQLite's `sharing_policy` column and the serialised
`lesson_data` blob, and `get_lesson` still answered `private`. The store has FOUR
layers and the default read path is `_read_cold(...) or _read_warm(...)` — so the
YAML is a read source, not an archive. Three layers correct out of four is still a
two-sources-of-truth defect, and the only way to see it is to assert through the
surface a caller actually reads.
"""

from __future__ import annotations

import json

import pytest

from empirica.core.lessons.hot_cache import LessonHotCache
from empirica.data.artifact_fields import (
    ARTIFACT_TABLES,
    ARTIFACT_UPDATABLE_FIELDS,
    FOREIGN_STORE_TYPES,
    filter_updates,
)


@pytest.fixture
def store(tmp_path):
    """A REAL LessonStorageManager on tmp paths.

    Layer parity is the thing under test, so a fake would remove exactly what
    needs checking — the defect lived in the seam between two real layers. Warm is
    a temp sqlite file built by the store's own migrations; cold is a temp dir.
    """
    from empirica.core.lessons.storage import LessonStorageManager
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase(db_path=str(tmp_path / "s.db"))
    store = LessonStorageManager(db_conn=db.conn, cold_storage_path=tmp_path / "cold")
    store._hot = _FreshHotCache()
    try:
        yield store
    finally:
        db.close()


class _FreshHotCache(LessonHotCache):
    """The hot cache is a process-global singleton, so two tests would share one.

    Per-test isolation matters here specifically: a stale hot entry is one of the
    layers that can drift, and sharing it across tests would make a real drift look
    like leakage from the previous test.

    SUBCLASSES the real cache rather than duck-typing it, so the fake cannot drift
    from the interface it stands in for — the same rule the qdrant fake in
    test_lesson_federation.py earned the hard way.
    """

    def get_lesson(self, lesson_id):
        # Always a miss: reads fall through to the persisted layers, which is where
        # parity lives and where the defect this file guards was hiding.
        return None


def _make(store, policy="private", level="personal", lid="lesson-under-test"):
    from empirica.core.lessons.schema import EpistemicDelta, Lesson, LessonEpistemic

    # Constructed via kwargs rather than assigned after the fact: sharing_policy and
    # abstraction_level are Literal-typed, so post-hoc assignment type-errors even
    # though it runs.
    lesson = Lesson(
        id=lid,
        name="a-transferable-pattern",
        version="1.0",
        description="body",
        epistemic=LessonEpistemic(
            source_confidence=0.8, teaching_quality=0.8, reproducibility=0.8, expected_delta=EpistemicDelta()
        ),
        sharing_policy=policy,
        abstraction_level=level,
    )
    store.create_lesson(lesson)
    return lesson.id


# ── the contract ─────────────────────────────────────────────────────────────


def test_lesson_is_declared_as_a_foreign_store_type():
    """Dispatch is on the declared set, not a hardcoded name in the CLI."""
    assert "lesson" in FOREIGN_STORE_TYPES
    assert "lesson" not in ARTIFACT_TABLES, "it must not resolve against sessions.db"


def test_only_metadata_is_correctable_never_the_lesson_text():
    """Same rule as a finding's claim: a wrong lesson is superseded, not edited."""
    allowed = ARTIFACT_UPDATABLE_FIELDS["lesson"]
    assert "sharing_policy" in allowed
    for immutable in ("name", "description", "steps", "version"):
        assert immutable not in allowed

    updates, rejected = filter_updates("lesson", {"sharing_policy": "public", "description": "rewritten"})
    assert updates == {"sharing_policy": "public"}
    assert rejected == ["description"], "rejected names are REPORTED, not silently dropped"


# ── layer parity: the defect found live ──────────────────────────────────────


def test_a_promotion_is_visible_through_the_default_read_path(store):
    """THE regression. Asserted through `get_lesson`, which is what callers use.

    Writing the column and the blob and skipping the YAML passed every check that
    read SQLite and failed the only one that matters.
    """
    lid = _make(store)
    assert store.update_metadata(lid, {"sharing_policy": "public"})["updated"] == ["sharing_policy"]

    assert store.get_lesson(lid).sharing_policy == "public", "default (auto) read path"


@pytest.mark.parametrize("layer", ["warm", "cold"])
def test_every_persisted_layer_agrees(store, layer):
    """Named per layer so a failure says WHICH one drifted, not merely that one did."""
    lid = _make(store)
    store.update_metadata(lid, {"sharing_policy": "org", "abstraction_level": "cross_org"})

    lesson = store.get_lesson(lid, layer=layer)
    assert lesson.sharing_policy == "org", f"{layer} layer is stale"
    assert lesson.abstraction_level == "cross_org"


def test_the_serialised_blob_agrees_with_its_own_column(store):
    """Read off the row directly — the tell for this class is that every test
    exercises the behaviour and none reads the storage back."""
    lid = _make(store)
    store.update_metadata(lid, {"sharing_policy": "org"})

    column, blob = store._conn.execute(
        "SELECT sharing_policy, lesson_data FROM lessons WHERE id = ?", (lid,)
    ).fetchone()
    assert column == "org"
    assert json.loads(blob)["sharing_policy"] == "org"


# ── refusals and warnings ────────────────────────────────────────────────────


def test_a_demotion_is_applied_and_warned_about_never_silently_accepted(store):
    """It cannot recall what peers already retrieved, and a caller not told that
    will believe they unpublished something."""
    lid = _make(store, policy="public")
    out = store.update_metadata(lid, {"sharing_policy": "private"})

    assert out["updated"] == ["sharing_policy"], "applied — refusing would be worse"
    assert out["warnings"] and "cannot recall" in out["warnings"][0]
    assert store.get_lesson(lid).sharing_policy == "private"


def test_a_promotion_carries_no_such_warning(store):
    """NEGATIVE CONTROL — a warning printed on every update would be ignored on all of them."""
    lid = _make(store)
    assert store.update_metadata(lid, {"sharing_policy": "public"})["warnings"] == []


def test_a_lateral_move_between_shared_policies_is_not_a_demotion(store):
    lid = _make(store, policy="public")
    assert store.update_metadata(lid, {"sharing_policy": "org"})["warnings"] == []


def test_a_missing_lesson_says_so_rather_than_reporting_success(store):
    out = store.update_metadata("no-such-lesson", {"sharing_policy": "public"})
    assert out["updated"] == []
    assert "no lesson with id" in out["warnings"][0]


def test_an_empty_update_is_a_no_op(store):
    lid = _make(store)
    assert store.update_metadata(lid, {}) == {"updated": [], "warnings": []}


# ── promotion actually publishes ─────────────────────────────────────────────


def test_promotion_makes_the_lesson_eligible_to_federate(store, monkeypatch):
    """The whole point: raising the policy must change what the sync sends.

    Without this the promotion is a database write nobody downstream acts on.
    """
    from empirica.core.qdrant import global_sync as gs

    sent: list[str] = []
    monkeypatch.setattr(gs, "_check_qdrant_available", lambda: True)
    monkeypatch.setattr(gs, "embed_to_global", lambda *, item_id, **_: sent.append(item_id) or True)

    import empirica.core.lessons.storage as st

    monkeypatch.setattr(st, "get_lesson_storage", lambda: store)

    lid = _make(store)
    assert gs.sync_lessons_to_global("p")["eligible"] == 0, "private stays home"

    store.update_metadata(lid, {"sharing_policy": "org"})
    out = gs.sync_lessons_to_global("p")

    assert out["eligible"] == 1 and out["synced"] == 1
    assert sent == [f"lesson_{lid}"]
