"""A tool failure must clear a HIGH bar before becoming a permanent dead-end.

A dead-end is retrieved into later sessions as "avoid re-trying", so a false positive
does not merely add noise — it removes a viable approach from the practice's option
space. Measured 2026-07-27: 637 of 750 open dead-ends on this practice were captured
tool failures, including a `git commit` that SUCCEEDED (its own why_failed text
contained the successful push) but was recorded because a CI-wait loop in the same
command hit `timeout`. Future sessions were being told to avoid re-trying git commit.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def hook():
    path = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/tool-failure.py"
    spec = importlib.util.spec_from_file_location("tool_failure_hook", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "error",
    [
        "Exit code 143\nCommand timed out after 10m 0s",  # THE dominant real case
        "Command timed out after 2m 0s and was moved to the background",
        "Exit code 137 — killed",
        "Error: operation timed out after 30s waiting for the run to finish",
    ],
)
def test_timeouts_and_signals_are_not_dead_ends(hook, error):
    """The clock or the harness killed it. That says nothing about whether the
    APPROACH works — and a timeout message is long, so the >=20-char heuristic waved
    every one of these straight through."""
    assert hook._is_interesting_failure("Bash", error) is False


def test_a_command_that_actually_worked_is_not_a_dead_end(hook):
    """The exact live case: the push landed; a wait loop in the same command timed
    out."""
    error = "Exit code 143\nCommand timed out after 10m 0s\n   205c60bf2..678335bc7  develop -> develop"
    assert hook._is_interesting_failure("Bash", error) is False


@pytest.mark.parametrize(
    "error",
    ["3 files changed, 40 insertions(+)", "Successfully installed empirica", "12 passed in 0.3s"],
)
def test_success_markers_veto_capture(hook, error):
    assert hook._is_interesting_failure("Bash", error) is False


def test_operational_outages_are_not_epistemic(hook):
    """A service being down is an operational fact with a lifetime of minutes; a
    dead-end is permanent."""
    assert hook._is_interesting_failure("Bash", "curl: (7) Failed to connect: Connection refused") is False
    assert hook._is_interesting_failure("Bash", "ssh: Could not resolve host: empirica-server") is False


def test_a_genuine_approach_failure_is_still_captured(hook):
    """The filter must not be so wide that nothing lands — this is the case the hook
    exists for."""
    error = (
        "ModuleNotFoundError: No module named 'qdrant_client.async_client' — "
        "the async API was removed in 1.9 and there is no drop-in replacement"
    )
    assert hook._is_interesting_failure("Bash", error) is True


def test_short_errors_remain_uninteresting(hook):
    assert hook._is_interesting_failure("Bash", "nope") is False
