"""Git checkpoint and project management command parsers."""

from . import format_help_text


def _add_edge_flags(parser, include_evidence_from: bool = False):
    """Add --edge / --related-to (and optional --evidence-from) to a *-log parser.

    Flags are repeatable. They declare outgoing edges on the artifact's note
    JSON (<type>_data.edges) so the commit-context walker can traverse them.

    --edge ID:RELATION  canonical form, e.g. --edge a1008873:supports
    --related-to ID     convenience, defaults relation to "related"
    --evidence-from ID  decision-log only, defaults relation to "evidence"
    """
    parser.add_argument(
        "--edge",
        action="append",
        dest="edges_raw",
        default=[],
        help="Declare a graph edge as ID:RELATION (e.g. abc12345:supports). Repeatable.",
    )
    parser.add_argument(
        "--related-to",
        action="append",
        dest="related_to_ids",
        default=[],
        help="Anchor this artifact to another (relation=related). Repeatable.",
    )
    if include_evidence_from:
        parser.add_argument(
            "--evidence-from",
            action="append",
            dest="evidence_from_ids",
            default=[],
            help="Finding/source IDs that ground this decision (relation=evidence). Repeatable.",
        )


def _add_entity_flags(parser):
    """Add --entity-type, --entity-id, --via flags to artifact parsers.

    These flags enable cross-entity provenance tracking via entity_artifacts
    in workspace.db. When provided, the artifact is linked to the specified
    entity (organization, contact, engagement) in addition to the project.
    """
    parser.add_argument(
        "--entity-type",
        choices=["project", "organization", "contact", "engagement"],
        help="Entity type this artifact relates to (default: project)",
    )
    parser.add_argument("--entity-id", help="Entity UUID (organization, contact, or engagement ID)")
    parser.add_argument("--via", help="Discovery channel (cli, email, linkedin, calendar, agent, web)")


def add_checkpoint_parsers(subparsers):
    """Add git checkpoint management command parsers (Phase 2)"""
    # Checkpoint create command
    checkpoint_create_parser = subparsers.add_parser(
        "checkpoint-create", help="Create git checkpoint for session (Phase 1.5/2.0)"
    )
    checkpoint_create_parser.add_argument(
        "--session-id", required=True, help=format_help_text("Session ID", required=True)
    )
    checkpoint_create_parser.add_argument(
        "--phase",
        choices=["PREFLIGHT", "CHECK", "ACT", "POSTFLIGHT"],
        required=True,
        help=format_help_text("Workflow phase", required=True),
    )
    checkpoint_create_parser.add_argument(
        "--round", type=int, default=1, help=format_help_text("Round number", default=1)
    )
    checkpoint_create_parser.add_argument("--metadata", help=format_help_text("JSON metadata"))
    checkpoint_create_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    checkpoint_create_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Checkpoint load command
    checkpoint_load_parser = subparsers.add_parser("checkpoint-load", help="Load latest checkpoint for session")
    checkpoint_load_parser.add_argument("--session-id", required=True, help="Session ID")
    checkpoint_load_parser.add_argument("--max-age", type=int, default=24, help="Max age in hours (default: 24)")
    checkpoint_load_parser.add_argument("--phase", help="Filter by specific phase (optional)")
    checkpoint_load_parser.add_argument(
        "--output", choices=["table", "json"], default="table", help="Output format (also accepts --output json)"
    )
    # Add backward compatibility with --format
    checkpoint_load_parser.add_argument(
        "--format", dest="output", choices=["json", "table"], help="Output format (deprecated, use --output)"
    )
    checkpoint_load_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Checkpoint list command
    checkpoint_list_parser = subparsers.add_parser("checkpoint-list", help="List checkpoints for session")
    checkpoint_list_parser.add_argument("--session-id", help="Session ID (optional, lists all if omitted)")
    checkpoint_list_parser.add_argument("--limit", type=int, default=10, help="Maximum checkpoints to show")
    checkpoint_list_parser.add_argument("--phase", help="Filter by phase (optional)")
    checkpoint_list_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    checkpoint_list_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Checkpoint diff command
    checkpoint_diff_parser = subparsers.add_parser(
        "checkpoint-diff", help="Show vector differences from last checkpoint"
    )
    checkpoint_diff_parser.add_argument("--session-id", required=True, help="Session ID")
    checkpoint_diff_parser.add_argument("--threshold", type=float, default=0.15, help="Significance threshold")
    checkpoint_diff_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    checkpoint_diff_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Efficiency report command
    checkpoint_sign_parser = subparsers.add_parser(
        "checkpoint-sign", help="Sign checkpoint with AI identity (Phase 2 - Crypto)"
    )
    checkpoint_sign_parser.add_argument("--session-id", required=True, help="Session ID")
    checkpoint_sign_parser.add_argument(
        "--phase", choices=["PREFLIGHT", "CHECK", "ACT", "POSTFLIGHT"], required=True, help="Workflow phase"
    )
    checkpoint_sign_parser.add_argument("--round", type=int, required=True, help="Round number")
    checkpoint_sign_parser.add_argument("--ai-id", required=True, help="AI identity to sign with")
    checkpoint_sign_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    checkpoint_sign_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Checkpoint verify command
    checkpoint_verify_parser = subparsers.add_parser(
        "checkpoint-verify", help="Verify signed checkpoint (Phase 2 - Crypto)"
    )
    checkpoint_verify_parser.add_argument("--session-id", required=True, help="Session ID")
    checkpoint_verify_parser.add_argument(
        "--phase", choices=["PREFLIGHT", "CHECK", "ACT", "POSTFLIGHT"], required=True, help="Workflow phase"
    )
    checkpoint_verify_parser.add_argument("--round", type=int, required=True, help="Round number")
    checkpoint_verify_parser.add_argument("--ai-id", help="AI identity (uses embedded public key if omitted)")
    checkpoint_verify_parser.add_argument("--public-key", help="Public key hex (overrides AI ID)")
    checkpoint_verify_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    checkpoint_verify_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Checkpoint signatures command
    checkpoint_signatures_parser = subparsers.add_parser(
        "checkpoint-signatures", help="List all signed checkpoints (Phase 2 - Crypto)"
    )
    checkpoint_signatures_parser.add_argument("--session-id", help="Filter by session ID (optional)")
    checkpoint_signatures_parser.add_argument("--ai-id", help="AI identity (only needed if no local identities exist)")
    checkpoint_signatures_parser.add_argument(
        "--output", choices=["human", "json"], default="human", help="Output format"
    )
    checkpoint_signatures_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Handoff Reports Commands (Phase 1.6)

    # Handoff create command
    handoff_create_parser = subparsers.add_parser(
        "handoff-create", help="Create handoff report: epistemic (with CASCADE deltas) or planning (documentation-only)"
    )

    # AI-FIRST: Positional config file argument
    handoff_create_parser.add_argument(
        "config", nargs="?", help='JSON config file path or "-" for stdin (AI-first mode)'
    )

    # LEGACY: Flag-based arguments (backward compatible)
    handoff_create_parser.add_argument("--session-id", help="Session UUID (auto-derived from active transaction)")
    handoff_create_parser.add_argument(
        "--task-summary", help=format_help_text("What was accomplished (2-3 sentences)", required=True)
    )
    handoff_create_parser.add_argument("--summary", dest="task_summary", help="Alias for --task-summary")
    handoff_create_parser.add_argument("--key-findings", help=format_help_text("JSON array of findings", required=True))
    handoff_create_parser.add_argument("--findings", dest="key_findings", help="Alias for --key-findings")
    handoff_create_parser.add_argument("--remaining-unknowns", help=format_help_text("JSON array of unknowns"))
    handoff_create_parser.add_argument("--unknowns", dest="remaining_unknowns", help="Alias for --remaining-unknowns")
    handoff_create_parser.add_argument(
        "--next-session-context", help=format_help_text("Critical context for next session", required=True)
    )
    handoff_create_parser.add_argument("--artifacts", help=format_help_text("JSON array of files created"))
    handoff_create_parser.add_argument(
        "--planning-only",
        action="store_true",
        help="Create planning handoff (no CASCADE workflow required) instead of epistemic handoff",
    )
    handoff_create_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    handoff_create_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Handoff query command
    handoff_query_parser = subparsers.add_parser("handoff-query", help="Query handoff reports")
    handoff_query_parser.add_argument("--session-id", help="Specific session UUID")
    handoff_query_parser.add_argument("--ai-id", help="Filter by AI ID")
    handoff_query_parser.add_argument("--limit", type=int, default=5, help="Number of results (default: 5)")
    handoff_query_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    handoff_query_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Mistake Logging Commands (Learning from Failures)

    # Mistake log command
    mistake_log_parser = subparsers.add_parser(
        "mistake-log",
        help=(
            "Log an error YOU made + how to prevent it. Use when you "
            "introduced a bug, mis-applied a pattern, broke an assumption, "
            "or otherwise produced output that needed correction. Differs "
            "from deadend-log (an approach that didn't work) — mistakes "
            "are about your decision-making, dead-ends about the approach. "
            "The --prevention flag is the load-bearing field for future-you."
        ),
    )
    mistake_log_parser.add_argument("--project-id", help="Project UUID")
    mistake_log_parser.add_argument(
        "--session-id", required=False, help="Session UUID (auto-derived from active transaction)"
    )
    mistake_log_parser.add_argument(
        "--mistake", required=True, help="Short title — what was done wrong. Rendered as markdown."
    )
    mistake_log_parser.add_argument(
        "--why-wrong", required=True, help="Short explanation of why it was wrong. Rendered as markdown."
    )
    mistake_log_parser.add_argument("--cost-estimate", help='Estimated time/effort wasted (e.g., "2 hours")')
    mistake_log_parser.add_argument(
        "--root-cause-vector", help='Epistemic vector that caused the mistake (e.g., "KNOW", "CONTEXT")'
    )
    mistake_log_parser.add_argument(
        "--prevention", help="Short — how to prevent this mistake in the future. Rendered as markdown."
    )
    mistake_log_parser.add_argument(
        "--description",
        help="Optional rich markdown body — full account: trigger, signals you missed, recovery path, related findings/dead-ends. Rendered in extension and skill surfaces.",
    )
    mistake_log_parser.add_argument("--goal-id", help="Optional goal identifier this mistake relates to")
    mistake_log_parser.add_argument(
        "--scope",
        choices=["session", "project", "both"],
        help="Scope: session (ephemeral), project (persistent), or both (dual-log). Auto-inferred if omitted.",
    )
    _add_entity_flags(mistake_log_parser)
    _add_edge_flags(mistake_log_parser)
    mistake_log_parser.add_argument(
        "--source",
        action="append",
        dest="source_ids",
        help="Source ID (from source-add). Repeatable for multiple sources.",
    )
    mistake_log_parser.add_argument(
        "--visibility",
        choices=["public", "shared", "local"],
        help="Visibility tier (default: shared). public=world-shareable, shared=team-private, local=machine-only.",
    )
    mistake_log_parser.add_argument(
        "--epistemic-source",
        choices=["intuition", "search", "mixed"],
        help="How this artifact was arrived at: intuition (training data + loaded context, no external retrieval since goal opened), search (external retrieval this session), or mixed.",
    )
    mistake_log_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    mistake_log_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Mistake query command
    mistake_query_parser = subparsers.add_parser(
        "mistake-query",
        help=(
            "Look up logged mistakes — useful before tackling work that "
            "echoes a pattern you've gotten wrong before. Filter by "
            "--session-id (this session's only) or --goal-id (mistakes "
            "against a specific goal). For semantic search across mistake "
            'narratives, use `project-search --task "..."` instead.'
        ),
    )
    mistake_query_parser.add_argument("--session-id", help="Filter by session UUID")
    mistake_query_parser.add_argument("--goal-id", help="Filter by goal UUID")
    mistake_query_parser.add_argument("--limit", type=int, default=10, help="Number of results (default: 10)")
    mistake_query_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    mistake_query_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Project Tracking Commands (Multi-repo/multi-session)

    # Project init command (NEW: initialize Empirica in a new repo)
    project_init_parser = subparsers.add_parser(
        "project-init", help="Initialize Empirica in a new git repository (creates config files)"
    )
    project_init_parser.add_argument("--project-name", help="Project name (defaults to repo name)")
    project_init_parser.add_argument("--project-description", help="Project description")
    project_init_parser.add_argument(
        "--project-id", help="Link to existing workspace project ID (skip DB creation, reuse existing)"
    )
    project_init_parser.add_argument("--enable-beads", action="store_true", help="Enable BEADS by default")
    project_init_parser.add_argument(
        "--create-semantic-index", action="store_true", help="Create SEMANTIC_INDEX.yaml template"
    )
    project_init_parser.add_argument(
        "--type",
        choices=[
            "software",
            "content",
            "research",
            "data",
            "design",
            "operations",
            "strategic",
            "engagement",
            "legal",
        ],
        help="Project type (default: software)",
    )
    project_init_parser.add_argument("--domain", help="Domain taxonomy (e.g., ai/measurement)")
    project_init_parser.add_argument(
        "--classification", choices=["open", "internal", "restricted"], default="internal", help="Access classification"
    )
    project_init_parser.add_argument(
        "--evidence-profile",
        choices=["code", "prose", "web", "hybrid", "auto"],
        default="auto",
        help="Evidence profile for grounded calibration",
    )
    project_init_parser.add_argument("--languages", nargs="+", help="Programming languages")
    project_init_parser.add_argument("--tags", nargs="+", help="Project tags")
    project_init_parser.add_argument("--non-interactive", action="store_true", help="Skip interactive prompts")
    project_init_parser.add_argument("--force", action="store_true", help="Reinitialize if already initialized")
    project_init_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    project_init_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Project update command (update project.yaml fields after init)
    project_update_parser = subparsers.add_parser(
        "project-update", help="Update project.yaml fields (type, domain, contacts, edges, etc.)"
    )
    project_update_parser.add_argument(
        "--type",
        choices=[
            "software",
            "content",
            "research",
            "data",
            "design",
            "operations",
            "strategic",
            "engagement",
            "legal",
        ],
        help="Project type",
    )
    project_update_parser.add_argument("--domain", help="Domain taxonomy (e.g., ai/measurement)")
    project_update_parser.add_argument(
        "--classification", choices=["open", "internal", "restricted"], help="Access classification"
    )
    project_update_parser.add_argument("--status", choices=["active", "dormant", "archived"], help="Project status")
    project_update_parser.add_argument(
        "--evidence-profile",
        choices=["code", "prose", "web", "hybrid", "auto"],
        help="Evidence profile for grounded calibration",
    )
    project_update_parser.add_argument("--languages", nargs="+", help="Set programming languages")
    project_update_parser.add_argument("--tags", nargs="+", help="Set project tags (replaces all)")
    project_update_parser.add_argument("--add-tag", help="Add a single tag")
    project_update_parser.add_argument("--remove-tag", help="Remove a single tag")
    project_update_parser.add_argument("--add-contact", help="Add contact by ID")
    project_update_parser.add_argument("--roles", nargs="+", help="Roles for --add-contact (e.g., owner architect)")
    project_update_parser.add_argument("--remove-contact", help="Remove contact by ID")
    project_update_parser.add_argument("--add-edge", help="Add edge to entity (e.g., project/empirica-iris)")
    project_update_parser.add_argument("--relation", help="Relation type for --add-edge (default: related)")
    project_update_parser.add_argument("--remove-edge", help="Remove edge to entity")
    project_update_parser.add_argument(
        "--migrate", action="store_true", help="Upgrade v1.0 to v2.0 with auto-detected values"
    )
    project_update_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    project_update_parser.add_argument("--verbose", action="store_true", help="Show detailed info")

    # Project create command
    project_create_parser = subparsers.add_parser("project-create", help="Create a new project for multi-repo tracking")
    project_create_parser.add_argument("--name", required=True, help="Project name")
    project_create_parser.add_argument("--description", help="Project description")
    project_create_parser.add_argument(
        "--path",
        help="Path to git repo — also initializes .empirica/ filesystem config (bridges project-create + project-init)",
    )
    project_create_parser.add_argument(
        "--repos", help='JSON array of repository names (e.g., \'["empirica", "empirica-dev"]\')'
    )
    project_create_parser.add_argument(
        "--type",
        choices=["product", "application", "feature", "research", "documentation", "infrastructure", "operations"],
        default="product",
        help="Project type for workspace categorization",
    )
    project_create_parser.add_argument("--tags", help="Tags for categorization (comma-separated or JSON array)")
    project_create_parser.add_argument("--parent", help="Parent project ID for hierarchical organization")
    project_create_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    project_create_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Project handoff command
    project_handoff_parser = subparsers.add_parser("project-handoff", help="Create project-level handoff report")
    project_handoff_parser.add_argument("--project-id", required=True, help="Project UUID")
    project_handoff_parser.add_argument("--summary", required=True, help="Project summary")
    project_handoff_parser.add_argument("--key-decisions", help="JSON array of key decisions")
    project_handoff_parser.add_argument("--patterns", help="JSON array of patterns discovered")
    project_handoff_parser.add_argument("--remaining-work", help="JSON array of remaining work")
    project_handoff_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    project_handoff_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Project list command
    project_list_parser = subparsers.add_parser("project-list", help="List all projects")
    project_list_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    project_list_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Project switch command
    project_switch_parser = subparsers.add_parser(
        "project-switch", help="Switch to a different project with clear context banner"
    )
    project_switch_parser.add_argument("project_identifier", help="Project name or UUID")
    project_switch_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    project_switch_parser.add_argument(
        "--claude-session-id", help="Claude Code conversation UUID (for instance isolation)"
    )

    # Project bootstrap command
    project_bootstrap_parser = subparsers.add_parser(
        "project-bootstrap", aliases=["pb", "bootstrap"], help="Show epistemic breadcrumbs for project"
    )
    project_bootstrap_parser.add_argument(
        "--project-id", required=False, help="Project UUID or name (auto-detected from git remote if omitted)"
    )
    project_bootstrap_parser.add_argument(
        "--session-id", required=False, help="Session UUID (auto-resolved from project if omitted)"
    )
    project_bootstrap_parser.add_argument(
        "--ai-id",
        required=False,
        help="AI identifier to load epistemic handoff for (e.g., empirica, cortex; derives from project basename if omitted)",
    )
    project_bootstrap_parser.add_argument(
        "--subject", help="Subject/workstream to filter by (auto-detected from directory if omitted)"
    )
    project_bootstrap_parser.add_argument(
        "--check-integrity", action="store_true", help="Analyze doc-code integrity (adds ~2s)"
    )
    project_bootstrap_parser.add_argument(
        "--context-to-inject", action="store_true", help="Generate markdown context for AI prompt injection"
    )
    project_bootstrap_parser.add_argument("--task-description", help="Task description for context load balancing")
    project_bootstrap_parser.add_argument(
        "--epistemic-state",
        help='Epistemic vectors from PREFLIGHT as JSON string (e.g., \'{"uncertainty":0.8,"know":0.3}\')',
    )
    project_bootstrap_parser.add_argument(
        "--include-live-state", action="store_true", help="Include current epistemic vectors + git state"
    )
    # DEPRECATED: --fresh-assess removed (legacy). Use 'empirica assess-state' instead for canonical vector capture
    project_bootstrap_parser.add_argument(
        "--trigger",
        choices=["pre_compact", "post_compact", "manual"],
        help="Compact boundary trigger for session auto-resolution",
    )
    project_bootstrap_parser.add_argument(
        "--depth",
        choices=["minimal", "moderate", "full", "auto"],
        default="auto",
        help="Context depth: minimal (~500 tokens), moderate (~1500), full (~3000-5000), auto (drift-based)",
    )
    project_bootstrap_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    project_bootstrap_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")
    project_bootstrap_parser.add_argument(
        "--global",
        dest="include_global",
        action="store_true",
        help="Include global cross-project learnings (requires --task-description)",
    )

    # V1.5 — Single-project register (atomic discover-one + cortex POST)
    project_register_parser = subparsers.add_parser(
        "project-register",
        help=(
            "Atomic single-project register: read .empirica/project.yaml at PATH, "
            "dual-write workspace.db (global_projects + entity_registry), upsert "
            "~/.empirica/registry.yaml, POST to cortex with the local project_id. "
            "Replaces the chained 'projects-discover --register NAME && "
            "projects-bulk-register --include NAME' with one verb for the "
            "AI-as-CLI-user / copy-prompt UX (extension's Discover/Register surface)."
        ),
    )
    project_register_parser.add_argument(
        "path", nargs="?", default=".", help="Project root path (default: current directory)"
    )
    project_register_parser.add_argument(
        "--no-cortex",
        action="store_true",
        help="Stop after local writes (workspace.db + registry.yaml). Use offline-first or when cortex is down.",
    )
    project_register_parser.add_argument(
        "--skip-user-link", action="store_true", help="Skip the defensive user-project link after register."
    )
    project_register_parser.add_argument(
        "--force-metadata-update",
        action="store_true",
        help="Carry force_metadata_update:true so cortex refreshes name/repo_url on an existing row.",
    )
    project_register_parser.add_argument(
        "--cortex-url", help="Override cortex URL (default: ~/.empirica/credentials.yaml)"
    )
    project_register_parser.add_argument(
        "--api-key", help="Override cortex API key (default: ~/.empirica/credentials.yaml)"
    )
    project_register_parser.add_argument(
        "--timeout", type=float, default=10.0, help="Cortex POST timeout in seconds (default: 10)"
    )
    project_register_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Forgejo provisioning (operator / self-hosting power-user PUSH mode)
    forgejo_publish_parser = subparsers.add_parser(
        "forgejo-publish",
        help=(
            "Provision a managed Forgejo remote for a project (operator / "
            "self-hosting power-user tool, not an end-user default): POST "
            "cortex's forgejo-publish endpoint, write the access token 0600, add "
            "the 'forgejo' git remote, and push the cortex-supplied refspecs. "
            "This is the PUSH mode for projects with no existing remote — "
            "distinct from the managed pull-mirror path. Leaves 'origin' "
            "(repo_url) untouched."
        ),
    )
    forgejo_publish_parser.add_argument(
        "path", nargs="?", default=".", help="Project root path (default: current directory)"
    )
    forgejo_publish_parser.add_argument(
        "--rotate",
        action="store_true",
        help="Mint a fresh access token (revokes the prior) — also the way to re-push an already-published project.",
    )
    forgejo_publish_parser.add_argument("--description", help="Optional Forgejo repo description.")
    forgejo_publish_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Workspace overview command
    workspace_overview_parser = subparsers.add_parser(
        "workspace-overview", help="Show epistemic health overview of all projects in workspace"
    )
    workspace_overview_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    workspace_overview_parser.add_argument(
        "--sort-by",
        choices=["activity", "knowledge", "uncertainty", "name"],
        default="activity",
        help="Sort projects by",
    )
    workspace_overview_parser.add_argument(
        "--filter", choices=["active", "inactive", "complete"], help="Filter projects by status"
    )
    workspace_overview_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Entity registry CLI surface (backs the Practice Model — see /empirica-constitution XIII).
    # Reads from ~/.empirica/workspace/workspace.db (entity_registry + entity_memberships).
    entity_list_parser = subparsers.add_parser(
        "entity-list",
        help=(
            "List entities from the workspace registry. Currently populated "
            "types: project, contact, organization, engagement, user. "
            "Default scope is active entities; use --status all to include "
            "inactive/archived."
        ),
    )
    entity_list_parser.add_argument(
        "--type", help="Filter by entity_type (project|contact|organization|engagement|user)"
    )
    entity_list_parser.add_argument(
        "--status",
        choices=["active", "inactive", "archived", "all"],
        default="active",
        help="Filter by status (default: active)",
    )
    entity_list_parser.add_argument("--limit", type=int, default=100, help="Max rows (default: 100)")
    entity_list_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    entity_create_parser = subparsers.add_parser(
        "entity-create",
        help=(
            "Idempotent mint of a contact, engagement, or organization into "
            "the workspace entity registry. Contacts dedupe by email first "
            "(strongest key) then deterministic slug ('c-<name>[-<company>]'); "
            "engagements/organizations dedupe by slug id ('e-'/'o-' prefix, or "
            "pass --id explicitly). Re-minting the same identity returns the "
            "existing entity_id with created=false — a verified no-op. Other "
            "entity types (project, user) are written by their owning pipelines."
        ),
    )
    entity_create_parser.add_argument(
        "--type",
        default="contact",
        choices=["contact", "engagement", "organization"],
        help="Entity type to mint (default: contact)",
    )
    entity_create_parser.add_argument("--name", required=True, help="Entity display name")
    entity_create_parser.add_argument(
        "--id",
        dest="id",
        help="Explicit entity_id (engagement/organization only; defaults to a '<prefix>-<name>' slug)",
    )
    entity_create_parser.add_argument("--email", help="Email (contact primary identity key for dedupe)")
    entity_create_parser.add_argument("--phone", help="Phone number (contact)")
    entity_create_parser.add_argument("--role", help="Role/title at their organization (contact)")
    entity_create_parser.add_argument("--company", help="Company/organization name (contact — folded into the slug)")
    entity_create_parser.add_argument("--description", help="Free-text context for the entity")
    entity_create_parser.add_argument("--metadata", help="Extra metadata as a JSON object string")
    entity_create_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    entity_create_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    entity_show_parser = subparsers.add_parser(
        "entity-show",
        help=(
            "Show one entity's full record plus membership edges (incoming "
            "and outgoing). Pass entity as 'type:id' or split via --type "
            "+ --id. The id can be a full value or unambiguous prefix "
            "(≥4 chars)."
        ),
    )
    entity_show_parser.add_argument("entity", nargs="?", help='Entity reference as "type:id" (or use --type + --id)')
    entity_show_parser.add_argument("--type", dest="entity_type", help="Entity type (alternative to positional)")
    entity_show_parser.add_argument("--id", dest="entity_id", help="Entity id (alternative to positional)")
    entity_show_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    entity_walk_parser = subparsers.add_parser(
        "entity-walk",
        help=(
            "BFS the membership graph from a starting entity, following "
            "edges in both directions (member_of + members). Pass the "
            "start node as 'type:id'. Default depth is 2; increase with "
            "--depth. Cycles are detected and skipped."
        ),
    )
    entity_walk_parser.add_argument("entity", nargs="?", help='Start entity as "type:id" (or use --type + --id)')
    entity_walk_parser.add_argument("--type", dest="entity_type", help="Entity type (alternative to positional)")
    entity_walk_parser.add_argument("--id", dest="entity_id", help="Entity id (alternative to positional)")
    entity_walk_parser.add_argument("--depth", type=int, default=2, help="Max traversal depth (default: 2)")
    entity_walk_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    entity_delete_parser = subparsers.add_parser(
        "entity-delete",
        help=(
            "Delete an entity. Default is a reversible soft-archive "
            "(status='archived' + close memberships); --hard does an irreversible "
            "dependent-order cascade and requires --confirm. Pass as 'type:id'."
        ),
    )
    entity_delete_parser.add_argument("entity", nargs="?", help='Entity as "type:id" (or use --type + --id)')
    entity_delete_parser.add_argument("--type", dest="entity_type", help="Entity type (alternative to positional)")
    entity_delete_parser.add_argument("--id", dest="entity_id", help="Entity id (alternative to positional)")
    entity_delete_parser.add_argument(
        "--hard", action="store_true", help="Irreversible dependent-order cascade delete (requires --confirm)"
    )
    entity_delete_parser.add_argument("--confirm", action="store_true", help="Confirm an irreversible --hard delete")
    entity_delete_parser.add_argument("--dry-run", action="store_true", help="Preview the effect without mutating")
    entity_delete_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # --- engagement substrate CLI (rides the entities-mint path; operational) ---
    engagement_create_parser = subparsers.add_parser(
        "engagement-create",
        help=(
            "Create an engagement: mints the engagement entity (the entities-mint "
            "path) then writes the operational sidecar row. Idempotent by slug. "
            "Optionally link to an organization with --org (role='ticket_of')."
        ),
    )
    engagement_create_parser.add_argument("--title", required=True, help="Engagement title")
    engagement_create_parser.add_argument(
        "--id", dest="id", help="Explicit engagement_id (defaults to an 'e-<title>' slug)"
    )
    engagement_create_parser.add_argument(
        "--domain", help="Engagement domain (outreach|sales|support|security|infra|onboarding|...)"
    )
    engagement_create_parser.add_argument("--stage", help="Initial stage_id (must belong to --domain)")
    engagement_create_parser.add_argument(
        "--engagement-type", dest="engagement_type", default="outreach", help="Engagement type (default: outreach)"
    )
    engagement_create_parser.add_argument("--org", help="Organization entity_id to link as role='ticket_of'")
    engagement_create_parser.add_argument("--description", help="Free-text context")
    engagement_create_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    engagement_create_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    engagement_list_parser = subparsers.add_parser(
        "engagement-list",
        help="List engagements (active-by-default) filtered by --domain / --lifecycle / --org; "
        "--include-closed for terminal ones.",
    )
    engagement_list_parser.add_argument("--domain", help="Filter by domain")
    engagement_list_parser.add_argument(
        "--lifecycle",
        help="Filter by lifecycle_state (planned|open|in_progress|blocked|closed), or 'all' for the full set",
    )
    engagement_list_parser.add_argument("--org", help="Scope to an organization's tickets (role='ticket_of')")
    engagement_list_parser.add_argument(
        "--include-closed",
        action="store_true",
        help="Legacy sugar — add terminal (closed) engagements back. Default: active-only "
        "(open|in_progress|blocked); pre-active 'planned' stays out unless requested or --lifecycle all. "
        "Ignored when --lifecycle is given.",
    )
    engagement_list_parser.add_argument("--limit", type=int, default=100, help="Max rows (default: 100)")
    engagement_list_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    engagement_list_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    engagement_show_parser = subparsers.add_parser(
        "engagement-show",
        help="Show one engagement's record + its membership edges.",
    )
    engagement_show_parser.add_argument("engagement_id", help="Engagement id (full value or unambiguous prefix)")
    engagement_show_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    engagement_show_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    engagement_walk_parser = subparsers.add_parser(
        "engagement-walk",
        help="BFS the membership graph from an engagement (default depth 2).",
    )
    engagement_walk_parser.add_argument("engagement_id", help="Engagement id (full value or unambiguous prefix)")
    engagement_walk_parser.add_argument("--depth", type=int, default=2, help="Max traversal depth (default: 2)")
    engagement_walk_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    engagement_walk_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    entity_search_parser = subparsers.add_parser(
        "entity-search",
        help=(
            "Text-search entities by display_name + description (case-"
            "insensitive LIKE). For semantic search across artifacts, use "
            "project-search or workspace-search instead."
        ),
    )
    entity_search_parser.add_argument("query", help='Search query (e.g. "MastersOfDirt")')
    entity_search_parser.add_argument("--type", help="Optional entity_type filter")
    entity_search_parser.add_argument(
        "--semantic",
        action="store_true",
        help="Semantic vector search over entity-row points (§6.2) instead of SQL LIKE",
    )
    entity_search_parser.add_argument(
        "--status",
        choices=["active", "inactive", "archived", "all"],
        default="active",
        help="Filter by status (default: active)",
    )
    entity_search_parser.add_argument("--limit", type=int, default=50, help="Max results (default: 50)")
    entity_search_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    entity_reindex_parser = subparsers.add_parser(
        "entity-reindex",
        help=(
            "Backfill: embed every entity_registry row as a searchable "
            "workspace_index point (ERM §6.2 point_kind='entity'). Idempotent "
            "(stable ids → upsert). Run once after upgrading, or to reindex."
        ),
    )
    entity_reindex_parser.add_argument("--type", help="Optional entity_type filter (contact, organization, engagement)")
    entity_reindex_parser.add_argument("--dry-run", action="store_true", help="Count rows without embedding")
    entity_reindex_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    entity_link_parser = subparsers.add_parser(
        "entity-link",
        help=(
            "Write (or soft-close) a typed membership edge between two "
            "entities: '<member> is <role> of <group>'. The write peer to "
            "entity-show/-walk's read path. Both refs are 'type:id'. "
            "Idempotent on the edge — re-linking updates role/notes and "
            "re-activates a soft-closed edge. Edges are never deleted; "
            "--close soft-closes (stamps left_at) so history stays auditable. "
            "Example: entity-link engagement:e-cowork-recovery "
            "organization:o-nle --role ticket_of"
        ),
    )
    entity_link_parser.add_argument("member", help="Member entity as 'type:id' (e.g. engagement:e-x)")
    entity_link_parser.add_argument("group", help="Group entity as 'type:id' (e.g. organization:o-y)")
    entity_link_parser.add_argument("--role", help="Relation verb for the edge (e.g. ticket_of, member, serves)")
    entity_link_parser.add_argument("--notes", help="Optional free-text note on the edge")
    entity_link_parser.add_argument(
        "--close", action="store_true", help="Soft-close the edge (stamp left_at) instead of writing it"
    )
    entity_link_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    entity_link_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    # Workspace map command
    workspace_map_parser = subparsers.add_parser(
        "workspace-map", help="Discover git repositories in parent directory and show epistemic health"
    )
    workspace_map_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    workspace_map_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Workspace init command - EPISTEMIC INITIALIZATION
    workspace_init_parser = subparsers.add_parser(
        "workspace-init", help="Initialize workspace with epistemic self-awareness (uses CASCADE workflow)"
    )
    workspace_init_parser.add_argument("--path", type=str, help="Workspace path (defaults to current directory)")
    workspace_init_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    workspace_init_parser.add_argument(
        "--non-interactive", action="store_true", help="Skip user questions, use defaults"
    )
    workspace_init_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Workspace list command - show projects with types and tags
    workspace_list_parser = subparsers.add_parser(
        "workspace-list", help="List projects with types, tags, and hierarchical relationships"
    )
    workspace_list_parser.add_argument(
        "--type",
        choices=["product", "application", "feature", "research", "documentation", "infrastructure", "operations"],
        help="Filter by project type",
    )
    workspace_list_parser.add_argument("--tags", help="Filter by tags (comma-separated, matches any)")
    workspace_list_parser.add_argument("--parent", help="Show only children of this project ID")
    workspace_list_parser.add_argument("--tree", action="store_true", help="Show hierarchical tree view")
    workspace_list_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    workspace_list_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Workspace backfill entities — populate entity_registry for existing global_projects
    workspace_backfill_entities_parser = subparsers.add_parser(
        "workspace-backfill-entities",
        help=(
            "Backfill workspace.db.entity_registry with entity_type=project rows "
            "for every existing global_projects row. Closes the gap where projects "
            "registered before the dual-write path don't appear in the Practice "
            "Model surface (extension dashboard, entity-list/-show/-walk). "
            "Idempotent."
        ),
    )
    workspace_backfill_entities_parser.add_argument(
        "--dry-run", action="store_true", help="Preview what would change without writing"
    )
    workspace_backfill_entities_parser.add_argument(
        "--output", choices=["human", "json"], default="human", help="Output format"
    )
    workspace_backfill_entities_parser.add_argument(
        "--verbose", action="store_true", help="Show detailed operation info"
    )

    # Ecosystem check command - analyze cross-project dependencies and impact
    ecosystem_check_parser = subparsers.add_parser(
        "ecosystem-check", help="Analyze ecosystem dependencies, impact, and health from ecosystem.yaml"
    )
    ecosystem_check_parser.add_argument("--file", help="File or module path to check impact for")
    ecosystem_check_parser.add_argument("--project", help="Project name to check downstream/upstream")
    ecosystem_check_parser.add_argument(
        "--role", help="Filter projects by role (core, extension, ecosystem-tool, etc.)"
    )
    ecosystem_check_parser.add_argument("--tag", help="Filter projects by tag")
    ecosystem_check_parser.add_argument("--validate", action="store_true", help="Validate manifest integrity")
    ecosystem_check_parser.add_argument("--manifest", help="Path to ecosystem.yaml (auto-detected if not specified)")
    ecosystem_check_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    ecosystem_check_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Engagement focus command — set active engagement for auto-linking
    engagement_focus_parser = subparsers.add_parser(
        "engagement-focus", help="Set active engagement for current transaction (auto-links all artifacts)"
    )
    engagement_focus_parser.add_argument("engagement_id", nargs="?", help="Engagement UUID or name")
    engagement_focus_parser.add_argument("--clear", action="store_true", help="Clear active engagement")
    engagement_focus_parser.add_argument("--output", choices=["json", "default"], default="json", help="Output format")

    # Workspace search command — cross-project entity-navigable semantic search
    workspace_search_parser = subparsers.add_parser(
        "workspace-search", help="Search across all projects by entity or semantic query"
    )
    workspace_search_parser.add_argument("--entity", help="Entity filter: TYPE/ID (e.g., contact/david, org/acme)")
    workspace_search_parser.add_argument("--task", help="Semantic search query")
    workspace_search_parser.add_argument("--project-id", help="Restrict to specific project")
    workspace_search_parser.add_argument("--limit", type=int, default=20, help="Maximum results")
    workspace_search_parser.add_argument("--output", choices=["json", "human"], default="json", help="Output format")

    # Git abstraction: save command — git add + commit with auto-message
    save_parser = subparsers.add_parser("save", help="Save current work (git add + commit with auto-generated message)")
    save_parser.add_argument("--message", "-m", help="Custom commit message")
    save_parser.add_argument("--output", choices=["json", "default"], default="json", help="Output format")

    # Git abstraction: history command — epistemic timeline from git log + notes
    history_parser = subparsers.add_parser("history", help="Show epistemic timeline from git log + notes")
    history_parser.add_argument("--entity", help="Filter by entity: TYPE/ID")
    history_parser.add_argument("--limit", type=int, default=20, help="Maximum entries")
    history_parser.add_argument("--output", choices=["json", "human"], default="human", help="Output format")

    # Project semantic search command (Qdrant-backed)
    project_search_parser = subparsers.add_parser(
        "project-search", help="Semantic search for relevant docs/memory by task description"
    )
    project_search_parser.add_argument("--project-id", required=True, help="Project UUID")
    project_search_parser.add_argument("--task", required=True, help="Task description to search for")
    project_search_parser.add_argument(
        "--type",
        choices=[
            "focused",
            "all",
            "intelligence",
            "docs",
            "memory",
            "eidetic",
            "episodic",
            "assumptions",
            "decisions",
            "goals",
        ],
        default="focused",
        help="Result type: focused (docs+eidetic+episodic), all, intelligence (goals+decisions+assumptions), or single collection",
    )
    project_search_parser.add_argument("--limit", type=int, default=5, help="Number of results to return (default: 5)")
    project_search_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    project_search_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")
    project_search_parser.add_argument(
        "--global",
        dest="global_search",
        action="store_true",
        help="Also search the global-learnings pool + other LOCAL projects (semantic, this machine). "
        "Cross-practice/mesh search is `cortex investigate`.",
    )

    # Project embed (build vectors) command
    project_embed_parser = subparsers.add_parser(
        "project-embed", help="Embed project docs & memory into Qdrant for semantic search"
    )
    project_embed_parser.add_argument("--project-id", required=True, help="Project UUID")
    project_embed_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    project_embed_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")
    project_embed_parser.add_argument(
        "--global",
        dest="global_sync",
        action="store_true",
        help="Sync high-impact items to global learnings collection",
    )
    project_embed_parser.add_argument(
        "--min-impact", type=float, default=0.7, help="Minimum impact for global sync (default: 0.7)"
    )

    # Code embed (AST-based API surface extraction)
    code_embed_parser = subparsers.add_parser(
        "code-embed", help="Extract and embed Python API surfaces into Qdrant for semantic search"
    )
    code_embed_parser.add_argument("--project-id", required=True, help="Project UUID")
    code_embed_parser.add_argument(
        "--path", default=None, help="Root directory to scan (default: project root from DB, or cwd)"
    )
    code_embed_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Documentation completeness check
    doc_check_parser = subparsers.add_parser("doc-check", help="Compute documentation completeness and suggest updates")
    doc_check_parser.add_argument("--project-id", required=True, help="Project UUID")
    doc_check_parser.add_argument("--session-id", help="Optional session UUID for context")
    doc_check_parser.add_argument("--goal-id", help="Optional goal UUID for context")
    doc_check_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    doc_check_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # NOTE: skill-suggest and skill-fetch are implemented in skill_commands.py

    # Finding log command
    finding_log_parser = subparsers.add_parser(
        "finding-log",
        aliases=["fl"],
        help=(
            "Log a discovery — something concrete you NOW know that wasn't "
            "obvious before. Use for: facts surfaced from a read/grep, "
            "patterns observed in the codebase, verified assumptions, "
            "resolved unknowns, behaviors confirmed by experiment. The "
            "core building block of the project knowledge graph. "
            "--impact 0.0-1.0 weights how much it matters. Pair with "
            "--source <id> when the finding came from external material."
        ),
    )
    finding_log_parser.add_argument("config", nargs="?", help="JSON config file or - for stdin (AI-first mode)")
    finding_log_parser.add_argument("--project-id", required=False, help="Project UUID")
    finding_log_parser.add_argument("--session-id", required=False, help="Session UUID")
    finding_log_parser.add_argument(
        "--finding",
        required=False,
        help="Short title — what was learned/discovered. Rendered as markdown; use --description for rich body if the title alone is too dense.",
    )
    finding_log_parser.add_argument(
        "--description",
        help="Optional rich markdown body — rendered in the extension and skill surfaces. Use sections, lists, code blocks, tables, links for nuance that doesn't fit the short --finding title.",
    )
    finding_log_parser.add_argument("--goal-id", help="Optional goal UUID")
    finding_log_parser.add_argument("--task-id", help="Optional task UUID")
    finding_log_parser.add_argument(
        "--subject", help="Subject/workstream identifier (auto-detected from directory if omitted)"
    )
    finding_log_parser.add_argument(
        "--impact",
        type=float,
        help="Impact score 0.0-1.0 (importance of this finding, auto-derived from CASCADE if omitted)",
    )
    finding_log_parser.add_argument(
        "--scope",
        choices=["session", "project", "both"],
        help="Scope: session (ephemeral), project (persistent), or both (dual-log). Auto-inferred if omitted.",
    )
    finding_log_parser.add_argument(
        "--source",
        action="append",
        dest="source_ids",
        help="Source ID (from source-add). Repeatable for multiple sources.",
    )
    _add_entity_flags(finding_log_parser)
    _add_edge_flags(finding_log_parser)
    finding_log_parser.add_argument(
        "--visibility",
        choices=["public", "shared", "local"],
        help="Visibility tier (default: shared). public=world-shareable, shared=team-private, local=machine-only.",
    )
    finding_log_parser.add_argument(
        "--epistemic-source",
        choices=["intuition", "search", "mixed"],
        help="How this artifact was arrived at: intuition (training data + loaded context, no external retrieval since goal opened), search (external retrieval this session), or mixed.",
    )
    finding_log_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    finding_log_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Note command — fast scratchpad / note-to-self
    note_parser = subparsers.add_parser(
        "note",
        help=(
            "Jot a quick note-to-self while in flow — a scratchpad for things "
            "to check on after the current work. Faster + lower-friction than a "
            "full finding/decision: pure metadata, NOT shared, NOT embedded. "
            "Notes are transaction-scoped and surface at POSTFLIGHT for triage "
            "(promote to an artifact/goal, or discard). They survive context "
            "compaction. Use --list to review, --clear to mark triaged."
        ),
    )
    note_parser.add_argument("text", nargs="?", help="The note text (positional, the common case)")
    note_parser.add_argument("--text", dest="text_flag", help="The note text (flag form, for MCP/scripts)")
    note_parser.add_argument("--tag", help="Optional free-form tag (suggested: followup | doubt | idea)")
    note_parser.add_argument(
        "--list", action="store_true", help="List untriaged notes for the current transaction/session"
    )
    note_parser.add_argument(
        "--clear", action="store_true", help="Mark the current transaction/session notes as triaged"
    )
    note_parser.add_argument("--session-id", required=False, help="Session UUID")
    note_parser.add_argument("--project-id", required=False, help="Project UUID")
    note_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Unknown log command
    unknown_log_parser = subparsers.add_parser(
        "unknown-log",
        aliases=["ul"],
        help=(
            "Log an open question — something you'd need to know before "
            "acting confidently, but don't yet. Use when investigation "
            "surfaces a gap (file not read yet, behavior unclear, decision "
            "pending input). The Sentinel CHECK gate reads open unknowns "
            "as a signal you may still be noetic. Close with "
            "`unknown-resolve` once answered (ideally with a finding link)."
        ),
    )
    unknown_log_parser.add_argument("config", nargs="?", help="JSON config file or - for stdin (AI-first mode)")
    unknown_log_parser.add_argument("--project-id", required=False, help="Project UUID")
    unknown_log_parser.add_argument("--session-id", required=False, help="Session UUID")
    unknown_log_parser.add_argument(
        "--unknown",
        required=False,
        help="Short title — what is unclear/unknown. Rendered as markdown; use --description for rich body when the question has context.",
    )
    unknown_log_parser.add_argument(
        "--description",
        help="Optional rich markdown body — context behind the question, what you tried, what would resolve it. Rendered in extension and skill surfaces.",
    )
    unknown_log_parser.add_argument("--goal-id", help="Optional goal UUID")
    unknown_log_parser.add_argument("--task-id", help="Optional task UUID")
    unknown_log_parser.add_argument(
        "--subject", help="Subject/workstream identifier (auto-detected from directory if omitted)"
    )
    unknown_log_parser.add_argument(
        "--impact",
        type=float,
        help="Impact score 0.0-1.0 (importance of this unknown, auto-derived from CASCADE if omitted)",
    )
    unknown_log_parser.add_argument(
        "--scope",
        choices=["session", "project", "both"],
        help="Scope: session (ephemeral), project (persistent), or both (dual-log). Auto-inferred if omitted.",
    )
    unknown_log_parser.add_argument(
        "--source",
        action="append",
        dest="source_ids",
        help="Source ID (from source-add). Repeatable for multiple sources.",
    )
    _add_entity_flags(unknown_log_parser)
    _add_edge_flags(unknown_log_parser)
    unknown_log_parser.add_argument(
        "--visibility",
        choices=["public", "shared", "local"],
        help="Visibility tier (default: shared). public=world-shareable, shared=team-private, local=machine-only.",
    )
    unknown_log_parser.add_argument(
        "--epistemic-source",
        choices=["intuition", "search", "mixed"],
        help="How this artifact was arrived at: intuition (training data + loaded context, no external retrieval since goal opened), search (external retrieval this session), or mixed.",
    )
    unknown_log_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    unknown_log_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Unknown resolve command
    unknown_resolve_parser = subparsers.add_parser(
        "unknown-resolve",
        help=(
            "Close an open unknown — pass the answer as --resolved-by and "
            "optionally link the finding that answered it via --finding. "
            "Run before POSTFLIGHT to drop the CHECK-gate weight of stale "
            "questions and surface the resolution as evidence for the "
            "next transaction's grounded calibration."
        ),
    )
    unknown_resolve_parser.add_argument("--unknown-id", required=True, help="Unknown UUID")
    unknown_resolve_parser.add_argument("--resolved-by", required=True, help="How was this unknown resolved?")
    unknown_resolve_parser.add_argument(
        "--finding", dest="resolution_finding_id", help="Finding ID that answered this unknown (provenance link)"
    )
    unknown_resolve_parser.add_argument(
        "--output", choices=["human", "json"], default="json", help="Output format (default: json)"
    )
    unknown_resolve_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Finding resolve command (#307 — the prune primitive)
    finding_resolve_parser = subparsers.add_parser(
        "finding-resolve",
        help=(
            "Resolve/supersede a finding — kept for history, dropped from live "
            "retrieval (PREFLIGHT/CHECK relevant_findings). Findings are the fruit "
            "that must be pluckable: recency-decay only knows 'old', never "
            "'superseded'. Run to stop a stale/superseded finding resurfacing."
        ),
    )
    finding_resolve_parser.add_argument("finding_id", help="Finding UUID (full or 8+ char prefix)")
    finding_resolve_parser.add_argument(
        "--resolution", required=True, help="Why resolved (e.g. stale, superseded, invalidated)"
    )
    finding_resolve_parser.add_argument(
        "--superseded-by",
        dest="superseded_by",
        help="Finding ID that replaced it (superseded finding → its replacement)",
    )
    finding_resolve_parser.add_argument(
        "--output", choices=["human", "json"], default="json", help="Output format (default: json)"
    )
    finding_resolve_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Unknown list command
    unknown_list_parser = subparsers.add_parser(
        "unknown-list",
        help=(
            "List open project unknowns (default) or resolved ones with "
            "--resolved. Useful at PREFLIGHT to surface stale questions "
            "that should be cleaned up, or between transactions to triage "
            "what still needs investigation. For cross-project unknowns, "
            'use `project-search --task "..." --global`.'
        ),
    )
    unknown_list_parser.add_argument("--project-id", required=False, help="Project UUID")
    unknown_list_parser.add_argument("--session-id", required=False, help="Session UUID (to derive project)")
    unknown_list_parser.add_argument("--resolved", action="store_true", help="Show resolved unknowns instead of open")
    unknown_list_parser.add_argument("--all", action="store_true", dest="show_all", help="Show both open and resolved")
    unknown_list_parser.add_argument("--subject", help="Filter by subject/workstream")
    unknown_list_parser.add_argument("--limit", type=int, default=30, help="Max unknowns to show (default: 30)")
    unknown_list_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    unknown_list_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Dead end log command
    deadend_log_parser = subparsers.add_parser(
        "deadend-log",
        aliases=["de"],
        help=(
            "Log an approach that didn't work. Use when you tried "
            "something and the result rules out a path (lib X doesn't "
            "support Y, refactor strategy hit a wall, fix attempt made "
            "things worse). Differs from mistake-log (an error you made) "
            "— dead-ends are about the approach. CHECK reads dead-ends "
            "as evidence of search effort. --why-failed is load-bearing."
        ),
    )
    deadend_log_parser.add_argument("config", nargs="?", help="JSON config file or - for stdin (AI-first mode)")
    deadend_log_parser.add_argument("--project-id", required=False, help="Project UUID")
    deadend_log_parser.add_argument("--session-id", required=False, help="Session UUID")
    deadend_log_parser.add_argument(
        "--approach",
        required=False,
        help="Short title — what approach was tried. Rendered as markdown; use --description for the full story.",
    )
    deadend_log_parser.add_argument(
        "--why-failed", required=False, help="Short title — why it failed. Rendered as markdown."
    )
    deadend_log_parser.add_argument(
        "--description",
        help="Optional rich markdown body — full account: what you expected, what happened, signals you noticed, what alternative might work. Rendered in extension and skill surfaces.",
    )
    deadend_log_parser.add_argument("--goal-id", help="Optional goal UUID")
    deadend_log_parser.add_argument("--task-id", help="Optional task UUID")
    deadend_log_parser.add_argument(
        "--subject", help="Subject/workstream identifier (auto-detected from directory if omitted)"
    )
    deadend_log_parser.add_argument(
        "--impact",
        type=float,
        help="Impact score 0.0-1.0 (importance of this dead end, auto-derived from CASCADE if omitted)",
    )
    deadend_log_parser.add_argument(
        "--scope",
        choices=["session", "project", "both"],
        help="Scope: session (ephemeral), project (persistent), or both (dual-log). Auto-inferred if omitted.",
    )
    deadend_log_parser.add_argument(
        "--source",
        action="append",
        dest="source_ids",
        help="Source ID (from source-add). Repeatable for multiple sources.",
    )
    _add_entity_flags(deadend_log_parser)
    _add_edge_flags(deadend_log_parser)
    deadend_log_parser.add_argument(
        "--visibility",
        choices=["public", "shared", "local"],
        help="Visibility tier (default: shared). public=world-shareable, shared=team-private, local=machine-only.",
    )
    deadend_log_parser.add_argument(
        "--epistemic-source",
        choices=["intuition", "search", "mixed"],
        help="How this artifact was arrived at: intuition (training data + loaded context, no external retrieval since goal opened), search (external retrieval this session), or mixed.",
    )
    deadend_log_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    deadend_log_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Assumption log command
    assumption_log_parser = subparsers.add_parser(
        "assumption-log",
        help=(
            "Log a belief you're acting on without verification. Use when "
            'proceeding requires taking something for granted (e.g. "Redis '
            'is available", "the spec is current"). Differs from '
            "finding-log (verified fact) — assumptions are explicitly "
            "unverified, with a --confidence 0.0-1.0 stating how much you "
            "trust them. Convert to finding-log once verified, or "
            "decision-log if you decide to act despite the uncertainty."
        ),
    )
    assumption_log_parser.add_argument("config", nargs="?", help="JSON config file or - for stdin (AI-first mode)")
    assumption_log_parser.add_argument("--project-id", required=False, help="Project UUID")
    assumption_log_parser.add_argument("--session-id", required=False, help="Session UUID")
    assumption_log_parser.add_argument(
        "--assumption",
        required=False,
        help="Short title — the assumption being made. Rendered as markdown; use --description to record the basis for the confidence.",
    )
    assumption_log_parser.add_argument(
        "--description",
        help="Optional rich markdown body — what would verify or falsify the assumption, why you're leaning toward the stated confidence, how brittle it is. Rendered in extension and skill surfaces.",
    )
    assumption_log_parser.add_argument(
        "--confidence", type=float, default=0.5, help="Confidence in this assumption (0.0-1.0)"
    )
    assumption_log_parser.add_argument("--domain", help="Domain scope (e.g., security, architecture)")
    assumption_log_parser.add_argument("--goal-id", help="Optional goal UUID")
    _add_entity_flags(assumption_log_parser)
    _add_edge_flags(assumption_log_parser)
    assumption_log_parser.add_argument(
        "--source",
        action="append",
        dest="source_ids",
        help="Source ID (from source-add). Repeatable for multiple sources.",
    )
    assumption_log_parser.add_argument(
        "--visibility",
        choices=["public", "shared", "local"],
        help="Visibility tier (default: shared). public=world-shareable, shared=team-private, local=machine-only.",
    )
    assumption_log_parser.add_argument(
        "--epistemic-source",
        choices=["intuition", "search", "mixed"],
        help="How this artifact was arrived at: intuition (training data + loaded context, no external retrieval since goal opened), search (external retrieval this session), or mixed.",
    )
    assumption_log_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Decision log command
    decision_log_parser = subparsers.add_parser(
        "decision-log",
        help=(
            "Log a deliberate choice between alternatives. Use at every "
            "fork: which library, which approach, which trade-off, even "
            '"keep the current behavior" when it was reconsidered. '
            "--rationale explains the WHY, --alternatives lists what was "
            "rejected, --reversibility flags how easily it can be undone "
            "(exploratory / committal / forced). Link supporting findings "
            'via --evidence <id>. The audit trail for "why is the code '
            'this way?" questions.'
        ),
    )
    decision_log_parser.add_argument("config", nargs="?", help="JSON config file or - for stdin (AI-first mode)")
    decision_log_parser.add_argument("--project-id", required=False, help="Project UUID")
    decision_log_parser.add_argument("--session-id", required=False, help="Session UUID")
    decision_log_parser.add_argument(
        "--choice",
        required=False,
        help="Short title — the choice made. Rendered as markdown; use --description for the full deliberation.",
    )
    decision_log_parser.add_argument(
        "--alternatives", required=False, help="Alternatives considered (comma-separated or JSON array)"
    )
    decision_log_parser.add_argument(
        "--rationale",
        required=False,
        help="Short rationale — why this choice was made. Rendered as markdown; use --description for extended reasoning.",
    )
    decision_log_parser.add_argument(
        "--description",
        help="Optional rich markdown body — extended reasoning, trade-offs table, what would change this decision, related findings. Rendered in extension and skill surfaces.",
    )
    decision_log_parser.add_argument(
        "--confidence", type=float, default=0.7, help="Confidence in this decision (0.0-1.0)"
    )
    decision_log_parser.add_argument(
        "--reversibility",
        choices=["exploratory", "committal", "forced"],
        default="exploratory",
        help="How reversible is this decision?",
    )
    decision_log_parser.add_argument("--domain", help="Domain scope (e.g., security, architecture)")
    decision_log_parser.add_argument("--goal-id", help="Optional goal UUID")
    decision_log_parser.add_argument(
        "--evidence",
        action="append",
        dest="evidence_refs",
        help="Finding ID as evidence for this decision. Repeatable for multiple findings.",
    )
    decision_log_parser.add_argument(
        "--source",
        action="append",
        dest="source_ids",
        help="Source ID (from source-add) for external citations. Repeatable.",
    )
    _add_entity_flags(decision_log_parser)
    _add_edge_flags(decision_log_parser, include_evidence_from=True)
    decision_log_parser.add_argument(
        "--visibility",
        choices=["public", "shared", "local"],
        help="Visibility tier (default: shared). public=world-shareable, shared=team-private, local=machine-only.",
    )
    decision_log_parser.add_argument(
        "--epistemic-source",
        choices=["intuition", "search", "mixed"],
        help="How this artifact was arrived at: intuition (training data + loaded context, no external retrieval since goal opened), search (external retrieval this session), or mixed.",
    )
    decision_log_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # refdoc-add CLI removed in goal 3d6aeb08 Phase 2. The Python API
    # (BreadcrumbRepository.add_reference_doc / SessionDatabase.add_reference_doc)
    # remains in place for internal callers; it routes to epistemic_sources
    # WHERE source_type='pointer' since migration 046. Use source-add at
    # the CLI surface.

    # Source add command — entity-agnostic source logging with direction
    source_add_parser = subparsers.add_parser(
        "source-add",
        help=(
            "Register external material as a citable source. Use for any "
            "evidence outside the current code (RFC, paper, blog, customer "
            "call, design doc, screenshot, vendor contract). Pass --noetic "
            "when it informed your knowledge, --praxic when you produced "
            "it as output. Returns a source UUID — link it from findings / "
            "decisions / dead-ends via `--source <uuid>` on those *-log "
            "commands so the audit trail traces back to origin."
        ),
    )
    source_add_parser.add_argument("--title", required=True, help="Source title")
    source_add_parser.add_argument("--description", help="Source description")
    source_add_parser.add_argument(
        "--source-type",
        default="document",
        help="Source type (document, meeting, email, calendar, code, web, design, api)",
    )
    source_add_parser.add_argument("--path", help="File path (for local documents)")
    source_add_parser.add_argument(
        "--media",
        help=(
            "Local media/binary file (image, etc.) to register AND upload to "
            "cortex as a blob, so peers can fetch it cross-tenant via "
            "`source-get`. Implies --path. Pair with --visibility shared for "
            "cross-tenant reads (producer half of media-bearing sources)."
        ),
    )
    source_add_parser.add_argument("--url", help="URL (for web sources)")
    source_add_parser.add_argument("--cortex-url", help="Cortex URL override (default: credentials.yaml)")
    source_add_parser.add_argument("--api-key", help="Cortex API key override (default: credentials.yaml)")
    direction_group = source_add_parser.add_mutually_exclusive_group(required=True)
    direction_group.add_argument(
        "--noetic", action="store_true", help="Source used — evidence that informed knowledge (source IN)"
    )
    direction_group.add_argument(
        "--praxic", action="store_true", help="Source created — output produced by action (source OUT)"
    )
    source_add_parser.add_argument(
        "--confidence", type=float, default=0.7, help="Confidence in source quality (0.0-1.0, default: 0.7)"
    )
    source_add_parser.add_argument(
        "--visibility",
        choices=["public", "shared", "local"],
        help="Visibility tier (default: shared). public=world-shareable, shared=team-private, local=machine-only. Required for cross-mesh source-map participation.",
    )
    source_add_parser.add_argument("--session-id", help="Session ID (auto-derived from transaction)")
    source_add_parser.add_argument("--project-id", help="Project ID (auto-derived from context)")
    _add_entity_flags(source_add_parser)
    source_add_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    source_add_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    # Source list command
    source_list_parser = subparsers.add_parser(
        "source-list",
        help=(
            "List registered sources for a project. Filter by --type "
            "(document/code/web/api/…) or --direction (noetic/praxic/all). "
            "Useful for finding the source UUID to cite in a new artifact, "
            "or for auditing what external material has informed the "
            "project. Archived sources are hidden by default — pass "
            "--include-archived for forensics."
        ),
    )
    source_list_parser.add_argument("--project-id", help="Project UUID or name (auto-derived from context)")
    source_list_parser.add_argument(
        "--type", dest="source_type", help="Filter by source type (document, code, web, api, etc.)"
    )
    source_list_parser.add_argument(
        "--direction",
        choices=["noetic", "praxic", "all"],
        default="all",
        help="Filter by direction (noetic=evidence IN, praxic=output OUT)",
    )
    source_list_parser.add_argument(
        "--include-archived",
        action="store_true",
        help="Include soft-deleted/archived sources (forensics view; archived rows hidden by default)",
    )
    source_list_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    source_list_parser.add_argument("--verbose", action="store_true", help="Show detailed info")

    # Source get command — download a media-bearing source's retained bytes
    # (consumer half of cross-tenant media transfer, SER ser_a92b3a05).
    source_get_parser = subparsers.add_parser(
        "source-get",
        help=(
            "Download a media-bearing source's retained bytes from cortex to a "
            "local file. Fetches via the source uuid, verifies the SHA-256 "
            "content hash, and writes to --out. Cross-tenant fetch is permitted "
            "when the source's visibility (shared/public) allows the caller; "
            "cortex writes a per-fetch access-log entry. The consumer half of "
            "media-bearing sources — pairs with `source-add --media`."
        ),
    )
    source_get_parser.add_argument("--id", required=True, help="Source UUID to fetch")
    source_get_parser.add_argument("--out", required=True, help="Local path to write the fetched bytes to")
    source_get_parser.add_argument("--cortex-url", help="Cortex URL override (default: credentials.yaml)")
    source_get_parser.add_argument("--api-key", help="Cortex API key override (default: credentials.yaml)")
    source_get_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    source_get_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    # Source map command — cross-mesh discoverability view (goal 74d35435)
    sources_map_parser = subparsers.add_parser(
        "sources-map",
        help=(
            "Show the cross-mesh source map for the current project. Locally "
            "owned sources (from epistemic_sources) plus, with --global, "
            "sources discoverable across other practices via project-scoped "
            "Qdrant collections. The Maven-POM-for-knowledge view: who owns "
            "what canonical reference material across the mesh."
        ),
    )
    sources_map_parser.add_argument("--project-id", help="Project UUID or name (auto-derived from context)")
    sources_map_parser.add_argument(
        "--global",
        dest="include_global",
        action="store_true",
        help="Include sources discoverable in other projects' Qdrant collections (cross-mesh)",
    )
    sources_map_parser.add_argument(
        "--query",
        help="Optional semantic search query for cross-mesh discovery (default: empty → recent sources by upload order)",
    )
    sources_map_parser.add_argument(
        "--type", dest="source_type", help="Filter by source type (document, code, web, api, etc.)"
    )
    sources_map_parser.add_argument(
        "--limit", type=int, default=20, help="Max cross-mesh results to surface (default: 20)"
    )
    sources_map_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    sources_map_parser.add_argument("--verbose", action="store_true", help="Show detailed info")

    # Source archive command (SOURCES_LIFECYCLE_SPEC Phase 1 — soft-delete)
    source_archive_parser = subparsers.add_parser(
        "source-archive",
        help=(
            "Soft-delete a source. Use when the source is no longer valid "
            "(file deleted, URL dead, superseded by newer material). Edges "
            "from citing artifacts are preserved so the audit trail stays "
            "intact — the source just disappears from default listings. "
            "Pass --reason superseded + --target-id <newer-uuid> to chain "
            "forward to the replacement."
        ),
    )
    source_archive_parser.add_argument("--source-id", required=True, help="Source UUID (or unique prefix) to archive")
    source_archive_parser.add_argument(
        "--reason",
        required=True,
        choices=["user_deleted", "file_missing", "url_unreachable", "superseded"],
        help="Why this source is being archived",
    )
    source_archive_parser.add_argument(
        "--target-id", help="Replacement source UUID (REQUIRED when --reason superseded — the chain forward)"
    )
    source_archive_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    source_archive_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    # Source update (the ACT half of the lifecycle — re-fetch + recompute identity)
    source_update_parser = subparsers.add_parser(
        "source-update",
        help=(
            "Re-fetch a source and recompute its content identity "
            "(content_hash / size / mime). The ACT half of the source "
            "lifecycle: run it after sources-check flags a source stale or "
            "broken. Prefers a local canonical_path, else the http(s) "
            "source_url. A failed re-fetch updates nothing — an existing "
            "content_hash is never wiped by an unreachable source."
        ),
    )
    source_update_parser.add_argument("--source-id", required=True, help="Source UUID (or unique prefix) to re-fetch")
    source_update_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    source_update_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    # Source review (source-lifecycle REVIEW half — human/AI verdict on a source)
    source_review_parser = subparsers.add_parser(
        "source-review",
        help=(
            "Record a human/AI review verdict on a source — the REVIEW half of "
            "the source lifecycle (CHECK detects, UPDATE re-fetches, REVIEW judges). "
            "Stamps last_reviewed_at + review_verdict and appends a 'reviewed' event "
            "to the lifecycle audit log. The verdict routes to the next action: "
            "stale→source-update, superseded/irrelevant→source-archive."
        ),
    )
    source_review_parser.add_argument("--source-id", required=True, help="Source UUID (or unique prefix) to review")
    source_review_parser.add_argument(
        "--verdict",
        required=True,
        choices=["valid", "stale", "superseded", "irrelevant"],
        help="valid (keep) | stale (→source-update) | superseded/irrelevant (→source-archive)",
    )
    source_review_parser.add_argument("--note", help="Optional free-text review note")
    source_review_parser.add_argument(
        "--reviewer", help="Who reviewed (ai_id or human name); recorded in the audit event"
    )
    source_review_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    source_review_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    # Enforcement report (artifact-graph enforce telemetry — block/self-resolve rate)
    enforcement_report_parser = subparsers.add_parser(
        "enforcement-report",
        help=(
            "Artifact-graph enforce telemetry: block-rate and self-resolve-rate "
            "from weave_enforce_events. self-resolve-rate is the health metric for "
            "enforce-by-default — high means the gate nudges and the system "
            "recovers on its own; low means it may be over-blocking."
        ),
    )
    enforcement_report_parser.add_argument("--session-id", help="Scope to one session (default: all recorded verdicts)")
    enforcement_report_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Blindspot scan (dry-run unknown-unknown detection — intent-gap signal)
    blindspot_scan_parser = subparsers.add_parser(
        "blindspot-scan",
        help=(
            "Dry-run blindspot detection: predicted unknown-unknowns for a session — "
            "stated goals/tasks with no covering artifact and no acknowledging unknown "
            "(the intent-gap signal). Reports only; wired to nobody yet."
        ),
    )
    blindspot_scan_parser.add_argument("--session-id", help="Session to scan (default: current session)")
    blindspot_scan_parser.add_argument(
        "--include-planned",
        action="store_true",
        help="Include dormant 'planned' goals (backlog view); default scans active goals only",
    )
    blindspot_scan_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Blindspot report (telemetry — surfaced/acknowledged/dismissed/regretted)
    blindspot_report_parser = subparsers.add_parser(
        "blindspot-report",
        help=(
            "Blindspot telemetry: surfaced / acknowledged / dismissed / regretted rates "
            "from blindspot_events. acknowledge-rate = the nudge is useful; regret-rate = "
            "dismissed ones that later became mistakes/dead-ends."
        ),
    )
    blindspot_report_parser.add_argument("--session-id", help="Scope to one session (default: all events)")
    blindspot_report_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Sources sanctify (corpus hygiene — classify dead/duplicate/zombie/valid)
    sources_sanctify_parser = subparsers.add_parser(
        "sources-sanctify",
        help=(
            "Classify the active source corpus and recommend hygiene actions: "
            "dead (canonical_path missing), duplicate (shared content_hash), "
            "zombie (no sourced_from reference), valid. Report-only (deletions "
            "go through review); retire flagged sources via source-archive."
        ),
    )
    sources_sanctify_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Sources reconcile (unified source identity — adopt catalogue uuids)
    sources_reconcile_parser = subparsers.add_parser(
        "sources-reconcile",
        help=(
            "Match local sources against the central catalogue by content "
            "identity and adopt the catalogue uuid. Default adopt is "
            "NON-DESTRUCTIVE: the local row keeps its PK and stores the "
            "catalogue uuid as an alias (the daemon resolves id OR cortex_uuid). "
            "Pass --converge for the destructive one-uuid PK-swap + cascade "
            "(edges, supersession pointers, finding source_refs), then run "
            "`empirica rebuild` to re-point Qdrant. Also lazy-backfills "
            "content_hash/size/canonical_path on file-backed rows predating "
            "migration 050. Dry-run by default; pass --apply to mutate."
        ),
    )
    sources_reconcile_parser.add_argument(
        "--apply", action="store_true", help="Perform the confirmed adopts (default: dry-run report)"
    )
    sources_reconcile_parser.add_argument(
        "--converge",
        action="store_true",
        help=(
            "With --apply: PK-swap local ids to the catalogue uuid (destructive "
            "one-uuid convergence + edge cascade). Default is a non-destructive "
            "alias adopt. Run `empirica rebuild` after --converge to re-point Qdrant."
        ),
    )
    sources_reconcile_parser.add_argument(
        "--push-bodies",
        action="store_true",
        help=(
            "With --apply: also upload the BODY of each small adopted source "
            "(<= EMPIRICA_SMALL_BODY_THRESHOLD, default 1MB) to cortex via "
            "POST /v1/sources/{id}/body, so a remote peer can fetch it — "
            "sync-when-small (P2). Best-effort + idempotent (cortex dedupes on body_hash)."
        ),
    )
    sources_reconcile_parser.add_argument(
        "--project-id", help="Project UUID (auto-derived from active session when omitted)"
    )
    sources_reconcile_parser.add_argument(
        "--cortex-url", help="Cortex base URL (default: credentials.yaml / CORTEX_URL env)"
    )
    sources_reconcile_parser.add_argument(
        "--api-key", help="Cortex API key (default: credentials.yaml / CORTEX_API_KEY env)"
    )
    sources_reconcile_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    sources_reconcile_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    # sources-check — link-rot detection (artifact-hygiene WS1)
    sources_check_parser = subparsers.add_parser(
        "sources-check",
        help=(
            "Probe the http(s) URLs in this project's epistemic sources and "
            "surface link-rot (dead / auth-walled / errored). SURFACE-ONLY — "
            "reports rot, never deletes (retire a dead source via "
            "delete-artifacts or source-archive). Exit 1 if any URL is dead. "
            "The smallest mechanical slice of artifact-hygiene "
            "(docs/architecture/ARTIFACT_HYGIENE.md)."
        ),
    )
    sources_check_parser.add_argument(
        "--project-id", help="Project UUID (auto-derived from active session when omitted)"
    )
    sources_check_parser.add_argument(
        "--timeout", type=float, default=6.0, help="Per-URL probe timeout in seconds (default: 6.0)"
    )
    sources_check_parser.add_argument(
        "--staleness-days",
        type=int,
        default=None,
        dest="staleness_days",
        help=(
            "Only re-probe sources older than N days (fresh ones presumed live); "
            "0 probes everything. Default: the practice's hygiene_policy "
            "source_staleness_days (30)."
        ),
    )
    sources_check_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Graph artifact commands — batch logging and resolution
    log_artifacts_parser = subparsers.add_parser(
        "log-artifacts",
        help=(
            "Log ≥3 connected artifacts in one call instead of N individual "
            "*-log invocations. Accepts a JSON graph (nodes = typed "
            "artifacts, edges = relationships). Use when artifacts have "
            "declared edges between them (sourced_from, evidence_for, "
            "supersedes, etc.) — the batch keeps the graph atomic. For a "
            "single artifact, prefer the per-type *-log command."
        ),
        description="""
Log a connected set of epistemic artifacts in one call.

Accepts JSON on stdin (or from file) with nodes (typed artifacts)
and edges (relationships between them). Creates in dependency order,
resolves local refs to UUIDs, wires edges.

Example:
  echo '{"nodes": [{"ref": "f1", "type": "finding", "data": {"finding": "X", "impact": 0.7}}]}' | empirica log-artifacts -
        """,
    )
    log_artifacts_parser.add_argument(
        "config", nargs="?", default="-", help="JSON file or - for stdin (default: stdin)"
    )
    log_artifacts_parser.add_argument(
        "--schema", action="store_true", help="Print the input JSON schema and exit (use this to learn the shape)"
    )
    log_artifacts_parser.add_argument("--session-id", help="Session UUID (auto-derived)")
    log_artifacts_parser.add_argument("--project-id", help="Project UUID (auto-derived)")
    log_artifacts_parser.add_argument("--output", choices=["human", "json"], default="json", help="Output format")
    log_artifacts_parser.add_argument("--verbose", action="store_true", help="Show detailed output")

    resolve_artifacts_parser = subparsers.add_parser(
        "resolve-artifacts",
        help=(
            "Close multiple open artifacts (unknowns, assumptions, goals) "
            "in one call. Typically used pre-POSTFLIGHT to clean up the "
            "ledger when investigation answered several questions at once. "
            "For a single artifact, prefer the per-type resolve verb "
            "(unknown-resolve, goals-complete)."
        ),
        description="""
Batch resolve multiple open artifacts in one call.

Accepts JSON with resolutions array. Each item specifies type, id, and resolution.

Example:
  echo '{"resolutions": [{"type": "unknown", "id": "abc123", "resolution": "Answered by X"}]}' | empirica resolve-artifacts -
        """,
    )
    resolve_artifacts_parser.add_argument(
        "config", nargs="?", default="-", help="JSON file or - for stdin (default: stdin)"
    )
    resolve_artifacts_parser.add_argument("--schema", action="store_true", help="Print the input JSON schema and exit")
    resolve_artifacts_parser.add_argument("--output", choices=["human", "json"], default="json", help="Output format")
    resolve_artifacts_parser.add_argument("--verbose", action="store_true", help="Show detailed output")

    delete_artifacts_parser = subparsers.add_parser(
        "delete-artifacts",
        help=(
            "Remove stale, duplicate, or test-noise artifacts from the "
            "ledger. Unlike resolve-artifacts (closes WITH a resolution "
            "reason), this hard-deletes from SQLite + Qdrant. The deletion "
            "itself is logged as a decision for audit. Use --dry-run first "
            'to preview. For "still valid but answered", use resolve. For '
            '"never should have been logged", use this.'
        ),
        description="""
Delete stale or non-pertinent artifacts from the epistemic chain.

Accepts JSON with deletions array. Each item specifies type and id.
Deletes from SQLite + Qdrant. Logs deletion as a decision for audit trail.

Supports --dry-run to preview without deleting.

Example:
  echo '{"deletions": [{"type": "finding", "id": "abc123"}], "reason": "Stale test data"}' | empirica delete-artifacts -
        """,
    )
    delete_artifacts_parser.add_argument(
        "config", nargs="?", default="-", help="JSON file or - for stdin (default: stdin)"
    )
    delete_artifacts_parser.add_argument("--schema", action="store_true", help="Print the input JSON schema and exit")
    delete_artifacts_parser.add_argument("--dry-run", action="store_true", help="Preview deletions without executing")
    delete_artifacts_parser.add_argument("--output", choices=["human", "json"], default="json", help="Output format")
    delete_artifacts_parser.add_argument("--verbose", action="store_true", help="Show detailed output")

    # EPP activation telemetry
    # Self-reported: Claude logs when it invoked EPP protocol during a turn.
    # Writes to ~/.empirica/hook_counters{suffix}.json (counter + log).
    # See: docs/superpowers/specs/2026-04-07-epp-strengthening-design.md
    epp_activate_parser = subparsers.add_parser(
        "epp-activate", help="Log EPP (Epistemic Persistence Protocol) activation — self-reported telemetry"
    )
    epp_activate_parser.add_argument(
        "--category",
        required=True,
        choices=["emotional", "rhetorical", "evidential", "logical", "contextual"],
        help="Pushback category classified",
    )
    epp_activate_parser.add_argument(
        "--action",
        required=True,
        choices=["hold", "soften", "update", "reframe"],
        help="Action decided: HOLD / SOFTEN / UPDATE / REFRAME",
    )
    epp_activate_parser.add_argument("--session-id", help="Session ID (auto-derived if omitted)")
    epp_activate_parser.add_argument("--output", choices=["human", "json"], default="json", help="Output format")
    epp_activate_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    # Training data export
    training_export_parser = subparsers.add_parser(
        "training-export", help="Export epistemic transaction data as JSONL for model fine-tuning"
    )
    training_export_parser.add_argument("--output-path", help="Output JSONL file path (default: stdout)")
    training_export_parser.add_argument(
        "--workspace", action="store_true", help="Export from ALL project databases in workspace (not just current)"
    )
    training_export_parser.add_argument("--project-id", help="Filter by project (prefix match)")
    training_export_parser.add_argument("--ai-id", help="Filter by AI ID (e.g., empirica, cortex, autonomy)")
    training_export_parser.add_argument(
        "--min-vectors", type=int, default=3, help="Minimum vector count to include a transaction (default: 3)"
    )
    training_export_parser.add_argument(
        "--no-artifacts", action="store_true", help="Exclude noetic artifacts (findings, unknowns, dead-ends)"
    )
    training_export_parser.add_argument("--no-grounded", action="store_true", help="Exclude grounded calibration data")
    training_export_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    training_export_parser.add_argument("--verbose", action="store_true", help="Show detailed info")

    # NEW: Goal Management Commands (MCP v2 Integration)
    # Aliases: goals-X → goal-X (singular), short aliases (gc, gl, etc.)

    # Goals create command (AI-first with config file support)
    goals_create_parser = subparsers.add_parser(
        "goals-create",
        aliases=["goal-create", "gc"],
        help=(
            "Create a new goal — the unit of tracked work. One per coherent "
            "deliverable: a feature, a fix, a doc sweep. Set --status planned "
            "when scoped-but-not-started (collaborative planning); "
            "in_progress when actively working. For multi-step work, follow "
            "with goals-add-task per distinct unit. AI-first: pass JSON "
            "via stdin/file; legacy: --objective + flags."
        ),
    )

    # AI-FIRST: Positional config file argument (optional, takes precedence)
    goals_create_parser.add_argument("config", nargs="?", help='JSON config file path or "-" for stdin (AI-first mode)')

    # LEGACY: Flag-based arguments (backward compatible)
    goals_create_parser.add_argument("--session-id", help="Session ID (auto-derived from active transaction)")
    goals_create_parser.add_argument(
        "--project-id", help="Target project UUID or name (for cross-project goal creation)"
    )
    goals_create_parser.add_argument("--ai-id", default="empirica_cli", help="AI identifier (legacy)")
    goals_create_parser.add_argument("--objective", help="Goal title — short, actionable (~256 char cap)")
    goals_create_parser.add_argument(
        "--description", help="Optional rich body — context, motivation, success-criteria detail (8000 char cap)"
    )
    goals_create_parser.add_argument(
        "--scope-breadth", type=float, default=0.3, help="Goal breadth (0.0-1.0, how wide the goal spans)"
    )
    goals_create_parser.add_argument(
        "--scope-duration", type=float, default=0.2, help="Goal duration (0.0-1.0, expected lifetime)"
    )
    goals_create_parser.add_argument(
        "--scope-coordination",
        type=float,
        default=0.1,
        help="Goal coordination (0.0-1.0, multi-agent coordination needed)",
    )
    goals_create_parser.add_argument(
        "--success-criteria", help='Success criteria as JSON array (or "-" to read from stdin)'
    )
    goals_create_parser.add_argument(
        "--success-criteria-file", help="Read success criteria from file (avoids shell quoting issues)"
    )
    goals_create_parser.add_argument("--estimated-complexity", type=float, help="Complexity estimate (0.0-1.0)")
    goals_create_parser.add_argument("--constraints", help="Constraints as JSON object")
    goals_create_parser.add_argument("--metadata", help="Metadata as JSON object")
    goals_create_parser.add_argument("--use-beads", action="store_true", help="Create BEADS issue and link to goal")
    goals_create_parser.add_argument(
        "--status",
        choices=["planned", "in_progress", "blocked"],
        default="in_progress",
        help="Initial status: 'planned' (logged, not started), 'in_progress' (active, default), or 'blocked' (waiting on external dependency)",
    )
    goals_create_parser.add_argument("--force", action="store_true", help="Create goal even if similar goal exists")
    goals_create_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    goals_create_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Goals add-task command
    goals_add_task_parser = subparsers.add_parser(
        "goals-add-task",
        aliases=["goal-add-task"],
        help=(
            "Decompose a goal into trackable units. One task per distinct "
            "piece of work you'll execute (read this, edit that, write "
            "these tests). Decompose at PREFLIGHT, not retroactively — "
            "tasks added after the work is done are self-graded checkboxes, "
            "not tracked units. Close each with goals-complete-task + "
            "--evidence as you finish."
        ),
    )
    goals_add_task_parser.add_argument("--goal-id", required=True, help="Goal UUID")
    goals_add_task_parser.add_argument("--description", required=True, help="Task description")
    goals_add_task_parser.add_argument(
        "--importance", choices=["critical", "high", "medium", "low"], default="medium", help="Epistemic importance"
    )
    goals_add_task_parser.add_argument("--dependencies", help="Dependencies as JSON array")
    goals_add_task_parser.add_argument("--estimated-tokens", type=int, help="Estimated token usage")
    goals_add_task_parser.add_argument("--use-beads", action="store_true", help="Create BEADS task and link to goal")
    goals_add_task_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Goals add-dependency command (NEW: Goal-to-goal dependencies)
    goals_add_dep_parser = subparsers.add_parser(
        "goals-add-dependency", help="Add dependency between goals (Goal A depends on Goal B)"
    )
    goals_add_dep_parser.add_argument("--goal-id", required=True, help="Goal that has the dependency")
    goals_add_dep_parser.add_argument("--depends-on", required=True, help="Goal that must complete first")
    goals_add_dep_parser.add_argument(
        "--type",
        choices=["blocks", "informs", "extends"],
        default="blocks",
        help="Dependency type: blocks (must complete first), informs (provides context), extends (builds upon)",
    )
    goals_add_dep_parser.add_argument("--description", help="Description of dependency relationship")
    goals_add_dep_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Goals complete-task command
    goals_complete_task_parser = subparsers.add_parser(
        "goals-complete-task",
        aliases=["goal-complete-task"],
        help=(
            "Close a task with evidence of completion. Always pass "
            "--evidence: commit SHA, test result, file path, link — "
            "whatever proves the work landed. Empty completions inflate "
            "the goal-completion vector without grounding it. Close "
            "as-you-go, not batched at the end."
        ),
    )
    goals_complete_task_parser.add_argument("--task-id", required=True, help="Task UUID (full or unambiguous prefix)")
    goals_complete_task_parser.add_argument("--evidence", help="Completion evidence (commit hash, file path, etc.)")
    goals_complete_task_parser.add_argument(
        "--output", choices=["human", "json"], default="human", help="Output format"
    )

    # Goals progress command
    goals_progress_parser = subparsers.add_parser(
        "goals-progress",
        aliases=["goal-progress"],
        help=(
            "Show task-level progress for a single goal: how many "
            "tasks total, how many completed, with their evidence. "
            "Useful before deciding whether to close the goal "
            "(goals-complete) or whether more tasks are needed."
        ),
    )
    goals_progress_parser.add_argument("--goal-id", required=True, help="Goal UUID")
    goals_progress_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    goals_progress_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Goals get-tasks command
    goals_get_tasks_parser = subparsers.add_parser(
        "goals-get-tasks",
        help=(
            "Dump the full task list for a goal (id, description, "
            "status, evidence, importance). Useful for picking the next "
            "task to work on, or for grepping task ids when "
            "completing several at once."
        ),
    )
    goals_get_tasks_parser.add_argument("--goal-id", required=True, help="Goal UUID")
    goals_get_tasks_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Goals list command
    goals_list_parser = subparsers.add_parser(
        "goals-list",
        aliases=["goal-list", "gl"],
        help=(
            "List goals in the current project. Default: active "
            "(in_progress). Use --status {planned,in_progress,completed,all,drift} "
            'for finer filtering; "drift" surfaces goals where the status '
            "text disagrees with is_completed (data-integrity check). "
            "Scope-* flags filter on goal-shape vectors (breadth, "
            "duration, coordination). For semantic queries, use "
            "goals-search."
        ),
    )
    goals_list_parser.add_argument("--ai-id", help="Filter by AI identifier")
    goals_list_parser.add_argument("--session-id", help="Derive project_id from session (convenience)")
    goals_list_parser.add_argument("--transaction-id", help="Filter by transaction ID (measurement scope)")
    goals_list_parser.add_argument("--project-id", help="Filter by project ID (structural scope)")
    goals_list_parser.add_argument("--scope-breadth-min", type=float, help="Filter by minimum breadth (0.0-1.0)")
    goals_list_parser.add_argument("--scope-breadth-max", type=float, help="Filter by maximum breadth (0.0-1.0)")
    goals_list_parser.add_argument("--scope-duration-min", type=float, help="Filter by minimum duration (0.0-1.0)")
    goals_list_parser.add_argument("--scope-duration-max", type=float, help="Filter by maximum duration (0.0-1.0)")
    goals_list_parser.add_argument(
        "--scope-coordination-min", type=float, help="Filter by minimum coordination (0.0-1.0)"
    )
    goals_list_parser.add_argument(
        "--scope-coordination-max", type=float, help="Filter by maximum coordination (0.0-1.0)"
    )
    goals_list_parser.add_argument(
        "--completed",
        action="store_true",
        help="Show completed goals (default: active). Use --status for finer filtering.",
    )
    goals_list_parser.add_argument(
        "--status",
        choices=["planned", "in_progress", "blocked", "completed", "all", "drift"],
        help='Filter by lifecycle status. Takes precedence over --completed. "drift" surfaces rows where status text disagrees with is_completed (canonical).',
    )
    goals_list_parser.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    goals_list_parser.add_argument(
        "--include-archived",
        action="store_true",
        help="Include archived completed goals (hidden by default; archive via goals-archive)",
    )
    goals_list_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    goals_list_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Goals semantic search command (Qdrant-powered)
    goals_search_parser = subparsers.add_parser(
        "goals-search",
        help=(
            "Semantic search across goals + tasks (Qdrant embeddings). "
            'Finds matches by meaning, not just keyword — "authentication '
            'system" surfaces "user login flow", "JWT validation". Pass '
            "a positional query string. Use to find prior work on a topic "
            "before duplicating effort, or to resurface relevant goals "
            "across sessions. For status-only listing, use goals-list."
        ),
    )
    goals_search_parser.add_argument("query", help='Search query (e.g., "authentication system")')
    goals_search_parser.add_argument("--project-id", help="Project ID (auto-detects if not provided)")
    goals_search_parser.add_argument("--type", choices=["goal", "task"], help="Filter by type (default: both)")
    goals_search_parser.add_argument(
        "--status", choices=["in_progress", "complete", "pending", "completed"], help="Filter by status"
    )
    goals_search_parser.add_argument("--ai-id", help="Filter by AI identifier")
    goals_search_parser.add_argument("--limit", type=int, default=10, help="Maximum results (default: 10)")
    goals_search_parser.add_argument("--sync", action="store_true", help="Sync SQLite goals to Qdrant before searching")
    goals_search_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    goals_search_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # goals-ready command (BEADS integration - Phase 1)
    goals_ready_parser = subparsers.add_parser(
        "goals-ready",
        help=(
            "Find work that's ready to start — open goals/tasks with "
            "their dependencies satisfied AND your current epistemic state "
            "meets the confidence/uncertainty thresholds. Wraps BEADS "
            "priority filtering with empirica's vector gates. Use when "
            'asking "what can I tackle next?" rather than scrolling '
            "goals-list manually."
        ),
    )
    goals_ready_parser.add_argument(
        "--session-id", required=False, help="Session UUID (auto-detects active session if not provided)"
    )
    goals_ready_parser.add_argument(
        "--min-confidence", type=float, default=0.7, help="Minimum confidence threshold (0.0-1.0)"
    )
    goals_ready_parser.add_argument(
        "--max-uncertainty", type=float, default=0.3, help="Maximum uncertainty threshold (0.0-1.0)"
    )
    goals_ready_parser.add_argument("--min-priority", type=int, help="Minimum BEADS priority (1, 2, or 3)")
    goals_ready_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    goals_ready_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Goals-discover command (NEW: Phase 1 - Cross-AI Goal Discovery)
    goals_discover_parser = subparsers.add_parser(
        "goals-discover",
        help=(
            "Surface goals created by OTHER AIs in this project (via git "
            'notes sync). Use for cross-AI coordination — "what is the '
            'cortex AI working on right now?" — before duplicating or '
            "colliding. Filter by --from-ai-id or --session-id. Pair with "
            "goals-resume to pick one up."
        ),
    )
    goals_discover_parser.add_argument("--from-ai-id", help="Filter by AI creator")
    goals_discover_parser.add_argument("--session-id", help="Filter by session")
    goals_discover_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    goals_discover_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Goals-resume command (NEW: Phase 1 - Cross-AI Goal Handoff)
    goals_resume_parser = subparsers.add_parser(
        "goals-resume",
        help=(
            "Take over a goal another AI started. Reassigns the goal's "
            "ai_id to you, imports its tasks + history into your "
            "session's context. Use after goals-discover surfaces work "
            "a peer left mid-flight, or during planned handoff."
        ),
    )
    goals_resume_parser.add_argument("goal_id", help="Goal ID to resume")
    goals_resume_parser.add_argument("--ai-id", default="empirica_cli", help="Your AI identifier")
    goals_resume_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    goals_resume_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Goals-claim command (NEW: Phase 3a - Git Bridge)
    goals_claim_parser = subparsers.add_parser(
        "goals-claim",
        help=(
            "Start working on a goal: create a git branch named after it, "
            "link to the BEADS issue, optionally run PREFLIGHT. Differs "
            "from goals-resume (takeover of a peer's goal) — claim is for "
            "goals already yours that you're committing to start. Skip "
            "branch creation with --no-branch for non-code goals."
        ),
    )
    goals_claim_parser.add_argument("--goal-id", required=True, help="Goal UUID to claim")
    goals_claim_parser.add_argument(
        "--create-branch", action="store_true", default=True, help="Create git branch (default: True)"
    )
    goals_claim_parser.add_argument(
        "--no-branch", dest="create_branch", action="store_false", help="Skip branch creation"
    )
    goals_claim_parser.add_argument("--run-preflight", action="store_true", help="Run PREFLIGHT after claiming")
    goals_claim_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    goals_claim_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Goals-complete command (NEW: Phase 3a - Git Bridge)
    goals_complete_parser = subparsers.add_parser(
        "goals-complete",
        aliases=["goal-complete"],
        help=(
            "Close a goal as done. Pass --reason explaining what shipped "
            "(commit SHAs, what got verified). Optional: --merge-branch + "
            "--delete-branch to wrap the git side, --run-postflight to "
            "auto-close the active transaction. Run BEFORE postflight-submit "
            "so the closure shows up in the transaction's grounded evidence."
        ),
    )
    goals_complete_parser.add_argument("--goal-id", required=True, help="Goal UUID to complete")
    goals_complete_parser.add_argument("--run-postflight", action="store_true", help="Run POSTFLIGHT before completing")
    goals_complete_parser.add_argument("--merge-branch", action="store_true", help="Merge git branch to main")
    goals_complete_parser.add_argument("--delete-branch", action="store_true", help="Delete branch after merge")
    goals_complete_parser.add_argument("--create-handoff", action="store_true", help="Create handoff report")
    goals_complete_parser.add_argument("--reason", default="completed", help="Completion reason (for BEADS)")
    goals_complete_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    goals_complete_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Goals prune — bulk close stale/duplicate/planned goals
    goals_prune_parser = subparsers.add_parser(
        "goals-prune",
        help="Bulk close stale, duplicate, or planned-never-activated goals (dry-run by default)",
        description="""
Bulk cleanup verb for the long tail of stale open goals that accumulate
on long-running projects (the 996→18 noise gap). Three modes, combinable:

  --by-status-planned       Close all planned goals (collaboratively-defined
                            futures the team chose not to pursue)
  --auto-stale [N]          Close in_progress goals with no activity in N days
                            (default 30)
  --duplicates [thresh]     Close goals whose objective text overlaps another's
                            by ≥ thresh (Jaccard token overlap, default 0.7).
                            Keeps the OLDEST; closes the duplicates.

Dry-run is the DEFAULT — pass --apply to actually mutate. Receipt
written to git notes (breadcrumbs ref) for audit trail.
        """,
    )
    goals_prune_parser.add_argument(
        "--test-pollution",
        action="store_true",
        help="Close goals matching test-runner patterns (objective starts with 'Test '/'E2E test', ai_id starts with 'test-')",
    )
    goals_prune_parser.add_argument(
        "--by-status-planned", action="store_true", help="Close all goals with status=planned"
    )
    goals_prune_parser.add_argument(
        "--auto-stale",
        type=int,
        nargs="?",
        const=30,
        metavar="DAYS",
        help="Close in_progress goals older than N days with no activity (default: 30)",
    )
    goals_prune_parser.add_argument(
        "--duplicates",
        type=float,
        nargs="?",
        const=0.7,
        metavar="THRESH",
        help="Close goals whose objective text is ≥ thresh similar to another (default: 0.7)",
    )
    goals_prune_parser.add_argument("--apply", action="store_true", help="Actually mutate (omit for dry-run)")
    goals_prune_parser.add_argument("--project-id", help="Override project_id (auto-resolved if omitted)")
    goals_prune_parser.add_argument("--output", choices=["human", "json"], default="json", help="Output format")
    goals_prune_parser.add_argument("--verbose", action="store_true", help="Show detailed output")

    # Goals mark-stale command (used by pre-compact hooks)
    goals_mark_stale_parser = subparsers.add_parser(
        "goals-mark-stale",
        help=(
            "Flag in_progress goals as stale (typically called by the "
            "pre-compact hook before context loss). Marks them for "
            "re-evaluation on the other side. Not for manual cleanup — "
            "use goals-prune for that. Pair: goals-get-stale to retrieve."
        ),
    )
    goals_mark_stale_parser.add_argument("--session-id", required=True, help="Session UUID")
    goals_mark_stale_parser.add_argument(
        "--reason", default="memory_compact", help="Reason for marking stale (default: memory_compact)"
    )
    goals_mark_stale_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Goals get-stale command (retrieve stale goals needing re-evaluation)
    goals_get_stale_parser = subparsers.add_parser(
        "goals-get-stale",
        help=(
            "List goals marked stale by goals-mark-stale (typically "
            "set by the pre-compact hook). Used after compaction to "
            "decide which goals to refresh (still relevant) vs prune "
            "(superseded by what happened). Pair: goals-refresh / "
            "goals-prune."
        ),
    )
    goals_get_stale_parser.add_argument("--session-id", help="Filter by session ID")
    goals_get_stale_parser.add_argument("--project-id", help="Filter by project ID")
    goals_get_stale_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Goals activate command (transition planned → in_progress, link to transaction)
    goals_activate_parser = subparsers.add_parser(
        "goals-activate",
        aliases=["goal-activate"],
        help=(
            "Flip a planned goal to in_progress and link it to the active "
            "transaction. Use when you're ready to start work on a goal "
            "created earlier as planned (collaborative pre-scoping). Differs "
            "from goals-claim — activate is the same-AI status transition; "
            "claim is the lifecycle hook (branch, BEADS)."
        ),
    )
    goals_activate_parser.add_argument("--goal-id", required=True, help="Goal UUID to activate (prefix match)")
    goals_activate_parser.add_argument("--output", choices=["human", "json"], default="json", help="Output format")

    # Goals reopen command (reverse a completed goal back to in_progress)
    goals_reopen_parser = subparsers.add_parser(
        "goals-reopen",
        aliases=["goal-reopen"],
        help=(
            "Reopen a COMPLETED goal — flip it back to in_progress and re-link "
            "it to the active transaction. The inverse of goals-complete: undo "
            "an accidental or premature completion so it re-enters the active list."
        ),
    )
    goals_reopen_parser.add_argument("--goal-id", required=True, help="Goal UUID to reopen (prefix match)")
    goals_reopen_parser.add_argument("--reason", help="Optional note recorded in the goal's reopen history")
    goals_reopen_parser.add_argument("--output", choices=["human", "json"], default="json", help="Output format")

    # Goals archive command (archive completed goals older than N days — hygiene)
    goals_archive_parser = subparsers.add_parser(
        "goals-archive",
        aliases=["goal-archive"],
        help=(
            "Archive completed goals older than N days so the completed list "
            "doesn't grow unbounded (mirrors source-archive). Archived goals drop "
            "out of goals-list unless --include-archived; goals-reopen un-archives. "
            "Dry-run by default; pass --apply to archive."
        ),
    )
    goals_archive_parser.add_argument(
        "--older-than",
        type=int,
        default=30,
        dest="older_than",
        help="Age threshold in days on completion time (default: 30)",
    )
    goals_archive_parser.add_argument(
        "--goal-id", help="Archive one completed goal by id/prefix (ignores --older-than)"
    )
    goals_archive_parser.add_argument("--apply", action="store_true", help="Actually archive (default: dry-run report)")
    goals_archive_parser.add_argument("--output", choices=["human", "json"], default="json", help="Output format")

    # Goals refresh command (mark stale goal as in_progress after regaining context)
    goals_refresh_parser = subparsers.add_parser(
        "goals-refresh",
        help=(
            "Move a stale goal back to in_progress after you've regained "
            "context (typically post-compact). Use after goals-get-stale "
            "surfaces the goal and you've confirmed it's still relevant. "
            "For irrelevant stale goals, prefer goals-complete (with reason) "
            "or goals-prune."
        ),
    )
    goals_refresh_parser.add_argument("--goal-id", required=True, help="Goal UUID to refresh")
    goals_refresh_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Identity commands (NEW: Phase 2 - Cryptographic Trust / EEP-1)
    identity_create_parser = subparsers.add_parser(
        "identity-create", help="Create new AI identity with Ed25519 keypair"
    )
    identity_create_parser.add_argument("--ai-id", required=True, help="AI identifier")
    identity_create_parser.add_argument("--overwrite", action="store_true", help="Overwrite existing identity")
    identity_create_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    identity_create_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    identity_list_parser = subparsers.add_parser("identity-list", help="List all AI identities")
    identity_list_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    identity_export_parser = subparsers.add_parser("identity-export", help="Export public key for sharing")
    identity_export_parser.add_argument("--ai-id", required=True, help="AI identifier")
    identity_export_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    identity_verify_parser = subparsers.add_parser("identity-verify", help="Verify signed session")
    identity_verify_parser.add_argument("session_id", help="Session ID to verify")
    identity_verify_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    identity_verify_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Sessions resume command
    sessions_resume_parser = subparsers.add_parser(
        "sessions-resume", aliases=["session-resume", "sr"], help="Resume previous sessions"
    )
    sessions_resume_parser.add_argument("--ai-id", help="Filter by AI ID")
    sessions_resume_parser.add_argument("--count", type=int, default=1, help="Number of sessions to retrieve")
    sessions_resume_parser.add_argument(
        "--detail-level", choices=["summary", "detailed", "full"], default="summary", help="Detail level"
    )
    sessions_resume_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    sessions_resume_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Session create command (AI-first with config file support)
    session_create_parser = subparsers.add_parser(
        "session-create", aliases=["sc"], help="Create new session (AI-first: use config file, Legacy: use flags)"
    )

    # AI-FIRST: Positional config file argument
    session_create_parser.add_argument(
        "config", nargs="?", help='JSON config file path or "-" for stdin (AI-first mode)'
    )

    # LEGACY: Flag-based arguments (backward compatible)
    session_create_parser.add_argument("--ai-id", help="AI agent identifier (legacy)")
    session_create_parser.add_argument("--user-id", help="User identifier (legacy)")
    session_create_parser.add_argument(
        "--project-id", help="Project UUID to link session to (optional, auto-detected from git remote if omitted)"
    )
    session_create_parser.add_argument(
        "--subject", help="Subject/workstream identifier (auto-detected from directory if omitted)"
    )
    session_create_parser.add_argument("--parent-session-id", help="Parent session UUID for sub-agent lineage tracking")
    session_create_parser.add_argument(
        "--output", choices=["human", "json"], default="json", help="Output format (default: json for AI)"
    )
    session_create_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # Auto-init: Initialize .empirica/ if not present (issue #25)
    session_create_parser.add_argument(
        "--auto-init",
        action="store_true",
        help="Auto-initialize .empirica/ if not present in git repo (prevents orphaned sessions)",
    )

    # ===== SYNC COMMANDS =====
    # Git notes synchronization for multi-device/multi-AI coordination

    # sync config command
    sync_config_parser = subparsers.add_parser(
        "sync-config", help="Configure sync settings (remote, visibility, provider)"
    )
    sync_config_parser.add_argument(
        "key", nargs="?", help="Config key to get/set (enabled, remote, visibility, provider)"
    )
    sync_config_parser.add_argument("value", nargs="?", help="Value to set")
    sync_config_parser.add_argument("--output", choices=["human", "json"], default="json", help="Output format")
    sync_config_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # sync push command
    sync_push_parser = subparsers.add_parser("sync-push", help="Push all epistemic notes to remote")
    sync_push_parser.add_argument("--remote", help="Git remote name (uses config default if not specified)")
    sync_push_parser.add_argument("--dry-run", action="store_true", help="Show what would be pushed without pushing")
    sync_push_parser.add_argument("--force", action="store_true", help="Push even if sync is disabled in config")
    sync_push_parser.add_argument("--output", choices=["human", "json"], default="json", help="Output format")
    sync_push_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # sync pull command
    sync_pull_parser = subparsers.add_parser("sync-pull", help="Pull all epistemic notes from remote")
    sync_pull_parser.add_argument("--remote", help="Git remote name (uses config default if not specified)")
    sync_pull_parser.add_argument("--rebuild", action="store_true", help="Also rebuild SQLite from notes after pull")
    sync_pull_parser.add_argument("--force", action="store_true", help="Pull even if sync is disabled in config")
    sync_pull_parser.add_argument("--output", choices=["human", "json"], default="json", help="Output format")
    sync_pull_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # sync status command
    sync_status_parser = subparsers.add_parser(
        "sync-status", help="Show sync status (local note counts, remote availability)"
    )
    sync_status_parser.add_argument("--remote", help="Git remote name (uses config default if not specified)")
    sync_status_parser.add_argument("--output", choices=["human", "json"], default="json", help="Output format")
    sync_status_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # rebuild command
    rebuild_parser = subparsers.add_parser("rebuild", help="Reconstruct SQLite from git notes")
    rebuild_parser.add_argument(
        "--from-notes", action="store_true", default=True, help="Rebuild from git notes (default)"
    )
    rebuild_parser.add_argument("--qdrant", action="store_true", help="Also rebuild Qdrant embeddings")
    rebuild_parser.add_argument("--output", choices=["human", "json"], default="json", help="Output format")
    rebuild_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")

    # artifacts-generate command
    artifacts_parser = subparsers.add_parser(
        "artifacts-generate", help="Generate browsable .empirica/ markdown files from git notes"
    )
    artifacts_parser.add_argument("--output-dir", help="Output directory (default: .empirica/)")
    artifacts_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    artifacts_parser.add_argument("--verbose", action="store_true", help="Show detailed operation info")
