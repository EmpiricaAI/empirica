# Epistemic Health Quick Reference

**One-pager:** what to look at when an AI session opens, what the bootstrap
shows, and how to act on it. For the full vector reference see
[../end-users/05_EPISTEMIC_VECTORS_EXPLAINED.md](../end-users/05_EPISTEMIC_VECTORS_EXPLAINED.md).

---

## The 13 Vectors in 30 Seconds

| Role | Vectors |
|---|---|
| **Foundation** (feasibility) | `know`, `do`, `context` |
| **Meta** (self-assessment quality) | `engagement`, `uncertainty` |
| **Phase-dependent** (weighted by `work_type`) | `clarity`, `coherence`, `signal`, `density`, `state`, `change`, `completion`, `impact` |

All on `0.0–1.0`. `uncertainty` is inverted direction (higher = more uncertain).

---

## What `project-bootstrap` Returns

```
project-bootstrap output
├─ Project context (id, repo, db location, ai_id)
├─ Project summary (session count, last activity, next focus)
├─ Flow state (productivity gauge from active triggers)
├─ Health score (epistemic quality composite)
├─ Recent findings (last 10, with impact + transaction_id)
├─ Unresolved unknowns (open questions with age)
├─ Dead ends (failed approaches + reasons)
├─ Recent mistakes (errors + prevention strategies)
├─ Active goals (in_progress + planned)
└─ Reference docs (ref-doc-add tagged)
```

Typical size: ~800–2000 tokens depending on artifact density. Run on
every session start when context is fresh — it's the cheapest way to
load relevant project state.

---

## Reading the Bootstrap

### Foundation Check

```
know × do × context
  → 0.7+ × 0.7+ × 0.7+   = ready to work
  → mixed                = read recent findings first
  → < 0.5 anywhere       = investigation needed before action
```

### Uncertainty Driving

```
uncertainty < 0.35  → proceed; CHECK ceremony optional
uncertainty 0.35–0.6 → moderate; CHECK with grounded reasoning
uncertainty 0.6–0.8 → high; full noetic pass before any praxic
uncertainty > 0.8   → don't act; investigate or ask user
```

### Artifact Signals

| Signal | Meaning |
|---|---|
| **Critical issues present** | Fix before any other work |
| **Recent findings (< 1h)** | Read first — likely answers current question |
| **Dead ends in your planned approach** | Read WHY; the lesson applies to similar approaches |
| **Stale unknowns (> N days)** | Either resolve or close as `wont_fix` |
| **Repeated mistakes** | Conceptual gap — read prevention notes |

---

## Decision Framework

### Issue Severity

| Tier | Examples | Action |
|---|---|---|
| 🔴 Critical | System down, commands broken, data corruption | Fix immediately before any new work |
| 🟠 High | Missing features, bugs in active paths | Fix before starting new workstream |
| 🟡 Medium | Performance issues, TODOs in active code | Plan for this sprint/cycle |
| 🔵 Low | Cosmetic, nice-to-haves | Backlog |

### Work-Type Routing

Set `work_type` in PREFLIGHT to scale evidence weights properly:

- `code` — execution weighted, full pytest at goal completion
- `research` — comprehension + meta weighted, artifacts/noetic up
- `docs` — comprehension weighted
- `debug` — investigation-heavy, lower praxic expectations
- `infra` — code_quality/pytest down-weighted
- `release` — mechanical pipeline, all evidence excluded
- `remote-ops` — local Sentinel can't observe (SSH, customer machines); `calibration_status=ungrounded_remote_ops`

---

## Common Scenarios

### Starting fresh

```
Bootstrap shows: low uncertainty, recent findings, clean git
→ Load minimal context, read 1–2 recent findings, proceed
```

### Joining mid-project

```
Bootstrap shows: many open unknowns, mixed findings, uncommitted changes
→ Full project-bootstrap output, read findings + dead ends, ask about
  uncommitted state before acting
```

### Critical issues present

```
Bootstrap shows: critical issues, low foundation, repeated dead ends
→ DO NOT start new work. Fix criticals first. Investigation session.
  Return to feature work only when foundation recovers.
```

### High-uncertainty domain

```
Bootstrap shows: uncertainty > 0.8, unfamiliar terms in findings
→ project-search --task "<topic>" --global    # check other projects
→ docs-explain --topic "<concept>"            # query empirica docs
→ Only then PREFLIGHT
```

---

## Sentinel Calibration

The Sentinel decides whether to require CHECK ceremony or auto-proceed
based on thresholds calibrated from your prior transactions (summary in
`.breadcrumbs.yaml` at the repo root). The baseline is `know >= 0.70` and
`uncertainty <= 0.35`; calibration only tightens above it (by at most 0.05).

- Good historical calibration → gates stay at the baseline, numbers trusted as-is
- Drift between belief and grounded observation → tighter gates

Gaming vectors costs you: POST-FLIGHT grounded verification catches the
divergence and the gate tightens.

---

## Handoff Checklist

Before ending a session:

- [ ] All work committed
- [ ] POSTFLIGHT submitted (vectors recorded)
- [ ] Critical issues fixed OR documented as goals
- [ ] Findings reflect actual work done
- [ ] Open unknowns have context (someone else needs to act on them)
- [ ] Dead ends include "why it failed"
- [ ] Mistakes include prevention
- [ ] If switching AIs: `empirica handoff-create` with next-session-context

---

## Command Reference

```bash
# Bootstrap
empirica project-bootstrap                            # current project
empirica project-bootstrap --output json              # machine-readable

# Lifecycle
empirica session-create --ai-id $(basename $PWD)
empirica preflight-submit -                           # JSON via stdin
empirica check-submit -                               # gate
empirica postflight-submit -                          # close + grounded verification

# Inspection
empirica calibration-report                           # grounded calibration
empirica calibration-report --learning-trajectory     # PREFLIGHT→POSTFLIGHT deltas
empirica commit-context HEAD                          # walk artifact graph
empirica project-search --task "..."                  # this project
empirica project-search --task "..." --global         # cross-project

# Self-serve docs
empirica docs-explain --topic "vectors"
empirica docs-explain --question "How do I run CHECK?"
empirica docs-assess --summary-only
```

---

## Example PREFLIGHT / POSTFLIGHT

**PREFLIGHT** (start of work):

```json
{
  "task_context": "Refactor auth module to use RS256",
  "work_type": "code",
  "vectors": {
    "know": 0.60, "uncertainty": 0.65,
    "context": 0.50, "engagement": 0.85,
    "do": 0.75, "completion": 0.0
  },
  "reasoning": "Familiar with JWT, haven't read this codebase's auth surface yet."
}
```

**POSTFLIGHT** (end of work):

```json
{
  "vectors": {
    "know": 0.85, "uncertainty": 0.20,
    "context": 0.85, "engagement": 0.80,
    "do": 0.85, "completion": 1.0,
    "change": 0.45, "impact": 0.6
  },
  "reasoning": "RS256 refactor shipped, audience check enforced on both paths, integration tests added."
}
```

Δuncertainty = -0.45 → investigation was effective.

---

## See Also

- **Vectors deep-dive:** [../end-users/05_EPISTEMIC_VECTORS_EXPLAINED.md](../end-users/05_EPISTEMIC_VECTORS_EXPLAINED.md)
- **Sentinel architecture:** [../../architecture/SENTINEL_ARCHITECTURE.md](../../architecture/SENTINEL_ARCHITECTURE.md)
- **CASCADE workflow:** [../end-users/EMPIRICA_NATURAL_LANGUAGE_GUIDE.md](../end-users/EMPIRICA_NATURAL_LANGUAGE_GUIDE.md)
- **AI self-management:** [AI_SELF_MANAGEMENT.md](AI_SELF_MANAGEMENT.md)
