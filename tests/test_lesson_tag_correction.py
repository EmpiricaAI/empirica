"""Tagging an existing lesson: one allowlist entry, and the bind that made it fail.

A peer needed to tag lesson FAMILIES — to test whether people actually retrieve by
family before a lessons knowledge-graph earns its keep — and found no path. They
proposed a new `lesson-update` verb. The mechanism already existed: `update-artifacts`
has written lesson metadata through a foreign-store path since `sharing_policy`
needed promoting. Only `tags` was absent from the correctable set.

**The allowlist entry alone was necessary and not sufficient.** Every previously
correctable field is a string, so `[*updates.values()]` bound fine by coincidence.
The first list-valued field raised `type 'list' is not supported` the moment it was
allowed through — an advertised capability that fails at runtime, which is the exact
shape this store has been bitten by before.

Fixed by binding `to_warm_dict()`'s serialisation rather than the raw value, so the
column format has one definition and a create and an update produce identical bytes.
"""

from __future__ import annotations

import pytest

from empirica.core.lessons import EpistemicDelta, Lesson, LessonEpistemic, get_lesson_storage
from empirica.data.artifact_fields import filter_updates


def test_tags_are_correctable():
    """The allowlist entry. Without it the request is rejected by name before any
    storage call, which is correct behaviour for a field that is not correctable —
    and was the whole blocker."""
    accepted, rejected = filter_updates("lesson", {"tags": ["a", "b"]})
    assert accepted == {"tags": ["a", "b"]}
    assert rejected == []


def test_the_claim_itself_is_still_not_correctable():
    """NEGATIVE CONTROL, and the line this allowlist draws. Tags are governance
    metadata; the lesson's CONTENT is not editable — a lesson that turns out wrong is
    superseded, never quietly rewritten. If `description` ever becomes correctable
    this test fails and forces that to be a decision."""
    accepted, rejected = filter_updates("lesson", {"description": "rewritten"})
    assert accepted == {}
    assert "description" in rejected


@pytest.fixture
def stored(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("EMPIRICA_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    # The cold layer writes YAML into `.empirica/lessons/` relative to cwd and does
    # not create it. Building the directory here rather than letting the store find
    # a real one is the same isolation the rest of this suite needs: without it the
    # test either fails on a fresh tmp_path or, worse, writes into the practitioner's
    # live lesson store.
    (tmp_path / ".empirica" / "lessons").mkdir(parents=True, exist_ok=True)

    storage = get_lesson_storage()
    lesson = Lesson(
        id=Lesson.generate_id("tag-probe", "1.0"),
        name="tag-probe",
        version="1.0",
        description="probe",
        epistemic=LessonEpistemic(
            source_confidence=0.8,
            teaching_quality=0.8,
            reproducibility=0.7,
            expected_delta=EpistemicDelta(),
        ),
        steps=[],
        tags=["original"],
    )
    storage.create_lesson(lesson)
    return storage, lesson.id


def test_a_list_valued_field_binds(stored):
    """THE regression. sqlite3 cannot bind a Python list, and every field correctable
    before this was a string — so the bind worked by coincidence and broke on the
    first list."""
    storage, lid = stored

    result = storage.update_metadata(lid, {"tags": ["family-a", "family-b"]})

    assert result["updated"], result.get("warnings")
    assert not [w for w in result.get("warnings", []) if "not supported" in w]


def test_the_read_path_serves_the_new_tags(stored):
    """Verified where a CONSUMER reads, not at the column. This store has four layers
    and a prior defect promoted a lesson in three of them while `get_lesson` — what
    every caller uses — still served the old value."""
    storage, lid = stored
    storage.update_metadata(lid, {"tags": ["family-a", "family-b"]})

    served = storage.get_lesson(lid)
    assert sorted(getattr(served, "tags", [])) == ["family-a", "family-b"]


def test_update_writes_the_same_column_format_as_create(stored):
    """The reason to bind `to_warm_dict()` rather than special-case lists here.

    `create_lesson` writes tags as a comma-joined string. An update that wrote JSON,
    or a repr, would store something every existing reader mis-parses while the
    update itself reported success — two-sources-of-truth by serialisation rather
    than by layer.
    """
    storage, lid = stored
    storage.update_metadata(lid, {"tags": ["x", "y"]})

    row = storage._conn.execute("SELECT tags FROM lessons WHERE id = ?", (lid,)).fetchone()
    assert row[0] == "x,y", f"column format diverged from create: {row[0]!r}"
