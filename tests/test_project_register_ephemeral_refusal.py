"""`project-register` on a temporary checkout must say it was REFUSED, not that the write failed (finding 2 of the release sweep).

`_register_in_workspace_db` returns False both when the database write fails and when it declines a throwaway path on purpose
(60dd7154a). `project-register` read every False as "Failed to write workspace.db — check permissions and disk space", which
names the wrong cause and never mentions `EMPIRICA_REGISTER_EPHEMERAL`. One boolean was answering two questions.

The workspace database is redirected through EMPIRICA_WORKSPACE_DB and the home directory is faked as a NON-temp path (the guard
treats a home inside a temp dir as a sandbox), so nothing here touches the real registry.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

PID = "22222222-2222-2222-2222-222222222222"


def _project(tmp_path: Path) -> Path:
    d = tmp_path / "src" / "scratch-checkout"
    (d / ".empirica").mkdir(parents=True)
    (d / ".empirica" / "project.yaml").write_text(
        yaml.dump({"version": "2.0", "name": "scratch-checkout", "type": "software", "project_id": PID})
    )
    return d


def _args(path: Path):
    return SimpleNamespace(
        path=str(path),
        no_cortex=True,
        skip_user_link=False,
        force_metadata_update=False,
        cortex_url=None,
        api_key=None,
        timeout=10.0,
        output="json",
        verbose=False,
    )


@pytest.fixture
def real_home_registry(tmp_path, monkeypatch):
    """A home that the guard treats as REAL, holding a checkout that it treats as TEMPORARY.

    HOME is a directory inside tmp_path (so nothing touches the real home), and `_temp_roots` is narrowed to the checkout's
    parent only, so the home is not "inside a temp dir" (which the guard reads as a sandbox that keeps registering) while the
    checkout is. Nothing patches Path.home, which is shared by every module.
    """
    from empirica.cli.command_handlers import workspace_init

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EMPIRICA_WORKSPACE_DB", str(tmp_path / "ws.db"))
    monkeypatch.delenv("EMPIRICA_REGISTER_EPHEMERAL", raising=False)
    monkeypatch.setattr(workspace_init, "_temp_roots", lambda: [tmp_path / "src"])
    with patch("empirica.api.registry.DEFAULT_REGISTRY_PATH", tmp_path / "registry.yaml"):
        yield tmp_path


def _run(args, capsys):
    from empirica.cli.command_handlers.projects_commands import handle_project_register_command

    try:
        handle_project_register_command(args)
        code = 0
    except SystemExit as e:
        code = e.code if e.code is not None else 0
    captured = capsys.readouterr()
    out = captured.out
    return code, (json.loads(out) if out.strip().startswith("{") else {"stdout": out, "stderr": captured.err[-400:]})


def test_a_temporary_checkout_is_refused_on_purpose_and_the_message_says_so(real_home_registry, capsys):
    code, payload = _run(_args(_project(real_home_registry)), capsys)
    assert code == 1 and payload["ok"] is False
    text = (payload["error"] + " " + payload.get("hint", "")).lower()
    assert "temporary" in text and "empirica_register_ephemeral" in text
    assert "permission" not in text and "disk space" not in text, "this was not a write failure"


def test_the_override_registers_it(real_home_registry, monkeypatch, capsys):
    monkeypatch.setenv("EMPIRICA_REGISTER_EPHEMERAL", "1")
    code, payload = _run(_args(_project(real_home_registry)), capsys)
    assert code == 0, payload


def test_a_genuine_write_failure_still_says_the_write_failed(real_home_registry, capsys):
    with (
        patch("empirica.cli.command_handlers.workspace_init._is_ephemeral_registration", return_value=False),
        patch("empirica.cli.command_handlers.workspace_init._register_in_workspace_db", return_value=False),
    ):
        code, payload = _run(_args(_project(real_home_registry)), capsys)
    assert code == 1
    assert "Failed to write workspace.db" in payload["error"]
