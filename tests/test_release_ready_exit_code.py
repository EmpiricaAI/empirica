"""release-ready's exit code agrees with the verdict it prints; --strict makes warnings fail the gate.

Found by the 2026-10-06 pipeline sweep (U1) and ruled by David the same day: the handler printed "RELEASE READY (with warnings)" and
exited 1 (`ok` meant "READY exactly"), so automation could trust the text or the exit code but not both. Default: exit 0 when
nothing FAILED. --strict: exit 1 on warnings. A FAIL always exits 1.
"""

from __future__ import annotations

import json
import types

import pytest

from empirica.cli.command_handlers import release_commands as rc

PASS, WARN, FAIL = rc.AssessmentStatus.PASS, rc.AssessmentStatus.WARN, rc.AssessmentStatus.FAIL
REAL_AGENT = (
    rc.EpistemicReleaseAgent
)  # kept: the tests replace rc.EpistemicReleaseAgent with a factory that builds this


def _agent_with(tmp_path, monkeypatch, statuses):
    agent = REAL_AGENT(project_root=tmp_path, quick=True)
    names = [
        "check_version_sync",
        "check_architecture",
        "check_pypi_packages",
        "check_privacy_security",
        "check_documentation",
        "check_git_status",
    ]
    for name, status in zip(names, statuses, strict=True):
        monkeypatch.setattr(
            agent,
            name,
            lambda status=status, name=name: rc.CheckResult(name=name, status=status, message=str(status), moon="x"),
        )
    return agent


@pytest.mark.parametrize(
    ("statuses", "ok", "clean", "verdict"),
    [
        ([PASS] * 6, True, True, "READY"),
        ([PASS, WARN, PASS, PASS, PASS, PASS], True, False, "READY WITH WARNINGS"),
        ([PASS, WARN, PASS, FAIL, PASS, PASS], False, False, "NOT READY"),
    ],
)
def test_run_reports_ok_as_nothing_failed_and_clean_as_nothing_to_note(
    tmp_path, monkeypatch, statuses, ok, clean, verdict
):
    result = _agent_with(tmp_path, monkeypatch, statuses).run()
    assert (result["ok"], result["clean"], result["status"]) == (ok, clean, verdict)


def _handle(tmp_path, monkeypatch, statuses, capsys, **flags):
    monkeypatch.setattr(rc, "EpistemicReleaseAgent", lambda **kw: _agent_with(tmp_path, monkeypatch, statuses))
    args = types.SimpleNamespace(project_root=str(tmp_path), quick=True, output="json", **flags)
    code = rc.handle_release_ready_command(args)
    return code, json.loads(capsys.readouterr().out)


def test_warnings_exit_zero_by_default(tmp_path, monkeypatch, capsys):
    code, out = _handle(tmp_path, monkeypatch, [PASS, WARN, PASS, PASS, PASS, PASS], capsys)
    assert code == 0 and out["ok"] is True and out["status"] == "READY WITH WARNINGS" and out["strict"] is False


def test_strict_makes_warnings_exit_one_and_say_so_in_the_json(tmp_path, monkeypatch, capsys):
    code, out = _handle(tmp_path, monkeypatch, [PASS, WARN, PASS, PASS, PASS, PASS], capsys, strict=True)
    assert code == 1 and out["ok"] is False and out["strict"] is True


@pytest.mark.parametrize("strict", [False, True])
def test_a_fail_always_exits_one(tmp_path, monkeypatch, capsys, strict):
    code, out = _handle(tmp_path, monkeypatch, [PASS, PASS, PASS, FAIL, PASS, PASS], capsys, strict=strict)
    assert code == 1 and out["ok"] is False


@pytest.mark.parametrize("strict", [False, True])
def test_a_clean_run_exits_zero_either_way(tmp_path, monkeypatch, capsys, strict):
    code, out = _handle(tmp_path, monkeypatch, [PASS] * 6, capsys, strict=strict)
    assert code == 0 and out["ok"] is True


def test_the_human_verdict_matches_the_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        rc,
        "EpistemicReleaseAgent",
        lambda **kw: _agent_with(tmp_path, monkeypatch, [PASS, WARN, PASS, PASS, PASS, PASS]),
    )
    base = {"project_root": str(tmp_path), "quick": True, "output": "human"}
    assert rc.handle_release_ready_command(types.SimpleNamespace(**base)) == 0
    assert "RELEASE READY (with warnings)" in capsys.readouterr().out
    assert rc.handle_release_ready_command(types.SimpleNamespace(**base, strict=True)) == 1
    assert "NOT READY (--strict" in capsys.readouterr().out


def test_the_parser_accepts_strict():
    import argparse

    from empirica.cli.parsers.release_parsers import add_release_parsers

    parser = argparse.ArgumentParser()
    add_release_parsers(parser.add_subparsers(dest="command"))
    assert parser.parse_args(["release-ready", "--strict"]).strict is True
    assert parser.parse_args(["release-ready"]).strict is False
