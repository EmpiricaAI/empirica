# Google Secure AI Framework (SAIF)

**Canonical source:** https://saif.google/
**Status:** populated — bodies summarised from canonical guidance.
Refresh via the Phase 3 corpus-refresh loop — Google issues
periodic SAIF expansions and detailed control mappings.

> SAIF organizes its principles around six core elements; section IDs
> here mirror that structure (SAIF-1 through SAIF-6) and are stable
> across SAIF revisions.

---

## SAIF-1 — Expand strong security foundations to the AI ecosystem

Existing security disciplines (IAM, network security, data
classification, vulnerability management, incident response) apply to
AI systems unchanged. The first SAIF principle is "don't reinvent" —
the AI system is software, the software-security playbook still
applies.

**Scanner relevance:** the scanner is the AI-specific piece of an
otherwise-conventional inventory + posture-check pipeline. Treating
AI systems as a "different kind of thing" that escapes existing
security review is the failure mode SAIF-1 warns against.

## SAIF-2 — Extend detection and response to bring AI into the threat universe

Detection and incident response coverage must include AI-specific
threats — prompt injection, model extraction, training data
poisoning, agent runaway, supply-chain compromise of models/plugins.
**Direct anchor for the scanner's purpose — extending detection to
AI services running on dev machines** that traditional EDR doesn't
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

Manual review can't keep up with the AI threat surface — defenses
must automate. Includes automated dependency scanning, automated
prompt-injection testing, automated drift detection.

**Scanner relevance:** the biweekly cron loop is the automation
substrate. Manual-only inventory checks would not catch the
orphan-cron-style failure modes the scanner exists to surface.

**Mitigations:** scheduled audits (loop registry), differential
analysis (`scan-diff`), automated notifications on novelty (ntfy
integration), automated post-test verification (Empirica's grounded
calibration pipeline).

## SAIF-4 — Harmonize platform-level controls to ensure consistent security across the organization

The same security baseline should apply to every AI deployment in the
organization — not "production has X, but research has Y, and
individual dev machines have nothing." Includes shared logging
infrastructure, common identity boundaries, centralised policy.

**Scanner relevance:** Empirica's per-project compliance.yaml + the
shared bundled corpus + the canonical loop registry all push toward
a shared baseline. Per-project overrides are explicit, not implicit.

## SAIF-5 — Adapt controls to adjust mitigations and create faster feedback loops for AI deployment

AI-specific risks evolve faster than traditional software risks —
new injection techniques appear, new threat models surface, model
behaviour drifts. Controls must adapt accordingly.

**Scanner relevance:** corpus refresh (Phase 3 monthly cron) is
this principle made concrete — the corpus the auditor cites
adapts to evolving guidance. Calibration tracking (Empirica
core) measures whether the operator's confidence in agent
behaviour stays accurate as agents drift.

**Mitigations:** periodic corpus refresh, continuous calibration
verification, post-test drift detection, rollback paths on
detected regression.

## SAIF-6 — Contextualize AI system risks in surrounding business processes

AI risk is never standalone — an agent that handles customer
communications carries the customer-comms risk; an agent that
modifies infrastructure carries the infra risk. Risk assessment
must consider the business context the agent participates in.

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
