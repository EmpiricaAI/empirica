"""project-embed reads everything it needs BEFORE closing its database.

bf0955f88 (2026-09-06) added a decisions/assumptions read below an existing
`db.close()`, so every run raised "Cannot operate on a closed database". The
failure was captured as a high-severity issue while project-bootstrap, which
runs the embed, still answered ok:true; it surfaced only when the release gate
refused to publish over it.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "empirica" / "cli" / "command_handlers" / "project_embed.py"


def _handler_body():
    tree = ast.parse(SRC.read_text())
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and any(
            isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_read_decisions_and_assumptions"
            for n in ast.walk(fn)
        ):
            return fn
    raise AssertionError("positive control: the handler that reads decisions was not found")


def test_no_read_takes_the_db_after_it_is_closed():
    fn = _handler_body()
    close_lines = [
        n.lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "close"
        and getattr(n.func.value, "id", "") == "db"
    ]
    assert close_lines, "positive control: the handler no longer closes db at all"
    first_close = min(close_lines)
    late = [
        (n.lineno, getattr(n.func, "id", getattr(n.func, "attr", "?")))
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and n.lineno > first_close
        and any(isinstance(a, ast.Name) and a.id == "db" for a in n.args)
    ]
    assert late == [], f"db is passed to a call after db.close() at line {first_close}: {late}"


def test_the_summary_names_every_category_it_counts():
    """It listed five of six memory categories: parts 2010, total 2132, the gap
    being `mistakes` (empirica-extension). A total must be checkable from its parts."""
    from empirica.cli.command_handlers.project_embed import _embed_summary

    parts = {"findings": 228, "unknowns": 51, "mistakes": 122, "dead_ends": 130, "lessons": 7, "snapshots": 1594}
    line = _embed_summary(52, 2132, parts, 274, 38)
    assert "mistakes: 122" in line and "decisions: 274" in line and "assumptions: 38" in line
    assert "not itemised" not in line


def test_a_total_its_parts_do_not_reach_says_so():
    from empirica.cli.command_handlers.project_embed import _embed_summary

    line = _embed_summary(1, 2132, {"findings": 2010}, 0, 0)
    assert "[+122 not itemised]" in line
