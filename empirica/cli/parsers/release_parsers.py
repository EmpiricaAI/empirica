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

    # Bootstrap context — three-circle artifact graph injection
    bootstrap_parser = subparsers.add_parser(
        'bootstrap-context',
        help='Emit the bootstrap context payload (schema v2) — three-circle artifact graph',
        description=(
            "Three-circle surfacing model: active_state (recency-decayed), "
            "persistent_reference (no decay), topic_relevant_backlog "
            "(similarity-pulled). Used by post-compact / session-init hooks "
            "and the daemon GET /api/v1/bootstrap endpoint. See "
            "docs/specs/PROPOSAL_BOOTSTRAP_AGGREGATOR.md for the design."
        ),
    )
    bootstrap_parser.add_argument(
        '--project-path',
        default=None,
        help='Project root (default: resolve via InstanceResolver canonical chain).',
    )
    bootstrap_parser.add_argument(
        '--session-id',
        default=None,
        help='Active session UUID (informational; queries scope by project_id).',
    )
    bootstrap_parser.add_argument(
        '--similarity-threshold',
        type=float,
        default=0.65,
        help='Cosine threshold for circle 3 topic-relevance pull (default: 0.65).',
    )
    bootstrap_parser.add_argument(
        '--output',
        choices=['human', 'json'],
        default='json',
        help='Output format (default: json — what hooks/MCP consume).',
    )

    # Practice context — Ambassador addressbook (Lane 2 of cortex prop_7r5tihxyqr)
    practice_context_parser = subparsers.add_parser(
        'practice-context',
        help='Ambassador addressbook — project roster as per-practitioner rows with substrate',
        description=(
            "Pulls /v1/users/me/roster from cortex and projects each "
            "(tenant, project) seat as a practitioner row with substrate "
            "annotation (cortex|git|local). The substrate field determines "
            "transport for messaging the practitioner. Used by autonomy's "
            "Ambassador to know who exists in the mesh + how to reach them. "
            "Lane 2 of David's Ambassador design-of-record."
        ),
    )
    practice_context_parser.add_argument(
        '--cortex-url',
        default=None,
        help='Cortex base URL override (else env CORTEX_URL or ~/.empirica/credentials.yaml).',
    )
    practice_context_parser.add_argument(
        '--api-key',
        default=None,
        help='Cortex API key override (else env CORTEX_API_KEY or credentials.yaml).',
    )
    practice_context_parser.add_argument(
        '--ai-id',
        default=None,
        help='Filter to a single ai_id (default: all).',
    )
    practice_context_parser.add_argument(
        '--timeout',
        type=float,
        default=10.0,
        help='HTTP timeout in seconds (default: 10).',
    )
    practice_context_parser.add_argument(
        '--output',
        choices=['human', 'json'],
        default='human',
        help='Output format (default: human table; json for autonomy / scripting).',
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

    # Rust-aware docs assessment — counts pub items + /// docs in
    # Cargo.toml workspace member crates. Use this for Rust projects
    # where docs-assess (Python-focused) and docpistemic (Python-biased
    # discovery) mishandle the surface.
    rust_docs_parser = subparsers.add_parser(
        'rust-docs-assess',
        help='Rust-aware documentation coverage — pub items + /// docs in workspace crates'
    )
    rust_docs_parser.add_argument(
        '--project-root',
        help='Root directory of the project (default: current directory)'
    )
    rust_docs_parser.add_argument(
        '--include',
        action='append',
        default=[],
        help='Path prefix to include (relative to project_root). Can repeat. '
             'When set, only matching crates are walked. Combines with '
             '.empirica/rust_docs.toml [rust_docs] include list.'
    )
    rust_docs_parser.add_argument(
        '--exclude',
        action='append',
        default=[],
        help='Path prefix to skip. Can repeat. Combines with config exclude list. '
             'Excludes win over includes — safety bias is to skip.'
    )
    rust_docs_parser.add_argument(
        '--strict',
        action='store_true',
        help='Only /// outer doc comments count; reject #[doc=...] attribute form. '
             'More conservative, more honest.'
    )
    rust_docs_parser.add_argument(
        '--output',
        choices=['human', 'json'],
        default='human',
        help='Output format. JSON shape compatible with docpistemic for compliance-report.'
    )
