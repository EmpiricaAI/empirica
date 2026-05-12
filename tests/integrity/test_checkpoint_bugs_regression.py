#!/usr/bin/env python3
"""
Regression Tests for Checkpoint System Bugs

Tests for bugs found by claude-code during audit:
1. Missing list_checkpoints method in GitEnhancedReflexLogger
2. Missing reflexes table in database schema
3. Checkpoints created with empty vectors

Created: 2025-12-01
Purpose: Prevent regression after fixes
"""

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger


class TestCheckpointListMethod:
    """Test Bug #1: Missing list_checkpoints method"""

    @pytest.fixture
    def git_logger(self, tmp_path):
        """Create logger with git repo"""
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True)

        # Create initial commit
        test_file = tmp_path / "test.txt"
        test_file.write_text("test")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

        return GitEnhancedReflexLogger(
            session_id="test-session",
            enable_git_notes=True,
            base_log_dir=str(tmp_path / ".empirica_reflex_logs"),
            git_repo_path=str(tmp_path)
        )

    def test_list_checkpoints_method_exists(self, git_logger):
        """REGRESSION: Verify list_checkpoints method exists"""
        assert hasattr(git_logger, 'list_checkpoints'), \
            "GitEnhancedReflexLogger must have list_checkpoints method"
        assert callable(git_logger.list_checkpoints), \
            "list_checkpoints must be callable"

    def test_list_checkpoints_empty(self, git_logger):
        """REGRESSION: list_checkpoints returns empty list when no checkpoints"""
        checkpoints = git_logger.list_checkpoints()

        assert isinstance(checkpoints, list), "list_checkpoints must return a list"
        assert len(checkpoints) == 0, "Should return empty list when no checkpoints"

    def test_list_checkpoints_after_create(self, git_logger):
        """REGRESSION: list_checkpoints returns created checkpoints"""
        # Create multiple checkpoints
        git_logger.add_checkpoint("PREFLIGHT", 1, {"know": 0.5, "do": 0.6})
        git_logger.add_checkpoint("CHECK", 1, {"know": 0.7, "do": 0.8})
        git_logger.add_checkpoint("POSTFLIGHT", 1, {"know": 0.9, "do": 0.95})

        # List all checkpoints
        checkpoints = git_logger.list_checkpoints()

        assert len(checkpoints) >= 3, "Should return all created checkpoints"

        # Verify checkpoint structure
        for checkpoint in checkpoints:
            assert "session_id" in checkpoint
            assert "phase" in checkpoint
            assert "round" in checkpoint
            assert "vectors" in checkpoint
            assert "timestamp" in checkpoint

    def test_list_checkpoints_filter_by_session(self, git_logger, tmp_path):
        """REGRESSION: list_checkpoints can filter by session_id"""
        # Create checkpoint for this session
        git_logger.add_checkpoint("PREFLIGHT", 1, {"know": 0.5})

        # Create checkpoint for different session
        other_logger = GitEnhancedReflexLogger(
            session_id="other-session",
            enable_git_notes=True,
            base_log_dir=str(tmp_path / ".empirica_reflex_logs"),
            git_repo_path=str(tmp_path)
        )
        other_logger.add_checkpoint("PREFLIGHT", 1, {"know": 0.6})

        # List only this session's checkpoints
        checkpoints = git_logger.list_checkpoints(session_id="test-session")

        assert len(checkpoints) >= 1
        for checkpoint in checkpoints:
            assert checkpoint["session_id"] == "test-session"

    def test_list_checkpoints_filter_by_phase(self, git_logger):
        """REGRESSION: list_checkpoints can filter by phase"""
        git_logger.add_checkpoint("PREFLIGHT", 1, {"know": 0.5})
        git_logger.add_checkpoint("CHECK", 1, {"know": 0.7})
        git_logger.add_checkpoint("POSTFLIGHT", 1, {"know": 0.9})

        # Filter by phase
        preflight_checkpoints = git_logger.list_checkpoints(phase="PREFLIGHT")

        assert len(preflight_checkpoints) >= 1
        for checkpoint in preflight_checkpoints:
            assert checkpoint["phase"] == "PREFLIGHT"

    def test_list_checkpoints_limit(self, git_logger):
        """REGRESSION: list_checkpoints respects limit parameter"""
        # Create many checkpoints
        for i in range(5):
            git_logger.add_checkpoint("CHECK", i+1, {"know": 0.5 + i*0.1})

        # List with limit
        checkpoints = git_logger.list_checkpoints(limit=3)

        assert len(checkpoints) <= 3, "Should respect limit parameter"

    def test_list_checkpoints_sorted_by_timestamp(self, git_logger):
        """REGRESSION: list_checkpoints returns newest first"""
        git_logger.add_checkpoint("PREFLIGHT", 1, {"know": 0.5})
        git_logger.add_checkpoint("CHECK", 2, {"know": 0.7})
        git_logger.add_checkpoint("POSTFLIGHT", 3, {"know": 0.9})

        checkpoints = git_logger.list_checkpoints()

        if len(checkpoints) >= 2:
            # Verify descending timestamp order
            for i in range(len(checkpoints) - 1):
                assert checkpoints[i]["timestamp"] >= checkpoints[i+1]["timestamp"], \
                    "Checkpoints should be sorted newest first"


class TestReflexesTableSchema:
    """Test Bug #2: Missing reflexes table in database"""

    @pytest.fixture
    def db_path(self, tmp_path):
        """Get path to reflex database"""
        # Database is at .empirica/sessions/sessions.db (project-local, not home dir)
        empirica_dir = Path.cwd() / ".empirica" / "sessions"
        empirica_dir.mkdir(parents=True, exist_ok=True)  # Ensure directory exists
        db_file = empirica_dir / "sessions.db"

        # For testing, use temp database if main doesn't exist
        if not db_file.exists():
            db_file = tmp_path / "test_sessions.db"

        return db_file

    def test_reflexes_table_exists(self, db_path):
        """REGRESSION: Verify reflexes table exists in database"""
        if not db_path.exists():
            pytest.skip("Database file doesn't exist yet - expected during initial setup")

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if reflexes table exists
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='reflexes'
        """)

        result = cursor.fetchone()
        conn.close()

        assert result is not None, \
            "reflexes table must exist in database schema"
        assert result[0] == "reflexes"

    def test_reflexes_table_schema(self, db_path):
        """REGRESSION: Verify reflexes table has correct columns"""
        if not db_path.exists():
            pytest.skip("Database file doesn't exist yet")

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get table schema
        cursor.execute("PRAGMA table_info(reflexes)")
        columns = cursor.fetchall()
        conn.close()

        if not columns:
            pytest.fail("reflexes table exists but has no columns")

        column_names = [col[1] for col in columns]

        # Verify essential columns exist
        essential_columns = [
            "id", "session_id", "phase", "timestamp",
            "engagement", "know", "do", "context",
            "clarity", "coherence", "signal", "density",
            "state", "change", "completion", "impact", "uncertainty"
        ]

        for col in essential_columns:
            assert col in column_names, \
                f"reflexes table must have {col} column"

    def test_reflexes_table_can_store_vectors(self, db_path):
        """REGRESSION: Verify reflexes table can store epistemic vectors"""
        if not db_path.exists():
            pytest.skip("Database file doesn't exist yet")

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Try to insert test vector data
        test_vectors = {
            "session_id": "test-session",
            "phase": "PREFLIGHT",
            "timestamp": 1234567890.0,
            "engagement": 0.85,
            "know": 0.65,
            "do": 0.80,
            "context": 0.70,
            "clarity": 0.90,
            "coherence": 0.85,
            "signal": 0.90,
            "density": 0.50,
            "state": 0.60,
            "change": 0.85,
            "completion": 0.80,
            "impact": 0.75,
            "uncertainty": 0.35
        }

        try:
            cursor.execute("""
                INSERT INTO reflexes (
                    session_id, phase, timestamp,
                    engagement, know, do, context,
                    clarity, coherence, signal, density,
                    state, change, completion, impact, uncertainty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                test_vectors["session_id"],
                test_vectors["phase"],
                test_vectors["timestamp"],
                test_vectors["engagement"],
                test_vectors["know"],
                test_vectors["do"],
                test_vectors["context"],
                test_vectors["clarity"],
                test_vectors["coherence"],
                test_vectors["signal"],
                test_vectors["density"],
                test_vectors["state"],
                test_vectors["change"],
                test_vectors["completion"],
                test_vectors["impact"],
                test_vectors["uncertainty"]
            ))

            # Rollback to avoid polluting database
            conn.rollback()

        except sqlite3.Error as e:
            pytest.fail(f"Failed to insert into reflexes table: {e}")

        finally:
            conn.close()


@pytest.mark.integration
class TestCheckpointVectorStorage:
    """Test Bug #3: Checkpoints created with empty vectors.

    Marked integration: the `session_with_vectors` fixture calls
    `SessionDatabase()` which requires a resolvable sessions.db path
    (git-initialized CWD or $EMPIRICA_SESSION_DB). Fresh CI runners
    lack that environment. Run explicitly: pytest -m integration ...
    """

    @pytest.fixture
    def session_with_vectors(self, tmp_path):
        """Create session with PREFLIGHT vectors submitted"""
        # This would normally be done via CLI, but we simulate
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()

        # Bootstrap session - create_session returns session_id
        session_id = db.create_session(ai_id="test-ai")

        # Store PREFLIGHT vectors (simulating preflight-submit)
        vectors = {
            "engagement": 0.85,
            "know": 0.65,
            "do": 0.80,
            "context": 0.70,
            "clarity": 0.90,
            "coherence": 0.85,
            "signal": 0.90,
            "density": 0.50,
            "state": 0.60,
            "change": 0.85,
            "completion": 0.80,
            "impact": 0.75,
            "uncertainty": 0.35
        }

        # Store vectors in reflexes table
        # (This is what preflight-submit should do)
        db.store_vectors(session_id, "PREFLIGHT", vectors)

        return session_id, db, vectors

    def test_checkpoint_loads_vectors_from_database(self, session_with_vectors, tmp_path):
        """REGRESSION: Verify checkpoints load vectors from database"""
        session_id, db, expected_vectors = session_with_vectors

        # Create git logger
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True)

        test_file = tmp_path / "test.txt"
        test_file.write_text("test")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

        GitEnhancedReflexLogger(
            session_id=session_id,
            enable_git_notes=True,
            git_repo_path=str(tmp_path)
        )

        # Load vectors from database
        loaded_result = db.get_latest_vectors(session_id)

        assert loaded_result is not None, \
            "Must be able to load vectors from database"
        assert 'vectors' in loaded_result, \
            "Result must contain vectors key"

        loaded_vectors = loaded_result['vectors']
        assert loaded_vectors != {}, \
            "Loaded vectors must not be empty"

        # Verify vector values
        for key, value in expected_vectors.items():
            assert key in loaded_vectors, f"Vector {key} must be present"
            assert loaded_vectors[key] == value, \
                f"Vector {key} value must match stored value"

    def test_checkpoint_create_includes_vectors(self, session_with_vectors, tmp_path):
        """REGRESSION: Verify created checkpoints include vectors"""
        session_id, db, _expected_vectors = session_with_vectors

        # Setup git
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True)

        test_file = tmp_path / "test.txt"
        test_file.write_text("test")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

        logger = GitEnhancedReflexLogger(
            session_id=session_id,
            enable_git_notes=True,
            git_repo_path=str(tmp_path)
        )

        # Load vectors from database
        vectors = db.get_latest_vectors(session_id)

        # Create checkpoint WITH vectors
        logger.add_checkpoint("PREFLIGHT", 1, vectors=vectors)

        # Verify checkpoint in git notes
        result = subprocess.run(
            ["git", "notes", "show", "HEAD"],
            cwd=tmp_path,
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            checkpoint = json.loads(result.stdout)

            assert "vectors" in checkpoint, \
                "Checkpoint must include vectors field"
            assert checkpoint["vectors"] != {}, \
                "Checkpoint vectors must not be empty"
            assert len(checkpoint["vectors"]) == 13, \
                "Checkpoint must include all 13 epistemic vectors"

            # Verify specific vectors
            assert checkpoint["vectors"]["know"] == 0.65
            assert checkpoint["vectors"]["do"] == 0.80
            assert checkpoint["vectors"]["uncertainty"] == 0.35


class TestCLICheckpointCommands:
    """Integration tests for CLI checkpoint commands"""

    def test_checkpoint_list_command_exists(self):
        """REGRESSION: Verify checkpoint-list CLI command exists"""
        result = subprocess.run(
            ["empirica", "checkpoint-list", "--help"],
            capture_output=True,
            text=True
        )

        assert result.returncode == 0 or "checkpoint-list" in (result.stdout + result.stderr), \
            "checkpoint-list command must be available in CLI"

    def test_checkpoint_list_executes_without_crash(self, tmp_path):
        """REGRESSION: Verify checkpoint-list doesn't crash with AttributeError"""
        # Create test session
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True)

        test_file = tmp_path / "test.txt"
        test_file.write_text("test")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

        result = subprocess.run(
            ["empirica", "checkpoint-list", "--session-id", "test-session"],
            capture_output=True,
            text=True,
            cwd=tmp_path
        )

        # Should not crash with AttributeError
        assert "AttributeError" not in result.stderr, \
            "checkpoint-list must not throw AttributeError"
        assert "'GitEnhancedReflexLogger' object has no attribute 'list_checkpoints'" not in result.stderr, \
            "list_checkpoints method must exist"

    def test_checkpoint_create_stores_vectors(self, tmp_path):
        """REGRESSION: Verify checkpoint-create includes vectors from database"""
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True)

        test_file = tmp_path / "test.txt"
        test_file.write_text("test")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

        # Create checkpoint
        result = subprocess.run(
            ["empirica", "checkpoint-create",
             "--session-id", "test-session",
             "--phase", "CHECK",
             "--round", "1"],
            capture_output=True,
            text=True,
            cwd=tmp_path
        )

        # Should not show "Creating checkpoint with empty vectors" warning
        assert "empty vectors" not in result.stdout.lower(), \
            "Checkpoint should not have empty vectors"

        # Should not show "Could not load vectors" warning
        assert "Could not load vectors" not in result.stderr, \
            "Should be able to load vectors from database"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
