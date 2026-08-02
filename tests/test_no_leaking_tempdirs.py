"""No test may create a temp dir nothing removes.

Reported by empirica-cortex, who lost an hour to it: their suite threw 5
failures + 3 errors in `test_canonical_guardians` — code they had not touched.
The cause was ENOSPC. /tmp was a 24G tmpfs at 100%, holding 39,686 stranded
test DBs, 1,046 of them ours. Cleared it, re-ran the identical tree, 4366/4366
green with no code change.

That is the expensive property and the reason this guard exists: **a temp-dir
leak presents as unrelated tests failing**, so the natural response is to debug
innocent code. The signal points nowhere near the cause.

`tempfile.mkdtemp()` returns a path and takes no responsibility for it — there
is no context manager, no teardown, nothing. pytest's `tmp_path` /
`tmp_path_factory` are removed automatically, and are already the convention
everywhere else in this suite.
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# mkdtemp inside a `with` is fine — that one cleans up after itself.
_MKDTEMP = re.compile(r"(?<!with )tempfile\.mkdtemp\(")


def test_no_test_file_calls_mkdtemp():
    offenders = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue  # a comment explaining the fix is not a violation
            if _MKDTEMP.search(line):
                offenders.append(f"{path.relative_to(TESTS_DIR.parent)}:{lineno}")

    assert not offenders, (
        "These create temp dirs nothing removes — use pytest's tmp_path "
        "(or tmp_path_factory for module scope):\n  " + "\n  ".join(offenders)
    )
