# Security corpus

Summary-grade notes on five public AI-security frameworks. The `services-auditor` skill reads them when it cites a framework in a
finding. They are written for that purpose: short, with a "Scanner relevance" and "Mitigations" paragraph per section that are
empirica's own mapping, not the frameworks' text.

## Where these files are

They live beside the skill that reads them, so they ship in the package and `empirica setup-claude-code` deploys them with it:

| Place | What it is |
|---|---|
| `empirica/plugins/claude-code-integration/skills/services-auditor/corpus/` | the source, in the repository and in the wheel |
| `~/.claude/plugins/local/empirica/skills/services-auditor/corpus/` | the deployed copy the skill reads (setup overwrites it) |
| `~/.empirica/security-corpus/` | optional: files you add or override; the skill prefers a file here over the shipped one |

Setup replaces the deployed copy, so put your own additions in `~/.empirica/security-corpus/`, not in the deployed directory.

## Contents

| File | Framework | Edition stated in the file |
|---|---|---|
| `owasp-llm-top10.md` | OWASP Top 10 for LLM Applications | 2026 (published 2026-08-03) |
| `owasp-agentic-top10.md` | OWASP Top 10 for Agentic Applications | 2026 (December 2025) |
| `nist-ai-rmf.md` | NIST AI RMF | 1.0 (January 2023) |
| `mitre-atlas.md` | MITRE ATLAS | content version 2026.09 |
| `google-saif.md` | Google SAIF | introduced 2023; SAIF 2.0 announced 2025-10 |

Every file's header carries `**Canonical source:**`, `**Edition:**` and `**Verified:**` (the date a person last compared it with that
source). Read the `Edition` line, not this table: this table is not kept in step by anything.

## Section IDs: ours and the framework's

The `LLM-A##`, `Agentic-A##`, `SAIF-#` and NIST heading labels are this corpus's own, kept stable because the skill cites them. Each
heading also shows the official identifier where the framework has one. **In anything a person will read, cite the official ID.**
Where our label does not match the official entry of the same number (for example `GOVERN-1.5`), the section says so.

## What is and is not verified

The identifiers and titles were compared with first-party pages on the `Verified` date. Section bodies are summaries written by empirica
and are mostly **not** compared with the published text; each file marks what it did not check. Treat a body as orientation, and open
the canonical source before quoting a framework to someone outside the team.

## Refreshing

Nothing refreshes these files automatically. To re-verify one:

1. open its `Canonical source` and find the current edition;
2. compare identifiers and titles, fix what differs, and say in the file what you could not check;
3. update `Edition` and `Verified`.

`empirica doctor` reads the `Verified` dates and warns when one is older than 180 days or missing.

## Scheduled audits

For unattended monitoring, register the biweekly services-audit cron via the `/services-audit-cron` skill. Its body runs
`empirica services-audit` (scan, diff, notify on novelty) on the canonical `0 6 1,15 * *` cadence (1st and 15th of each month at 06:00
UTC). It audits running services; it does not refresh this corpus.
