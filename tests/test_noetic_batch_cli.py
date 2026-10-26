"""Integration tests for `empirica noetic-batch` CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("def login():\n    pass\n")
    (tmp_path / "README.md").write_text("# Test\n")
    return tmp_path


def _run_cli(args: list[str], stdin: str | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "empirica.cli", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(cwd) if cwd else None,
        check=False,
    )


def test_cli_stdin_json(project: Path):
    payload = {
        "intent": "smoke",
        "reads": [{"path": "src/auth.py"}],
    }
    proc = _run_cli(
        ["noetic-batch", "-", "--output", "json", "--project-root", str(project)],
        stdin=json.dumps(payload),
        cwd=project,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["ok"] is True
    assert out["intent"] == "smoke"
    assert len(out["reads"]) == 1
    assert "def login()" in out["reads"][0]["content"]


def test_cli_text_output(project: Path):
    payload = {"intent": "smoke", "reads": [{"path": "src/auth.py"}]}
    proc = _run_cli(
        ["noetic-batch", "-", "--output", "text", "--project-root", str(project)],
        stdin=json.dumps(payload),
        cwd=project,
    )
    assert proc.returncode == 0
    assert "NOETIC BATCH" in proc.stdout
    assert "src/auth.py" in proc.stdout


def test_cli_schema(project: Path):
    proc = _run_cli(["noetic-batch", "--schema", "--output", "json"], cwd=project)
    assert proc.returncode == 0
    schema = json.loads(proc.stdout)
    assert "properties" in schema
    assert "intent" in schema["properties"]


def test_cli_dry_run_valid(project: Path):
    payload = {"intent": "x", "reads": [{"path": "anything"}]}
    proc = _run_cli(
        ["noetic-batch", "-", "--dry-run"],
        stdin=json.dumps(payload),
        cwd=project,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["operation_count"] == 1


def test_cli_dry_run_invalid_schema(project: Path):
    payload = {"reads": [{"path": "x"}]}  # missing intent
    proc = _run_cli(
        ["noetic-batch", "-", "--dry-run"],
        stdin=json.dumps(payload),
        cwd=project,
    )
    assert proc.returncode == 2
    out = json.loads(proc.stdout)
    assert out["ok"] is False


def test_cli_invalid_json_stdin(project: Path):
    proc = _run_cli(
        ["noetic-batch", "-"],
        stdin="not valid json",
        cwd=project,
    )
    assert proc.returncode == 2
    out = json.loads(proc.stdout)
    assert "invalid JSON" in out["error"]


def test_cli_per_op_error_exits_1(project: Path):
    """Missing file is per-op error → exit 1, batch still ok."""
    payload = {
        "intent": "x",
        "reads": [{"path": "nonexistent.py"}],
    }
    proc = _run_cli(
        ["noetic-batch", "-", "--output", "json"],
        stdin=json.dumps(payload),
        cwd=project,
    )
    assert proc.returncode == 1
    out = json.loads(proc.stdout)
    assert out["ok"] is True
    assert out["reads"][0]["error"] is not None


def test_cli_flag_form(project: Path):
    proc = _run_cli(
        [
            "noetic-batch",
            "--intent",
            "via flags",
            "--read",
            "src/auth.py",
            "--glob",
            "*.md",
            "--project-root",
            str(project),
        ],
        cwd=project,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["intent"] == "via flags"
    assert len(out["reads"]) == 1
    assert len(out["globs"]) == 1


def test_cli_flag_grep_with_glob(project: Path):
    proc = _run_cli(
        [
            "noetic-batch",
            "--intent",
            "x",
            "--grep",
            "def:src/**/*.py",
        ],
        cwd=project,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["greps"][0]["pattern"] == "def"
    assert out["greps"][0]["glob"] == "src/**/*.py"


def test_cli_flag_grep_with_context(project: Path):
    proc = _run_cli(
        [
            "noetic-batch",
            "--intent",
            "x",
            "--grep",
            "def:src/**/*.py:context=2",
        ],
        cwd=project,
    )
    assert proc.returncode == 0


def test_cli_no_input_exits_2(project: Path):
    proc = _run_cli(["noetic-batch"], cwd=project)
    assert proc.returncode == 2


def test_cli_explicit_project_root_overrides_cwd(project: Path, tmp_path: Path):
    """--project-root scopes the batch even when invoked from a different cwd.

    Regression: ensures the CLI honors an explicit project-root over both the
    InstanceResolver and cwd. Tests the "I'm in another project, want to grep
    the empirica project" workflow.
    """
    foreign = tmp_path / "foreign_cwd"
    foreign.mkdir()
    payload = {"intent": "x", "globs": ["src/**/*.py"]}
    proc = _run_cli(
        ["noetic-batch", "-", "--output", "json", "--project-root", str(project)],
        stdin=json.dumps(payload),
        cwd=foreign,  # cwd is unrelated; --project-root must win
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["globs"][0]["total_matches"] == 1
    assert "auth.py" in out["globs"][0]["matches"][0]
