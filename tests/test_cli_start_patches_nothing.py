"""The CLI start installs no global asyncio or warnings patch (goal 95c3ee16).

cli/asyncio_fix.py, written for the removed MCP server, ran on every CLI start: global warning filters (including
one hiding "coroutine was never awaited"), an event loop and an atexit hook. Nothing in the package needs it.
"""

from __future__ import annotations

import subprocess
import sys


def test_importing_the_cli_adds_no_warning_filters_and_no_event_loop():
    code = (
        "import warnings, asyncio, sys\n"
        "before = list(warnings.filters)\n"
        "import empirica.cli.cli_core\n"
        "added = [f for f in warnings.filters if f not in before]\n"
        "print(len(added))\n"
        "print(int('empirica.cli.asyncio_fix' in sys.modules))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=True)
    added, patched = out.stdout.split()
    assert patched == "0", "empirica.cli.asyncio_fix is imported at CLI start"
    assert added == "0", f"importing the CLI added {added} global warning filter(s)"
