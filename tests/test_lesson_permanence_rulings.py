"""David's two lesson-store rulings (2026-09-04): refuse silent replace; test noise may die.

The store's id is md5(name + version), so `lesson-create` under a used
name+version used to OVERWRITE IN PLACE — same id, same version, body replaced,
no history, no recovery — on a store whose whole contract is that a lesson is
PERMANENT. A peer measured it before anyone was bitten cross-practice.

And test noise was immortal: `delete-artifacts` refused type `lesson` outright,
so probe rows sat in the store forever, while superseding one leaves two rows
where there was one — worse than the problem.

The line drawn: a lesson that is WRONG is superseded, never deleted; a row that
never carried a claim may be deleted, through the same dry-run machinery as
every other destructive op, with per-layer honesty (#413's lesson applied to
lessons themselves).
"""

from __future__ import annotations

import pytest

from empirica.core.lessons import EpistemicDelta, Lesson, LessonEpistemic, get_lesson_storage


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolated singleton bound to tmp_path — same pattern (and reasons) as
    test_lesson_tag_correction.py's fixture: the store is a module singleton and
    the cold layer writes relative to cwd."""
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("EMPIRICA_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".empirica" / "lessons").mkdir(parents=True, exist_ok=True)

    import empirica.core.lessons.storage as _store_mod

    monkeypatch.setattr(_store_mod, "_storage", None)
    # Sever the SEARCH layer too. The tag-correction fixture this is modeled on
    # doesn't touch qdrant, and its tests never noticed — but a delete test that
    # inherits the box's live client writes a probe VECTOR into the real
    # `empirica_lessons` collection, which is precisely the test-noise sin this
    # ruling exists to clean up. Measured: the first run of this file did it.
    monkeypatch.setattr(_store_mod, "_try_get_qdrant_client", lambda: None)
    yield get_lesson_storage()
    monkeypatch.setattr(_store_mod, "_storage", None)


def _lesson(name="probe lesson", version="1.0", body="original body"):
    return Lesson(
        id=Lesson.generate_id(name, version),
        name=name,
        version=version,
        description=body,
        epistemic=LessonEpistemic(
            source_confidence=0.8,
            teaching_quality=0.8,
            reproducibility=0.7,
            expected_delta=EpistemicDelta(),
        ),
        steps=[],
    )


# ── refuse silent replace ────────────────────────────────────────────────────


def test_recreating_an_existing_lesson_REFUSES(store):
    """THE regression. Same name + same version = same id = silent overwrite."""
    assert store.create_lesson(_lesson(body="original body"))["ok"] is True

    second = store.create_lesson(_lesson(body="a completely different body"))

    assert second["ok"] is False
    assert "REFUSING" in second["error"]


def test_the_refusal_names_both_designed_paths(store):
    """A refusal that doesn't say what TO do teaches the workaround it fears —
    the peer who reported this explicitly declined two bad workarounds and asked
    for the path."""
    store.create_lesson(_lesson())
    err = store.create_lesson(_lesson())["error"]

    assert "version" in err
    assert "--supersedes" in err


def test_the_original_body_survives_the_refused_attempt(store):
    """The refusal must actually protect the content, not just complain."""
    store.create_lesson(_lesson(body="original body"))
    store.create_lesson(_lesson(body="attacker body"))

    got = store.get_lesson(Lesson.generate_id("probe lesson", "1.0"), layer="warm")
    assert got is not None
    assert "original body" in (got.description or "")


def test_a_version_bump_still_creates(store):
    """NEGATIVE CONTROL — the designed revision path must stay open. A new
    version is a new id, so it is not a replace."""
    assert store.create_lesson(_lesson(version="1.0"))["ok"] is True
    assert store.create_lesson(_lesson(version="1.1"))["ok"] is True


def test_a_different_name_still_creates(store):
    assert store.create_lesson(_lesson(name="lesson A"))["ok"] is True
    assert store.create_lesson(_lesson(name="lesson B"))["ok"] is True


# ── test noise may die, honestly ─────────────────────────────────────────────


def test_delete_removes_every_layer_and_says_so(store):
    store.create_lesson(_lesson(name="zztest-probe"))
    lid = Lesson.generate_id("zztest-probe", "1.0")

    result = store.delete_lesson(lid)

    assert result["ok"] is True
    assert result["existed"] is True
    assert result["layers"]["warm"] == "deleted"
    assert result["layers"]["cold"] == "deleted"
    # no qdrant client in this fixture — the layer must say so, not pretend
    assert result["layers"]["search"].startswith("unavailable")

    assert store.get_lesson(lid, layer="warm") is None


def test_deleting_nothing_cannot_report_as_a_cleanup(store):
    """#413's lesson: a backend answers deletion of an absent point with success,
    so 'existed' must come from a read, not from the delete call's mood."""
    result = store.delete_lesson("feedfacefeedface")

    assert result["existed"] is False
    assert result["layers"]["warm"] == "absent"
    assert result["layers"]["cold"] == "absent"


def test_delete_is_scoped_to_one_lesson(store):
    """The child-table sweep must not take siblings with it."""
    store.create_lesson(_lesson(name="keep me"))
    store.create_lesson(_lesson(name="zztest-probe"))

    store.delete_lesson(Lesson.generate_id("zztest-probe", "1.0"))

    assert store.get_lesson(Lesson.generate_id("keep me", "1.0"), layer="warm") is not None


# ── the CLI routing ──────────────────────────────────────────────────────────


def test_delete_artifacts_routes_lesson_to_the_store(monkeypatch, store):
    """The graph verb used to refuse type lesson outright; now it routes, and
    dry-run NAMES the lesson — a count is unreviewable, the name is what tells
    an operator the row about to die is a probe and not knowledge."""
    import empirica.cli.command_handlers.graph_commands as gc

    store.create_lesson(_lesson(name="zztest-probe"))
    lid = Lesson.generate_id("zztest-probe", "1.0")

    monkeypatch.setattr("empirica.core.lessons.get_lesson_storage", lambda: store)

    preview = gc._delete_foreign_lesson(lid, dry_run=True)
    assert preview["action"] == "would_delete"
    assert preview["name"] == "zztest-probe"
    # dry-run must not have deleted anything
    assert store.get_lesson(lid, layer="warm") is not None

    done = gc._delete_foreign_lesson(lid, dry_run=False)
    assert done["action"] == "deleted"
    assert store.get_lesson(lid, layer="warm") is None


def test_delete_artifacts_refuses_an_unknown_lesson_id(monkeypatch, store):
    import empirica.cli.command_handlers.graph_commands as gc

    monkeypatch.setattr("empirica.core.lessons.get_lesson_storage", lambda: store)

    result = gc._delete_foreign_lesson("feedfacefeedface", dry_run=True)
    assert "error" in result
