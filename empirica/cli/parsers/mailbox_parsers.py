"""Parsers for `empirica mailbox` subcommand group.

NEW group dedicated to Cortex AI-mesh interaction. Distinct from:
  - `empirica message-*` (git-notes local agent messaging)
  - `empirica notify *` (multi-backend event dispatch)

Implements prop_rau4ymp62fhenavyolejadahtq.
"""

from __future__ import annotations


def add_mailbox_parsers(subparsers):
    """Register `empirica mailbox <action>` subcommand group."""
    mailbox_root = subparsers.add_parser(
        "mailbox",
        help="Cortex AI mesh interaction — atomic reply with auto-close "
             "(distinct from message-* git-notes local messaging)",
    )
    mailbox_subs = mailbox_root.add_subparsers(
        dest="mailbox_action", metavar="action",
    )

    reply = mailbox_subs.add_parser(
        "reply",
        help="Atomic propose + complete in one call — fixes the AI "
             "ack-discipline gap (skip the second cortex_complete_proposal step)",
    )
    reply.add_argument(
        "--parent-id", required=True,
        help="Parent proposal id being replied to (the inbox row)",
    )
    reply.add_argument(
        "--summary", required=True,
        help="Reply body (the actual message)",
    )
    reply.add_argument(
        "--title",
        help='Reply title (default: "Re: <parent.title>", truncated to 200)',
    )
    reply.add_argument(
        "--type", default="collab_brief",
        choices=[
            "architecture_decision", "collab_brief", "code_change_request",
            "investigation_request", "spec_updated", "publish",
            "trust_escalation_request",
        ],
        help="Reply proposal type (default: collab_brief)",
    )
    reply.add_argument(
        "--target-claudes",
        help="Comma-separated target ai_ids "
             "(default: auto-derive from parent.source_claude)",
    )
    reply.add_argument(
        "--source-claude",
        help="Your ai_id (default: from .empirica/project.yaml)",
    )
    reply.add_argument(
        "--payload",
        help="Optional type-specific payload as JSON string (default: {})",
    )
    reply.add_argument(
        "--result", default="shipped",
        choices=["shipped", "failed", "wont_fix"],
        help="Completion result applied to parent (default: shipped)",
    )
    reply.add_argument(
        "--commit-sha",
        help="Optional commit_sha attached to parent completion",
    )
    reply.add_argument(
        "--no-close", action="store_true",
        help="Send reply WITHOUT closing parent (follow-up question case)",
    )
    reply.add_argument(
        "--no-archive", action="store_true",
        help=(
            "Close the parent but do NOT archive it. Default behaviour archives "
            "the parent after close to keep your inbox view focused on "
            "un-actioned work. Use --no-archive when you want the parent to "
            "stay visible in audit / status=accepted polls."
        ),
    )
    reply.add_argument(
        "--output", choices=["human", "json"], default="json",
        help="Output format (default: json)",
    )

    return mailbox_root
