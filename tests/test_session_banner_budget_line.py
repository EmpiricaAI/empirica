"""The SessionStart banner says when the context-budget state was not saved (goal 3468e18f; ecodex prop_houwj6qw3jfzzkqdokafni754q; roadmap).

`_init_context_budget` records `summary["persist"]` as `persisted`, `skipped: <reason>` or `failed: see hook log`, and returns `{"error": ...}` when
initialisation itself raised. The banner printed the Budget line only when there was no error and never read `persist`, so since the persist became
conditional (1bb10b937) a skipped save left no trace anywhere a person looks: the reason sat in a dict nobody prints. The banner is the human's
channel, so the line carries the outcome whenever it is anything but `persisted`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"
LIB = HOOKS.parent / "lib"

USAGE = {"tokens_used": 1200, "tokens_available": 98800, "utilization_pct": 1.2}


@pytest.fixture(scope="module")
def session_init():
    sys.path.insert(0, str(LIB))
    sys.path.insert(0, str(HOOKS))
    spec = importlib.util.spec_from_file_location("session_init_banner_budget", HOOKS / "session-init.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


def test_a_persisted_budget_keeps_the_plain_line(session_init):
    line = session_init._budget_banner_line({**USAGE, "persist": "persisted"})
    assert line == "\nBudget: 1,200t used / 98,800t avail (1.2%)"


def test_a_skipped_persist_is_named_with_its_reason(session_init):
    line = session_init._budget_banner_line({**USAGE, "persist": "skipped: session started from a compact in /work/x"})
    assert line.startswith("\nBudget: 1,200t used / 98,800t avail (1.2%)")
    assert "not saved" in line and "session started from a compact in /work/x" in line


def test_a_failed_persist_is_named(session_init):
    line = session_init._budget_banner_line({**USAGE, "persist": "failed: see hook log"})
    assert "not saved" in line and "failed: see hook log" in line


def test_a_budget_that_could_not_even_initialise_says_so_instead_of_vanishing(session_init):
    line = session_init._budget_banner_line({"error": "No module named 'empirica.core.context_budget'"})
    assert line.startswith("\nBudget: unavailable") and "No module named" in line


def test_nothing_to_report_is_empty_not_a_fabricated_zero_line(session_init):
    assert session_init._budget_banner_line(None) == ""
    assert session_init._budget_banner_line({}) == ""


def test_a_summary_without_a_persist_key_is_not_called_unsaved(session_init):
    """Positive control for the refusal above: absence of the key is an older summary, not a failure."""
    assert "not saved" not in session_init._budget_banner_line(USAGE)


def test_a_long_reason_stays_one_line(session_init):
    line = session_init._budget_banner_line({**USAGE, "persist": "skipped: " + "x" * 400 + "\nsecond line"})
    assert line.count("\n") == 1 and len(line) < 400
