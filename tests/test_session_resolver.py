"""Tests for the session resolver — alias support and partial-UUID lookup.

These tests use a fixture-seeded tmp SQLite DB so they run deterministically
on any machine (CI, fresh clone, container) rather than skipping when the
dev's live ~/.empirica/sessions.db is empty.
"""

from __future__ import annotations

import logging
import time
import uuid

import pytest

from empirica.utils.session_resolver import (
    detect_current_location,
    get_instance_id,
    get_latest_session_id,
    is_session_alias,
    resolve_session_id,
)

# Fixed UUIDs — partial-UUID tests can assert exact prefixes
SID_ACTIVE_CLAUDE = "88dbf132-cc7c-4a4b-9b59-77df3b13dbd2"
SID_ACTIVE_GPT = "99eef241-aabb-4ccc-8ddd-eef0a1b2c3d4"
SID_DONE_CLAUDE = "aabbccdd-1111-2222-3333-444455556666"


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Tmp DB seeded with three sessions for deterministic resolver tests.

    - SID_ACTIVE_CLAUDE: active claude-code, most recent
    - SID_ACTIVE_GPT:    active gpt-5, second-most recent
    - SID_DONE_CLAUDE:   completed claude-code, oldest

    instance_id is left NULL on all seeded rows. The resolver's instance-
    isolation filter accepts NULL as a legacy match, so this DB is visible
    regardless of what EMPIRICA_INSTANCE_ID resolves to in the test env.
    """
    db_path = tmp_path / "sessions.db"
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(db_path))
    # Ensure no cached SessionDatabase singleton points at the live DB
    monkeypatch.setenv("EMPIRICA_INSTANCE_ID", "test-isolated-resolver")

    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()

    project_id = str(uuid.uuid4())
    now = time.time()
    rows = [
        # (session_id, ai_id, end_time, start_time)
        (SID_DONE_CLAUDE, "claude-code", now - 50, now - 300),
        (SID_ACTIVE_GPT, "gpt-5", None, now - 200),
        (SID_ACTIVE_CLAUDE, "claude-code", None, now - 100),
    ]
    for sid, ai_id, end_t, start_t in rows:
        db.conn.execute(
            "INSERT INTO sessions (session_id, project_id, ai_id, "
            "start_time, end_time, components_loaded) VALUES (?, ?, ?, ?, ?, ?)",
            (sid, project_id, ai_id, start_t, end_t, "[]"),
        )
    db.conn.commit()
    db.close()

    yield db_path


# ─── Pure functions (no DB) ──────────────────────────────────────────────


def test_is_session_alias():
    assert is_session_alias("latest")
    assert is_session_alias("last")
    assert is_session_alias("latest:active")
    assert is_session_alias("latest:claude-code")
    assert is_session_alias("latest:active:claude-code")
    assert not is_session_alias("88dbf132-cc7c-4a4b-9b59-77df3b13dbd2")
    assert not is_session_alias("88dbf132")


def test_resolve_full_uuid():
    """Full UUIDs pass through unchanged without DB lookup."""
    full_uuid = "88dbf132-cc7c-4a4b-9b59-77df3b13dbd2"
    assert resolve_session_id(full_uuid) == full_uuid


# ─── Resolver against seeded DB ──────────────────────────────────────────


def test_resolve_partial_uuid(seeded_db):
    """Partial UUID (8 chars) resolves to the matching full UUID."""
    result = resolve_session_id("88dbf132")
    assert result == SID_ACTIVE_CLAUDE


def test_resolve_partial_uuid_unknown_raises(seeded_db):
    """A partial UUID that matches nothing raises ValueError."""
    with pytest.raises(ValueError, match="No session found matching"):
        resolve_session_id("deadbeef")


def test_resolve_latest_alias(seeded_db):
    """'latest' returns the most recently started session."""
    result = resolve_session_id("latest")
    assert result == SID_ACTIVE_CLAUDE


def test_resolve_last_alias(seeded_db):
    """'last' is a synonym for 'latest'."""
    assert resolve_session_id("last") == resolve_session_id("latest")


def test_resolve_auto_alias(seeded_db):
    """'auto' is also normalized to 'latest'."""
    assert resolve_session_id("auto") == resolve_session_id("latest")


def test_resolve_latest_active(seeded_db):
    """'latest:active' filters out the completed session."""
    result = resolve_session_id("latest:active")
    # Both active sessions exist; the more recent (claude-code) wins.
    assert result == SID_ACTIVE_CLAUDE


def test_resolve_latest_with_ai_id(seeded_db):
    """'latest:<ai_id>' filters by AI."""
    assert resolve_session_id("latest:claude-code") == SID_ACTIVE_CLAUDE
    assert resolve_session_id("latest:gpt-5") == SID_ACTIVE_GPT


def test_resolve_compound_alias(seeded_db):
    """'latest:active:<ai_id>' combines both filters.

    SID_DONE_CLAUDE is claude-code but completed — must be skipped in favor
    of SID_ACTIVE_CLAUDE which is active.
    """
    assert resolve_session_id("latest:active:claude-code") == SID_ACTIVE_CLAUDE


def test_resolve_compound_alias_no_match(seeded_db):
    """Compound alias with no matching session raises ValueError."""
    with pytest.raises(ValueError, match="No session found"):
        resolve_session_id("latest:active:gpt-4-turbo")


def test_get_latest_session_id(seeded_db):
    """Convenience function returns the most recent session."""
    assert get_latest_session_id() == SID_ACTIVE_CLAUDE


def test_get_latest_session_id_with_ai_filter(seeded_db):
    """ai_id filter restricts to that AI's sessions."""
    assert get_latest_session_id(ai_id="gpt-5") == SID_ACTIVE_GPT


def test_get_latest_session_id_active_only(seeded_db):
    """active_only=True excludes completed sessions."""
    # SID_DONE_CLAUDE is excluded; SID_ACTIVE_CLAUDE wins by recency.
    assert get_latest_session_id(active_only=True) == SID_ACTIVE_CLAUDE


def test_get_latest_session_id_combined_filters(seeded_db):
    """ai_id + active_only filter together."""
    assert get_latest_session_id(ai_id="claude-code", active_only=True) == SID_ACTIVE_CLAUDE


def test_resolve_invalid_alias(seeded_db):
    """Unknown AI in alias raises ValueError."""
    with pytest.raises(ValueError, match="No session found"):
        resolve_session_id("latest:nonexistent-ai-xyz-12345")


# ─── get_active_project_path CWD override (issue #90) ────────────────────


class TestGetActiveProjectPath:
    """Tests for get_active_project_path CWD-reliable override (issue #90)."""

    @staticmethod
    def _isolate_home(tmp_path, monkeypatch):
        """Redirect HOME/USERPROFILE to tmp_path so get_instance_id() can't
        read the developer's real ~/.empirica/instance_projects/. Pins an
        isolated instance_id that points at a non-existent file so the
        fallthrough paths return None rather than opportunistic matches."""
        fake_home = tmp_path / "home"
        fake_home.mkdir(exist_ok=True)
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setenv("USERPROFILE", str(fake_home))
        monkeypatch.setenv("EMPIRICA_INSTANCE_ID", "test-isolated")

    def test_cwd_reliable_with_project_yaml(self, tmp_path, monkeypatch):
        """When EMPIRICA_CWD_RELIABLE=true and CWD has .empirica/project.yaml, return CWD."""
        from empirica.utils.session_resolver import get_active_project_path

        self._isolate_home(tmp_path, monkeypatch)
        project_dir = tmp_path / "project"
        empirica_dir = project_dir / ".empirica"
        empirica_dir.mkdir(parents=True)
        (empirica_dir / "project.yaml").write_text("project_id: test-123\n")

        monkeypatch.setenv("EMPIRICA_CWD_RELIABLE", "true")
        monkeypatch.chdir(project_dir)

        result = get_active_project_path()
        assert result == str(project_dir)

    def test_cwd_reliable_without_project_yaml(self, tmp_path, monkeypatch):
        """When EMPIRICA_CWD_RELIABLE=true but no project.yaml, fall through to other sources."""
        from empirica.utils.session_resolver import get_active_project_path

        self._isolate_home(tmp_path, monkeypatch)
        monkeypatch.setenv("EMPIRICA_CWD_RELIABLE", "true")
        monkeypatch.chdir(tmp_path)

        # No project.yaml guard, no instance_projects file, no active_work — must be None
        result = get_active_project_path()
        assert result is None

    def test_no_cwd_reliable_flag(self, tmp_path, monkeypatch):
        """Without EMPIRICA_CWD_RELIABLE, CWD is never used even with project.yaml."""
        from empirica.utils.session_resolver import get_active_project_path

        self._isolate_home(tmp_path, monkeypatch)
        project_dir = tmp_path / "project"
        empirica_dir = project_dir / ".empirica"
        empirica_dir.mkdir(parents=True)
        (empirica_dir / "project.yaml").write_text("project_id: test-123\n")

        monkeypatch.delenv("EMPIRICA_CWD_RELIABLE", raising=False)
        monkeypatch.chdir(project_dir)

        # No flag means CWD check never fires; with isolated HOME the fallthrough
        # finds nothing either, so the result must be None.
        result = get_active_project_path()
        assert result is None

    def test_cwd_reliable_beats_stale_instance_projects(self, tmp_path, monkeypatch):
        """CWD override takes priority over stale instance_projects data."""
        import json

        from empirica.utils.session_resolver import get_active_project_path

        # Set up CWD project
        cwd_project = tmp_path / "current_project"
        cwd_project.mkdir()
        empirica_dir = cwd_project / ".empirica"
        empirica_dir.mkdir()
        (empirica_dir / "project.yaml").write_text("project_id: current\n")

        # Set up stale instance_projects pointing to a different project
        stale_project = tmp_path / "stale_project"
        stale_project.mkdir()
        instance_dir = tmp_path / "home" / ".empirica" / "instance_projects"
        instance_dir.mkdir(parents=True)
        (instance_dir / "win-default.json").write_text(json.dumps({"project_path": str(stale_project)}))

        monkeypatch.setenv("EMPIRICA_CWD_RELIABLE", "true")
        monkeypatch.setenv("EMPIRICA_INSTANCE_ID", "win-default")
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
        monkeypatch.chdir(cwd_project)

        result = get_active_project_path()
        assert result == str(cwd_project)


class TestGetInstanceIdOverrideWarning:
    """get_instance_id() Priority-1 override warning behavior under tmux.

    An explicit EMPIRICA_INSTANCE_ID overrides TMUX_PANE. Intentional, stable
    shapes — a cockpit slot (`[a-z][a-z0-9_-]*`) OR a UUID (incl. UUIDv7) — log
    at debug; other shapes warn that they break per-pane isolation. Regression
    guard for the codex/ecodex UUIDv7 practitioner_id false-positive: a UUIDv7
    starts with a digit, so the old leading-lowercase-letter guard mis-warned
    and advised unsetting the id (which would sever the practitioner→calibration
    mapping). The override always RESOLVES correctly; only the warning misfired.
    """

    def _warnings(self, caplog):
        return [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_uuidv7_override_resolves_without_warning(self, monkeypatch, caplog):
        uuidv7 = "019f23cf-2b49-7f21-96bf-b7280bbafbc4"  # starts with a digit
        monkeypatch.setenv("TMUX_PANE", "%11")
        monkeypatch.setenv("EMPIRICA_INSTANCE_ID", uuidv7)
        with caplog.at_level(logging.DEBUG, logger="empirica.utils.session_resolver"):
            assert get_instance_id() == uuidv7
        assert self._warnings(caplog) == []

    def test_cockpit_slot_override_does_not_warn(self, monkeypatch, caplog):
        monkeypatch.setenv("TMUX_PANE", "%11")
        monkeypatch.setenv("EMPIRICA_INSTANCE_ID", "cockpit-slot-3")
        with caplog.at_level(logging.DEBUG, logger="empirica.utils.session_resolver"):
            assert get_instance_id() == "cockpit-slot-3"
        assert self._warnings(caplog) == []

    def test_non_slot_override_still_warns(self, monkeypatch, caplog):
        # A globally-set id with special chars genuinely breaks per-pane isolation.
        monkeypatch.setenv("TMUX_PANE", "%11")
        monkeypatch.setenv("EMPIRICA_INSTANCE_ID", "GLOBAL:%bad")
        with caplog.at_level(logging.DEBUG, logger="empirica.utils.session_resolver"):
            assert get_instance_id() == "GLOBAL:%bad"
        assert len(self._warnings(caplog)) == 1


class TestDetectCurrentLocation:
    """detect_current_location resolves the ephemeral PHYSICAL location
    (tmux pane / TTY / …) — a controller SEPARATE from durable identity (B3).

    Unlike get_instance_id it ignores the EMPIRICA_INSTANCE_ID override, which
    carries a durable practitioner IDENTITY (e.g. an ecodex thread_id), not a
    physical location. So a practitioner whose identity is wired into that env
    var still records its real tmux/TTY as its presence location.
    """

    def test_ignores_identity_override_resolves_physical(self, monkeypatch):
        # ecodex-style: EMPIRICA_INSTANCE_ID = a durable thread_id identity.
        monkeypatch.setenv("EMPIRICA_INSTANCE_ID", "019f23cf-2b49-7f21-96bf-b7280bbafbc4")
        monkeypatch.setenv("TMUX_PANE", "%11")
        # identity resolver honors the override; location resolver returns physical tmux
        assert get_instance_id() == "019f23cf-2b49-7f21-96bf-b7280bbafbc4"
        assert detect_current_location() == "tmux_11"

    def test_agrees_with_instance_id_without_override(self, monkeypatch):
        monkeypatch.delenv("EMPIRICA_INSTANCE_ID", raising=False)
        monkeypatch.delenv("CLAUDE_INSTANCE_ID", raising=False)
        monkeypatch.setenv("TMUX_PANE", "%7")
        # no identity override → the two resolvers agree (both = physical tmux)
        assert detect_current_location() == "tmux_7" == get_instance_id()

    def test_none_when_no_physical_location(self, monkeypatch):
        for var in ("EMPIRICA_INSTANCE_ID", "CLAUDE_INSTANCE_ID", "TMUX_PANE", "TERM_SESSION_ID", "WINDOWID"):
            monkeypatch.delenv(var, raising=False)
        # No terminal at all (non-terminal harness) → honest None, not a leaked identity.
        monkeypatch.setattr("empirica.utils.session_resolver.get_tty_key", lambda: None)
        assert detect_current_location() is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
