"""Phase 2 T3 tests for the POSTFLIGHT coverage block (paper section 4.1).

Covers:
- Validation accepts the documented coverage shape (file/artifact/citation/etc.)
- Validation accepts free-form keys (forward compat)
- Validation accepts coverage absent (backward compat)
- _postflight_parse_config_or_legacy round-trips coverage from JSON config
- _build_postflight_result echoes coverage when present, omits when absent

End-to-end persistence + echo is exercised separately via the smoke test in
the dev session — these tests cover the unit-level invariants.
"""

from __future__ import annotations

import pytest

from empirica.cli.validation import PostflightInput

# ── Validation ──────────────────────────────────────────────────────────


class TestPostflightInputCoverage:
    def test_coverage_absent_validates(self):
        result = PostflightInput.model_validate(
            {
                "session_id": "s1",
                "vectors": {"know": 0.8},
            }
        )
        assert result.coverage is None

    def test_coverage_documented_shape_validates(self):
        result = PostflightInput.model_validate(
            {
                "session_id": "s1",
                "vectors": {"know": 0.8},
                "coverage": {
                    "files_inspected": 12,
                    "files_relevant": 491,
                    "artifacts_inspected": 5,
                    "artifacts_relevant": 50,
                    "citations_made": 3,
                    "citations_available": 52,
                    "subagents_dispatched": 0,
                    "subagents_relevant": 0,
                    "tools_invoked": 7,
                    "tools_available": 12,
                    "scalar": 0.07,
                    "notes": "auditor over scanner snapshot",
                },
            }
        )
        assert result.coverage is not None
        assert result.coverage["files_inspected"] == 12
        assert result.coverage["scalar"] == 0.07

    def test_coverage_free_form_keys_preserved(self):
        """Forward-compat — extra keys flow through untouched."""
        result = PostflightInput.model_validate(
            {
                "session_id": "s1",
                "vectors": {"know": 0.5},
                "coverage": {
                    "files_inspected": 1,
                    "custom_dimension_x": "experimental",
                    "nested": {"a": 1, "b": [2, 3]},
                },
            }
        )
        assert result.coverage["custom_dimension_x"] == "experimental"
        assert result.coverage["nested"]["b"] == [2, 3]

    def test_coverage_must_be_dict_when_present(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PostflightInput.model_validate(
                {
                    "session_id": "s1",
                    "vectors": {"know": 0.5},
                    "coverage": "not a dict",
                }
            )


# ── Parser tuple shape ──────────────────────────────────────────────────


class TestPostflightParserCoverage:
    """Verify the parser surfaces coverage from JSON config to the dict."""

    def test_parsed_tuple_contains_coverage(self):
        import argparse
        import io
        import json
        import sys

        from empirica.cli.command_handlers.workflow_commands import (
            _postflight_parse_config_or_legacy,
        )

        config_text = json.dumps(
            {
                "session_id": "parser-test-session",
                "vectors": {"know": 0.7},
                "reasoning": "parse test",
                "coverage": {"files_inspected": 1, "files_relevant": 10},
            }
        )

        old_stdin = sys.stdin
        sys.stdin = io.StringIO(config_text)
        try:
            args = argparse.Namespace(
                config="-",
                session_id=None,
                vectors=None,
                reasoning=None,
                output="json",
            )
            (
                session_id,
                vectors,
                _reasoning,
                _grounded_vectors,
                _grounded_rationale,
                coverage,
                _claims,
                output_format,
            ) = _postflight_parse_config_or_legacy(args)
        finally:
            sys.stdin = old_stdin

        assert session_id == "parser-test-session"
        assert vectors == {"know": 0.7}
        assert coverage == {"files_inspected": 1, "files_relevant": 10}
        assert output_format == "json"


# ── Result builder echo ─────────────────────────────────────────────────


class TestBuildPostflightResultCoverage:
    """Verify _build_postflight_result echoes coverage when present."""

    @staticmethod
    def _kwargs():
        return {
            "session_id": "s1",
            "postflight_confidence": 0.9,
            "internal_consistency": "good",
            "deltas": {},
            "trajectory_issues": None,
            "grounded_verification": None,
            "sentinel_decision": None,
            "compliance_result": None,
            "compliance_error": None,
            "postflight_grounded_vectors": None,
            "postflight_grounded_rationale": None,
            "vectors": {"know": 0.9},
            "resolved_project_path": "/tmp",
        }

    def test_coverage_omitted_when_none(self, monkeypatch):
        from empirica.cli.command_handlers import workflow_commands

        # Skip the memory-cache side effect to keep the unit test pure
        monkeypatch.setattr(
            workflow_commands,
            "_postflight_update_memory_hot_cache",
            lambda *a, **kw: None,
        )

        result = workflow_commands._build_postflight_result(**self._kwargs())
        assert "coverage" not in result

    def test_coverage_echoed_when_present(self, monkeypatch):
        from empirica.cli.command_handlers import workflow_commands

        monkeypatch.setattr(
            workflow_commands,
            "_postflight_update_memory_hot_cache",
            lambda *a, **kw: None,
        )

        cov = {"files_inspected": 12, "files_relevant": 491, "scalar": 0.024}
        result = workflow_commands._build_postflight_result(
            **self._kwargs(),
            postflight_coverage=cov,
        )
        assert result["coverage"] == cov

    def test_coverage_falsy_does_not_echo(self, monkeypatch):
        """Empty dict is logically 'no coverage reported' — don't surface it."""
        from empirica.cli.command_handlers import workflow_commands

        monkeypatch.setattr(
            workflow_commands,
            "_postflight_update_memory_hot_cache",
            lambda *a, **kw: None,
        )

        result = workflow_commands._build_postflight_result(
            **self._kwargs(),
            postflight_coverage={},
        )
        assert "coverage" not in result
