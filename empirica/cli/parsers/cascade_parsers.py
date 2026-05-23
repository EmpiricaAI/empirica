"""Epistemic transaction workflow command parsers.

Aliases:
- preflight-submit → pre, preflight
- postflight-submit → post, postflight
"""

# Shared help strings — repeated across the four workflow commands.
_HELP_CONFIG = (
    'JSON config file path, or "-" to read JSON from stdin (AI-first mode). '
    'Required unless using --vectors / --reasoning flags. The JSON object holds '
    'the full assessment payload — `vectors`, `reasoning`, optional `session_id`, '
    'plus PREFLIGHT-only `task_context` / `work_type` / `domain` / `criticality`. '
    'Example: `empirica preflight-submit - <<EOF\\n{"vectors":{...}, "reasoning":"..."}\\nEOF`'
)
_HELP_SESSION_LEGACY = (
    'Session UUID (legacy flag-based mode). Normally auto-derived from the '
    'active transaction file; only needed when running outside a transaction '
    'or against a specific session_id.'
)
_HELP_VECTORS_LEGACY = (
    'Epistemic vectors as a JSON dict (legacy mode). Prefer passing them inside '
    'the config-file payload instead. Example: '
    '\'{"know":0.7, "uncertainty":0.2, "context":0.6, "engagement":0.9}\''
)
_HELP_OUTPUT_JSON_DEFAULT = (
    'Output format (default: json — AI-friendly, machine-parsable). Use `human` '
    'when reading by eye at the terminal.'
)
_HELP_OUTPUT_HUMAN_DEFAULT = (
    'Output format (default: human — readable at the terminal). Use `json` when '
    'scripting or feeding into another tool.'
)
_HELP_VERBOSE = (
    'Echo extra operation info to stderr (DB paths, timing, debug detail). '
    'Doesn\'t affect the structured output on stdout.'
)


def add_cascade_parsers(subparsers):
    """Add cascade command parsers (Primary CLI interface for epistemic assessments).

    The transaction workflow commands are the primary interface for AI-based
    epistemic assessments. MCP tools route to these CLI commands:
      - preflight-submit: opens a measurement window with baseline vectors
      - check / check-submit: gates the noetic → praxic transition
      - postflight-submit: closes the window with final vectors

    This function provides the core CLI interface for epistemic self-assessment.
    """
    # ── preflight-submit ────────────────────────────────────────────────
    preflight_submit_parser = subparsers.add_parser(
        'preflight-submit',
        aliases=['pre', 'preflight'],
        help=(
            'Open an epistemic transaction. Records baseline vectors + task '
            'context as the starting measurement point. Must be called before '
            'any praxic tool (Edit/Write/Bash); the Sentinel firewall enforces '
            'this. Pairs with check-submit (mid-cycle gate) and postflight-submit '
            '(close). AI-first: pass JSON via stdin or a config file path.'
        ),
    )

    preflight_submit_parser.add_argument(
        'config', nargs='?',
        help=_HELP_CONFIG,
    )

    # Legacy flag-based mode (backward compatible)
    preflight_submit_parser.add_argument(
        '--session-id',
        help=_HELP_SESSION_LEGACY,
    )
    preflight_submit_parser.add_argument(
        '--vectors',
        help=_HELP_VECTORS_LEGACY,
    )
    preflight_submit_parser.add_argument(
        '--reasoning',
        help=(
            'Free-text narrative explaining the baseline assessment '
            '(legacy mode). What you know, what you don\'t, why these vector '
            'values reflect your actual epistemic state right now. Prefer '
            'setting in the config-file payload as `"reasoning": "..."`.'
        ),
    )
    preflight_submit_parser.add_argument(
        '--voice',
        help=(
            'Voice profile name to load for outreach drafting work '
            '(e.g. `--voice david`). Resolved via the empirica voice loader. '
            'Only relevant for outreach / publishing transactions; ignored '
            'for code / docs / research work.'
        ),
    )
    preflight_submit_parser.add_argument(
        '--output', choices=['human', 'json'], default='json',
        help=_HELP_OUTPUT_JSON_DEFAULT,
    )
    preflight_submit_parser.add_argument(
        '--verbose', action='store_true',
        help=_HELP_VERBOSE,
    )

    # ── check ───────────────────────────────────────────────────────────
    check_parser = subparsers.add_parser(
        'check',
        help=(
            'Run an epistemic check WITHOUT submitting it as the gate decision. '
            'Use this to probe whether your current state would pass the '
            'noetic→praxic gate before committing to the transition. For '
            'actually gating, use `check-submit`.'
        ),
    )

    check_parser.add_argument(
        'config', nargs='?',
        help=_HELP_CONFIG,
    )

    check_parser.add_argument(
        '--session-id',
        help=_HELP_SESSION_LEGACY,
    )
    check_parser.add_argument(
        '--findings',
        help=(
            'Investigation findings logged this transaction, as a JSON array '
            '(legacy mode). Usually unnecessary — the gate reads logged '
            'findings from the active transaction directly.'
        ),
    )
    # Mutually exclusive: --unknowns / --remaining-unknowns alias pair
    unknowns_group = check_parser.add_mutually_exclusive_group(required=False)
    unknowns_group.add_argument(
        '--unknowns', dest='unknowns',
        help=(
            'Open unknowns at the gate, as a JSON array (legacy mode). '
            'Usually unnecessary — the gate reads logged unknowns from the '
            'active transaction directly. See also --remaining-unknowns.'
        ),
    )
    unknowns_group.add_argument(
        '--remaining-unknowns', dest='unknowns',
        help='Alias for --unknowns (legacy compatibility shim).',
    )
    check_parser.add_argument(
        '--confidence', type=float,
        help=(
            'Overall confidence score 0.0–1.0 (legacy mode). The gate prefers '
            'the per-vector breakdown in the config payload; --confidence is '
            'a flat-scalar fallback for old callers.'
        ),
    )
    check_parser.add_argument(
        '--output', choices=['human', 'json'], default='json',
        help=_HELP_OUTPUT_JSON_DEFAULT,
    )
    check_parser.add_argument(
        '--verbose', action='store_true',
        help='Show detailed gate-decision analysis (which vectors blocked, '
             'what threshold inflation was applied, Brier scoring detail).',
    )

    # ── check-submit ────────────────────────────────────────────────────
    check_submit_parser = subparsers.add_parser(
        'check-submit',
        help=(
            'Submit a check assessment AND apply the gate decision. Pass `decision`=`proceed` '
            'to move to the praxic phase, `investigate` to stay noetic, '
            '`proceed_with_caution` for a soft gate. The Sentinel firewall '
            'reads the result to allow/deny subsequent praxic tools. Required '
            'after PREFLIGHT before any Edit/Write/Bash.'
        ),
    )

    check_submit_parser.add_argument(
        'config', nargs='?',
        help=_HELP_CONFIG,
    )

    check_submit_parser.add_argument(
        '--session-id',
        help=_HELP_SESSION_LEGACY,
    )
    check_submit_parser.add_argument(
        '--vectors',
        help=_HELP_VECTORS_LEGACY,
    )
    check_submit_parser.add_argument(
        '--decision', choices=['proceed', 'investigate', 'proceed_with_caution'],
        help=(
            'Gate decision (legacy mode). `proceed` → praxic phase unlocks. '
            '`investigate` → stay noetic, more reads/searches needed. '
            '`proceed_with_caution` → soft gate (tools unlock but Sentinel '
            'logs a warning). Usually carried inside the config payload '
            'rather than this flag.'
        ),
    )
    check_submit_parser.add_argument(
        '--reasoning',
        help=(
            'Free-text explaining the gate decision (legacy mode). What '
            'investigation answered the original unknowns, what residual '
            'uncertainty remains, why proceeding now is the right call.'
        ),
    )
    check_submit_parser.add_argument(
        '--cycle', type=int,
        help=(
            'Investigation cycle number (legacy mode). 1 on first CHECK, '
            'increments if you re-investigate then re-CHECK before proceeding.'
        ),
    )
    check_submit_parser.add_argument(
        '--round', type=int,
        help=(
            'Round number used for checkpoint tracking across multi-stage '
            'investigations (legacy mode).'
        ),
    )
    check_submit_parser.add_argument(
        '--output', choices=['human', 'json'], default='human',
        help=_HELP_OUTPUT_HUMAN_DEFAULT,
    )
    check_submit_parser.add_argument(
        '--verbose', action='store_true',
        help=_HELP_VERBOSE,
    )

    # ── postflight-submit ───────────────────────────────────────────────
    postflight_submit_parser = subparsers.add_parser(
        'postflight-submit',
        aliases=['post', 'postflight'],
        help=(
            'Close the epistemic transaction. Records final vectors + a '
            'reasoning narrative describing what changed since PREFLIGHT. '
            'Triggers the grounded-calibration pipeline (compares your '
            'beliefs to deterministic evidence: git, lint, tests, artifact '
            'logs). Run after committing the work — uncommitted edits are '
            'invisible to the change/state/do evidence sensors.'
        ),
    )

    postflight_submit_parser.add_argument(
        'config', nargs='?',
        help=_HELP_CONFIG,
    )

    postflight_submit_parser.add_argument(
        '--session-id',
        help=_HELP_SESSION_LEGACY,
    )
    postflight_submit_parser.add_argument(
        '--vectors',
        help=_HELP_VECTORS_LEGACY,
    )
    postflight_submit_parser.add_argument(
        '--reasoning',
        help=(
            'Free-text describing what changed from PREFLIGHT to POSTFLIGHT '
            '(legacy mode). Surface what you learned, what surprised you, '
            'what you shipped, what residual unknowns carry into the next '
            'transaction.'
        ),
    )
    postflight_submit_parser.add_argument(
        '--changes', dest='reasoning',
        help='Deprecated alias for --reasoning. Use --reasoning instead.',
    )
    postflight_submit_parser.add_argument(
        '--output', choices=['human', 'json'], default='json',
        help=_HELP_OUTPUT_JSON_DEFAULT,
    )
    postflight_submit_parser.add_argument(
        '--verbose', action='store_true',
        help=_HELP_VERBOSE,
    )
