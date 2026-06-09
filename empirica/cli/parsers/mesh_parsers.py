"""Parsers for `empirica mesh ...` — unified mesh diagnostic + control surface."""

from __future__ import annotations


def add_mesh_parsers(subparsers) -> None:
    mesh_root = subparsers.add_parser(
        "mesh",
        help="Unified mesh diagnostic + control surface across listener "
             "instances and (optional) cortex bridge",
        description=(
            "Consolidates per-instance listener service management, "
            "health diagnostics, and zombie detection. Reports two layers: "
            "LOCAL (systemd/launchd service + loop_fires.log + local loops) "
            "and CORTEX BRIDGE (ntfy curl subscription + inbox poll), "
            "surfacing the cortex layer only when configured."
        ),
    )
    mesh_subs = mesh_root.add_subparsers(dest="mesh_action", metavar="action")

    # status
    status = mesh_subs.add_parser(
        "status",
        help="Show health table across mesh instances (green/yellow/red + reason)",
    )
    status.add_argument(
        "instance", nargs="?",
        help="ai_id (default: enumerate all installed listener services)",
    )
    status.add_argument(
        "--output", choices=["human", "json"], default="human",
    )

    # diagnose
    diag = mesh_subs.add_parser(
        "diagnose",
        help="Deep per-instance diagnostic + suggest exact fix command",
    )
    diag.add_argument("instance", help="ai_id to diagnose")
    diag.add_argument(
        "--cortex", action="store_true",
        help="Also run cortex-side participation checks (identity + channels "
             "endpoint + listener subscription URL + ntfy ACL probe + mesh "
             "agreements). Cross-correlates the local view with cortex's view "
             "of this practitioner so silent-failure classes (label mismatch, "
             "topic drift, ACL 403, silent strand) surface at one verb.",
    )
    diag.add_argument(
        "--peer", metavar="CANONICAL",
        help="With --cortex, also probe mesh_sharing_agreement with this peer "
             "(canonical 3-form like 'empirica.philipp.empirica-autonomy'). "
             "Fails if the agreement row is missing.",
    )
    diag.add_argument(
        "--output", choices=["human", "json"], default="human",
    )

    # restart
    restart = mesh_subs.add_parser(
        "restart",
        help="Restart the listener service for an instance (clears curl zombies)",
    )
    restart.add_argument("instance", help="ai_id to restart")

    # on
    on = mesh_subs.add_parser(
        "on",
        help="Install + start + enable the listener service for an instance",
    )
    on.add_argument("instance", help="ai_id to bring online")

    # off
    off = mesh_subs.add_parser(
        "off",
        help="Stop the listener service for an instance",
    )
    off.add_argument("instance", help="ai_id to bring offline")
    off.add_argument(
        "--uninstall", action="store_true",
        help="Also remove the systemd/launchd unit (default: stop only)",
    )

    # tail
    tail = mesh_subs.add_parser(
        "tail",
        help="Live tail loop_fires.log filtered by instance(s)",
    )
    tail.add_argument(
        "instance", nargs="?",
        help="ai_id (default: tail all installed instances)",
    )

    # migrate-topics
    migrate = mesh_subs.add_parser(
        "migrate-topics",
        help="Migrate legacy per-practice + retired bare ntfy topics to "
             "the per-tenant canonical (closes SER canonical-channel model)",
        description=(
            "Inspects ~/.empirica/credentials.yaml `ntfy.topic` and every "
            "`~/.empirica/listener_active_*.json` topic, detects retired "
            "forms (bare `orchestration-events`, pre-tenant per-org form, "
            "or any per-practice topic without `-orchestration-events-` "
            "structure), queries cortex's notification-channels endpoint "
            "for the canonical per-tenant topic, and rewrites the "
            "credentials block + listener_active markers in place. "
            "Dry-run by default; pass --apply to actually write."
        ),
    )
    migrate.add_argument(
        "--apply", action="store_true",
        help="Actually rewrite credentials.yaml + listener_active markers "
             "(default: dry-run reports what would change)",
    )
    migrate.add_argument(
        "--output", choices=["human", "json"], default="human",
    )


__all__ = ["add_mesh_parsers"]
