"""A lesson must come back out of COLD storage as the lesson that went in.

`Lesson.to_dict()` (YAML) wrote the whole migration-037 block — sharing policy,
abstraction level, EKG links, trigger, output rendering, feedback counters — and
`Lesson.from_dict()` read none of it. So every lesson reconstructed from cold
storage silently reverted to the dataclass defaults, and the two that matter most
are `sharing_policy="private"` and `abstraction_level="personal"`.

That is not a cosmetic loss. `sharing_policy` is the field that decides whether a
lesson propagates past the practice that wrote it, so the failure mode was: publish
a lesson at `org`, both storage layers hold `org`, and every read reports it
`private`. It does not fail to share loudly — it reports itself unshared, which is
indistinguishable from a lesson nobody chose to share.

Measured 2026-08-18 on lesson 517629df611ecfaa: `.empirica/lessons/*.yaml` line 157
`sharing_policy: org`, the SQLite `lessons` row `org|cross_org`, and both
`lesson-load` and `lesson-search` returning `private`/`personal`.

The instance is one deserializer. The CLASS is a field added to `to_dict` and
forgotten in `from_dict` — which produces no error, no warning, and a plausible
default. So the round-trip is asserted over *every* serialized key rather than the
handful this incident touched.
"""

from __future__ import annotations

import pytest

from empirica.core.lessons.schema import (
    EpistemicDelta,
    Lesson,
    LessonEpistemic,
    LessonPhase,
    LessonStep,
)

#: Keys `to_dict` emits that `from_dict` is not expected to restore, each with the
#: reason. Anything else missing is a defect, not a decision.
NON_ROUNDTRIP_KEYS: dict[str, str] = {}


def _lesson_with_every_field_set() -> Lesson:
    """Deliberately non-default on every field, so a dropped one cannot hide."""
    return Lesson(
        id="0123456789abcdef",
        name="round-trip probe",
        version="2.1",
        description="every field set to a non-default value",
        epistemic=LessonEpistemic(
            source_confidence=0.91,
            teaching_quality=0.82,
            reproducibility=0.73,
            expected_delta=EpistemicDelta(know=0.25, uncertainty=-0.3),
        ),
        steps=[LessonStep(order=1, phase=LessonPhase.PRAXIC, action="do the thing")],
        suggested_tier="pro",
        suggested_price=12.5,
        created_by="empirica.david.empirica",
        tags=["alpha", "beta"],
        domain="epistemic-hygiene",
        abstraction_level="cross_org",
        sharing_policy="org",
        abstract_pattern="a canonical cross-cutting pattern name",
        parent_lesson_id="fedcba9876543210",
        entity_ids=["e-one", "e-two"],
        project_id="p-1",
        org_id="o-1",
        user_id="u-1",
        trigger_type="event",
        trigger_config={"on": "postflight"},
        output_format="slides",
        output_renderer="llm",
        output_config={"model": "opus"},
        execution_count=7,
        feedback_score=0.66,
        last_executed=1_700_000_000.0,
        last_feedback=1_700_000_001.0,
    )


@pytest.mark.parametrize(
    "key", sorted(k for k in _lesson_with_every_field_set().to_dict() if k not in NON_ROUNDTRIP_KEYS)
)
def test_every_serialized_field_survives_the_cold_round_trip(key: str):
    original = _lesson_with_every_field_set()
    restored = Lesson.from_dict(original.to_dict())
    assert restored.to_dict()[key] == original.to_dict()[key], (
        f"`{key}` is written by to_dict and lost by from_dict — it reverts to the dataclass "
        f"default on every cold read, silently and with a plausible value. Add it to from_dict, "
        f"or record it in NON_ROUNDTRIP_KEYS with a reason if the loss is deliberate."
    )


def test_sharing_policy_is_the_one_that_decides_propagation():
    """Called out separately because a generic round-trip failure reads as cosmetic.

    A lesson published `org` that reads back `private` is indistinguishable from
    one nobody chose to share.
    """
    restored = Lesson.from_dict(_lesson_with_every_field_set().to_dict())
    assert restored.sharing_policy == "org"
    assert restored.abstraction_level == "cross_org"


def test_absent_governance_keys_still_fall_back_to_the_safe_default():
    """Older YAML predates migration 037 — it must load, and must not over-share."""
    d = _lesson_with_every_field_set().to_dict()
    for k in ("sharing_policy", "abstraction_level"):
        d.pop(k)
    restored = Lesson.from_dict(d)
    assert restored.sharing_policy == "private"
    assert restored.abstraction_level == "personal"
