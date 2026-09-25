"""Hooks run the same interpreter as the CLI, so they import what the CLI imports.

setup-claude-code wrote a bare `python3` into every hook command. A pipx or
Homebrew install puts empirica only in its own interpreter, so there the hooks
could not import empirica and sentinel-gate allowed every tool call, silently.
Reproduced 2026-09-25 in a clean environment: permissionDecision allow, reason
"Cannot import path_resolver", suppressOutput true.
"""

from __future__ import annotations

import sys

from empirica.cli.command_handlers import setup_claude_code as sc


def test_setup_chooses_the_interpreter_running_the_cli():
    assert sc._hook_interpreter().strip('"') == sc._stable_interpreter_path(sys.executable)


def test_a_homebrew_cellar_path_becomes_the_stable_opt_path(tmp_path):
    cellar = tmp_path / "Cellar" / "empirica" / "1.14.2" / "libexec" / "bin" / "python"
    opt = tmp_path / "opt" / "empirica" / "libexec" / "bin" / "python"
    opt.parent.mkdir(parents=True)
    opt.write_text("")
    assert sc._stable_interpreter_path(str(cellar)) == str(opt)


def test_a_non_homebrew_path_is_kept(tmp_path):
    venv = str(tmp_path / "venvs" / "empirica" / "bin" / "python")
    assert sc._stable_interpreter_path(venv) == venv


def _settings(plugin_dir):
    return {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Edit|Write", "hooks": [{"command": f"python3 {plugin_dir}/hooks/sentinel-gate.py"}]},
                {"matcher": "Bash", "hooks": [{"command": "python3 /somewhere/else/their-own-hook.py"}]},
            ],
            "SessionEnd": [
                {"hooks": [{"command": f"python3 {plugin_dir}/hooks/curate-snapshots.py --output json"}]},
            ],
        },
        "statusLine": {"type": "command", "command": f"python3 {plugin_dir}/scripts/statusline_empirica.py"},
    }


def test_existing_seats_are_repaired_in_place(tmp_path):
    plugin_dir = (tmp_path / "plugins" / "empirica").as_posix()
    settings = _settings(plugin_dir)
    py = "/opt/venvs/empirica/bin/python"

    changed = sc._repair_hook_interpreters(settings, tmp_path / "plugins" / "empirica", py, "json")

    assert changed == 3
    pre = settings["hooks"]["PreToolUse"]
    assert pre[0]["hooks"][0]["command"] == f"{py} {plugin_dir}/hooks/sentinel-gate.py"
    assert pre[1]["hooks"][0]["command"] == "python3 /somewhere/else/their-own-hook.py", "someone else's hook"
    end = settings["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
    assert end == f"{py} {plugin_dir}/hooks/curate-snapshots.py --output json", "arguments are kept"
    assert settings["statusLine"]["command"] == f"{py} {plugin_dir}/scripts/statusline_empirica.py"


def test_the_repair_is_idempotent(tmp_path):
    plugin_dir = tmp_path / "plugins" / "empirica"
    settings = _settings(plugin_dir.as_posix())
    sc._repair_hook_interpreters(settings, plugin_dir, "/opt/py", "json")
    assert sc._repair_hook_interpreters(settings, plugin_dir, "/opt/py", "json") == 0


# ─── doctor asks the interpreter the hooks actually run ─────────────────────


def _settings_with_sentinel(tmp_path, interpreter):
    import json

    f = tmp_path / "settings.json"
    cmd = f"{interpreter} /plugins/empirica/hooks/sentinel-gate.py"
    f.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [{"command": cmd}]}]}}))
    return f


def test_doctor_fails_when_the_hook_interpreter_cannot_import_empirica(tmp_path, monkeypatch):
    from empirica.cli.command_handlers import doctor

    monkeypatch.setenv("HOME", str(tmp_path))
    fake = tmp_path / "python-without-empirica"
    fake.write_text("#!/bin/sh\necho \"ModuleNotFoundError: No module named 'empirica'\" >&2\nexit 1\n")
    fake.chmod(0o755)
    check = doctor.check_hook_interpreter(_settings_with_sentinel(tmp_path, fake))
    assert check.status == doctor.FAIL
    assert "allowing every tool call" in check.detail


def test_doctor_passes_for_an_interpreter_that_has_it(tmp_path, monkeypatch):
    """Positive control: the check is not simply always failing."""
    from empirica.cli.command_handlers import doctor

    monkeypatch.setenv("HOME", str(tmp_path))
    check = doctor.check_hook_interpreter(_settings_with_sentinel(tmp_path, sys.executable))
    assert check.status == doctor.PASS


def test_doctor_warns_while_the_gate_reports_itself_unable(tmp_path, monkeypatch):
    from empirica.cli.command_handlers import doctor

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "sentinel_unavailable.json").write_text("{}")
    check = doctor.check_hook_interpreter(_settings_with_sentinel(tmp_path, sys.executable))
    assert check.status == doctor.WARN
