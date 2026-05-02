# MITRE ATLAS — Adversarial Threat Landscape for AI Systems

**Canonical source:** https://atlas.mitre.org/
**Status:** populated — tactics summarised from canonical matrix; technique
list curated for scanner-relevant anchors. Refresh via the Phase 3
corpus-refresh loop — MITRE updates the matrix periodically.

> ATLAS uses the same tactic / technique structure as ATT&CK.
> Citations here will look like `T1499 — Endpoint Denial of Service`.
> Tactic names match ATLAS conventions; technique IDs are the canonical
> ATT&CK/ATLAS identifiers.

---

## Tactics (selected)

### Reconnaissance

The adversary gathers information about the AI system before attacking
— model capabilities, training data sources, deployed endpoints,
authentication mechanisms, deployed prompts, known fine-tuning data.
Includes both passive (public docs, repos) and active (querying the
deployed model) reconnaissance.

**Scanner relevance:** unauthorized scanning of the operator's own
agent inventory is itself reconnaissance. Auth-protected endpoints
limit the surface. The scanner's read-surface config is intentionally
local-only to avoid being a recon tool itself.

### Initial Access

The adversary first reaches the AI system — through malicious user
input, supply-chain compromise of a model/tokenizer, manipulated
training data, or compromised infrastructure (e.g., an unpatched
inference server).

**Scanner relevance:** listening ports + auth posture + version
strings on inference servers are the initial-access surface. The
scanner enumerates them; the auditor judges hardening.

### Execution

The adversary causes unintended code paths or actions to run — via
prompt injection, malicious model output triggering downstream
execution, or exploitation of an inference-runtime vulnerability.

**Scanner relevance:** processes accepting LLM output as command input
(see LLM-A02 / Agentic-A04) are the execution surface.

### Persistence

The adversary maintains presence after the initial action — via
cron entries, systemd units, modified shell rc files, scheduled
agent loops, modified plugin configurations. **Long-lived listener
processes from forgotten experiments map here** — they're not
adversary-installed but they exhibit the same persistence-without-purpose
pattern.

**Scanner relevance:** **direct.** The scheduled-tasks collector
enumerates cron + systemd timers + at-jobs. Agent loops registered
in the empirica loop registry are visible there too.

### Defense Evasion

The adversary avoids detection — disabling logging, obfuscating
process names, running under unusual user accounts, embedding in
legitimate-looking processes.

**Scanner relevance:** processes with mismatched names vs binaries,
processes running under unexpected users, gaps in log files
(history/diff between scans showing process disappearance with no
known cause) all surface here.

### Credential Access

The adversary obtains credentials to extend access — by reading
env vars, dumping memory of running processes, scraping config
files, or extracting credentials from training data via model
extraction. **Stale processes holding API keys to dead accounts
map here** — they're not adversary-controlled but they're
credential-access surface waiting for compromise.

**Scanner relevance:** **direct.** Env-var name enumeration surfaces
credential bearers. Process-age + outbound network state surface
which credentials have ongoing exposure. Cross-reference with
secret_scan for credential-grade material in repo content.

### Collection

The adversary gathers data of interest — training data, prompts,
fine-tuning corpora, conversation logs, model outputs — for
exfiltration or further analysis.

**Scanner relevance:** processes with broad filesystem read access
+ persistent state (databases, vector stores, log files) are the
collection surface.

### Exfiltration

The adversary moves data out — over agent-controlled network
channels, via output channels of legitimate agents (LLM-A06
disclosure pipeline), or via model-extraction queries.

**Scanner relevance:** outbound network state from agent processes,
listening ports on data-bearing services, manifest scope analysis
(does this agent need network reach for its declared purpose?).

### Impact

The adversary disrupts, corrupts, or destroys the AI system or its
outputs — DoS, model corruption via memory poisoning, biased outputs
that propagate downstream, financial damage via wallet-drain attacks.

**Scanner relevance:** resource-consumption metrics, billing-affecting
process inventory, agent loop heartbeats (silent agents may indicate
ongoing impact-tactic compromise).

## Techniques (selected anchors for scanner findings)

- **T1499** — Endpoint Denial of Service: long-running orphan agents,
  recursive tool loops, runaway autonomy. Maps to LLM-A04 and
  Agentic-A06.
- **T1078** — Valid Accounts (compromised credential reuse): stale
  credentials still authorising agents post-rotation. Maps to
  Agentic-A07.
- **T1588** — Obtain Capabilities (acquiring AI/ML capabilities):
  the supply-chain side — adversary obtains models/plugins/MCP
  servers via the same channels operators do. Maps to LLM-A05.
- **T1059** — Command and Scripting Interpreter: agent-driven
  shell execution paths. Maps to LLM-A02 and Agentic-A04.

The scanner's role is inventory, not attack-pattern detection — the
auditor cites these technique IDs when classifying findings whose
disposition matches the technique shape.
