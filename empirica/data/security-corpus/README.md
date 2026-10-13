# Security corpus

Summary-grade notes on five public AI-security frameworks. The
`services-auditor` skill (`plugins/claude-code-integration/skills/services-auditor/SKILL.md`)
reads them when it cites a framework in a finding: from this directory
in a source checkout, or from `~/.empirica/security-corpus/` if you
create that directory.

## Where these files are, and are not

Checked 2026-10-13 against 1.14.9:

- **They are in the git repository only.** `pyproject.toml`'s
  `package-data` has no `data/**` entry, and the built 1.14.9 wheel
  contains no `security-corpus` file, so a `pip install empirica` does
  not include them.
- **Nothing copies them.** `setup-claude-code` does not create
  `~/.empirica/security-corpus/`; no Python code references this
  directory. If you want local copies or your own additions, put them
  there by hand.
- **The consumer is a skill, not an agent.** The auditor is the
  `services-auditor` skill. No code parses these files: the skill tells
  the AI to read them.

Whether the corpus should ship in the package and be copied at setup is
an open question, not a documented behaviour.

## Contents

| File | Source | Edition stated in the file |
|---|---|---|
| `owasp-llm-top10.md` | OWASP LLM Top 10 | 2025 |
| `owasp-agentic-top10.md` | OWASP agentic-AI Top 10 | December 2025 |
| `nist-ai-rmf.md` | NIST AI RMF | 1.0 (January 2023) |
| `mitre-atlas.md` | MITRE ATLAS | none stated |
| `google-saif.md` | Google SAIF | none stated |

Each file names its source URL near the top. The summaries have not been
compared with the published texts in this pass; treat the section IDs as
the files' own labels and check the source before quoting one externally.

## Scheduled audits

For unattended monitoring, register the biweekly services-audit cron
via the `/services-audit-cron` skill. Its body runs
`empirica services-audit` (scan + diff + notify on novelty) on the
canonical `0 6 1,15 * *` cadence (1st and 15th of each month at 06:00
UTC). See the skill template for cadence options and heartbeat wiring.

## Refresh policy

Updates are manual: a commit by anyone with the canonical text on hand.
No automated corpus-refresh loop exists in this repository (an earlier
version of this file described a monthly one; no code implements it).
Each file's `Status:` line says only that its section bodies are
populated.
