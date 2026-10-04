"""`calibration-report` says what it computed: whose ai_id, over what window, and which numbers are clamped.

cowork (prop_oskuzp467jgvjkgv3zmcfpmncq) and empirica-extension (prop_dptjb4e76zbcxfiiqeesqoq2fi), from output:

* `--weeks 1` and `--weeks 8` returned byte-identical divergence blocks and the same `observations`. The default grounded path
  never passes the window to get_grounded_beliefs / get_grounded_adjustments / get_calibration_divergence, so the flag, whose help
  said "Number of weeks to analyze", was a no-op there: an advertised flag that is discarded is worse than a missing one.
* The JSON carried no ai_id, so a reader could not confirm the --ai-id filter was applied.
* `adjustments` are the negated, evidence-weighted gap capped at +/-0.25 (MAX_CORRECTION_MAGNITUDE); the cap was not marked, so a
  dashboard showed a clamp as a measurement.
* The --ai-id help said "default: all" while the code resolves the practice's own id.

Not changed here: making the default path actually window its divergence. That recomputes from grounded_verifications rather than
reading the aggregated beliefs, a different quantity, and is a ruling for David. The output now says so instead of implying it.
"""

from __future__ import annotations

import json
import types

import pytest

import empirica.cli.command_handlers.monitor_commands as mc
import empirica.core.post_test.grounded_calibration as gc
from empirica.cli.cli_core import create_argument_parser

ADJUSTMENTS = {"know": 0.25, "do": -0.25, "state": 0.1, "change": -0.0613}
DIVERGENCE = {"know": {"gap": -0.4, "grounded_evidence": 12}, "state": {"gap": -0.1, "grounded_evidence": 9}}


class _FakeGCM:
    def __init__(self, db):
        self.db = db

    def get_grounded_beliefs(self, ai_id):
        return {"know": types.SimpleNamespace(evidence_count=12), "state": types.SimpleNamespace(evidence_count=9)}

    def get_grounded_adjustments(self, ai_id, exclusions=None):
        return dict(ADJUSTMENTS)

    def get_calibration_divergence(self, ai_id, exclusions=None):
        return {k: dict(v) for k, v in DIVERGENCE.items()}

    def summarize_exclusions(self, ai_id, exclusions):
        return [{"vectors": ["change"], "observations": 7, "reason": "r"}] if exclusions else []


@pytest.fixture
def report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "sessions.db"))
    monkeypatch.setattr(gc, "GroundedCalibrationManager", _FakeGCM)
    monkeypatch.setattr(mc, "_get_open_disputes", lambda _db: {})
    # Never read this checkout's project.yaml: the practice's real exclusions would change what these tests see.
    monkeypatch.setattr(gc, "load_calibration_exclusions", lambda _root: [])

    def run(ai_id="some-practice", weeks=8, output="json"):
        mc._show_grounded_calibration(types.SimpleNamespace(), ai_id, weeks, output, False)
        out = capsys.readouterr().out
        return json.loads(out) if output == "json" else out

    return run


def test_the_json_says_which_ai_id_it_was_computed_under(report):
    assert report(ai_id="empirica-extension")["ai_id"] == "empirica-extension"


def test_the_window_is_stated_honestly_whatever_weeks_was_asked(report):
    one, eight = report(weeks=1), report(weeks=8)

    assert one["window"] == {"requested_weeks": 1, "applied": False, "scope": "all_time"}
    assert eight["window"]["requested_weeks"] == 8 and eight["window"]["applied"] is False


def test_the_divergence_really_is_identical_across_weeks_and_the_output_no_longer_hides_it(report):
    """The control for the finding itself: the two reports differ only in the stated request, never in the numbers."""
    one, eight = report(weeks=1), report(weeks=8)

    assert one["divergence"] == eight["divergence"] and one["observations"] == eight["observations"]
    assert one["window"] != eight["window"]


def test_vectors_at_the_cap_are_marked_clamped_and_others_are_not(report):
    out = report()

    assert out["adjustments_clamped"] == ["do", "know"]
    assert out["adjustments"]["state"] == 0.1 and "state" not in out["adjustments_clamped"]


def test_the_sign_conventions_are_in_the_output(report):
    sign = report()["sign"]

    assert "self_referential_mean - grounded_mean" in sign["gap"] and "read higher" in sign["gap"]
    assert "0.25" in sign["adjustments"] and "raise" in sign["adjustments"]


def test_the_human_output_states_the_window_and_the_ai_id(report):
    text = report(ai_id="some-practice", output="human")

    assert "some-practice" in text and "all history" in text.lower()


def _help(dest: str) -> str:
    parser = create_argument_parser()
    sub = next(a for a in parser._actions if getattr(a, "choices", None) and "calibration-report" in a.choices)
    return next(a.help for a in sub.choices["calibration-report"]._actions if a.dest == dest)


def test_the_weeks_help_names_the_paths_it_actually_applies_to():
    text = _help("weeks")

    assert "learning-trajectory" in text and "trajectory" in text and "all history" in text.lower()


def test_the_ai_id_help_no_longer_claims_the_default_is_all():
    text = _help("ai_id")

    assert "default: all" not in text.lower() and "own" in text.lower()


def test_exclusions_applied_are_reported_in_json_and_in_the_human_output(report, monkeypatch):
    entry = {"vectors": ["change"], "source": "git", "from": None, "until": None, "reason": "r"}
    monkeypatch.setattr(gc, "load_calibration_exclusions", lambda _root: [entry])

    assert report()["exclusions_applied"] == [{"vectors": ["change"], "observations": 7, "reason": "r"}]
    assert "excluded as known-bad" in report(output="human")


def test_no_exclusions_means_an_empty_list_not_a_missing_key(report):
    assert report()["exclusions_applied"] == []


def test_the_report_names_the_database_it_read(report, tmp_path):
    """The store is chosen by the instance context, not the cwd, so the output must say which one it read."""
    target = report()["target"]

    assert target["db_path"] == str(tmp_path / "sessions.db")
    assert target["matches_cwd"] in (True, False, None)
