# OWASP Top 10 for Large Language Model Applications (2025)

**Canonical source:** https://genai.owasp.org/llm-top-10/
**Edition:** 2025
**Status:** populated — bodies summarised from canonical guidance.
Refresh via the Phase 3 corpus-refresh loop to pick up errata and edition revisions.

> Phase 2 ``services-auditor`` cites sections here when emitting findings.
> Section IDs (LLM-A01 through LLM-A10) are stable across OWASP revisions,
> so existing citations remain valid even when bodies are refreshed.

---

## LLM-A01 — Prompt Injection

Manipulation of an LLM via crafted inputs that override the system prompt
or steer the model into unintended behaviour. Two main forms: **direct**
(adversarial user input) and **indirect** (malicious content reaching the
model through retrieved documents, tool output, web pages, or shared
files). Indirect is the harder threat — the attacker doesn't need access
to the user's session, just to whatever the agent reads.

**Scanner relevance:** processes that fetch external content (browser
tools, RAG retrievers, MCP servers reading remote URLs) are indirect
injection surfaces. Listening ports that accept user-shaped input
(WhatsApp/Slack bridges, email gateways) are direct injection surfaces.

**Mitigations:** privilege separation (the agent's tools should not
exceed what the user controlling the prompt is authorized to do),
human-in-the-loop for high-impact actions, content provenance tagging
on retrieved input, output filtering against tool-call schemas.

## LLM-A02 — Insecure Output Handling

Treating LLM output as trusted in downstream systems — passing it to
shells, eval'd code, SQL queries, browsers, or other interpreters
without validation. The LLM is an untrusted user from the perspective of
every consumer of its output.

**Scanner relevance:** processes piping LLM output into shells (`bash -c`
wrappers around model output), DB clients reading agent-generated SQL,
template engines rendering agent-generated HTML.

**Mitigations:** treat LLM output as untrusted user input — the same
escaping/validation rules apply. Apply context-aware encoding on the
output side, not just the input side.

## LLM-A03 — Training Data Poisoning

Tampering with pre-training, fine-tuning, or RAG corpora to bias the
model's behaviour or insert backdoors. Includes data sourced from public
internet (which can be seeded with poisoned content) and supply-chain
contamination of curated datasets.

**Scanner relevance:** secondary — most scanner-detected processes
*consume* models, not train them. Becomes primary on machines where
fine-tuning or embedding-pipeline jobs run.

**Mitigations:** dataset provenance tracking, anomaly detection on
training inputs, integrity verification of model weights at load time
(checksum or signed model files).

## LLM-A04 — Model Denial of Service

Attacks that consume disproportionate resources — long-context attacks,
recursive tool loops, runaway agents. Also includes wallet-drain attacks
on metered APIs (the attacker doesn't break the model, they just make
the operator pay).

**Scanner relevance:** **high.** Long-running orphan agent processes
are a passive form of this — they're not malicious but they consume
budget without producing output. The orphan-cron failure mode
(empirica-outreach incident, Apr 2026) is the canonical example.

**Mitigations:** per-process resource limits, token budget caps,
liveness gates that kill agents producing no output, billing alerts
on per-agent spend.

## LLM-A05 — Supply Chain Vulnerabilities

Risks from third-party dependencies — model weights, tokenizers,
inference frameworks, MCP servers, plugins, agent skills. The
provenance chain for an LLM-using application is long and most links
are not signed.

**Scanner relevance:** **direct.** Plugin manifests, MCP server
configurations, and skill registries are exactly the supply-chain
surface. The scanner's `manifests` collector targets this.

**Mitigations:** SBOM for ML models (MLBOM), signed model weights,
allowlisted plugin/MCP registries, dependency audit (pip-audit
analogue for plugins), pinned versions with integrity hashes.

## LLM-A06 — Sensitive Information Disclosure

The model leaking secrets — from training data, retrieved documents,
or user-session memory — to unintended recipients. Includes API keys,
PII, and proprietary content embedded in prompts that surfaces in
later, unrelated outputs.

**Scanner relevance:** processes with broad filesystem read access
(`~/.config`, `~/.ssh`, repo .env files) plus outbound network reach
are the disclosure pipeline. Cross-reference with secret_scan
findings (trufflehog) for credential-grade material in agent context.

**Mitigations:** input/output sanitisation against PII patterns, scoped
read access (least privilege on files the agent can read), credential
isolation (vault retrieval at runtime, not env-var injection).

## LLM-A07 — Insecure Plugin Design

Plugins/tools the LLM can call that have over-broad scopes, weak
authentication, or poor input validation. Maps closely to LLM-A02
(insecure output handling) on the plugin author's side.

**Scanner relevance:** **direct.** Every MCP server, every tool a
plugin defines, every shell command an agent skill embeds. The
scanner's manifest enumeration is the inventory layer; the auditor
judges whether the scopes look reasonable.

**Mitigations:** least-privilege tool scopes, parameterised input
schemas (no free-form shell), mandatory authentication on plugin
endpoints, audit logging on all tool calls.

## LLM-A08 — Excessive Agency

The agent has more capabilities, more permissions, or more autonomy
than the task requires. Manifests as: tools the agent never needs;
permissions kept in case-it-needs-them; autonomous loops that take
non-reversible actions without human review.

**Scanner relevance:** an inventory finding alone can't judge "excessive"
— that's the auditor's job — but the scanner enumerates the surface
the auditor reasons over. Agentic-A10 in the OWASP Agentic list is
the same concept extended.

**Mitigations:** minimum-privilege tool/permission grants, human
checkpoints on irreversible actions, time-boxed autonomy windows,
regular reauthorization.

## LLM-A09 — Overreliance

Operators trusting LLM output without verification — committing
generated code without review, executing recommended actions without
sanity-checking, treating model-generated risk assessments as
authoritative. The failure mode is human, not technical.

**Scanner relevance:** indirect. The scanner's role is to make
verification *possible* by surfacing what's running; it doesn't
prevent the operator from ignoring the report.

**Mitigations:** mandatory review gates on agent-produced code,
calibration tracking (does the operator's confidence match outcomes?),
explicit confidence/uncertainty surfacing in agent output (Empirica's
core thesis).

## LLM-A10 — Model Theft

Unauthorized exfiltration of proprietary model weights, prompts, or
fine-tuning data. Includes both direct theft (file copy) and
extraction attacks (querying the model to reconstruct weights).

**Scanner relevance:** processes with both large local model files
in their working directory AND outbound network reach are the
exfiltration pipeline. Combines with LLM-A06 patterns.

**Mitigations:** weight encryption at rest, query rate limits,
anomaly detection on output entropy (extraction attacks tend to
produce statistically unusual queries).
