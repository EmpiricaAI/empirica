# Messaging — two layers, and why both exist

**`message-*` and `mailbox *` are different systems with confusingly similar
names.** One needs no server and ships in core; the other is Cortex's proposal
layer. Confusing them leads people to conclude that Empirica cannot do AI-to-AI
messaging without Cortex, which is false.

Companion: [`TRIGGER_MODEL.md`](TRIGGER_MODEL.md) covers *when* a practice wakes.
This document covers *what it can say, and to whom*.

---

## The two layers

| | **`message-*`** | **`mailbox *`** |
|---|---|---|
| Transport | **git notes** (`refs/notes/empirica/messages/…`) | HTTPS to Cortex `/v1/orchestration/*` |
| Needs Cortex | **No** | **Yes** |
| Needs a network | Only a git remote — works offline, delivers on push | Yes |
| Verbs | `message-send`, `message-inbox`, `message-read`, `message-reply`, `message-thread`, `message-channels`, `message-cleanup` | `mailbox poll`, `mailbox show`, `mailbox reply`, `mailbox archive`, `mailbox sers` |
| Carries | A message — sent, read, replied to | A **proposal**: a typed request that someone *act* |
| Gating | None. Peers exchange text. | ECO Accept/Decline before the target acts |
| Durability | Travels with the repo; survives clone | Cortex-side, queryable across practices |
| Reaches | Anyone sharing a git remote | Practices registered with Cortex |

### `message-*` — the Cortex-free layer

Backed by `GitMessageStore`, which writes messages as git notes. No server, no
account, no HTTP anywhere in the module. Two AIs sharing a git remote can
message each other — including air-gapped, where delivery happens whenever
someone pushes.

Verified rather than asserted: with `CORTEX_API_KEY` and `EMPIRICA_CORTEX_URL`
stripped from the environment, `message-send` succeeds and `message-inbox` reads
the message back.

```bash
empirica message-send --to-ai-id empirica --subject "..." --body "..."
empirica message-inbox --ai-id empirica
```

**This is the layer to build on for a Cortex-less deployment** — ecodex, the
Empirica Foundation, an air-gapped install, or anyone wanting peer messaging
without standing up infrastructure.

### `mailbox *` — the Cortex proposal layer

Not "messaging with extra steps". A **proposal** asks another practice to *do
something*, and it passes an ECO (human or auto) decision before the target
acts. The mailbox verbs are how a practice sees proposals aimed at it, replies
with a completion handshake, and tracks the status of what it emitted.

That gate is precisely why this layer needs a server: something must hold the
authoritative decision state, and it cannot be the repo, because the two
practices may not share one.

```bash
empirica mailbox poll --outbox --limit 50    # reports matched / has_more
empirica mailbox reply --parent-id <id> --summary "..." --result shipped
```

---

## Choosing

```
Do you need the other side to DO something, gated by a decision?
├── yes → mailbox (Cortex). Proposals, ECO, completion handshakes.
└── no — you need to say something
    ├── share a git remote with them?
    │   └── yes → message-*.  No server, no account, works offline.
    └── no  → mailbox (Cortex) is the only path that reaches them.
```

Rule of thumb: **`message-*` carries words; `mailbox *` carries authority.**

---

## The naming overlap (read this before filing a bug)

`message-inbox` and `mailbox poll` both sound like "check my messages" and are
unrelated code paths over unrelated stores. The collision is real and known:

- `message-inbox` requires `--ai-id`; `mailbox poll` infers it
- `message-send --to` is ambiguous (`--to-ai-id` vs `--to-machine`)
- "inbox" appears in both vocabularies meaning different things

**A message sent with `message-send` will never appear in `mailbox poll`, and
vice versa.** They share no storage, and neither is malfunctioning when it fails
to show the other's traffic. If a command seems to be ignoring a message you know
exists, check which layer wrote it before assuming a defect.

---

## For AIs reading this

Your system prompt's mesh guidance (`/cortex-mailbox-send`,
`/cortex-mailbox-poll`) describes the **Cortex layer**. Everything there —
collab vs propose, ECO gating, canonical 3-form addressing, the completion
handshake — applies to `mailbox *` and the `cortex_*` MCP tools.

**None of it applies to `message-*`.** That layer needs no canonical id, no API
key, and no ECO decision. So do not conclude from a Cortex outage, a missing
credential, or an unregistered peer that you cannot communicate: if you share a
repo, `message-send` is available and unaffected.
