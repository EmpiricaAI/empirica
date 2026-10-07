"""check_architecture must not PASS on an average built from only the directories that answered.

Found by the 2026-10-06 pipeline sweep (U1): a failed, timed-out or unreadable directory assessment appended a detail string and was
left out of the average, so release-ready could report 'Architecture health: 0.80' with the data layer never assessed. A subset now
downgrades a PASS to WARN and says how many directories were missing; it never improves a WARN or FAIL.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import release_commands as rc

DIRS = ("empirica/core", "empirica/cli", "empirica/data")
OK, BAD = "ok", "bad"


def _run_with(tmp_path, monkeypatch, outcomes):
    for d in DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    agent = rc.EpistemicReleaseAgent(project_root=tmp_path, quick=False)
    queue = iter(outcomes)

    def fake_run(cmd, **kwargs):
        outcome = next(queue)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(cmd, 60)
        if outcome == "exit":
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        if outcome == "garbage":
            return SimpleNamespace(returncode=0, stdout="not json", stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"average_health": outcome}), stderr="")

    monkeypatch.setattr(rc.subprocess, "run", fake_run)
    return agent.check_architecture()


def test_all_directories_assessed_and_healthy_passes(tmp_path, monkeypatch):
    result = _run_with(tmp_path, monkeypatch, [0.8, 0.8, 0.8])
    assert result.status == rc.AssessmentStatus.PASS and "could not be assessed" not in result.message


@pytest.mark.parametrize("failure", ["exit", "timeout", "garbage"])
def test_a_directory_that_could_not_be_assessed_downgrades_a_pass_to_warn(tmp_path, monkeypatch, failure):
    result = _run_with(tmp_path, monkeypatch, [0.9, 0.9, failure])
    assert result.status == rc.AssessmentStatus.WARN
    assert "(1 of 3 directories could not be assessed)" in result.message


def test_two_unassessed_are_counted(tmp_path, monkeypatch):
    result = _run_with(tmp_path, monkeypatch, [0.9, "exit", "timeout"])
    assert result.status == rc.AssessmentStatus.WARN and "(2 of 3 directories" in result.message


def test_an_unhealthy_average_stays_a_fail_when_a_directory_is_also_missing(tmp_path, monkeypatch):
    """The U1 draft turned this FAIL into a WARN, weakening the verdict."""
    result = _run_with(tmp_path, monkeypatch, [0.2, 0.3, "exit"])
    assert result.status == rc.AssessmentStatus.FAIL and "could not be assessed" in result.message


def test_a_warn_average_stays_a_warn(tmp_path, monkeypatch):
    result = _run_with(tmp_path, monkeypatch, [0.6, 0.6, "timeout"])
    assert result.status == rc.AssessmentStatus.WARN


def test_nothing_assessed_is_still_the_existing_warn(tmp_path, monkeypatch):
    result = _run_with(tmp_path, monkeypatch, ["exit", "exit", "exit"])
    assert result.status == rc.AssessmentStatus.WARN and result.message == "Could not assess architecture"
