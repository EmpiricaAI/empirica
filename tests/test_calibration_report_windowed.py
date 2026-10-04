"""`calibration-report --windowed`: a real windowed gap, as a separate and named quantity.

David ruled 2026-10-04 (cowork prop_oskuzp467jgvjkgv3zmcfpmncq): keep the default report as it is, labelled all-time, and add an
opt-in flag that recomputes the gap from grounded_verifications inside the window. It is a DIFFERENT quantity from the default's
`divergence` (the mean of per-verification raw gaps, not the gap between aggregated belief means), so it lives under its own key
and says what it is; the two are never merged.
"""

from __future__ import annotations

import json
import time
import types

import pytest

import empirica.cli.command_handlers.monitor_commands as mc
from empirica.cli.cli_core import create_argument_parser
from empirica.core.post_test.grounded_calibration import GroundedCalibrationManager
from empirica.data.session_database import SessionDatabase

DAY = 86400.0


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "sessions.db"))
    yield d
    try:
        d.close()
    except Exception:  # the report closes the handle it was given
        pass


def _row(db, vid, ai_id, age_days, self_vecs, grounded_vecs):
    db.conn.execute(
        "INSERT INTO grounded_verifications (verification_id, session_id, ai_id, self_assessed_vectors, "
        "grounded_vectors, created_at) VALUES (?, 's', ?, ?, ?, ?)",
        (
            vid,
            ai_id,
            json.dumps(self_vecs),
            json.dumps(
                {
                    k: {"value": v, "confidence": 0.7, "evidence_count": 3, "source": "git"}
                    for k, v in grounded_vecs.items()
                }
            ),
            time.time() - age_days * DAY,
        ),
    )
    db.conn.commit()


def _seed(db):
    # inside 1 week: know self 0.9 vs grounded 0.5 twice -> gap 0.4; outside: know self 0.2 vs grounded 0.8 -> gap -0.6
    _row(db, "v1", "p", 1, {"know": 0.9, "uncertainty": 0.1}, {"know": 0.5, "uncertainty": 0.4})
    _row(db, "v2", "p", 3, {"know": 0.9}, {"know": 0.5})
    _row(db, "v3", "p", 20, {"know": 0.2}, {"know": 0.8})
    _row(db, "v4", "other", 1, {"know": 0.0}, {"know": 1.0})


def test_the_window_filters_by_age_and_by_ai_id(db):
    _seed(db)
    one = GroundedCalibrationManager(db).get_windowed_divergence("p", weeks=1)
    four = GroundedCalibrationManager(db).get_windowed_divergence("p", weeks=4)

    assert one["verifications"] == 2 and four["verifications"] == 3
    assert one["divergence"]["know"]["gap"] == pytest.approx(0.4)
    assert four["divergence"]["know"]["gap"] == pytest.approx((0.4 + 0.4 - 0.6) / 3, abs=1e-4)
    assert four["divergence"]["know"]["observations"] == 3


def test_a_different_window_gives_a_different_number(db):
    """Control for the original defect: --weeks 1 and --weeks 4 may no longer agree."""
    _seed(db)
    g = GroundedCalibrationManager(db)

    assert g.get_windowed_divergence("p", 1)["divergence"] != g.get_windowed_divergence("p", 4)["divergence"]


def test_an_empty_window_says_so_instead_of_returning_zero_gaps(db):
    _seed(db)
    out = GroundedCalibrationManager(db).get_windowed_divergence("p", weeks=1, now=time.time() + 100 * DAY)

    assert out["verifications"] == 0 and out["divergence"] == {}


def test_uncertainty_is_marked_derived(db):
    """Its grounded value is computed from the other vectors' gaps and coverage, not measured (finding d06849e9)."""
    _seed(db)
    div = GroundedCalibrationManager(db).get_windowed_divergence("p", 1)["divergence"]

    assert div["uncertainty"]["derived_from_other_vectors"] is True
    assert "derived_from_other_vectors" not in div["know"]


def _run(db, monkeypatch, capsys, windowed, weeks=1):
    """The report closes the handle it opens, so each run gets a fresh one on the same file."""
    path = db.db_path
    monkeypatch.setattr(mc, "_get_open_disputes", lambda _db: {})
    monkeypatch.setattr("empirica.core.post_test.grounded_calibration.load_calibration_exclusions", lambda _root: [])
    monkeypatch.setattr(
        "empirica.data.session_database.SessionDatabase", lambda *_a, **_k: SessionDatabase(db_path=path)
    )
    mc._show_grounded_calibration(types.SimpleNamespace(windowed=windowed), "p", weeks, "json", False)
    return json.loads(capsys.readouterr().out)


def test_without_the_flag_the_report_is_unchanged_and_has_no_windowed_block(db, monkeypatch, capsys):
    _seed(db)
    out = _run(db, monkeypatch, capsys, windowed=False)

    assert "windowed" not in out and out["window"]["applied"] is False


def test_with_the_flag_the_windowed_block_is_added_and_the_default_is_untouched(db, monkeypatch, capsys):
    _seed(db)
    plain = _run(db, monkeypatch, capsys, windowed=False)
    out = _run(db, monkeypatch, capsys, windowed=True)

    assert out["window"] == plain["window"], "the default divergence is still all-time and still says so"
    assert out["divergence"] == plain["divergence"]
    w = out["windowed"]
    assert w["window"] == {"requested_weeks": 1, "applied": True, "scope": "last_weeks"}
    assert w["verifications"] == 2
    assert "mean of per-verification" in w["quantity"]


def test_the_flag_is_on_the_parser_and_its_help_names_the_quantity():
    parser = create_argument_parser()
    sub = next(a for a in parser._actions if getattr(a, "choices", None) and "calibration-report" in a.choices)
    action = next(a for a in sub.choices["calibration-report"]._actions if a.dest == "windowed")

    assert "different quantity" in action.help.lower() and action.default is False


def test_windowed_is_not_silently_dropped_from_the_human_output(db, monkeypatch, capsys):
    """The flag's help promises a windowed block; --output defaults to human, where it used to vanish."""
    _seed(db)
    path = db.db_path
    monkeypatch.setattr(mc, "_get_open_disputes", lambda _db: {})
    monkeypatch.setattr("empirica.core.post_test.grounded_calibration.load_calibration_exclusions", lambda _root: [])
    monkeypatch.setattr(
        "empirica.data.session_database.SessionDatabase", lambda *_a, **_k: SessionDatabase(db_path=path)
    )

    mc._show_grounded_calibration(types.SimpleNamespace(windowed=True), "p", 1, "human", False)
    text = capsys.readouterr().out

    assert "windowed: last 1 week(s), 2 verifications" in text and "know: +0.400" in text
    assert "does not apply calibration_exclusions" in text
