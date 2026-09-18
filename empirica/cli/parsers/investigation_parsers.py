"""Investigation command parsers."""


def add_investigation_parsers(subparsers):
    """Add investigation command parsers"""
    # investigate <query>: retrieval over this project's docs + memory — an
    # alias of `project-search --task`. It does not analyze files (a path
    # target is refused); the file/directory/comprehensive types it once
    # advertised never had an implementation behind them.
    investigate_parser = subparsers.add_parser(
        "investigate", help="Retrieve what the practice knows about a topic (alias of project-search --task)"
    )
    investigate_parser.add_argument("target", help="Question or topic to retrieve for (not a file path)")
    investigate_parser.add_argument("--limit", type=int, default=5, help="Number of results to return (default: 5)")
    investigate_parser.add_argument(
        "--global",
        dest="global_search",
        action="store_true",
        help="Also search the global-learnings pool + other LOCAL projects on this machine",
    )
    investigate_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")
    investigate_parser.add_argument(
        "--output",
        choices=["human", "json"],
        default="human",
        help="Output format. empirica-mcp always passes --output json; bare CLI users get human by default.",
    )

    # ========== Epistemic Branching Commands (CASCADE 2.0) ==========

    # investigate-create-branch command
    create_branch_parser = subparsers.add_parser(
        "investigate-create-branch", help="Create parallel investigation branch (epistemic auto-merge)"
    )
    create_branch_parser.add_argument("--session-id", required=True, help="Session ID")
    create_branch_parser.add_argument(
        "--investigation-path", required=True, help="What is being investigated (e.g., oauth2)"
    )
    create_branch_parser.add_argument("--description", help="Description of investigation")
    create_branch_parser.add_argument("--preflight-vectors", help="Epistemic vectors at branch start (JSON)")
    create_branch_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    create_branch_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    # investigate-checkpoint-branch command
    checkpoint_branch_parser = subparsers.add_parser(
        "investigate-checkpoint-branch", help="Checkpoint branch after investigation"
    )
    checkpoint_branch_parser.add_argument("--branch-id", required=True, help="Branch ID")
    checkpoint_branch_parser.add_argument(
        "--postflight-vectors", required=True, help="Epistemic vectors after investigation (JSON)"
    )
    checkpoint_branch_parser.add_argument("--tokens-spent", help="Tokens spent in investigation")
    checkpoint_branch_parser.add_argument("--time-spent", help="Time spent in investigation (minutes)")
    checkpoint_branch_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    checkpoint_branch_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    # investigate-merge-branches command
    merge_branches_parser = subparsers.add_parser(
        "investigate-merge-branches", help="Auto-merge best branch based on epistemic scores"
    )
    merge_branches_parser.add_argument("--session-id", required=True, help="Session ID")
    merge_branches_parser.add_argument("--round", help="Investigation round number")
    merge_branches_parser.add_argument(
        "--tag-losers", action="store_true", help="Auto-tag losing branches as dead ends with divergence reason"
    )
    merge_branches_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    merge_branches_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    # ========== Multi-Persona Orchestration (CASCADE 2.1) ==========

    # investigate-multi command - parallel epistemic agents with different personas
    multi_parser = subparsers.add_parser(
        "investigate-multi", help="Multi-persona parallel investigation with epistemic auto-merge"
    )
    multi_parser.add_argument("--task", required=True, help="Task for all personas to investigate")
    multi_parser.add_argument(
        "--personas", required=True, help="Comma-separated persona IDs (e.g., security,ux,performance)"
    )
    multi_parser.add_argument("--session-id", required=True, help="Session ID")
    multi_parser.add_argument("--context", help="Additional context from parent investigation")
    multi_parser.add_argument(
        "--aggregate-strategy",
        choices=["epistemic-score", "consensus", "all"],
        default="epistemic-score",
        help="How to merge results (default: epistemic-score)",
    )
    multi_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
