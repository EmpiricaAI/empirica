# Upgrading to Empirica 1.9

This guide covers the 1.7 → 1.9 jump. If you're on 1.8.x already, most of
this has been incremental — read the "What changed since 1.8" section
near the end. If you're on 1.7 or earlier, the highlights below are the
load-bearing additions.

---

## Quick Upgrade

```bash
pip install --upgrade empirica
empirica setup-claude-code --force   # Refresh plugin hooks
```

The `--force` flag refreshes hooks and skill manifests. Safe to run any
time — it's idempotent and clears stale entries.

---

## Highlights since 1.7

### Goal-driven post-tests (1.9, brand new)

Goals can now declare measurable success criteria that auto-evaluate at
POSTFLIGHT. Three validation methods:

```bash
# Task completion ratio
empirica goals-create \
  --objective "Refactor auth module" \
  --success-criteria '["completion:task_ratio@>=0.9"]'

# Quality-gate against a named EvidenceItem metric
empirica goals-create \
  --objective "Preserve voice in published outreach articles" \
  --success-criteria '["quality_gate:prose_stylometry_composite_drift@<=0.25"]'

# Vector-threshold against POSTFLIGHT vectors (deferred)
```

POSTFLIGHT response now includes a `goal_criteria` block per active goal.
Self-diagnosing — if a quality_gate metric isn't in the evidence bundle,
the response says exactly which evaluator was registered and why it
didn't apply. (Design rationale lives in the local-only spec draft
*`docs/specs/PROPOSAL_GOAL_DRIVEN_POST_TESTS.md`*, gitignored.)

### Stylometry / voice-drift collector (1.9, brand new)

Computes 12 stylometric markers from session prose and compares against a
voice fingerprint at `~/.empirica/voice/<name>.fingerprint.json`. Emits
`prose_stylometry_composite_drift` as an EvidenceItem the goal-criterion
bridge can gate on — turns "I preserved your voice" from assertion into
a falsifiable claim with a number attached.

### Content-aware source-provenance nudge (1.9, brand new)

When you log a finding/decision/etc. that contains a URL but no
`--source` flag, the CLI emits a stderr nudge naming the detected URL
and listing three remediations (source-add then re-log, tag with
`--epistemic-source search`, or suppress with env var). Non-blocking —
the artifact still logs.

Closes the long-standing source-adoption gap (prior nudges at CHECK
and POSTFLIGHT had measured 0% adoption).

### Cockpit + groups (1.8.x, mature in 1.9)

`empirica cockpit launch` orchestrates multi-pane setup. Groups
(1.8.18+) let you partition the cockpit into logical sets (e.g.
`core` + `research` + `outreach`). See [TMUX_MULTI_PANE_GUIDE.md](./TMUX_MULTI_PANE_GUIDE.md).

### Source-aware Sentinel substrate (1.8.x)

Every `*-log` command accepts `--epistemic-source {intuition|search|mixed}`.
The POSTFLIGHT `calibration_reflection.epistemic_provenance` block reports
how artifacts were arrived at — visibility-only in 1.9 but informs the
calibration substrate.

### Commit-context + edge declaration (1.8.x)

```bash
empirica commit-context <sha>            # Show artifacts anchored to a commit
empirica commit-context --range A..B     # Walk artifact graph in a range
empirica commit-context --depth 2 --since <date>
```

`*-log` commands accept inline edge flags:
- `--edge ID:RELATION` for typed links (any of: caused_by, evidence,
  invalidates, prevents, raised_by, resolves, sourced_from, attached_to,
  grounded_by)
- `--related-to ID` for generic relations
- `--evidence-from ID` (decision-log only) for finding→decision linkage

The temporal artifact graph + walker make it possible to ask "what did I
learn around this commit?" and follow the chain.

### Goals-list filters + drift detection (1.8.x)

`empirica goals-list --status {planned|in_progress|completed|drift}`
surfaces goals whose `is_completed` and `status` columns disagree (data
healing aid).

### Live-scan semantic index (1.9)

`docs/SEMANTIC_INDEX.yaml` is no longer a hand-managed cache. The loader
detects staleness against source mtimes and live-scans automatically.
`project-embed` (which runs on session-end) always sees fresh data.

### Sentinel quote-aware redirect detection (1.9)

Commands like `gh api ... | python3 -c "if x > 5: ..."` no longer get
falsely blocked because of `>` inside quoted code. Only real shell
redirects are flagged.

### Lean core prompt (default in 1.9)

The lean system prompt graduated from experimental. Identity, vectors,
transaction discipline always loaded; everything else (constitution,
EPP, EWM, code-audit, etc.) loads on demand via `Skill` tool. Net result:
~80% reduction in always-loaded context.

To verify you're on it:

```bash
head -3 ~/.claude/empirica-system-prompt.md
# Should mention "Lean Core v1.7.0" or later
```

If you want to revert to the verbose prompt, copy the template from
`empirica/plugins/claude-code-integration/templates/empirica-system-prompt-full.md`.

---

## Breaking changes

**None for typical users.** The 1.7 → 1.9 path is additive.

**For plugin/MCP developers:**

- The `empirica.config.semantic_index_loader.load_semantic_index()` signature
  added optional kwargs (`force_scan`, `write_back`) — calls with positional
  or no-kwarg arguments still work.
- `EvidenceItem` gained an optional `direction` field defaulting to
  `higher_is_better`. Existing emitters don't need changes; collectors that
  emit error-rate-style metrics should set `direction="lower_is_better"`.

---

## What changed since 1.8

| 1.9 addition | Status | Notes |
|---|---|---|
| Goal-driven post-tests bridge | Shipped | G1 (engine) + G2 (EvidenceMetricEvaluator) + G4 (criterion parser) |
| Stylometry collector | Shipped | T1+T2; T3 (NRC emotion) and T4 (bootstrap CLI) follow |
| Content-aware source nudge | Shipped | Fires at `*-log` invocation when text contains URLs without `--source` |
| Quote-aware redirect detection | Shipped | Sentinel no longer false-positives `>` inside quoted code |
| Live-scan semantic index | Shipped | Auto-invalidates on source mtime; project-embed always fresh |
| Cockpit dispatcher message polish | Shipped | "Registered evaluator(s) did not apply" vs "no evaluator registered" |
| Symmetric `--source` flag | Shipped | Now on assumption-log, decision-log, mistake-log (was: only finding/unknown/deadend) |
| EvidenceBundle `has`/`get`/`direction` helpers | Shipped | For named-metric lookup by goal-criterion evaluators |

---

## Action items for upgraders

- [ ] `pip install --upgrade empirica && empirica setup-claude-code --force`
- [ ] Verify lean core: `head -3 ~/.claude/empirica-system-prompt.md`
- [ ] If you have voice goals: declare a `quality_gate:prose_stylometry_composite_drift@<=0.25` criterion + drop a fingerprint at `~/.empirica/voice/<name>.fingerprint.json`
- [ ] If you have many stylometry-blind artifacts: nudge surfaces in stderr — review 3 actionable remediations on first nudge

---

## Cross-references

- [Project context for AI agents](./PROJECT_SWITCHING_FOR_AIS.md) — Authoritative
  guide to project resolution after the project-switch verb stabilized
- [Tmux multi-pane + cockpit](./TMUX_MULTI_PANE_GUIDE.md) — Multi-instance orchestration
- [Auto issue capture](./AUTO_ISSUE_CAPTURE_GUIDE.md) — Background bug capture
- *Goal-driven post-tests proposal* — `docs/specs/PROPOSAL_GOAL_DRIVEN_POST_TESTS.md` (local-only draft)
- *Stylometry drift proposal* — `docs/specs/PROPOSAL_STYLOMETRIC_DRIFT_COLLECTOR.md` (local-only draft)
