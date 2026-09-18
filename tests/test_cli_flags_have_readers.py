"""Every CLI flag the parser accepts has a reader in some handler module.

An accepted flag nothing consumes is an advertised no-op: it discards what the
user supplied, silently. The first run of scripts/cli_unread_flags.py found 24
such dests (a vision handler reading attributes its parser never defined, six
goals-list filters never applied, a monitor verb whose nine flags fed a
deprecation notice). This keeps the count at zero.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("cli_unread_flags", REPO / "scripts" / "cli_unread_flags.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_flag_is_read_nowhere(probe):
    hits = probe.find_unread()
    assert hits == [], "\n".join(f"{verb} --{d.replace('_', '-')} ({mod})" for _, verb, d, mod in hits)


def test_positive_control_an_unread_flag_is_reported(probe, monkeypatch):
    """Without this the assertion above could pass because the probe sees nothing."""
    import argparse

    from empirica.cli import cli_core

    real = cli_core.create_argument_parser

    def with_a_dead_flag():
        parser = real()
        sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        sub.choices["goals-list"].add_argument("--zz-never-read-by-anything")
        return parser

    monkeypatch.setattr(cli_core, "create_argument_parser", with_a_dead_flag)
    hits = probe.find_unread()
    assert ("NOWHERE", "goals-list", "zz_never_read_by_anything", "goal_commands") in hits
