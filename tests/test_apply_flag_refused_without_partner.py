"""`--apply` without the flag that reads it is refused, not ignored.

Four verbs in this binary use `--apply` as "dry-run by default, --apply to
write". `setup-claude-code --apply` and `doctor --apply` spell the same flag
but read it only under `--uninstall` / `--reconcile-notes`. Passed alone it
was accepted, parsed and silently dropped — and setup went on to write the
install a caller had asked to preview. Reported by outreach after a near-miss
on ~/.claude/CLAUDE.md (prop_soovcisfmra6jbg77b2h76blxq).

The guard has to fire BEFORE any write, so each test fences the write paths:
if the guard were missing, the fence would raise, not the assertion.
"""

from __future__ import annotations

import json
import types

import pytest

from empirica.cli.command_handlers import doctor as doctor_mod
from empirica.cli.command_handlers import setup_claude_code as setup_mod


def _setup_args(**overrides):
    defaults = {"apply": True, "uninstall": False, "output": "human", "harness": None, "force": False}
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


@pytest.fixture
def fenced_setup(monkeypatch):
    """Any path past the guard raises — the install and the uninstall alike."""

    def boom(*a, **k):
        raise AssertionError("write path reached: the --apply guard did not fire")

    monkeypatch.setattr(setup_mod, "_handle_uninstall", boom)
    monkeypatch.setattr(setup_mod, "resolve_harness", boom)


def test_setup_apply_without_uninstall_is_refused_before_any_write(fenced_setup, capsys):
    rc = setup_mod.handle_setup_claude_code_command(_setup_args())
    assert rc == 2
    err = capsys.readouterr().err
    assert "--apply only means something with --uninstall" in err
    assert "nothing was done" in err


def test_setup_refusal_is_json_when_asked(fenced_setup, capsys):
    rc = setup_mod.handle_setup_claude_code_command(_setup_args(output="json"))
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "apply_without_uninstall"


def test_setup_apply_with_uninstall_still_reaches_the_uninstall_path(monkeypatch):
    seen = []
    monkeypatch.setattr(setup_mod, "_handle_uninstall", lambda apply_it: seen.append(apply_it) or 0)
    rc = setup_mod.handle_setup_claude_code_command(_setup_args(uninstall=True))
    assert rc == 0
    assert seen == [True]


def _doctor_args(**overrides):
    defaults = {"apply": True, "reconcile_notes": False, "output": "human", "deploy_gaps": False}
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


@pytest.fixture
def fenced_doctor(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("report/repair path reached: the --apply guard did not fire")

    monkeypatch.setattr(doctor_mod, "_handle_reconcile_notes", boom)
    monkeypatch.setattr(doctor_mod, "run_all_checks", boom)
    monkeypatch.setattr(doctor_mod, "deploy_gap_checks", boom)


def test_doctor_apply_without_reconcile_notes_is_refused(fenced_doctor, capsys):
    rc = doctor_mod.handle_doctor_command(_doctor_args())
    assert rc == 2
    assert "--apply only means something with --reconcile-notes" in capsys.readouterr().err


def test_doctor_refusal_is_json_when_asked(fenced_doctor, capsys):
    rc = doctor_mod.handle_doctor_command(_doctor_args(output="json"))
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["error"] == "apply_without_reconcile_notes"


def test_doctor_apply_with_reconcile_notes_still_repairs(monkeypatch):
    seen = []
    monkeypatch.setattr(doctor_mod, "_handle_reconcile_notes", lambda cwd, apply_it: seen.append(apply_it) or 0)
    rc = doctor_mod.handle_doctor_command(_doctor_args(reconcile_notes=True))
    assert rc == 0
    assert seen == [True]


def test_help_text_names_the_partner_flag():
    """The usage line cannot express the coupling in argparse; the option help
    must, and it must say the flag is refused alone."""
    import subprocess
    import sys

    for verb, partner in (("setup-claude-code", "--uninstall"), ("doctor", "--reconcile-notes")):
        out = subprocess.run(
            [sys.executable, "-m", "empirica.cli", verb, "--help"], capture_output=True, text=True, check=False
        ).stdout
        # The usage line mentions --apply first; the option's own entry is the
        # indented "  --apply" row in the options block.
        block = " ".join(out.split("\n  --apply", 1)[1][:260].split())
        assert f"REQUIRES {partner}" in block, (verb, block)
        assert "Refused on its own" in block, (verb, block)
