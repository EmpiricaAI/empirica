"""The meta-test's own negative control.

`scripts/negative_control.py` exists to catch tests that pass on the fix and would
also have passed on the bug. Shipping it with its own VACUOUS branch never
executed would be self-refuting — so this builds synthetic repositories where the
right answer is known by construction, including the case the tool exists to
report.

Each fixture is a throwaway git repo under tmp_path with two commits: a first that
establishes a bug, and a second that fixes it and adds a test. What varies is
whether the added test can tell the two apart.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "negative_control.py"
_spec = importlib.util.spec_from_file_location("negative_control", _SCRIPT)
negative_control = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(negative_control)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "tests").mkdir()
    return repo


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _run_against(repo: Path) -> dict:
    return negative_control.run("HEAD", root=repo)


def test_a_test_that_can_tell_the_bug_from_the_fix_is_DISCRIMINATES(tmp_path):
    repo = _repo(tmp_path, "good")
    (repo / "calc.py").write_text("def double(n):\n    return n + n if n else 1\n")
    _commit(repo, "seed")

    (repo / "calc.py").write_text("def double(n):\n    return n * 2\n")
    (repo / "tests" / "test_calc.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))\n"
        "from calc import double\n\n"
        "def test_zero():\n    assert double(0) == 0\n"
    )
    _commit(repo, "fix(calc): double(0) returned 1")

    v = _run_against(repo)
    assert v["result"] == "DISCRIMINATES"
    assert v["failed"] == 1


def test_a_test_that_passes_on_the_bug_too_is_VACUOUS(tmp_path):
    """The branch this tool exists for: real assertion, real fix, no discrimination.

    `double(2)` is 4 under both implementations, so the test verifies nothing
    about the defect that was fixed. A green suite cannot distinguish this from
    the test above, which is the entire problem.
    """
    repo = _repo(tmp_path, "vacuous")
    (repo / "calc.py").write_text("def double(n):\n    return n + n if n else 1\n")
    _commit(repo, "seed")

    (repo / "calc.py").write_text("def double(n):\n    return n * 2\n")
    (repo / "tests" / "test_calc.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))\n"
        "from calc import double\n\n"
        "def test_two():\n    assert double(2) == 4\n"
    )
    _commit(repo, "fix(calc): double(0) returned 1")

    v = _run_against(repo)
    assert v["result"] == "VACUOUS"
    assert v["passed"] == 1
    assert v["claims_a_fix"] is True, "a commit prefixed fix( must be flagged, not merely reported"


def test_a_refactor_with_passing_tests_is_not_treated_as_a_defect(tmp_path):
    """VACUOUS is expected for a refactor and must not read as a finding.

    Same mechanical outcome as the test above — every test passes against the
    parent source — but the commit claims no fix, so `claims_a_fix` is False and
    --strict stays quiet. Conflating the two would make the tool noise.
    """
    repo = _repo(tmp_path, "refactor")
    (repo / "calc.py").write_text("def double(n):\n    return n * 2\n")
    _commit(repo, "seed")

    (repo / "calc.py").write_text("def double(n):\n    result = n * 2\n    return result\n")
    (repo / "tests" / "test_calc.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))\n"
        "from calc import double\n\n"
        "def test_two():\n    assert double(2) == 4\n"
    )
    _commit(repo, "refactor(calc): name the intermediate")

    v = _run_against(repo)
    assert v["result"] == "VACUOUS"
    assert v["claims_a_fix"] is False


def test_a_commit_with_no_tests_is_skipped_not_passed(tmp_path):
    """NOT_APPLICABLE is its own outcome — folding it into a pass hides the gap."""
    repo = _repo(tmp_path, "notests")
    (repo / "calc.py").write_text("def double(n):\n    return n + n\n")
    _commit(repo, "seed")

    (repo / "calc.py").write_text("def double(n):\n    return n * 2\n")
    _commit(repo, "fix(calc): arithmetic")

    v = _run_against(repo)
    assert v["result"] == "NOT_APPLICABLE"
    assert "no test files" in v["why"]


def test_a_test_for_brand_new_source_cannot_have_run_before_it_existed(tmp_path):
    """A source file ADDED by the commit is removed, not reverted.

    The import failure that follows is the correct answer: a test for code that
    did not exist could not have passed against the parent.
    """
    repo = _repo(tmp_path, "newfile")
    (repo / "keep.py").write_text("X = 1\n")
    _commit(repo, "seed")

    (repo / "feature.py").write_text("def shiny():\n    return 42\n")
    (repo / "tests" / "test_feature.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))\n"
        "from feature import shiny\n\n"
        "def test_shiny():\n    assert shiny() == 42\n"
    )
    _commit(repo, "feat: add shiny")

    v = _run_against(repo)
    assert v["source_removed"] == ["feature.py"]
    assert v["result"] == "DISCRIMINATES"
