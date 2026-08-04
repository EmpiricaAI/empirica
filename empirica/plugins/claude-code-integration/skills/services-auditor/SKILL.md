---
name: services-auditor
description: "Use when the user runs `empirica scan --explain` or asks you to audit running AI services. You read the deterministic scanner snapshot, judge each AI-touching process against the bundled security corpus, and emit findings/assumptions/unknowns with confidence + cited corpus sections. Two-tier judgment (cheap AI-touching pre-filter, then full taxonomy with citation). Read-only by design — never kill processes or modify configuration; emit `recommended_action` strings only. Tracks citation coverage explicitly (which of the 5 corpus files were referenced before each finding) so trust grounding is auditable."
version: 1.0.0
---

# Services Auditor — AI judgment over a deterministic scanner snapshot

This skill fires when an AI agent is asked to reason about the running
state of an AI-touching machine. The deterministic scan
(`empirica scan`) gives you the snapshot — what's actually executing
right now. Your job is to **judge** each AI-touching entry against the
bundled security corpus and emit empirica artifacts with confidence
and citation.

You are not a separate process. You are the AI session that the user
asked to audit. Run inside a normal empirica transaction.

---

## When to use

- The user typed `empirica scan --explain` and a system-reminder
  pointed you here.
- The user said "audit running services," "what AI agents are dangerous
  here," "review the scan output," or similar.
- A scheduled `services-audit` loop fired (Phase 3, future) and woke
  you with this skill referenced.

If you only need the inventory itself (no judgment), point the user
at `empirica scan` instead.

---

## Phase 0 — PREFLIGHT

Open a transaction with the audit work_type so the Sentinel weights
your evidence sources correctly:

```bash
empirica preflight-submit - <<'EOF'
{
  "task_context": "Services audit — read the deterministic scanner snapshot at ~/.empirica/last_scan_<project_id>.json, judge each AI-touching entry against the security corpus, emit findings/assumptions/unknowns.",
  "work_type": "audit",
  "domain": "default",
  "criticality": "medium",
  "vectors": { "...": "YOUR assessment across the 13 vectors" },
  "reasoning": "Audit transaction. Will read snapshot + corpus, judge per-process against the taxonomy, cite sections."
}
EOF
```

---

## Phase 1 — Read inputs

Two files are load-bearing:

### 1. The scanner snapshot

Always read the most recent saved snapshot (the user gets this via
`empirica scan --explain` which auto-saves):

```bash
cat ~/.empirica/last_scan_<project_id>.json
```

If absent, run `empirica scan --save` yourself first, then read it.

### 2. The bundled security corpus

Stable, citable canon at `empirica/data/security-corpus/` (or the
user-customizable copy at `~/.empirica/security-corpus/` if present):

| File | Source | Section IDs you cite |
|---|---|---|
| `owasp-llm-top10.md` | OWASP 2025 | `LLM-A01` … `LLM-A10` |
| `owasp-agentic-top10.md` | OWASP Dec 2025 | `Agentic-A01` … `Agentic-A10` |
| `nist-ai-rmf.md` | NIST AI RMF 1.0 | `GOVERN-1.5`, `MEASURE-2.7`, … |
| `mitre-atlas.md` | MITRE ATLAS | `T1499`, `T1078`, `T1588`, `T1059`, … |
| `google-saif.md` | Google SAIF | `SAIF-1` … `SAIF-6` |

Section IDs are stable across revisions even when the body content
is currently a stub. Cite the IDs.

---

## Phase 2 — Two-tier judgment

### Tier 1 — Cheap AI-touching pre-filter

Walk the snapshot's process list. For each row, classify in one
short pass: **AI-touching** (`true` / `false`).

A process is AI-touching if any of:
- cmdline contains `claude`, `cursor`, `codex`, `aider`, `gh copilot`,
  `gemini`, `ollama`, `vllm`, `llama-cpp`, `lmstudio`, `openai`,
  `anthropic`, `cohere`, `huggingface`, `replicate`, `qdrant`,
  `chromadb`, `weaviate`, `pinecone`, `langchain`, `crewai`, `autogen`
- holds an env var name matching `*_API_KEY` for an AI vendor (cross-
  reference `process_env.var_names_only` if available)
- listens on a port commonly used by local AI tooling (11434 ollama,
  8000/8080 generic LLM servers, 6333 qdrant default, 6379 redis if
  flagged in registered MCP servers)
- is registered as an MCP server in `~/.claude/mcp.json`

Filter the ~hundreds of processes down to a working set of ~10–30.
Most processes (browsers, terminals, system daemons) are not
AI-touching and don't need full taxonomy judgment.

### Tier 2 — Full taxonomy per AI-touching process

For each survivor, judge against the corpus and emit one artifact.

**Confidence ladder** (per the proposal):

| Confidence | Citation present? | Artifact type | Behavior |
|---|---|---|---|
| ≥ 0.95 | yes | `finding-log` | high-trust |
| 0.6 – 0.95 | yes | `assumption-log` | medium-trust, logged |
| < 0.6 | _any_ | `unknown-log` | needs human review |
| _any_ | **no** | `unknown-log` | uncited downgrades |

Emission examples (use the batch form when emitting many at once via
`log-artifacts`; the single-verb form is fine for one-off artifacts):

```bash
# High-trust finding with citation
empirica finding-log --finding "PID 12345 (curl -N https://ntfy.sh/...) is an orphaned credentialed listener — parent PID 1, cmdline references ntfy auth env vars, age 14 days. Recommended: kill 12345 + investigate parent recovery." \
  --impact 0.85 --visibility shared --output json
empirica source-add --title "OWASP Agentic Top 10 — A06: Vulnerable & Outdated Components" \
  --url "https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/" \
  --noetic --confidence 0.95 --output json
# Then link via log-artifacts evidence edge if you want the graph

# Medium-trust assumption (not enough signal for finding)
empirica assumption-log --assumption "PID 87654 (ollama serve, age 2 days) is benign because it's localhost-only on port 11434 with no external peers in the snapshot." \
  --confidence 0.75 --domain security --visibility shared

# Unknown — uncertain or uncited
empirica unknown-log --unknown "PID 98765 (/opt/foo/binary, no recognizable cmdline) — purpose unclear, no AI vendor signature, no listening port. Manual investigation required."
```

### Citation discipline (load-bearing)

Every `finding` and `assumption` you emit MUST cite at least one
corpus section ID. The citation goes in the artifact text itself
(human-readable) AND optionally as a `source-add` + `sourced_from`
edge for graph traversal.

**Uncited findings are downgraded to `unknown` regardless of model
confidence.** This is not negotiable — it is the trust-grounding
contract that makes the auditor's output auditable.

---

## Phase 3 — Coverage tracking

The paper (`COVERAGE_VECTORS_PAPER_OUTLINE.md`) defines coverage as
inspected / relevant. Track yours explicitly so the user can see what
fraction of the relevant material you actually inspected:

| Dimension | Numerator | Denominator |
|---|---|---|
| **Process coverage** | processes you full-judged in tier 2 | AI-touching processes after tier 1 filter |
| **Citation coverage** | unique corpus section IDs you cited | corpus sections that exist (sum across the 5 files) |
| **Listener coverage** | listeners you judged | total listeners in `network.connections` |

Surface the numbers in your final summary, e.g.:

```
Coverage: 18/24 AI-touching processes judged (75%),
          7/52 corpus sections cited (13%),
          4/4 listeners judged (100%).
```

A 95%-confidence finding with 13% citation coverage is honest. A
95%-confidence finding without a coverage report is not.

---

## Phase 4 — POSTFLIGHT

Close the transaction with grounded vectors that reflect what you
actually did:

```bash
empirica postflight-submit - <<'EOF'
{
  "vectors": { "...": "YOUR assessment — what the audit actually established"
  },
  "reasoning": "Audit complete. Judged N AI-touching processes against corpus. Emitted X findings + Y assumptions + Z unknowns. Citation coverage K/52. Recommended actions surfaced as text — no destructive operations performed."
}
EOF
```

Phase 3 (future) wires a biweekly cron loop that fires this skill
automatically. Today, the user runs it on demand.

---

## Out of scope (V1)

- **Process killing or config mutation.** Read-only. `recommended_action`
  strings only. Empirica does not execute them; the user does.
- **Network packet inspection.** Metadata only — connection 5-tuple +
  listening ports. Same posture as Phase 1.
- **Multi-host fleet view.** Separate product (`empirica fleet`).
- **Hosted-agent inventory** (cloud operators on user's account) —
  Phase 4+ — needs API token introspection.
- **RAG over the corpus.** Phase 4 — needs Qdrant collection.
- **Fine-grained semantic confidence calibration over many runs** —
  comes from coverage paper validation work, not the auditor itself.

---

## Anti-patterns

- **Emitting findings without citation.** Downgrade to unknown.
- **Listing every process in the system as "interesting."** Tier 1 must
  filter aggressively. Most processes are not AI-touching.
- **Skipping the snapshot read.** The deterministic snapshot is the
  ground truth — reasoning from memory or guess is uncited and breaks
  the contract.
- **Inflating confidence to clear the citation requirement.** The
  ladder gates by both — confidence ≥ 0.95 + cited is the only
  "finding" path. Inflating confidence to dodge an honest "unknown"
  is exactly the failure mode the auditor exists to flag in others.
- **Killing processes or pushing config changes.** Read-only by
  design. If you observe a vulnerability that warrants action, the
  user takes the action.

---

## Sister skill: `/services-audit-cron`

For unattended scheduled scans (Phase 3), invoke `/services-audit-cron`
to register the canonical biweekly cron loop. Body is one command
(`empirica services-audit`) that does scan + diff + notify-on-novelty;
loop registry + heartbeat handle the schedule. Complementary, not
redundant: `services-auditor` is on-demand AI judgment; the cron loop
is automated novelty detection.
