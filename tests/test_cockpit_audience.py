"""Cockpits are single-user or shared, said explicitly (goal 9ccdd9d4, David 2026-11-26).

Sharing was implicit: a config with socket: and server_access users was a shared stage, everything else single-user, and
nothing listed or checked them as two kinds.
"""

from __future__ import annotations

from empirica.core.cockpit.launcher import config as cfg


def _write(tmp_path, name, body):
    p = tmp_path / f"config-{name}.yaml"
    p.write_text("session_name: s-" + name + "\n" + body)
    return p


def test_the_default_follows_the_socket(tmp_path):
    assert cfg.load_config(_write(tmp_path, "a", "")).audience == "single"
    assert cfg.load_config(_write(tmp_path, "b", "socket: /tmp/stage.sock\n")).audience == "shared"


def test_an_explicit_audience_wins(tmp_path):
    assert cfg.load_config(_write(tmp_path, "c", "audience: shared\nsocket: /tmp/s.sock\n")).audience == "shared"


def test_a_shared_cockpit_needs_a_socket(tmp_path):
    errors, _ = cfg.validate_config(cfg.load_config(_write(tmp_path, "d", "audience: shared\n")))
    assert any("shared" in e and "socket" in e for e in errors)


def test_a_single_user_cockpit_cannot_grant_access(tmp_path):
    errors, _ = cfg.validate_config(cfg.load_config(_write(tmp_path, "e", "audience: single\nserver_access: [bob]\n")))
    assert any("server_access" in e for e in errors)


def test_an_unknown_audience_is_an_error(tmp_path):
    errors, _ = cfg.validate_config(cfg.load_config(_write(tmp_path, "f", "audience: team\n")))
    assert any("audience" in e for e in errors)


def test_profiles_are_listed_in_two_groups(tmp_path):
    _write(tmp_path, "solo", "")
    _write(tmp_path, "stage", "socket: /tmp/stage.sock\n")
    (tmp_path / "config.yaml").write_text("session_name: cockpit\n")
    assert cfg.profiles_by_audience(tmp_path) == {"single": ["(default)", "solo"], "shared": ["stage"]}
