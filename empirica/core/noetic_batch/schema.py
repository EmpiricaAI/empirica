"""Pydantic models for noetic batch input/output.

Schema version 1. Backward-incompatible changes bump the version.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .budgets import (
    HARD_CAP_FILE_BYTES,
    HARD_CAP_GLOB_FILES,
    HARD_CAP_GREP_MATCHES,
    HARD_CAP_INVESTIGATE_RESULTS,
)

SCHEMA_VERSION = "1"


# =============================================================================
# Input models — what the AI sends
# =============================================================================


class ReadOperation(BaseModel):
    """Read a file (optionally a line range)."""

    path: str = Field(..., min_length=1, description="File path (absolute or relative to project root)")
    lines: str | None = Field(
        None,
        description="Optional line range like 'N-M', 'N-' (from N to end), or '-M' (from 1 to M). 1-indexed inclusive.",
    )

    @field_validator("lines")
    @classmethod
    def _validate_lines(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        # Forms: N, N-M, N-, -M
        parts = v.split("-")
        if len(parts) == 1:
            if not parts[0].isdigit() or int(parts[0]) < 1:
                raise ValueError(f"lines must be a positive int or 'N-M' range; got {v!r}")
            return v
        if len(parts) == 2:
            start, end = parts
            if not start and not end:
                raise ValueError("lines must have at least a start or end (e.g., '1-', '-10', '1-10')")
            if start and (not start.isdigit() or int(start) < 1):
                raise ValueError(f"lines start must be empty or positive int; got {start!r}")
            if end and (not end.isdigit() or int(end) < 1):
                raise ValueError(f"lines end must be empty or positive int; got {end!r}")
            if start and end and int(start) > int(end):
                raise ValueError(f"lines start > end in {v!r}")
            return v
        raise ValueError(f"lines must be 'N', 'N-M', 'N-', or '-M'; got {v!r}")


class GrepOperation(BaseModel):
    """Run a grep across a glob scope."""

    pattern: str = Field(..., min_length=1, description="Regex pattern (POSIX extended)")
    glob: str = Field("**/*", description="Path glob (default: all files under project root)")
    context: int = Field(0, ge=0, le=5, description="Lines of context before/after each match")
    case_sensitive: bool = Field(False, description="Case-sensitive matching")
    max_matches: int = Field(
        100,
        ge=1,
        le=HARD_CAP_GREP_MATCHES,
        description=f"Max matches to return (hard cap: {HARD_CAP_GREP_MATCHES})",
    )


class GlobOperation(BaseModel):
    """Resolve a path glob (list matching files)."""

    pattern: str = Field(..., min_length=1, description="Glob pattern (e.g. 'src/**/*.py')")
    root: str | None = Field(None, description="Optional root dir; defaults to project root")


class InvestigateOperation(BaseModel):
    """Semantic search via empirica project-search."""

    query: str = Field(..., min_length=1, description="Search query")
    scope: Literal["session", "project", "global"] = Field("project", description="Search scope")
    limit: int = Field(
        5,
        ge=1,
        le=HARD_CAP_INVESTIGATE_RESULTS,
        description=f"Max results (hard cap: {HARD_CAP_INVESTIGATE_RESULTS})",
    )


class NoeticBatchInput(BaseModel):
    """A single batched investigation request."""

    schema_version: str = Field(SCHEMA_VERSION, description="Schema version (currently '1')")
    intent: str = Field(..., min_length=1, max_length=500, description="One-line investigation goal")
    reads: list[ReadOperation] = Field(default_factory=list)
    greps: list[GrepOperation] = Field(default_factory=list)
    globs: list[GlobOperation | str] = Field(default_factory=list)
    investigate: list[InvestigateOperation] = Field(default_factory=list)

    @field_validator("globs")
    @classmethod
    def _normalize_globs(cls, v: list[GlobOperation | str]) -> list[GlobOperation]:
        """Allow bare strings as shorthand for {pattern: '...'}."""
        out: list[GlobOperation] = []
        for item in v:
            if isinstance(item, str):
                out.append(GlobOperation(pattern=item))
            elif isinstance(item, GlobOperation):
                out.append(item)
            else:
                out.append(GlobOperation(**item))  # dict
        return out

    def operation_count(self) -> int:
        return len(self.reads) + len(self.greps) + len(self.globs) + len(self.investigate)


# =============================================================================
# Output models — what the executor returns
# =============================================================================


class ReadResult(BaseModel):
    path: str
    lines: str | None = None
    content: str = ""
    size_bytes: int = 0
    truncated: bool = False
    error: str | None = None


class GrepMatch(BaseModel):
    file: str
    line: int
    text: str
    context_before: list[str] = Field(default_factory=list)
    context_after: list[str] = Field(default_factory=list)


class GrepResult(BaseModel):
    pattern: str
    glob: str
    matches: list[GrepMatch] = Field(default_factory=list)
    total_matches: int = 0
    truncated: bool = False
    files_scanned: int = 0
    duration_ms: int = 0
    error: str | None = None


class GlobResult(BaseModel):
    pattern: str
    matches: list[str] = Field(default_factory=list)
    total_matches: int = 0
    truncated: bool = False
    error: str | None = None


class InvestigateResult(BaseModel):
    query: str
    scope: str
    results: list[dict] = Field(default_factory=list)
    truncated: bool = False
    error: str | None = None


class BatchSummary(BaseModel):
    total_files_read: int = 0
    total_grep_matches: int = 0
    total_globs_resolved: int = 0
    total_investigate_results: int = 0
    duration_ms: int = 0
    approx_tokens: int = 0


class NoeticBatchResult(BaseModel):
    ok: bool = True
    schema_version: str = SCHEMA_VERSION
    intent: str
    reads: list[ReadResult] = Field(default_factory=list)
    greps: list[GrepResult] = Field(default_factory=list)
    globs: list[GlobResult] = Field(default_factory=list)
    investigate: list[InvestigateResult] = Field(default_factory=list)
    summary: BatchSummary = Field(default_factory=BatchSummary)
    error: str | None = None  # populated only on top-level fatal failure


__all__ = [
    "HARD_CAP_FILE_BYTES",
    "HARD_CAP_GLOB_FILES",
    "HARD_CAP_GREP_MATCHES",
    "HARD_CAP_INVESTIGATE_RESULTS",
    "SCHEMA_VERSION",
    "BatchSummary",
    "GlobOperation",
    "GlobResult",
    "GrepMatch",
    "GrepOperation",
    "GrepResult",
    "InvestigateOperation",
    "InvestigateResult",
    "NoeticBatchInput",
    "NoeticBatchResult",
    "ReadOperation",
    "ReadResult",
]
