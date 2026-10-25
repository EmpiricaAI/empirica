"""The package supports Python 3.10 (pyproject requires-python >=3.10, pyright pythonVersion 3.10), so it must not import names that arrived later.

`datetime.UTC` is 3.11+. wake_service.py used it; on 3.10 the import fails, the listener's wake-service start is wrapped in a try so it
would have degraded quietly, and CI on 3.10 would have failed. A whole-project pyright run caught it ("UTC" is unknown import symbol); a
per-file run earlier did not. This guard checks the one name that has already bitten, by source text, so it fails on any interpreter.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "empirica"
# `from datetime import ..., UTC, ...` and `datetime.UTC`: both need 3.11. `timezone.utc` is the 3.10-safe spelling.
LATE_UTC = re.compile(r"from\s+datetime\s+import\s+[^\n]*\bUTC\b|\bdatetime\.UTC\b")


def test_nothing_in_the_package_imports_datetime_utc():
    offenders = [
        str(p.relative_to(PACKAGE.parent))
        for p in PACKAGE.rglob("*.py")
        if LATE_UTC.search(p.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert offenders == [], "datetime.UTC needs Python 3.11; use datetime.timezone.utc"


def test_the_pattern_matches_what_it_is_meant_to_catch():
    """Positive control: a pattern that matched nothing would also pass the test above."""
    assert LATE_UTC.search("from datetime import UTC, datetime")
    assert LATE_UTC.search("x = datetime.UTC")
    assert not LATE_UTC.search("from datetime import datetime, timezone")
    assert not LATE_UTC.search("datetime.now(timezone.utc)")


# ── f-string syntax that only parses on 3.12+ (PEP 701) ──────────────────────
#
# Before 3.12 a quote that matches the f-string's own quote, or a backslash, inside a replacement field is a SyntaxError. `ruff format`
# targets py310 yet parses with the newest grammar, and rewrote an escaped quote into a same-quote nesting; `ruff check` passed; the
# suite passed on 3.14; CI on 3.11 failed to even import scripts/release.py (1.14.14 follow-up, 2026-10-25).


def _pep701_only_fstrings(source: str) -> list[int]:
    """Line numbers of f-strings whose replacement fields need Python 3.12+ to parse."""
    import ast

    bad = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.JoinedStr):
            continue
        literal = ast.get_source_segment(source, node) or ""
        body = literal.lstrip("fFrRbBuU")
        if body[:3] in ('"""', "'''"):
            quote = None  # triple-quoted: a single quote inside a field is legal before 3.12
        else:
            quote = body[:1]
        for value in node.values:
            if not isinstance(value, ast.FormattedValue):
                continue
            expr = ast.get_source_segment(source, value.value) or ""
            if "\\" in expr or (quote and quote in expr and body.count(quote) <= 2 + 2 * expr.count(quote)):
                bad.append(value.lineno)
    return sorted(set(bad))


# Sources that only PARSE on 3.12+: the guard's positive controls. Below 3.12 the interpreter rejects them itself, which is the signal
# the guard exists to give early, so feeding them to ast.parse there raises instead of testing anything.
PEP701_SOURCES = [
    'x = f"a{" and b" if y else ""}"\n',  # the line that shipped: a same-quote string inside a replacement field
    "x = f\"a{' and b\\'s' if y else ''}\"\n",  # a backslash inside a field
]


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="these sources are a SyntaxError below 3.12; that error is the signal"
)
@pytest.mark.parametrize("source", PEP701_SOURCES)
def test_the_f_string_guard_catches_the_syntax_that_broke_release_py_on_3_11(source):
    """Positive control, built from the line that shipped. A guard that flagged nothing would also pass the tree-wide test below."""
    assert _pep701_only_fstrings(source) == [1]


@pytest.mark.parametrize(
    "source",
    [
        "x = f\"a{' and b' if y else ''}\"\n",  # a different quote is legal before 3.12
        'x = f"""a{d["k"]}"""\n',  # a triple-quoted f-string may hold a single quote
        'x = f"a{d}"\n',
    ],
)
def test_the_f_string_guard_leaves_syntax_that_is_valid_everywhere_alone(source):
    """Negative control, valid on every supported interpreter, so it runs on all of them."""
    assert _pep701_only_fstrings(source) == []


def test_no_python_file_uses_f_string_syntax_that_needs_python_3_12():
    root = Path(__file__).resolve().parents[1]
    files = [*root.joinpath("scripts").glob("*.py"), *PACKAGE.rglob("*.py"), *root.joinpath("tests").rglob("*.py")]
    assert len(files) > 500, "control: the scan walked the tree"
    offenders = {}
    for p in files:
        try:
            lines = _pep701_only_fstrings(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        if lines:
            offenders[str(p.relative_to(root))] = lines
    assert offenders == {}, f"f-strings that need Python 3.12: {offenders}"
