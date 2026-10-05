# Noetic-Praxic Framework: The Autonomous Epistemic Loop

**How AI agents investigate, decide and act inside a measured transaction**

**Related docs:**
- [SENTINEL_ARCHITECTURE.md](./SENTINEL_ARCHITECTURE.md) - how the PreToolUse hook enforces the praxic side
- [PHASE_AWARE_CALIBRATION.md](./PHASE_AWARE_CALIBRATION.md) - how the phase boundary splits calibration, and where the CHECK thresholds come from
- [EPISTEMIC_STATE_COMPLETE_CAPTURE.md](./EPISTEMIC_STATE_COMPLETE_CAPTURE.md) - full state capture design
- [Architecture README](./README.md) - system overview

## The idea

Work in an Empirica practice alternates between two kinds of activity, and the framework keeps them apart so each can be measured on its own terms.

- **Noetic** (Greek *noesis*, understanding): gathering information. Reading, searching, running a read-only command, logging what was learned. Nothing in the world changes, so it is never gated.
- **Praxic** (Greek *praxis*, action): anything that can change state. Editing, writing, running a command that mutates, pushing, installing. Praxic work needs an open, certified transaction.

The line is drawn by effect, not by tool name. The Sentinel classifies each tool call by whether it, as written, can change state; `sed -i` is praxic and `sed -n` is not. Anything unclassified counts as praxic.

The unit that carries the two phases is the **epistemic transaction**:

```
PREFLIGHT ──► noetic work ──► CHECK or certifying claim ──► praxic work ──► POSTFLIGHT ──► post-test
 (baseline)   (investigate,    (what the action rests on)    (act)          (assess)       (grounded
               log findings)                                                               verification)
```

Investigation and action happen inside the same transaction. CHECK is where the practitioner states what the praxic work rests on; it does not end the transaction. POSTFLIGHT closes it, and the post-test then grades the self-assessment against evidence.

## What each step does

| Step | Records | Notes |
|------|---------|-------|
| **PREFLIGHT** | The 13 epistemic vectors, `work_type`, `domain`, `criticality`, task context, optional `claims` and `falsifiers` | `work_type` selects the cascade profile and weights the later calibration. `claims` and `falsifiers` are optional. Claims name the 2-3 load-bearing beliefs and how each was grounded. |
| **Noetic work** | Findings, unknowns, dead-ends, assumptions, decisions, mistakes | Ungated. The Sentinel counts noetic and praxic tool calls separately. |
| **CHECK** | Vectors, a decision (`proceed` or `investigate`), optional `claims` and `falsifiers` | Computed by `check-submit`, below. |
| **Praxic work** | Edits, commits, tests | Gated by the hook: a CHECK, a certifying claim, or a confident PREFLIGHT must exist. |
| **POSTFLIGHT** | Vectors again; each declared claim adjudicated `held`, `refuted` or `untested`; open falsifiers adjudicated | Closes the loop. After it, praxic tools are denied until the next PREFLIGHT. |
| **Post-test** | Grounded vectors from deterministic evidence, split noetic/praxic when a CHECK `proceed` marks a boundary | Divergence from the self-assessment is the calibration signal; see [Phase-Aware Calibration](./PHASE_AWARE_CALIBRATION.md). |

The 13 vectors and what each measures are in [05_EPISTEMIC_VECTORS_EXPLAINED.md](../human/end-users/05_EPISTEMIC_VECTORS_EXPLAINED.md).

## How CHECK decides

`check-submit` computes a gate decision from **uncertainty alone**. `uncertainty` is the practitioner's summary of confidence across the other twelve vectors, so the gate does not also test `know`.

- `proceed` if `uncertainty <= ready_uncertainty_threshold`.
- `proceed` anyway if investigation has plateaued: in the last two CHECK-to-CHECK steps `know` moved by less than 0.05 and `uncertainty` fell by no more than 0.05, and uncertainty is at most 0.45.
- `proceed` anyway on the fifth round or later if uncertainty is at most 0.40.
- Otherwise `investigate`.

If the payload carries no `decision`, the computed one is used. A decision the practitioner supplies stands unless `EMPIRICA_AUTOPILOT_MODE` is on (then the computed one replaces it) or one of the overrides below fires. The response reports both (`metacog.computed_decision`, `metacog.gate_passed`).

The threshold starts from a baseline (0.35 by default; a cascade profile or `calibration.yaml` can set it) and calibration history can only tighten it. The mechanics are in [Phase-Aware Calibration](./PHASE_AWARE_CALIBRATION.md#dynamic-thresholds). The response reports the threshold used, where its base came from, and the reason in `metacog.gate_reason`.

Three things can override the decision after that:

- a critical blindspot predicted by the optional `empirica-prediction` package turns `proceed` into `investigate`;
- the artifact-graph weave gate turns `proceed` into `investigate` when the transaction has logged at least one artifact and fewer than 34% of them carry an edge, until enough edges are woven to reach that floor. The defaults are strictness 0.75 (the enforcing band starts at 0.70) and floor 0.34, tunable per practice in `project.yaml` under `artifact_graph:`;
- a verdict from a registered external evaluator, if any is registered (none is by default).

If CHECK carries `claims`, the response echoes how many are weakly grounded (`retrieved` or `assumed`) while there is still time to investigate. The echo is advisory; nothing blocks on it.

## How the hook treats praxic calls

The hook does not run the CHECK computation again. It asks whether the transaction has been **certified**, and there are three ways to be:

| State of the transaction | Praxic tool call |
|--------------------------|------------------|
| PREFLIGHT's own `know` and `uncertainty` clear the thresholds | Allowed (auto-proceed); no CHECK or claim needed |
| A valid CHECK exists (`proceed` or `investigate`; newer than the PREFLIGHT, not rushed) | Allowed; a shortfall against the thresholds is appended as an advisory, not a refusal |
| No CHECK, but PREFLIGHT declared a claim grounded `read`, or `ran` with a `scope` and a `count` | Allowed; the claim certifies the transaction |
| None of the above | Denied: do the noetic grounding, then declare claims or submit CHECK |

`retrieved` (your own earlier artifacts) and `assumed` never certify. They are testimony or the absence of grounding.

The rest of the hook, including what it allows without a transaction and what it denies after POSTFLIGHT, is in [SENTINEL_ARCHITECTURE.md](./SENTINEL_ARCHITECTURE.md).

### When CHECK is worth submitting

CHECK certifies; it does not unlock. If you have already read the files you will rely on, say so in PREFLIGHT's `claims` and go straight to praxic work. A CHECK submitted moments after its PREFLIGHT, with nothing logged between, certifies nothing; the hook denies it as rushed when it comes under 30 seconds after PREFLIGHT with no finding or unknown logged. Submit a real CHECK when your next actions rest on assumptions you have not yet checked: do the reading first, then CHECK.

## Why keep the phases apart

- **Attribution.** With a clean boundary, calibration can ask different questions of each phase: did the investigation reduce uncertainty as much as claimed, and did the action produce what was predicted? A transaction that only investigated is graded on noetic evidence and is not penalised for having no commits.
- **Cost of late discovery.** Acting while still exploring tends to produce a revision once the better option turns up. CHECK is the point where "what is this resting on" gets said.
- **Bounded exploration.** The `investigate` decision sends the practitioner back to noetic work, and the plateau and fifth-round rules stop that from running indefinitely.

## What the loop does across sessions

Artifacts persist in SQLite and replicate as git notes (see [SYNC_ARCHITECTURE.md](./SYNC_ARCHITECTURE.md)). `project-bootstrap` loads the project's findings, unknowns, dead-ends and goals into the next session, and PREFLIGHT reports feedback from the previous transaction. An unknown logged in one session is already targeted when the next begins; a resolved one stops steering retrieval.

## The statusline's noetic/praxic marker

The statusline shows 🔍 for noetic and 🔨 for praxic. It is derived from the last phase and gate decision, not inferred from vectors: PREFLIGHT, and a CHECK that returned `investigate`, show noetic; a CHECK that returned `proceed` and POSTFLIGHT show praxic; no phase shows noetic. It is a display of where the transaction is, not a second opinion on where the practitioner is.

## Two short examples

**Already grounded.** The practitioner reads the three files a fix touches, then opens PREFLIGHT with `claims` such as `{claim: "retry loop lives in worker.py", grounding: read}` and `{claim: "no other caller of retry()", grounding: ran, scope: "src/", count: 0}`. The `read` claim certifies the transaction, so edits proceed with no CHECK. POSTFLIGHT adjudicates both claims.

**Not yet grounded.** PREFLIGHT reports `uncertainty` 0.55 with no claims. The first `Edit` is denied. The practitioner investigates, logs two findings and an unknown, and submits CHECK with `uncertainty` 0.30. The gate computes `proceed` against the 0.35 threshold, and praxic work begins.

## Epistemic honesty is the mechanism

The loop is self-correcting only if the self-assessment is honest. A practitioner who reports low uncertainty to get through CHECK produces a transaction whose post-test evidence disagrees; the gap shows up in calibration, the Brier reliability term rises, and the thresholds tighten. Inflated confidence is not blocked at the moment it is reported. It is made visible afterwards and raises the cost of the next one.
