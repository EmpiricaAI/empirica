"""
Pytest configuration and shared fixtures for Empirica tests

This file provides common fixtures and configuration for all Empirica tests,
following patterns from Pydantic AI's testing approach.
"""

import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# =============================================================================
# Instance Isolation: Prevent tests from corrupting live Empirica state
# =============================================================================
#
# Root cause (2026-04-09, KNOWN_ISSUES 11.17 + handoff):
# Tests inherit TMUX_PANE from the test runner. Subprocess-spawned `empirica`
# commands resolve get_instance_id() → same tmux pane → write test session IDs
# into ~/.empirica/instance_projects/{pane}.json, overwriting the LIVE session.
# This breaks ALL Claude instances on that pane.
#
# Fix: Set EMPIRICA_INSTANCE_ID (priority 1 in get_instance_id(), overrides
# TMUX_PANE at priority 2) to a test-specific value. Also strip all terminal
# identity vars and set EMPIRICA_HEADLESS=true. Tests that need DB access
# should use EMPIRICA_SESSION_DB (priority 0 in get_session_db_path()) per
# the pattern in test_ai_agent_workflow.py (11.17 fix).

@pytest.fixture(autouse=True, scope="session")
def isolate_empirica_instance():
    """Prevent tests from polluting live Empirica instance state.

    Sets EMPIRICA_INSTANCE_ID to a test-specific value (priority 1 in
    get_instance_id), overriding TMUX_PANE. Strips all terminal identity
    vars as belt-and-suspenders. Also backs up and restores active_transaction
    files for extra safety.

    See: docs/architecture/instance_isolation/KNOWN_ISSUES.md #11.17
    See: .empirica/handoffs/transaction-pollution-fix.md
    """
    import glob

    test_instance_id = f"test-{os.getpid()}"

    # Save and strip terminal identity vars
    saved_env = {}
    identity_vars = (
        "TMUX", "TMUX_PANE", "WINDOWID", "TERM_SESSION_ID",
        "EMPIRICA_INSTANCE_ID", "EMPIRICA_HEADLESS",
    )
    for var in identity_vars:
        saved_env[var] = os.environ.get(var)

    # Set test-specific instance identity (priority 1 in get_instance_id)
    os.environ["EMPIRICA_INSTANCE_ID"] = test_instance_id
    os.environ["EMPIRICA_HEADLESS"] = "true"

    # Strip terminal vars so subprocesses don't inherit them.
    # CRITICAL: TMUX is included — without it, any test that subshells `tmux`
    # without a -t target (e.g. `tmux detach-client`) acts on the user's
    # live tmux server. test_detach_handler_writes_clean_marker actually
    # detached the dev's live tmux session before this was added.
    for var in ("TMUX", "TMUX_PANE", "WINDOWID", "TERM_SESSION_ID"):
        os.environ.pop(var, None)

    # Belt-and-suspenders: back up active_transaction files
    backup = {}
    patterns = [
        str(Path.home() / '.empirica' / 'active_transaction*.json'),
        str(Path.cwd() / '.empirica' / 'active_transaction*.json'),
    ]
    for pattern in patterns:
        for filepath in glob.glob(pattern):
            try:
                with open(filepath) as f:
                    backup[filepath] = f.read()
            except Exception:
                pass

    yield

    # Restore environment
    for var in identity_vars:
        if saved_env[var] is not None:
            os.environ[var] = saved_env[var]
        else:
            os.environ.pop(var, None)

    # Restore backed-up transaction files
    for filepath, contents in backup.items():
        try:
            with open(filepath, 'w') as f:
                f.write(contents)
        except Exception:
            pass


# =============================================================================
# Fixtures: Temporary Directories
# ============================================================================

@pytest.fixture
def temp_empirica_dir() -> Iterator[Path]:
    """Create temporary .empirica directory for testing"""
    with tempfile.TemporaryDirectory() as tmpdir:
        empirica_path = Path(tmpdir) / ".empirica"
        empirica_path.mkdir()
        (empirica_path / "sessions").mkdir()

        # Create credentials template
        creds_template = empirica_path / "credentials.yaml.template"
        creds_template.write_text("""# Empirica Credentials Template
# Copy to credentials.yaml and add your API keys

# OpenAI
openai_api_key: \"your-key-here\"

# Anthropic
anthropic_api_key: \"your-key-here\"
"""
        )

        yield empirica_path


@pytest.fixture
def temp_reflex_logs_dir() -> Iterator[Path]:
    """Create temporary reflex logs directory for testing"""
    with tempfile.TemporaryDirectory() as tmpdir:
        logs_path = Path(tmpdir) / ".empirica_reflex_logs"
        logs_path.mkdir()
        (logs_path / "cascade").mkdir()
        yield logs_path


# =============================================================================
# Fixtures: Database
# ============================================================================

@pytest.fixture
def temp_session_db(temp_empirica_dir):
    """Create temporary session database for testing"""
    from empirica.data.session_database import SessionDatabase

    db_path = temp_empirica_dir / "sessions" / "test.db"
    db = SessionDatabase(db_path=str(db_path))

    yield db

    db.close()


# =============================================================================
# Fixtures: Sample Data
# ============================================================================

@pytest.fixture
def sample_assessment_response() -> dict[str, Any]:
    """Sample genuine assessment response for testing"""
    return {
        "engagement": {
            "score": 0.8,
            "rationale": "Genuine collaborative intelligence with user"
        },
        "foundation": {
            "know": {
                "score": 0.6,
                "rationale": "Moderate domain knowledge of the task area"
            },
            "do": {
                "score": 0.7,
                "rationale": "Good execution capability for this type of task"
            },
            "context": {
                "score": 0.5,
                "rationale": "Adequate environmental context, but some gaps"
            }
        },
        "comprehension": {
            "clarity": {
                "score": 0.8,
                "rationale": "Clear semantic understanding of requirements"
            },
            "coherence": {
                "score": 0.7,
                "rationale": "Coherent grasp of context and relationships"
            },
            "signal": {
                "score": 0.6,
                "rationale": "Key priority signals identified"
            },
            "density": {
                "score": 0.4,
                "rationale": "Manageable cognitive complexity"
            }
        },
        "execution": {
            "state": {
                "score": 0.5,
                "rationale": "Basic environment state mapping"
            },
            "change": {
                "score": 0.6,
                "rationale": "Can track modification trajectories"
            },
            "completion": {
                "score": 0.7,
                "rationale": "Clear path to completion criteria"
            },
            "impact": {
                "score": 0.6,
                "rationale": "Understand consequence propagation"
            }
        },
        "uncertainty": {
            "score": 0.4,
            "rationale": "Moderate uncertainty about edge cases and unknowns"
        }
    }


@pytest.fixture
def sample_preflight_vectors() -> dict[str, float]:
    """Sample preflight vector scores for testing"""
    return {
        "know": 0.5,
        "do": 0.6,
        "context": 0.4,
        "clarity": 0.7,
        "coherence": 0.6,
        "signal": 0.5,
        "density": 0.5,
        "state": 0.4,
        "change": 0.5,
        "completion": 0.6,
        "impact": 0.5,
        "engagement": 0.7,
        "uncertainty": 0.5
    }


@pytest.fixture
def sample_postflight_vectors() -> dict[str, float]:
    """Sample postflight vector scores (showing learning)"""
    return {
        "know": 0.7,  # Increased
        "do": 0.7,    # Increased
        "context": 0.6,  # Increased
        "clarity": 0.8,  # Increased
        "coherence": 0.7,
        "signal": 0.6,
        "density": 0.5,
        "state": 0.6,  # Increased
        "change": 0.6,
        "completion": 0.7,
        "impact": 0.6,
        "engagement": 0.8,  # Increased
        "uncertainty": 0.3  # Decreased (more confident)
    }


# =============================================================================
# Fixtures: Test Helpers
# ============================================================================

@pytest.fixture
def mock_llm_response():
    """Factory fixture for creating mock LLM responses"""
    def _make_response(vectors: dict[str, float]) -> str:
        """Create a mock LLM response with given vectors"""
        response = {
            "engagement": {
                "score": vectors.get("engagement", 0.7),
                "rationale": "Mock engagement rationale"
            },
            "foundation": {
                "know": {
                    "score": vectors.get("know", 0.5),
                    "rationale": "Mock knowledge rationale"
                },
                "do": {
                    "score": vectors.get("do", 0.5),
                    "rationale": "Mock capability rationale"
                },
                "context": {
                    "score": vectors.get("context", 0.5),
                    "rationale": "Mock context rationale"
                }
            },
            "comprehension": {
                "clarity": {
                    "score": vectors.get("clarity", 0.5),
                    "rationale": "Mock clarity rationale"
                },
                "coherence": {
                    "score": vectors.get("coherence", 0.5),
                    "rationale": "Mock coherence rationale"
                },
                "signal": {
                    "score": vectors.get("signal", 0.5),
                    "rationale": "Mock signal rationale"
                },
                "density": {
                    "score": vectors.get("density", 0.5),
                    "rationale": "Mock density rationale"
                }
            },
            "execution": {
                "state": {
                    "score": vectors.get("state", 0.5),
                    "rationale": "Mock state rationale"
                },
                "change": {
                    "score": vectors.get("change", 0.5),
                    "rationale": "Mock change rationale"
                },
                "completion": {
                    "score": vectors.get("completion", 0.5),
                    "rationale": "Mock completion rationale"
                },
                "impact": {
                    "score": vectors.get("impact", 0.5),
                    "rationale": "Mock impact rationale"
                }
            },
            "uncertainty": {
                "score": vectors.get("uncertainty", 0.5),
                "rationale": "Mock uncertainty rationale"
            }
        }
        return json.dumps(response)

    return _make_response


# =============================================================================
# Pytest Configuration
# ============================================================================

def pytest_configure(config):
    """Register custom markers"""
    config.addinivalue_line(
        "markers",
        "integration: Integration tests (deselect with '-m \"not integration\"')"
    )
    config.addinivalue_line(
        "markers",
        "integrity: Framework integrity tests (core principle validation)"
    )
    config.addinivalue_line(
        "markers",
        "slow: Slow running tests"
    )
    config.addinivalue_line(
        "markers",
        "requires_mcp: Tests that require MCP server running"
    )


@pytest.fixture(autouse=True)
def cleanup_test_artifacts():
    """Automatically clean up test artifacts after each test"""
    yield

    # Clean up any test databases
    test_dbs = Path(".").glob("test*.db")
    for db in test_dbs:
        try:
            db.unlink()
        except Exception:
            pass

    # Clean up test reflex logs
    test_logs = Path(".empirica_reflex_logs_test")
    if test_logs.exists():
        shutil.rmtree(test_logs, ignore_errors=True)


# =============================================================================
# Assertion Helpers (inspired by Pydantic AI's dirty-equals usage)
# ============================================================================

@pytest.fixture
def assert_vectors_valid():
    """Helper to assert epistemic vectors are valid"""
    def _assert(vectors: dict[str, float]):
        """Assert all vector scores are in valid range [0.0, 1.0]"""
        required_vectors = [
            "know", "do", "context",
            "clarity", "coherence", "signal", "density",
            "state", "change", "completion", "impact",
            "engagement", "uncertainty"
        ]

        for vector in required_vectors:
            assert vector in vectors, f"Missing vector: {vector}"
            score = vectors[vector]
            assert isinstance(score, (int, float)), f"{vector} score must be numeric"
            assert 0.0 <= score <= 1.0, f"{vector} score must be in [0.0, 1.0], got {score}"

    return _assert


@pytest.fixture
def assert_genuine_assessment():
    """Helper to assert assessment contains genuine rationale"""
    def _assert(assessment_dict: dict[str, Any]):
        """Assert assessment contains genuine rationale, not template text"""
        # Check engagement
        assert "engagement" in assessment_dict
        assert "rationale" in assessment_dict["engagement"]
        engagement_rationale = assessment_dict["engagement"]["rationale"]
        assert len(engagement_rationale) > 10, "Rationale too short to be genuine"

        # Check foundation vectors have rationale
        for vector in ["know", "do", "context"]:
            assert vector in assessment_dict["foundation"]
            assert "rationale" in assessment_dict["foundation"][vector]
            rationale = assessment_dict["foundation"][vector]["rationale"]
            assert len(rationale) > 10, f"{vector} rationale too short"
            # Ensure it's not just template text
            assert rationale.lower() not in [
                "adequate",
                "sufficient",
                "baseline",
                "default"
            ], f"{vector} rationale appears to be template text"

    return _assert


# Exclude archived tests from collection
collect_ignore_glob = ['_archive/**']
