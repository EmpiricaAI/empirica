"""Findings from the 1.14.6 broccoli sweep of `empirica auth connectors` and doctor's connector check.

Each was reproduced by an independent reviewer and re-checked against the code before the fix:
a lost update (the change-stamp was taken after the read), a wrong remedy and a stranded backup on a
conflict, an expiry test that disagreed with the helper's, an `http://` URL accepted as ours, a plan row
that said `switch` and then skipped, doctor crashing on a non-object `mcpServers`, and an
"environment reference" test that any `${` satisfied.
"""

from __future__ import annotations

import json
import time
import types

import pytest

from empirica.cli.command_handlers import auth_commands
from empirica.cli.command_handlers import setup_claude_code as sc
from empirica.cli.command_handlers.doctor import check_static_connector_headers
from empirica.config.credentials_loader import CredentialsLoader
from empirica.core.auth.connector_switch import HELPER_COMMAND, has_static_authorization, host_is_ours, plan_switches

SECRET = "Bearer sk-live-SUPERSECRETVALUE0123456789"  # noqa: S105 - fake; asserted never to reach any output


def _cfg(url="https://crm.getempirica.com/mcp/", **extra):
    return {"type": "http", "url": url, "headers": {"Authorization": SECRET}, **extra}


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
    path = home / ".claude.json"

    def write(config, mode=0o600):
        path.write_text(json.dumps(config))
        path.chmod(mode)

    def valid_token():
        loader.save_cortex_oauth(
            access_token="LIVE",  # noqa: S106 - fake value
            refresh_token="RT",  # noqa: S106 - fake value
            expires_at=time.time() + 3600,
        )

    def run(*names, apply=True):
        rc = auth_commands.handle_auth_connectors_command(
            types.SimpleNamespace(name=list(names), apply=apply, output="human")
        )
        return rc, capsys.readouterr()

    return types.SimpleNamespace(home=home, path=path, write=write, valid_token=valid_token, run=run, loader=loader)


def _backups(seat):
    return sorted(p for p in seat.home.iterdir() if ".bak-" in p.name)


# ── the lost update ─────────────────────────────────────────────────────────


def test_the_change_stamp_is_taken_before_the_read(tmp_path, monkeypatch):
    """A write that lands between the read and the stat must not be absorbed into the stamp."""
    p = tmp_path / "claude.json"
    p.write_text(json.dumps({"a": 1}))
    real = sc._ensure_json_file

    def read_then_foreign_write(path, default):
        data = real(path, default)
        path.write_text(json.dumps({"a": 1, "newProject": True}))  # Claude Code writing meanwhile
        return data

    monkeypatch.setattr(sc, "_ensure_json_file", read_then_foreign_write)
    data, stamp = sc._read_json_with_stamp(p, {})

    with pytest.raises(sc.ConcurrentlyModified):
        sc._write_json_file(p, {**data, "ours": 1}, expect_stamp=stamp)
    assert json.loads(p.read_text())["newProject"] is True


def test_a_conflicting_apply_keeps_the_file_and_says_what_to_do(seat, monkeypatch):
    seat.write({"mcpServers": {"crm": _cfg()}})
    seat.valid_token()
    import empirica.core.auth.connector_switch as cs

    real = cs.apply_switches

    def apply_then_foreign_write(config, rows):
        changed = real(config, rows)
        seat.path.write_text(json.dumps({"mcpServers": {"crm": _cfg()}, "newProject": True}))
        return changed

    monkeypatch.setattr(cs, "apply_switches", apply_then_foreign_write)

    rc, out = seat.run("crm")

    assert rc == 1
    assert "setup-claude-code" not in out.err
    assert "Re-run it" in out.err and "backup" in out.err
    on_disk = json.loads(seat.path.read_text())
    assert on_disk["newProject"] is True and "headers" in on_disk["mcpServers"]["crm"]
    assert len(_backups(seat)) == 1
    assert SECRET not in out.out + out.err


# ── the gates ───────────────────────────────────────────────────────────────


def test_a_token_expired_in_milliseconds_is_not_valid(seat, monkeypatch):
    seat.write({"mcpServers": {"crm": _cfg()}})
    ten_days_ago_ms = (time.time() - 864000) * 1000
    monkeypatch.setattr(seat.loader, "get_cortex_oauth", lambda: {"access_token": "T", "expires_at": ten_days_ago_ms})

    rc, out = seat.run("crm")

    assert rc == 0 and "no valid OAuth token" in out.out
    assert "headers" in json.loads(seat.path.read_text())["mcpServers"]["crm"]
    assert _backups(seat) == []


@pytest.mark.parametrize(
    "url",
    ["http://crm.getempirica.com/mcp/", "ftp://crm.getempirica.com/", "//crm.getempirica.com/mcp/"],
)
def test_only_https_urls_qualify_as_ours(url):
    assert host_is_ours(url) is False


def test_an_http_connector_is_left_alone_even_when_named(seat):
    seat.write({"mcpServers": {"crm": _cfg("http://crm.getempirica.com/mcp/")}})
    seat.valid_token()

    rc, out = seat.run("crm")

    assert rc == 0 and "https" in out.out
    assert "headers" in json.loads(seat.path.read_text())["mcpServers"]["crm"]
    assert _backups(seat) == []


def test_https_on_our_host_is_still_switched(seat):
    """Positive control for the scheme rule."""
    seat.write({"mcpServers": {"crm": _cfg()}})
    seat.valid_token()

    rc, _ = seat.run("crm")

    assert rc == 0
    assert json.loads(seat.path.read_text())["mcpServers"]["crm"]["headersHelper"] == HELPER_COMMAND


def test_a_connector_with_its_own_helper_is_planned_as_leave_not_switch():
    rows = plan_switches({"mcpServers": {"dual": _cfg(headersHelper="mine")}}, ["dual"], token_valid=True)

    assert rows[0]["action"] == "leave" and "headersHelper" in rows[0]["why"]


def test_applying_when_every_row_is_a_leave_writes_nothing_and_makes_no_backup(seat):
    seat.write({"mcpServers": {"dual": _cfg(headersHelper="mine")}})
    seat.valid_token()
    before = seat.path.read_bytes()

    rc, out = seat.run("dual")

    assert rc == 0 and "switched" not in out.out
    assert seat.path.read_bytes() == before and _backups(seat) == []


# ── the backup and the mode ─────────────────────────────────────────────────


def test_two_applies_in_one_second_keep_both_backups(seat, monkeypatch):
    seat.write({"mcpServers": {"a": _cfg(), "b": _cfg("https://cortex.getempirica.com/mcp/")}})
    seat.valid_token()
    monkeypatch.setattr(auth_commands.time, "strftime", lambda _fmt: "20261003-120000")

    assert seat.run("a")[0] == 0
    assert seat.run("b")[0] == 0

    backups = _backups(seat)
    assert len(backups) == 2
    first = json.loads(backups[0].read_text())
    assert "headers" in first["mcpServers"]["a"], "the first backup must still hold the original header"


def test_a_loose_file_mode_is_reported_after_the_apply(seat):
    seat.write({"mcpServers": {"crm": _cfg()}}, mode=0o644)
    seat.valid_token()

    rc, out = seat.run("crm")

    assert rc == 0 and "chmod 600" in out.err


def test_a_private_file_mode_gets_no_warning(seat):
    seat.write({"mcpServers": {"crm": _cfg()}}, mode=0o600)
    seat.valid_token()

    assert "chmod" not in seat.run("crm")[1].err


# ── one predicate for "stores a literal credential" ─────────────────────────


@pytest.mark.parametrize(
    ("value", "static"),
    [
        ("Bearer sk-live-abc", True),
        ("Bearer abc${X}", True),
        ("Bearer realsecretvalue ${", True),
        ("${X:-literal-default-secret}", True),
        ("Bearer ${TOKEN}", False),
        ("${TOKEN}", False),
        ("bearer ${TOKEN}", False),
        ("", False),
    ],
)
def test_only_a_pure_environment_reference_is_not_a_stored_credential(value, static):
    assert has_static_authorization({"headers": {"Authorization": value}}) is static


def test_a_header_name_with_stray_whitespace_is_still_the_authorization_header():
    assert has_static_authorization({"headers": {" Authorization": "Bearer x"}}) is True


def test_doctor_and_the_switcher_agree_on_every_shape(tmp_path):
    shapes = {
        "pure-env": {"headers": {"Authorization": "Bearer ${TOKEN}"}},
        "mixed": {"headers": {"Authorization": "Bearer abc${X}"}},
        "literal": {"headers": {"Authorization": "Bearer sk-1"}},
        "none": {"type": "stdio"},
    }
    (tmp_path / ".claude.json").write_text(json.dumps({"mcpServers": shapes}))

    flagged = {s.split(":", 1)[1] for s in check_static_connector_headers(tmp_path).data.get("connectors", [])}

    assert flagged == {n for n, c in shapes.items() if has_static_authorization(c)} == {"mixed", "literal"}


# ── doctor does not abort on odd shapes, and names projects unambiguously ───


@pytest.mark.parametrize("servers", [5, "x", ["a"], True])
def test_a_non_object_mcpservers_does_not_abort_doctor(tmp_path, servers):
    (tmp_path / ".claude.json").write_text(json.dumps({"mcpServers": servers}))

    check = check_static_connector_headers(tmp_path)

    assert check.name and "0 user-scope connector(s)" in check.detail


def test_two_projects_with_the_same_directory_name_are_told_apart(tmp_path):
    cfg = {"mcpServers": {"x": _cfg()}}
    (tmp_path / ".claude.json").write_text(json.dumps({"projects": {"/work/a/app": cfg, "/work/b/app": cfg}}))

    found = check_static_connector_headers(tmp_path).data["connectors"]

    assert found == ["project /work/a/app:x", "project /work/b/app:x"]
