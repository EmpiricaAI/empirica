"""Argparse parsers for `empirica visibility` subcommand group (Phase 0).

Per docs/architecture/PROPOSAL_VISIBILITY_TIERS.md (not in this repo).
"""

from __future__ import annotations


def add_visibility_parsers(subparsers):
    """Register the `visibility` subcommand group with list/show."""
    visibility_root = subparsers.add_parser(
        "visibility",
        help="Visibility tiers (public/shared/local) — list and inspect artifact classification",
        description="""
Visibility primitive (Phase 0 — metadata only):

  - public  : world-shareable (generic technical content, public-RFC citations)
  - shared  : team-private, co-versioned (default — safest invariant)
  - local   : machine-only, never shared (raw secrets, session state)

Phase 0 stores the tier in a dedicated column on each artifact table.
Phase 1 will introduce git-crypt for the 'shared' tier.
        """,
    )
    vis_subs = visibility_root.add_subparsers(dest="visibility_action", metavar="action")

    # ─── list ─────────────────────────────────────────────────────────────
    vlist = vis_subs.add_parser(
        "list",
        help="Show artifact counts by visibility tier",
    )
    vlist.add_argument("--project-id", help="Project UUID (default: active project)")
    vlist.add_argument("--tier", choices=["public", "shared", "local"], help="Filter to a single tier")
    vlist.add_argument(
        "--type",
        choices=["finding", "unknown", "dead_end", "mistake", "assumption", "decision", "goal"],
        dest="artifact_type",
        help="Filter to a single artifact type",
    )
    vlist.add_argument("--limit", type=int, default=10, help="Recent items to show per tier (default: 10)")
    vlist.add_argument("--output", choices=["json", "human"], default="human", help="Output format (default: human)")

    # ─── show ─────────────────────────────────────────────────────────────
    vshow = vis_subs.add_parser(
        "show",
        help="Show visibility tier for one artifact (by UUID prefix)",
    )
    vshow.add_argument("artifact_id", help="Artifact UUID or prefix (≥8 chars)")
    vshow.add_argument("--output", choices=["json", "human"], default="human", help="Output format (default: human)")
