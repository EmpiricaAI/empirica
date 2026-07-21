"""Statusline context-usage meter (ecodex/David request).

Claude Code passes context usage into the statusline input as
``context_window.used_percentage``; `format_context_window` renders it as a
bracketed-cell meter that fills as context is consumed — e.g. `[####------] 42%`.
Empty string when the harness supplies no usage (older CC / other harnesses).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_SL = _ROOT / "empirica" / "plugins" / "claude-code-integration" / "scripts" / "statusline_empirica.py"
_LIB = _ROOT / "empirica" / "plugins" / "claude-code-integration" / "lib"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _load():
    if str(_LIB) not in sys.path:
        sys.path.insert(0, str(_LIB))
    spec = importlib.util.spec_from_file_location("statusline_empirica", _SL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _meter(sl, pct) -> str:
    return _ANSI.sub("", sl.format_context_window({"context_window": {"used_percentage": pct}}))


def test_meter_matches_spec(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sl = _load()
    assert _meter(sl, 42) == "[####------] 42%"


def test_meter_fills_and_caps(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sl = _load()
    assert _meter(sl, 80) == "[########--] 80%"
    assert _meter(sl, 100) == "[##########] 100%"


def test_meter_empty_without_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sl = _load()
    # No usage from the harness → render nothing (don't show a fake 0-bar).
    assert sl.format_context_window({}) == ""
    assert _meter(sl, 0) == ""
