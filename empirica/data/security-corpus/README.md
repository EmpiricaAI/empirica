# Bundled security corpus

This directory ships with the Empirica package and is copied to
`~/.empirica/security-corpus/` on `setup-claude-code` so users can edit
the canon for their own context. The Phase 2 services-auditor agent
cites these files when emitting findings; Phase 1 only ships them
(no consumer yet).

## Contents

| File | Source | Purpose |
|---|---|---|
| `owasp-llm-top10.md` | OWASP 2025 | LLM-specific vulnerability patterns |
| `owasp-agentic-top10.md` | OWASP Dec 2025 | Autonomous agent risk patterns |
| `nist-ai-rmf.md` | NIST AI RMF 1.0 | Governance + risk frame |
| `mitre-atlas.md` | MITRE ATLAS | Adversarial threat patterns against AI systems |
| `google-saif.md` | Google SAIF | Secure AI framework principles |

## Refresh policy

The bundled corpus ships with **populated section bodies** — summary-
grade content sufficient for the auditor's citation needs. Section IDs
match the canonical frameworks exactly so citations remain valid across
refreshes. Updates land via:

- the **Phase 3 corpus-refresh loop** (monthly cron) which pulls the
  latest published versions of each framework, OR
- a **manual commit** by anyone with the canonical text on hand.

Each file declares its own freshness in the front-matter `Status:` line.
Operators can drop in their own organisation-specific corpus extensions
under `~/.empirica/security-corpus/`; the auditor sees both bundled and
local files when running.
