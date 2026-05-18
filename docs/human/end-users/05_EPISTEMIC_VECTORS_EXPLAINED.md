# The 13 Epistemic Vectors

Empirica measures epistemic state across **13 vectors**, each on `0.0–1.0`. You
report them honestly in PREFLIGHT/CHECK/POSTFLIGHT; the system grounds them
against observations from deterministic services (tests, git, ruff,
codebase model) and surfaces divergence as your calibration signal.

**Not all vectors matter equally for all work.** They split into three
roles:

| Role | Vectors | Why it's grouped this way |
|---|---|---|
| **Foundation** (always load-bearing) | `know`, `do`, `context` | Feasibility — can you do this task at all? |
| **Meta** (quality of self-assessment) | `engagement`, `uncertainty` | Self-referential — are your other vectors trustworthy? |
| **Phase-dependent** (weighted by work_type) | `clarity`, `coherence`, `signal`, `density`, `state`, `change`, `completion`, `impact` | Importance shifts by what you're doing |

**Key principle:** rate what you ACTUALLY know right now. Inflated
vectors produce divergence from grounded observations — that divergence
is exactly the discipline signal the system is built to expose, not a
score to optimize.

---

## Foundation — Feasibility

These three are load-bearing for every transaction.

### `know` — Domain knowledge

How well you understand the domain, concepts, technologies relevant to
the task.

| Range | Meaning |
|---|---|
| 0.8+ | Deep familiarity |
| 0.5–0.7 | Working knowledge |
| 0.3–0.5 | Some exposure |
| <0.3 | Unfamiliar territory |

**Low `know` triggers:** investigation, search, read docs, ask user.

### `do` — Execution capability

Can you execute the required actions? Tools, access, technical
capability.

| Range | Meaning |
|---|---|
| 0.8+ | Have everything needed |
| 0.5–0.7 | Mostly capable, some gaps |
| <0.5 | Missing critical tools / access |

**Low `do` triggers:** check tool availability, ask about permissions.

### `context` — Situational awareness

Understanding of the surrounding state — project, history, constraints,
files involved.

| Range | Meaning |
|---|---|
| 0.8+ | Mapped the relevant surface |
| 0.5–0.7 | Partial picture |
| <0.5 | Don't know what's around |

**Low `context` triggers:** read files, grep, run `project-bootstrap`.

---

## Meta — Self-Assessment Quality

These two govern whether your other vectors are trustworthy at all.

### `engagement`

How actively you're working the problem. Distractions, half-attention,
mismatched scope all pull this down. The system uses it as a sanity
gate — low engagement means even high `know` shouldn't be trusted.

| Range | Meaning |
|---|---|
| 0.8+ | Fully on the task |
| 0.5–0.7 | Working but not fully engaged |
| <0.5 | Disengaged — vectors questionable |

### `uncertainty`

What you DON'T know — explicit. **Higher is more uncertain.** Inverted
direction from the other vectors.

| Range | Meaning |
|---|---|
| <0.2 | Very confident |
| 0.3–0.5 | Some unknowns |
| 0.5–0.7 | Significant unknowns |
| >0.8 | Should be investigating, not acting |

`uncertainty` is **excluded from the calibration score** — it's
derived from the same gaps it would be scored against. But it gates
the CHECK decision: high `uncertainty` means stay noetic.

---

## Phase-Dependent — Weighted by `work_type`

These eight matter differently depending on what you're doing.

### Comprehension cluster: `clarity`, `coherence`, `signal`, `density`

How well you understand what's in front of you.

| Vector | What it measures |
|---|---|
| `clarity` | How clear the path forward is |
| `coherence` | Internal consistency of your understanding |
| `signal` | Quality of information you're working with (vs. noise) |
| `density` | Relevant knowledge per unit of context |

Weighted up for `work_type: docs` and `research`.

### Execution cluster: `state`, `change`, `completion`, `impact`

What's happening to the system.

| Vector | What it measures |
|---|---|
| `state` | Awareness of current system / project state |
| `change` | Amount of change made in this transaction |
| `completion` | Progress toward the current phase goal (noetic OR praxic) |
| `impact` | Significance of the work to the project |

Weighted up for `work_type: code` (execution matters most when shipping).

---

## `work_type` Scaling

The `work_type` you declare in PREFLIGHT changes which evidence sources
the post-test pipeline weights when grounding your vectors:

| `work_type` | What it weights |
|---|---|
| `code` | execution 0.40, foundation 0.30 (shipping matters most) |
| `research` | comprehension 0.35, meta 0.25 |
| `docs` | comprehension 0.40 |
| `debug` | investigation-heavy; lower praxic expectations |
| `infra` | infrastructure/config; code-quality / pytest down-weighted |
| `release` | mechanical pipeline; all evidence excluded (self-assessment stands) |
| `remote-ops` | SSH / server-side work the local Sentinel can't observe (`calibration_status=ungrounded_remote_ops`) |

`docs`, `config`, `data`, `comms`, `design`, `audit` are also valid.

Set it honestly. Mismatched `work_type` produces meaningless divergence.

---

## Reporting Vectors

In PREFLIGHT / CHECK / POSTFLIGHT JSON:

```json
{
  "task_context": "Fix auth bug in token validation",
  "work_type": "code",
  "vectors": {
    "know": 0.55,
    "uncertainty": 0.40,
    "context": 0.65,
    "clarity": 0.70,
    "coherence": 0.65,
    "signal": 0.60,
    "density": 0.50,
    "state": 0.45,
    "change": 0.0,
    "completion": 0.0,
    "impact": 0.60,
    "do": 0.85,
    "engagement": 0.90
  },
  "reasoning": "Read the validator surface but haven't traced the refresh path yet."
}
```

You don't need to send all 13 every time — the foundation + meta five
(`know`, `do`, `context`, `engagement`, `uncertainty`) plus
phase-relevant subset is plenty. The system fills missing entries
with neutral defaults.

---

## CHECK Gate

CHECK is the noetic→praxic transition gate. The Sentinel evaluates
your vectors plus dynamic thresholds from `.empirica/breadcrumbs.yaml`
(calibrated from your prior transactions), not fixed cutoffs.

**Three outcomes:**
- `proceed` — vectors + grounded predictive ability are sufficient
- `investigate` — keep gathering evidence
- `auto-proceed` — vectors high enough that no CHECK ceremony is needed

**When CHECK is needed vs. not:**
- **Not needed:** your predictive ability for the next action is
  grounded in data you've actually pulled this session (files read,
  patterns verified, behaviors observed).
- **Needed:** your predictive ability rests on priors and assumptions
  instead of session-gathered evidence. Do the real grounding work
  FIRST, then CHECK reflects what you actually found.

**Anti-pattern:** PREFLIGHT immediately followed by CHECK with high
vectors and no intervening reads. That inflates beliefs without
grounding — the divergence pipeline catches it.

---

## PREFLIGHT → POSTFLIGHT Delta = Learning

The whole point of measurement is to compute the delta:

```
PREFLIGHT vectors  →  noetic work  →  CHECK  →  praxic work  →  POSTFLIGHT vectors
                                                                          │
                                                                          ▼
                                                          Δ = your learning trajectory
                                                          + grounded observations
                                                          = calibration signal
```

**Track Δ_uncertainty especially.** If you started a transaction with
`uncertainty: 0.7` and ended with `uncertainty: 0.3`, the investigation
worked. If `uncertainty` stayed at 0.7, you either learned nothing or
underestimated the unknowns.

**Grounded observations** (after POSTFLIGHT) come from:
- `git`: commits, files changed, LOC delta
- `code_quality`: ruff violations, radon complexity, pyright errors
- `pytest`: test results (at goal completion)
- `codebase_model`: entities discovered
- `triage`: goals completed, artifacts logged
- `noetic`: investigation thoroughness

Divergence between your beliefs and these observations is **discipline
feedback**, not a verdict on truth. It points at where work discipline
needs attention (more grounding before CHECK, more commits before
POSTFLIGHT, broader artifact logging, etc.).

---

## Common Patterns

### "I don't know this domain"
```
know: 0.30, uncertainty: 0.80
→ Stay noetic. Investigate before any praxic action.
```

### "Vague request"
```
clarity: 0.25, coherence: 0.40
→ Ask user for specifics before opening a transaction.
```

### "Don't know what's there"
```
state: 0.30, context: 0.35
→ project-bootstrap, grep, read.
```

### "Everything blurs together"
```
density: 0.85 (low), signal: 0.30 (low)
→ Break into smaller goals + subtasks.
```

### "Can't predict consequences"
```
impact: 0.35, uncertainty: 0.70
→ Ask user about constraints + acceptable side-effects.
```

---

## Calibration Honesty

The calibration pipeline rewards accurate self-assessment, not high
scores.

- ❌ "I could probably figure this out" → `know: 0.80`
- ✅ "I don't know this domain yet" → `know: 0.30`

- ❌ "I'm confident I'll get it right" → `uncertainty: 0.1`
- ✅ "Multiple unknowns I can't size yet" → `uncertainty: 0.7`

High uncertainty isn't weakness — it's the signal the Sentinel uses to
decide whether to require more investigation. Hiding it produces silent
divergence later.

---

## See Also

- **CLI basics:** [04_QUICKSTART_CLI.md](04_QUICKSTART_CLI.md)
- **Workflow:** [SESSION_GOAL_WORKFLOW.md](SESSION_GOAL_WORKFLOW.md)
- **Sentinel architecture:** [../../architecture/SENTINEL_ARCHITECTURE.md](../../architecture/SENTINEL_ARCHITECTURE.md)
- **Plain-English overview:** [EMPIRICA_EXPLAINED_SIMPLE.md](EMPIRICA_EXPLAINED_SIMPLE.md)
