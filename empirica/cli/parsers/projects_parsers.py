"""CLI parsers for the bulk-project verbs (v0.5).

projects-discover  — walk the filesystem looking for .empirica/ directories
projects-list      — list discovered projects (cached or fresh-scanned)
projects-bulk-register — register all discovered projects on Cortex (T2)

See docs/specs/PROPOSAL_BULK_PROJECT_LINK.md (mirrored from
empirica-extension/docs/v0.5-BULK-PROJECT-LINK.md) for the design rationale.
"""

from __future__ import annotations


def add_projects_parsers(subparsers) -> None:
    """Register the bulk-project verbs on the top-level subparsers."""

    # ── projects-discover ──────────────────────────────────────────────
    discover = subparsers.add_parser(
        "projects-discover",
        help="Walk filesystem for .empirica/ directories and emit a manifest.",
        description=(
            "Find all local Empirica projects by walking from one or more roots. "
            "Outputs a manifest (yaml/json) describing each discovered project's "
            "path, name, and git remote URL. Used by projects-bulk-register to "
            "import many projects to Cortex in one shot."
        ),
    )
    discover.add_argument(
        "--root",
        action="append",
        dest="roots",
        help="Root directory to walk (default: $HOME). Repeatable.",
    )
    discover.add_argument(
        "--max-depth",
        type=int,
        default=5,
        help="Maximum walk depth from each root (default: 5).",
    )
    discover.add_argument(
        "--include-hidden",
        action="store_true",
        help="Walk hidden directories (default: skip).",
    )
    discover.add_argument(
        "--output",
        choices=["yaml", "json"],
        default="yaml",
        help="Output format (default: yaml).",
    )
    discover.add_argument(
        "--manifest",
        default=None,
        help=(
            "Write manifest to this path (default: ~/.empirica/discovered_projects.yaml). "
            "Use '-' to write to stdout only."
        ),
    )
    discover.add_argument(
        "--register",
        nargs="?",
        const="*",
        default=None,
        metavar="NAME",
        help=(
            "After scanning, upsert each discovered project into "
            "~/.empirica/registry.yaml (the daemon's served set). Idempotent — "
            "matches on project_id. Pass NAME to register a single project "
            "by directory basename or project.yaml name (e.g. `--register "
            "empirica-mesh-support`); pass no value to register all "
            "discovered projects. (v1.9.6+)"
        ),
    )
    discover.add_argument(
        "--prune",
        action="store_true",
        help=(
            "Only with --register (no NAME): also remove registry entries "
            "whose path no longer exists or no longer contains .empirica/."
        ),
    )

    # ── daemon-list ────────────────────────────────────────────────────
    daemon_list = subparsers.add_parser(
        "daemon-list",
        help="List projects registered with the local daemon (~/.empirica/registry.yaml).",
        description=(
            "Print the contents of ~/.empirica/registry.yaml — the set of "
            "projects the local `empirica serve` daemon is willing to route "
            "?project_id= requests to. Populate via `empirica projects-discover "
            "--register` or hand-edit the YAML. (v1.9.6+)"
        ),
    )
    daemon_list.add_argument(
        "--output",
        choices=["yaml", "json", "table"],
        default="table",
        help="Output format (default: table).",
    )

    # ── projects-list ──────────────────────────────────────────────────
    listing = subparsers.add_parser(
        "projects-list",
        help="List discovered local Empirica projects.",
        description=(
            "Read the cached discovery manifest and print it. Falls back to a "
            "fresh discover scan if no cache exists. Same shape as projects-discover "
            "for parity with extension consumers."
        ),
    )
    listing.add_argument(
        "--output",
        choices=["yaml", "json", "table"],
        default="table",
        help="Output format (default: table).",
    )
    listing.add_argument(
        "--manifest",
        default=None,
        help="Read manifest from this path (default: ~/.empirica/discovered_projects.yaml).",
    )
    listing.add_argument(
        "--refresh",
        action="store_true",
        help="Force a fresh discover scan even if cache exists.",
    )

    # ── projects-bulk-register ─────────────────────────────────────────
    register = subparsers.add_parser(
        "projects-bulk-register",
        help="[CORTEX] Register all discovered projects on the Cortex backend.",
        description=(
            "Register every discovered Empirica project on the Cortex backend "
            "in one shot.\n\n"
            "⚠ This command is Cortex-dependent. It POSTs to Cortex's "
            "/v1/projects/register endpoint, so it requires:\n"
            "  • CORTEX_REMOTE_URL env var (or --cortex-url) pointing at a "
            "reachable Cortex instance\n"
            "  • CORTEX_API_KEY env var (or --api-key) for authentication\n\n"
            "Idempotent — projects already on Cortex (matched by name) are "
            "skipped. Failures on individual projects are logged and the loop "
            "continues to the rest. No partial-rollback. Use --dry-run to "
            "preview without making any HTTP calls."
        ),
    )
    register.add_argument(
        "--from",
        dest="manifest_path",
        default=None,
        help=(
            "Manifest YAML to read (default: ~/.empirica/discovered_projects.yaml). "
            "Falls back to running projects-discover live if absent."
        ),
    )
    register.add_argument(
        "--include",
        action="append",
        dest="includes",
        default=None,
        help=(
            "Regex matched against project name OR path. Repeatable — multi "
            "--include is OR (project kept if ANY pattern matches). If no "
            "--include is given, all projects pass the include stage."
        ),
    )
    register.add_argument(
        "--exclude",
        action="append",
        dest="excludes",
        default=None,
        help=(
            "Regex matched against project name OR path. Repeatable — multi "
            "--exclude is OR (project dropped if ANY pattern matches). Applied "
            "after --include."
        ),
    )
    register.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be registered without making HTTP calls.",
    )
    register.add_argument(
        "--force-metadata-update",
        action="store_true",
        help=(
            "Set `force_metadata_update: true` in each request body. "
            "Cortex's safe-update logic then backfills UUID-shaped "
            "placeholder names + empty repo_urls on already-existing rows. "
            "Useful when Cortex has stale metadata that should be refreshed "
            "from the local registry. (v1.9.6+)"
        ),
    )
    register.add_argument(
        "--from-discovered",
        action="store_true",
        help=(
            "Source projects from the raw scanner output "
            "(~/.empirica/discovered_projects.yaml) instead of the curated "
            "daemon registry (~/.empirica/registry.yaml, the default). Use "
            "when you want to register EVERY project you have on disk, "
            "not just the curated set the daemon serves. (v1.9.6+)"
        ),
    )
    register.add_argument(
        "--cortex-url",
        default=None,
        help="Override Cortex base URL (default: $CORTEX_REMOTE_URL).",
    )
    register.add_argument(
        "--api-key",
        default=None,
        help="Override Cortex API key (default: $CORTEX_API_KEY).",
    )
    register.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds (default: 10).",
    )
    register.add_argument(
        "--output",
        choices=["human", "json"],
        default="human",
        help="Output format for the summary (default: human).",
    )

    # ── projects-sync ─────────────────────────────────────────────────
    # Master command collapsing projects-discover → registry.yaml upsert →
    # Cortex POST into one verb. Mirrors the `listener on/off` and
    # `mailbox reply` AI-ergonomic pattern: AI-as-primary-CLI-user, single
    # verb hides the multi-step protocol. The individual verbs stay as
    # power-user surface for fine control.
    sync = subparsers.add_parser(
        "projects-sync",
        help=(
            "One-shot: walk filesystem → upsert ~/.empirica/registry.yaml → "
            "register on Cortex. Idempotent. Use --no-cortex for offline, "
            "--no-write for pure preview, --dry-run for full preview."
        ),
        description=(
            "Master command for end-to-end project sync. Equivalent to:\n"
            "  empirica projects-discover --register [--prune]\n"
            "  empirica projects-bulk-register [filters...]\n"
            "But in one verb with one set of flags. Default: full pipeline "
            "(walk filesystem, write manifest cache, upsert registry.yaml, "
            "POST each to Cortex). Phase-skip flags peel off as needed. "
            "Closes prop_ncitlxqewrabzheagvdkra5ahi from the extension AI — "
            "AI-as-primary-CLI-user, single verb hides multi-step protocol. "
            "The individual `projects-discover` / `projects-bulk-register` "
            "verbs remain as the power-user surface for fine-grained control."
        ),
    )
    sync.add_argument(
        "--root",
        action="append",
        dest="roots",
        help="Root directory to walk (default: $HOME). Repeatable.",
    )
    sync.add_argument(
        "--max-depth",
        type=int,
        default=5,
        help="Maximum walk depth from each root (default: 5).",
    )
    sync.add_argument(
        "--include-hidden",
        action="store_true",
        help="Walk hidden directories during discovery (default: skip).",
    )
    sync.add_argument(
        "--include",
        action="append",
        dest="includes",
        default=None,
        help=(
            "Regex matched against project name OR path during Cortex POST. "
            "Repeatable — multi --include is OR. Doesn't affect discovery "
            "or registry.yaml — only filters what gets registered on Cortex."
        ),
    )
    sync.add_argument(
        "--exclude",
        action="append",
        dest="excludes",
        default=None,
        help=(
            "Regex matched against project name OR path during Cortex POST. "
            "Repeatable — multi --exclude is OR (project dropped if ANY "
            "pattern matches). Applied after --include."
        ),
    )
    sync.add_argument(
        "--no-cortex",
        action="store_true",
        help=(
            "Stop after registry.yaml write. Use when Cortex is down, "
            "offline-first setup, or when you only need the daemon's served "
            "set populated."
        ),
    )
    sync.add_argument(
        "--no-write",
        action="store_true",
        help=(
            "Pure discover-only preview. Don't write the manifest cache, "
            "don't upsert registry.yaml, don't POST to Cortex. Equivalent "
            "to `--dry-run` for the discover phase only."
        ),
    )
    sync.add_argument(
        "--prune",
        action="store_true",
        help=(
            "Remove stale entries from registry.yaml (projects no longer "
            "present on disk). Off by default — keeps the registry "
            "additive-only unless explicitly asked."
        ),
    )
    sync.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Full pipeline preview: walk, show what would be written/"
            "registered, but make no changes (no manifest write, no "
            "registry upsert, no Cortex POST). Strongest no-op flag."
        ),
    )
    sync.add_argument(
        "--cortex-url",
        default=None,
        help="Override Cortex base URL (default: $CORTEX_REMOTE_URL).",
    )
    sync.add_argument(
        "--api-key",
        default=None,
        help="Override Cortex API key (default: $CORTEX_API_KEY).",
    )
    sync.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout for Cortex POSTs in seconds (default: 10).",
    )
    sync.add_argument(
        "--force-metadata-update",
        action="store_true",
        help=(
            "Set `force_metadata_update: true` in each Cortex request body, "
            "asking Cortex to backfill UUID-shaped placeholder names + empty "
            "repo_urls on already-existing rows. Useful when Cortex has "
            "stale metadata that should be refreshed from local."
        ),
    )
    sync.add_argument(
        "--output",
        choices=["human", "json"],
        default="human",
        help="Output format for the summary (default: human).",
    )

    # ── projects-unregister ────────────────────────────────────────────
    unreg = subparsers.add_parser(
        "projects-unregister",
        help="Unregister a project from Cortex (soft archive by default; --purge to hard-delete).",
        description=(
            "Soft archive (default): sets is_archived=true + archived_at "
            "on the cortex project row, removes the project_id from the "
            "caller's user.project_ids so it stops surfacing in roster / "
            "threads / sers projections. Proposals + SERs + artifacts stay "
            "readable for audit. Reversible by re-running `projects-bulk-register` "
            "on the same project.\n\n"
            "Hard purge (--purge --confirm): deletes the project row and "
            "cascade-deletes proposals + SERs + artifacts owned by it. "
            "Irreversible. Requires --confirm to actually execute."
        ),
    )
    unreg.add_argument(
        "--project-id",
        default=None,
        help="Cortex project UUID. Mutually exclusive with --slug; one of them or .empirica/project.yaml required.",
    )
    unreg.add_argument(
        "--slug",
        default=None,
        help="Project slug (resolves on the cortex side against caller's projects).",
    )
    unreg.add_argument(
        "--purge",
        action="store_true",
        help="Hard-delete instead of soft-archive. Cascade-deletes proposals + SERs + artifacts. Requires --confirm.",
    )
    unreg.add_argument(
        "--confirm",
        action="store_true",
        help="Required with --purge — acknowledge the destructive operation.",
    )
    unreg.add_argument(
        "--cortex-url",
        default=None,
        help="Override Cortex base URL.",
    )
    unreg.add_argument(
        "--api-key",
        default=None,
        help="Override Cortex API key.",
    )
    unreg.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP timeout in seconds (default: 10).",
    )
    unreg.add_argument(
        "--output",
        choices=["human", "json"],
        default="human",
        help="Output format (default: human).",
    )
