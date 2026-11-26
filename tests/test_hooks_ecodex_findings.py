"""ecodex's findings on core's 1.17.17 hooks (goal befb499f)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks"


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HOOKS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "cmd, free",
    [
        ("empirica source-add --title t --noetic --path f", True),
        ("empirica source-add --title t --noetic --path f --visibility local", True),
        ("empirica source-add --title t --noetic --path f --visibility shared", False),  # POSTs to Cortex
        ("empirica source-add --title t --path f --visibility=public", False),
        ("empirica source-add --title t --path f --vis shared", False),
        ("empirica finding-log --finding x --visibility shared", True),  # logging records locally; sync sends later
    ],
)
def test_a_shared_source_registration_is_judged(cmd, free):
    gate = _load("sentinel-gate")
    assert gate.empirica_command_free_in_discipline(cmd) is free


def test_the_nudge_survives_a_null_prompt():
    tracker = _load("context-shift-tracker")
    assert "no active transaction" in tracker._build_no_transaction_nudge({"prompt": None})


def test_the_hook_survives_invalid_json(tmp_path):
    out = subprocess.run(
        [sys.executable, str(HOOKS / "context-shift-tracker.py")],
        input="{not json",
        capture_output=True,
        text=True,
        timeout=60,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert "Traceback" not in out.stderr
