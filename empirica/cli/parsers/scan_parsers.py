"""Argparse parser for `empirica scan` (Phase 1 — one-shot).

Per docs/architecture/PROPOSAL_AI_SERVICE_SCANNER.md.
"""

from __future__ import annotations


def add_scan_parsers(subparsers):
    """Register the `scan` verb."""
    scan_root = subparsers.add_parser(
        'scan',
        help='One-shot inventory of running AI-touching services (read-only)',
        description="""
Live running-state inventory: processes, sockets, scheduled tasks, plugin
manifests, MCP servers, env-var names. Phase 1 is deterministic data
collection only — no AI judgment yet. Markdown output by default; JSON
for piping. Read-surface declared in project.yaml under
cockpit.scanner.read_surface (defaults applied if absent).
        """,
    )
    scan_root.add_argument('--output', choices=['markdown', 'json'],
                           default='markdown',
                           help='Output format (default: markdown)')
    scan_root.add_argument('--save', action='store_true',
                           help='Persist the JSON snapshot to ~/.empirica/scans/<scan_id>.json '
                                'and update last_scan_<project_id>.json for cockpit consumption')
    scan_root.add_argument('--explain', action='store_true',
                           help='Hand the snapshot to the services-auditor skill for AI judgment '
                                '(Phase 2). Auto-saves the snapshot and prints a system-reminder '
                                'pointing the AI at /services-auditor with the snapshot path.')
    scan_root.add_argument('--project-id',
                           help='Project UUID (overrides automatic resolution)')

    # ─── Phase 3 history verbs ───────────────────────────────────────
    history = subparsers.add_parser(
        'scan-history',
        help='List past scan snapshots for the project (audit trail)',
        description='Reads ~/.empirica/scan_history_<project_id>.jsonl and '
                    'prints the audit trail newest-first.',
    )
    history.add_argument('--limit', type=int, default=20,
                         help='Max rows to show (default: 20, 0 = all)')
    history.add_argument('--project-id',
                         help='Project UUID (overrides auto-resolution)')
    history.add_argument('--output', choices=['human', 'json'], default='human',
                         help='Output format (default: human)')

    show = subparsers.add_parser(
        'scan-show',
        help='Show a saved scan snapshot by scan_id (UUID prefix accepted)',
    )
    show.add_argument('scan_id', help='Scan UUID or ≥8-char prefix')
    show.add_argument('--project-id',
                      help='Project UUID (overrides auto-resolution)')
    show.add_argument('--output', choices=['markdown', 'json'], default='markdown',
                      help='Output format (default: markdown)')

    diff = subparsers.add_parser(
        'scan-diff',
        help='Diff two saved scan snapshots — added/removed processes + ports',
    )
    diff.add_argument('scan_id_a', help='Older snapshot UUID or prefix')
    diff.add_argument('scan_id_b', help='Newer snapshot UUID or prefix')
    diff.add_argument('--project-id',
                      help='Project UUID (overrides auto-resolution)')
    diff.add_argument('--output', choices=['human', 'json'], default='human',
                      help='Output format (default: human)')

    audit = subparsers.add_parser(
        'services-audit',
        help='One fire of the services-audit loop: scan + diff vs prior + '
             'notify on novel services',
        description='Captures a fresh snapshot via `scan --save`, diffs it '
                    'against the previous entry in the project history, and '
                    'emits a notification when novel running services appear. '
                    'Returns structured JSON with a result field (found / '
                    'empty / fail) for the loop body to feed into '
                    '`loop heartbeat --result`.',
    )
    audit.add_argument('--no-notify', action='store_true',
                       help='Skip notification dispatch even when novelty '
                            'detected (testing / dry-run mode)')
    audit.add_argument('--project-id',
                       help='Project UUID (overrides auto-resolution)')
    audit.add_argument('--output', choices=['human', 'json'], default='json',
                       help='Output format (default: json — loop bodies '
                            'consume this)')
