"""The package supports Python 3.10 (pyproject requires-python >=3.10, pyright pythonVersion 3.10), so it must not import names that arrived later.

`datetime.UTC` is 3.11+. wake_service.py used it; on 3.10 the import fails, the listener's wake-service start is wrapped in a try so it
would have degraded quietly, and CI on 3.10 would have failed. A whole-project pyright run caught it ("UTC" is unknown import symbol); a
per-file run earlier did not. This guard checks the one name that has already bitten, by source text, so it fails on any interpreter.
"""

from __future__ import annotations

import re
from pathlib import Path

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
