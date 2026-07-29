"""
Message Parsers - CLI argument parsers for inter-agent messaging commands.
"""

from empirica.cli.command_handlers.message_commands import (
    handle_message_channels_command,
    handle_message_cleanup_command,
    handle_message_inbox_command,
    handle_message_read_command,
    handle_message_reply_command,
    handle_message_send_command,
    handle_message_thread_command,
)


def add_message_parsers(subparsers):
    """Register message command parsers."""

    # message-send
    send_parser = subparsers.add_parser(
        "message-send", aliases=["msg-send", "ms"], help="Send message to another agent via git notes"
    )
    send_parser.add_argument("config", nargs="?", help="JSON config file or - for stdin (AI-first mode)")
    send_parser.add_argument("--from-ai-id", help="Sender AI ID (optional, default: claude-code)")
    send_parser.add_argument("--to-ai-id", help="Recipient AI ID or * for broadcast (required)")
    send_parser.add_argument("--to-machine", help="Recipient machine hostname (optional)")
    send_parser.add_argument(
        "--channel",
        default="direct",
        help="Channel: crosscheck, direct, broadcast, or custom (optional, default: direct)",
    )
    send_parser.add_argument("--subject", help="Message subject (required)")
    send_body_group = send_parser.add_mutually_exclusive_group()
    send_body_group.add_argument(
        "--body",
        help="Message body, or - to read the raw body from stdin (required unless --body-file is used)",
    )
    send_body_group.add_argument(
        "--body-file",
        metavar="PATH",
        help="Read the message body from a UTF-8 file (mutually exclusive with --body)",
    )
    send_parser.add_argument(
        "--type",
        choices=["request", "response", "notification", "ack"],
        default="request",
        help="Message type (optional, default: request)",
    )
    send_parser.add_argument("--reply-to", help="Message ID this replies to (optional)")
    send_parser.add_argument("--thread-id", help="Thread ID to join (optional)")
    send_parser.add_argument(
        "--ttl", type=int, default=86400, help="Time-to-live in seconds (optional, default: 86400 = 24h, 0 = never)"
    )
    send_parser.add_argument(
        "--priority",
        choices=["low", "normal", "high"],
        default="normal",
        help="Message priority (optional, default: normal)",
    )
    send_parser.add_argument("--session-id", help="Sender session ID (optional)")
    send_parser.add_argument("--goal-id", help="Related goal ID (optional)")
    send_parser.add_argument("--project-id", help="Related project ID (optional)")
    send_parser.add_argument("--output", choices=["human", "json"], default="json")
    send_parser.add_argument("--verbose", action="store_true")
    send_parser.set_defaults(func=handle_message_send_command)

    # message-inbox
    inbox_parser = subparsers.add_parser(
        "message-inbox", aliases=["msg-inbox", "mi"], help="Check inbox for messages addressed to this agent"
    )
    inbox_parser.add_argument("--ai-id", required=True, help="Your AI ID (required)")
    inbox_parser.add_argument("--machine", help="Your machine hostname (optional, auto-detected)")
    inbox_parser.add_argument("--channel", help="Filter by channel (optional)")
    inbox_parser.add_argument(
        "--status",
        choices=["unread", "read", "all"],
        default="unread",
        help="Filter by status (optional, default: unread)",
    )
    inbox_parser.add_argument("--limit", type=int, default=50, help="Max messages to return (optional, default: 50)")
    inbox_parser.add_argument("--include-expired", action="store_true", help="Include expired messages (optional)")
    inbox_parser.add_argument("--output", choices=["human", "json"], default="json")
    inbox_parser.add_argument("--verbose", action="store_true")
    inbox_parser.set_defaults(func=handle_message_inbox_command)

    # message-read
    read_parser = subparsers.add_parser("message-read", aliases=["msg-read", "mr"], help="Mark a message as read")
    read_parser.add_argument("--message-id", required=True, help="Message UUID (required)")
    read_parser.add_argument("--channel", required=True, help="Channel name (required)")
    read_parser.add_argument("--ai-id", required=True, help="Your AI ID (required)")
    read_parser.add_argument("--machine", help="Your machine hostname (optional)")
    read_parser.add_argument("--output", choices=["human", "json"], default="json")
    read_parser.set_defaults(func=handle_message_read_command)

    # message-reply
    reply_parser = subparsers.add_parser("message-reply", aliases=["msg-reply"], help="Reply to a message")
    reply_parser.add_argument("config", nargs="?", help="JSON config file or - for stdin")
    reply_parser.add_argument("--message-id", help="Original message ID (required)")
    reply_parser.add_argument("--channel", help="Channel of original message (required)")
    reply_parser.add_argument("--from-ai-id", help="Your AI ID (optional, default: claude-code)")
    reply_body_group = reply_parser.add_mutually_exclusive_group()
    reply_body_group.add_argument(
        "--body",
        help="Reply body, or - to read the raw body from stdin (required unless --body-file is used)",
    )
    reply_body_group.add_argument(
        "--body-file",
        metavar="PATH",
        help="Read the reply body from a UTF-8 file (mutually exclusive with --body)",
    )
    reply_parser.add_argument(
        "--type", choices=["response", "ack"], default="response", help="Reply type (optional, default: response)"
    )
    reply_parser.add_argument("--session-id", help="Your session ID (optional)")
    reply_parser.add_argument("--output", choices=["human", "json"], default="json")
    reply_parser.set_defaults(func=handle_message_reply_command)

    # message-thread
    thread_parser = subparsers.add_parser("message-thread", help="View conversation thread")
    thread_parser.add_argument("--thread-id", required=True, help="Thread ID (required)")
    thread_parser.add_argument("--channel", help="Filter by channel (optional)")
    thread_parser.add_argument("--output", choices=["human", "json"], default="json")
    thread_parser.set_defaults(func=handle_message_thread_command)

    # message-channels
    channels_parser = subparsers.add_parser("message-channels", help="List channels with message counts")
    channels_parser.add_argument("--ai-id", help="Count unread for this AI ID (optional)")
    channels_parser.add_argument("--output", choices=["human", "json"], default="json")
    channels_parser.set_defaults(func=handle_message_channels_command)

    # message-cleanup
    cleanup_parser = subparsers.add_parser("message-cleanup", help="Remove expired messages")
    cleanup_parser.add_argument("--dry-run", action="store_true", help="Show what would be removed without removing")
    cleanup_parser.add_argument("--output", choices=["human", "json"], default="json")
    cleanup_parser.set_defaults(func=handle_message_cleanup_command)
