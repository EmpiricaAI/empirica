---
name: practice-model
description: "Use when the question is which practice or project work belongs to: writing to another practice with --project-id, reading another practice's artifacts, working on a remote or client machine (work_type remote-ops), a Claude Code vs Claude Desktop vs empirica project mix-up, or where a client's identity lives (crm-mcp, not entity_registry). Triggers: '--project-id', 'which project', 'practice vs project', 'remote-ops', 'entity_registry'."
version: 1.0.0
---

# Practice model

Load it when the answer to "where does this belong, and as whom am I acting" is not obvious from the working directory.

---

## The practice model

The prompt's IDENTITY section defines practice, practitioner and agent; the unit of identity is the practice, not the LLM, the
directory or the conversation. Two more terms matter here: a **client / contact** is an entity the practice serves, and an
**engagement** is a scoped piece of work for one. The identity and status of both live in `crm-mcp`, not in `entity_registry`.

### Entity registry as the shared substrate

`~/.empirica/workspace/workspace.db` holds an `entity_registry` of the practices
(`entity_type='project'`) and users across the org. `entity_memberships` (M:N) holds
typed relationships — `member-of`, `serves`, `uses`, `owns`.

**Organizations, contacts and engagements are not registry state any more.** Core's
CRM projection was retired (commit `fcf442110`): `crm-mcp` is the canonical store
(`empirica-workspace org|contact|engagement`, or the `crm_*` tools). Old
`organization`, `contact` and `engagement` rows may still sit in the registry; their
ids are valid join targets, but their name and status are stale. Do not read them
with `entity-show`; ask `crm-mcp`. What the practice learned about a client lives in
its own epistemic graph (`empirica-workspace entity knowledge --entity ...`).

**Vocabulary vs storage:** the table stores `entity_type='project'`; the concept is
"practice". Both are correct — one is the literal value, one is the load-bearing
idea. Future types (`ai`, `agent`, `skill`) are not populated, so don't claim them as
current state.

Walk it from any node: `entity-list` (by type/status) · `entity-show <type:id>` (one
entity plus its edges) · `entity-walk <type:id> [--depth N]` (BFS, cycle-protected) ·
`entity-search <query>`. All take `--output {human|json}`.

### When practice ≠ working directory

`.empirica/project.yaml`'s `ai_id` is canonical; filesystem location is incidental.
The Sentinel, calibration and inbox routing all follow `ai_id`.

- **SSH'd into a client's machine.** Your CWD is theirs, but you act *as* your home
  practice. Set `work_type=remote-ops` so the Sentinel reports
  `ungrounded_remote_ops` instead of scoring against an empty git tree.
- **Reading another practice's findings.** `project-search --project-id <other>
  --task "..."` reaches across. Don't `cd` over and re-bootstrap just to read.
- **Writing elsewhere.** Default to your active practice; use `--project-id <other>`
  only when you genuinely discovered something that practice owns. Switching
  practices to write one finding costs you context for the next ten. (Supported today
  on `finding-log` and `unknown-log`; other verbs need the full UUID.)

### Three things called "project"

- **Empirica practice** — the epistemic seat, identified by `ai_id`.
  Artifacts accumulate here and persist across models and moves; calibration accrues to the model that did the work.
- **Claude Code project** — a filesystem location with its own `.claude/`. Often
  one-to-one with a practice; not always.
- **Claude Desktop project** — a conversation-context bundle. Orthogonal to both.
