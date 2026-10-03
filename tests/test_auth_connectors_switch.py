"""`empirica auth connectors`: plan first, switch only what was named, never leak the token, never print a value.

Switching an MCP connector from a stored `Authorization` bearer to `headersHelper` makes the seat's OAuth
token travel to the connector's URL, so the safety rules are the feature: dry run by default, only hosts
that are ours, only with a valid OAuth token now, only the names given, a backup first, and no credential
value in any output. Built under tmp_path with HOME pinned; the machine's ~/.claude.json is never read.
"""

from __future__ import annotations

import json
import os
import stat
import time
import types

import pytest

from empirica.cli.command_handlers import auth_commands
from empirica.config.credentials_loader import CredentialsLoader
from empirica.core.auth.connector_switch import HELPER_COMMAND, apply_switches, host_is_ours, plan_switches

SECRET = "Bearer sk-live-SUPERSECRETVALUE0123456789"  # noqa: S105 - fake; asserted never to reach any output


def _cfg(url="https://crm.getempirica.com/mcp/", extra=None):
    headers = {"Authorization": SECRET, **(extra or {})}
    return {"type": "http", "url": url, "headers": headers}


@pytest.fixture
def seat(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    creds = tmp_path / "credentials.yaml"
    creds.write_text('version: "1.0"\ncortex:\n  url: https://cortex.getempirica.com\n')
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(creds))
    loader = CredentialsLoader()
    loader._credentials_cache = None
    monkeypatch.setattr(auth_commands, "_loader", lambda: loader)

    def write(config, mode=0o600):
        p = home / ".claude.json"
        p.write_text(json.dumps(config))
        p.chmod(mode)
        return p

    def valid_token():
        loader.save_cortex_oauth(
            access_token="LIVE",  # noqa: S106 - fake value
            refresh_token="RT",  # noqa: S106 - fake value
            expires_at=time.time() + 3600,
        )

    def run(*names, apply=False, output="human"):
        rc = auth_commands.handle_auth_connectors_command(
            types.SimpleNamespace(name=list(names), apply=apply, output=output)
        )
        return rc, capsys.readouterr()

    return types.SimpleNamespace(home=home, write=write, valid_token=valid_token, run=run, path=home / ".claude.json")


# ── the pure planner ─────────────────────────────────────────────────────────


def test_a_named_connector_on_our_host_with_a_valid_token_is_switched():
    rows = plan_switches({"mcpServers": {"crm": _cfg()}}, ["crm"], token_valid=True)

    assert rows == [{"name": "crm", "host": "crm.getempirica.com", "action": "switch", "why": None}]


@pytest.mark.parametrize(
    ("url", "ours"),
    [
        ("https://cortex.getempirica.com/mcp/", True),
        ("https://getempirica.com/x", True),
        ("https://evil.example/mcp/", False),
        ("https://getempirica.com.evil.example/mcp/", False),
        ("https://notgetempirica.com/mcp/", False),
        ("not a url", False),
        (None, False),
    ],
)
def test_only_our_hosts_qualify_and_lookalikes_do_not(url, ours):
    assert host_is_ours(url) is ours


def test_the_seats_own_cortex_host_qualifies_even_outside_the_domain():
    assert host_is_ours("https://cortex.internal.example/mcp/", "https://cortex.internal.example") is True


def test_a_foreign_host_is_left_alone_even_when_named():
    rows = plan_switches({"mcpServers": {"x": _cfg("https://evil.example/mcp/")}}, ["x"], token_valid=True)

    assert rows[0]["action"] == "leave" and "never presented elsewhere" in rows[0]["why"]


def test_no_valid_token_means_nothing_is_switched():
    rows = plan_switches({"mcpServers": {"crm": _cfg()}}, ["crm"], token_valid=False)

    assert rows[0]["action"] == "leave" and "no valid OAuth token" in rows[0]["why"]


def test_an_unnamed_connector_is_listed_and_left():
    rows = plan_switches({"mcpServers": {"crm": _cfg()}}, [], token_valid=True)

    assert rows[0]["action"] == "leave" and "--name" in rows[0]["why"]


def test_a_connector_without_a_static_header_is_not_a_candidate():
    config = {"mcpServers": {"a": {"type": "http", "headersHelper": HELPER_COMMAND}, "b": {"type": "stdio"}}}

    assert plan_switches(config, ["a", "b"], token_valid=True) == []


def test_apply_removes_only_the_authorization_header_and_sets_the_helper():
    config = {"mcpServers": {"crm": _cfg(extra={"X-Trace": "keep"})}}
    rows = plan_switches(config, ["crm"], token_valid=True)

    assert apply_switches(config, rows) == 1

    assert config["mcpServers"]["crm"]["headers"] == {"X-Trace": "keep"}
    assert config["mcpServers"]["crm"]["headersHelper"] == HELPER_COMMAND


def test_apply_drops_an_emptied_headers_object_and_never_overwrites_an_existing_helper():
    config = {"mcpServers": {"crm": _cfg(), "own": {**_cfg(), "headersHelper": "my-own-helper"}}}
    rows = plan_switches(config, ["crm", "own"], token_valid=True)

    assert apply_switches(config, rows) == 1

    assert "headers" not in config["mcpServers"]["crm"]
    assert config["mcpServers"]["own"]["headersHelper"] == "my-own-helper"


# ── the command ──────────────────────────────────────────────────────────────


def test_a_dry_run_writes_nothing_and_prints_no_value(seat):
    p = seat.write({"mcpServers": {"crm": _cfg()}})
    before = p.read_text()
    seat.valid_token()

    rc, out = seat.run("crm")

    assert rc == 0 and "switch" in out.out and "dry run" in out.out
    assert p.read_text() == before and not list(seat.home.glob(".claude.json.bak-*"))
    assert SECRET not in out.out + out.err and "SUPERSECRET" not in out.out + out.err


def test_apply_backs_up_switches_keeps_the_mode_and_prints_no_value(seat):
    p = seat.write({"mcpServers": {"crm": _cfg()}, "other": {"keep": True}})
    seat.valid_token()

    rc, out = seat.run("crm", apply=True)

    assert rc == 0
    config = json.loads(p.read_text())
    assert (
        config["mcpServers"]["crm"]["headersHelper"] == HELPER_COMMAND and "headers" not in config["mcpServers"]["crm"]
    )
    assert config["other"] == {"keep": True}, "the rest of the file is untouched"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    backups = list(seat.home.glob(".claude.json.bak-*"))
    assert len(backups) == 1 and stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert "SUPERSECRET" in backups[0].read_text(), "the backup holds the old bearer, and the output says to delete it"
    assert "delete it" in out.out and "SUPERSECRET" not in out.out + out.err


def test_apply_without_a_valid_token_changes_nothing(seat):
    p = seat.write({"mcpServers": {"crm": _cfg()}})
    before = p.read_text()

    rc, out = seat.run("crm", apply=True)

    assert rc == 0 and "no valid OAuth token" in out.out and p.read_text() == before
    assert not list(seat.home.glob(".claude.json.bak-*"))


def test_apply_never_switches_a_connector_that_was_not_named(seat):
    p = seat.write({"mcpServers": {"crm": _cfg(), "other": _cfg("https://cortex.getempirica.com/mcp/")}})
    seat.valid_token()

    seat.run("crm", apply=True)

    config = json.loads(p.read_text())
    assert "headersHelper" in config["mcpServers"]["crm"] and "headersHelper" not in config["mcpServers"]["other"]


def test_a_name_with_no_static_header_is_reported_not_found(seat):
    seat.write({"mcpServers": {"crm": _cfg()}})
    seat.valid_token()

    rc, out = seat.run("typo", apply=True)

    assert rc == 0 and "not found: 'typo'" in out.out


def test_a_missing_or_unreadable_claude_json_is_reported_and_changes_nothing(seat):
    rc, out = seat.run("crm", apply=True)
    assert rc == 0 and "no user-scope connector" in out.out

    seat.path.write_text("{not json")
    rc, out = seat.run("crm", apply=True)
    assert rc == 1 and "could not be read" in out.err and seat.path.read_text() == "{not json"


def test_json_output_carries_the_plan_and_no_value(seat):
    seat.write({"mcpServers": {"crm": _cfg()}})
    seat.valid_token()

    rc, out = seat.run("crm", output="json")
    doc = json.loads(out.out)

    assert rc == 0 and doc["plan"][0]["action"] == "switch" and doc["applied"] is None and SECRET not in out.out


def test_a_symlinked_claude_json_is_written_through(seat, tmp_path):
    real = tmp_path / "dotfiles" / "claude.json"
    real.parent.mkdir()
    real.write_text(json.dumps({"mcpServers": {"crm": _cfg()}}))
    real.chmod(0o600)
    seat.path.symlink_to(real)
    seat.valid_token()

    seat.run("crm", apply=True)

    assert seat.path.is_symlink() and "headersHelper" in json.loads(real.read_text())["mcpServers"]["crm"]
    assert os.path.exists(real)
