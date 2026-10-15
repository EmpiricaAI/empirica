# OWASP Top 10 for Large Language Model Applications (2026)

**Canonical source:** https://genai.owasp.org/resource/owasp-genai-llm-top-10-2026/
**Edition:** OWASP Top 10 for LLM Applications 2026 (published 2026-08-03)
**Verified:** 2026-10-15 against https://genai.owasp.org/resource/owasp-genai-llm-top-10-2026/
**Refresh:** manual; see README.md in this directory.

> Phase 2 ``services-auditor`` cites sections here when emitting findings.
> The IDs LLM-A01 through LLM-A10 are this corpus's own labels, not OWASP's.
> They are kept fixed so existing citations stay valid. Each section lists
> the official ID beside our label so a reader can map. LLM-A11 and LLM-A12
> are new labels for 2025/2026 entries that had no counterpart in the older
> list this file was first written from.

**Provenance notes (2026-10-15)**
- The earlier revision of this file was titled "2025" but its ten titles are
  the 2023/24 list (Insecure Output Handling, Training Data Poisoning, Model
  Denial of Service, Insecure Plugin Design, Overreliance, Model Theft). The
  2025 edition (LLM01:2025..LLM10:2025) replaced that list, and the 2026
  edition (published 2026-08-03) replaced the 2025 one.
- Official 2026 titles for LLM01-LLM06 were confirmed on a first-party OWASP
  page (the Q3 2026 exploit round-up). The 2025 list was confirmed on the
  first-party list page. The 2026 titles for LLM07-LLM10 and the move/rename
  history were read only from a secondary source (Imperva blog); the OWASP
  2026 resource page itself did not list entries in the text I could fetch.
  Treat LLM07:2026-LLM10:2026 as secondary-sourced.
- https://genai.owasp.org/llm-top-10/ still showed the 2025 list when
  fetched on 2026-10-15, so it lags the 2026 edition.
- Section bodies below are retained from the prior revision and are
  **unverified** against the published text (the fetched pages gave titles
  and IDs, not bodies). They are summary-grade notes.

## Official ID map

| Our label | Official 2026 ID and title | 2025 ID and title | Notes |
|---|---|---|---|
| LLM-A01 | LLM01:2026 Prompt Injection | LLM01:2025 Prompt Injection | same title |
| LLM-A02 | LLM10:2026 Improper Output Handling | LLM05:2025 Improper Output Handling | our title is the 2023/24 name; entry renamed |
| LLM-A03 | LLM05:2026 Data and Model Poisoning | LLM04:2025 Data and Model Poisoning | our title is the 2023/24 name; entry renamed and broadened |
| LLM-A04 | LLM06:2026 Unbounded Consumption | LLM10:2025 Unbounded Consumption | our title is the 2023/24 name; entry renamed |
| LLM-A05 | LLM04:2026 Supply Chain | LLM03:2025 Supply Chain | our title is the 2023/24 name |
| LLM-A06 | LLM02:2026 Sensitive Information Disclosure | LLM02:2025 Sensitive Information Disclosure | same title |
| LLM-A07 | none (retired) | none (retired) | no 2025 or 2026 entry with this title |
| LLM-A08 | LLM03:2026 Excessive Agency | LLM06:2025 Excessive Agency | same title; moved up |
| LLM-A09 | LLM07:2026 Misinformation (approximate) | LLM09:2025 Misinformation (approximate) | 2023/24 Overreliance; successor mapping is approximate |
| LLM-A10 | none (retired) | none (retired) | no 2025 or 2026 entry with this title |
| LLM-A11 | LLM08:2026 Hidden Context Exposure | LLM07:2025 System Prompt Leakage | new label |
| LLM-A12 | LLM09:2026 Vector and Embedding Weaknesses | LLM08:2025 Vector and Embedding Weaknesses | new label |

---

## LLM-A01 — Prompt Injection  (official: LLM01:2026)

*Body unverified; retained from prior revision.*

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

## LLM-A02 — Insecure Output Handling  (official: LLM10:2026 Improper Output Handling; 2025: LLM05:2025)

*Body unverified; retained from prior revision. Our title is the older
name; OWASP now calls this Improper Output Handling.*

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

## LLM-A03 — Training Data Poisoning  (official: LLM05:2026 Data and Model Poisoning; 2025: LLM04:2025)

*Body unverified; retained from prior revision. Our title is the older
name; OWASP now calls this Data and Model Poisoning.*

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

## LLM-A04 — Model Denial of Service  (official: LLM06:2026 Unbounded Consumption; 2025: LLM10:2025)

*Body unverified; retained from prior revision. Our title is the older
name; OWASP now calls this Unbounded Consumption.*

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

## LLM-A05 — Supply Chain Vulnerabilities  (official: LLM04:2026 Supply Chain; 2025: LLM03:2025)

*Body unverified; retained from prior revision. Our title is the older
name; the current official title is Supply Chain.*

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

## LLM-A06 — Sensitive Information Disclosure  (official: LLM02:2026; 2025: LLM02:2025)

*Body unverified; retained from prior revision.*

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

## LLM-A07 — Insecure Plugin Design  (official: none; retired from the 2025 and 2026 lists)

*Body unverified; retained from prior revision. This is a 2023/24 entry
with no entry of the same title in the 2025 or 2026 lists. Where its
content went is not verified here; the practical risk (over-broad tool
scopes) is still worth auditing and overlaps LLM-A08 and LLM-A05.*

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

## LLM-A08 — Excessive Agency  (official: LLM03:2026; 2025: LLM06:2025)

*Body unverified; retained from prior revision, except the cross-reference
below, which was corrected.*

The agent has more capabilities, more permissions, or more autonomy
than the task requires. Manifests as: tools the agent never needs;
permissions kept in case-it-needs-them; autonomous loops that take
non-reversible actions without human review.

**Scanner relevance:** an inventory finding alone can't judge "excessive"
— that's the auditor's job — but the scanner enumerates the surface
the auditor reasons over. For the agent-specific extension of this
concept see the OWASP Top 10 for Agentic Applications 2026 (IDs
ASI01-ASI10); see owasp-agentic-top10.md in this directory for which
entry applies. (The earlier "Agentic-A10" label does not match OWASP's
agentic IDs.)

**Mitigations:** minimum-privilege tool/permission grants, human
checkpoints on irreversible actions, time-boxed autonomy windows,
regular reauthorization.

## LLM-A09 — Overreliance  (official: nearest is LLM07:2026 Misinformation; 2025: LLM09:2025)

*Body unverified; retained from prior revision. Overreliance is a 2023/24
entry; the 2025 and 2026 lists have Misinformation in its slot, and the
mapping is approximate (verified only by the 2025 page reusing the old
slug, not by a stated merge).*

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

## LLM-A10 — Model Theft  (official: none; retired from the 2025 and 2026 lists)

*Body unverified; retained from prior revision. This is a 2023/24 entry
with no entry of the same title in the 2025 or 2026 lists. Keep as a
local concern; do not cite it as a current OWASP category.*

Unauthorized exfiltration of proprietary model weights, prompts, or
fine-tuning data. Includes both direct theft (file copy) and
extraction attacks (querying the model to reconstruct weights).

**Scanner relevance:** processes with both large local model files
in their working directory AND outbound network reach are the
exfiltration pipeline. Combines with LLM-A06 patterns.

**Mitigations:** weight encryption at rest, query rate limits,
anomaly detection on output entropy (extraction attacks tend to
produce statistically unusual queries).

## LLM-A11 — Hidden Context Exposure  (official: LLM08:2026; 2025: LLM07:2025 System Prompt Leakage)

*New label. Title and rename history from a secondary source (Imperva);
the body below is my summary and is unverified against OWASP's text.*

The 2025 entry covered leakage of system prompts. The 2026 edition renamed
it and, per the secondary source, widened it to other hidden inputs such as
retrieved documents, tool definitions and earlier turns held in memory.
The risk is that instructions or context never meant for the user, and
anything sensitive embedded in them, can be extracted.

**Scanner relevance:** agent configs, skill files and MCP tool definitions
that embed credentials, internal URLs or policy logic are the exposure
surface. Overlaps LLM-A06.

**Mitigations (suggested, not from OWASP text):** keep secrets out of
prompts and tool definitions, and enforce authorisation outside the model.

## LLM-A12 — Vector and Embedding Weaknesses  (official: LLM09:2026; 2025: LLM08:2025)

*New label. Title confirmed on the first-party 2025 list page; the 2026
position (LLM09) is from a secondary source (Imperva). The body is my
summary and is unverified against OWASP's text.*

Security risks in systems that generate, store or retrieve vector
embeddings, as used in retrieval-augmented generation.

**Scanner relevance:** vector stores and embedding pipelines (Qdrant,
Chroma, pgvector and similar) running alongside agents are the surface.
Overlaps LLM-A03 for poisoned retrieval corpora.

**Mitigations (suggested, not from OWASP text):** access control per
tenant or collection, and provenance checks on ingested documents.
