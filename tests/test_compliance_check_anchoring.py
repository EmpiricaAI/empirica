"""A compliance check must be anchored to the project, not to where you stood.

Every tool `compliance-report` shells out to resolves its own configuration by
walking up from the directory it runs in. `_run_check` passed no `cwd`, so it
inherited the process working directory — and the report then meant something
different depending on where the operator happened to be. A fork's ruff excludes
applied or did not, and nothing in the output recorded which.

Reported by ecodex (a fork of openai/codex) as the complexity sub-check ignoring
`ruff.toml extend-exclude` while the lint sub-check honoured it. That exact
asymmetry does NOT reproduce — `ruff check` and `ruff check --select C901` honour
excludes identically, verified with a positive control — but the investigation
found this underneath, which can produce the same symptom for a different reason.

`cwd` is deliberately a REQUIRED parameter rather than a defaulted one: a default
lets a new call site silently reintroduce the bug, where a required argument makes
it a TypeError at import time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from empirica.cli.command_handlers.compliance_report_commands import _run_check

_TANGLED = (
    "def tangled(n):\n" + "".join(f"    if n == {i}:\n        return {i}\n" for i in range(25)) + "    return -1\n"
)


@pytest.fixture
def two_configs(tmp_path: Path) -> Path:
    """A root that excludes `sub/`, and a `sub/` whose own config does not.

    The realistic shape for a fork or a monorepo: vendored code excluded at the
    top level, with a nested config that knows nothing about that decision.
    """
    (tmp_path / "sub").mkdir()
    (tmp_path / "ruff.toml").write_text('extend-exclude = ["sub"]\n[lint.mccabe]\nmax-complexity = 15\n')
    (tmp_path / "sub" / "ruff.toml").write_text("[lint.mccabe]\nmax-complexity = 15\n")
    (tmp_path / "sub" / "vendored.py").write_text(_TANGLED)
    return tmp_path


def _c901(cwd: Path) -> dict:
    return _run_check("c901", ["ruff", "check", "--select", "C901"], cwd=cwd, timeout=60)


def test_anchored_to_the_project_the_exclusion_applies(two_configs):
    assert _c901(two_configs)["passed"] is True


def test_anchored_to_a_subdirectory_it_does_not(two_configs):
    """The positive control for the test above.

    Without this, both assertions could pass because C901 never fired at all —
    an exclusion "verified" through an instrument that was never shown to be
    live. This proves the fixture really does contain a violation to exclude.
    """
    assert _c901(two_configs / "sub")["passed"] is False


def test_cwd_has_no_default_so_a_new_call_site_cannot_omit_it(two_configs):
    """The guard is the signature, not vigilance."""
    with pytest.raises(TypeError):
        _run_check("c901", ["ruff", "check"], timeout=10)  # type: ignore[call-arg]


def test_a_tool_that_is_not_installed_is_unavailable_not_failed(tmp_path):
    """`passed: None` + an error, never a silent False — absence is not a violation."""
    r = _run_check("nope", ["definitely-not-a-real-tool-xyz"], cwd=tmp_path, timeout=10)
    assert r["passed"] is None
    assert r["error"] == "tool not installed"
