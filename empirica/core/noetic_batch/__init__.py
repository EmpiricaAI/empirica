"""Batched investigation primitive — single tool call replaces N round-trips.

The Sentinel sees one noetic intent instead of N individual reads/greps/globs,
eliminating gating overhead and keeping the TUI signal-to-noise high. Same
architectural pattern as `cortex_log_artifacts`: a graph schema replacing
N individual logging calls.

Public API:
    run_batch(payload: dict) -> dict
    NoeticBatchInput  (pydantic model)
    NoeticBatchResult (pydantic model)
    SCHEMA_VERSION

See docs/architecture/NOETIC_BATCH_SPEC.md for the full design.
"""

from .executor import run_batch
from .schema import (
    SCHEMA_VERSION,
    GlobOperation,
    GrepOperation,
    InvestigateOperation,
    NoeticBatchInput,
    NoeticBatchResult,
    ReadOperation,
)

__all__ = [
    "SCHEMA_VERSION",
    "GlobOperation",
    "GrepOperation",
    "InvestigateOperation",
    "NoeticBatchInput",
    "NoeticBatchResult",
    "ReadOperation",
    "run_batch",
]
