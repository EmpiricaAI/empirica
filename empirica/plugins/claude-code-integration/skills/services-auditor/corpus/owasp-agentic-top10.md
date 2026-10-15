# OWASP Top 10 for Agentic Applications 2026 (December 2025)

**Canonical source:** https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
**Edition:** Version 2026, December 2025 (announced 2025-12-09)
**Verified:** 2026-10-15 against https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/ (landing page, its PDF https://genai.owasp.org/download/52117/?tmstv=1765059207, and the Agentic Security Initiative page https://genai.owasp.org/initiatives/agentic-security-initiative/)
**Refresh:** manual; see README.md in this directory.

> Official title: "OWASP Top 10 For Agentic Applications 2026", from the OWASP
> GenAI Security Project's Agentic Security Initiative. Official IDs are
> `ASI01` through `ASI10`. As of 2026-10-15 the initiative page lists no
> newer edition; later agentic items there (MCP server guide, crosswalks,
> state-of-security report, Agent Control Standard) are companion documents.
>
> The older "Agentic AI - Threats and Mitigations" guide
> (https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/,
> v1.0, 2025-02-17) is a different document, a threat model with `T`
> numbers. The Top 10 PDF maps each ASI entry back to those T numbers.
>
> Our section IDs (Agentic-A01 through Agentic-A10) are the labels the
> `services-auditor` skill cites and stay stable. They are NOT OWASP IDs.
> Each heading below carries the closest official ASI entry. The OWASP
> document does not map to our labels, so the pairing is our own judgement.
> Where there is no good counterpart it says so.
>
> Skill inventory across all agent platforms in use is the recommendation
> that motivates the empirica scanner. Phase 1 is the dev-machine shape
> of that recommendation.

---

## Agentic-A01 — Tool Misuse

**Official ID:** ASI02 — Tool Misuse and Exploitation (old guide: T2 Tool Misuse)

The agent uses a granted tool in a way the developer didn't intend —
either by combining tools in unexpected sequences, by passing
unexpected arguments, or by being tricked into using a tool against
its proper recipient. Includes prompt-injection-driven tool misuse
(LLM-A01 leaking into agent action). OWASP frames ASI02 as the agent
staying inside its granted privileges while applying a legitimate tool
unsafely (deleting data, over-invoking costly APIs, exfiltrating);
privilege escalation belongs to ASI03 and injected code execution to
ASI05.

**Scanner relevance:** the manifest collector enumerates every tool
each agent has access to. The auditor judges whether the
agent-to-tool grant is excessive given the agent's stated purpose.

**Mitigations:** scoped tool grants per task, parameter validation
on the tool side, allowlists on tool combinations (e.g., "this agent
can read files OR send messages, but not both in the same turn").

## Agentic-A02 — Privilege Compromise

**Official ID:** ASI03 — Identity and Privilege Abuse (old guide: T3 Privilege Compromise)

The agent operates with credentials or filesystem access that exceed
its task. Includes both static over-privilege (agent has root because
nobody narrowed it) and dynamic privilege escalation (agent gets
elevated mid-task and never gets de-elevated). OWASP's ASI03 also
covers delegation chains, inherited or cached credentials and
agent-to-agent trust being exploited to escalate access.

**Scanner relevance:** **direct.** Process inspection surfaces what
each agent can actually reach — its uid/gid, its open file descriptors,
its environment variables (proxied by env-name enumeration).

**Mitigations:** least-privilege execution context, time-boxed
elevation tokens, dropping privilege at task boundaries, separate
service accounts per agent.

## Agentic-A03 — Memory Poisoning

**Official ID:** ASI06 — Memory & Context Poisoning (old guide: T1 Memory Poisoning)

Adversarial content reaches the agent's persistent memory (vector
store, episodic log, finding database) and influences future decisions.
Maps to LLM-A03 (training data poisoning) but at runtime — the agent
doesn't need to be retrained, just to read its own corrupted notes.
ASI06 widens this to any retained or retrieved context: summaries,
embeddings and RAG stores, not only long-term memory.

**Scanner relevance:** processes that write to shared memory stores
(Qdrant servers, embedding indexers) are the contamination surface.
Cross-instance memory sharing increases blast radius.

**Mitigations:** memory provenance (every artifact tagged with the
session/agent that wrote it), per-source confidence weighting,
anomaly detection on belief drift (Empirica's calibration tracking
is the relevant primitive).

## Agentic-A04 — Prompt Injection (Agent-Specific)

**Official ID:** ASI01 — Agent Goal Hijack (old guide: T06 Goal Manipulation). The 2026 list has no separate agent prompt-injection entry; plain prompt injection stays in the LLM Top 10 (LLM01:2025).

Prompt injection that targets agent-orchestration features — the
attacker doesn't want to make the model say something, they want to
make the agent *do* something. Includes goal-injection, tool-call
hijacking, and inter-agent prompt smuggling. Inter-agent message
spoofing is also covered by ASI07 (Insecure Inter-Agent Communication).

**Scanner relevance:** see LLM-A01. Agent-specific surfaces are
multi-agent message bridges, shared scratchpads, and any persistence
layer the agent reads as part of its operating loop.

**Mitigations:** structured-output enforcement (the agent emits typed
events, not free-form text), trust boundaries between agent inputs
(user vs document vs other-agent), capability tokens that authorize
specific actions rather than general permission grants.

## Agentic-A05 — Goal Misalignment

**Official ID:** no exact match. Nearest: ASI01 — Agent Goal Hijack (the PDF ties it to old-guide T07 Misaligned & Deceptive Behaviors) and ASI10 — Rogue Agents (drift without an active attacker).

The agent pursues a literal interpretation of its goal that doesn't
match operator intent — classic Goodharting at the action layer.
Includes spec-gaming (optimising the metric the agent was told to
optimise rather than the underlying objective) and reward hacking.

**Scanner relevance:** indirect — the scanner cannot judge alignment.
But process duration and resource consumption are weak proxies:
agents producing output for hours without converging are spec-gaming
candidates.

**Mitigations:** outcome verification (does the work actually meet the
operator's intent?), human-in-the-loop on goal refinement, autonomy
budgets that force re-anchoring.

## Agentic-A06 — Vulnerable & Outdated Components

**Official ID:** ASI04 — Agentic Supply Chain Vulnerabilities (old guide: T17 Supply Chain Compromise)

The agent depends on plugins, MCP servers, models, or runtime
components with known vulnerabilities. The longer an agent runs, the
more drift accumulates between its dependencies and current patched
versions. OWASP's ASI04 emphasises third-party and runtime-loaded
components (tools, MCP, A2A, registries, update channels) that may be
malicious or tampered with, which is wider than "outdated".
**The orphan-cron failure mode (empirica-outreach incident,
Apr 2026) maps here** — the cron loop kept executing against
deprecated plugin versions long after the project moved on.

**Scanner relevance:** **direct.** This is the scanner's primary
motivation. Process age (`uptime`), version strings in command lines,
manifest version pins, last-modified times on cached binaries —
all surface drift.

**Mitigations:** scheduled re-verification of agent dependency
versions, kill-and-restart cadence, dependency drift monitoring
(scanner's history/diff verbs).

## Agentic-A07 — Identity & Authentication Drift

**Official ID:** ASI03 — Identity and Privilege Abuse (shared with Agentic-A02). No separate credential-drift entry exists in the 2026 list.

The credentials the agent uses become stale, over-scoped, or get
rotated without the agent noticing. Includes leaked credentials
remaining valid (no revocation cascade) and shared credentials losing
attribution (multiple agents use the same service account). OWASP
describes the attribution gap of agents without their own governed
identity under ASI03.

**Scanner relevance:** env-var inventory + manifest credential refs
surface what each agent authenticates as. Listening ports on
auth-bearing services indicate potential token-issuance points.

**Mitigations:** short-lived tokens with mandatory rotation, per-agent
service accounts, credential inventory tracking, deprecation cascades
on rotation.

## Agentic-A08 — Insufficient Sandboxing

**Official ID:** ASI05 — Unexpected Code Execution (RCE) (partial: ASI05 covers host or container compromise and sandbox escape from agent-generated or executed code; it has no general sandboxing-coverage entry)

The agent's execution environment doesn't constrain blast radius —
filesystem writes outside the working directory, network reach beyond
declared endpoints, fork/exec without resource limits, container
escapes.

**Scanner relevance:** **direct.** Processes running outside containers
(no cgroup parent), processes with unrestricted fork potential
(no PR_SET_NO_NEW_PRIVS), open network connections beyond declared
peers — all visible at the OS level.

**Mitigations:** container/sandbox by default for agent execution,
network egress allowlists, filesystem mounts read-only outside
working directory, seccomp/AppArmor profiles.

## Agentic-A09 — Insufficient Observability

**Official ID:** none. The 2026 list has no observability entry; the PDF touches traceability only inside ASI09 (old-guide T8 Repudiation & Untraceability).

The operator can't tell what an agent is doing in real time — what
tools it called, what files it read, what it's about to do next.
**The motivating gap for the scanner: most dev machines have zero
observability over which AI agents are actually running.**

**Scanner relevance:** **direct and primary.** The scanner is the
operator's window into running agents. Process listing, network state,
manifest enumeration, env-var inventory — all are the observability
substrate this section calls for.

**Mitigations:** structured action logging from agent runtimes,
real-time tool-call audit trails, periodic snapshot inventory
(what the scanner produces), notification on novel agents
(`empirica services-audit` does this).

## Agentic-A10 — Excessive Autonomy

**Official ID:** none as a standalone entry. Related: LLM06:2025 Excessive Agency (LLM Top 10), which ASI02 and ASI09 build on; ASI10 — Rogue Agents covers behaviour that drifts outside its authorized scope.

The agent makes consequential decisions without checkpoint — hires,
fires, transfers funds, modifies infrastructure, ships code, all
without human review. Maps closely to LLM-A08 but is decisive at
the action layer rather than the capability layer.

**Scanner relevance:** indirect. The scanner enumerates capability;
audit-log inspection is the right tool for measuring autonomy
exercised. But high-blast-radius capabilities (network reach,
filesystem write, billing-affecting tool grants) can be flagged
preemptively.

**Mitigations:** mandatory human checkpoints on irreversible actions,
autonomy ceilings per session, transaction-level audit (Empirica's
PREFLIGHT/POSTFLIGHT model), reversibility classification on every
tool exposed.

---

## Official entries with no Agentic-A counterpart

These three ASI entries have no section above. They are listed by official
ID only; no Agentic-A label has been assigned (a skill cannot cite one that
does not exist, and adding Agentic-A11+ is a separate decision).

- **ASI07 — Insecure Inter-Agent Communication.** Messages between agents
  lack authentication, integrity or semantic validation, enabling
  interception, spoofing or manipulation. Relevant to multi-agent bridges
  the scanner can enumerate.
- **ASI08 — Cascading Failures.** A single fault (bad decision, corrupted
  tool, poisoned memory) propagates and amplifies across agents and
  workflows; OWASP classifies the origin elsewhere (ASI04/06/07) and uses
  ASI08 for the spread.
- **ASI09 — Human-Agent Trust Exploitation.** Agents' fluency and apparent
  authority lead humans to approve harmful actions without independent
  validation, which can make the agent's role invisible afterwards.

## Official-to-local crosswalk

| Official ID | Official title | Local section |
|---|---|---|
| ASI01 | Agent Goal Hijack | Agentic-A04 (A05 partial) |
| ASI02 | Tool Misuse and Exploitation | Agentic-A01 |
| ASI03 | Identity and Privilege Abuse | Agentic-A02, Agentic-A07 |
| ASI04 | Agentic Supply Chain Vulnerabilities | Agentic-A06 |
| ASI05 | Unexpected Code Execution (RCE) | Agentic-A08 (partial) |
| ASI06 | Memory & Context Poisoning | Agentic-A03 |
| ASI07 | Insecure Inter-Agent Communication | none |
| ASI08 | Cascading Failures | none |
| ASI09 | Human-Agent Trust Exploitation | none |
| ASI10 | Rogue Agents | Agentic-A05, Agentic-A10 (related only) |

Cross-reference note: the `LLM-A01`, `LLM-A03` and `LLM-A08` labels used in
the bodies above are this corpus's own labels from `owasp-llm-top10.md`, not
OWASP's IDs. In the 2026 LLM edition the matching entries are LLM01:2026
Prompt Injection, LLM05:2026 Data and Model Poisoning and LLM03:2026 Excessive
Agency (LLM01:2025, LLM04:2025 and LLM06:2025 in the 2025 edition). The ID map
in `owasp-llm-top10.md` is the one to trust; reconcile there, not here.
