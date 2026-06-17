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


def _modules_after(import_target: str) -> set[str]:
    """Return sys.modules keys after importing ``import_target`` in a fresh
    interpreter. Isolated so the parent test process can't contaminate it."""
    code = (
        f"import {import_target}\n"
        "import sys, json\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"importing {import_target} failed:\n{proc.stderr}"
    )
    import json

    return set(json.loads(proc.stdout.strip().splitlines()[-1]))


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
        "empirica.cli eagerly imported GitPython ('git') — "
        "signed_operations must import it lazily inside its methods."
    )


def test_signed_operations_import_is_light():
    """Importing the git-notes module itself must not drag in GitPython."""
    loaded = _modules_after("empirica.core.git_ops.signed_operations")
    assert "git" not in loaded, (
        "signed_operations eagerly imported GitPython at module top."
    )


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
