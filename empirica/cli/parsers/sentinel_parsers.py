"""
Sentinel Parsers - CLI argument parsers for Sentinel orchestration commands

Commands:
- sentinel-orchestrate: Run autonomous multi-agent orchestration
- sentinel-load-profile: Load domain compliance profile
- sentinel-status: Show Sentinel status and loop tracking
- sentinel-check: Run compliance check with domain gates
"""


def add_sentinel_parsers(subparsers):
    """Add Sentinel-related command parsers"""

    # sentinel-orchestrate
    orchestrate_parser = subparsers.add_parser(
        "sentinel-orchestrate", help="Run autonomous multi-agent orchestration with persona selection"
    )
    orchestrate_parser.add_argument(
        "--session-id", required=True, help="Session ID for orchestration context (required)"
    )
    orchestrate_parser.add_argument(
        "--task", required=True, help="Task description for persona selection and orchestration (required)"
    )
    orchestrate_parser.add_argument(
        "--max-agents", type=int, default=3, help="Maximum parallel agents to spawn (optional, default: 3)"
    )
    orchestrate_parser.add_argument(
        "--profile", help="Domain profile name: general, healthcare or finance (optional; another name is refused)"
    )
    orchestrate_parser.add_argument(
        "--scope-breadth",
        type=float,
        default=0.5,
        help="Scope breadth 0.0-1.0, affects max loops (optional, default: 0.5)",
    )
    orchestrate_parser.add_argument(
        "--scope-duration",
        type=float,
        default=0.5,
        help="Scope duration 0.0-1.0, affects max loops (optional, default: 0.5)",
    )
    orchestrate_parser.add_argument(
        "--merge",
        choices=["union", "consensus", "best_score", "weighted"],
        default="union",
        help="Merge strategy for aggregating findings (optional, default: union)",
    )
    orchestrate_parser.add_argument(
        "--dry-run", action="store_true", help="Select personas without spawning agents (optional)"
    )
    orchestrate_parser.add_argument(
        "--output", choices=["human", "json"], default="human", help="Output format (optional, default: human)"
    )

    # sentinel-load-profile
    profile_parser = subparsers.add_parser(
        "sentinel-load-profile",
        help="Load a domain profile (gates and thresholds); audit and tool settings are configuration only, not enforced",
    )
    profile_parser.add_argument("--session-id", required=True, help="Session ID (required)")
    profile_parser.add_argument(
        "--profile", required=True, help="Profile name: general, healthcare, finance (required)"
    )
    profile_parser.add_argument("--file", help="Custom profile YAML file path (optional, overrides built-in)")
    profile_parser.add_argument(
        "--output", choices=["human", "json"], default="human", help="Output format (optional, default: human)"
    )

    # sentinel-status
    status_parser = subparsers.add_parser(
        "sentinel-status", help="Show Sentinel status, loop tracking, and available profiles"
    )
    status_parser.add_argument("--session-id", required=True, help="Session ID (required)")
    status_parser.add_argument(
        "--output", choices=["human", "json"], default="human", help="Output format (optional, default: human)"
    )

    # sentinel-check
    check_parser = subparsers.add_parser("sentinel-check", help="Run compliance check against domain gates")
    check_parser.add_argument("--session-id", required=True, help="Session ID (required)")
    check_parser.add_argument("--profile", help="Domain profile to use for compliance (optional)")
    check_parser.add_argument("--vectors", help='Epistemic vectors as JSON string or "-" for stdin (optional)')
    check_parser.add_argument(
        "--know", type=float, default=0.5, help="Knowledge level 0.0-1.0 (optional, default: 0.5)"
    )
    check_parser.add_argument(
        "--uncertainty", type=float, default=0.5, help="Uncertainty level 0.0-1.0 (optional, default: 0.5)"
    )
    check_parser.add_argument("--findings", nargs="*", help="List of findings for compliance check (optional)")
    check_parser.add_argument("--unknowns", nargs="*", help="List of unknowns for compliance check (optional)")
    check_parser.add_argument(
        "--output", choices=["human", "json"], default="human", help="Output format (optional, default: human)"
    )
