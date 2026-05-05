# PROPOSAL: AI Service Scanner — local-first inventory of running AI-touching services

**Status:** Draft (2026-04-30)
**Author:** David S. L. Van Assche + Claude Code (Opus 4.7 1M)
**Related:**
- [`PROPOSAL_EVENT_LISTENER.md`](PROPOSAL_EVENT_LISTENER.md) — loop/listener primitives this rides on (shipped 1.9.0)
- [`COCKPIT.md`](COCKPIT.md) — visibility surface; new `services` panel proposed alongside `compliance`
- [`../research/COVERAGE_VECTORS_PAPER_OUTLINE.md`](../research/COVERAGE_VECTORS_PAPER_OUTLINE.md) — methodological companion: coverage as a first-class metric

---

## Honest framing first

The code-scanning space is saturated: Anthropic Claude Code Security, OpenAI Aardvark, GitHub Security Lab Taskflow, Snyk, Aikido. Building "another security scanner" would be AI slop and we know it. **This proposal is not that.**

Three things make this distinct, and we lead with them:

1. **Live running-state inventory, not source-code analysis.** Those tools scan code at rest. This scans processes, sockets, scheduled tasks, plugin manifests, and held connections — what's *actually executing on this machine right now.* The empirica-outreach orphan-cron incident (1.9.0 work) is the canonical failure mode; no static analyzer would have caught it.
2. **Epistemic measurement applied to a real failure mode.** Findings carry confidence + citation, with a coverage co-metric ("the agent saw N/M of available signals"). The methodological contribution — coverage vectors as a first-class metric — is paper-worthy on its own and unique to bounded-transaction frameworks like Empirica. See companion paper outline.
3. **Local-first, developer-workflow integrated.** No cloud telemetry, no SOC analyst persona. Sits next to `empirica status`, integrates with the loop/listener/notify infrastructure already shipped in 1.9.0. Audience is solo devs and small teams running AI-heavy stacks on their own machines.

The product is a developer-side implementation of what OWASP Agentic Top 10 (Dec 2025) explicitly recommends: *"Conduct a skill inventory across all agent platforms in use — treat this as an immediate priority given active exploitation confirmed in 2026."* Enterprise vendors (Bessemer-funded segment: Obsidian, Palo Alto, CrowdStrike, Trend Micro, Qualys, Microsoft agent-governance-toolkit) are all building the SOC-shaped version. The dev-machine shape is empty.

---

## Problem statement

Modern dev machines accumulate AI-touching services that the user did not deliberately set up — or did, weeks ago, and forgot. Concretely, on the machine this proposal was drafted on:

- Multiple Claude Code instances across tmux panes (some with stale active_transaction files)
- Background `ollama serve` from yesterday's experiment
- Several MCP servers from various plugins (chrome MCP, claude-mem MCP, empirica-mcp, others)
- Held curl listeners from 1.9.0 work (what triggered this proposal)
- CronCreate jobs from days/weeks ago — pre-1.9.0 we had no way to inventory them
- Browser MCP extension state
- Background `qdrant` daemon
- AI-related env vars exporting API keys with broad read scope

No tool currently answers: *"What AI-touching services are running on this machine right now, what does each do, what credentials does each hold, which need human attention?"*

The failure modes split cleanly:

| Class | Symptom | Cost |
|---|---|---|
| Token bleed | Cron firing forever, agent loop with no exit | $$$ — observed in production |
| Credential leak | Stale process holds API key to dead account | Security |
| Privilege drift | Agent gained shell access nobody remembers granting | Security |
| Cost surprise | Hosted operator running on your account | $$$ |
| Forgotten state | MCP server still talking to deprecated path | Reliability |
| Orphan listener | Held curl with dead parent process | Resource leak + security |

OWASP Agentic AI Top 10 mapping: A06 (vulnerable & outdated components), A09 (insufficient observability), A10 (excessive autonomy without oversight). NIST AI RMF: GOVERN-1.5 (third-party AI risk), MEASURE-2.7 (system inventory).

---

## Two-layer architecture

### Layer 1: Scanner (deterministic data collector)

Pure data collection. No regex deciding "interesting." No classification. Emits a snapshot.

**Tools used:**
- `psutil` — process tree, age, command-line, parent PID, working dir, num open files
- `ss` / `netstat` — open sockets, peer endpoints (no packet inspection)
- `lsof` — file descriptors held by each process (metadata only, never contents)
- `crontab -l` + `~/.config/systemd/user/` — scheduled tasks
- `git ls-files` + `~/.empirica/` — known-plugin manifests
- env-var name enumeration (NEVER values)
- `ps -o args` — full command lines

**Read-surface YAML** (in `project.yaml`'s `cockpit:` block, agent-defined boundary):

```yaml
cockpit:
  scanner:
    read_surface:
      process:
        - pid
        - cmdline
        - parent_pid
        - age_seconds
        - working_dir
        - num_open_files
        - cpu_percent
        - memory_mb
      network:
        - peer_host
        - peer_port
        - listening_ports
        # never: packet contents, header values
      filesystem:
        - plugin_manifest_paths
        - recently_touched_model_weights  # >1GB files in $HOME
        - env_files_present  # path only, never values
      process_env:
        - var_names_only  # never values; AI_*, *_API_KEY, OPENAI_*, ANTHROPIC_*, etc.
      scheduled:
        - cron_entries
        - systemd_user_units
        - launchd_agents  # macOS
      mcp:
        - registered_servers  # from ~/.claude/mcp.json
        - active_connections  # from socket scan
    relevant_globs_for_coverage:
      code:
        - "empirica/**/*.py"
      docs:
        - "docs/**/*.md"
      audit:
        - "**/*.py"
        - "**/*.json"
```

The YAML defines the agent's **read permission boundary**, not its classification logic. Heuristics describe what the read-only agent is allowed to inspect; judgment of "is this dangerous" is the agent's job in Layer 2.

**Output:** structured JSON snapshot. No interpretation.

```json
{
  "scan_id": "uuid",
  "started_at": "2026-04-30T20:00:00Z",
  "host": "machine-name",
  "snapshot": {
    "processes": [
      {"pid": 12345, "cmd": "curl -N https://ntfy.sh/...", "ppid": 1, "age_seconds": 1209600, "working_dir": "...", ...}
    ],
    "sockets": [...],
    "scheduled": [...],
    "env_var_names": ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", ...],
    "mcp_servers": [...]
  }
}
```

### Layer 2: Agent (looped empirica transaction)

Reads the scanner snapshot. Judges each entry against the bundled security corpus. Emits findings with confidence and citation. Runs as a normal empirica transaction with bounded scope and a dedicated persona.

**Persona:** new `services-auditor` registered in the persona registry. Read-only by design — no edit/write/exec tools. Judgment + finding-emission only.

**Bundled security corpus** at `empirica/data/security-corpus/` (5 markdown files, ~50-150 lines each):

| File | Source | Purpose |
|---|---|---|
| `owasp-llm-top10.md` | OWASP 2025 | LLM-specific vulnerability patterns |
| `owasp-agentic-top10.md` | OWASP Dec 2025 | Autonomous agent risk patterns |
| `nist-ai-rmf.md` | NIST AI RMF | Governance + risk frame |
| `mitre-atlas.md` | MITRE ATLAS | Adversarial threat patterns |
| `google-saif.md` | Google SAIF | Secure AI framework principles |

Bundled in the Python package; copied to `~/.empirica/security-corpus/` on `setup-claude-code` (writable for user customization). Biweekly refresh by separate scheduled loop pulls latest published versions.

**Two-tier judgment** (cost optimization):
1. **Cheap pass:** for each process, "is this AI-touching at all?" (fast classifier). Filters down ~200 processes to ~10-30.
2. **Full pass:** for each AI-touching process, full taxonomy judgment with citation. Outputs finding/assumption/unknown.

**Output schema:**

```json
{
  "scan_id": "uuid",
  "agent_run_id": "transaction_id",
  "coverage": {
    "snapshot_size": 200,
    "judged": 198,
    "errors": 2,
    "ratio": 0.99
  },
  "findings": [
    {
      "process": {"pid": 42342, "cmd": "..."},
      "verdict": "orphaned credentialed listener",
      "confidence": 0.97,
      "grounded_in": [
        {"source": "owasp-agentic-top10.md", "section": "A06 — Vulnerable and Outdated Components"},
        {"source": "mitre-atlas.md", "section": "T1499 — Endpoint Denial of Service"}
      ],
      "recommended_action": "kill 42342; investigate parent process recovery",
      "owasp_mapping": ["LLM-A06", "Agentic-A06"],
      "nist_mapping": ["GOVERN-1.5"]
    }
  ],
  "assumptions": [
    {"process": {...}, "assumption": "Local Ollama is safe because it's localhost-only", "confidence": 0.7}
  ],
  "unknowns": [
    {"process": {...}, "unknown": "Unfamiliar binary at /opt/foo — purpose unclear from cmdline alone"}
  ]
}
```

---

## Confidence model

Three grounding layers (developed across earlier brainstorm):

1. **Coverage grounding** — the scanner snapshot is ground truth for "what was there." Coverage = judged/snapshot_size. Falsifiable: mismatch = bug.
2. **Judgment grounding** — security canon citation. Every finding cites which corpus section grounds it. **Required field** — uncited findings drop to `assumption` or `unknown` regardless of model confidence.
3. **Outcome grounding** — user feedback over time (drift signal, NOT gate). User confirms/dismisses findings; calibration adjusts.

**Threshold ladder:**
- ≥0.95 confidence + cited → `finding` (alerts via ntfy, surfaces in cockpit panel)
- 0.6–0.95 confidence + cited → `assumption` (logged in empirica artifacts, no alert)
- <0.6 confidence OR uncited → `unknown` (logged for human review; never alerts)

**Empirica integration:** findings/assumptions/unknowns map to existing artifact taxonomy. Every scan generates artifacts in the regular knowledge graph. EU AI Act / GDPR / ISO 42001 audit trail comes for free via the existing compliance pipeline.

---

## CLI surface

```bash
empirica scan                            # one-shot snapshot + judgment
empirica scan --explain                  # verbose reasoning per finding
empirica scan --output json|markdown     # default markdown, json for piping
empirica scan history                    # list past scans
empirica scan diff <id1> <id2>           # what changed between scans
empirica scan show <id>                  # detail view of past scan

# Loop integration (uses existing primitives)
empirica loop install-request --instance <ID> \
  --name services-audit --interval 14d
```

Read-only by design. No `empirica scan kill` or similar. Findings carry `recommended_action` strings that the user can copy-paste; we don't execute.

---

## Cockpit integration

New `#services` panel below `#compliance` (mirrors the pattern shipped in 1.9.0):

```
🔍 services — 12 known · 2 flagged · 1 unknown (5m ago)
```

Press `s` to expand. Failures (high-confidence findings) show by default; passing/known services collapsed.

`aggregate_instance_state` reads `~/.empirica/last_scan_<project_id>.json` and surfaces:

```json
"services": {
  "scan_id": "...",
  "scanned_at": "...",
  "summary": {
    "total_processes": 200,
    "ai_touching": 12,
    "findings": 2,
    "assumptions": 5,
    "unknowns": 1
  },
  "top_findings": [...]  // top 5 by confidence
}
```

Action surfaces (read-only, but actionable):
- **ntfy:** new high-confidence finding → push notification (re-uses 1.9.0 dispatcher)
- **Notify integration:** `--source services-scanner` matched in routing rules
- **Click-through:** finding → detail view → copy-paste recommended action

---

## State files

| Path | Purpose | Owner |
|---|---|---|
| `~/.empirica/scans/<scan_id>.json` | Full snapshot + judgment per scan | scanner agent |
| `~/.empirica/last_scan_<project_id>.json` | Latest scan, for cockpit panel | scanner agent |
| `~/.empirica/scan_history_<project_id>.jsonl` | Append-only audit trail (one line per scan) | scanner agent |
| `~/.empirica/security-corpus/*.md` | Grounding canon (writable, user-customizable) | bundled package + biweekly refresh loop |

History retention: keep last 90 days by default; configurable in `cockpit.scanner.retention_days`.

---

## Loop integration (cockpit.loops in project.yaml)

```yaml
cockpit:
  loops:
    - name: services-audit
      kind: cron
      cron: "0 9 1,15 * *"  # biweekly with compliance loop
      description: "Biweekly AI service inventory + risk audit"
    - name: security-corpus-refresh
      kind: cron
      cron: "0 3 1 * *"  # monthly
      description: "Pull latest OWASP / NIST / MITRE corpus into ~/.empirica/security-corpus/"
```

L-click in cockpit installs both. The biweekly cadence stacks cleanly with the compliance-debt-sweep loop already configured.

---

## Implementation phases

| Phase | Version | Scope | Effort |
|---|---|---|---|
| **1** | 1.9.0 | One-shot `empirica scan` + scanner module + read-surface YAML + bundled corpus + Markdown report. Deterministic only — no AI judgment yet. | ~3 days |
| **2** | 1.9.0 | `--explain` AI judgment layer + `services-auditor` persona + cockpit `#services` panel + click-to-expand. | ~5 days |
| **3** | 1.9.0 | Biweekly loop + ntfy integration + history/diff verbs + corpus-refresh loop. | ~3 days |
| **4** | 1.9.x | RAG over corpus (Qdrant collection) + dynamic CVE feed + cross-instance fleet view. | ~2 weeks |

Phase 1 alone solves the empirica-outreach orphan-cron failure mode. Phase 2 adds the empirica-distinctive judgment + coverage. Phase 3 makes it autonomous.

---

## Out of scope (V1)

- **Multi-host fleet view** — separate product (`empirica fleet`), separate company decisions
- **Adversarial evasion resistance** — V1 assumes honest user. Enterprise sale needs this; not yet
- **Network packet inspection** — explicitly read-only on metadata only
- **Action layer that kills processes** — read-only by design; `recommended_action` strings only
- **Hosted-agent inventory** (GitHub Operator, ChatGPT Operator, claude.ai Projects on your account) — Phase 4+, requires API token introspection
- **Browser tab inventory** (browser-extension scope) — possible companion but not core scanner

---

## Risks

1. **The scanner becomes the very thing it's meant to detect.** Long-lived daemon with broad read access. Mitigation: prefer one-shot scans, biweekly loop fires + exits, no persistent daemon. Self-row in scan output: scanner shows itself.
2. **Privacy data ingestion.** Scanner sees env-var names (potentially their values), open file paths, network endpoints. Must stay local. Same posture as Empirica today; align the docs.
3. **Token cost on busy machines.** 200+ processes × full taxonomy judgment is expensive. Two-tier judgment (cheap pre-filter, full pass on AI-touching subset) keeps it bounded. Estimate: 200 processes → ~10-30 AI-touching → <$0.10/scan with Sonnet.
4. **False positives** in the 0.6-0.95 range. By design these go to `assumption` not `finding`, so they don't alert. But unbounded `assumption` accumulation is noise. Cap per scan: top 10 by confidence.
5. **Corpus staleness.** OWASP / NIST update annually; CVE feeds daily. Monthly corpus-refresh loop is the floor; users can run manually or wire to git pull.

---

## Why empirica is the right home

Three reasons, in increasing order of strategic importance:

1. **Infrastructure already shipped.** Loop primitives, listener primitives, notify dispatcher, cockpit aggregation, persona registry, artifact graph, compliance pipeline, Qdrant retrieval. The scanner-agent is one more application of these primitives, not a new substrate. Implementation is mostly composition.

2. **Coverage measurement.** Empirica is the only framework that can compute coverage in this context, because it's the only framework with explicit transaction boundaries. Coverage is the methodological differentiator — paper-worthy on its own and uniquely accessible to us.

3. **Brand coherence.** "Empirica measures what AI knows and doesn't know." Scanner extends that to "Empirica measures what AI services are running and how dangerous they are." Same thesis, different surface. The product's positioning writes itself: *"You wouldn't ship code without tests. Why are you running autonomous agents without inventory?"*

---

## Companion: paper

Methodological contribution (coverage vectors) is written up separately at [`docs/research/COVERAGE_VECTORS_PAPER_OUTLINE.md`](../research/COVERAGE_VECTORS_PAPER_OUTLINE.md). Product spec drives implementation; paper drives positioning + publication. Different audiences, mutually reinforcing.

---

## Open questions

1. **Bundled-corpus size.** Should the 5 corpus files ship as full text (~5KB each, ~25KB total in the wheel) or as URLs the agent fetches on first use? Bundled is simpler + offline-safe; fetched is always-fresh. Vote: bundle, refresh via loop.
2. **Persona definition.** `services-auditor` persona prompt needs writing. Should mirror existing personas in `~/.empirica/voice/` patterns. ~30 lines of prompt with explicit read-only-ness, citation requirement, confidence ladder.
3. **First-fire cadence.** Should phase-1 install fire immediately at install or wait for first scheduled cron? Vote: immediate fire on install (otherwise user has to wait 14 days to see anything).

---

## Acceptance criteria for Phase 1

- [ ] `empirica scan` produces structured JSON snapshot of running processes + sockets + scheduled tasks + env vars (names) + plugin manifests
- [ ] Read-surface YAML in `project.yaml` is honored (agent never reads outside it)
- [ ] Markdown report renders cleanly for human consumption
- [ ] Scanner shows itself in its own output (self-row)
- [ ] No persistent daemon (one-shot only)
- [ ] Tests cover: snapshot shape, read-surface enforcement, scanner-self-detection
- [ ] `docs/architecture/SERVICES_SCANNER.md` written (graduates from this proposal)
- [ ] CHANGELOG entry under `[Unreleased]`
