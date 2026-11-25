"""A failed CI run on the release commit stops --prepare (goal 7315d65f).

1.17.17 prep: CI failed on the release commit (a test flaky on CI's filesystem), yet --prepare printed "Ready to Publish":
_develop_ci_green answered only green or not, and "not green" fell through to a local full-suite run, which passed. A red
CI is evidence a local run cannot overrule; only an unknown or in-progress one falls back to it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

RELEASE_PY = Path(__file__).resolve().parents[1] / "scripts" / "release.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("release_script_ci", RELEASE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manager(mod, runs, head="abc123def4567890"):
    mgr = mod.ReleaseManager(dry_run=True)
    mgr.develop_head = head

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(runs), stderr="")

    return mgr, fake_run


def _run(sha, status="completed", conclusion="success", name="CI", rid=1):
    return {"headSha": sha, "status": status, "conclusion": conclusion, "workflowName": name, "databaseId": rid}


@pytest.mark.parametrize(
    "runs, expected",
    [
        ([_run("abc123def4567890ffff")], "green"),
        ([_run("abc123def4567890ffff", conclusion="failure", rid=7)], "red"),
        ([_run("abc123def4567890ffff", status="in_progress", conclusion="")], "unknown"),
        ([_run("0000000000000000")], "unknown"),  # the newest run is another commit's
        (
            [_run("0000000000000000"), _run("abc123def4567890ffff", conclusion="failure")],
            "red",
        ),  # not only the newest run
        ([_run("abc123def4567890ffff", name="Nightly", conclusion="failure")], "unknown"),  # another workflow
        ([], "unknown"),
        # Nothing failed: a cancelled, skipped or neutral run is not red; the local suite decides (1.17.18 sweep).
        ([_run("abc123def4567890ffff", conclusion="cancelled")], "unknown"),
        ([_run("abc123def4567890ffff", conclusion="skipped")], "unknown"),
        ([_run("abc123def4567890ffff", conclusion="neutral")], "unknown"),
        ([_run("abc123def4567890ffff", conclusion="timed_out")], "red"),
    ],
)
def test_ci_state_for_the_release_commit(mod, monkeypatch, runs, expected):
    mgr, fake = _manager(mod, runs)
    monkeypatch.setattr(mod.subprocess, "run", fake)
    assert mgr._develop_ci_state()[0] == expected


def test_prepare_stops_on_a_red_ci_before_any_local_test_run():
    src = RELEASE_PY.read_text()
    prepare = src[src.index("def run_prepare") :]
    red = prepare.index('== "red"')
    assert red < prepare.index("self.run_tests()")
    assert "sys.exit(1)" in prepare[red : red + 900]
