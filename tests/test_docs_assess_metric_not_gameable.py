"""The `tech_docs` coverage metric must measure documentation, not name-dropping.

Reported by empirica-workspace (2026-07-26) with before/after evidence: adding 137
accurate, code-derived docstrings moved coverage 0% (stayed 24.9%), while generating a
markdown file that merely LISTED 256 feature names took it to 100%. The check was a
substring match against concatenated markdown, so it was satisfiable by dumping class
names into a .md and unmovable by real documentation — inverting the EU AI Act Art. 11
/ ISO 7.5 intent the check is framed against, and rewarding exactly the behaviour the
docs-match-reality discipline exists to prevent.
"""

from __future__ import annotations

import inspect

import pytest

from empirica.cli.command_handlers import docs_commands as dc


@pytest.fixture
def assessor():
    cls = next(o for _n, o in inspect.getmembers(dc, inspect.isclass) if hasattr(o, "_check_if_documented"))
    return cls()


# ── the gaming vector ─────────────────────────────────────────────────


def test_a_generated_name_index_does_not_count_as_documentation(assessor):
    """workspace's exact vector: a file that lists names and nothing else."""
    gamed = "\n".join(f"- FakeThing{i}" for i in range(50)).lower()
    assert assessor._check_if_documented("FakeThing7", gamed) is False


def test_a_table_row_of_names_does_not_count(assessor):
    gamed = "| FakeThing7 | core |\n| FakeThing8 | api |\n".lower()
    assert assessor._check_if_documented("FakeThing7", gamed) is False


def test_a_bare_heading_does_not_count(assessor):
    assert assessor._check_if_documented("FakeThing7", "## fakething7\n\n".lower()) is False


# ── what SHOULD count ─────────────────────────────────────────────────


def test_real_prose_about_the_symbol_counts(assessor):
    """Practices that document in markdown rather than docstrings are not penalised."""
    prose = (
        "The FakeThing7 component resolves inbound routing decisions and owns the retry policy for the dispatch bus."
    ).lower()
    assert assessor._check_if_documented("FakeThing7", prose) is True


def test_a_symbol_with_a_docstring_counts_without_any_markdown(assessor, monkeypatch):
    """The core inversion: real docstrings must MOVE the metric. Previously they
    could not, because the check never consulted the AST scan sitting next to it."""
    monkeypatch.setattr(
        type(assessor), "check_docstrings", lambda self: {"documented_symbols": ["WellDocumented"]}, raising=False
    )
    assessor._documented_symbols_cache = None
    assert assessor._check_if_documented("WellDocumented", "") is True
    assert assessor._check_if_documented("Undocumented", "") is False


# ── the inventory the fix depends on ──────────────────────────────────


def test_check_docstrings_reports_the_symbols_it_counted(assessor):
    """`_documented_symbols` is only as good as this inventory — if the walk counts
    N documented items it must be able to name them, or the docstring half of the
    metric silently does nothing (scoring docstring-documented practices at zero)."""
    result = assessor.check_docstrings()
    symbols = result.get("documented_symbols")
    assert symbols is not None, "check_docstrings must expose documented_symbols"
    assert len(symbols) == result["documented_items"], "every counted item must be named"


# ── resolver robustness ───────────────────────────────────────────────


def test_a_project_root_that_no_longer_exists_does_not_crash(tmp_path):
    """Resolver state (active_work / instance_projects / registry) outlives the
    directory it names — a project that was deleted, moved, or lived on an unmounted
    volume. A stale pointer must degrade, not take the command down.

    Live instance: a pointer to a project deleted earlier the same day made
    `iterdir()` raise FileNotFoundError and bricked `docs-assess` entirely."""
    gone = tmp_path / "deleted-project"  # never created

    config = dc._auto_detect_project_config(gone)

    assert config is not None
    assert config.package_dirs == [] or isinstance(config.package_dirs, list)
