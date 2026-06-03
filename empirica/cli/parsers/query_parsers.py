"""
Unified Query Parsers - Consistent interface for querying epistemic breadcrumbs

Provides a single `empirica query <type>` command with consistent --scope flag
for querying: findings, unknowns, deadends, mistakes, issues, handoffs, goals
"""


def add_query_parsers(subparsers):
    """Add unified query command parser"""

    query_parser = subparsers.add_parser(
        'query',
        help='Query epistemic breadcrumbs (findings, unknowns, deadends, mistakes, issues, handoffs, blockers)',
        description='''
Unified query interface for all epistemic breadcrumbs.

Examples:
  empirica query findings                    # Recent findings (global)
  empirica query unknowns --scope session --session-id <ID>
  empirica query deadends --scope project --project-id <ID>
  empirica query mistakes --limit 20
  empirica query issues --status new
  empirica query handoffs --ai-id empirica
  empirica query goals --status active
  empirica query blockers --limit 10         # Goal-linked unknowns (blockers)
        '''
    )

    # Required: what to query
    query_parser.add_argument(
        'type',
        choices=['findings', 'unknowns', 'deadends', 'mistakes', 'issues', 'handoffs', 'goals', 'blockers'],
        help='Type of breadcrumb to query (blockers = goal-linked unknowns)'
    )

    # Scope selection (consistent across all types)
    query_parser.add_argument(
        '--scope',
        choices=['session', 'project', 'global'],
        default='global',
        help='Query scope: session (one session), project (all sessions in project), global (all)'
    )

    query_parser.add_argument(
        '--session-id',
        help='Session ID (required for session scope)'
    )

    query_parser.add_argument(
        '--project-id',
        help='Project ID (required for project scope)'
    )

    # Common filters
    query_parser.add_argument(
        '--limit',
        type=int,
        default=20,
        help='Maximum results to return (default: 20)'
    )

    query_parser.add_argument(
        '--status',
        help='Filter by status (type-specific: new/resolved for unknowns, active/completed for goals, etc.)'
    )

    query_parser.add_argument(
        '--ai-id',
        help='Filter by AI ID'
    )

    query_parser.add_argument(
        '--since',
        help='Filter by date (ISO format: 2025-01-01)'
    )

    # Output format
    query_parser.add_argument(
        '--output',
        choices=['human', 'json'],
        default='human',
        help='Output format (default: human)'
    )

    query_parser.set_defaults(handler='query')
