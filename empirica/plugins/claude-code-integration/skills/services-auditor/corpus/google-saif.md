# Google Secure AI Framework (SAIF)

**Canonical source:** https://saif.google/
**Edition:** SAIF, introduced 2023-06-08 with six core elements; extended as "SAIF 2.0" (announced 2025-10-06) with agent guidance. The saif.google site itself states no version number or date.
**Verified:** 2026-10-15 against https://saif.google/ (supporting pages listed under Sources at the end)
**Refresh:** manual; see README.md in this directory.

> SAIF organizes its principles around six core elements, as published in
> the original announcement and summary PDF (see Sources). The SAIF-1
> through SAIF-6 labels below are this corpus's own stable section IDs,
> numbered in the published order; Google does not assign IDs, so each
> heading carries "Official: element N of 6" beside it. The six elements
> are still the published core as of the SAIF 2.0 announcement, which
> adds an agent risk map and agent guidance on top (see "Current
> saif.google structure" below). Note: saif.google itself no longer
> presents the six elements as a numbered list on the pages checked; it
> is organised around the SAIF Map, Risks, Controls and Focus on Agents.

---

## SAIF-1 — Expand strong security foundations to the AI ecosystem
*Official: element 1 of 6 (no assigned ID)*

Reuse the secure-by-default infrastructure protections and expertise
built over roughly two decades, applying them to AI systems,
applications and users, while building organisational expertise to
keep pace with AI and adapting those protections to AI-specific threat
models. The published example is adapting older injection mitigations
(input sanitisation and limiting) to prompt-injection style attacks.

**Scanner relevance:** the scanner is the AI-specific piece of an
otherwise-conventional inventory + posture-check pipeline. Treating
AI systems as a "different kind of thing" that escapes existing
security review is the failure mode SAIF-1 warns against.

## SAIF-2 — Extend detection and response to bring AI into the threat universe
*Official: element 2 of 6 (no assigned ID; published title ends "an organization's threat universe")*

Detect and respond to AI-related incidents in good time by extending
threat intelligence and related capabilities. For organisations this
includes monitoring the inputs and outputs of generative AI systems
for anomalies and using threat intelligence to anticipate attacks,
typically in collaboration with trust and safety, threat intelligence
and counter-abuse teams.
**Direct anchor for the scanner's purpose — extending detection
to AI services running on dev machines** that traditional EDR doesn't
classify as relevant.

**Scanner relevance:** **direct and primary.** Process inventory +
listener inventory + manifest enumeration are exactly the AI-specific
detection layer SAIF-2 calls for. The auditor judges per-finding
severity; the scanner provides the substrate.

**Mitigations / practices:** treat agent inventory as monitorable
state (snapshot regularly), alert on novel agents (`empirica
services-audit` ntfy integration), preserve audit trails of
agent capabilities over time (`scan-history`).

## SAIF-3 — Automate defenses to keep pace with existing and new threats
*Official: element 3 of 6 (no assigned ID)*

Use current AI capabilities to improve the scale and speed of response
to security incidents, on the expectation that adversaries will use AI
to scale their own impact.

**Scanner relevance:** the biweekly cron loop is the automation
substrate. Manual-only inventory checks would not catch the
orphan-cron-style failure modes the scanner exists to surface.

**Mitigations:** scheduled audits (loop registry), differential
analysis (`scan-diff`), automated notifications on novelty (ntfy
integration), automated post-test verification (Empirica's grounded
calibration pipeline).

## SAIF-4 — Harmonize platform-level controls to ensure consistent security across the organization
*Official: element 4 of 6 (no assigned ID; published wording is "platform level controls", unhyphenated)*

Align control frameworks so AI risk mitigation scales across different
platforms and tools, making the best protections available to every AI
application in a scalable, cost-efficient way. Google's own examples
include extending secure-by-default protections to its AI platforms,
building controls into the software development lifecycle, and
general-purpose capabilities that the whole organisation can reuse.

**Scanner relevance:** Empirica's per-project compliance.yaml + the
shared bundled corpus + the canonical loop registry all push toward
a shared baseline. Per-project overrides are explicit, not implicit.

## SAIF-5 — Adapt controls to adjust mitigations and create faster feedback loops for AI deployment
*Official: element 5 of 6 (no assigned ID)*

Keep testing implementations and evolve detection and protections as
the threat environment changes. Published techniques include learning
from incidents and user feedback (updating training data, fine-tuning
models to respond to attacks, embedding security in the model-building
software, e.g. anomaly detection) and regular red team exercises for
AI-powered products.

**Scanner relevance:** corpus refresh (manual; see README.md in this
directory) is this principle made concrete — the corpus the auditor
cites adapts to evolving guidance. Calibration tracking (Empirica
core) measures whether the operator's confidence in agent
behaviour stays accurate as agents drift.

**Mitigations:** periodic corpus refresh, continuous calibration
verification, post-test drift detection, rollback paths on
detected regression.

## SAIF-6 — Contextualize AI system risks in surrounding business processes
*Official: element 6 of 6 (no assigned ID)*

Run end-to-end risk assessments of how the organisation will deploy
AI, including end-to-end business risk (data lineage, validation,
operational behaviour monitoring for certain application types), and
build automated checks that validate AI performance.

**Scanner relevance:** the per-project context (project_path,
domain, criticality declared in PREFLIGHT) is the contextual
anchor. The auditor ties scanner findings to the project's
business context via that linkage. A "running agent" finding has
different severity for an outreach project vs a payment-processing
project.

**Mitigations:** explicit domain/criticality declaration on every
project (Empirica's project.yaml), business-context tagging on
agent registries, severity scoring informed by context rather than
generic.

---

## Current saif.google structure (added 2026-10-15; no SAIF-n label)

The site is organised around the SAIF Map (a visual guide dividing AI
development into Data, Infrastructure, Model and Application component
areas, with a separate agent-focused view), Components, Risks,
Controls, an AI Development Primer, a Risk Self Assessment, a Focus on
Agents section and an Agent Risk Self Assessment. The footer notes the
content is informational and not a statement of Google's current
technical implementations.

**Risks (15, as titled on the Risks page):** Data Poisoning;
Unauthorized Training Data; Model Source Tampering; Excessive Data
Handling; Model Exfiltration; Model Deployment Tampering; Denial of ML
Service; Model Reverse Engineering; Insecure Integrated Component;
Prompt Injection; Model Evasion; Sensitive Data Disclosure; Inferred
Sensitive Data; Insecure Model Output; Rogue Actions.

**Controls (grouped, as titled on the Controls page):**
- Data: Privacy Enhancing Technologies; Training Data Management;
  Training Data Sanitization; User Data Management
- Infrastructure: Model and Data Inventory Management; Model and Data
  Access Controls; Model and Data Integrity Management; Secure-by-Default
  ML Tooling
- Model: Input Validation and Sanitization; Output Validation and
  Sanitization; Adversarial Training and Testing
- Application: Application Access Management; User Transparency and
  Controls; Agent User Control; Agent Permissions; Agent Observability
  (flagged "New" on the page)
- Assurance: Red Teaming; Vulnerability Management; Threat Detection;
  Incident Response Management
- Governance: User Policies and Education; Internal Policies and
  Education; Product Governance; Risk Governance

**Agent guidance (Focus on Agents page):** breaks an agentic system
into four components — Application & Perception, Reasoning Core,
Orchestration (agent memory, tools, content/RAG, auxiliary models) and
Response Rendering — and highlights Sensitive Data Disclosure and
Rogue Actions as agent-relevant risks, calling for layered defences at
every architectural layer.

**SAIF 2.0 (announced 2025-10-06 on blog.google):** adds an agent risk
map, security capabilities for Google's agents built on three
principles (agents need well-defined human controllers; their powers
must be carefully limited; their actions and planning must be
observable), and a donation of the risk map data to the Coalition for
Secure AI Risk Map initiative.

**Scanner relevance:** agent inventory maps to Agent Observability and
Agent Permissions; the scanner's registry of agents and their
capabilities is evidence for the "observable" and "limited powers"
principles. Rogue Actions is the risk the scanner's agent findings
most directly inform.

## Unverified in this refresh

- The numbering of Risks/Controls and the pairing of the "three
  principles" with specific site pages were not confirmed on saif.google
  itself (principles taken from the blog post only).
- Mappings of the six elements to the Risks/Controls lists are not
  published in the pages fetched; none are asserted here.
- The Agent Risk Self Assessment and any separate SAIF 2.0 paper were not
  fetched.
- Scanner-relevance and Mitigations text is this project's own and is not
  a claim about the source.

## Sources (fetched 2026-10-15)

- https://saif.google/ , https://saif.google/why-saif ,
  https://saif.google/secure-ai-framework ,
  https://saif.google/secure-ai-framework/risks ,
  https://saif.google/secure-ai-framework/controls ,
  https://saif.google/secure-ai-framework/saif-map ,
  https://saif.google/focus-on-agents , https://saif.google/resources
- https://blog.google/innovation-and-ai/technology/safety-security/introducing-googles-secure-ai-framework/ (2023-06-08; six elements)
- https://services.google.com/fh/files/blogs/google_secure_ai_framework_summary.pdf (six elements, summary PDF)
- https://blog.google/innovation-and-ai/technology/safety-security/ai-security-frontier-strategy-tools/ (2025-10-06; SAIF 2.0)
