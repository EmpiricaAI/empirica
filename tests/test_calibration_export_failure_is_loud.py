"""A calibration export that fails says so; a fresh practice with nothing to export does not.

export_calibration_to_breadcrumbs returned False for both, and its only
caller logs the False at DEBUG. So a persistent failure left
.breadcrumbs.yaml frozen at its last export while every SessionStart kept
injecting it as current calibration. The failure paths now WARN with the
cause; the nothing-to-export path stays quiet.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from empirica.core import bayesian_beliefs as bb


def _manager_returning(adjustments, report, *, raise_exc=None):
    class _M:
        def __init__(self, db):
            pass

        def get_calibration_adjustments(self, ai_id):
            if raise_exc:
                raise raise_exc
            return adjustments

        def get_calibration_report(self, ai_id):
            return report

    return _M


def test_nothing_to_export_is_quiet(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(bb, "BayesianBeliefManager", _manager_returning({}, {}))
    with caplog.at_level(logging.WARNING, logger=bb.logger.name):
        ok = bb.export_calibration_to_breadcrumbs("ai", db=None, git_root=str(tmp_path))
    assert ok is False
    assert caplog.text == ""


def test_a_failing_belief_read_warns_with_the_cause(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(
        bb, "BayesianBeliefManager", _manager_returning({}, {}, raise_exc=RuntimeError("no such table"))
    )
    with caplog.at_level(logging.WARNING, logger=bb.logger.name):
        ok = bb.export_calibration_to_breadcrumbs("ai", db=None, git_root=str(tmp_path))
    assert ok is False
    assert "reading beliefs FAILED" in caplog.text
    assert "no such table" in caplog.text


def test_an_unreadable_breadcrumbs_path_warns(tmp_path, monkeypatch, caplog):
    """The existing-file read sat outside every try; the caller's broad except
    turned the raise into a debug line."""
    monkeypatch.setattr(bb, "BayesianBeliefManager", _manager_returning({"know": 0.1}, {"n": 1}))
    (tmp_path / ".breadcrumbs.yaml").mkdir()  # a directory where a file is expected
    with caplog.at_level(logging.WARNING, logger=bb.logger.name):
        ok = bb.export_calibration_to_breadcrumbs("ai", db=None, git_root=str(tmp_path))
    assert ok is False
    assert "reading" in caplog.text and "FAILED" in caplog.text


def test_an_unwritable_breadcrumbs_file_warns(tmp_path, monkeypatch, caplog):
    import os

    monkeypatch.setattr(bb, "BayesianBeliefManager", _manager_returning({"know": 0.1}, {"n": 1}))
    target = tmp_path / ".breadcrumbs.yaml"
    target.write_text("calibration:\n  x: 1\n")
    os.chmod(target, 0o444)
    try:
        with caplog.at_level(logging.WARNING, logger=bb.logger.name):
            ok = bb.export_calibration_to_breadcrumbs("ai", db=None, git_root=str(tmp_path))
    finally:
        os.chmod(target, 0o644)
    if os.geteuid() == 0:  # root ignores mode bits; the case cannot be produced
        return
    assert ok is False
    assert "writing" in caplog.text and "FAILED" in caplog.text


def test_the_two_corrections_loaders_have_no_caller():
    """Pins a measured fact: load_bias_corrections and load_grounded_corrections
    are referenced nowhere outside their module. Removing them later is then a
    known act, and so is adding a caller (this test would name the file)."""
    repo = Path(bb.__file__).resolve().parents[2]
    hits = subprocess.run(
        ["rg", "-l", "load_bias_corrections|load_grounded_corrections", str(repo / "empirica"), str(repo / "tests")],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    others = [h for h in hits if not h.endswith("bayesian_beliefs.py") and not h.endswith(Path(__file__).name)]
    assert others == [], others
