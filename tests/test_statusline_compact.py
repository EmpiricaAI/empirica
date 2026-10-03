"""The compact default statusline, and the expanded view behind a mode switch.

David (2026-10-03): the default showed too many numbers. The default is now one short line:
practice | stage + confidence | goals, unknowns, assumptions, findings/decisions | learning |
context | model - investigate or act. The old default is the `expanded` mode.

Decisions he made, each pinned below: findings and decisions count what was logged in THIS transaction;
the learning rating comes from the grounded calibration of the last closed transaction (not self-reported);
`tests` is a cascade stage (POST until the post-test has graded the transaction, then TEST). Claude Code has
no built-in compact/expanded toggle, so the mode is a file read on every render, with the env var behind it.

Built under tmp_path; the statusline module is loaded by path.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_SL = _ROOT / "empirica" / "plugins" / "claude-code-integration" / "scripts" / "statusline_empirica.py"
_LIB = _ROOT / "empirica" / "plugins" / "claude-code-integration" / "lib"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(s: str) -> str:
    return _ANSI.sub("", s)


@pytest.fixture
def sl(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home" / ".empirica").mkdir(parents=True)
    monkeypatch.delenv("EMPIRICA_STATUS_MODE", raising=False)
    monkeypatch.delenv("EMPIRICA_STATUS_MODEL", raising=False)
    if str(_LIB) not in sys.path:
        sys.path.insert(0, str(_LIB))
    spec = importlib.util.spec_from_file_location("statusline_compact_under_test", _SL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── the learning rating ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("score", "coverage", "expected"),
    [
        (0.03, 0.80, "great"),
        (0.099, 0.80, "great"),
        (0.10, 0.80, "good"),
        (0.199, 0.80, "good"),
        (0.20, 0.80, "average"),
        (0.299, 0.80, "average"),
        (0.30, 0.80, "poor"),
        (0.55, 0.80, "poor"),
    ],
)
def test_the_rating_bands_run_from_great_to_poor_on_the_mean_gap(sl, score, coverage, expected):
    """overall_calibration_score is a gap from the evidence: lower is better."""
    assert sl.learning_rating(score, coverage) == expected


def test_a_score_with_too_little_evidence_behind_it_is_unrated_not_great(sl):
    """A 0.03 over 23% of the vectors says little: say so instead of awarding 'great'."""
    assert sl.learning_rating(0.03, 0.23) == "unrated"


def test_no_graded_transaction_is_pending(sl):
    assert sl.learning_rating(None, None) == "pending"


# ── the cascade stage ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("phase", "tested", "stage"),
    [
        ("PREFLIGHT", False, "PRE"),
        ("CHECK", False, "CHECK"),
        ("POSTFLIGHT", False, "POST"),
        ("POSTFLIGHT", True, "TEST"),
        (None, False, "---"),
    ],
)
def test_the_cascade_stage_moves_from_post_to_test_when_the_post_test_has_graded_it(sl, phase, tested, stage):
    assert sl.cascade_stage(phase, tested) == stage


# ── the data paths ──────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path, sl):
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase(db_path=str(tmp_path / "sessions.db"))
    pid = db.create_project(name="p")
    sid = db.create_session(ai_id="a")
    yield type("S", (), {"db": db, "pid": pid, "sid": sid})
    db.close()


def test_findings_and_decisions_count_the_transaction_not_the_project(sl, store):
    for i in range(2):
        store.db.log_finding(store.pid, store.sid, f"f{i}", transaction_id="tx-A")
    store.db.log_decision(store.pid, store.sid, "c", "r", transaction_id="tx-A")
    store.db.log_finding(store.pid, store.sid, "other", transaction_id="tx-B")

    assert sl.get_transaction_artifacts(store.db, "tx-A") == {"findings": 2, "decisions": 1}
    assert sl.get_transaction_artifacts(store.db, "tx-B") == {"findings": 1, "decisions": 0}
    assert sl.get_transaction_artifacts(store.db, None) == {"findings": 0, "decisions": 0}


def test_assumptions_are_the_unresolved_ones_for_the_project(sl, store):
    store.db.log_assumption(store.pid, store.sid, "a1")
    store.db.log_assumption(store.pid, store.sid, "a2")
    verified = store.db.log_assumption(store.pid, store.sid, "a3")
    store.db.conn.execute("UPDATE assumptions SET status = 'verified' WHERE id = ?", (verified,))
    store.db.conn.commit()

    counts = sl.get_open_counts(store.db, store.sid, project_id=store.pid)

    assert counts["open_assumptions"] == 2


def _verification(store, tx, phase, score, coverage):
    store.db.conn.execute(
        "INSERT INTO grounded_verifications (verification_id, session_id, ai_id, self_assessed_vectors, "
        "grounded_vectors, calibration_gaps, grounded_coverage, overall_calibration_score, evidence_count, "
        "sources_available, sources_failed, domain, created_at, phase, transaction_id) "
        "VALUES (?, ?, 'a', '{}', '{}', '{}', ?, ?, 1, '[]', '[]', 'default', ?, ?, ?)",
        (f"v-{tx}-{phase}", store.sid, coverage, score, time.time(), phase, tx),
    )
    store.db.conn.commit()


def test_a_transaction_with_no_verification_row_is_untested(sl, store):
    assert sl.get_post_test(store.db, "tx-A") == {"tested": False, "score": None, "coverage": None}


def test_a_combined_verification_row_is_the_graded_result(sl, store):
    _verification(store, "tx-A", "combined", 0.12, 0.8)

    assert sl.get_post_test(store.db, "tx-A") == {"tested": True, "score": 0.12, "coverage": 0.8}


def test_split_noetic_and_praxic_rows_are_weighted_by_the_evidence_behind_each(sl, store):
    """No combined row: 0.033 over 23% and 0.128 over 85% must not average to 0.08; the better-evidenced
    half carries the weight."""
    _verification(store, "tx-A", "noetic", 0.033, 0.23)
    _verification(store, "tx-A", "praxic", 0.128, 0.85)

    result = sl.get_post_test(store.db, "tx-A")

    assert result["tested"] is True
    assert result["score"] == pytest.approx((0.033 * 0.23 + 0.128 * 0.85) / (0.23 + 0.85), abs=1e-6)
    assert result["coverage"] == pytest.approx((0.23 + 0.85) / 2, abs=1e-6)


# ── the mode ────────────────────────────────────────────────────────────────


def test_the_default_mode_is_compact(sl):
    assert sl.resolve_status_mode() == "compact"


def test_the_env_var_still_selects_a_mode(sl, monkeypatch):
    monkeypatch.setenv("EMPIRICA_STATUS_MODE", "learning")

    assert sl.resolve_status_mode() == "learning"


def test_the_old_default_name_means_the_expanded_view(sl, monkeypatch):
    monkeypatch.setenv("EMPIRICA_STATUS_MODE", "default")

    assert sl.resolve_status_mode() == "expanded"


def test_the_mode_file_wins_over_the_env_var_and_is_read_every_time(sl, monkeypatch, tmp_path):
    monkeypatch.setenv("EMPIRICA_STATUS_MODE", "learning")
    f = tmp_path / "home" / ".empirica" / "statusline_mode"

    f.write_text("expanded\n")
    assert sl.resolve_status_mode() == "expanded"
    f.write_text("  COMPACT  \n")
    assert sl.resolve_status_mode() == "compact", (
        "case and whitespace do not matter, and a rewrite takes effect at once"
    )


@pytest.mark.parametrize("content", ["", "bogus", "full extra words", "\n"])
def test_an_unusable_mode_file_falls_through_to_the_env_var(sl, monkeypatch, tmp_path, content):
    monkeypatch.setenv("EMPIRICA_STATUS_MODE", "basic")
    (tmp_path / "home" / ".empirica" / "statusline_mode").write_text(content)

    assert sl.resolve_status_mode() == "basic"


# ── the render ──────────────────────────────────────────────────────────────

VECTORS = {
    "know": 0.8,
    "uncertainty": 0.2,
    "context": 0.75,
    "completion": 0.5,
    "clarity": 0.8,
    "coherence": 0.8,
    "signal": 0.8,
    "density": 0.6,
    "state": 0.7,
    "change": 0.3,
    "impact": 0.6,
}
COUNTS = {"open_goals": 2, "open_unknowns": 5, "goal_linked_unknowns": 3, "open_assumptions": 3, "completion": 0.5}
ARTIFACTS = {"findings": 4, "decisions": 1}
STDIN = {"model": {"display_name": "Sonnet 5.5"}, "context_window": {"used_percentage": 41}}


def _render(sl, mode, phase="PREFLIGHT", gate=None, post_test=None, stdin=STDIN, vectors=VECTORS):
    return sl.format_statusline(
        {"session_id": "s", "ai_id": "a"},
        phase,
        vectors,
        None,
        mode,
        gate_decision=gate,
        open_counts=COUNTS,
        project_name="empirica",
        stdin_context=stdin,
        artifacts=ARTIFACTS,
        post_test=post_test or {"tested": False, "score": None, "coverage": None},
    )


def test_the_compact_line_has_every_element_in_david_s_order(sl):
    text = _plain(_render(sl, "compact"))

    order = ["empirica", "PRE 7", "G2 U5 A3 F4/D1", "learning pending", "41%ctx", "Sonnet 5.5", "investigate"]
    positions = [text.find(x) for x in order]
    assert all(p >= 0 for p in positions), (text, positions)
    assert positions == sorted(positions), text


def test_the_practice_label_is_black_on_white(sl):
    raw = _render(sl, "compact")

    assert raw.lstrip().startswith("\x1b[30;47m") and "empirica" in raw.split("\x1b[0m")[0]


def test_model_and_the_work_mode_are_joined_by_a_dash(sl):
    assert "Sonnet 5.5 - " in _plain(_render(sl, "compact")).replace("🧠 ", "")


def test_the_compact_line_drops_the_numbers_the_expanded_view_keeps(sl):
    compact = _plain(_render(sl, "compact"))
    expanded = _plain(_render(sl, "expanded"))

    assert "K:" not in compact and "🎯" not in compact
    assert "K:" in expanded and "🎯" in expanded
    assert len(compact) < len(expanded) + 40


def test_the_old_default_is_what_expanded_renders(sl):
    assert _render(sl, "expanded") == _render(sl, "default")


@pytest.mark.parametrize(
    ("phase", "gate", "word"),
    [
        ("PREFLIGHT", None, "investigate"),
        ("CHECK", "investigate", "investigate"),
        ("CHECK", "proceed", "act"),
        ("POSTFLIGHT", None, "act"),
    ],
)
def test_investigate_or_act_follows_the_stage(sl, phase, gate, word):
    assert _plain(_render(sl, "compact", phase=phase, gate=gate)).rstrip().endswith(word)


def test_after_the_post_test_the_stage_is_test_and_the_learning_is_rated(sl):
    graded = {"tested": True, "score": 0.12, "coverage": 0.85}

    text = _plain(_render(sl, "compact", phase="POSTFLIGHT", post_test=graded))

    assert "TEST" in text and "learning good" in text


def test_before_the_post_test_the_stage_is_post_and_the_learning_is_pending(sl):
    text = _plain(_render(sl, "compact", phase="POSTFLIGHT"))

    assert "POST " in text and "learning pending" in text


def test_a_harness_that_sends_no_model_or_context_still_renders(sl):
    text = _plain(_render(sl, "compact", stdin=None))

    assert (
        "empirica" in text and "learning" in text and "ctx" not in text and " - " not in text.replace("investigate", "")
    )


def test_no_phase_and_no_vectors_does_not_crash(sl):
    text = _plain(_render(sl, "compact", phase=None, vectors={}))

    assert "---" in text and "empirica" in text


def test_the_model_tag_is_not_added_twice(sl):
    assert _plain(_render(sl, "compact")).count("Sonnet 5.5") == 1


@pytest.mark.parametrize("mode", ["basic", "learning", "full", "expanded"])
def test_the_other_modes_still_render(sl, mode):
    assert _plain(_render(sl, mode))


def test_the_model_tag_is_appended_outside_the_other_layouts_but_never_to_compact(sl):
    """main() used to append the tag to every layout. Compact carries it inline, so appending again would show it twice."""
    compact = _render(sl, "compact")
    expanded = _render(sl, "expanded")

    assert sl.compose_output(compact, "compact", STDIN) == compact
    assert _plain(sl.compose_output(expanded, "expanded", STDIN)).count("Sonnet 5.5") == 1
    assert sl.compose_output(expanded, "expanded", None) == expanded
