"""Release command parsers."""


def add_release_parsers(subparsers):
    """Add release command parsers"""
    # Release readiness check
    release_parser = subparsers.add_parser(
        'release-ready',
        help='Epistemic release assessment - verifies version sync, architecture health, security, and documentation'
    )
    release_parser.add_argument(
        '--project-root',
        help='Root directory of the project (default: current directory)'
    )
    release_parser.add_argument(
        '--quick',
        action='store_true',
        help='Quick check (skip architecture assessment)'
    )
    release_parser.add_argument(
        '--output',
        choices=['human', 'json'],
        default='human',
        help='Output format'
    )

    # Docs assessment
    docs_parser = subparsers.add_parser(
        'docs-assess',
        help='Epistemic documentation assessment - measures docs coverage against actual features'
    )
    docs_parser.add_argument(
        '--project-root',
        help='Root directory of the project (default: current directory)'
    )
    docs_parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed undocumented items'
    )
    docs_parser.add_argument(
        '--summary-only',
        action='store_true',
        help='Lightweight summary (~50 tokens) for bootstrap context'
    )
    docs_parser.add_argument(
        '--output',
        choices=['human', 'json'],
        default='human',
        help='Output format'
    )
    docs_parser.add_argument(
        '--check-docstrings',
        action='store_true',
        help='Check Python code for missing docstrings (functions, classes, modules)'
    )
    docs_parser.add_argument(
        '--turtle',
        action='store_true',
        help='Epistemic recursive mode: iterate between code and docs to surface gaps'
    )
    docs_parser.add_argument(
        '--check-staleness',
        action='store_true',
        help='Detect stale docs by cross-referencing with recent findings, dead-ends, and mistakes'
    )
    docs_parser.add_argument(
        '--staleness-threshold',
        type=float,
        default=0.7,
        help='Minimum similarity threshold for staleness detection (default: 0.7)'
    )
    docs_parser.add_argument(
        '--staleness-days',
        type=int,
        default=30,
        help='Look back N days for memory items (default: 30)'
    )

    # Docs link check — broken-link integrity for tech docs
    link_parser = subparsers.add_parser(
        'docs-link-check',
        help='Verify markdown internal links — finds broken relative paths in tech docs',
        description=(
            "Walks the project (or --root) for *.md files outside SKIP_DIRS "
            "(.git, .venv, node_modules, _archive, etc.), extracts markdown links, "
            "and verifies each relative-path link resolves to an existing file. "
            "External URLs and pure anchors are not checked. "
            "Tier-prioritised output: top-level README, per-folder READMEs, then all others. "
            "Exit code 0 = clean, 1 = broken links found, 2 = invalid args."
        ),
    )
    link_parser.add_argument(
        '--root',
        default=None,
        help='Project root to scan (default: current directory).',
    )
    link_parser.add_argument(
        '--exclude',
        action='append',
        default=None,
        help='Additional directory names to skip (repeatable). On top of the default skip set.',
    )
    link_parser.add_argument(
        '--output',
        choices=['human', 'json'],
        default='human',
        help='Output format. JSON shape: {scanned_files, broken_total, passed, tiers}.',
    )

    # Docs explain - focused information retrieval
    explain_parser = subparsers.add_parser(
        'docs-explain',
        help='Get focused explanation of Empirica topics - inverts docs-assess'
    )
    explain_parser.add_argument(
        '--topic',
        help='Topic to explain (e.g., "vectors", "sessions", "goals")'
    )
    explain_parser.add_argument(
        '--question',
        help='Question to answer (e.g., "How do I start a session?")'
    )
    explain_parser.add_argument(
        '--audience',
        choices=['user', 'developer', 'ai', 'all'],
        default='all',
        help='Target audience for explanation'
    )
    explain_parser.add_argument(
        '--project-root',
        help='Root directory of the project (default: current directory)'
    )
    explain_parser.add_argument(
        '--project-id',
        help='Project ID for Qdrant semantic search (auto-detected if not specified)'
    )
    explain_parser.add_argument(
        '--output',
        choices=['human', 'json'],
        default='human',
        help='Output format'
    )
