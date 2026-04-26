"""Token / byte / count budgets for noetic batch operations.

A poorly-bounded batch could return 100MB of file contents and crash the
context window. Each operation type has its own per-op cap; the executor
also enforces a total response cap.

Override via BatchBudgets(**kwargs) in tests; production uses defaults.
"""

from __future__ import annotations

from dataclasses import dataclass

# Per-op defaults. Hard caps are enforced separately in schema validation.
DEFAULT_MAX_FILE_BYTES = 50 * 1024          # 50KB
DEFAULT_MAX_GREP_MATCHES = 100
DEFAULT_MAX_GLOB_FILES = 200
DEFAULT_MAX_INVESTIGATE_RESULTS = 5
DEFAULT_MAX_TOTAL_BYTES = 200 * 1024        # 200KB

# Schema-level hard caps (refuse input that exceeds these — see schema.py)
HARD_CAP_FILE_BYTES = 1024 * 1024           # 1MB
HARD_CAP_GREP_MATCHES = 500
HARD_CAP_GLOB_FILES = 1000
HARD_CAP_INVESTIGATE_RESULTS = 20
HARD_CAP_TOTAL_BYTES = 2 * 1024 * 1024      # 2MB


@dataclass
class BatchBudgets:
    """Mutable per-batch budget overrides.

    Defaults match the DEFAULT_* constants. The executor passes a single
    instance through all operations. Hard caps from schema.py are enforced
    earlier, so values here can be smaller than the hard cap but never
    larger (clamped at construction).
    """

    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_grep_matches: int = DEFAULT_MAX_GREP_MATCHES
    max_glob_files: int = DEFAULT_MAX_GLOB_FILES
    max_investigate_results: int = DEFAULT_MAX_INVESTIGATE_RESULTS
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES

    def __post_init__(self) -> None:
        self.max_file_bytes = min(self.max_file_bytes, HARD_CAP_FILE_BYTES)
        self.max_grep_matches = min(self.max_grep_matches, HARD_CAP_GREP_MATCHES)
        self.max_glob_files = min(self.max_glob_files, HARD_CAP_GLOB_FILES)
        self.max_investigate_results = min(self.max_investigate_results, HARD_CAP_INVESTIGATE_RESULTS)
        self.max_total_bytes = min(self.max_total_bytes, HARD_CAP_TOTAL_BYTES)
