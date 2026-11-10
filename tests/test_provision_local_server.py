"""Onboarding with no Cortex starts the local server (1.15.1, goal b4cdb5db; David's ruling 2026-11-10).

provision-practice is the onboarding act, so it is where the serve service gets installed when no Cortex is configured and
no local server answers. It says so, `--no-local-server` skips it, and plain `setup` never installs a background service.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import provision_practice_commands as pp

_REAL_API_CHECK = pp._api_extra_missing  # captured at import, before conftest's autouse stub


def _args(**kw):
    base = {"no_cortex": False, "no_local_server": False}
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def ran(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(pp, "_run", lambda cmd, cwd, dry: calls.append(cmd) or (True, ""))
    monkeypatch.setattr(pp, "_wait_for_local_server", lambda: True)
    monkeypatch.setattr(pp, "_api_extra_missing", lambda: None)  # never measure the box: its venv, its unit file
    monkeypatch.setattr(pp, "_serve_unit_installed", lambda: False)
    return calls


@pytest.mark.parametrize(
    "edition, installs, note",
    [(None, True, None), ("local", False, "already running"), ("cortex", False, "Cortex is configured")],
)
def test_the_service_is_installed_only_with_no_cortex_and_nothing_running(
    ran, monkeypatch, tmp_path, edition, installs, note
):
    monkeypatch.setattr(pp, "_mesh_edition", lambda: edition)
    step = pp._local_server_step(_args(), tmp_path, dry_run=False)
    assert step["step"] == "local-server"
    assert (["empirica", "serve-service", "install"] in ran) is installs
    if note:
        assert note in step["note"]


@pytest.mark.parametrize("flags", [{"no_local_server": True}, {"no_cortex": True}])
def test_the_opt_outs_skip_it(ran, monkeypatch, tmp_path, flags):
    monkeypatch.setattr(pp, "_mesh_edition", lambda: None)
    step = pp._local_server_step(_args(**flags), tmp_path, dry_run=False)
    assert ran == [] and step["changed"] is False


def test_a_failed_install_is_reported_not_hidden(monkeypatch, tmp_path):
    monkeypatch.setattr(pp, "_mesh_edition", lambda: None)
    monkeypatch.setattr(pp, "_run", lambda cmd, cwd, dry: (False, "no systemd user manager"))
    step = pp._local_server_step(_args(), tmp_path, dry_run=False)
    assert step["changed"] is False and "no systemd user manager" in step["error"]
    assert "empirica serve" in step["hint"]


def test_the_parser_has_the_opt_out():
    from empirica.cli.cli_core import create_argument_parser

    args = create_argument_parser().parse_args(["provision-practice", "demo", "--no-local-server"])
    assert args.no_local_server is True


# ── 1.15.1 sweep, third reviewer ─────────────────────────────────────────────────────────────────────────────────────


def test_without_the_api_extra_nothing_is_installed_and_the_step_says_why(ran, monkeypatch, tmp_path):
    """Without fastapi/uvicorn the unit's `empirica serve` exits 1 and restarts until systemd gives up, while the step
    reported ok. Refuse before installing, and name the extra."""
    monkeypatch.setattr(pp, "_mesh_edition", lambda: None)
    monkeypatch.setattr(pp, "_api_extra_missing", lambda: "No module named 'uvicorn'")
    monkeypatch.setattr(pp, "_serve_unit_installed", lambda: False)
    step = pp._local_server_step(_args(), tmp_path, dry_run=False)
    assert ran == [] and step["error"] and "empirica[api]" in step["hint"]


def test_the_service_is_installed_from_home_not_from_the_practice(monkeypatch, tmp_path):
    """serve binds its project from WorkingDirectory: installing from the practice bound the machine's one serve unit
    (the extension's daemon too) to whichever practice was provisioned last."""
    cwds = []
    monkeypatch.setattr(pp, "_run", lambda cmd, cwd, dry: cwds.append(cwd) or (True, ""))
    monkeypatch.setattr(pp, "_wait_for_local_server", lambda: True)
    monkeypatch.setattr(pp, "_mesh_edition", lambda: None)
    monkeypatch.setattr(pp, "_api_extra_missing", lambda: None)
    monkeypatch.setattr(pp, "_serve_unit_installed", lambda: False)
    monkeypatch.setattr(pp.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    pp._local_server_step(_args(), tmp_path / "practice", dry_run=False)
    assert cwds == [tmp_path / "home"]


def test_an_installed_unit_that_does_not_answer_is_reported_not_rewritten(ran, monkeypatch, tmp_path):
    """An older serve still running, a failed unit, or another process on the port: each provision rewrote the unit (and
    its one .bak). Leave it, and say what to check."""
    monkeypatch.setattr(pp, "_mesh_edition", lambda: None)
    monkeypatch.setattr(pp, "_api_extra_missing", lambda: None)
    monkeypatch.setattr(pp, "_serve_unit_installed", lambda: True)
    step = pp._local_server_step(_args(), tmp_path, dry_run=False)
    assert ran == [] and step["error"] and "serve-service status" in step["hint"]


def test_a_server_that_never_answers_after_install_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(pp, "_run", lambda cmd, cwd, dry: (True, ""))
    monkeypatch.setattr(pp, "_wait_for_local_server", lambda: False)
    monkeypatch.setattr(pp, "_mesh_edition", lambda: None)
    monkeypatch.setattr(pp, "_api_extra_missing", lambda: None)
    monkeypatch.setattr(pp, "_serve_unit_installed", lambda: False)
    step = pp._local_server_step(_args(), tmp_path, dry_run=False)
    assert step["changed"] is True and step["error"] and "serve-service status" in step["hint"]


def test_the_api_check_reads_the_interpreter_of_the_binary_the_unit_runs(monkeypatch, tmp_path):
    """The unit runs `shutil.which('empirica')`, which may be another venv (pipx) than the one provisioning."""
    py = tmp_path / "python3"
    py.write_text("")
    exe = tmp_path / "empirica"
    exe.write_text(f"#!{py}\nimport sys\n")
    seen = []
    monkeypatch.setattr(pp.shutil, "which", lambda name: str(exe))
    monkeypatch.setattr(
        pp.subprocess,
        "run",
        lambda cmd, **kw: seen.append(cmd) or SimpleNamespace(returncode=1, stderr="ModuleNotFoundError: uvicorn"),
    )
    assert "uvicorn" in (_REAL_API_CHECK() or "")
    assert seen and seen[0][0] == str(py)


# ── 1.15.1 second pass ────────────────────────────────────────────────────────────────────────────────────────────────


def _launcher(tmp_path, name, text):
    f = tmp_path / name
    f.write_text(text)
    return str(f)


def test_the_launcher_python_is_read_from_each_shape_and_unknown_ones_are_none(tmp_path):
    """D1: a `#!/bin/sh` launcher ran `/bin/sh -c "import fastapi, uvicorn"` (ImageMagick's `import` on one box, a 30 s
    hang), a bash shim ran bash, `env -S` ran `-S`: each reported a working install as missing the extra."""
    py = "/opt/my venv/bin/python3.12"
    assert pp._launcher_python(_launcher(tmp_path, "a", "#!/usr/bin/python3\n")) == "/usr/bin/python3"
    assert pp._launcher_python(_launcher(tmp_path, "b", "#!/usr/bin/env python3\n")) == "python3"
    sh = f"#!/bin/sh\n'''exec' \"{py}\" \"$0\" \"$@\"\n' '''\n"
    assert pp._launcher_python(_launcher(tmp_path, "c", sh)) == py
    assert pp._launcher_python(_launcher(tmp_path, "d", "#!/usr/bin/env bash\nexec pyenv ...\n")) is None
    assert pp._launcher_python(_launcher(tmp_path, "e", "#!/usr/bin/env -S python3 -X utf8\n")) is None
    assert pp._launcher_python(_launcher(tmp_path, "f", "MZ\x90\x00binary")) is None


def test_an_unrecognised_launcher_skips_the_check_instead_of_blocking(monkeypatch, tmp_path):
    exe = _launcher(tmp_path, "empirica", "#!/usr/bin/env bash\n")
    ran = []
    monkeypatch.setattr(pp.shutil, "which", lambda name: exe)
    monkeypatch.setattr(pp.subprocess, "run", lambda cmd, **kw: ran.append(cmd))
    assert _REAL_API_CHECK() is None and ran == []


def test_the_installed_but_silent_hint_names_no_platform_tool(ran, monkeypatch, tmp_path):
    """D2: the hint said `systemctl --user restart`, which a macOS launchd install has no use for."""
    monkeypatch.setattr(pp, "_mesh_edition", lambda: None)
    monkeypatch.setattr(pp, "_serve_unit_installed", lambda: True)
    step = pp._local_server_step(_args(), tmp_path, dry_run=False)
    assert "systemctl" not in step["hint"] and "--no-local-server" in step["hint"]
