"""A release must check the box that cut it, not only the channels it published to.

`release.py` verified six PUBLISH channels and zero INSTALL surfaces, so a release
read as complete while its own box kept running the previous version through its
CLI, its plugin and its MCP server. After 1.13.47 that cost an evening: a correct
feature was measured as broken by four practices within minutes of shipping,
because no local executor carried it.

The detector already existed — `empirica doctor --deploy-gaps` — and nothing had
ever invoked it. An alarm nobody runs. It now runs at publish, which is the moment
the box BECOMES stale: the tag moves and the local installs do not.

A first attempt at this problem built a second detector in a sibling command
without looking for the first one. It was reverted; one question gets one answer.
"""

from __future__ import annotations

import importlib.util
import json
import types
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release.py"


@pytest.fixture(scope="module")
def R():
    spec = importlib.util.spec_from_file_location("release_script", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manager(R):
    cls = next(v for v in vars(R).values() if isinstance(v, type) and hasattr(v, "report_install_surfaces"))
    return cls.__new__(cls)


def _run_with(R, monkeypatch, capsys, *, stdout="", raises=None):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["cwd"] = cmd, kw.get("cwd")
        if raises:
            raise raises
        return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(R.subprocess, "run", fake_run)
    _manager(R).report_install_surfaces()
    return seen, capsys.readouterr().out


def _checks(*pairs):
    return json.dumps({"checks": [{"name": n, "status": s, "detail": d} for n, s, d in pairs]})


def test_a_gap_is_reported_with_the_refresh_command(R, monkeypatch, capsys):
    """The incident, as an assertion."""
    _seen, out = _run_with(
        R,
        monkeypatch,
        capsys,
        stdout=_checks(("CLI matches checkout", "WARN", "a pipx COPY predating HEAD")),
    )

    assert "CLI matches checkout" in out
    assert "OLDER code than it just released" in out
    assert "pipx install --force --editable" in out, "name the fix, not just the problem"


def test_a_clean_box_gets_no_alarm(R, monkeypatch, capsys):
    """Positive control — an alarm on every release is one nobody reads."""
    _seen, out = _run_with(R, monkeypatch, capsys, stdout=_checks(("CLI matches checkout", "PASS", "editable")))

    assert "PASS" in out
    assert "OLDER code" not in out


def test_the_detector_runs_from_a_neutral_directory(R, monkeypatch, capsys):
    """From inside the repo, cwd can masquerade as the install.

    Publish always runs from the repo root — the one place this matters most.
    """
    seen, _out = _run_with(R, monkeypatch, capsys, stdout=_checks(("x", "PASS", "")))

    assert seen["cmd"][:3] == ["empirica", "doctor", "--deploy-gaps"]
    assert seen["cwd"] and Path(seen["cwd"]).resolve() != _SCRIPT.parent.parent.resolve()


def test_a_detector_that_cannot_run_says_UNCHECKED(R, monkeypatch, capsys):
    """ "Could not check" must not read as "nothing to report"."""
    _seen, out = _run_with(R, monkeypatch, capsys, raises=FileNotFoundError("empirica"))

    assert "UNCHECKED" in out


def test_an_empty_answer_says_UNCHECKED(R, monkeypatch, capsys):
    """A verdict over zero checks is the exemption-reports-clean shape."""
    _seen, out = _run_with(R, monkeypatch, capsys, stdout=json.dumps({"checks": []}))

    assert "UNCHECKED" in out


def test_json_with_a_prefix_is_still_parsed(R, monkeypatch, capsys):
    """The CLI can print a line before its JSON; that must not blank the report.

    Parsing from the first brace rather than the first byte — a JSONDecodeError here
    would silently turn every release's report into "UNCHECKED".
    """
    _seen, out = _run_with(
        R, monkeypatch, capsys, stdout="some banner line\n" + _checks(("CLI matches checkout", "PASS", "ok"))
    )

    assert "CLI matches checkout" in out
    assert "UNCHECKED" not in out


def test_publish_actually_calls_it(R):
    """A reporter nobody calls is the defect it was written to fix."""
    import inspect

    src = inspect.getsource(R)
    assert "self.report_install_surfaces()" in src


# ─── the report REFRESHES the surface it owns, and FAILS the publish if still stale ─


def _checks_with_data(*triples):
    return json.dumps({"checks": [{"name": n, "status": s, "detail": d, "data": data} for n, s, d, data in triples]})


def _scripted(R, monkeypatch, answers):
    """Each subprocess call pops the next scripted (returncode, stdout); records every command."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append({"cmd": cmd, "cwd": kw.get("cwd")})
        rc, out = answers.pop(0)
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr="")

    monkeypatch.setattr(R.subprocess, "run", fake_run)
    return calls


PIPX = {"cli_package_dir": "/home/u/.local/share/pipx/venvs/empirica/lib/python3.12/site-packages/empirica"}
STALE = ("CLI matches checkout", "WARN", "a pipx COPY predating HEAD", PIPX)
FRESH = ("CLI matches checkout", "PASS", "editable", PIPX)


def test_a_stale_pipx_cli_is_reinstalled_editable_and_remeasured(R, monkeypatch, capsys):
    """The refresh that lived in a memory file, executed by the script instead."""
    calls = _scripted(R, monkeypatch, [(0, _checks_with_data(STALE)), (0, ""), (0, _checks_with_data(FRESH))])

    ok = _manager(R).report_install_surfaces()
    out = capsys.readouterr().out

    assert ok is True
    assert [c["cmd"][:2] for c in calls] == [["empirica", "doctor"], ["pipx", "install"], ["empirica", "doctor"]]
    assert calls[1]["cmd"] == ["pipx", "install", "--force", "--editable", "."]
    assert Path(calls[1]["cwd"]).resolve() == _SCRIPT.parent.parent.resolve(), "editable from THIS checkout"
    assert "STILL runs OLDER" not in out
    assert "match the release" in out


def test_a_cli_still_stale_after_the_refresh_fails_the_report(R, monkeypatch, capsys):
    _scripted(R, monkeypatch, [(0, _checks_with_data(STALE)), (0, ""), (0, _checks_with_data(STALE))])

    ok = _manager(R).report_install_surfaces()
    out = capsys.readouterr().out

    assert ok is False
    assert "STILL runs OLDER" in out
    assert "pipx install --force --editable" in out


def test_a_stale_non_pipx_cli_is_not_rewritten_but_still_fails(R, monkeypatch, capsys):
    """A system pip is not ours to reinstall; the verdict is still 'not done'."""
    calls = _scripted(
        R,
        monkeypatch,
        [
            (
                0,
                _checks_with_data(
                    (
                        "CLI matches checkout",
                        "WARN",
                        "site-packages copy",
                        {"cli_package_dir": "/usr/lib/python3/dist-packages/empirica"},
                    )
                ),
            )
        ],
    )

    ok = _manager(R).report_install_surfaces()
    out = capsys.readouterr().out

    assert ok is False
    assert all(c["cmd"][0] != "pipx" for c in calls)
    assert "not a pipx install" in out
    assert "STILL runs OLDER" in out


def test_a_failed_pipx_reinstall_is_reported_and_the_verdict_stays_stale(R, monkeypatch, capsys):
    _scripted(R, monkeypatch, [(0, _checks_with_data(STALE)), (1, "boom")])

    ok = _manager(R).report_install_surfaces()
    out = capsys.readouterr().out

    assert ok is False
    assert "pipx reinstall exited 1" in out


def test_plugin_or_mcp_lag_warns_but_does_not_fail_the_publish(R, monkeypatch, capsys):
    """Those surfaces belong to the ecosystem update on a shared box."""
    _scripted(
        R,
        monkeypatch,
        [(0, _checks_with_data(FRESH, ("Deployed plugin fresh", "WARN", "deployed 1.13.46; package 1.13.47", {})))],
    )

    ok = _manager(R).report_install_surfaces()
    out = capsys.readouterr().out

    assert ok is True
    assert "ecosystem update" in out


def test_unchecked_is_not_a_failed_publish(R, monkeypatch, capsys):
    """Could-not-check is said as such; it is not a verdict in either direction."""
    _seen, out = _run_with(R, monkeypatch, capsys, raises=FileNotFoundError("empirica"))
    assert "UNCHECKED" in out
    # and the return value carries no stale verdict
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("x")))
    assert _manager(R).report_install_surfaces() is True


def test_publish_exits_box_stale_when_the_report_fails(R):
    import inspect

    src = inspect.getsource(R)
    assert "if not self.report_install_surfaces():" in src
    assert "sys.exit(self.EXIT_BOX_STALE)" in src
    cls = next(v for v in vars(R).values() if isinstance(v, type) and hasattr(v, "EXIT_BOX_STALE"))
    assert cls.EXIT_BOX_STALE not in (0, 1), "distinct from success and from a failed publish step"
