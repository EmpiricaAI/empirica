"""`release.py --prepare` proves the tree parses on the oldest Python it claims to support.

pyproject says `requires-python >=3.10`, CI runs 3.11 and 3.13, and a developer's box runs something newer. An f-string that only parses on
3.12 passed ruff (which targets py310 but parses with the newest grammar), passed the whole suite on 3.14, and broke CI on 3.11 after the
push (1.14.14 follow-up, 2026-10-25). Nothing ever ran 3.10 at all. The gate parses every file under the floor interpreter through `uv`.

When it cannot run (no uv, no network for the interpreter) it says so loudly and does not pretend: an unverified floor is not a clean one.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
RELEASE_PY = REPO_ROOT / "scripts" / "release.py"


def _mod():
    spec = importlib.util.spec_from_file_location("release_script_floor", RELEASE_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manager(tmp_path: Path, requires: str = ">=3.10"):
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "x"\nrequires-python = "{requires}"\n')
    rm = _mod().ReleaseManager()
    rm.repo_root = tmp_path
    return rm


def _fake_run(stdout: str = "", returncode: int = 0, stderr: str = ""):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_the_floor_is_read_from_requires_python_not_hardcoded(tmp_path):
    assert _manager(tmp_path, ">=3.10").python_floor() == "3.10"
    assert _manager(tmp_path, ">=3.9,<4").python_floor() == "3.9"


def test_a_clean_tree_passes_and_the_command_pins_the_floor_interpreter(tmp_path, monkeypatch):
    monkeypatch.setattr(_mod().shutil, "which", lambda name: "/usr/bin/uv")
    run = _fake_run(json.dumps({"checked": 12, "errors": []}))
    assert _manager(tmp_path).run_python_floor_parse(run=run) is True
    cmd = run.calls[0]
    assert cmd[:2] == ["uv", "run"] and cmd[cmd.index("--python") + 1] == "3.10" and "--no-project" in cmd


def test_a_syntax_error_on_the_floor_fails_the_gate_and_names_the_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(_mod().shutil, "which", lambda name: "/usr/bin/uv")
    payload = {"checked": 12, "errors": [["scripts/release.py", 1288, "f-string: expecting '}'"]]}
    assert _manager(tmp_path).run_python_floor_parse(run=_fake_run(json.dumps(payload))) is False
    assert "scripts/release.py:1288" in capsys.readouterr().out


def test_without_uv_the_gate_says_it_verified_nothing_and_does_not_fail(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(_mod().shutil, "which", lambda name: None)
    run = _fake_run()
    assert _manager(tmp_path).run_python_floor_parse(run=run) is True
    assert run.calls == []
    assert "NOT VERIFIED" in capsys.readouterr().out


def test_an_interpreter_that_cannot_be_fetched_is_unverified_not_clean(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(_mod().shutil, "which", lambda name: "/usr/bin/uv")
    run = _fake_run(returncode=2, stderr="error: no interpreter found for Python 3.10")
    assert _manager(tmp_path).run_python_floor_parse(run=run) is True
    assert "NOT VERIFIED" in capsys.readouterr().out


def test_the_parse_script_itself_finds_a_syntax_error_and_counts_what_it_walked(tmp_path):
    """Positive control for the script the gate runs: a gate that parsed nothing would also report no errors."""
    for d in ("scripts", "empirica", "tests"):
        (tmp_path / d).mkdir()
    (tmp_path / "empirica" / "ok.py").write_text("x = 1\n")
    (tmp_path / "scripts" / "bad.py").write_text("def broken(:\n")
    out = subprocess.run(
        [sys.executable, "-c", _mod().PYTHON_FLOOR_PARSE_SCRIPT],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    payload = json.loads(out.stdout.strip().splitlines()[-1])
    assert payload["checked"] == 2
    assert [e[0] for e in payload["errors"]] == ["scripts/bad.py"]


def test_prepare_runs_the_floor_gate_after_pyright():
    src = RELEASE_PY.read_text()
    prepare = src[src.index("self.run_pyright():") :]
    assert "run_python_floor_parse()" in prepare[:1500], "the gate must sit with the other pre-publish gates"
