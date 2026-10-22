"""`sync-config` must exit with the outcome of what it was asked to do and print ONE JSON document (goal d897e750, HIGH item).

`handle_sync_config_command` called `_handle_sync_config_command_helper(...)` and discarded its return value. A refused set
(unknown key, bad `visibility`, unsupported trigger) therefore printed its error and then went on to print the whole config as a
second document and exit 0, and a successful set printed its message and then the config again. Two JSON documents on stdout
cannot be parsed by anything, and an exit code of 0 on a refusal means a script cannot tell the refusal happened.

The config is loaded and saved through the module's own functions, replaced here so nothing touches a real config file.
"""

from __future__ import annotations

import json
import types

import pytest

from empirica.cli.command_handlers import sync_commands as sc


@pytest.fixture
def config(monkeypatch):
    store = {"enabled": False, "visibility": "private", "provider": "auto", "auto_push_on": []}
    saved = []
    monkeypatch.setattr(sc, "_load_sync_config", lambda: dict(store))
    monkeypatch.setattr(sc, "_save_sync_config", lambda c: saved.append(dict(c)) or True)
    monkeypatch.setattr(sc, "_resolve_remote", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_list_remotes", lambda: [])
    return saved


def _run(capsys, key=None, value=None, output="json"):
    rc = sc.handle_sync_config_command(types.SimpleNamespace(key=key, value=value, output=output))
    return rc, capsys.readouterr().out


def _one_document(out):
    """Raises if stdout is not exactly one JSON document."""
    return json.loads(out)


@pytest.mark.parametrize(
    "key,value",
    [("nonsense_key", "x"), ("visibility", "banana"), ("provider", "myspace"), ("auto_push_on", "session_end")],
)
def test_a_refused_set_exits_nonzero_and_prints_one_document(config, capsys, key, value):
    rc, out = _run(capsys, key, value)
    assert rc == 1, "a refusal must not exit 0"
    assert _one_document(out)["ok"] is False
    assert config == [], "and nothing was saved"


def test_a_successful_set_exits_zero_and_prints_one_document(config, capsys):
    rc, out = _run(capsys, "provider", "github")
    assert rc == 0
    doc = _one_document(out)
    assert doc["ok"] is True and doc["config"]["provider"] == "github"
    assert config and config[-1]["provider"] == "github"


def test_showing_the_whole_config_is_unchanged(config, capsys):
    rc, out = _run(capsys)
    doc = _one_document(out)
    assert rc == 0 and doc["ok"] is True and doc["config"]["visibility"] == "private"


def test_showing_one_key_is_unchanged(config, capsys):
    rc, out = _run(capsys, "visibility")
    assert rc == 0 and _one_document(out) == {"ok": True, "key": "visibility", "value": "private"}


def test_showing_an_unknown_key_still_fails(config, capsys):
    rc, out = _run(capsys, "nonsense_key")
    assert rc == 1 and _one_document(out)["ok"] is False
