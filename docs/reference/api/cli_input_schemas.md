# CLI Input Schemas and Helper Classes

**Module:** `empirica.cli.validation`, `empirica.cli.command_handlers.*`, `empirica.cli.cli_core`
**Category:** CLI Reference
**Stability:** Production Ready

---

## Overview

This document covers the Pydantic input schemas used to validate JSON
payloads on stdin to Empirica CLI commands, plus the dataclass and
helper classes used internally by the CLI command handlers (vision,
docs assessment, monitoring, help formatting).

The Pydantic models live in `empirica/cli/validation.py` and are the
authoritative schemas for the AI-first stdin JSON mode of every
workflow command (`preflight-submit -`, `check-submit -`,
`postflight-submit -`, `finding-log -`, `unknown-log -`, etc).

---

## Pydantic Input Schemas

### `VectorValues`

Epistemic vector values for AI self-assessment (0.0–1.0 scale).

Captures the 13-vector epistemic state used throughout Empirica's
measurement workflow. Only `know` and `uncertainty` are required; all
other vectors are optional and default to None.

**Vector groups:**
- **Knowledge axis** — `know`, `uncertainty`, `signal`, `density`
- **Context axis** — `context`, `clarity`, `coherence`, `state`
- **Action axis** — `change`, `completion`, `do`
- **Engagement axis** — `engagement`, `impact`

Used by `PreflightInput`, `CheckInput`, and `PostflightInput`.

### `PreflightInput`

Pydantic input schema for the `preflight-submit` CLI command.

PREFLIGHT opens an epistemic measurement transaction. The AI declares
its baseline state across the 13 vectors plus optional `work_context`
and `work_type` metadata that adjust grounded calibration normalization.

The payload is `extra="forbid"`: an unknown key is a hard error, not a
silent drop. That makes the field list below load-bearing rather than
informational — but a hand-maintained list drifts, so treat
**`empirica preflight-submit --schema`** as authoritative. It prints the
model's own JSON schema, which cannot disagree with what validation
accepts.

**Fields:**
- `session_id: str` — UUID of the active Empirica session (required, 1–100 chars)
- `vectors: dict[str, float]` — Vector name → 0.0–1.0 value. Must include
  at least `know` and `uncertainty`.
- `reasoning: str` — Free-text explanation (optional, max 5000 chars)
- `task_context: str` — Brief task description for pattern retrieval (optional, max 2000 chars)
- `work_context: str` — One of `greenfield`, `iteration`, `investigation`, `refactor`
- `work_type: str` — One of `code`, `infra`, `research`, `release`, `debug`,
  `config`, `docs`, `data`, `comms`, `design`, `audit`, `remote-ops`
- `domain: str` — Domain classification for compliance check selection
  (e.g. `cybersec`, `payments`, `default`)
- `criticality: str` — One of `low`, `medium`, `high`, `critical`
- `claims: list[dict]` — The 2–3 load-bearing claims the work rests on, each
  `{claim, grounding: read|ran|retrieved|assumed, ref}`. Declaring one grounded
  by `read` or `ran` certifies the transaction, so praxic may proceed without a
  separate CHECK.
- `engagement_id: str` — Opaque reference to the engagement this work belongs
  to. Core never resolves or interprets it; engagement entities and their types
  live in the workspace layer. A goal created inside this transaction without
  its own `--engagement-id` inherits it.
- `predicted_check_outcomes: dict[str, float]` — Predicted pass-probability per
  compliance `check_id`, for Brier scoring
- `voice: str` — Voice profile to load for outreach drafting
- `retrospective_reason: str` — Acknowledgment clearing the retrospective
  soft-gate (previous transaction made praxic calls and logged no artifacts)
- `current_phase: str` — `noetic` or `praxic`; accepted, not acted on at PREFLIGHT
- `notes: str` — Free-form scratch text; accepted, not acted on (does NOT drive
  pattern retrieval — use `task_context` for that)

**Raises** `ValueError` via field validators when `session_id` is empty,
`vectors` dict is empty, an unknown vector key is used, a value is
outside 0.0–1.0, or required vectors (`know`, `uncertainty`) are missing.

### `PostflightInput`

Pydantic input schema for the `postflight-submit` CLI command.

POSTFLIGHT closes an epistemic measurement transaction. The AI declares
its updated state after doing the work; the system computes deltas,
runs grounded verification, and produces a calibration score.

**Fields:**
- `session_id: str` — UUID of the active Empirica session
- `vectors: dict[str, float]` — Final vector values (required)
- `reasoning: str` — Retrospective explanation (optional, max 5000 chars)
- `learnings: str` — Key learnings from the transaction (optional, max 5000 chars)
- `goal_id: str` — Optional goal UUID for goal-progress linkage

### `FindingInput`

Pydantic input schema for the `finding-log` CLI command.

Findings record concrete discoveries made during noetic or praxic
work — observations, root causes, behavioral patterns, dependencies
learned. They feed into the calibration loop and pattern retrieval.

**Fields:**
- `session_id: str` — UUID of the active session
- `finding: str` — The discovery text (1–5000 chars). Should be specific
  and actionable.
- `impact: float` — How significant this finding is (0.0–1.0, default 0.5)
- `domain: str` — Optional domain tag (e.g. `auth`, `db`, `frontend`)
- `goal_id: str` — Optional goal UUID

### `UnknownInput`

Pydantic input schema for the `unknown-log` CLI command.

Unknowns record open questions that need investigation. The
noetic-phase complement to findings — findings are what you DO know,
unknowns are what you don't. Unknowns can later be resolved (which
generates a finding).

**Fields:**
- `session_id: str` — UUID of the active session
- `unknown: str` — The open question text (1–5000 chars)
- `impact: float` — How important resolving this is (0.0–1.0, default 0.5)
- `goal_id: str` — Optional goal UUID this unknown blocks

---

## CLI Command Handler Helper Classes

### `FeatureCoverage`

Documentation coverage report for a single feature category.

Holds counts and undocumented-symbol list for one slice of the
`empirica docs-assess` output. Provides derived properties for the
coverage ratio (0.0–1.0) and a moon-phase emoji indicator.

**Attributes:**
- `name: str` — Category display name (e.g. "CLI Commands", "Core")
- `total: int` — Total features detected in this category
- `documented: int` — How many have docs
- `undocumented: list[str]` — Symbol names without sufficient docs

**Properties:**
- `coverage: float` — `documented / total` ratio in [0.0, 1.0]
- `moon: str` — One of 🌑 🌒 🌓 🌔 🌕 reflecting the band

### `StalenessItem`

A single doc-vs-memory staleness signal from `docs-assess`.

Represents one detected mismatch between a documentation section and
an Empirica memory artifact (finding, dead end, mistake, or unknown).
Used to flag docs that contradict or lag behind what the system has
actually learned.

**Attributes:**
- `doc_path: str` — Filesystem path of the doc file
- `section: str` — Heading or anchor of the flagged section
- `severity: str` — `high`, `medium`, or `low`
- `audience: str` — `ai`, `developer`, or `user`
- `memory_type: str` — `finding`, `dead_end`, `mistake`, or `unknown`
- `memory_text: str` — Text of the memory artifact
- `memory_age_days: int` — How old the memory artifact is
- `similarity: float` — Vector similarity (0.0–1.0)
- `suggestion: str` — Human-readable remediation hint

### `UsageMonitor`

Tracks and displays adapter usage statistics for CLI monitoring commands.

Persists usage stats to disk (default `~/.empirica/usage_stats.json`)
and exposes a small API for incrementing counters and querying
aggregated metrics. Used by the `empirica usage-stats` and related
monitoring CLI commands.

**Tracks per adapter:**
- Request counts (total and by call type)
- Total cost in USD
- Average latency in milliseconds
- Success / failure rates and recent error log

### `VisionAnalyzer`

Lightweight image analyzer used by the vision CLI commands.

Provides metadata-only assessment of image files (no ML models, no
LLM calls — just PIL/Pillow inspection) suitable for the
`empirica vision-assess` command and pitch-deck slide auditing.

**Capabilities:**
- Read image dimensions, aspect ratio, file size
- Detect common presentation aspect ratios (4:3, 16:9, 16:10)
- Generate `BasicImageAssessment` records for downstream tools
- Surface size warnings (oversized files, suspicious dimensions)

Requires PIL/Pillow at construction time — raises ImportError if not
installed (`pip install pillow`).

### `GroupedHelpFormatter`

Argparse formatter that groups Empirica's subcommands by category.

Empirica has 180+ CLI commands across 26 categories. The default
argparse subcommand listing renders all of them in a flat,
impossible-to-scan list. This formatter overrides `_format_action`
to render only a curated "Core Commands" view (~25 high-traffic
commands grouped under Workflow / Epistemic Artifacts / Goals /
Context / Monitoring), with a footer pointing users to
`empirica help <category>` for the full enumeration.

Inherits `RawDescriptionHelpFormatter` so the multi-line program
description survives without word-wrap mangling. Used as the
`formatter_class` of the top-level argparse parser in
`cli_core.py:create_parser`.

---

## See Also

- [`docs/reference/api/core_session_management.md`](core_session_management.md) — `SessionDatabase` API the Pydantic schemas hand off to
- [`docs/reference/CLI_ALIASES.md`](../CLI_ALIASES.md) — Short-form aliases for the workflow commands these schemas validate
