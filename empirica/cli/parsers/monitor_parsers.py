"""Monitoring command parsers."""


def add_monitor_parsers(subparsers):
    """Add monitoring command parsers"""
    # Unified monitor command (consolidates monitor, monitor-export, monitor-reset, monitor-cost)
    monitor_parser = subparsers.add_parser("monitor", help="Monitoring dashboard and statistics")
    monitor_parser.add_argument("--export", metavar="FILE", help="Export data to file (replaces monitor-export)")
    monitor_parser.add_argument("--reset", action="store_true", help="Reset statistics (replaces monitor-reset)")
    monitor_parser.add_argument("--cost", action="store_true", help="Show cost analysis (replaces monitor-cost)")
    monitor_parser.add_argument("--history", action="store_true", help="Show recent request history")
    monitor_parser.add_argument("--health", action="store_true", help="Include adapter health checks")
    monitor_parser.add_argument(
        "--turtle",
        action="store_true",
        help="Show epistemic health: flow state, transaction completeness, unknowns/findings",
    )
    monitor_parser.add_argument("--project", action="store_true", help="Show cost projections (with --cost)")
    monitor_parser.add_argument(
        "--output", choices=["json", "csv"], default="json", help="Export format (with --export)"
    )
    monitor_parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation (with --reset)")
    monitor_parser.add_argument("--verbose", action="store_true", help="Show detailed stats")

    # MCO load command - load Meta-Agent Configuration Object
    mco_load_parser = subparsers.add_parser("mco-load", help="Load MCO (Meta-Agent Configuration Object) configuration")
    mco_load_parser.add_argument("--session-id", help="Session UUID (optional, for inference)")
    mco_load_parser.add_argument("--ai-id", help="AI identifier (optional, for model/persona inference)")
    mco_load_parser.add_argument("--snapshot", help="Path to pre_summary snapshot (for post-compact reload)")
    mco_load_parser.add_argument("--model", help="Explicit model override (claude_haiku, claude_sonnet, gpt4, etc.)")
    mco_load_parser.add_argument(
        "--persona", help="Explicit persona override (researcher, implementer, reviewer, etc.)"
    )
    mco_load_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    mco_load_parser.add_argument("--verbose", action="store_true", help="Show detailed output")

    # Assess state command - capture sessionless epistemic state
    assess_state_parser = subparsers.add_parser(
        "assess-state", help="Capture sessionless epistemic state (for statusline, monitoring, compact boundaries)"
    )
    assess_state_parser.add_argument("--session-id", help="Session UUID (optional, for context)")
    assess_state_parser.add_argument("--prompt", help="Self-assessment context/evidence (optional)")
    assess_state_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    assess_state_parser.add_argument("--verbose", action="store_true", help="Show detailed output")
    assess_state_parser.add_argument(
        "--turtle",
        action="store_true",
        help="Recursive grounding check: verify observer stability before observing (Noetic Handshake)",
    )

    # Trajectory project command - the turtle telescope
    trajectory_parser = subparsers.add_parser(
        "trajectory-project",
        help="Project viable epistemic paths forward based on current grounding (Turtle Telescope)",
    )
    trajectory_parser.add_argument("--session-id", help="Session UUID for context")
    trajectory_parser.add_argument("--turtle", action="store_true", help="Include full turtle stack in projection")
    trajectory_parser.add_argument(
        "--depth",
        type=int,
        default=3,
        choices=[1, 2, 3],
        help="Projection depth: 1=immediate, 2=short-term, 3=strategic (default: 3)",
    )
    trajectory_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    trajectory_parser.add_argument("--verbose", action="store_true", help="Show detailed reasoning for each path")

    # System status command - unified /proc-style kernel overview
    # NOTE: 'status' alias removed — new top-level 'empirica status' command is the cockpit overview.
    # Use 'empirica system-status' for the kernel-style diagnostic.
    system_status_parser = subparsers.add_parser(
        "system-status", help="Unified Noetic OS system status (config, memory, bus, gate, integrity)"
    )
    system_status_parser.add_argument("--session-id", help="Session UUID (auto-detects if omitted)")
    system_status_parser.add_argument(
        "--output", choices=["human", "json"], default="human", help="Output format (default: human)"
    )
    system_status_parser.add_argument("--summary", action="store_true", help="One-line summary (for statusline)")

    # REMOVED: monitor-export, monitor-reset, monitor-cost
    # Use: monitor --export FILE, monitor --reset, monitor --cost

    # Commit-context command - aggregate git notes anchored to commit(s)
    from empirica.cli.command_handlers.commit_context_commands import (
        add_commit_context_parser,
    )

    add_commit_context_parser(subparsers)

    # Compact analysis command - measure epistemic loss during memory compaction
    compact_parser = subparsers.add_parser(
        "compact-analysis",
        help="Analyze epistemic loss during memory compaction",
        description="""
Retroactively analyze pre-compact snapshots vs post-compact assessments
to measure knowledge loss and recovery during Claude Code memory compaction.

Data Quality Filtering (default):
- Excludes test sessions (ai_id: test*, *-test, storage-*)
- Requires sessions with actual work evidence (findings/unknowns)
- Filters rapid-fire sessions (< 5 min duration)
        """,
    )
    compact_parser.add_argument(
        "--include-tests", action="store_true", help="Include test sessions in analysis (normally filtered)"
    )
    compact_parser.add_argument(
        "--min-findings", type=int, default=0, help="Minimum findings count to include session (default: 0)"
    )
    compact_parser.add_argument("--limit", type=int, default=20, help="Maximum compact events to analyze (default: 20)")
    compact_parser.add_argument(
        "--output", choices=["human", "json"], default="human", help="Output format (default: human)"
    )

    # Calibration report command - grounded calibration from post-test evidence
    calibration_parser = subparsers.add_parser(
        "calibration-report",
        help="Generate calibration report from grounded evidence",
        description="""
Analyze AI calibration by comparing POSTFLIGHT self-assessment against objective evidence.

Default: Shows grounded calibration (POSTFLIGHT → POST-TEST evidence comparison).
This is real calibration - measuring accuracy of self-assessment against reality.

Evidence sources:
- Test results (pytest JSON reports)
- Git metrics (commits, lines changed)
- Artifact counts (findings, unknowns, dead-ends)
- Goal/task completion ratios

Use --learning-trajectory to see PREFLIGHT→POSTFLIGHT deltas (learning, not calibration).
        """,
    )
    calibration_parser.add_argument(
        "--ai-id", help="Filter by AI identifier (default: all; canonical ai_ids derived from project basename)"
    )
    calibration_parser.add_argument("--weeks", type=int, default=8, help="Number of weeks to analyze (default: 8)")
    calibration_parser.add_argument(
        "--include-tests", action="store_true", help="Include test sessions in analysis (normally filtered)"
    )
    calibration_parser.add_argument(
        "--min-samples", type=int, default=10, help="Minimum samples per vector for confident analysis (default: 10)"
    )
    calibration_parser.add_argument(
        "--output", choices=["human", "json", "markdown"], default="human", help="Output format (default: human)"
    )
    calibration_parser.add_argument(
        "--update-prompt", action="store_true", help="Generate copy-paste ready calibration table for system prompts"
    )
    calibration_parser.add_argument("--verbose", action="store_true", help="Show detailed per-vector analysis")
    calibration_parser.add_argument(
        "--learning-trajectory",
        action="store_true",
        help="Show learning trajectory (PREFLIGHT→POSTFLIGHT deltas) - NOT calibration",
    )
    calibration_parser.add_argument(
        "--trajectory", action="store_true", help="Show calibration trend over time (closing/widening/stable)"
    )
    calibration_parser.add_argument(
        "--list-disputes", action="store_true", help="Show all calibration disputes (open and resolved)"
    )
    calibration_parser.add_argument(
        "--brier",
        action="store_true",
        help="Show Brier score decomposition per phase (reliability, resolution, uncertainty)",
    )

    # Grounding export - single-practice grounding snapshot for mesh (L1/L2) tooling
    grounding_export_parser = subparsers.add_parser(
        "grounding-export",
        help="Export one practice's current grounding state (self-assessed + grounded 13-vectors + divergence) as JSON",
        description="""
Export a single practice's current grounding state — the cross-practice
composition primitive for mesh-level tooling (e.g. autonomy's mesh-cohesion
glance). Resolves a practice by --ai-id against this host's global sessions.db
and emits self_assessed_13 (latest POSTFLIGHT), grounded_13 (evidence-derived
beliefs), per-vector divergence, a holistic alignment score, evidence_count,
and staleness.

LOCALITY: resolves only practices present on THIS host (grounded beliefs live
in this host's sessions.db). Cross-host/mesh-wide grounding is the separate
L2/foundation piece (grounded snapshot pushed to cortex). Unknown/non-local
ai_id → ok:false, reason:not_local.
        """,
    )
    grounding_export_parser.add_argument(
        "--ai-id", required=True, help="Practice to export (canonical 3-form or bare basename)"
    )
    grounding_export_parser.add_argument(
        "--output", choices=["human", "json"], default="json", help="Output format (default: json)"
    )

    # Calibration dispute command - AI pushback on measurement artifacts
    dispute_parser = subparsers.add_parser(
        "calibration-dispute",
        help="Dispute a grounded calibration measurement as a measurement artifact",
        description="""
File a dispute when grounded calibration reports a gap that's a measurement bug,
not a real overestimate. Example: greenfield project getting change=0.2 when creating
an entire repo from scratch.

Disputes are stored in SQLite and flagged in subsequent calibration reports.
        """,
    )
    dispute_parser.add_argument("--vector", required=True, help="Vector name to dispute (e.g., change, impact, do)")
    dispute_parser.add_argument(
        "--reported", type=float, required=True, help="The grounded value reported by post-test (e.g., 0.2)"
    )
    dispute_parser.add_argument(
        "--expected", type=float, required=True, help="The value you believe is correct (e.g., 0.85)"
    )
    dispute_parser.add_argument(
        "--reason",
        required=True,
        help='Why this measurement is wrong (e.g., "Greenfield repo, normalization inappropriate")',
    )
    dispute_parser.add_argument(
        "--evidence", default="", help='Supporting evidence (e.g., "git log --stat shows 8 files created")'
    )
    dispute_parser.add_argument("--session-id", help="Session to dispute (default: active session)")
    dispute_parser.add_argument(
        "--output", choices=["human", "json"], default="json", help="Output format (default: json)"
    )

    # Compliance report — project-wide quality snapshot mapped to regulatory frameworks
    compliance_parser = subparsers.add_parser(
        "compliance-report",
        help="Generate compliance report mapped to regulatory frameworks",
        description="""
Run all deterministic quality checks and map results to regulatory frameworks.

Checks (always run):
- ruff: static analysis (lint violations)
- radon: cyclomatic complexity
- pyright: type safety errors
- epistemic audit: transaction trail completeness
- calibration: grounded verification data

Optional checks (--tests, --dep-audit):
- pytest: full test suite (slow)
- pip-audit: dependency CVE scanning

Mapped frameworks: EU AI Act, GDPR, ISO/IEC 42001
        """,
    )
    compliance_parser.add_argument("--tests", action="store_true", help="Include test suite execution (slow)")
    compliance_parser.add_argument(
        "--emit",
        action="store_true",
        help="Emit the result to cortex System│Diagnostics (needs a cortex api_key)",
    )
    compliance_parser.add_argument("--dep-audit", action="store_true", help="Include dependency CVE audit")
    compliance_parser.add_argument("--security", action="store_true", help="Include semgrep OWASP security scan")
    compliance_parser.add_argument(
        "--output", choices=["text", "json"], default="text", help="Output format (default: text)"
    )

    # Security audit — pip-audit + CISA KEV cross-reference
    security_audit_parser = subparsers.add_parser(
        "security-audit",
        help="Supply-chain security audit (pip-audit + CISA KEV)",
        description="""
Run pip-audit and cross-reference findings with CISA's Known Exploited
Vulnerabilities (KEV) catalog. Promotes actively-exploited CVEs to top
priority.

Output priority levels:
- now:     CVE is in CISA KEV (observed in-the-wild exploitation)
- month:   CVE found by pip-audit but not in KEV
- monitor: low-severity finding
- safe:    no priority action

Maps to: EU AI Act Art. 15(4), ISO 42001 8.4, GDPR Art. 32.
        """,
    )
    security_audit_parser.add_argument(
        "--project-root", default=".", help="Project root to audit (default: current directory)"
    )
    security_audit_parser.add_argument(
        "--refresh-feeds", action="store_true", help="Force re-download of CISA KEV feed (otherwise cached for 24h)"
    )
    security_audit_parser.add_argument(
        "--output", choices=["text", "json"], default="text", help="Output format (default: text)"
    )

    # Noetic batch — single-call investigation primitive
    nb_parser = subparsers.add_parser(
        "noetic-batch",
        help="Batched investigation: reads + greps + globs + investigate in one call",
        description="""
Run a batched investigation — reads, greps, globs, investigate queries
returned as one merged structured response. Use when batching ≥3
investigation operations; individual Read/Grep/Glob/investigate calls
are noetic anywhere and don't need batching. NOT a Sentinel bypass —
calling it once for a single read is misuse.

JSON input via stdin (AI-first):
    empirica noetic-batch - <<EOF
    {
      "intent": "understand auth surface",
      "reads": [{"path": "src/auth.py"}],
      "greps": [{"pattern": "decorator", "glob": "src/**/*.py", "context": 2}],
      "globs": ["src/**/*auth*"],
      "investigate": [{"query": "auth flow", "scope": "project"}]
    }
    EOF

Or use --schema to print the full input schema. --dry-run to validate
without executing. See docs/architecture/NOETIC_BATCH_SPEC.md.
        """,
    )
    nb_parser.add_argument("config", nargs="?", help='JSON config file path, or "-" for stdin (AI-first mode)')
    nb_parser.add_argument("--intent", help="One-line investigation goal (alternative to JSON config)")
    nb_parser.add_argument("--read", action="append", help="File path to read (repeatable)")
    nb_parser.add_argument(
        "--grep",
        action="append",
        help='Grep spec: "pattern" or "pattern:glob" or "pattern:glob:context=N" (repeatable)',
    )
    nb_parser.add_argument("--glob", action="append", help="Glob pattern (repeatable)")
    nb_parser.add_argument("--investigate", action="append", help="project-search query (repeatable)")
    nb_parser.add_argument(
        "--project-root",
        default=None,
        help="Project root for relative paths. Default: InstanceResolver.project_path() (the active Empirica project), falling back to cwd if unresolvable. Pass explicitly to override.",
    )
    nb_parser.add_argument("--schema", action="store_true", help="Print the input JSON schema and exit")
    nb_parser.add_argument("--dry-run", action="store_true", help="Validate input without executing operations")
    nb_parser.add_argument("--output", choices=["json", "text"], default="json", help="Output format (default: json)")
