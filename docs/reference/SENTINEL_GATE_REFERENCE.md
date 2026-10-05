# Sentinel Gate Reference

**Status:** AUTHORITATIVE
**Source:** `empirica/plugins/claude-code-integration/hooks/sentinel-gate.py`
**Audience:** Developers, not AI agents (see [Sentinel Constitution](../architecture/SENTINEL_CONSTITUTION.md) Principle II: Measurement Opacity)

---

## What it is

The Sentinel Gate is a `PreToolUse` hook. It sorts every tool call it sees into **noetic** (reads, searches, planning: cannot change state) or **praxic** (can change state) and lets praxic calls through only when the current transaction certifies them. Design principle: default deny for anything that might write, explicit allow for what is known to read.

`empirica setup` registers it twice, with a 10 second timeout, for the matchers `Edit|Write` and `Bash` (`_register_all_hooks` in `empirica/cli/command_handlers/setup_claude_code.py`). A second PreToolUse hook, `ruling-shape.py`, matches `AskUserQuestion` and never blocks. Consequences worth knowing:

- Under Claude Code the gate never sees `Read`, `Grep`, `Glob`, `WebFetch` or MCP calls. The noetic tool sets below matter for harnesses that route more tools to the hook, for the phase counters, and for the closed-loop paths.
- The gate certifies **structure**, not vector values. Nearly everything it denies is "no open transaction", "loop closed", "no CHECK and no grounded claims", "rushed CHECK". A CHECK whose vectors miss the threshold is allowed with an advisory.

---

## Decision flow

`main()` runs these steps in order; the first that answers wins.

1. **Count and annotate.** Increment the hook counters (parent sessions only), compute the autonomy and goalless nudges, and look up artifacts that mention an `Edit`/`Write` target. None of this decides anything.
2. **Release-path exemption.** Always allow: any `mcp__empirica__*` tool; the pause/resume toggles; and a Bash call that is fully safe *and* contains a recovery or measurement verb in any pipeline segment (`preflight-submit`, `check-submit`, `postflight-submit`, the `*-log` family, `log-artifacts`, `resolve-artifacts`, `delete-artifacts`, `note`, `source-add`, `unknown-resolve`, `noetic-batch`, goal create/complete/list verbs, `doctor`, `diagnose`, `setup`, `plugin-sync`, `sentinel`, `listener on|off|status|arm`, `loop status|heartbeat|pause|resume|...`). The authoritative list is `_RECOVERY_MEASUREMENT_PREFIXES`. The rule: no gate may block the action that clears it.
3. **Investigation-proportionality budget.** For `Read`, `Grep` and `Glob` only: once `tool-router.py` has armed a budget on a hypothesis-bearing prompt, deny after the limit (default 5) until the next prompt resets it. Armings older than an hour are dropped. Under the default registration these tools never reach the hook, so this applies only where a harness routes them.
4. **Noetic firewall.** Allow a noetic tool, a `monitor` call whose whole input is `{"action": "list"}`, a safe Bash command, or a `Write`/`Edit` of a plan file (`/.claude/plans/` in the resolved path). A praxic remote command (`ssh`/`scp`/`rsync` that writes) is allowed here only if the latest PREFLIGHT or CHECK in the session has `uncertainty <= 0.25` (the ConfidenceGate for remote infra; `know` is not evaluated).
5. **Exemptions.** Allow when the caller is a subagent, when Empirica is paused, or when the Sentinel is disabled.
6. **Authorization pipeline.** Everything praxic that is left:
   - *Can the gate run at all?* If the hook's interpreter cannot import `empirica`, allow and say so (first call per session). If there is no git repo, no env root and no `.empirica/project.yaml` above the working directory, allow ("not applicable"). No resolvable session or no database: allow with a warning.
   - *Transaction file closed* (POSTFLIGHT sets `status` to `closed` and leaves the file): only noetic tools, safe Bash, transition commands and safe `empirica` statements pass; everything else is denied with the closed-loop message.
   - Optional (`EMPIRICA_SENTINEL_REQUIRE_BOOTSTRAP=true`): deny if the session has no `project_id`.
   - *No PREFLIGHT:* allow safe Bash and transition commands, deny the rest with "No open transaction". Calls are counted and nudged at 5 and 10.
   - *Project changed since PREFLIGHT:* deny, re-run PREFLIGHT.
   - *POSTFLIGHT exists after PREFLIGHT* (the same check against the database): same allow-list as the closed file; deny otherwise.
   - *Previous transaction's CHECK said `investigate` and no finding has been logged since this PREFLIGHT:* **ask** (praxic only).
   - *Auto-proceed:* PREFLIGHT `know >= K` and `uncertainty <= U` allows with no CHECK at all (thresholds below).
   - *No CHECK yet:* check-submit and noetic calls pass. Otherwise allow if PREFLIGHT declared at least one **certifying claim**, else deny.
   - *CHECK older than PREFLIGHT:* deny.
   - *Rushed:* CHECK within `EMPIRICA_MIN_NOETIC_DURATION` seconds (default 30) of PREFLIGHT with no finding and no unknown logged since PREFLIGHT: deny. `work_type=remote-ops` is exempt. One artifact clears it; waiting does not.
   - *CHECK decision `investigate`:* noetic work counts toward a cool-down; a `check-submit` before 3 noetic calls have passed since the `investigate` is denied; other praxic calls are allowed with an advisory.
   - Optional expiry (30 minutes) and compaction invalidation: deny.
   - *CHECK thresholds:* allow. A shortfall against the thresholds is surfaced as an `ADVISORY:` message, never a deny.

### Certifying claims

PREFLIGHT `claims` are stored in `transaction_claims`. Only `grounding: read`, or `ran` with both a non-empty `scope` and a `count`, certify. `retrieved` (our own prior artifact: testimony) and `assumed` do not. The hook cannot import `empirica.core.claims`, so it queries the table directly with the same rule; a test pins the two together. If the lookup itself fails (an old schema), the deny names the failure instead of saying "you declared nothing". An item with none of `claim`/`statement`/`text`, `grounding`, `scope`, `count`, `ref` is listed under `claims.skipped` in the PREFLIGHT output and not stored.

### Thresholds

`K` and `U` come from `_get_dynamic_thresholds`: static fallbacks `0.70` and `0.35`; the base uncertainty can be replaced by `ready_uncertainty` in `calibration.yaml` (practice over global, see [Configuration Reference](CONFIGURATION_REFERENCE.md)); a Brier-score adjustment per `ai_id` (the practitioner's own trajectory once it has enough points) can then **only tighten** them, bounded by the ceilings in `cascade_styles.yaml`. For the auto-proceed test only, `U` is scaled down when the transaction carries a `domain` or `criticality` whose checklist has checks (`max(0.10, U * (1 - coverage_min * 0.6))`). The gate always reads the **raw** vectors the AI submitted; calibration corrections are feedback, never silently applied here, and `EMPIRICA_CALIBRATION_FEEDBACK` does not change that.

---

## Tool classification

### Noetic tools

`NOETIC_TOOLS`: `Read`, `Glob`, `Grep`, `LSP`, `WebFetch`, `WebSearch`, `ToolSearch`, `Task`, `TaskOutput`, `TodoWrite`, `AskUserQuestion`, `Skill`, `KillShell`.

**Cortex MCP (`NOETIC_MCP_CORTEX`).** Membership is a decision, not a claim that every tool is read-only. It holds the pure reads (`investigate`, `search_knowledge`, `get_entity_context`, `cortex_stats`, the `list_*` and inbox/outbox polls, `get_proposal`, `get_skill`, source readers), the epistemic-workflow writes the gate admits by policy (`cortex_*_log`, `cortex_goal_create`, `cortex_log_artifacts`, ingest, the bus family, `cortex_collab`), and two soft flips on your own view (`archive_proposal`, `complete_proposal`). `cortex_propose`, `cortex_publish` and every unlisted tool stay gated; a tool nobody has classified is praxic. A bare `mcp__cortex` namespace is resolved to `mcp__cortex__<op>` from `op`/`operation`/`name`/`tool` in the input before classification.

**CRM MCP (`NOETIC_MCP_CRM_READS`).** `crm_whoami`, `crm_schema`, `crm_get`, `crm_list_*`, `crm_scope_changes`, `crm_consent_state`. Every upsert, delete, link, scope, supersede, transfer and consent-event tool is gated.

**Chrome MCP (`NOETIC_MCP_CHROME`).** Tab listing and creation, `navigate`, `read_page`, `get_page_text`, `find`, console and network reads, `screenshot`, `gif_creator`. `form_input`, `javascript_tool` and `computer` are praxic.

**Empirica MCP.** Every `mcp__empirica__*` tool is treated as epistemic workflow and always allowed.

Every site that asks "is this tool noetic" calls `_is_noetic_tool`, so a tool added to a set is noetic everywhere, including after POSTFLIGHT.

### Safe Bash

`is_safe_bash_command` answers "can this command, as written, change state?". Everything it accepts is noetic; the rest is praxic.

- **Prefix list.** `SAFE_BASH_PREFIXES` is the allow-list of read-only programs: file inspection (`cat`, `head`, `ls`, `find`, `fd`, `stat`, `diff`, `bat`, `tokei`), hashes, text tools (`grep`, `rg`, `ast-grep`, `sed`, `awk`, `jq`, `yq`, `sort`, `cut`, `column`, `xxd`), git reads, `gh` reads, environment and process inspection, `tmux` display commands, disk and network inspection (`curl`, `dig`, `ping -c`, `wget -O-`), documentation, `test`, and static analysis (`ruff check`, `pyright`, `mypy`, `vulture`, `radon`).
- **Mutating flags on a safe program are praxic.** `find -delete|-exec|...`, `fd -x`, `sort -o`, `yq -i`, `ast-grep --rewrite|-U`, `sed -i`, an `awk` program that prints to a file or calls `system()`.
- **Chains.** `;`, `&&`, `||`, newline-separated statements and `for`/`while` loops are safe only if every segment is. `ls; pwd` is safe, `ls; rm x` is not. Control-flow keywords, `[ ... ]` tests, `VAR=value` and `exit N` are inert shapes.
- **Command substitution.** `$(...)` and backticks are validated by classifying the inner command. In a chain segment or inside double quotes a substitution with a safe inner command passes (`echo "$(date)"`); a bare unquoted `$(...)` or backtick in a single command does not (`echo $(date)` is praxic).
- **Pipes.** Every stage must be read-only. After the first stage the legacy receivers in `SAFE_PIPE_TARGETS` (`python3 -c`, `xargs echo`, `tee /dev/stderr`, `base64`) are also accepted, never as the first stage.
- **Redirections.** `2>/dev/null`, `2>&1` and `>/dev/null` are fine. `>`, `>>` and `<` outside quotes are praxic. Heredocs are allowed for safe commands.
- **`python3 -c`.** A first-stage `python3 -c` is safe unless its text contains a write pattern (`open(`, `.write(`, `shutil.`, `os.remove(`, `subprocess.`, `os.system(`, `requests.post(`, SQL write keywords, `exec(`, `eval(`). That is a text scan, not a sandbox.
- **`sqlite3`.** Reads only: `SELECT`, `WITH`, `PRAGMA`, `EXPLAIN`, `ANALYZE` and display-only dot-commands. Quoted literals and comments are blanked before the write-keyword scan, so a word inside `'...'` is data; an unterminated literal is scanned as code. `-init` and `writefile()`, `load_extension()`, `edit()`, `fts3_tokenizer()` are refused.
- **`ssh`, `scp`, `rsync`.** Classified by the remote command and transfer direction, seeing through `timeout`, `env`, `nice` and similar wrappers: `ssh host ls` and `scp host:/x .` are safe, `ssh host "systemctl restart x"`, `scp x host:/y` and `rsync a b` are not; `rsync -n` is. Under `work_type=remote-ops` any `ssh`/`scp`/`rsync` passes, because local sensors cannot observe the remote box.
- **Work-type expansion.** With `work_type` `infra`, `config` or `debug`, `INFRA_SAFE_PREFIXES` adds system, docker, network and service inspection (`systemctl status`, `journalctl`, `docker ps|logs|inspect`, `ss`, `ip addr`, `kubectl get`, `terraform plan`, all `tmux`, macOS counterparts). `remote-ops` gets the same list.

Verified with the real function:

| Command | Safe? |
|---|---|
| `cd /tmp && grep foo file` | yes |
| `grep foo f \| head -5` | yes |
| `for f in a b; do cat $f; done` | yes |
| `echo "$(date)"` | yes |
| `ls; rm x` | no |
| `grep foo f > out.txt` | no |
| `echo $(date)` | no |
| `sed -i s/a/b/ f` | no |
| `find . -delete` | no |
| `git commit -m x` | no (but a transition command) |
| `sqlite3 db 'SELECT 1'` | yes |
| `sqlite3 db 'DELETE FROM t'` | no |
| `systemctl status x` | no; yes with `work_type=infra` |

`git branch -D x` also classifies as safe: the prefix `git branch` is on the list and the flag is not inspected. Treat that as a known gap, not as a policy.

### `empirica` CLI tiers

`is_safe_empirica_statement` accepts one statement (a heredoc counts as one; anything after its terminator is a second statement) whose verb is in:

- **Tier 1, read-only.** `EMPIRICA_TIER1_PREFIXES`: goal and epistemic queries, `project-bootstrap|search|switch|list`, `workspace-*`, `lesson-*` queries, `sentinel-*` queries, `calibration-report`, `compliance-report`, `doctor`, `diagnose`, `status`, `query`, `noetic-batch`, `practice-context`, `mailbox poll|show|sers`, `mesh status|diagnose|tail`, `mesh-agreements list`, `auth status`, plus the `sentinel`, `loop`, `listener` and `instance` control verbs.
- **Tier 1b, by naming convention.** Any verb ending in `-list`, `-show`, `-search`, `-status`, `-stats`, `-report`, `-map`, `-walk`, `-diff`, `-history`, `-explain`, `-context`, `-top`, `-related`, `-get`, `-verify` or `-signatures`. `-check` is deliberately absent: `sources-check` writes review stamps.
- **Tier 2, state-changing but part of the workflow.** `EMPIRICA_TIER2_PREFIXES`: the three transaction verbs, the `*-log` and `*-resolve` verbs, `note`, `source-add`, the batch artifact verbs, goal lifecycle verbs, `session-create|end`, `project-init|embed`, lesson lifecycle, profile sync/prune, `mailbox reply|archive`, `release`, `setup`, `plugin-sync`, `investigate`.
- **Help and version.** `--help`, `-h` and `--version` as a real argument are inert for any verb. The global flags `--verbose` and `-v` before the verb are stripped before prefix matching.
- **`empirica-workspace`** is a separate binary: only an exact (group, action) table is read-only (`org|contact|engagement list|show`, `engagement materials`, `touchpoint list`, `revenue-event list`, `entity knowledge|recall`, `crm-sync preview`).

Deliberately not Tier 1: `auth token` (can refresh and rewrite stored credentials) and `auth connectors` (rewrites `~/.claude.json` with `--apply`, and argparse accepts abbreviated flags).

---

## Between transactions

After POSTFLIGHT a new cycle must start without a chicken-and-egg. These are allowed while the loop is closed: safe Bash, safe `empirica` statements, the pause/resume toggles, and `TRANSITION_COMMANDS`: `cd `, `empirica session-create`, `project-bootstrap`, `project-init`, `project-switch`, `project-list`, `preflight-submit`, `git add`, `git commit`.

A multi-statement transition is allowed only if every segment is a transition command or a plain producer (`echo`, `cat`, `printf`), as in `echo '{...}' | empirica preflight-submit -` or `cd /path && empirica preflight-submit - <<'EOF'`. The heredoc must close with nothing after it, and anything on the delimiter line must be inert: `2>&1` and pipes into `head`, `tail`, `wc`, `grep`, `rg`, `cut`, `tr`, `cat`, `jq`. A PREFLIGHT that shares its Bash call with anything else is refused, and the deny says so instead of repeating "run PREFLIGHT". Likewise a command substitution whose inner command is not a read is named as the cause of the refusal.

---

## Anti-gaming

- **Rushed assessment.** See the pipeline. The deny names both halves of its predicate (short gap and zero artifacts) and both remedies (log one artifact, or skip CHECK by declaring grounded claims).
- **INVESTIGATE continuity.** A fresh PREFLIGHT cannot launder an `investigate` into a proceed without logging a finding: praxic calls get an `ask`, noetic calls pass.
- **Raw vectors only.** The gate never applies calibration corrections.
- **Release-path invariant.** Recovery and measurement verbs are evaluated before every other gate, so a deny can always be cleared from inside the session.

---

## Counters and nudges

Every call that reaches the hook in a parent session (one with an `active_work_{claude_session_id}.json`) increments `tool_call_count` in the **hook counters file**, beside the transaction file and named by it (`hook_counters_{suffix}.json`). The transaction file is workflow-owned; the counters are hook-owned, so each file has one writer. POSTFLIGHT reads the counters and deletes the file. The counters are split into `noetic_tool_calls` and `praxic_tool_calls`, and also record edited files and a capped tool trace; the split feeds phase-weighted calibration ([Phase-Aware Calibration](../architecture/PHASE_AWARE_CALIBRATION.md)).

Nudges are informational. They ride `additionalContext` on allowed calls, because Claude Code discards `permissionDecisionReason` on allow.

| Nudge | Trigger |
|---|---|
| Autonomy | Tool count against `avg_turns`, the mean tool-call count of the last 20 POSTFLIGHTs in the session (no nudge until there is history). At 1.0x "past average", at 1.5x "consider POSTFLIGHT soon", at 2.0x "strongly recommended" |
| Goalless work | 5 gated calls in this transaction with no goal in play (a goal created in it, a task created or completed since PREFLIGHT, or a finding logged against a goal); stronger at 10 |
| Work type | PREFLIGHT omitted `work_type`; once per transaction |
| File relevance | An `Edit`/`Write` target is mentioned by existing artifacts |
| Remote-ops | `ssh`/`scp`/`rsync` seen with a `work_type` other than `remote-ops`, `infra` or `config`; once per transaction |
| No transaction | 5 and 10 calls with no PREFLIGHT, counted in `pre_tx_calls{suffix}.json` |

---

## Subagents

A call is a subagent's when `active_work_{claude_session_id}.json` carries `is_subagent: true` (written by the SubagentStart hook). If that file is missing the gate falls back to absence signals: a linked git worktree, or an `active_session` whose session belongs to another `active_work` file. Subagents are allowed; the parent's CHECK already authorized the spawn. The SubagentStop hook adds their tool counts to the parent's `delegated_tool_calls`.

---

## Pause and disable

| Mechanism | How | Scope |
|---|---|---|
| Pause (off-record) | `empirica off` / `empirica on`, or `empirica sentinel pause` / `resume` (`--instance`, `--session`, `--all`, `--global`); the `/empirica` command wraps these | `~/.empirica/sentinel_paused_{instance_id}` per instance, checked first; `~/.empirica/sentinel_paused` for all instances |
| Disable (file) | Write `false` to `~/.empirica/sentinel_enabled` | Read on every call, no restart |
| Disable (env) | `EMPIRICA_SENTINEL_LOOPING=false` | Needs a restart |

The toggle verbs are recognised by `is_toggle_command` (token-exact, so `empirica onboarding` does not match) and pass the release-path exemption, so they work even with the loop closed. The file wins in both directions: the environment variable is consulted only when the file does not exist, so a file holding `true` makes `EMPIRICA_SENTINEL_LOOPING=false` inert. An unattended runner that must not be gated should use a scratch `HOME`, or run with the plugin off. Inside a project the Sentinel keeps refusing unmeasured praxic work for a run nobody opens a transaction for; that is by design.

---

## Instance isolation and project root

Per-instance files: `<project>/.empirica/active_transaction{suffix}.json`, `~/.empirica/sentinel_paused_{instance_id}`, `~/.empirica/pre_tx_calls{suffix}.json`, and the hook counters file. `suffix` is `_` plus the sanitized instance id, or empty with no instance.

Instance id, first match: `EMPIRICA_INSTANCE_ID` or `CLAUDE_INSTANCE_ID`, `TMUX_PANE`, `TERM_SESSION_ID`, `WINDOWID`, the TTY device. When no transaction file matches the current suffix (a hook that did not inherit `TMUX_PANE`, a pane number rotated across compaction), the lookup scans `active_transaction*.json` and prefers the durable `claude_session_id`, then an open transaction, then recency, so a stale closed file at the current suffix cannot mask a live one.

The project root comes from the shared project resolver: `instance_projects/{instance_id}.json`, then `active_work_{claude_session_id}.json`. There is no working-directory fallback, because Claude Code resets the cwd after compaction.

---

## Failure behaviour

- **Fail open by default.** A crash (import error, DB lock, unexpected exception) allows the call and writes `SENTINEL_CRASH` to stderr. `EMPIRICA_SENTINEL_FAIL_CLOSED=1` (also `true`, `yes`) turns a crash into a deny for hardened deployments.
- **Cannot import `empirica`.** The gate allows (a broken install must not lock a session out), says so once per Claude session in the user message and the model's context, and writes `~/.empirica/sentinel_unavailable.json` for `doctor`. The repair text follows `EMPIRICA_HARNESS`: under Claude Code, re-run `empirica setup-claude-code`; elsewhere, make the empirica CLI reachable on `PATH` (for codex, also `empirica diagnose --frontend ecodex`).
- **No project.** With no git repo, no env root and no `.empirica/project.yaml` at or above the working directory (the home directory's own `~/.empirica` does not count) the gate allows with "Sentinel not applicable". That is neither a crash nor a deny under fail-closed. A directory holding `project.yaml` but no git repo is a project whose root could not be resolved: it goes through the crash handler.

---

## Environment variables

| Variable | Default | Effect |
|---|---|---|
| `EMPIRICA_SENTINEL_LOOPING` | `true` | `false` disables the Sentinel; ignored when `~/.empirica/sentinel_enabled` exists |
| `EMPIRICA_SENTINEL_FAIL_CLOSED` | off | `1`/`true`/`yes`: a crash denies instead of allowing |
| `EMPIRICA_SENTINEL_REQUIRE_BOOTSTRAP` | `false` | Require a session `project_id` (from `project-bootstrap`) before praxic actions |
| `EMPIRICA_SENTINEL_CHECK_EXPIRY` | `false` | `true`: a CHECK older than 30 minutes denies |
| `EMPIRICA_SENTINEL_COMPACT_INVALIDATION` | `false` | `true`: a compaction after the CHECK denies. Takes effect only together with `EMPIRICA_SENTINEL_CHECK_EXPIRY=true`: the compaction comparison needs the CHECK time, which is parsed only inside the expiry branch |
| `EMPIRICA_MIN_NOETIC_DURATION` | `30` | Seconds between PREFLIGHT and CHECK below which an artifact-free CHECK is rushed |
| `EMPIRICA_INSTANCE_ID`, `CLAUDE_INSTANCE_ID` | unset | Explicit instance id |
| `EMPIRICA_HARNESS` | `claude-code` | Selects the repair text when the hook cannot import `empirica` |

The full list is in [Environment Variables](ENVIRONMENT_VARIABLES.md).

---

## Response format

The hook prints JSON in Claude Code's PreToolUse format:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow|ask|deny",
    "permissionDecisionReason": "...",
    "additionalContext": "Sentinel: <nudges>"
  },
  "suppressOutput": true
}
```

`suppressOutput` appears only on an allow with no nudge; `additionalContext` only on an allow with at least one nudge. **Ask** prompts the user and is used only for INVESTIGATE continuity. **Deny** shows the reason and, where a remedy exists, names it.

---

## Related documents

| Document | Relationship |
|---|---|
| [Sentinel Constitution](../architecture/SENTINEL_CONSTITUTION.md) | Governance principles that constrain this code |
| [Sentinel Architecture](../architecture/SENTINEL_ARCHITECTURE.md) | Higher-level architecture (orchestrator, compliance gates) |
| [Phase-Aware Calibration](../architecture/PHASE_AWARE_CALIBRATION.md) | How phase-split tool counts feed calibration |
| [Environment Variables](ENVIRONMENT_VARIABLES.md) | Full env var reference |
| [Configuration Reference](CONFIGURATION_REFERENCE.md) | File-based configuration, thresholds, `calibration.yaml` |
