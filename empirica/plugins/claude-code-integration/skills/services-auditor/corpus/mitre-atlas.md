# MITRE ATLAS — Adversarial Threat Landscape for AI Systems

**Canonical source:** https://atlas.mitre.org/
**Edition:** ATLAS content version 2026.09 (data modified 2026-09-15), data format 6.0.0
**Verified:** 2026-10-15 against https://github.com/mitre-atlas/atlas-data (dist/v6/ATLAS-2026.09.yaml, dist/manifest.yaml, CHANGELOG.md)
**Refresh:** manual; see README.md in this directory.

> ATLAS has its own identifier scheme: tactics are `AML.TA####`, techniques
> are `AML.T####`, sub-techniques `AML.T####.###`. Many ATLAS techniques
> carry a cross-reference to an ATT&CK enterprise ID (e.g. `AML.T0050`
> references ATT&CK `T1059`), but the ATT&CK ID is **not** the ATLAS ID.
> Cite the `AML.*` ID. ATT&CK IDs are given in parentheses only as a
> cross-reference where the published data supplies one.
> The 2026.09 matrix has 16 tactics, in this published column order:
> Reconnaissance, Resource Development, AI Attack Adaptation, Initial
> Access, AI Model Access, Execution, Persistence, Privilege Escalation,
> Defense Evasion, Credential Access, Discovery, Lateral Movement,
> Collection, Command and Control, Exfiltration, Impact.

---

## Tactics (selected)

### Reconnaissance (AML.TA0002)

The adversary gathers information about the AI system before attacking
— model capabilities, training data sources, deployed endpoints,
authentication mechanisms, deployed prompts, known fine-tuning data.
Includes both passive (public docs, repos) and active (querying the
deployed model) reconnaissance.

**Scanner relevance:** unauthorized scanning of the operator's own
agent inventory is itself reconnaissance. Auth-protected endpoints
limit the surface. The scanner's read-surface config is intentionally
local-only to avoid being a recon tool itself.

### Initial Access (AML.TA0004)

The adversary first reaches the AI system — through supply-chain
compromise of a model, tool or software dependency, abuse of valid
accounts, or exposed/misconfigured infrastructure (e.g., an unpatched
or unauthenticated inference server). (Our earlier wording also listed
malicious user input and manipulated training data here; in ATLAS,
prompt injection is catalogued under Execution and training-data
poisoning under Persistence.)

**Scanner relevance:** listening ports + auth posture + version
strings on inference servers are the initial-access surface. The
scanner enumerates them; the auditor judges hardening.

### Execution (AML.TA0005)

The adversary causes unintended code paths or actions to run — via
prompt injection, malicious model output triggering downstream
execution, unsafe AI artifacts (e.g. model files that run code when
loaded), poisoned agent tools, or exploitation of an inference-runtime
vulnerability.

**Scanner relevance:** processes accepting LLM output as command input
(see LLM-A02 / Agentic-A04) are the execution surface.

### Persistence (AML.TA0006)

The adversary maintains presence after the initial action — via
cron entries, systemd units, modified shell rc files, scheduled
agent loops, modified plugin or agent configurations, poisoned agent
memory or context, poisoned agent tools. **Long-lived listener
processes from forgotten experiments map here** — they're not
adversary-installed but they exhibit the same persistence-without-purpose
pattern.

**Scanner relevance:** **direct.** The scheduled-tasks collector
enumerates cron + systemd timers + at-jobs. Agent loops registered
in the empirica loop registry are visible there too.

### Defense Evasion (AML.TA0007)

The adversary avoids detection — disabling logging, obfuscating
process names, running under unusual user accounts, embedding in
legitimate-looking processes.

**Scanner relevance:** processes with mismatched names vs binaries,
processes running under unexpected users, gaps in log files
(history/diff between scans showing process disappearance with no
known cause) all surface here.

### Credential Access (AML.TA0013)

The adversary obtains credentials to extend access — by reading
env vars, dumping memory of running processes, scraping config
files (including AI agent and tool configuration), or harvesting them
through an agent's tools. **Stale processes holding API keys to dead
accounts map here** — they're not adversary-controlled but they're
credential-access surface waiting for compromise.

**Scanner relevance:** **direct.** Env-var name enumeration surfaces
credential bearers. Process-age + outbound network state surface
which credentials have ongoing exposure. Cross-reference with
secret_scan for credential-grade material in repo content.

### Collection (AML.TA0009)

The adversary gathers data of interest — training data, prompts,
fine-tuning corpora, conversation logs, model outputs — for
exfiltration or further analysis.

**Scanner relevance:** processes with broad filesystem read access
+ persistent state (databases, vector stores, log files) are the
collection surface.

### Exfiltration (AML.TA0010)

The adversary moves data out — over agent-controlled network
channels, via output channels of legitimate agents (LLM-A06
disclosure pipeline), via write-capable agent tools, or via
model-extraction queries.

**Scanner relevance:** outbound network state from agent processes,
listening ports on data-bearing services, manifest scope analysis
(does this agent need network reach for its declared purpose?).

### Impact (AML.TA0011)

The adversary disrupts, corrupts, or destroys the AI system or its
outputs — DoS, model corruption via memory poisoning, biased outputs
that propagate downstream, financial damage via wallet-drain attacks.

**Scanner relevance:** resource-consumption metrics, billing-affecting
process inventory, agent loop heartbeats (silent agents may indicate
ongoing impact-tactic compromise).

### Tactics ATLAS defines that this file did not previously list

Added so a scanner can cite the full 2026.09 tactic set. Short glosses
are our own summaries of the published tactic descriptions.

- **Resource Development** (AML.TA0003) — adversary builds or acquires
  resources (infrastructure, accounts, capabilities, poisoned artifacts)
  to support operations.
- **AI Attack Adaptation** (AML.TA0001) — adversary adapts capabilities,
  methods or objectives into attack-ready outputs for the target.
  Renamed from "AI Attack Staging" in 2026.08.
- **AI Model Access** (AML.TA0000) — adversary gains some level of access
  to an AI model (inference API through full model access).
- **Privilege Escalation** (AML.TA0012) — adversary gains higher-level
  permissions (e.g. via agent tool invocation, jailbreak, container
  escape).
- **Discovery** (AML.TA0008) — adversary works out the AI environment,
  including agent configuration and tool definitions.
- **Lateral Movement** (AML.TA0015) — adversary moves through the AI
  environment.
- **Command and Control** (AML.TA0014) — adversary communicates with
  compromised AI systems or agents to control them.

## Techniques (selected anchors for scanner findings)

Our four original anchors, corrected. The IDs `T1499`, `T1078`, `T1588`
and `T1059` are ATT&CK enterprise IDs, not ATLAS IDs.

- **AML.T0029** — Denial of AI Service (replaces **T1499**, Endpoint
  Denial of Service: long-running orphan agents, recursive tool loops,
  runaway autonomy). ATLAS has no technique that references ATT&CK
  T1499 and no technique named Endpoint Denial of Service; the closest
  are AML.T0029 (flooding an AI service) and the sub-technique
  **AML.T0034.002** — Agentic Resource Consumption (under AML.T0034
  Cost Harvesting). Maps to LLM-A04 and Agentic-A06.
- **AML.T0012** — Valid Accounts (= **T1078**, compromised credential
  reuse; ATLAS cross-references ATT&CK T1078): stale credentials still
  authorising agents post-rotation. Tactics: Initial Access, Privilege
  Escalation, Lateral Movement. Maps to Agentic-A07.
- **AML.T0016** — Obtain Capabilities (= **T1588**; ATLAS cross-references
  ATT&CK T1588): the supply-chain side — adversary obtains tooling the
  same way operators do; ATLAS sub-techniques include AML.T0016.004 AI
  Agent Tools. Tactic: Resource Development. The consumer-side
  supply-chain technique is **AML.T0010** — AI Supply Chain Compromise
  (see below). Maps to LLM-A05.
- **AML.T0050** — Command and Scripting Interpreter (= **T1059**; ATLAS
  cross-references ATT&CK T1059): agent-driven shell execution paths.
  Tactic: Execution. Maps to LLM-A02 and Agentic-A04.

Additional anchors for local agents, MCP servers, model files,
credential access and exfiltration (tactic in brackets):

**Agent and MCP tooling**
- **AML.T0010.005** — AI Agent Tool (sub-technique of AML.T0010 AI Supply
  Chain Compromise) [Initial Access]: compromised agent tool as the
  supply-chain entry.
- **AML.T0110** — AI Agent Tool Poisoning [Persistence], with
  sub-techniques AML.T0110.000 Definition and Instructions, AML.T0110.001
  Implementation, AML.T0110.002 Runtime Response. The published
  description names MCP servers among the tool kinds in scope.
- **AML.T0011.002** — Poisoned AI Agent Tool (under AML.T0011 User
  Execution) [Execution].
- **AML.T0053** — AI Agent Tool Invocation [Execution, Privilege
  Escalation, Lateral Movement]: adversary uses an agent's tool access.
- **AML.T0051** — LLM Prompt Injection [Execution]; AML.T0051.000 Direct,
  AML.T0051.001 Indirect, AML.T0051.002 Triggered.
- **AML.T0080** — AI Agent Context Poisoning [Persistence]; AML.T0080.000
  Memory, AML.T0080.001 Thread.
- **AML.T0081** — Modify AI Agent Configuration [Persistence, Defense
  Evasion].
- **AML.T0109** — AI Supply Chain Rug Pull [Defense Evasion]: trusted
  component later updated to a malicious variant.
- **AML.T0112.000** — Local AI Agent (under AML.T0112 Machine Compromise)
  [Impact]: abuse of agents running on a host with broad permissions.
- **AML.T0105** — Escape to Host [Privilege Escalation] (ATT&CK T1611).
- **AML.T0101** — Data Destruction via AI Agent Tool Invocation [Impact].
- **AML.T0132** — Misconfigured or Publicly Exposed AI Services [Initial
  Access]: added in 2026.09; e.g. missing authentication on LLM runtimes.

**Model files and AI artifacts**
- **AML.T0011.000** — Unsafe AI Artifacts [Execution]: artifacts that do
  harm when loaded or used (serialized models are the standard case).
- **AML.T0018.002** — Embed Malware (under AML.T0018 Manipulate AI Model)
  [AI Attack Adaptation, Persistence]: malicious code in model files, with
  pickle-style deserialization the stated example.
- **AML.T0010.003** — Model, **AML.T0010.001** — AI Software,
  **AML.T0010.004** — Container Registry (all under AML.T0010)
  [Initial Access].
- **AML.T0115** — Publish Poisoned AI Artifacts [Resource Development];
  AML.T0115.000 Datasets, AML.T0115.001 Models, AML.T0115.002 AI Agent
  Tools.
- **AML.T0035** — AI Artifact Collection [Collection].
- **AML.T0020** — Training Data Poisoning [Persistence].

**Credential access**
- **AML.T0055** — Unsecured Credentials [Credential Access] (ATT&CK
  T1552): plaintext files, shell history, environment variables.
- **AML.T0083** — Credentials from AI Agent Configuration [Credential
  Access].
- **AML.T0098** — AI Agent Tool Credential Harvesting [Credential Access].
- **AML.T0082** — RAG Credential Harvesting [Credential Access].
- **AML.T0090** — OS Credential Dumping [Credential Access] (ATT&CK
  T1003).

**Collection and exfiltration**
- **AML.T0037** — Data from Local System [Collection] (ATT&CK T1005).
- **AML.T0036** — Data from Information Repositories [Collection].
- **AML.T0086** — Exfiltration via AI Agent Tool Invocation [Exfiltration]:
  data encoded into the parameters of a write-capable tool.
- **AML.T0025** — Exfiltration via Cyber Means [Exfiltration].
- **AML.T0057** — LLM Data Leakage [Exfiltration].
- **AML.T0024** — Exfiltration via AI Inference API [Exfiltration];
  AML.T0024.002 Extract AI Model.
- **AML.T0077** — LLM Response Rendering [Exfiltration].

The scanner's role is inventory, not attack-pattern detection — the
auditor cites these technique IDs when classifying findings whose
disposition matches the technique shape.

*Unverified (carried over from the previous revision, not checked against
ATLAS): the `LLM-A0x` / `Agentic-A0x` cross-references above belong to
other frameworks' files and were not part of this ATLAS check. Tactic
"Scanner relevance" paragraphs are Empirica's own guidance, not ATLAS
content.*
