"""Tests for empirica.core.noetic_batch.schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from empirica.core.noetic_batch.schema import (
    SCHEMA_VERSION,
    GlobOperation,
    GrepOperation,
    InvestigateOperation,
    NoeticBatchInput,
    ReadOperation,
)


def test_minimal_valid_batch():
    batch = NoeticBatchInput(intent="check auth", reads=[ReadOperation(path="src/auth.py")])
    assert batch.schema_version == SCHEMA_VERSION
    assert batch.operation_count() == 1


def test_intent_required():
    with pytest.raises(ValidationError):
        NoeticBatchInput(reads=[ReadOperation(path="x")])  # type: ignore[call-arg]


def test_intent_max_length():
    with pytest.raises(ValidationError):
        NoeticBatchInput(intent="x" * 501)


def test_read_path_required():
    with pytest.raises(ValidationError):
        ReadOperation(path="")


def test_read_lines_valid_forms():
    for spec in ("1", "1-10", "1-", "-10"):
        op = ReadOperation(path="x", lines=spec)
        assert op.lines == spec


def test_read_lines_invalid_forms():
    for spec in ("0", "0-10", "abc", "1-2-3", "10-1", "-"):
        with pytest.raises(ValidationError):
            ReadOperation(path="x", lines=spec)


def test_grep_pattern_required():
    with pytest.raises(ValidationError):
        GrepOperation(pattern="")


def test_grep_defaults():
    op = GrepOperation(pattern="foo")
    assert op.glob == "**/*"
    assert op.context == 0
    assert op.case_sensitive is False
    assert op.max_matches == 100


def test_grep_context_bounded():
    GrepOperation(pattern="x", context=5)
    with pytest.raises(ValidationError):
        GrepOperation(pattern="x", context=6)


def test_grep_max_matches_hard_capped():
    GrepOperation(pattern="x", max_matches=500)
    with pytest.raises(ValidationError):
        GrepOperation(pattern="x", max_matches=501)


def test_glob_string_shorthand():
    """Bare strings in `globs` should be normalized to GlobOperation."""
    batch = NoeticBatchInput(intent="x", globs=["src/**/*.py"])
    assert len(batch.globs) == 1
    assert isinstance(batch.globs[0], GlobOperation)
    assert batch.globs[0].pattern == "src/**/*.py"


def test_glob_dict_shorthand():
    batch = NoeticBatchInput(
        intent="x",
        globs=[{"pattern": "src/**/*.py", "root": "/tmp"}],  # type: ignore[list-item]
    )
    assert batch.globs[0].pattern == "src/**/*.py"
    assert batch.globs[0].root == "/tmp"


def test_investigate_scope_validated():
    InvestigateOperation(query="x", scope="project")
    InvestigateOperation(query="x", scope="session")
    InvestigateOperation(query="x", scope="global")
    with pytest.raises(ValidationError):
        InvestigateOperation(query="x", scope="invalid")  # type: ignore[arg-type]


def test_investigate_limit_hard_capped():
    InvestigateOperation(query="x", limit=20)
    with pytest.raises(ValidationError):
        InvestigateOperation(query="x", limit=21)


def test_operation_count_aggregates():
    batch = NoeticBatchInput(
        intent="x",
        reads=[ReadOperation(path="a"), ReadOperation(path="b")],
        greps=[GrepOperation(pattern="x")],
        globs=["a"],
        investigate=[InvestigateOperation(query="q")],
    )
    assert batch.operation_count() == 5


def test_empty_batch_allowed_but_useless():
    """Schema permits zero operations (intent-only); executor handles gracefully."""
    batch = NoeticBatchInput(intent="just thinking")
    assert batch.operation_count() == 0


def test_schema_version_default():
    batch = NoeticBatchInput(intent="x")
    assert batch.schema_version == "1"


def test_schema_version_explicit():
    batch = NoeticBatchInput(schema_version="1", intent="x")
    assert batch.schema_version == "1"
