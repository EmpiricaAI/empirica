"""`lesson-create` must not report success for content it discarded.

Reported by empirica.david.empirica-mesh-support, verified in source.

The handler cherry-picked a fixed key list with `.get()`, so anything it did not
recognise vanished. A call passing name/title/summary/context/pattern/
anti_pattern/application/visibility/confidence returned::

    {"ok": true, "lesson_id": "75d69f48…", "step_count": 0,
     "message": "Lesson … created successfully"}

with `description: ''`, `steps: []`. Every content field dropped, no warning, a
success message.

Three fields were never passed to the constructor at all, so the dataclass
defaults won regardless of what the caller supplied — an omission, not a
coercion: `sharing_policy` (→ `private`), `abstraction_level` (→ `personal`),
`abstract_pattern` (→ `None`). And step `phase` was collapsed by
``NOETIC if phase == "noetic" else PRAXIC``, so every unrecognised phase became
praxic silently.

`sharing_policy` is the consequential one. It decides whether the lesson crosses
the practice boundary, which is the entire distinction between a lesson and a
finding — so a practitioner authoring a lesson *specifically to propagate a
pattern* got a success message and an artifact no peer would ever see.
"""

from __future__ import annotations

from argparse import Namespace

import pytest

from empirica.cli.command_handlers.lesson_commands import (
    KNOWN_LESSON_KEYS,
    LESSON_ENUMS,
    handle_lesson_create_command,
)


@pytest.fixture(autouse=True)
def _isolate_lesson_store(tmp_path, monkeypatch):
    """Keep these tests out of the practice's real lesson store.

    `handle_lesson_create_command` calls `get_lesson_storage()`, which writes
    YAML to `.empirica/lessons` and rows to the live sessions database. Without
    this fixture the suite deposits its fixtures — `valid`, `shared-one`,
    `abstracted`, `defaulted`, `t` — into the practice's actual knowledge, where
    they are indistinguishable from authored lessons and get retrieved as though
    they meant something.

    Written after doing exactly that: five test lessons landed in the real store
    on the first run, the same defect as the E2E suite that had been writing
    production goals since 2026-02-17 (fixed a9ad3dcb8 the same day).
    """
    import empirica.core.lessons as lessons_pkg
    from empirica.core.lessons.storage import LessonStorageManager

    db = tmp_path / "lessons-sessions.db"
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(db))

    def _isolated(*_a, **_kw):
        return LessonStorageManager(cold_storage_path=tmp_path / "lessons")

    # The handler imports get_lesson_storage inside the function body, so
    # patching it on the package is enough — it resolves at call time.
    monkeypatch.setattr(lessons_pkg, "get_lesson_storage", _isolated)
    yield


def _run(payload: dict) -> dict:
    import json as _json

    return handle_lesson_create_command(Namespace(json=_json.dumps(payload), input=None, output="json"))


# ─── Unknown fields ────────────────────────────────────────────────────


def test_unknown_fields_are_rejected_not_dropped():
    """The reported call: content fields the handler does not know about."""
    result = _run({"name": "t", "description": "d", "summary": "dropped", "pattern": "also dropped"})
    assert result["ok"] is False
    assert set(result["unknown_fields"]) == {"summary", "pattern"}


def test_the_error_names_what_was_rejected_and_what_is_accepted():
    """`--help` documents --name/--input/--json/--output and nothing about the
    payload schema, so the error message is the only place a caller can learn
    the contract. It has to carry it."""
    result = _run({"name": "t", "anti_pattern": "x"})
    assert "anti_pattern" in result["error"]
    assert "description" in result["error"], "the accepted set must be listed"
    assert set(result["accepted_fields"]) == set(KNOWN_LESSON_KEYS)


def test_a_fully_valid_payload_is_accepted():
    """The guard must not reject the documented shape."""
    result = _run(
        {
            "name": "valid",
            "version": "1.0",
            "description": "body",
            "epistemic": {"source_confidence": 0.9},
            "steps": [{"order": 1, "phase": "noetic", "action": "read"}],
            "domain": "d",
            "tags": ["a"],
        }
    )
    assert result["ok"] is True, result.get("error")


# ─── Enums rejected, not coerced ───────────────────────────────────────


@pytest.mark.parametrize("field", sorted(LESSON_ENUMS))
def test_out_of_vocabulary_enum_is_rejected(field: str):
    result = _run({"name": "t", "description": "d", field: "not-a-real-value"})
    assert result["ok"] is False
    assert field in result["error"]
    for allowed in LESSON_ENUMS[field]:
        assert allowed in result["error"], "the error must list the vocabulary"


def test_sharing_policy_public_actually_persists():
    """THE REGRESSION, and the one with a consequence.

    Reverting to `private` means the lesson never leaves the practice. The
    author is told it was created; no peer ever sees it.
    """
    result = _run({"name": "shared-one", "description": "d", "sharing_policy": "public"})
    assert result["ok"] is True, result.get("error")
    assert result["stored"]["sharing_policy"] == "public"


def test_abstraction_level_and_abstract_pattern_persist():
    result = _run(
        {
            "name": "abstracted",
            "description": "d",
            "abstraction_level": "cross_org",
            "abstract_pattern": "read-back-from-the-serving-surface",
        }
    )
    assert result["stored"]["abstraction_level"] == "cross_org"
    assert result["stored"]["abstract_pattern"] == "read-back-from-the-serving-surface"


def test_defaults_still_apply_when_unspecified():
    result = _run({"name": "defaulted", "description": "d"})
    assert result["stored"]["sharing_policy"] == "private"
    assert result["stored"]["abstraction_level"] == "personal"


# ─── Step phase ────────────────────────────────────────────────────────


def test_unrecognised_step_phase_is_rejected_not_coerced_to_praxic():
    """A six-step lesson using diagnose/remediate/verify stored six praxic
    steps and reported success."""
    result = _run(
        {
            "name": "t",
            "description": "d",
            "steps": [{"order": 1, "phase": "noetic", "action": "a"}, {"order": 2, "phase": "diagnose", "action": "b"}],
        }
    )
    assert result["ok"] is False
    assert "diagnose" in result["error"]
    assert "step 2" in result["error"], "the caller needs to know WHICH step"


@pytest.mark.parametrize("phase", ["noetic", "praxic", "NOETIC"])
def test_valid_phases_are_accepted_case_insensitively(phase: str):
    result = _run({"name": "t", "description": "d", "steps": [{"order": 1, "phase": phase, "action": "a"}]})
    assert result["ok"] is True, result.get("error")
    assert result["stored"]["steps"][0]["phase"] == phase.lower()


# ─── The receipt ───────────────────────────────────────────────────────


def test_the_receipt_echoes_the_stored_record_not_a_message():
    """`ok: true` beside "created successfully" is not checkable — the reporter
    had to read the YAML back to discover the lesson was an empty shell.
    Success and failure must produce different, legible output."""
    result = _run(
        {"name": "t", "description": "twelve chars", "steps": [{"order": 1, "phase": "noetic", "action": "a"}]}
    )
    stored = result["stored"]
    assert stored["description_chars"] == len("twelve chars")
    assert stored["steps"] == [{"order": 1, "phase": "noetic"}]
    assert "sharing_policy" in stored, "the field that decides propagation must be in the receipt"
