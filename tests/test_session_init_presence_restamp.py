"""_write_practitioner_presence in session-init.py — presence write + pid re-stamp.

Part 3 of the presence-liveness fix: on `claude --resume` the session runs under a
fresh CC parent, so the session_pid captured at first launch is now dead. The
resume path re-stamps presence via this helper (shared with new-session init),
keeping part 1's kill-0 pid-liveness accurate for resumed sessions.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

HOOK_PATH = (
    Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "session-init.py"
)
_spec = importlib.util.spec_from_file_location("session_init_presence_restamp", HOOK_PATH)
assert _spec is not None and _spec.loader is not None
session_init = importlib.util.module_from_spec(_spec)
sys.modules["session_init_presence_restamp"] = session_init
_spec.loader.exec_module(session_init)

_write = session_init._write_practitioner_presence


def test_writes_presence_with_current_getppid():
    with patch.object(session_init.subprocess, "run") as run:
        _write("cc-123", "empirica", "es-456")
    assert run.call_count == 1
    argv = run.call_args[0][0]
    assert argv[:3] == ["empirica", "practitioner", "write"]
    assert argv[argv.index("--session") + 1] == "cc-123"  # durable key
    assert argv[argv.index("--ai-id") + 1] == "empirica"
    assert argv[argv.index("--empirica-session") + 1] == "es-456"
    # stamps the CURRENT parent pid — the whole point on resume
    assert argv[argv.index("--session-pid") + 1] == str(os.getppid())


def test_empty_session_id_is_noop():
    with patch.object(session_init.subprocess, "run") as run:
        _write("", "empirica", "es-1")
    run.assert_not_called()


def test_never_raises_on_subprocess_error():
    with patch.object(session_init.subprocess, "run", side_effect=OSError("boom")):
        _write("cc-1", "empirica", "es-1")  # best-effort — must not raise
