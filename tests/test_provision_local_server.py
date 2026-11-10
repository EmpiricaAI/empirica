"""Onboarding with no Cortex starts the local server (1.15.1, goal b4cdb5db; David's ruling 2026-11-10).

provision-practice is the onboarding act, so it is where the serve service gets installed when no Cortex is configured and
no local server answers. It says so, `--no-local-server` skips it, and plain `setup` never installs a background service.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import provision_practice_commands as pp


def _args(**kw):
    base = {"no_cortex": False, "no_local_server": False}
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def ran(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(pp, "_run", lambda cmd, cwd, dry: calls.append(cmd) or (True, ""))
    monkeypatch.setattr(pp, "_wait_for_local_server", lambda: True)
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
