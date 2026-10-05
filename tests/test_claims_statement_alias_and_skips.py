"""A claim keyed `statement` was dropped silently, and the Sentinel then said none had been declared.

ecodex (prop_f2byxezk4vdztjf2l5ftpryxgm), reproduced on their own transactions: `claims.declare` read only `claim` for the text and
`continue`d when it was empty. The falsifier docs teach `{statement, query, falsifies}`, so models carry `statement` over to
claims; four PREFLIGHTs on 2026-10-04 each declared 2-3 claims and none was stored, the PREFLIGHT response looked fine, and the
first praxic call was refused with "no grounded claims declared", which tells the practitioner to re-declare, the one thing that
cannot help. "Accepted and discarded" is the failure class the file's own comments name.

Three changes: `statement` and `text` are aliases of `claim`; an item that still cannot be stored is reported back with why
(never silently); and the Sentinel's deny points at that report.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from empirica.core import claims as C
from empirica.data.session_database import SessionDatabase

HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "t.db"))
    try:
        yield d
    finally:
        d.close()


def _declare(db, claims):
    return C.declare_reporting(db, session_id="s1", transaction_id="t1", claims=claims)


def test_a_claim_keyed_claim_is_stored_as_before(db):
    """Positive control for everything below."""
    stored, skipped = _declare(db, [{"claim": "x holds", "grounding": "read"}])

    assert [c["claim"] for c in stored] == ["x holds"] and skipped == []


@pytest.mark.parametrize("key", ["statement", "text"])
def test_the_alias_keys_are_stored_with_their_scope_and_count(db, key):
    stored, skipped = _declare(db, [{key: "x holds", "grounding": "ran", "scope": "all rows", "count": 12}])

    (c,) = stored
    assert (
        c["claim"] == "x holds" and c["grounding"] == "ran" and c["scope"] == "all rows" and c["measured_count"] == 12
    )
    assert skipped == []


def test_claim_wins_when_both_keys_are_given(db):
    stored, _ = _declare(db, [{"claim": "the real one", "statement": "ignored", "grounding": "read"}])

    assert stored[0]["claim"] == "the real one"


def test_an_item_with_no_text_is_reported_with_why_not_dropped_silently(db):
    stored, skipped = _declare(
        db,
        [{"claim": "kept", "grounding": "read"}, {"body": "no recognised text key", "grounding": "ran"}, "not a dict"],
    )

    assert [c["claim"] for c in stored] == ["kept"]
    assert [s["position"] for s in skipped] == [2, 3]
    assert (
        skipped[0]["keys"] == ["body", "grounding"]
        and "claim" in skipped[0]["reason"]
        and "statement" in skipped[0]["reason"]
    )
    assert "not an object" in skipped[1]["reason"]


def test_declare_keeps_returning_just_the_stored_rows(db):
    """The older callers and tests use declare(); its contract is unchanged."""
    assert [c["claim"] for c in C.declare(db, session_id="s1", transaction_id="t1", claims=[{"statement": "s"}])] == [
        "s"
    ]


def test_the_echo_carries_submitted_and_skipped_and_exists_even_when_nothing_was_stored(db):
    stored, skipped = _declare(db, [{"body": "nope"}])

    echo = C.summarize_for_check(stored, skipped=skipped, submitted=1)

    assert echo is not None and echo["declared"] == 0 and echo["submitted"] == 1
    assert echo["skipped"][0]["keys"] == ["body"] and "re-run" in echo["skipped_note"].lower()


def test_a_clean_declaration_echo_has_no_skipped_noise(db):
    stored, skipped = _declare(db, [{"claim": "ok", "grounding": "read"}])

    echo = C.summarize_for_check(stored, skipped=skipped, submitted=1)

    assert "skipped" not in echo and "submitted" not in echo


def test_the_preflight_helper_echoes_the_skip(db, monkeypatch):
    """End to end through the real PREFLIGHT path: a statement-keyed claim is stored, a keyless one is named."""
    from empirica.cli.command_handlers import _workflow_preflight as pf

    monkeypatch.setattr(pf, "_get_db_for_session", lambda _sid: SessionDatabase(db_path=str(db.db_path)))

    echo = pf._preflight_declare_claims(
        "s1", "t1", [{"statement": "seen", "grounding": "read"}, {"detail": "wrong key"}]
    )

    assert echo["declared"] == 1 and echo["skipped"][0]["keys"] == ["detail"] and echo["certifies_transaction"] is True


def test_the_sentinel_deny_points_at_the_skipped_report():
    sys.path.insert(0, str(HOOKS.parent / "lib"))
    spec = importlib.util.spec_from_file_location("sentinel_gate_claims_skip", HOOKS / "sentinel-gate.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    mod._claims_lookup_error = None

    _, text = mod._deny_no_check_no_claims()

    assert "skipped" in text and "claim, grounding" in text
