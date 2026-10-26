"""assess-state and trajectory-project must not invent a vector that was never assessed (goal e4344283, the A7 follow-through).

Three readers in monitor_commands.py turned "not assessed" (NULL since 1.14.16) back into a number:

* the assess-state fallback read the NEWEST reflexes row of any phase, so a CHECK row (7 of 13 vectors) hid the PREFLIGHT's other six;
* `_resolve_current_vectors` did the same with no session filter at all, and returned all thirteen vectors at 0.5 when it found nothing;
* the layer scores defaulted each missing vector to 0.5, so a session that had assessed nothing reported "EMERGENT ... PROCEED WITH CAUTION".
"""

from __future__ import annotations

import time

import pytest

from empirica.cli.command_handlers import monitor_commands as mc
from empirica.data.session_database import SessionDatabase

NAMES = [
    "engagement",
    "know",
    "do",
    "context",
    "clarity",
    "coherence",
    "signal",
    "density",
    "state",
    "change",
    "completion",
    "impact",
    "uncertainty",
]
PREFLIGHT = dict.fromkeys(NAMES, 0.8)
CHECK_ONLY = {
    "know": 0.9,
    "uncertainty": 0.1,
    "context": 0.85,
    "clarity": 0.8,
    "coherence": 0.8,
    "signal": 0.8,
    "do": 0.7,
}


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "s.db"
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(path))
    d = SessionDatabase(db_path=str(path))
    yield d
    d.close()


def put(db, session_id, phase, vectors, ts):
    cols = ", ".join(NAMES)
    marks = ", ".join("?" for _ in NAMES)
    db.conn.execute(
        f"INSERT INTO reflexes (session_id, phase, round, timestamp, {cols}, reflex_data) VALUES (?, ?, 1, ?, {marks}, '{{}}')",
        (session_id, phase, ts, *[vectors.get(n) for n in NAMES]),
    )
    db.conn.commit()


def test_the_assess_state_fallback_keeps_the_preflights_vectors_a_later_check_left_null(db):
    now = time.time()
    put(db, "s1", "PREFLIGHT", PREFLIGHT, now - 60)
    put(db, "s1", "CHECK", CHECK_ONLY, now)
    vectors, _ = mc._load_checkpoint_vectors("s1", False)
    assert set(vectors) == set(NAMES), sorted(set(NAMES) - set(vectors))
    assert vectors["know"] == 0.9, "the newer CHECK value wins where the CHECK assessed it"
    assert vectors["impact"] == 0.8, "a vector the CHECK left NULL keeps the PREFLIGHT's recorded value"


def test_resolve_current_vectors_is_scoped_to_the_session_it_was_asked_about(db):
    now = time.time()
    mine = db.create_session(ai_id="a")
    put(db, mine, "PREFLIGHT", PREFLIGHT, now - 60)
    put(db, "someone-else", "CHECK", CHECK_ONLY, now)
    vectors, _ = mc._resolve_current_vectors(db, mine)
    assert vectors.get("impact") == 0.8, "the other session's sparse CHECK must not stand in for mine"


def test_resolve_current_vectors_returns_nothing_not_thirteen_halves_when_nothing_was_recorded(db):
    vectors, _ = mc._resolve_current_vectors(db, None)
    assert vectors == {}, vectors


def test_a_layer_averages_only_the_vectors_that_were_assessed():
    score, missing = mc._layer_score({"context": 0.9}, ("context", "signal"))
    assert score == pytest.approx(0.9)
    assert missing == ["signal"]


def test_a_layer_with_nothing_assessed_has_no_score():
    score, missing = mc._layer_score({}, ("know", "clarity", "coherence"))
    assert score is None
    assert missing == ["know", "clarity", "coherence"]


def test_uncertainty_is_inverted_inside_the_safety_layer():
    score, _ = mc._layer_score(
        {"uncertainty": 0.1, "engagement": 0.9, "impact": 0.8}, ("uncertainty", "engagement", "impact")
    )
    assert score == pytest.approx((0.9 + 0.9 + 0.8) / 3)


def test_a_real_zero_is_a_score_not_a_missing_value():
    score, missing = mc._layer_score({"know": 0.0, "clarity": 0.0, "coherence": 0.0}, ("know", "clarity", "coherence"))
    assert score == 0.0 and missing == []


def test_the_turtle_stack_does_not_certify_a_session_that_assessed_nothing(capsys):
    mc._display_turtle_stack({})
    out = capsys.readouterr().out
    assert "NOT ASSESSED" in out
    assert "SAFE TO PROCEED" not in out and "PROCEED WITH CAUTION" not in out
    assert "0.50" not in out, out


def test_the_turtle_stack_still_certifies_a_fully_assessed_grounded_session(capsys):
    """Negative control: the new handling must not turn every report into a refusal."""
    mc._display_turtle_stack(dict(PREFLIGHT, uncertainty=0.1))
    out = capsys.readouterr().out
    assert "SAFE TO PROCEED" in out, out
    assert "NOT ASSESSED" not in out


def test_trajectory_project_reports_which_layers_were_not_assessed_and_never_calls_them_viable(db, capsys, monkeypatch):
    """With nothing recorded, every layer scores 0.0 (fail closed) and the JSON names the layers as unassessed."""
    import argparse
    import json

    monkeypatch.setattr(mc, "_resolve_current_vectors", lambda _db, _sid=None: ({}, None))
    mc.handle_trajectory_project_command(
        argparse.Namespace(session_id=None, output="json", turtle=False, depth=3, verbose=False)
    )
    result = json.loads(capsys.readouterr().out)
    assert result["grounding"]["unassessed_layers"] == [
        "USER INTENT",
        "NOETIC GRASP",
        "PRAXIC PATH",
        "EPISTEMIC SAFETY",
    ]
    praxic = next(p for p in result["paths"] if p["name"].upper().startswith("PRAXIC"))
    assert praxic["viable"] is False, praxic
