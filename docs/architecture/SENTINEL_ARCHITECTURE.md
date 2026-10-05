# Sentinel Architecture - The Gate

**Enforcement:** `empirica/plugins/claude-code-integration/hooks/sentinel-gate.py` (PreToolUse hook)
**Orchestration module:** `empirica.core.sentinel`

"The Sentinel" names two separate things in this codebase. Only the first one gates anything.

| Component | What it is | Runs when |
|-----------|------------|-----------|
| **The gate hook** (`sentinel-gate.py`) | A Claude Code `PreToolUse` hook. Classifies every tool call as noetic or praxic, and allows or denies praxic ones from the state of the current transaction. | On every tool call |
| **The orchestrator** (`empirica.core.sentinel`) | A library of domain profiles, compliance gates, persona selection and a loop tracker, reached only through the `sentinel-*` CLI verbs. | When you call `sentinel-orchestrate`, `sentinel-load-profile`, `sentinel-status` or `sentinel-check` |

PREFLIGHT, CHECK and POSTFLIGHT do not call the orchestrator. The gate hook reads what they wrote to the database; it does not import the orchestrator.

**Related docs:**
- [Sentinel Gate Reference](../reference/SENTINEL_GATE_REFERENCE.md) - decision flow, tool classification, safe-command lists, environment variables, response format
- [Sentinel Constitution](./SENTINEL_CONSTITUTION.md) - governance principles
- [NOETIC_PRAXIC_FRAMEWORK.md](./NOETIC_PRAXIC_FRAMEWORK.md) - the transaction the gate reads, and how CHECK decides
- [Phase-Aware Calibration](./PHASE_AWARE_CALIBRATION.md) - where the thresholds come from, and how phase-split tool counts weight calibration
- [CONFIGURATION_REFERENCE.md](../reference/CONFIGURATION_REFERENCE.md) - `EMPIRICA_SENTINEL_*` settings

---

## The gate hook

### Principle

Noetic work (reading, searching, read-only shell) cannot change state, so it is never gated. Praxic work (editing, writing, any shell that can mutate) is allowed only inside an open transaction that has been certified. The discriminator is the effect of the call as written, not the tool's name: `sed -i`, `find -delete` and `sqlite3 "UPDATE ..."` are praxic even though `sed`, `find` and `sqlite3` also have read modes.

### Order of evaluation

`main()` runs these in order and stops at the first that answers:

1. **Release-path exemption.** Recovery and measurement actions are always allowed, before any other gate: `empirica` MCP tools, the pause/resume toggle, and read-safe Bash whose pipeline carries a recovery or measurement verb from `_RECOVERY_MEASUREMENT_PREFIXES` (the PREFLIGHT/CHECK/POSTFLIGHT submits, the `*-log` verbs, `log-artifacts`, `resolve-artifacts`, `delete-artifacts`, `note`, `goals-*`, `noetic-batch`, and others listed there). A gate must never block the action that clears it.
2. **Investigation-proportionality budget.** If `tool-router.py` armed a budget on a hypothesis-bearing prompt, `Read`/`Grep`/`Glob` are denied once the limit is exceeded. The budget expires after an hour. This is the one gate that denies noetic tools.
3. **Noetic firewall.** Allowed without a transaction: the noetic tool set (`Read`, `Glob`, `Grep`, `LSP`, `WebFetch`, `WebSearch`, `ToolSearch`, `Task`, `TodoWrite`, `AskUserQuestion`, `Skill`, ...), the noetic subsets of the Chrome, Cortex and CRM MCP servers, read-only Bash, plan-file writes, and praxic remote commands when the latest PREFLIGHT or CHECK uncertainty clears the remote-infra bar.
4. **Exemptions.** Subagents, a paused Empirica (off-record), and a disabled Sentinel.
5. **Authorization pipeline** (below), for everything praxic that is left.

A tool nobody classified is praxic. The authoritative lists are the sets in `sentinel-gate.py`, and the reference doc explains each entry.

### Authorization pipeline

For a praxic call, in this order:

| Step | Result |
|------|--------|
| No session resolved, or no database connection | Allow (the Sentinel cannot see; it says so) |
| No PREFLIGHT in the transaction | Deny, except safe Bash and transition commands (`cd`, `session-create`, `project-bootstrap`, `preflight-submit`, `git add`, `git commit`); a counter nudges after 5 and 10 calls |
| PREFLIGHT's project differs from the current project | Deny: run PREFLIGHT for the new project |
| POSTFLIGHT exists after the PREFLIGHT (loop closed) | Deny, except safe Bash, toggles, transition commands and artifact-lifecycle `empirica` verbs |
| Previous transaction ended `investigate` with no findings logged since | Ask (praxic tools only; noetic tools and safe Bash pass) |
| **PREFLIGHT's own vectors clear the threshold** (`know >= K` and `uncertainty <= U`) | Allow ("auto-proceed"); no CHECK or claim is needed |
| No CHECK in the transaction | Allow if PREFLIGHT declared a certifying claim; otherwise deny and name both ways through |
| CHECK exists | Validate it, then allow (see below) |

**Certifying claims.** A claim certifies the transaction when it is grounded `read`, or grounded `ran` with both a non-empty `scope` and a `count`. `retrieved` (our own earlier artifacts) and `assumed` never certify. The hook queries `transaction_claims` directly because hooks cannot import the package; `empirica.core.claims.certifies` carries the same rule and a test pins the two together. If the lookup itself fails, the deny says the lookup failed rather than "you declared nothing". Skipping CHECK when you are already grounded is the intended path: noetic work is ungated, so reading before PREFLIGHT is the normal order.

**Validating a CHECK.** The CHECK row must be newer than the PREFLIGHT. A CHECK submitted under 30 seconds after PREFLIGHT (`EMPIRICA_MIN_NOETIC_DURATION`) with no finding or unknown logged since is denied as rushed; one artifact satisfies it, and `remote-ops` work is exempt. After a CHECK that returned `investigate`, the hook counts noetic calls and refuses a re-submitted `check-submit` until at least three have happened.

**Thresholds are advisory once a CHECK exists.** If the CHECK's `know` and `uncertainty` miss the thresholds the hook still allows the call and appends an `ADVISORY` line naming the shortfall. A CHECK that returned `investigate` is advisory in the same way. What the hook enforces is that a CHECK, a claim, or a confident PREFLIGHT exists, not that the numbers are good.

### Thresholds

Static fallbacks are `know >= 0.70` and `uncertainty <= 0.35`. The hook reads the noetic-phase thresholds from `compute_dynamic_thresholds()` for the practice's own `ai_id` and the current practitioner model, and uses them when enough history exists. A base uncertainty from `calibration.yaml` replaces the 0.35 baseline. Calibration history can only tighten these, never loosen them; see [Phase-Aware Calibration](./PHASE_AWARE_CALIBRATION.md#dynamic-thresholds).

The domain and criticality declared at PREFLIGHT scale the uncertainty bar through `DomainRegistry`, but in the hook that scaling is applied only at the auto-proceed step. The post-CHECK comparison uses the unscaled dynamic threshold.

### Nudges that never block

The hook counts gated tool calls per transaction in a counters file beside the transaction file, split into `noetic_tool_calls` and `praxic_tool_calls`. It adds text to an allow when:

- the count passes the practice's average transaction length (1.0x, 1.5x, 2.0x) - consider POSTFLIGHT;
- five or more gated calls have run with no goal in play in this transaction;
- there have been 5 or 10 calls with no transaction at all.

The phase-split counts go into POSTFLIGHT, where they weight the noetic and praxic calibration scores.

### Exemptions and switches

- **Subagents** are not gated: the parent's authorization covered the spawn. Detection reads `active_work_<claude_session_id>.json` for `is_subagent: true`, and falls back to absence of a matching active session or running in a linked git worktree. Their tool calls are added to the parent's `delegated_tool_calls` afterwards. `Task` is in the noetic set, so spawning is not gated either: the exemption is the subagent's own calls.
- **Paused**: `empirica off` / `empirica sentinel pause` allows everything for that instance.
- **Disabled**: `~/.empirica/sentinel_enabled` containing `false`, or `EMPIRICA_SENTINEL_LOOPING=false`.
- **Optional checks, off by default**: `EMPIRICA_SENTINEL_REQUIRE_BOOTSTRAP`, `EMPIRICA_SENTINEL_CHECK_EXPIRY` (30 minutes), `EMPIRICA_SENTINEL_COMPACT_INVALIDATION`.

### Failure mode

The hook fails open. An internal error allows the call and writes `SENTINEL_CRASH` to stderr, because measurement must not strand the work it measures. `EMPIRICA_SENTINEL_FAIL_CLOSED=1` flips this to deny. When the hook cannot run at all it says so in the text the model reads.

---

## The orchestrator module (`empirica.core.sentinel`)

A separate toolkit for domain-aware governance and multi-agent orchestration. It keeps no state between CLI invocations: `sentinel-status` builds a fresh `Sentinel` each time and reports what a new one would track.

### CLI

| Verb | Does |
|------|------|
| `sentinel-orchestrate --session-id S --task T` | Selects personas, optionally spawns agents and merges their results (`--merge union\|consensus\|best_score\|weighted`, `--dry-run`, `--profile`, `--max-agents`, `--scope-breadth`, `--scope-duration`) |
| `sentinel-load-profile --session-id S --profile P` | Loads a domain profile (`--file` for a custom YAML) and prints its gates |
| `sentinel-check --session-id S` | Runs the profile's compliance gates against `--vectors` (or `--know` / `--uncertainty`), `--findings` and `--unknowns`, and prints a decision |
| `sentinel-status --session-id S` | Prints the loaded profile, loop tracking, and available profiles |

These are distinct from `empirica sentinel pause|resume|status`, which control the gate hook.

### Classes

- **`GateAction`**: `PROCEED`, `INVESTIGATE`, `HALT_AND_AUDIT`, `REQUIRE_HUMAN` (value `require_human_review`), `ESCALATE`, `LOG_AND_CONTINUE`.
- **`ComplianceGate`**: a `condition` string plus an action and priority. A condition is a vector comparison (`"uncertainty > 0.4"`), a flag name (`"pii_detected"`), or the built-in `"high_risk"` (uncertainty above 0.6 and impact above 0.7).
- **`DomainProfile`**: `uncertainty_trigger`, `confidence_to_proceed`, gates, persona and tool restrictions, audit settings. The built-in profiles are `general`, `healthcare` (HIPAA, trigger 0.30, proceed at 0.85) and `finance` (SOX, trigger 0.35, proceed at 0.80), defined in `Sentinel.DEFAULT_PROFILES`.
- **`NoeticFilter` / `AxiologicGate`**: pattern-based filters for investigation paths and for actions with required vectors. They are defined in `orchestrator.py`; no code outside it references them, so neither the hook nor any CLI verb applies them.
- **`EpistemicLoopTracker` / `LoopRecord`**: records PREFLIGHT-to-POSTFLIGHT vector deltas and decides whether another loop is warranted: it stops at `max_loops` (derived from scope breadth and duration when unset), when `know` and `uncertainty` deltas stay under `convergence_threshold` for `convergence_window` loops, or when the last uncertainty is under 0.25. `LoopMode` is `USER`, `AI` or `SENTINEL`.
- **`DecisionLogic` / `PersonaMatch`**: persona selection by semantic match, using Qdrant at the host and port given to `Sentinel`.
- **`Sentinel`**: ties these together. `check_compliance()`, `orchestrate()`, `auto_orchestrate()` (loop tracking plus agent wiring plus orchestrate), `from_goal()` (scope vectors read from a goal), `load_domain_profile()`, `init_loop_tracking()`.
- **`MergeStrategy`**: `CONSENSUS`, `BEST_SCORE`, `WEIGHTED`, `UNION`, `INTERSECTION`. The CLI offers the first four plus `union`; `INTERSECTION` is reachable only from Python.

### Compliance decision

`check_compliance()` evaluates every gate of the loaded profile. The most severe triggered action wins, in this order: `halt`, `require_human`, `escalate`, `investigate`. With no gate triggered it falls back to the profile's thresholds: `uncertainty > uncertainty_trigger` gives `investigate`, otherwise `know >= confidence_to_proceed` gives `proceed`, otherwise `investigate`. With no profile loaded it uses uncertainty alone: `<= 0.35` proceeds.

### Custom profile files

`--file` reads the YAML through `DomainProfile.from_dict`, which expects `uncertainty_trigger`, `confidence_to_proceed`, `audit_all_actions` and similar keys at the top level. The shipped files under `empirica/core/sentinel/profiles/` nest those under `thresholds:` and `audit:`; loading one of them with `--file` keeps the gates but reads the thresholds as the defaults (0.5 and 0.75) and audit as off. The built-in profiles come from `DEFAULT_PROFILES`, not from those files.

### Optional external evaluator

`empirica/core/canonical/empirica_git/sentinel_hooks.py` defines `SentinelHooks`, a registry where an outside evaluator can be registered to review CHECK checkpoints. `check-submit` consults it, and a registered evaluator's verdict can replace the decision (`proceed` or `investigate`), only when at least one evaluator is registered and autopilot binding is not on. With none registered it does nothing.

---

## Source files

- `empirica/plugins/claude-code-integration/hooks/sentinel-gate.py` - the gate hook
- `empirica/core/claims.py` - claim grounding and `certifies()`
- `empirica/core/sentinel/orchestrator.py` - `Sentinel`, profiles, gates, loop tracker
- `empirica/core/sentinel/decision_logic.py` - persona selection
- `empirica/cli/command_handlers/sentinel_commands.py` - the `sentinel-*` verbs
- `empirica/core/canonical/empirica_git/sentinel_hooks.py` - optional external CHECK evaluator
