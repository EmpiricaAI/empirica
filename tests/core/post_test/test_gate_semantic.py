"""
Tests for the meta uncertainty gate semantic.

Background (2026-04-07):
The Sentinel CHECK gate previously required (know >= 0.70 AND uncertainty <= 0.35).
The know condition was 'based on vibes' per David and made the gate gameable —
an AI could inflate know to bypass while uncertainty drifted high.

Per the meta-uncertainty design, uncertainty IS the unified confidence summary
that subsumes the AI's epistemic state across all 12 other vectors. The gate
should use ONLY meta uncertainty as the threshold:

  proceed if uncertainty <= ready_uncertainty_threshold

Gaming resistance: an AI that inflates other vectors but reports low
uncertainty passes the gate, but the POSTFLIGHT meta-uncertainty derivation
(mapper.py:_compute_meta_uncertainty) compares the self-reported uncertainty
to a value computed from gap magnitudes of the OTHER grounded vectors.
Inflation produces large gaps which produce high meta-uncertainty which
produces a large divergence which surfaces in calibration_trajectory and
overestimate_tendency. Honest measurement is recovered post-hoc.

These tests lock in the new gate semantic by inspecting the source files
where the gate logic lives.
"""

from __future__ import annotations

from pathlib import Path

# Repo root resolved from this file's location so the tests work on any
# checkout path (local dev, CI runner, etc.). tests/core/post_test/this.py
# → parents[3] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]

WORKFLOW_COMMANDS_PATH = REPO_ROOT / "empirica/cli/command_handlers/_workflow_check.py"
SENTINEL_GATE_PATH = REPO_ROOT / "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py"
SENTINEL_HOOKS_PATH = REPO_ROOT / "empirica/core/canonical/empirica_git/sentinel_hooks.py"
ORCHESTRATOR_PATH = REPO_ROOT / "empirica/core/sentinel/orchestrator.py"


def _src(path: Path) -> str:
    return path.read_text()


# ---------------------------------------------------------------------------
# Source-level regression: no gate uses know AND uncertainty compound
# ---------------------------------------------------------------------------


class TestGateSourceRegression:
    """Source files must not contain the old `know >= X and uncertainty <= Y`
    compound gate. The gate is uncertainty-only after 2026-04-07."""

    def test_workflow_commands_main_gate_is_uncertainty_only(self):
        """The CHECK handler's main gate must use uncertainty only."""
        src = _src(WORKFLOW_COMMANDS_PATH)
        # The new condition must be present
        assert "if uncertainty <= ready_uncertainty_threshold:" in src
        # The old compound condition must be gone
        assert "know >= ready_know_threshold and uncertainty <= ready_uncertainty_threshold" not in src

    def test_workflow_commands_diminishing_returns_is_uncertainty_only(self):
        """The diminishing-returns override path must also use uncertainty only."""
        src = _src(WORKFLOW_COMMANDS_PATH)
        # New condition: pure uncertainty check at the diminishing-returns point
        # We look for the literal expression after the comment
        assert "if uncertainty <= 0.45:" in src
        # Old compound must be gone
        assert "know >= 0.60 and uncertainty <= 0.45" not in src

    def test_workflow_commands_round_cap_is_uncertainty_only(self):
        """The round-cap override must use uncertainty only."""
        src = _src(WORKFLOW_COMMANDS_PATH)
        assert "round_num >= 5 and uncertainty <= 0.40" in src
        assert "round_num >= 5 and know >= 0.60 and uncertainty <= 0.40" not in src

    def test_sentinel_gate_remote_check_is_uncertainty_only(self):
        """sentinel-gate.py:_confidence_gate_remote uses uncertainty only."""
        src = _src(SENTINEL_GATE_PATH)
        # Old line was:
        #   if know >= thresholds['know_min'] and uncertainty <= thresholds['uncertainty_max']:
        assert 'know >= thresholds["know_min"] and uncertainty <= thresholds["uncertainty_max"]' not in src
        # New line:
        assert 'if uncertainty <= thresholds["uncertainty_max"]:' in src

    def test_sentinel_hooks_evaluator_is_uncertainty_only(self):
        """sentinel_hooks.py SentinelDecision evaluator uses uncertainty only."""
        src = _src(SENTINEL_HOOKS_PATH)
        # Old line was: if know >= min_know and uncertainty <= max_uncertainty:
        assert "know >= min_know and uncertainty <= max_uncertainty" not in src
        # New line:
        assert "if uncertainty <= max_uncertainty:" in src
        # The "low knowledge with doubt" branch was also removed
        assert "know < 0.5 and uncertainty > 0.5" not in src

    def test_orchestrator_default_gate_is_uncertainty_only(self):
        """sentinel/orchestrator.py default-profile gate uses uncertainty only."""
        src = _src(ORCHESTRATOR_PATH)
        # Old condition removed
        assert "know >= 0.7 and uncertainty <= 0.35" not in src
        # New condition present
        assert "if uncertainty <= 0.35:" in src


# ---------------------------------------------------------------------------
# Display strings reflect the new gate semantic
# ---------------------------------------------------------------------------


class TestDisplayStringsUpdated:
    """Reporting/display strings must show the uncertainty-only gate."""

    def test_monitor_commands_gate_string(self):
        """monitor_commands.py readiness_gate strings should describe the
        new uncertainty-only gate."""
        path = REPO_ROOT / "empirica/cli/command_handlers/monitor_commands.py"
        src = _src(path)
        # Old string is gone
        assert "know >= 0.70 AND uncertainty <= 0.35" not in src
        # At least one new string mentions meta uncertainty as the gate
        assert "uncertainty <= 0.35" in src
        assert "meta uncertainty" in src.lower()
