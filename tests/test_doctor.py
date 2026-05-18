"""Tests for `empirica doctor` — install + mesh-participation health check.

Closes prop_vnsvs6th6bc5lhprbhylvdxwmi (cortex AI proposal, 2026-05-18).
Covers the new mesh/cortex/ntfy/loops/MCP checks alongside the pre-existing
install/project state checks.
"""

from __future__ import annotations

import io
import json
import sqlite3
import types
import urllib.error
from pathlib import Path
from unittest.mock import patch

from empirica.cli.command_handlers.doctor import (
    FAIL,
    PASS,
    SKIP,
    WARN,
    Check,
    check_claude_code_cli,
    check_cortex_auth,
    check_cortex_creds,
    check_empirica_folder,
    check_git_present,
    check_loops_registered,
    check_mcp_config,
    check_ntfy_auth,
    check_ntfy_creds,
    check_project_yaml,
    check_python,
    check_sessions_db,
    handle_doctor_command,
    run_all_checks,
)

# ─── Install presence ──────────────────────────────────────────────────


def test_check_python_passes_on_310_plus():
    # We require Python 3.10+ per pyproject.toml, so this should always PASS
    # in any environment that can run the test suite.
    result = check_python()
    assert result.status == PASS
    assert "." in result.detail  # version triple like "3.12.4"


def test_check_git_present_passes_when_git_on_path():
    with patch("empirica.cli.command_handlers.doctor._which", return_value="/usr/bin/git"):
        result = check_git_present()
    assert result.status == PASS
    assert "/usr/bin/git" in result.detail


def test_check_git_present_fails_when_missing():
    with patch("empirica.cli.command_handlers.doctor._which", return_value=None):
        result = check_git_present()
    assert result.status == FAIL
    assert "git" in result.hint.lower()


def test_check_claude_code_cli_warns_when_missing():
    """`claude` is optional — should WARN not FAIL when missing."""
    with patch("empirica.cli.command_handlers.doctor._which", return_value=None):
        result = check_claude_code_cli()
    assert result.status == WARN


# ─── Project state ─────────────────────────────────────────────────────


def test_check_empirica_folder_warns_when_missing(tmp_path):
    result = check_empirica_folder(tmp_path)
    assert result.status == WARN


def test_check_empirica_folder_passes_when_present(tmp_path):
    (tmp_path / ".empirica" / "sessions").mkdir(parents=True)
    result = check_empirica_folder(tmp_path)
    assert result.status == PASS


def test_check_project_yaml_warns_when_missing(tmp_path):
    (tmp_path / ".empirica").mkdir()
    result = check_project_yaml(tmp_path)
    assert result.status == WARN
    assert "project-init" in result.hint


def test_check_project_yaml_passes_with_ai_id(tmp_path):
    import yaml
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text(
        yaml.safe_dump({"ai_id": "test", "name": "Test", "org_id": "org-x",
                        "tenant_slug": "x", "mesh_id_prefix": "x_x"})
    )
    result = check_project_yaml(tmp_path)
    assert result.status == PASS
    assert "ai_id=test" in result.detail
    assert result.data["ai_id"] == "test"
    assert result.data["mesh_id_prefix"] == "x_x"


def test_check_project_yaml_warns_without_ai_id(tmp_path):
    import yaml
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text(
        yaml.safe_dump({"name": "Test"})  # no ai_id
    )
    result = check_project_yaml(tmp_path)
    assert result.status == WARN
    assert "no ai_id" in result.detail


def test_check_sessions_db_warns_when_missing(tmp_path):
    (tmp_path / ".empirica" / "sessions").mkdir(parents=True)
    result = check_sessions_db(tmp_path)
    assert result.status == WARN


def test_check_sessions_db_passes_with_schema(tmp_path):
    (tmp_path / ".empirica" / "sessions").mkdir(parents=True)
    db_path = tmp_path / ".empirica" / "sessions" / "sessions.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO sessions VALUES ('s1')")
    result = check_sessions_db(tmp_path)
    assert result.status == PASS
    assert "1 sessions" in result.detail


# ─── Cortex connectivity ───────────────────────────────────────────────


def _fake_response(status: int, body: dict | str = ""):
    class _R:
        def __init__(self, s, b):
            self.status = s
            self._b = json.dumps(b).encode() if isinstance(b, dict) else str(b).encode()

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False
    return _R(status, body)


def test_check_cortex_creds_warns_when_missing(monkeypatch):
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    monkeypatch.delenv("CORTEX_REMOTE_URL", raising=False)
    monkeypatch.delenv("CORTEX_URL", raising=False)
    with patch("empirica.config.credentials_loader.get_credentials_loader",
               side_effect=Exception("no loader")):
        result = check_cortex_creds()
    assert result.status == WARN
    assert "missing" in result.detail


def test_check_cortex_creds_passes_from_env(monkeypatch):
    monkeypatch.setenv("CORTEX_REMOTE_URL", "https://example.com")
    monkeypatch.setenv("CORTEX_API_KEY", "ctx_test")
    result = check_cortex_creds()
    assert result.status == PASS


def test_check_cortex_auth_passes_with_mesh_fields(monkeypatch):
    monkeypatch.setenv("CORTEX_REMOTE_URL", "https://example.com")
    monkeypatch.setenv("CORTEX_API_KEY", "ctx_test")
    payload = {"user": "x", "org_id": "org-y", "tenant_slug": "y",
               "mesh_id_prefix": "y_y"}
    with patch("urllib.request.urlopen", return_value=_fake_response(200, payload)):
        result = check_cortex_auth()
    assert result.status == PASS
    assert result.data["mesh_id_prefix"] == "y_y"


def test_check_cortex_auth_warns_when_mesh_fields_missing(monkeypatch):
    """Auth OK but server is behind Phase 1 — WARN, not FAIL."""
    monkeypatch.setenv("CORTEX_REMOTE_URL", "https://example.com")
    monkeypatch.setenv("CORTEX_API_KEY", "ctx_test")
    payload = {"user": "x"}  # no mesh fields
    with patch("urllib.request.urlopen", return_value=_fake_response(200, payload)):
        result = check_cortex_auth()
    assert result.status == WARN
    assert "mesh fields missing" in result.detail


def test_check_cortex_auth_fails_on_401(monkeypatch):
    monkeypatch.setenv("CORTEX_REMOTE_URL", "https://example.com")
    monkeypatch.setenv("CORTEX_API_KEY", "bad")
    err = urllib.error.HTTPError(
        url="https://example.com/v1/users/me", code=401, msg="Unauthorized",
        hdrs=None, fp=io.BytesIO(b""),  # type: ignore[arg-type]
    )
    with patch("urllib.request.urlopen", side_effect=err):
        result = check_cortex_auth()
    assert result.status == FAIL
    assert "401" in result.detail


def test_check_cortex_auth_skips_without_creds(monkeypatch):
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    monkeypatch.delenv("CORTEX_REMOTE_URL", raising=False)
    monkeypatch.delenv("CORTEX_URL", raising=False)
    with patch("empirica.config.credentials_loader.get_credentials_loader",
               side_effect=Exception("no loader")):
        result = check_cortex_auth()
    assert result.status == SKIP


# ─── ntfy mesh ─────────────────────────────────────────────────────────


def test_check_ntfy_creds_warns_when_missing(monkeypatch):
    for var in ("ORCHESTRATION_NTFY_URL", "NTFY_URL", "ORCHESTRATION_NTFY_TOPIC",
                "ORCHESTRATION_NTFY_USER", "ORCHESTRATION_NTFY_PASS",
                "ORCHESTRATION_NTFY_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    with patch("empirica.config.credentials_loader.get_credentials_loader",
               side_effect=Exception("no loader")):
        result = check_ntfy_creds()
    assert result.status == WARN
    assert "missing" in result.detail


def test_check_ntfy_creds_passes_with_token(monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_NTFY_URL", "https://ntfy.example.com")
    monkeypatch.setenv("ORCHESTRATION_NTFY_TOPIC", "test-topic")
    monkeypatch.setenv("ORCHESTRATION_NTFY_TOKEN", "tk_test")
    result = check_ntfy_creds()
    assert result.status == PASS
    assert result.data["auth"] == "token"


def test_check_ntfy_auth_passes_on_200(monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_NTFY_URL", "https://ntfy.example.com")
    monkeypatch.setenv("ORCHESTRATION_NTFY_TOPIC", "test-topic")
    monkeypatch.setenv("ORCHESTRATION_NTFY_TOKEN", "tk_test")
    with patch("urllib.request.urlopen", return_value=_fake_response(200, {})):
        result = check_ntfy_auth()
    assert result.status == PASS


def test_check_ntfy_auth_fails_on_401(monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_NTFY_URL", "https://ntfy.example.com")
    monkeypatch.setenv("ORCHESTRATION_NTFY_TOPIC", "test-topic")
    monkeypatch.setenv("ORCHESTRATION_NTFY_TOKEN", "bad")
    err = urllib.error.HTTPError(
        url="https://ntfy.example.com/v1/account", code=401, msg="Unauthorized",
        hdrs=None, fp=io.BytesIO(b""),  # type: ignore[arg-type]
    )
    with patch("urllib.request.urlopen", side_effect=err):
        result = check_ntfy_auth()
    assert result.status == FAIL


# ─── Listener / loops ──────────────────────────────────────────────────


def test_check_loops_registered_warns_when_empty():
    payload = {"loops": []}
    with patch("empirica.cli.command_handlers.doctor._which", return_value="/x/empirica"), \
         patch("empirica.cli.command_handlers.doctor._run",
               return_value=(0, json.dumps(payload), "")):
        result = check_loops_registered()
    assert result.status == WARN
    assert "no loops" in result.detail


def test_check_loops_registered_passes_with_loops():
    payload = {"loops": [{"name": "cortex-mailbox-poll"}, {"name": "compliance-debt-sweep"}]}
    with patch("empirica.cli.command_handlers.doctor._which", return_value="/x/empirica"), \
         patch("empirica.cli.command_handlers.doctor._run",
               return_value=(0, json.dumps(payload), "")):
        result = check_loops_registered()
    assert result.status == PASS
    assert "cortex-mailbox-poll" in result.detail


# ─── MCP config ────────────────────────────────────────────────────────


def test_check_mcp_config_passes_with_empirica_server(tmp_path, monkeypatch):
    fake_home = tmp_path
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "mcp.json").write_text(json.dumps({
        "mcpServers": {"empirica": {"command": "empirica-mcp"}}
    }))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    result = check_mcp_config()
    assert result.status == PASS
    assert result.data["has_empirica"] is True


def test_check_mcp_config_warns_when_no_empirica_entry(tmp_path, monkeypatch):
    fake_home = tmp_path
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "mcp.json").write_text(json.dumps({
        "mcpServers": {"other-server": {"command": "other"}}
    }))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    result = check_mcp_config()
    assert result.status == WARN
    assert "no `empirica` or `cortex`" in result.detail


def test_check_mcp_config_warns_when_no_configs(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    result = check_mcp_config()
    assert result.status == WARN
    assert "no MCP config" in result.detail


# ─── Exit codes ────────────────────────────────────────────────────────


def test_handle_doctor_returns_zero_on_pass(monkeypatch, capsys):
    """Doctor returns 0 even with WARN, unless --strict-warn is set."""
    args = types.SimpleNamespace(output="json", strict_warn=False)
    with patch("empirica.cli.command_handlers.doctor.run_all_checks",
               return_value=[Check("p", PASS), Check("w", WARN)]):
        rc = handle_doctor_command(args)
    assert rc == 0
    output = capsys.readouterr().out
    assert json.loads(output)["summary"]["warn"] == 1


def test_handle_doctor_returns_one_on_fail(capsys):
    args = types.SimpleNamespace(output="json", strict_warn=False)
    with patch("empirica.cli.command_handlers.doctor.run_all_checks",
               return_value=[Check("p", PASS), Check("f", FAIL)]):
        rc = handle_doctor_command(args)
    assert rc == 1


def test_handle_doctor_strict_warn_returns_two(capsys):
    args = types.SimpleNamespace(output="json", strict_warn=True)
    with patch("empirica.cli.command_handlers.doctor.run_all_checks",
               return_value=[Check("p", PASS), Check("w", WARN)]):
        rc = handle_doctor_command(args)
    assert rc == 2


# ─── Integration ──────────────────────────────────────────────────────


def test_run_all_checks_returns_complete_list():
    """Smoke test: doctor returns all expected checks without crashing."""
    checks = run_all_checks()
    names = {c.name for c in checks}
    # Core categories present
    assert "Python version" in names
    assert "empirica CLI on PATH" in names
    assert "project.yaml present + has ai_id" in names
    assert "Cortex credentials configured" in names
    assert "Cortex auth + mesh fields" in names
    assert "ntfy credentials configured" in names
    assert "canonical loops registered" in names
    assert "MCP servers configured" in names
