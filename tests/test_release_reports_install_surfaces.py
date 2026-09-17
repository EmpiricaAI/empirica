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
