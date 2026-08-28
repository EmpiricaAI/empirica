"""`--from-global` had never worked for any lesson that was ever stored.

The pool returns the STORED record — `id`, `created_timestamp`, `org_id`,
`user_id`, `relations`, `execution_count` and a dozen more — and the create path
validates against the AUTHORING shape, which accepts only what an author may set.
So every ingest died on `Unknown field(s): corrections, created_timestamp, …`.

A producer/consumer mismatch, and the failure mode is what hid it: it refused
LOUDLY and named twenty fields, which reads like a malformed peer record rather
than a broken verb. The natural response is to blame the lesson, not the ingester.

Cross-practice lesson propagation is the ONLY sanctioned path for an artifact to
cross a practice boundary, so the verb being inert meant the mechanism the mesh
relies on to share a policy from one source did not exist.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers.lesson_commands import KNOWN_LESSON_KEYS, _ingest_from_global

#: The shape the pool actually returns — authoring fields plus stored columns.
STORED_RECORD = {
    "name": "a-peer-lesson",
    "description": "what it teaches",
    "steps": [{"action": "do the thing"}],
    "domain": "epistemic-hygiene",
    "abstraction_level": "cross_org",
    "sharing_policy": "org",
    # everything below is stored-only and must not reach the create validator
    "id": "e3b487c997177444",
    "created_timestamp": 1756000000.0,
    "updated_timestamp": 1756000001.0,
    "org_id": "org-empirica",
    "user_id": "u-123",
    "project_id": "p-456",
    "relations": [],
    "execution_count": 3,
    "feedback_score": 0.9,
    "corrections": [],
}


@pytest.fixture
def fetched(monkeypatch):
    import empirica.core.qdrant.global_sync as gs

    monkeypatch.setattr(
        gs, "fetch_global_lesson", lambda _id: {"record": dict(STORED_RECORD), "origin_project_id": "peer-practice"}
    )
    return STORED_RECORD


def test_stored_only_columns_never_reach_the_validator(fetched):
    """THE fix. Every key returned must be one an author is allowed to set."""
    record, err = _ingest_from_global("e3b487c997177444")
    assert err is None
    assert set(record) <= KNOWN_LESSON_KEYS, f"stored-only fields survived: {set(record) - KNOWN_LESSON_KEYS}"


def test_the_teaching_content_survives(fetched):
    """POSITIVE CONTROL, and not optional: a filter that returned {} would pass the
    test above perfectly while ingesting nothing."""
    record, _ = _ingest_from_global("e3b487c997177444")
    assert record["name"] == "a-peer-lesson"
    assert record["steps"] == [{"action": "do the thing"}]
    assert record["description"]


def test_attribution_is_stamped_and_sharing_is_not_inherited(fetched):
    """The copy carries its origin permanently — that is what stops it re-entering
    the pool under our name — and never inherits `shared`, because re-sharing a
    peer's lesson by default is precisely what must not happen."""
    record, _ = _ingest_from_global("e3b487c997177444")
    assert record["origin_practice"] == "peer-practice"
    assert record["sharing_policy"] == "private"


def test_the_filter_is_derived_not_hand_listed():
    """A drop-list of stored columns needs editing every time the stored shape
    grows one, and would silently start failing again on the first one nobody
    remembered. Filtering against KNOWN_LESSON_KEYS cannot rot that way."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "empirica/cli/command_handlers/lesson_commands.py").read_text()
    assert "if k in KNOWN_LESSON_KEYS" in src


def test_a_stepless_pool_record_refuses_rather_than_ingesting_a_husk(monkeypatch):
    """The fix's own cost, paid back.

    Filtering to the authoring shape turns "unknown field" from a LOUD refusal
    into a SILENT drop. That is right for `org_id` and `execution_count`, and
    wrong for teaching content — and this is the one path whose producer is a
    system we do not control, so it is exactly where a rename would land. Without
    this guard a stepless record stores fine and returns `ok: true` with
    `step_count: 0`: a lesson that teaches nothing, reported as created.

    Both docstrings already promised this refusal. Nothing enforced it.
    """
    import empirica.core.qdrant.global_sync as gs

    husk = {k: v for k, v in STORED_RECORD.items() if k != "steps"}
    monkeypatch.setattr(gs, "fetch_global_lesson", lambda _id: {"record": husk, "origin_project_id": "peer"})

    record, err = _ingest_from_global("e3b487c997177444")
    assert record is None
    assert err and "no replayable steps" in err


def test_the_stepless_refusal_names_what_it_dropped(monkeypatch):
    """A refusal that only says "no steps" points the reader at the peer's record,
    which is the accusation shape that hid the original bug for the verb's whole
    life. The message has to say which side expected what — so it names the fields
    OUR filter discarded, making a pool-schema rename diagnosable from the error
    alone instead of looking like a malformed lesson.
    """
    import empirica.core.qdrant.global_sync as gs

    drifted = {k: v for k, v in STORED_RECORD.items() if k != "steps"}
    drifted["procedure"] = [{"action": "renamed upstream"}]
    monkeypatch.setattr(gs, "fetch_global_lesson", lambda _id: {"record": drifted, "origin_project_id": "peer"})

    _, err = _ingest_from_global("e3b487c997177444")
    assert err and "procedure" in err, "the dropped field carrying the content must be named"
    assert "not the record being malformed" in err


def test_a_missing_pool_record_still_refuses(monkeypatch):
    """NEGATIVE CONTROL. The refusal path must survive the fix — minting a stub
    from a description alone is worse than saying no."""
    import empirica.core.qdrant.global_sync as gs

    monkeypatch.setattr(gs, "fetch_global_lesson", lambda _id: None)
    record, err = _ingest_from_global("nope")
    assert record is None and err and "no shared lesson" in err
