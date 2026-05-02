# NIST AI Risk Management Framework (AI RMF 1.0)

**Canonical source:** https://www.nist.gov/itl/ai-risk-management-framework
**Edition:** AI RMF 1.0 (Jan 2023)
**Status:** populated — bodies summarised from canonical guidance.
Refresh via the Phase 3 corpus-refresh loop. NIST issues periodic
updates and supplemental publications — track for incremental changes.

> Section IDs follow the framework's four functions: GOVERN, MAP,
> MEASURE, MANAGE. Subcategory numbering (e.g. GOVERN-1.5) matches
> NIST's own subcategory IDs and is stable across the framework's
> minor revisions.

---

## GOVERN-1 — Policies & Procedures

The organisation documents AI risk-management policies, procedures,
and accountability structures. Covers who is responsible for what
across the AI lifecycle (procurement, development, deployment,
operation, retirement). Without GOVERN-1, downstream MAP/MEASURE/MANAGE
work has no enforceable home.

**Scanner relevance:** weak — policy is upstream of inventory.
Findings here are typically meta ("this organisation has agents
running with no documented owner").

## GOVERN-1.5 — Third-party AI risk management

Risk arising from AI components the organisation didn't build —
hosted operators, foundation models, third-party plugins, MCP
servers, fine-tuned models from external sources. The supply chain
of an AI system extends well beyond what's in version control.
**Anchor for findings about hosted operators / external API consumers
running on developer machines.**

**Scanner relevance:** **direct.** Process inventory + manifest
enumeration surface third-party components. Outbound network state
surfaces hosted-operator dependencies.

**Mitigations:** vendor risk assessment for all external AI
providers, contract terms that require security disclosure,
SBOM/MLBOM collection at deployment, periodic re-verification of
provider posture.

## GOVERN-2 — Accountability structures

Roles, responsibilities, and oversight for AI risk. Translates
GOVERN-1 policies into named-owner reality — every agent, every
data pipeline, every tool grant has a person responsible for its
ongoing risk profile.

**Scanner relevance:** the auditor can flag agents without a
documented owner ("process running in user X's session, but no
agent registry entry attributes it"). Empirica's instance label
+ project_path linkage is the substrate.

## MAP-1 — Context

Establishing the context of an AI system — its purpose, intended
users, deployment environment, downstream consumers. Without
context, both risk assessment (MEASURE) and treatment (MANAGE)
are ungrounded.

**Scanner relevance:** the scanner's `read_surface` config + per-pane
project binding give the auditor the context anchor. Without project
context, an agent finding cannot be situated.

## MEASURE-2 — Trustworthy AI characteristics

The framework's seven trustworthy-AI characteristics: validity &
reliability, safety, security & resilience, accountability &
transparency, explainability & interpretability, privacy, fairness
& management of bias. Most empirica scanner findings touch one or
more of these — security & resilience and accountability are the
strongest matches.

**Scanner relevance:** the auditor uses MEASURE-2 as a taxonomy for
classifying which characteristic each finding affects. A
running-but-unowned agent affects accountability; a credential leak
affects security; a runaway loop affects safety/reliability.

## MEASURE-2.7 — System inventory

The organisation maintains an inventory of AI systems in use,
including their data sources, dependencies, and owners. **Direct
anchor for the scanner's purpose — knowing which AI services are
running is a precondition for measurement.** Without inventory,
the rest of MEASURE has nothing to measure against.

**Scanner relevance:** **direct and primary.** Every snapshot the
scanner produces is exactly the artefact MEASURE-2.7 calls for.
The history-and-diff verbs make the inventory time-aware.

**Mitigations / practices:** automated inventory refresh (the
biweekly services-audit cron loop), drift detection
(`scan-diff`), notification on novel agents, retention of
historical inventory for audit.

## MANAGE-1 — Risk treatment

Once risks are mapped and measured, the organisation treats them —
mitigation, transfer, acceptance, or avoidance. AI-specific
treatments include retraining, fine-tuning corrections, capability
restriction, deprecation, and rollback.

**Scanner relevance:** the auditor recommends treatments per finding;
the scanner provides the evidence base. Treatment effectiveness can
be re-measured at the next scan.

## MANAGE-4 — Communication

Risk information flows to the people who need it — operators, users,
oversight bodies, downstream consumers. Includes incident
notification, periodic risk reporting, and accessible documentation
of known limitations.

**Scanner relevance:** the ntfy integration on `services-audit` is
exactly this — risk events surface to the operator without manual
polling. Cockpit panels are the persistent dashboard surface.
