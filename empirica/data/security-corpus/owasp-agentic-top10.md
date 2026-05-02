# OWASP Top 10 for Agentic AI (December 2025)

**Canonical source:** https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/
**Edition:** December 2025
**Status:** populated — bodies summarised from canonical guidance.
Refresh via the Phase 3 corpus-refresh loop.

> Skill inventory across all agent platforms in use is the recommendation
> that motivates the empirica scanner — Phase 1 is the dev-machine shape
> of that recommendation. Section IDs (Agentic-A01 through Agentic-A10)
> are stable across revisions.

---

## Agentic-A01 — Tool Misuse

The agent uses a granted tool in a way the developer didn't intend —
either by combining tools in unexpected sequences, by passing
unexpected arguments, or by being tricked into using a tool against
its proper recipient. Includes prompt-injection-driven tool misuse
(LLM-A01 leaking into agent action).

**Scanner relevance:** the manifest collector enumerates every tool
each agent has access to. The auditor judges whether the
agent-to-tool grant is excessive given the agent's stated purpose.

**Mitigations:** scoped tool grants per task, parameter validation
on the tool side, allowlists on tool combinations (e.g., "this agent
can read files OR send messages, but not both in the same turn").

## Agentic-A02 — Privilege Compromise

The agent operates with credentials or filesystem access that exceed
its task. Includes both static over-privilege (agent has root because
nobody narrowed it) and dynamic privilege escalation (agent gets
elevated mid-task and never gets de-elevated).

**Scanner relevance:** **direct.** Process inspection surfaces what
each agent can actually reach — its uid/gid, its open file descriptors,
its environment variables (proxied by env-name enumeration).

**Mitigations:** least-privilege execution context, time-boxed
elevation tokens, dropping privilege at task boundaries, separate
service accounts per agent.

## Agentic-A03 — Memory Poisoning

Adversarial content reaches the agent's persistent memory (vector
store, episodic log, finding database) and influences future decisions.
Maps to LLM-A03 (training data poisoning) but at runtime — the agent
doesn't need to be retrained, just to read its own corrupted notes.

**Scanner relevance:** processes that write to shared memory stores
(Qdrant servers, embedding indexers) are the contamination surface.
Cross-instance memory sharing increases blast radius.

**Mitigations:** memory provenance (every artifact tagged with the
session/agent that wrote it), per-source confidence weighting,
anomaly detection on belief drift (Empirica's calibration tracking
is the relevant primitive).

## Agentic-A04 — Prompt Injection (Agent-Specific)

Prompt injection that targets agent-orchestration features — the
attacker doesn't want to make the model say something, they want to
make the agent *do* something. Includes goal-injection, tool-call
hijacking, and inter-agent prompt smuggling.

**Scanner relevance:** see LLM-A01. Agent-specific surfaces are
multi-agent message bridges, shared scratchpads, and any persistence
layer the agent reads as part of its operating loop.

**Mitigations:** structured-output enforcement (the agent emits typed
events, not free-form text), trust boundaries between agent inputs
(user vs document vs other-agent), capability tokens that authorize
specific actions rather than general permission grants.

## Agentic-A05 — Goal Misalignment

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

The agent depends on plugins, MCP servers, models, or runtime
components with known vulnerabilities. The longer an agent runs, the
more drift accumulates between its dependencies and current patched
versions. **The orphan-cron failure mode (empirica-outreach incident,
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

The credentials the agent uses become stale, over-scoped, or get
rotated without the agent noticing. Includes leaked credentials
remaining valid (no revocation cascade) and shared credentials losing
attribution (multiple agents use the same service account).

**Scanner relevance:** env-var inventory + manifest credential refs
surface what each agent authenticates as. Listening ports on
auth-bearing services indicate potential token-issuance points.

**Mitigations:** short-lived tokens with mandatory rotation, per-agent
service accounts, credential inventory tracking, deprecation cascades
on rotation.

## Agentic-A08 — Insufficient Sandboxing

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
