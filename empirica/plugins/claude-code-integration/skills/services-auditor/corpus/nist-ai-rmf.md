# NIST AI Risk Management Framework (AI RMF 1.0)

**Canonical source:** https://www.nist.gov/itl/ai-risk-management-framework
**Edition:** AI RMF 1.0, NIST AI 100-1, January 2023 (released January 26, 2023); NIST lists it as being revised as part of the White House AI Action Plan, with no revised edition published
**Verified:** 2026-10-15 against https://www.nist.gov/itl/ai-risk-management-framework
**Refresh:** manual; see README.md in this directory.

Subcategory IDs and titles below were checked on 2026-10-15 against the
AI RMF 1.0 PDF (https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf,
Tables 1-4 of Section 5). Bodies are summaries, not reproductions. The
"Scanner relevance" and "Mitigations" paragraphs are empirica's own
mapping, not NIST text. Anything not checked is marked **(unverified)**.

> Section IDs follow the framework's four functions: GOVERN, MAP,
> MEASURE, MANAGE. Where our heading label differs from the official
> subcategory of the same number, the heading carries an "Official ID"
> note; the label is kept because skills cite it. **Cite the official ID
> in anything user-facing.** Whether numbering is stable across a future
> revision is unknown: NIST states the framework is being revised.

---

## GOVERN-1 — Policies & Procedures

Official ID: GOVERN 1 (category: policies, processes, procedures and
practices for mapping, measuring and managing AI risk are in place,
transparent and implemented effectively).

The organisation documents AI risk-management policies, procedures,
and accountability structures. Subcategories cover legal and regulatory
requirements (1.1), integrating trustworthy-AI characteristics into
policy (1.2), risk-tolerance-based scaling of risk-management activity
(1.3), transparent risk-management process and outcomes (1.4), ongoing
monitoring and periodic review (1.5), AI system inventory (1.6) and safe
decommissioning (1.7). Without GOVERN-1, downstream MAP/MEASURE/MANAGE
work has no enforceable home.

**Scanner relevance:** weak — policy is upstream of inventory.
Findings here are typically meta ("this organisation has agents
running with no documented owner").

## GOVERN-1.5 — Third-party AI risk management

**Official ID differs.** In AI RMF 1.0, GOVERN 1.5 is about planned,
ongoing monitoring and periodic review of the risk-management process
and its outcomes, with clear roles and a defined review frequency. It is
NOT the third-party subcategory. Third-party risk is covered by:

- GOVERN 6.1 — policies and procedures for AI risks tied to third-party
  entities, including infringement of their IP or other rights.
- GOVERN 6.2 — contingency processes for failures or incidents in
  third-party data or AI systems deemed high-risk.
- MANAGE 3.1 — risks and benefits from third-party resources are
  regularly monitored, with controls applied and documented.
- MANAGE 3.2 — pre-trained models used for development are monitored as
  part of regular system monitoring and maintenance.

(Category titles: GOVERN 6 — policies and procedures address AI risks
and benefits from third-party software, data and other supply-chain
issues; MANAGE 3 — AI risks and benefits from third-party entities are
managed.)

Risk arising from AI components the organisation didn't build —
hosted operators, foundation models, third-party plugins, MCP
servers, fine-tuned models from external sources. The supply chain
of an AI system extends well beyond what's in version control.
**Anchor for findings about hosted operators / external API consumers
running on developer machines: use GOVERN 6.1/6.2 and MANAGE 3.1/3.2.**

**Scanner relevance:** **direct.** Process inventory + manifest
enumeration surface third-party components. Outbound network state
surfaces hosted-operator dependencies.

**Mitigations (unverified — not checked against the Playbook):** vendor
risk assessment for all external AI providers, contract terms that
require security disclosure, SBOM/MLBOM collection at deployment,
periodic re-verification of provider posture.

## GOVERN-1.6 — AI system inventory (official home of the inventory idea)

Added because our MEASURE-2.7 label below carries inventory content that
NIST places here. GOVERN 1.6: mechanisms are in place to inventory AI
systems and are resourced according to organisational risk priorities.

## GOVERN-2 — Accountability structures

Official ID: GOVERN 2 (category: accountability structures are in place
so appropriate teams and individuals are empowered, responsible and
trained). Subcategories: 2.1 documented, clear roles, responsibilities
and lines of communication; 2.2 AI risk-management training for
personnel and partners; 2.3 executive leadership takes responsibility
for decisions about AI development and deployment risk.

Roles, responsibilities, and oversight for AI risk. Translates
GOVERN-1 policies into named-owner reality — every agent, every
data pipeline, every tool grant has a person responsible for its
ongoing risk profile.

**Scanner relevance:** the auditor can flag agents without a
documented owner ("process running in user X's session, but no
agent registry entry attributes it"). Empirica's instance label
+ project_path linkage is the substrate.

## MAP-1 — Context

Official ID: MAP 1 (category: context is established and understood).
Subcategories 1.1 to 1.6 cover intended purposes and deployment settings
(1.1), interdisciplinary actors (1.2), mission and goals for AI (1.3),
business value (1.4), organisational risk tolerances (1.5) and system
requirements (1.6).

Establishing the context of an AI system — its purpose, intended
users, deployment environment, downstream consumers. Without
context, both risk assessment (MEASURE) and treatment (MANAGE)
are ungrounded.

**Scanner relevance:** the scanner's `read_surface` config + per-pane
project binding give the auditor the context anchor. Without project
context, an agent finding cannot be situated.

## MEASURE-2 — Trustworthy AI characteristics

Official ID: MEASURE 2 (category: AI systems are evaluated for
trustworthy characteristics). Its subcategories are 2.1 to 2.13; the ones
that map to characteristics are 2.5 (valid and reliable), 2.6 (safety),
2.7 (security and resilience), 2.8 (transparency and accountability),
2.9 (explainability and interpretability), 2.10 (privacy) and 2.11
(fairness and bias). Others: 2.1 test sets/metrics documented, 2.2 human
subject evaluations, 2.3 performance measured under deployment-like
conditions, 2.4 production monitoring, 2.12 environmental impact, 2.13
effectiveness of TEVV metrics.

The framework's seven trustworthy-AI characteristics: valid and
reliable, safe, secure and resilient, accountable and transparent,
explainable and interpretable, privacy-enhanced, and fair with harmful
bias managed. Most empirica scanner findings touch one or more of these —
security & resilience and accountability are the strongest matches.

**Scanner relevance:** the auditor uses MEASURE-2 as a taxonomy for
classifying which characteristic each finding affects. A
running-but-unowned agent affects accountability; a credential leak
affects security; a runaway loop affects safety/reliability.

## MEASURE-2.7 — System inventory

**Official ID differs.** In AI RMF 1.0, MEASURE 2.7 is: AI system
security and resilience, as identified in the MAP function, are
evaluated and documented. It is NOT an inventory subcategory. The
inventory subcategory is GOVERN 1.6 (see above). Cite GOVERN 1.6 for
inventory and MEASURE 2.7 for security/resilience evaluation.

Our original text said the organisation maintains an inventory of AI
systems with data sources, dependencies and owners; the official GOVERN
1.6 wording only requires inventory mechanisms resourced to risk
priorities, and does not mention data sources, dependencies or owners.
**Direct anchor for the scanner's purpose — knowing which AI services are
running is a precondition for measurement (empirica's reading, not NIST
text).**

**Scanner relevance:** **direct and primary** (for inventory, via GOVERN
1.6). Every snapshot the scanner produces is exactly the artefact an
inventory mechanism calls for. The history-and-diff verbs make the
inventory time-aware. Findings about security and resilience evaluation
anchor to MEASURE 2.7.

**Mitigations / practices (empirica's own, not NIST):** automated
inventory refresh (the biweekly services-audit cron loop), drift
detection (`scan-diff`), notification on novel agents, retention of
historical inventory for audit.

## MANAGE-1 — Risk treatment

Official ID: MANAGE 1 (category: AI risks based on MAP/MEASURE output are
prioritised, responded to and managed). Subcategories: 1.1 determination
whether the system meets its purposes and whether to proceed, 1.2
prioritisation by impact, likelihood and resources, 1.3 responses to
high-priority risks (options: mitigating, transferring, avoiding,
accepting), 1.4 negative residual risks documented.

Once risks are mapped and measured, the organisation treats them —
mitigation, transfer, acceptance, or avoidance. Examples such as
retraining, fine-tuning corrections, capability restriction,
deprecation and rollback are **(unverified — not found in the sections
checked)**.

**Scanner relevance:** the auditor recommends treatments per finding;
the scanner provides the evidence base. Treatment effectiveness can
be re-measured at the next scan.

## MANAGE-4 — Communication

Official ID: MANAGE 4 (category: risk treatments, including response and
recovery, and communication plans for identified and measured AI risks
are documented and monitored regularly). Subcategories: 4.1
post-deployment monitoring plans (including user input, appeal and
override, decommissioning, incident response, recovery, change
management), 4.2 measurable continual-improvement activities, 4.3
incidents and errors communicated to relevant AI actors, including
affected communities, with tracking/response/recovery processes.

Risk information flows to the people who need it — operators, users,
oversight bodies, downstream consumers. Incident notification is
supported by 4.3. "Periodic risk reporting" and "accessible documentation
of known limitations" in our original text are **(unverified)** as
MANAGE 4 content.

**Scanner relevance:** the ntfy integration on `services-audit` is
exactly this — risk events surface to the operator without manual
polling. Cockpit panels are the persistent dashboard surface.

---

## Companion and newer NIST publications (as of 2026-10-15)

From https://www.nist.gov/itl/ai-risk-management-framework unless noted:

- **NIST AI 600-1** — AI RMF: Generative AI Profile, released July 26,
  2024 (title page of the PDF reads July 2024). A use-case profile for
  generative AI risks. DOI 10.6028/NIST.AI.600-1. Its generative-AI risk
  list and action IDs were not reviewed here **(unverified)**.
- **AI RMF Profile on Trustworthy AI in Critical Infrastructure** —
  concept note released April 7, 2026 (a concept note, not a finished
  profile).
- **NIST IR 8596** — Cybersecurity Framework Profile for Artificial
  Intelligence (Cyber AI Profile), NIST Community Profile. Status on
  https://csrc.nist.gov/pubs/ir/8596/iprd: Initial Preliminary Draft,
  published December 16, 2025, comments due January 30, 2026. It builds
  on CSF 2.0 with three focus areas (securing AI system components,
  AI-enabled cyber defense, countering AI-enabled attacks). Whether a
  later draft or final version exists was **not verified**.
- **AI RMF Playbook** (https://airc.nist.gov/airmf-resources/playbook/)
  — the page says it will be updated after the AI RMF is revised. The
  Playbook's per-subcategory suggested actions were not read here
  **(unverified)**.
- The NIST page also lists an AI RMF Roadmap and Crosswalks; contents not
  reviewed.
