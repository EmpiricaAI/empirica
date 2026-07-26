"""Tests for `empirica diagnose` integration health command.

Each check should produce a CheckResult with the right status given a
controlled filesystem state. Tests use tmp_path fixtures to set up
fake ~/.claude/ directories rather than touching the real one.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

# Add empirica src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from empirica.cli.command_handlers.diagnose import (
    FAIL,
    PASS,
    SKIP,
    WARN,
    CheckResult,
    check_active_session,
    check_claude_dir,
    check_empirica_cli_on_path,
    check_hooks_registered,
    check_marketplace_registered,
    check_plugin_files,
    check_project_initialized,
    check_python_version,
    check_settings_json,
    check_statusline_configured,
    format_human,
    format_json,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_claude_dir(tmp_path) -> Path:
    """A fake ~/.claude/ directory with the standard subdirectory layout."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "plugins" / "local" / "empirica" / "scripts").mkdir(parents=True)
    (claude_dir / "plugins" / "local" / "empirica" / "hooks").mkdir(parents=True)
    (claude_dir / "plugins" / "local" / "empirica" / ".claude-plugin").mkdir(parents=True)
    return claude_dir


@pytest.fixture
def healthy_plugin(fake_claude_dir) -> Path:
    """A fake plugin install with all required files present."""
    plugin_dir = fake_claude_dir / "plugins" / "local" / "empirica"
    (plugin_dir / "scripts" / "statusline_empirica.py").write_text("# fake script")
    (plugin_dir / "hooks" / "sentinel-gate.py").write_text("# fake gate")
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text('{"name": "empirica"}')
    return plugin_dir


@pytest.fixture
def healthy_settings(fake_claude_dir) -> Path:
    """A fake settings.json with statusLine + all hooks."""
    settings = {
        "statusLine": {
            "type": "command",
            "command": "python3 /fake/path/statusline_empirica.py",
        },
        "hooks": {
            "PreToolUse": [
                {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "python3 /fake/sentinel-gate.py"}]},
            ],
            "PreCompact": [
                {"hooks": [{"type": "command", "command": "python3 /fake/pre-compact.py"}]},
            ],
            "SessionStart": [
                {"matcher": "compact", "hooks": [{"type": "command", "command": "python3 /fake/post-compact.py"}]},
                {
                    "matcher": "startup|resume",
                    "hooks": [{"type": "command", "command": "python3 /fake/session-init.py"}],
                },
            ],
            "SubagentStart": [
                {"hooks": [{"type": "command", "command": "python3 /fake/subagent-start.py"}]},
            ],
            "SubagentStop": [
                {"hooks": [{"type": "command", "command": "python3 /fake/subagent-stop.py"}]},
            ],
        },
    }
    settings_file = fake_claude_dir / "settings.json"
    settings_file.write_text(json.dumps(settings, indent=2))
    return settings_file


# ---------------------------------------------------------------------------
# Foundation checks
# ---------------------------------------------------------------------------


class TestPythonVersion:
    def test_current_python_passes(self):
        result = check_python_version()
        # Tests run on the same Python that built the package, so this should
        # always pass on supported configurations.
        assert result.status == PASS
        assert result.data["version"]


class TestEmpiricaCli:
    def test_returns_pass_or_fail_consistently(self):
        result = check_empirica_cli_on_path()
        assert result.status in (PASS, FAIL)
        if result.status == FAIL:
            assert "empirica" in result.hint.lower()


# ---------------------------------------------------------------------------
# Claude Code config dir
# ---------------------------------------------------------------------------


class TestClaudeDir:
    def test_existing_dir_passes(self, fake_claude_dir, monkeypatch):
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        monkeypatch.setenv("HOME", str(fake_claude_dir.parent))
        result = check_claude_dir()
        assert result.status == PASS
        assert result.data["is_override"] is False

    def test_missing_dir_fails(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        result = check_claude_dir()
        assert result.status == FAIL
        assert "empirica setup" in result.hint

    def test_env_var_override_detected(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom_claude"
        custom.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(custom))
        result = check_claude_dir()
        assert result.status == PASS
        assert result.data["is_override"] is True
        assert "override" in result.detail


# ---------------------------------------------------------------------------
# Plugin files
# ---------------------------------------------------------------------------


class TestPluginFiles:
    def test_complete_install_passes(self, fake_claude_dir, healthy_plugin):
        result = check_plugin_files(fake_claude_dir)
        assert result.status == PASS

    def test_missing_dir_fails(self, tmp_path):
        result = check_plugin_files(tmp_path)
        assert result.status == FAIL
        assert "does not exist" in result.detail
        assert "empirica setup" in result.hint

    def test_partial_install_fails_with_list(self, fake_claude_dir):
        # Plugin dir exists but no files in it
        result = check_plugin_files(fake_claude_dir)
        assert result.status == FAIL
        assert "missing" in result.detail.lower()
        assert "statusline_empirica.py" in result.detail
        assert "force" in result.hint


# ---------------------------------------------------------------------------
# settings.json
# ---------------------------------------------------------------------------


class TestSettingsJson:
    def test_valid_passes(self, fake_claude_dir, healthy_settings):
        result = check_settings_json(fake_claude_dir)
        assert result.status == PASS

    def test_missing_fails(self, fake_claude_dir):
        result = check_settings_json(fake_claude_dir)
        assert result.status == FAIL

    def test_invalid_json_fails(self, fake_claude_dir):
        (fake_claude_dir / "settings.json").write_text("not valid json {")
        result = check_settings_json(fake_claude_dir)
        assert result.status == FAIL
        assert "not valid JSON" in result.detail


class TestStatusLineConfigured:
    def test_pointing_at_empirica_passes(self, fake_claude_dir, healthy_settings):
        result = check_statusline_configured(fake_claude_dir)
        assert result.status == PASS
        assert "statusline_empirica" in result.detail

    def test_no_statusLine_fails(self, fake_claude_dir):
        (fake_claude_dir / "settings.json").write_text(json.dumps({"hooks": {}}))
        result = check_statusline_configured(fake_claude_dir)
        assert result.status == FAIL
        assert "no `statusLine`" in result.detail

    def test_other_plugin_owns_statusline_warns(self, fake_claude_dir):
        (fake_claude_dir / "settings.json").write_text(
            json.dumps({"statusLine": {"type": "command", "command": "/some/other/plugin"}})
        )
        result = check_statusline_configured(fake_claude_dir)
        assert result.status == WARN
        assert "Another plugin" in result.hint

    def test_skipped_when_settings_missing(self, fake_claude_dir):
        result = check_statusline_configured(fake_claude_dir)
        assert result.status == SKIP


class TestHooksRegistered:
    def test_complete_set_passes(self, fake_claude_dir, healthy_settings):
        result = check_hooks_registered(fake_claude_dir)
        assert result.status == PASS
        assert result.data["missing"] == []
        assert len(result.data["found"]) == 6

    def test_missing_hooks_fails_with_list(self, fake_claude_dir):
        (fake_claude_dir / "settings.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Edit",
                                "hooks": [{"type": "command", "command": "python3 /fake/sentinel-gate.py"}],
                            },
                        ],
                    }
                }
            )
        )
        result = check_hooks_registered(fake_claude_dir)
        assert result.status == FAIL
        assert len(result.data["missing"]) == 5  # all but sentinel
        assert len(result.data["found"]) == 1

    def test_post_compact_via_session_start_matcher(self, fake_claude_dir):
        """post-compact.py lives under SessionStart with matcher='compact', not its own event."""
        (fake_claude_dir / "settings.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "compact",
                                "hooks": [{"type": "command", "command": "python3 /fake/post-compact.py"}],
                            },
                        ],
                    }
                }
            )
        )
        result = check_hooks_registered(fake_claude_dir)
        # post-compact should be found, others missing
        found_names = result.data["found"]
        assert any("Post-compact" in n for n in found_names)


class TestMarketplaceRegistered:
    def test_present_passes(self, fake_claude_dir):
        plugins_dir = fake_claude_dir / "plugins"
        plugins_dir.mkdir(exist_ok=True)
        (plugins_dir / "known_marketplaces.json").write_text(json.dumps({"local": {}}))
        result = check_marketplace_registered(fake_claude_dir)
        assert result.status == PASS

    def test_missing_warns_not_fails(self, fake_claude_dir):
        result = check_marketplace_registered(fake_claude_dir)
        assert result.status == WARN  # not FAIL — marketplace is optional


# ---------------------------------------------------------------------------
# Active session
# ---------------------------------------------------------------------------


class TestProjectInitialized:
    def test_no_empirica_dir_fails(self, tmp_path, monkeypatch):
        """Bare tmp dir with no .empirica/ anywhere up the tree."""
        monkeypatch.chdir(tmp_path)
        result = check_project_initialized()
        assert result.status == FAIL
        assert "project-init" in result.hint
        assert "no project" in result.hint.lower() or "No .empirica" in result.detail

    def test_empirica_dir_in_cwd_passes(self, tmp_path, monkeypatch):
        (tmp_path / ".empirica").mkdir()
        (tmp_path / ".empirica" / "project.yaml").write_text("name: test")
        monkeypatch.chdir(tmp_path)
        result = check_project_initialized()
        assert result.status == PASS
        assert str(tmp_path) in result.data["project_root"]

    def test_empirica_dir_in_ancestor_passes(self, tmp_path, monkeypatch):
        """cwd is a subdirectory, .empirica is in a parent — still valid."""
        (tmp_path / ".empirica").mkdir()
        (tmp_path / ".empirica" / "config.yaml").write_text("")
        subdir = tmp_path / "src" / "module"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)
        result = check_project_initialized()
        assert result.status == PASS
        assert "ancestor" in result.detail

    def test_empirica_dir_without_project_yaml_fails(self, tmp_path, monkeypatch):
        """.empirica/ exists but without project.yaml or config.yaml — not a real init."""
        (tmp_path / ".empirica").mkdir()
        monkeypatch.chdir(tmp_path)
        result = check_project_initialized()
        assert result.status == FAIL


class TestActiveSession:
    def test_no_project_db_skips(self, tmp_path, monkeypatch):
        """Without .empirica/, this check should SKIP (the preceding
        project-initialized check already flagged it)."""
        monkeypatch.chdir(tmp_path)
        result = check_active_session()
        assert result.status == SKIP
        assert "project not initialized" in result.detail.lower()

    def test_active_session_passes(self, tmp_path, monkeypatch):
        # Create a fake project DB with one active session
        db_dir = tmp_path / ".empirica" / "sessions"
        db_dir.mkdir(parents=True)
        db_path = db_dir / "sessions.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                ai_id TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                components_loaded INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            INSERT INTO sessions (session_id, ai_id, start_time, end_time, components_loaded)
            VALUES ('abc-123', 'claude-code', '2026-04-08T00:00:00+00:00', NULL, 0)
        """)
        conn.commit()
        conn.close()

        monkeypatch.chdir(tmp_path)
        result = check_active_session()
        assert result.status == PASS
        assert result.data["session_id"] == "abc-123"

    def test_no_active_session_warns(self, tmp_path, monkeypatch):
        db_dir = tmp_path / ".empirica" / "sessions"
        db_dir.mkdir(parents=True)
        db_path = db_dir / "sessions.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                ai_id TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                components_loaded INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Insert an ENDED session
        conn.execute("""
            INSERT INTO sessions (session_id, ai_id, start_time, end_time, components_loaded)
            VALUES ('old-session', 'claude-code', '2026-04-01T00:00:00+00:00', '2026-04-01T01:00:00+00:00', 0)
        """)
        conn.commit()
        conn.close()

        monkeypatch.chdir(tmp_path)
        result = check_active_session()
        assert result.status == WARN
        assert "no active session" in result.detail.lower()


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


class TestOutputFormatting:
    def test_human_format_includes_summary(self):
        results = [
            CheckResult(name="Test 1", status=PASS, detail="all good"),
            CheckResult(name="Test 2", status=FAIL, detail="broken", hint="fix it"),
            CheckResult(name="Test 3", status=WARN, detail="meh"),
        ]
        out = format_human(results)
        assert "Test 1" in out
        assert "Test 2" in out
        assert "Test 3" in out
        assert "fix it" in out
        assert "1 passed" in out
        assert "1 failed" in out
        assert "1 warning" in out

    def test_json_format_round_trip(self):
        results = [
            CheckResult(name="Foo", status=PASS, detail="ok", data={"x": 1}),
            CheckResult(name="Bar", status=FAIL, detail="broken", hint="fix"),
        ]
        out = format_json(results)
        parsed = json.loads(out)
        assert parsed["ok"] is False  # FAIL present
        assert parsed["summary"]["PASS"] == 1
        assert parsed["summary"]["FAIL"] == 1
        assert parsed["checks"][0]["name"] == "Foo"
        assert parsed["checks"][0]["data"]["x"] == 1
        assert parsed["checks"][1]["hint"] == "fix"

    def test_json_ok_when_all_pass(self):
        results = [
            CheckResult(name="A", status=PASS),
            CheckResult(name="B", status=PASS),
        ]
        parsed = json.loads(format_json(results))
        assert parsed["ok"] is True
