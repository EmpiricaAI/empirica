"""Action logging command parsers for INVESTIGATE and ACT phases.

These two commands are the structured counterparts of the artifact log
family (finding-log, decision-log, etc.). They group multiple findings
or actions into a single coherent narrative for a phase boundary —
useful when reporting investigation results or commit-batch actions
in one shot rather than as N individual artifact logs.

For most one-off logging, prefer the per-artifact *-log commands.
"""


def add_action_parsers(subparsers):
    """Add action logging command parsers for INVESTIGATE and ACT phases."""
    # investigate-log: batch findings produced by an investigation phase
    investigate_log_parser = subparsers.add_parser(
        'investigate-log',
        help=(
            'Log a batch of findings produced by an investigation phase. '
            'Use when you have multiple related discoveries to record at '
            'once (e.g. after reading several files, running a series of '
            'greps). For single discoveries, prefer finding-log directly.'
        ),
    )
    investigate_log_parser.add_argument(
        '--session-id', required=False,
        help='Session UUID. Auto-derived from active transaction if omitted.',
    )
    investigate_log_parser.add_argument(
        '--findings', required=True,
        help=(
            'JSON array of finding strings or {finding, impact} objects. '
            'Example: \'["X uses Y", "Z deprecated since v3"]\' or '
            '\'[{"finding":"X uses Y","impact":0.7}]\'.'
        ),
    )
    investigate_log_parser.add_argument(
        '--evidence',
        help=(
            'JSON object linking findings to supporting evidence — file '
            'paths, line numbers, commit SHAs, URLs. Example: '
            '\'{"files":["src/x.py:42"], "commits":["abc123"]}\'.'
        ),
    )
    investigate_log_parser.add_argument(
        '--output', choices=['json', 'text'], default='text',
        help='Output format. Use `json` when scripting; `text` for terminal.',
    )
    investigate_log_parser.add_argument(
        '--verbose', action='store_true',
        help='Echo extra diagnostic info to stderr.',
    )

    # act-log: batch actions taken during the praxic phase
    act_log_parser = subparsers.add_parser(
        'act-log',
        help=(
            'Log a batch of praxic actions (file edits, commands run, '
            'commits made) with their artifacts. Use to record a coherent '
            'unit of execution work in one call rather than several. For '
            'tracking individual artifact creations, prefer per-type *-log '
            'commands; for tracking task completion, prefer '
            'goals-complete-task with --evidence.'
        ),
    )
    act_log_parser.add_argument(
        '--session-id', required=False,
        help='Session UUID. Auto-derived from active transaction if omitted.',
    )
    act_log_parser.add_argument(
        '--actions', required=True,
        help=(
            'JSON array describing actions taken. Example: '
            '\'["Edited src/x.py", "Added test_y", "Ran ruff check"]\'.'
        ),
    )
    act_log_parser.add_argument(
        '--artifacts',
        help=(
            'JSON array of files modified/created/deleted. Example: '
            '\'["src/x.py", "tests/test_y.py"]\'. Augments git for actions '
            'that don\'t produce a commit yet.'
        ),
    )
    act_log_parser.add_argument(
        '--goal-id',
        help='Goal UUID this action sequence advanced. Ties act-log to a tracked work unit.',
    )
    act_log_parser.add_argument(
        '--output', choices=['json', 'text'], default='text',
        help='Output format. Use `json` when scripting; `text` for terminal.',
    )
    act_log_parser.add_argument(
        '--verbose', action='store_true',
        help='Echo extra diagnostic info to stderr.',
    )
