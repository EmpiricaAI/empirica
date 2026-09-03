"""Regression tests: CLI import must not eagerly load heavy optional deps.

Diagnosed 2026-06-17: `import empirica.cli` cost ~1.0s on Windows while
`import empirica` alone cost ~70ms. An `-X importtime` trace pinned the gap
to two heavy *leaf* dependencies pulled in at module-import time, even for
trivial commands like `goals-list` that never touch them:

  - httpx (~190ms) — imported by ``empirica.cli.asyncio_fix`` whose
    ``patch_asyncio_for_mcp()`` ran at CLI import (cli_core.py) purely to
    monkey-patch ``httpx.AsyncClient.__del__`` for MCP server cleanup.
  - GitPython ``git`` (~140ms) — imported at module top in
    ``empirica.core.git_ops.signed_operations`` via the canonical git-notes
    chain that ``command_handlers`` re-exports.

Both are now imported lazily (only when a command actually needs them).
These tests lock in the invariant so the regression can't silently return.

The check runs in a fresh subprocess so prior imports in the test session
can't mask a stray eager import.
"""

from __future__ import annotations

import subprocess
import sys

# Heavy optional deps that must NOT be pulled in just by importing the CLI.
_FORBIDDEN_AT_CLI_IMPORT = ("httpx", "git")


#: Generous, because this is a cold interpreter start on a possibly-loaded box and
#: the number is not what the test is about. One retry on top, since transient
#: contention is legitimately retryable and a real eager import is not.
_IMPORT_TIMEOUT_S = 300


def _modules_after(import_target: str) -> set[str]:
    """`sys.modules` keys after importing `import_target` in a fresh interpreter.

    **A HARNESS failure must never render as a regression in the code under test.**

    These two tests failed twice inside a concurrent full-suite run and blocked a
    release cut. The import was never eager — verified nine times, standalone and
    under load — so what failed was the measurement: a subprocess that could not
    finish inside the timeout while two ~6,900-test suites competed for the box.

    The old harness let that surface through assertions worded *"empirica.cli
    eagerly imported httpx"*, so a resource failure accused the code of a
    regression it had not made. That is the instrument-indistinguishable-from-the-
    subject shape: the reader cannot tell "we could not measure" from "we measured
    and it is broken", and only one of those is about the code.

    So: retry once, then fail with a message that names the HARNESS and says the
    result is inconclusive. Not a skip — a skip would let a genuine regression hide
    behind a loaded machine, which is the opposite error and the more expensive one.
    """
    import json

    code = f"import {import_target}\nimport sys, json\nprint(json.dumps(sorted(sys.modules)))\n"
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=_IMPORT_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as e:
            last_exc = e
            continue
        if proc.returncode != 0:
            raise AssertionError(
                f"HARNESS FAILURE (attempt {attempt}), not an eager-import regression: the "
                f"subprocess importing {import_target} exited {proc.returncode}. This says "
                f"nothing about whether the import is lazy.\nstderr:\n{proc.stderr[-2000:]}"
            )
        try:
            return set(json.loads(proc.stdout.strip().splitlines()[-1]))
        except (ValueError, IndexError) as e:
            raise AssertionError(
                f"HARNESS FAILURE, not an eager-import regression: could not parse the module "
                f"list for {import_target} ({e}).\nstdout tail:\n{proc.stdout[-800:]}"
            ) from None

    raise AssertionError(
        f"HARNESS FAILURE, not an eager-import regression: importing {import_target} in a "
        f"fresh interpreter exceeded {_IMPORT_TIMEOUT_S}s on BOTH attempts, so the lazy-import "
        f"invariant could not be measured. This is a loaded machine, not a code change — "
        f"re-run when the box is quiet. ({last_exc})"
    )


def test_cli_import_does_not_pull_httpx():
    """httpx is ~190ms and only needed for MCP/cloud paths, not the CLI core."""
    loaded = _modules_after("empirica.cli")
    assert "httpx" not in loaded, (
        "empirica.cli eagerly imported httpx — the asyncio_fix httpx "
        "monkey-patch must stay lazy (only patch when httpx is already loaded)."
    )


def test_cli_import_does_not_pull_gitpython():
    """GitPython is ~140ms and only needed for git-notes write paths."""
    loaded = _modules_after("empirica.cli")
    assert "git" not in loaded, (
        "empirica.cli eagerly imported GitPython ('git') — signed_operations must import it lazily inside its methods."
    )


def test_signed_operations_import_is_light():
    """Importing the git-notes module itself must not drag in GitPython."""
    loaded = _modules_after("empirica.core.git_ops.signed_operations")
    assert "git" not in loaded, "signed_operations eagerly imported GitPython at module top."


def test_gitpython_still_usable_when_needed():
    """Lazy loading must not break actual git operations: the module-level
    names still resolve GitPython on demand."""
    code = (
        "from empirica.core.git_ops import signed_operations as so\n"
        "assert so.GIT_PYTHON_AVAILABLE is True, 'GitPython should resolve lazily'\n"
        "assert so.GitRepo is not None\n"
        "import sys; assert 'git' in sys.modules, 'access should have loaded git'\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout
