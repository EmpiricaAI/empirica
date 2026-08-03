"""Parsers for `empirica mailbox` subcommand group.

NEW group dedicated to Cortex AI-mesh interaction. Distinct from:
  - `empirica message-*` (git-notes local agent messaging)
  - `empirica notify *` (multi-backend event dispatch)

Implements prop_rau4ymp62fhenavyolejadahtq.
"""

from __future__ import annotations

# The proposal statuses cortex actually stores. ONE definition, read by both the
# --status help text and the validator in mailbox_commands — they used to be two
# things, with only the help text knowing the truth and nothing enforcing it.
VALID_POLL_STATUSES = (
    "eco_review",
    "accepted",
    # A publish proposal parked awaiting dispatch. MISSING from the first
    # version of this tuple, which I built by hand from the old help text —
    # so `--status accepted_pending_dispatch` was rejected as unknown. A
    # hand-maintained allowlist over ANOTHER system's vocabulary diverges the
    # moment they add a value; this one diverged before it was ever committed.
    # Kept as an explicit list anyway (rejecting a typo is worth more than
    # accepting every string), but the divergence risk is real and the honest
    # fix would be cortex publishing its status vocabulary as data.
    "accepted_pending_dispatch",
    "changed",
    "declined",
    "completed",
    "expired",
)

# `all` is not a stored status; it means "no filter". Accepted because it is the
# obvious word to type — I typed it — and because passing it through as a
# literal matched nothing and returned an empty mailbox indistinguishable from
# a genuinely empty one.
POLL_STATUS_ALL = "all"


def add_mailbox_parsers(subparsers):
    """Register `empirica mailbox <action>` subcommand group."""
    mailbox_root = subparsers.add_parser(
        "mailbox",
        help="Cortex AI mesh interaction — atomic reply with auto-close "
        "(distinct from message-* git-notes local messaging)",
    )
    mailbox_subs = mailbox_root.add_subparsers(
        dest="mailbox_action",
        metavar="action",
    )

    reply = mailbox_subs.add_parser(
        "reply",
        help="Atomic propose + complete in one call — fixes the AI "
        "ack-discipline gap (skip the second cortex_complete_proposal step)",
    )
    reply.add_argument(
        "--parent-id",
        required=True,
        help="Parent proposal id being replied to (the inbox row)",
    )
    reply.add_argument(
        "--summary",
        required=True,
        help="Reply body (the actual message)",
    )
    reply.add_argument(
        "--title",
        help='Reply title (default: "Re: <parent.title>", truncated to 200)',
    )
    reply.add_argument(
        "--type",
        default="collab_brief",
        choices=[
            "architecture_decision",
            "collab_brief",
            "code_change_request",
            "investigation_request",
            "spec_updated",
            "publish",
            "trust_escalation_request",
        ],
        help="Reply proposal type (default: collab_brief)",
    )
    reply.add_argument(
        "--target-claudes",
        help="Comma-separated target ai_ids (default: auto-derive from parent.source_claude)",
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
        "--result",
        default="shipped",
        choices=["shipped", "failed", "wont_fix"],
        help="Completion result applied to parent (default: shipped)",
    )
    reply.add_argument(
        "--commit-sha",
        help="Optional commit_sha attached to parent completion",
    )
    reply.add_argument(
        "--no-close",
        action="store_true",
        help="Send reply WITHOUT closing parent (follow-up question case)",
    )
    reply.add_argument(
        "--no-archive",
        action="store_true",
        help=(
            "Close the parent but do NOT archive it. Default behaviour archives "
            "the parent after close to keep your inbox view focused on "
            "un-actioned work. Use --no-archive when you want the parent to "
            "stay visible in audit / status=accepted polls."
        ),
    )
    reply.add_argument(
        "--output",
        choices=["human", "json"],
        default="json",
        help="Output format (default: json)",
    )

    # ── poll: the receive side, symmetric with reply ──
    poll = mailbox_subs.add_parser(
        "poll",
        help="Poll your cortex mesh inbox (or --outbox) — a CLI receive path "
        "so tool-aggregating harnesses skip the MCP namespace call",
    )
    poll.add_argument(
        "--ai-id",
        dest="ai_id",
        help="Your ai_id (canonical 3-form or basename; default: from .empirica/project.yaml)",
    )
    poll.add_argument(
        "--outbox",
        action="store_true",
        help="Poll your OUTBOX (status changes on proposals YOU sent) instead of the inbox",
    )
    poll.add_argument(
        "--status",
        help=(
            "Comma-separated status filter (default: 'accepted,changed' for "
            "inbox, 'completed,changed,declined' for outbox). Choices: "
            + ", ".join(VALID_POLL_STATUSES)
            + f", or '{POLL_STATUS_ALL}' for every status. An unrecognised value is "
            "an error, not an empty result."
        ),
    )
    poll.add_argument(
        "--since",
        help="ISO-8601 timestamp — only proposals created_at >= since (incremental polling)",
    )
    poll.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max proposals (default: 20, cortex caps at 200)",
    )
    poll.add_argument(
        "--related",
        action="store_true",
        help="Include per-proposal related_goals[] semantic hints (default off — faster polls)",
    )
    poll.add_argument(
        "--output",
        choices=["human", "json"],
        default="json",
        help="Output format (default: json)",
    )

    # ── show: full body of one proposal ──
    show = mailbox_subs.add_parser(
        "show",
        help="Show one proposal's full body — GET /v1/orchestration/<id>",
    )
    show.add_argument("proposal_id", help="Proposal id (prop_…) to fetch")
    show.add_argument(
        "--output",
        choices=["human", "json"],
        default="json",
        help="Output format (default: json)",
    )

    # ── archive: soft-delete from inbox view ──
    archive = mailbox_subs.add_parser(
        "archive",
        help="Archive a proposal (soft-delete from inbox view) — POST /v1/orchestration/<id>/archive",
    )
    archive.add_argument("proposal_id", help="Proposal id (prop_…) to archive")
    archive.add_argument(
        "--reason",
        help="Optional archive reason (audit trail)",
    )
    archive.add_argument(
        "--output",
        choices=["human", "json"],
        default="json",
        help="Output format (default: json)",
    )

    # ── sers: read-only SER participation ──
    #
    # One subcommand, not two. Bare lists; with an id, shows that record. A separate
    # `ser show` would have been a second entry on a surface the standing guidance
    # wants smaller.
    sers = mailbox_subs.add_parser(
        "sers",
        help=(
            "List the SERs this practice participates in — GET /v1/sers. Pass a ser_id "
            "to show one, including participant rows so required-tier holders are named. "
            "Read-only: transitions and acks go through cortex_propose payload actions."
        ),
    )
    sers.add_argument(
        "ser_id",
        nargs="?",
        help="Optional SER id (ser_…). Omit to list this practice's participation.",
    )
    sers.add_argument(
        "--ai-id",
        help="Canonical 3-form to query for (default: this project's ai_id)",
    )
    sers.add_argument(
        "--output",
        choices=["human", "json"],
        default="json",
        help="Output format (default: json)",
    )

    return mailbox_root
