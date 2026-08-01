"""
Message Commands - CLI handlers for inter-agent messaging via git notes.

Commands:
  message-send      Send a message to another agent
  message-inbox     Check inbox for messages
  message-read      Mark a message as read
  message-reply     Reply to a message
  message-thread    View conversation thread
  message-channels  List channels with counts
  message-cleanup   Remove expired messages
"""

import json
import sys
from pathlib import Path

from empirica.core.canonical.empirica_git.message_store import GitMessageStore


def _get_store() -> GitMessageStore:
    """Get message store instance."""
    return GitMessageStore()


def _output(data: dict, args) -> None:
    """Output data in requested format."""
    fmt = getattr(args, "output", "json")
    if fmt == "json":
        print(json.dumps(data, indent=2))
    else:
        _output_human(data)


def _output_human(data: dict) -> None:
    """Human-readable output."""
    if data.get("ok"):
        if "message_id" in data:
            print(f"Message sent: {data['message_id'][:8]}...")
            if data.get("channel"):
                print(f"  Channel: #{data['channel']}")
            if data.get("to"):
                print(f"  To: {data['to']}")
        elif "messages" in data:
            msgs = data["messages"]
            print(f"Inbox: {len(msgs)} message(s)")
            for msg in msgs:
                status = "READ" if msg.get("status") == "read" else "NEW"
                priority = msg.get("priority", "normal")
                prio_marker = "!" if priority == "high" else ""
                from_info = msg.get("from", {})
                print(f"  [{status}]{prio_marker} {msg.get('subject', '(no subject)')}")
                print(f"    From: {from_info.get('ai_id', '?')}@{from_info.get('machine', '?')}")
                print(f"    Channel: #{msg.get('channel', '?')} | {msg.get('timestamp', '?')[:19]}")
                print(f"    ID: {msg.get('message_id', '?')[:8]}...")
                print()
        elif "channels" in data:
            channels = data["channels"]
            print(f"Channels: {len(channels)}")
            for ch in channels:
                name = ch.get("channel", "?")
                unread = ch.get("unread", 0)
                marker = f" ({unread} unread)" if unread else ""
                print(f"  #{name}{marker}")
        elif "thread" in data:
            msgs = data["thread"]
            print(f"Thread: {len(msgs)} message(s)")
            for msg in msgs:
                from_info = msg.get("from", {})
                print(
                    f"  [{msg.get('timestamp', '?')[:19]}] {from_info.get('ai_id', '?')}@{from_info.get('machine', '?')}:"
                )
                print(f"    {msg.get('body', '')[:200]}")
                print()
        elif "removed" in data:
            count = data.get("removed_count", 0)
            dry = " (dry run)" if data.get("dry_run") else ""
            print(f"Cleanup: {count} expired message(s) removed{dry}")
        elif "marked_read" in data:
            print(f"Marked as read: {data.get('message_id', '?')[:8]}...")
        else:
            print(json.dumps(data, indent=2))
    else:
        print(f"Error: {data.get('message', 'Unknown error')}")


def _load_config(args) -> dict:
    """Load config from file/stdin or CLI args."""
    config_file = getattr(args, "config", None)
    if config_file:
        if config_file == "-":
            return json.load(sys.stdin)
        else:
            with open(config_file) as f:
                return json.load(f)
    return {}


def _load_message_body(args, config: dict) -> str | None:
    """Resolve a message body without sending arbitrary prose through shell expansion.

    Config JSON retains its existing precedence over CLI arguments. ``--body -``
    reads raw text from stdin, while ``--body-file`` reads a UTF-8 file.
    """
    config_body = config.get("body")
    if config_body:
        return config_body

    body = getattr(args, "body", None)
    if body == "-":
        return sys.stdin.read()

    body_file = getattr(args, "body_file", None)
    if body_file:
        with open(body_file, encoding="utf-8") as f:
            return f.read()

    return body


def _default_sender() -> str:
    """Who this practice is, for message-send / message-reply.

    Reported by FrancisFerrero (#389): the sender defaulted to the literal
    "claude-code" and no `EMPIRICA_MESH_*` variable was consulted anywhere in the
    package — so every practice's messages were attributed to the same name, and the
    documented override did nothing.

    Resolution order, most explicit first:
      1. ``EMPIRICA_MESH_AI_ID`` — the documented override
      2. ``.empirica/project.yaml`` ``ai_id`` — this practice's own identity
      3. ``"claude-code"`` — the historical value, kept as a last resort so a
         project-less invocation still sends rather than failing

    Note this is the practice-local id, not the canonical 3-form. Mesh addressing
    over cortex uses `<org>.<tenant>.<project>`; this is the local messaging store,
    which keys on the short id.
    """
    import os

    env = os.environ.get("EMPIRICA_MESH_AI_ID")
    if env and env.strip():
        return env.strip()

    try:
        import yaml

        cwd = Path.cwd()
        for parent in [cwd, *cwd.parents]:
            proj = parent / ".empirica" / "project.yaml"
            if proj.exists():
                cfg = yaml.safe_load(proj.read_text(encoding="utf-8")) or {}
                ai_id = cfg.get("ai_id")
                if isinstance(ai_id, str) and ai_id.strip():
                    return ai_id.strip()
                break
    except Exception:
        pass

    return "claude-code"


def _reject_ambiguous_stdin(args) -> str | None:
    """Reject attempts to consume stdin as both config JSON and raw body text."""
    if getattr(args, "config", None) == "-" and getattr(args, "body", None) == "-":
        return "Cannot use config stdin ('message-send -' or 'message-reply -') together with --body -"
    return None


def handle_message_send_command(args):
    """Handle message-send command."""
    store = _get_store()
    stdin_error = _reject_ambiguous_stdin(args)
    if stdin_error:
        _output({"ok": False, "message": stdin_error}, args)
        return
    config = _load_config(args)

    from_ai_id = config.get("from_ai_id") or getattr(args, "from_ai_id", None) or _default_sender()
    to_ai_id = config.get("to_ai_id") or getattr(args, "to_ai_id", None)
    channel = config.get("channel") or getattr(args, "channel", "direct")
    subject = config.get("subject") or getattr(args, "subject", None)
    try:
        body = _load_message_body(args, config)
    except OSError as exc:
        _output({"ok": False, "message": f"Cannot read --body-file: {exc}"}, args)
        return

    if not to_ai_id or not subject or not body:
        _output({"ok": False, "message": "Required: --to-ai-id, --subject, and --body or --body-file"}, args)
        return

    message_id = store.send_message(
        from_ai_id=from_ai_id,
        to_ai_id=to_ai_id,
        channel=channel,
        subject=subject,
        body=body,
        message_type=config.get("type") or getattr(args, "type", "request"),
        to_machine=config.get("to_machine") or getattr(args, "to_machine", None),
        from_session_id=config.get("session_id") or getattr(args, "session_id", None),
        reply_to=config.get("reply_to") or getattr(args, "reply_to", None),
        thread_id=config.get("thread_id") or getattr(args, "thread_id", None),
        ttl=config.get("ttl", getattr(args, "ttl", 86400)),
        priority=config.get("priority") or getattr(args, "priority", "normal"),
        metadata={
            "goal_id": config.get("goal_id") or getattr(args, "goal_id", None),
            "project_id": config.get("project_id") or getattr(args, "project_id", None),
        },
    )

    if message_id:
        _output(
            {
                "ok": True,
                "message_id": message_id,
                "channel": channel,
                "to": to_ai_id,
                "subject": subject,
                "message": f"Message sent to {to_ai_id} on #{channel}",
            },
            args,
        )
    else:
        _output({"ok": False, "message": "Failed to send message (git not available?)"}, args)


def handle_message_inbox_command(args):
    """Handle message-inbox command."""
    store = _get_store()

    ai_id = args.ai_id
    machine = getattr(args, "machine", None)
    channel = getattr(args, "channel", None)
    status = getattr(args, "status", "unread")
    limit = getattr(args, "limit", 50)
    include_expired = getattr(args, "include_expired", False)

    messages = store.get_inbox(
        ai_id=ai_id,
        machine=machine,
        channel=channel,
        status=status,
        include_expired=include_expired,
        limit=limit,
    )

    _output(
        {
            "ok": True,
            "ai_id": ai_id,
            "status_filter": status,
            "channel_filter": channel,
            "count": len(messages),
            "messages": messages,
        },
        args,
    )


def handle_message_read_command(args):
    """Handle message-read — return the message content AND mark it read.

    Reported by FrancisFerrero (#391) as "returns empty from/subject/body". The
    diagnosis is sharper than that: those fields were never in the projection at all.
    This verb only ever called ``mark_read`` and returned the receipt — it performed
    the write half of "read" and none of the read half, while the content sat
    perfectly intact in the git note the whole time.

    That is why the workaround was `git notes show`: the data was never missing, only
    unreachable through the verb named after fetching it. A consumer acting on the
    envelope alone gets nothing to act on — and they report a logged peer mistake of
    acknowledging messages on envelope data, which is exactly the failure this shape
    invites.

    Content is loaded BEFORE marking read, so a load failure does not silently consume
    the unread flag.
    """
    store = _get_store()

    message = store.load_message(channel=args.channel, message_id=args.message_id)
    if message is None:
        _output(
            {
                "ok": False,
                "message_id": args.message_id,
                "error": f"No message {args.message_id!r} on channel {args.channel!r}",
            },
            args,
        )
        return

    success = store.mark_read(
        channel=args.channel,
        message_id=args.message_id,
        ai_id=args.ai_id,
        machine=getattr(args, "machine", None),
    )

    if success:
        _output(
            {
                "ok": True,
                "marked_read": True,
                "message_id": args.message_id,
                "ai_id": args.ai_id,
                # The point of the verb. Key names are taken from what send_message
                # actually writes — `from`, `to`, `timestamp` — not from what they
                # sound like they should be.
                #
                # My first draft read `from_ai_id`/`to_ai_id`/`sent_at` and would have
                # returned empty fields, reproducing this exact bug report while
                # claiming to fix it. Checked the writer before trusting the names.
                "from": message.get("from"),
                "to": message.get("to"),
                "subject": message.get("subject"),
                "body": message.get("body"),
                "channel": args.channel,
                "timestamp": message.get("timestamp"),
                "thread_id": message.get("thread_id"),
                "priority": message.get("priority"),
            },
            args,
        )
    else:
        _output({"ok": False, "message": "Failed to mark message as read"}, args)


def handle_message_reply_command(args):
    """Handle message-reply command."""
    store = _get_store()
    stdin_error = _reject_ambiguous_stdin(args)
    if stdin_error:
        _output({"ok": False, "message": stdin_error}, args)
        return
    config = _load_config(args)

    message_id = config.get("message_id") or getattr(args, "message_id", None)
    channel = config.get("channel") or getattr(args, "channel", None)
    from_ai_id = config.get("from_ai_id") or getattr(args, "from_ai_id", None) or _default_sender()
    try:
        body = _load_message_body(args, config)
    except OSError as exc:
        _output({"ok": False, "message": f"Cannot read --body-file: {exc}"}, args)
        return

    if not message_id or not channel or not body:
        _output({"ok": False, "message": "Required: --message-id, --channel, and --body or --body-file"}, args)
        return

    reply_id = store.reply(
        original_message_id=message_id,
        original_channel=channel,
        from_ai_id=from_ai_id,
        body=body,
        message_type=config.get("type") or getattr(args, "type", "response"),
        from_session_id=config.get("session_id") or getattr(args, "session_id", None),
    )

    if reply_id:
        _output(
            {
                "ok": True,
                "message_id": reply_id,
                "reply_to": message_id,
                "channel": channel,
                "message": f"Reply sent on #{channel}",
            },
            args,
        )
    else:
        _output({"ok": False, "message": "Failed to send reply"}, args)


def handle_message_thread_command(args):
    """Handle message-thread command."""
    store = _get_store()

    thread_id = args.thread_id
    channel = getattr(args, "channel", None)

    messages = store.get_thread(thread_id=thread_id, channel=channel)

    _output(
        {
            "ok": True,
            "thread_id": thread_id,
            "count": len(messages),
            "thread": messages,
        },
        args,
    )


def handle_message_channels_command(args):
    """Handle message-channels command."""
    store = _get_store()

    channels = store.discover_channels()
    ai_id = getattr(args, "ai_id", None)

    channel_info = []
    for ch in channels:
        info = {"channel": ch}
        if ai_id:
            unread = store.get_inbox(ai_id, channel=ch, status="unread")
            info["unread"] = len(unread)
        channel_info.append(info)

    _output(
        {
            "ok": True,
            "count": len(channels),
            "channels": channel_info,
        },
        args,
    )


def handle_message_cleanup_command(args):
    """Handle message-cleanup command."""
    store = _get_store()

    dry_run = getattr(args, "dry_run", False)
    removed = store.cleanup_expired(dry_run=dry_run)

    _output(
        {
            "ok": True,
            "dry_run": dry_run,
            "removed_count": len(removed),
            "removed": [
                {"message_id": m.get("message_id"), "channel": m.get("channel"), "subject": m.get("subject")}
                for m in removed
            ],
        },
        args,
    )
