# Messaging System API Reference

**Version:** 1.11.0
**Purpose:** Asynchronous communication between AI instances over git-notes refs

---

## Overview

The messaging system enables **asynchronous AI-to-AI communication** over local git-notes refs (refs/notes/empirica/messages/). This is empirica's **local** messaging primitive — works offline, no external services. It backs use cases like:

- Cross-check requests between agents
- Handoff coordination
- Status updates and notifications
- Multi-machine coordination via git push/fetch

> **For cross-AI mesh coordination over an external serving layer** (cross-tenant proposals with ECO trust gating, push-wake on inbox events, peer-AI discovery across projects), see the optional [Mesh Setup guide](../../human/end-users/MESH_SETUP.md). The git-notes messaging documented below is the local/offline path; the mesh layer is the cloud-mediated path. They coexist.

All CLI verbs below were verified present in 1.11.0; daily housekeeping is handled by the `message-cleanup` canonical loop.

---

## Channels

| Channel | Purpose | Scope |
|---------|---------|-------|
| `direct` | Point-to-point messaging | AI → AI |
| `broadcast` | All AIs on a machine | Machine-wide |
| `crosscheck` | Verification requests | AI → AI |
| Custom | User-defined channels | Configurable |

---

## Commands

### `message-send`

Send a message to another AI.

```bash
# Direct message
empirica message-send \
  --to-ai-id philipp-code \
  --subject "Auth findings" \
  --body "JWT uses RS256 signing, tokens expire in 1 hour"

# Broadcast to all AIs
empirica message-send \
  --to-ai-id "*" \
  --channel broadcast \
  --subject "System notice" \
  --body "Database migration starting"

# Reply to a message
empirica message-send \
  --to-ai-id philipp-code \
  --subject "Re: Auth findings" \
  --body "Acknowledged, proceeding with implementation" \
  --reply-to abc123-msg-id \
  --type response

# AI-first mode (JSON stdin)
empirica message-send - << 'EOF'
{
  "to_ai_id": "philipp-code",
  "subject": "Investigation complete",
  "body": "Found 5 security issues",
  "priority": "high",
  "goal_id": "goal-123"
}
EOF
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--to-ai-id` | Yes | - | Recipient AI ID or `*` for broadcast |
| `--subject` | Yes | - | Message subject |
| `--body` | Yes | - | Message body |
| `--from-ai-id` | No | `claude-code` | Sender AI ID |
| `--to-machine` | No | - | Recipient machine hostname |
| `--channel` | No | `direct` | Channel: `direct`, `broadcast`, `crosscheck` |
| `--type` | No | `request` | `request`, `response`, `notification`, `ack` |
| `--reply-to` | No | - | Message ID this replies to |
| `--thread-id` | No | - | Thread ID to join |
| `--ttl` | No | `86400` | Time-to-live in seconds (0 = never) |
| `--priority` | No | `normal` | `low`, `normal`, `high` |
| `--session-id` | No | - | Sender session ID |
| `--goal-id` | No | - | Related goal ID |
| `--project-id` | No | - | Related project ID |
| `--output` | No | `human` | Output format |

**Output (JSON):**
```json
{
  "ok": true,
  "message_id": "abc123-msg-id",
  "thread_id": "thread-456",
  "channel": "direct",
  "to_ai_id": "philipp-code",
  "expires_at": 1707405000.0
}
```

---

### `message-inbox`

Check inbox for messages.

```bash
# Check unread messages
empirica message-inbox --ai-id claude-code

# Filter by channel
empirica message-inbox --ai-id claude-code --channel crosscheck

# Include read messages
empirica message-inbox --ai-id claude-code --status all --limit 20

# JSON output
empirica message-inbox --ai-id claude-code --output json
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--ai-id` | Yes | - | Your AI ID |
| `--machine` | No | auto | Your machine hostname |
| `--channel` | No | all | Filter by channel |
| `--status` | No | `unread` | `unread`, `read`, `all` |
| `--limit` | No | `50` | Max messages to return |
| `--include-expired` | No | `false` | Include expired messages |
| `--output` | No | `human` | Output format |

**Output (human):**
```
📬 INBOX: claude-code (3 unread)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📩 [HIGH] From: philipp-code | Channel: direct
   Subject: Need review on auth implementation
   Received: 2 hours ago
   Message ID: abc123...

📩 From: security-agent | Channel: crosscheck
   Subject: Security findings ready
   Received: 30 min ago
   Message ID: def456...

📩 From: * (broadcast) | Channel: broadcast
   Subject: Database migration complete
   Received: 15 min ago
   Message ID: ghi789...
```

**Output (JSON):**
```json
{
  "ok": true,
  "ai_id": "claude-code",
  "messages": [
    {
      "message_id": "abc123...",
      "from_ai_id": "philipp-code",
      "to_ai_id": "claude-code",
      "channel": "direct",
      "subject": "Need review on auth implementation",
      "body": "Please review the JWT implementation...",
      "type": "request",
      "priority": "high",
      "status": "unread",
      "created_at": 1707318600.0,
      "expires_at": 1707405000.0,
      "thread_id": "thread-123"
    }
  ],
  "count": 3,
  "unread_count": 3
}
```

---

### `message-read`

Read a specific message (marks as read).

```bash
empirica message-read \
  --message-id abc123-msg-id \
  --channel direct \
  --ai-id claude-code
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--message-id` | Yes | - | Message UUID |
| `--channel` | Yes | - | Channel name |
| `--ai-id` | Yes | - | Your AI ID |
| `--machine` | No | auto | Your machine hostname |
| `--output` | No | `human` | Output format |

**Output (JSON):**
```json
{
  "ok": true,
  "message": {
    "message_id": "abc123...",
    "from_ai_id": "philipp-code",
    "subject": "Need review on auth implementation",
    "body": "Please review the JWT implementation in src/auth/...",
    "type": "request",
    "priority": "high",
    "thread_id": "thread-123",
    "goal_id": "goal-456",
    "session_id": "session-789"
  },
  "marked_read": true
}
```

---

### `message-reply`

Reply to a message.

```bash
empirica message-reply \
  --message-id abc123-msg-id \
  --body "Review complete. Found 2 issues: ..."
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--message-id` | Yes | - | Message to reply to |
| `--body` | Yes | - | Reply body |
| `--output` | No | `human` | Output format |

---

### `message-thread`

View a message thread.

```bash
empirica message-thread --thread-id thread-123
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--thread-id` | Yes | - | Thread ID |
| `--output` | No | `human` | Output format |

**Output (human):**
```
📧 THREAD: thread-123
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[1] philipp-code → claude-code (2h ago)
    Subject: Need review on auth implementation
    Please review the JWT implementation...

[2] claude-code → philipp-code (1h ago)
    Subject: Re: Need review on auth implementation
    Review complete. Found 2 issues: ...

[3] philipp-code → claude-code (30m ago)
    Subject: Re: Need review on auth implementation
    Thanks! Fixing now.
```

---

### `message-channels`

List available channels.

```bash
empirica message-channels --ai-id claude-code
```

---

### `message-cleanup`

Clean up expired messages.

```bash
# Clean up expired messages
empirica message-cleanup

# Dry run (show what would be deleted)
empirica message-cleanup --dry-run
```

---

## Use Cases

### Cross-AI Handoff

```bash
# AI 1: Create handoff message
empirica message-send \
  --to-ai-id philipp-code \
  --channel direct \
  --subject "Handoff: Auth implementation" \
  --body "I've completed investigation. Findings attached. Please continue with implementation." \
  --goal-id goal-123 \
  --priority high

# AI 2: Check inbox
empirica message-inbox --ai-id philipp-code

# AI 2: Read and acknowledge
empirica message-read --message-id <id> --channel direct --ai-id philipp-code
empirica message-reply --message-id <id> --body "Acknowledged, starting implementation"
```

### Crosscheck Request

```bash
# Request crosscheck
empirica message-send \
  --to-ai-id security-agent \
  --channel crosscheck \
  --subject "Crosscheck: Auth implementation" \
  --body "Please verify security of JWT implementation in src/auth/" \
  --type request

# Respond to crosscheck
empirica message-send \
  --to-ai-id claude-code \
  --channel crosscheck \
  --reply-to <original-id> \
  --subject "Re: Crosscheck: Auth implementation" \
  --body "Verified. No vulnerabilities found." \
  --type response
```

### Broadcast Notification

```bash
# Notify all AIs
empirica message-send \
  --to-ai-id "*" \
  --channel broadcast \
  --subject "Database migration complete" \
  --body "Migration finished. All services restored." \
  --type notification
```

---

## Python API

```python
from empirica.core.messaging import MessageBus, Message

bus = MessageBus(ai_id="claude-code")

# Send message
msg = Message(
    to_ai_id="philipp-code",
    subject="Investigation complete",
    body="Found 5 security issues",
    priority="high"
)
result = bus.send(msg)

# Check inbox
messages = bus.get_inbox(status="unread")

# Read message
message = bus.read(message_id="abc123...")

# Reply
bus.reply(
    message_id="abc123...",
    body="Acknowledged"
)
```

---

## Message Expiry

Messages have a TTL (time-to-live):

| TTL | Meaning |
|-----|---------|
| `0` | Never expires |
| `86400` | 24 hours (default) |
| `3600` | 1 hour |

Expired messages are automatically cleaned up but can be included with `--include-expired`.

---

## Related Documentation

- [HANDOFF_SYSTEM.md](../../architecture/HANDOFF_SYSTEM.md) — AI handoff coordination
- [MULTI_SESSION_LEARNING.md](../../human/developers/MULTI_SESSION_LEARNING.md) — Multi-agent collaboration
