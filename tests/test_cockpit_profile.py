"""`empirica cockpit launch --profile NAME` picks ~/.empirica/cockpit/config-NAME.yaml.

A second (or third) cockpit used to need its full --config path. An unknown
profile must be an error naming what exists, never a default config written in
its place: a typo must not bring up the wrong cockpit.
"""

from __future__ import annotations

import types

from empirica.cli.command_handlers import cockpit_launcher_commands as clc


def _args(**kw):
    return types.SimpleNamespace(**{"config": None, "profile": None, **kw})


def _home(tmp_path, monkeypatch, *profiles):
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / ".empirica" / "cockpit"
    d.mkdir(parents=True)
    for name in profiles:
        (d / f"config-{name}.yaml").write_text("session_name: x\n")
    return d


def test_a_profile_resolves_to_its_config_file(tmp_path, monkeypatch):
    d = _home(tmp_path, monkeypatch, "ecodex")
    path, error = clc._config_path_arg(_args(profile="ecodex"))
    assert error is None and path == str(d / "config-ecodex.yaml")


def test_an_unknown_profile_is_an_error_listing_what_exists(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, "monitor-a", "ecodex")
    path, error = clc._config_path_arg(_args(profile="ecodx"))
    assert path is None and "ecodex" in error and "monitor-a" in error


def test_config_and_profile_together_are_refused(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, "ecodex")
    path, error = clc._config_path_arg(_args(profile="ecodex", config="/x.yaml"))
    assert path is None and "not both" in error


def test_without_a_profile_config_passes_through(tmp_path, monkeypatch):
    """Positive control: existing --config and default behaviour are unchanged."""
    _home(tmp_path, monkeypatch)
    assert clc._config_path_arg(_args(config="/x.yaml")) == ("/x.yaml", None)
    assert clc._config_path_arg(_args()) == (None, None)
