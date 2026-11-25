"""
Profile Parsers - Epistemic profile management commands

Commands:
- profile-sync: Sync epistemic profile (fetch notes, import to SQLite, rebuild Qdrant)
- profile-prune: Prune low-value artifacts with transparent audit receipts
- profile-status: Show profile state (artifact counts, sync status, calibration)
- profile-import: Import epistemic artifacts from AI conversation transcripts
"""


def add_profile_parsers(subparsers):
    """Add profile management command parsers."""

    # profile-sync
    sync_parser = subparsers.add_parser(
        "profile-sync",
        help="Sync epistemic profile: fetch notes → import to SQLite → rebuild Qdrant",
        description="Full profile sync pipeline. Fetches git notes from remote, "
        "imports artifacts idempotently into SQLite (preserving original UUIDs), "
        "and optionally rebuilds Qdrant semantic index.",
    )
    sync_parser.add_argument(
        "--remote", default=None, help='Git remote to sync with (default: from sync config, typically "forgejo")'
    )
    sync_parser.add_argument(
        "--push", action="store_true", help="Push local notes to remote after import (bidirectional sync)"
    )
    sync_parser.add_argument("--qdrant", action="store_true", help="Rebuild Qdrant semantic index after import")
    sync_parser.add_argument(
        "--import-only", action="store_true", help="Skip fetch, only import existing local git notes into SQLite"
    )
    sync_parser.add_argument(
        "--force", action="store_true", help="Sync even if disabled in config, and push to a public host"
    )
    sync_parser.add_argument("--output", choices=["json", "text"], default="json", help="Output format (default: json)")

    # profile-prune
    prune_parser = subparsers.add_parser(
        "profile-prune",
        help="Prune low-value artifacts with transparent audit receipts in git notes",
        description="Remove artifacts from SQLite/Qdrant that match pruning rules. "
        "Every prune is recorded as an immutable receipt in git notes for auditability.",
    )
    prune_parser.add_argument(
        "--rule",
        choices=[
            "stale-resolved-unknowns",
            "test-transactions",
            "low-impact-findings",
            "falsified-assumptions",
            "old-dead-ends",
            "low-confidence-imports",
        ],
        help="Apply a specific mechanical pruning rule",
    )
    prune_parser.add_argument("--artifact-id", help="Prune a specific artifact by UUID")
    prune_parser.add_argument(
        "--artifact-type",
        choices=["finding", "unknown", "dead_end", "mistake", "goal"],
        help="Type of artifact to prune (required with --artifact-id)",
    )
    prune_parser.add_argument("--reason", help="Reason for pruning (recorded in prune receipt)")
    prune_parser.add_argument("--older-than", type=int, metavar="DAYS", help="Only prune artifacts older than N days")
    prune_parser.add_argument(
        "--scope", choices=["memory"], help='Prune scope: "memory" archives stale CC memory files (promoted_*.md)'
    )
    prune_parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be pruned without actually removing anything"
    )
    prune_parser.add_argument(
        "--output", choices=["json", "text"], default="json", help="Output format (default: json)"
    )

    # profile-status
    status_parser = subparsers.add_parser(
        "profile-status",
        help="Show epistemic profile status: artifact counts, sync state, calibration",
        description="Unified view of the epistemic profile. Shows artifact counts by type, "
        "sync state (local vs remote), last sync time, drift between notes and SQLite, "
        "and calibration summary.",
    )
    status_parser.add_argument(
        "--remote", default=None, help="Git remote to check sync state against (default: from sync config)"
    )
    status_parser.add_argument(
        "--output", choices=["json", "text"], default="json", help="Output format (default: json)"
    )

    # profile-import
    import_parser = subparsers.add_parser(
        "profile-import",
        help="Import epistemic artifacts from AI conversation transcripts",
        description="Mine AI conversation transcripts for epistemic artifacts "
        "(findings, decisions, dead-ends, mistakes, unknowns). "
        "Supports Claude Code local transcripts and Claude.ai exports.",
    )
    import_parser.add_argument(
        "--source", required=True, choices=["claude-code", "claude-ai"], help="Source platform to import from"
    )
    import_parser.add_argument(
        "--project",
        help="Claude Code project directory name to import from (default: auto-discover from .claude/projects/)",
    )
    import_parser.add_argument("--file", help="Path to Claude.ai export JSON file (required for --source claude-ai)")
    import_parser.add_argument("--session", help="Import a specific session by ID (Claude Code only)")
    import_parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.5,
        help="Minimum extraction confidence to include (0.0-1.0, default: 0.5)",
    )
    import_parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be imported without storing anything"
    )
    import_parser.add_argument(
        "--include-sidechains", action="store_true", help="Include subagent/sidechain conversations (Claude Code only)"
    )
    import_parser.add_argument(
        "--output", choices=["json", "text"], default="text", help="Output format (default: text)"
    )
