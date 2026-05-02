# Known Issues - Instance Isolation

Historical bugs and fixes related to instance isolation. Useful for debugging and understanding design decisions.

---

## Fixed Issues

### 11.1 Sentinel Loop Auto-Closing (2026-02-06)

**Symptom:** Epistemic loops close unexpectedly between commands
**Root cause:** Sentinel used CWD-based `get_empirica_root()` which pointed to wrong project when Claude Code reset CWD.
**Fix:** Added `resolve_project_root()` using priority chain: transaction → instance → TTY → CWD

### 11.2 Compact Hooks Project Mismatch (2026-02-06)

**Symptom:** After compaction, commands fail with "Project not found"
**Root cause:** Hooks didn't check `active_work_{session_id}.json` first.
**Fix:** Added Priority 0 check for active_work file in pre-compact.py and post-compact.py.

### 11.3 Goal Transaction Linkage (2026-02-06)

**Symptom:** Goals have NULL transaction_id
**Root cause:** `save_goal()` didn't accept transaction_id.
**Fix:** Added transaction_id to save_goal() signature; goals-create auto-derives from active transaction.

### 11.4 CLI Commands Using Wrong Database (2026-02-06)

**Symptom:** CLI commands use wrong project database after project-switch
**Root cause:** `get_session_db_path()` only had CWD-based resolution.
**Fix:** Added active_work file to priority chain in path_resolver.py.

### 11.5 Statusline Shows Stale Session Data (2026-02-06)

**Symptom:** Statusline shows 0 goals when database has 23 active goals
**Root cause:** `get_active_session()` prioritized TTY session over active_work file.
**Fix:** Reordered priority: active_work (authoritative) → TTY session (fallback).

### 11.6 PREFLIGHT Writes Transaction to Wrong Project (2026-02-06)

**Symptom:** After project-switch, PREFLIGHT writes to CWD project
**Root cause:** Used `project_path=os.getcwd()` instead of resolving from active_work.
**Fix:** Resolve project_path from active_work file first.

### 11.7 Sentinel Blocks project-switch Without PREFLIGHT (2026-02-06)

**Symptom:** Can't run `project-switch` when no PREFLIGHT exists
**Root cause:** project-switch wasn't in whitelist.
**Fix:** Added to TRANSITION_COMMANDS and TIER1_PREFIXES.

### 11.8 Sentinel "Project Context Changed" After PREFLIGHT (2026-02-06)

**Symptom:** Sentinel blocks commands after project-switch + PREFLIGHT
**Root cause:** project_id format mismatch between vectors.py and sentinel.
**Fix:** Updated vectors.py to use same priority chain as sentinel.

### 11.9 instance_id Format Mismatch (2026-02-07)

**Symptom:** Statusline shows wrong pane's data (cross-pane bleed)
**Root cause:** Two `get_instance_id()` functions returned different formats (`tmux_4` vs `tmux:%4`).
**Fix:** Standardized to `tmux_N` format everywhere.
**Commit:** `dfb5261e`

### 11.10 CWD Fallback Causes Silent Wrong-Project (2026-02-07)

**Symptom:** Commands/hooks silently use wrong project
**Root cause:** All resolution functions fell back to CWD-based detection.
**Fix:** Removed CWD fallback entirely; return None/error if instance-aware mechanisms fail.
**Commit:** `dfb5261e`

### 11.11 Statusline Cross-Instance Phase Bleed (2026-02-07)

**Symptom:** Statusline shows other Claude instance's phase
**Root cause:** `get_latest_vectors()` queried by session_id only, not transaction_id.
**Fix:** Added transaction_id filter to query.
**Commit:** `abb5c430`

### 11.12 Post-Compact Writes Wrong Session to instance_projects (2026-02-13)

**Symptom:** Statusline shows stale CHECK data from different session after compaction
**Root cause:** `_write_active_work_for_new_conversation()` used `empirica_session` from generic lookup instead of transaction's session_id.
**Fix:** Use `active_transaction.get('session_id')` when continuing a transaction.
**Commit:** `f8d9a82f`

### 11.13 Instance Isolation Fails When claude_session_id Unavailable (2026-02-13)

**Symptom:** `instance_projects` has `claude_session_id: null`
**Root cause:** CLI commands via Bash can't access claude_session_id (only in hook stdin).
**Fix (partial):**
- post-compact: Always write instance_projects even without claude_session_id
- project-switch: Log warning when claude_session_id unavailable
**Key insight:** instance_id is PRIMARY isolation key; claude_session_id is supplementary.
**Commit:** `23f79366`

### 11.14 project-switch Reverted by Self-Heal in get_active_project_path (2026-02-19)

**Symptom:** `empirica project-switch <project>` runs successfully but Sentinel and
statusline still see the old project. `instance_projects` shows
`"source": "self-heal-from-active_work"` — the self-heal overwrote the switch.

**Root cause (regression):** Commit `55b2e4ac` (Feb 9) flipped the priority in
`get_active_project_path()` from instance_projects-first (correct, `a3f74a33` Feb 7)
to active_work-first. It also added a self-heal that overwrote `instance_projects`
from `active_work` when they disagreed.

After project-switch:
- `instance_projects` correctly points to new project (CLI just wrote it)
- `active_work` still points to old project (CLI can't update it — no claude_session_id)
- Next hook (Sentinel) calls `get_active_project_path(claude_session_id)` → reads
  stale `active_work` → self-heals `instance_projects` BACK to old project

The commit's comment "active_work (set by project-switch, user intent)" was incorrect —
project-switch CANNOT write active_work because it doesn't know claude_session_id.

**Fix:** Reverted to instance_projects-first priority. Removed self-heal entirely.
`instance_projects` is the most current source because it's writable by both hooks
AND project-switch CLI. `active_work` is fallback for non-TMUX only.

**Also committed (safe, additive):**
1. `_update_active_work()`: Added TTY session fallback for instance_id resolution
2. `session-init hook`: Propagates `claude_session_id` to TTY session file

**Commit:** `f9d607ed` (TTY fallback), Fixed in v1.6.6

### 11.15 Transaction File Deleted After POSTFLIGHT Breaks Compact (2026-02-21)

**Symptom:** After POSTFLIGHT closes a loop, compaction loses project context. post-compact
writes `active_work` pointing to wrong project. Sentinel blocks with "No PREFLIGHT".

**Root cause:** `clear_active_transaction()` deleted the transaction file immediately after
POSTFLIGHT. When compaction occurred, post-compact had no transaction file to read for
project context. It fell back to stale `active_work` data from a previous session.

**The problem sequence:**
1. POSTFLIGHT closes loop → `status="closed"` → file deleted
2. User continues work in same project (no new PREFLIGHT yet)
3. Compaction occurs
4. post-compact calls `_find_project_root()` → no transaction file exists
5. Falls back to stale `active_work` from old session → wrong project
6. Writes new `active_work` with wrong `project_path`
7. User's next PREFLIGHT attempt blocked because project context is wrong

**Fix:** Transaction files now persist as "project anchors" until overwritten by next PREFLIGHT.
1. `workflow_commands.py`: Removed `clear_active_transaction()` call from POSTFLIGHT handler
2. `sentinel-gate.py`: No longer deletes closed transactions (was backup purge)
3. `post-compact.py`: Uses closed transactions for project resolution (fallback to open)

**Design principle:** Closed transaction file serves two purposes:
- Open (`status="open"`): Active gating by Sentinel
- Closed (`status="closed"`): Project anchor for post-compact resolution

The file is only overwritten when a new PREFLIGHT starts a new transaction.

**Commit:** Fixed in v1.5.5 — Closed Transactions as Anchors

### 11.16 Auto-POSTFLIGHT From CHECK Doesn't Close Transaction File (2026-02-22)

**Symptom:** CHECK auto-triggers POSTFLIGHT on goal completion. Epistemic snapshot
written to DB, but transaction file stays `status="open"`. Statusline/Sentinel
still see an open transaction.

**Root cause:** POSTFLIGHT handler used a custom project resolution chain
(TTY→active_work→CWD) instead of canonical `get_active_project_path()`.

The failure path (historical — auto-POSTFLIGHT removed in 1.6.4):
1. CHECK called `_auto_postflight()` → spawned `empirica postflight-submit -` subprocess
2. Subprocess inherits `TMUX_PANE` (instance_id resolves correctly)
3. TTY session has `claude_session_id=None` (by design — CLI can't access it)
4. Without claude_session_id, can't find `active_work_{id}.json` → `resolved_project_path=None`
5. Falls back to `Path.cwd()` — **violates NO CWD FALLBACK rule**
6. If CWD is wrong (post-compact, Claude reset), can't find `.empirica/` → silent fail
7. DB snapshot written (uses session_id, not project_path) but transaction file untouched

**Fix:** Replace custom resolution with `get_active_project_path()`:
- P0: `instance_projects/tmux_N.json` (always available when TMUX_PANE set)
- P1: `active_work_{claude_session_id}.json` (fallback for non-TMUX)
- No CWD fallback

**Commit:** `72149c86`

### 11.17 Pytest Subprocess Tests Pollute Live Database (2026-02-22)

**Symptom:** Running `pytest` creates test sessions and goals in the live `sessions.db`.
`goals-list` shows dozens of "Test AI-first goal creation [...]" entries after test runs.

**Root cause:** `test_ai_agent_workflow.py` uses `subprocess.run(['empirica', ...])` which
inherits the parent environment. The subprocess resolves the database via:

1. `get_active_context()` → finds `instance_projects/tmux_N.json` → live project path
2. `get_session_db_path()` → returns live `sessions.db`
3. `resolve_session_db_path()` → also returns live `sessions.db`

`EMPIRICA_SESSION_DB` env var existed but was priority 3 (last) in `get_session_db_path()`
and not checked at all in `resolve_session_db_path()`. The live DB always won.

**Fix (3 parts):**

1. **`path_resolver.py`:** Moved `EMPIRICA_SESSION_DB` to priority 0 in both
   `get_session_db_path()` and `resolve_session_db_path()`. When explicitly set,
   it now wins over all instance-aware resolution. This also fixes the Docker use case
   documented in `README.md` (which previously didn't actually work if the container
   had a git repo).

2. **`test_ai_agent_workflow.py`:** Added `isolated_env` fixture that creates a temp
   `sessions.db` and passes `EMPIRICA_SESSION_DB` via subprocess env. Tests now write
   to the temp DB. TMUX_PANE is preserved so project path resolution still works.

3. **Priority chain update:**
   ```
   get_session_db_path():
     0. EMPIRICA_SESSION_DB (explicit override)
     1. Unified context (transaction → active_work → TTY → instance_projects)
     2. workspace.db lookup
     3. Git root based

   resolve_session_db_path():
     0. EMPIRICA_SESSION_DB (explicit override)
     1. instance_projects mapping
     2. TTY session
     3. get_session_db_path() fallthrough
   ```

**Verified:** After fix, running `pytest` twice produced 0 new test goals/sessions in live DB.

**Commit:** Fixed in v1.5.7 — Test Isolation EMPIRICA_SESSION_DB

### 11.18 SessionStart Hook Matchers Never Matched (2026-02-22)

**Symptom:** Instance isolation breaks when CWD-based resolution diverges from
project-switch binding. `session-init.py` never fires on new conversations.

**Root cause:** `~/.claude/settings.json` SessionStart hook matchers used invalid
trigger values:
- `"new|fresh"` → never matches (Claude Code triggers: `startup`, `resume`, `clear`, `compact`)
- `"compact"` → missed `resume` trigger (continued conversations)

This meant:
1. `session-init.py` **never fired via hooks** — only via manual invocation
2. `post-compact.py` only ran on compaction, not on conversation resume
3. `ewm-protocol-loader.py` only loaded on compaction events

**Why it appeared to work:** `post-compact.py` (on `"compact"` matcher) correctly
writes `instance_projects` and `active_work` files. Since most long sessions involve
compaction, isolation files were eventually created. `project-switch` also writes them.
The gap only showed when a fresh conversation started in a different CWD than the
bound project — `session-init` should have written the isolation files at startup
but never did.

**Fix:** Updated `~/.claude/settings.json` matchers:
```json
"SessionStart": [
  { "matcher": "compact|resume", "hooks": [...post-compact, ewm-protocol-loader...] },
  { "matcher": "startup",        "hooks": [...session-init, ewm-protocol-loader...] }
]
```

**Claude Code SessionStart triggers (authoritative):**
| Trigger | When |
|---------|------|
| `startup` | New conversation begins |
| `resume` | Existing conversation continued |
| `clear` | After `/clear` command |
| `compact` | After auto/manual context compaction |

**Prevention:** Verify hook trigger values against platform documentation, not intuition.

**Commit:** applied to `~/.claude/settings.json` (not in git — user config file)

### 11.19 Ghost Session Propagation in post-compact (2026-02-25)

**Symptom:** Statusline shows `[empirica-web:inactive]` despite hooks running successfully.
`instance_projects` and `active_work` reference a session_id that doesn't exist in any database.

**Root cause:** Session `7b56baa5` was created (likely in a different DB or deleted during cleanup)
but all isolation files still referenced it. When post-compact ran on `resume`:

1. `_get_empirica_session()` read `active_work` → found ghost session `7b56baa5`
2. `_get_session_phase_state("7b56baa5")` queried local DB → zero reflexes found
3. Zero reflexes → `is_complete = False` → routed to CHECK_GATE (not NEW_SESSION)
4. CHECK_GATE path does NOT create a new session — just re-propagates the ghost ID
5. Statusline queries `WHERE session_id = '7b56baa5' AND end_time IS NULL` → not found → "inactive"

**The self-reinforcing loop:** Each resume/compact re-reads the ghost from active_work,
fails to find it in DB, misinterprets as "mid-work incomplete session", and writes it back.
The ghost persists across unlimited compaction cycles.

**Fix:** Added ghost session detection in post-compact.py `main()`. After resolving
`empirica_session` from active_work, verify it exists in the local DB via
`SELECT 1 FROM sessions WHERE session_id = ?`. If not found, create a new session
and update all isolation files — bypassing the normal phase-state routing.

**Prevention:** Session existence check before using session_id from file-based state.
File-based state can reference sessions that no longer exist (cleanup, DB restore,
cross-DB creation). Always verify against the authoritative source (the database).

**Commit:** applied to `~/.claude/plugins/local/empirica/hooks/post-compact.py`

### 11.20 CLI Commands Use tmux-Only Instance ID (2026-03-19)

**Symptom:** CHECK records stored with `transaction_id=NULL`. Sentinel blocks all praxic
actions with "No valid CHECK found" despite CHECK returning `proceed`.

**Root cause:** `project-switch`, `session-create`, `transaction-adopt`, and
`write_tty_session()` constructed instance_id exclusively from `TMUX_PANE`. In X11,
macOS Terminal, or TTY-only environments, instance_id was `None`, so no
`instance_projects/{id}.json` was written. This broke the resolution chain:
`get_active_project_path()` → `read_active_transaction()` → CHECK `transaction_id`.

**Why it surfaced now:** Isolation hardening (v1.6.5–1.6.21) removed CWD fallbacks
and tightened resolution to be instance-aware. Correct in principle, but exposed
the latent tmux-only assumption in CLI commands.

**Fix:** All CLI commands now use canonical `get_instance_id()` which supports
tmux, X11 (`WINDOWID`), macOS Terminal (`TERM_SESSION_ID`), and TTY.
`tool-router.py` hook also fixed (had wrong format `tmux:{pane}` and no X11 support).

**Commit:** `dd4f823d`, `4c2b8266`

### 11.21 Subagent Detection Broken by Inherited Env Vars (2026-03-19)

**Symptom:** Explore/research subagents get full Sentinel gating (blocked by "No valid
CHECK found") instead of being exempted. Causes unbounded tool calls as subagents
struggle with noetic firewall they shouldn't face.

**Root cause:** Subagent exemption checked `active_session_{instance_suffix}`. But
subprocesses inherit env vars (`WINDOWID`, `TMUX_PANE`) from the parent, so
`_get_instance_suffix()` returns the same value for both parent and subagent.
The subagent finds the parent's `active_session` file and is treated as the parent.

**Fix:** Check `active_work_{claude_session_id}.json` instead. Subagents get a
different `claude_session_id` from Claude Code, so they won't have a matching file.
If `active_session` also exists (confirming parent was set up properly), the subagent
is exempted. If neither file exists, falls through to normal gating (fail-safe for
broken session-init).

**Commit:** `c74705c0`

### 11.22 Transaction Suffix Mismatch in Hook Context (2026-03-23)

**Symptom:** Sentinel blocks ALL praxic actions in Claude Code sessions. User cannot
write, edit, or execute anything. CHECK passes via CLI but sentinel-gate hook can't
find the transaction. Reported by Philipp on macOS with tmux.

**Root cause:** Transaction files are written by CLI commands where `TMUX_PANE` is
available (e.g., `active_transaction_tmux_5.json`). But Claude Code hooks don't
reliably inherit `TMUX_PANE` — `get_instance_id()` returns `None`, so
`_get_instance_suffix()` returns `""`, and the hook looks for
`active_transaction.json` which doesn't exist.

This is a reader/writer suffix mismatch: the writer has env var context the reader
lacks. Unlike 11.20 (where the writer was wrong), here both sides use the canonical
`get_instance_id()` correctly — but the env vars available differ between contexts.

**Fix:** Added `_find_transaction_file()` fallback to both `session_resolver.py` and
`sentinel-gate.py`. When the exact-suffix file doesn't exist, scans for any
`active_transaction_*.json` matching the current `session_id`. The scan is safe
because it's scoped by session_id — no cross-instance talk.

The fix is at the InstanceResolver level (`read_active_transaction_full()`) so all
consumers benefit automatically, plus mirrored in sentinel-gate.py which has its
own transaction lookup for performance reasons.

**Risk:** If multiple instances share a session_id (shouldn't happen in practice),
the scan could return the wrong instance's transaction file. Monitor for unexpected
cross-instance behavior after this change.

**Workaround (pre-fix):** `echo "false" > ~/.empirica/sentinel_enabled`

**Commit:** Fixed in v1.7.0

### 11.23 Cross-Project Session Bleed via Stale Context (2026-03-22)

**Symptom:** `session-create`, `goals-complete`, and Sentinel use wrong project's
database when working in a multi-project setup. Goals marked complete don't show
as complete in statusline. Sessions created in one project appear in another.

**Root cause:** `get_session_db_path()` calls `R.context()` which falls through the
priority chain (transaction → instance_projects → TTY session). When instance-keyed
files don't match (after restart, project switch, or tmux recreation), TTY session
or stale instance_projects return a DIFFERENT project's path. Since `get_session_db_path()`
trusts the context resolver unconditionally, `SessionDatabase()` connects to the wrong DB.

**Fix:** Added git-root cross-check to `get_session_db_path()`, gated behind
`EMPIRICA_CWD_RELIABLE=true` env var. Only activates when CWD is known reliable
(set by `session-init.py` after `os.chdir(project_root)`). Detects when context
project differs from git root and falls through to git-root-based resolution.
Sentinel hook does NOT use CWD cross-check (CWD unreliable in hooks per 11.10).

**Commit:** `7aade9e4`, `937bf15f`

### 11.24 Subagent Pollution + Post-Compact Auto-Heal Gap (2026-04-08)

**Symptom (in empirica-cortex after `/compact`):**
```
_validate_session_in_db: session cd738ee9... NOT FOUND in project-local DB:
  /home/yogapad/empirical-ai/empirica-cortex/.empirica/sessions/sessions.db
  Recent sessions in DB: [('8a7693e2', 'superpowers:code-reviewer'),
                          ('0592a9c3', 'Explore'),
                          ('ee07936a', 'general-purpose'), ...]
get_active_empirica_session_id: stale session in transaction: cd738ee9...
```
Statusline failed and PREFLIGHT couldn't find the parent session.

**Two intertwined root causes:**

1. **Subagents creating rows in main `sessions` table.** `SubagentStart` hook
   called `SessionDatabase.create_session()` for every Task spawn (Explore,
   general-purpose, superpowers:* etc), polluting the main table with rows
   that always had `parent_session_id` set. Subagent children were always
   newer than their parents, so the "Recent sessions in DB" diagnostic
   surfaced only subagent rows — masking the actual parent and making the
   error message misleading.

2. **`post-compact.py` `CONTINUE_TRANSACTION` branch never validated the
   session_id it propagated forward.** This is the unfinished half of
   **11.19**. That fix added `_validate_session_in_db` and wired it into
   the `CHECK_GATE` branch, but the open-transaction branch reads
   `tx_session_id = active_transaction.get('session_id') or empirica_session`
   and writes it to `active_work` / `active_transaction` without ever
   checking it exists in the current project's DB. When the parent was
   originally created in a different project (cross-project `--resume`),
   the cortex DB never got the row and every subsequent CLI command
   failed validation.

**Fix:**

- **Subagent isolation:** Migration 034 creates a dedicated
  `subagent_sessions` table. `SessionDatabase.create_subagent_session()`,
  `end_subagent_session()`, `get_subagent_session()`, and
  `list_subagents_for_parent()` write to and read from it. Subagent rows
  no longer touch the main `sessions` table; lineage is preserved via
  `parent_session_id`. The migration moves existing legacy subagent rows
  out automatically (status `completed` if they had `end_time`,
  `orphaned` otherwise). `SubagentStart` and `SubagentStop` hooks updated.

- **Post-compact auto-heal:** New
  `SessionDatabase.ensure_session_exists()` does an idempotent insert of
  a minimal session row preserving the caller-provided `session_id`,
  marked `session_notes='auto-healed by post-compact'`, also registered
  in `workspace.db` for cross-project visibility. `post-compact.py`'s
  `CONTINUE_TRANSACTION` branch now calls `_validate_session_in_db` on
  `tx_session_id` and heals before propagating forward. Heal failure is
  non-fatal and logged to stderr.

**Lessons learned:** When adding a validator to a hook with multiple
routing paths, audit ALL paths, not just the one that triggered the
original report. 11.19's fix added the right tool but wired it into one
branch; the bug returned six weeks later via the unwired branch with
the same symptoms. Defensive checklist for future validation work:

1. List every code path that writes the value being validated.
2. For each path, decide: validate-and-heal, validate-and-fail, or skip.
3. Document the decision (even "skip" — make it explicit).
4. Add a regression test that exercises EVERY path.

**Tests:** 13 new in `tests/test_subagent_sessions.py` covering schema,
migration, all repository methods, and `ensure_session_exists` idempotency.

**Commit:** `9dd4f39a2`

### 11.25 project-switch Auto-Heal Gap (2026-04-08)

**Symptom:** After `empirica project-switch <other_project>`, every CLI
command in the new project surfaces the validation diagnostic from
11.24:

```
_validate_session_in_db: session 26a05872... NOT FOUND in project-local DB
get_active_empirica_session_id: stale session in transaction: 26a05872...
```

The `active_work_<claude_session_id>.json` file points at a session
that doesn't exist in the new project's `sessions.db`. Discovered while
working on the 11.24 fix — caught my own session in flight.

**Root cause:** `handle_project_switch_command` (`project_commands.py:944-989`)
mirrors the parent session into the target project DB, but only if a
prior lookup against `global_sessions` returns a row matching the current
`instance_id` and `status='active'`. When that lookup misses (instance
ID mismatch from a tmux restart, status drift, or just a stale workspace
DB), `attached_session` stays `None`, the mirror block at line 948 is
skipped, and the active_work file is written with `empirica_session_id=None`
or stays pointing at the prior project's session_id.

This is the same bug class as 11.24 (validation gap on a code path
that propagates session_id forward) but in `project-switch` instead
of `post-compact`. The lessons-learned checklist from 11.24 explicitly
called out the failure mode: validators wired into one code path but
not all of them.

**Fix:** After the existing mirror logic, if `attached_session_id` is
still None, the project-switch handler now reads the existing
`active_work_<claude_session_id>.json` file and recovers its
`empirica_session_id` field as a fallback. Whatever session_id it ends
up with, it then calls `SessionDatabase.ensure_session_exists()` on the
target project's DB to guarantee the session row exists before
`_update_active_work` propagates the pointer forward. Idempotent — no-op
if the row already exists.

When the heal fires, the user sees:
```
🔄 Recovered session 26a05872 from active_work file
🩹 Auto-healed session 26a05872 into target project DB
```

**Lessons learned (revised from 11.24):** Audit ALL paths that propagate
a session_id, not just hooks. The original 11.24 checklist mentioned
"hooks with multiple routing paths" but the same logic applies to any
CLI command that writes session_id into shared state. Updated checklist:

1. List every code path (hook, CLI handler, daemon) that writes a
   session_id into `active_work`, `instance_projects`, transaction
   files, or any other shared state.
2. For each path, decide: validate-and-heal, validate-and-fail, or
   skip. Document the decision.
3. Add a regression test that exercises EVERY path.

**Commit:** (committed with project-switch fix below)

---

### 11.26 STARTUP OVERRIDE Bypassing Open Transactions (2026-04-08)

**Symptom:** After compact rotation, harness CWD doesn't match the active
transaction's project, but everything (auto-memory, gitStatus, CLI commands)
still resolves to the CWD project. The open transaction on the original
project gets orphaned and a new duplicate session is created in the wrong
project's DB.

**Discovery context:** David noticed mid-conversation: "wait, why are we in
outreach now, is the post compact not respecting that we had switched to
another project?" — the transaction was open on `empirica` core but the new
post-compact conversation landed in `empirica-outreach` CWD with outreach's
auto-memory loaded.

**Root cause:** `session-init.py:521-530` had a STARTUP OVERRIDE block added
in commit `d76a482a4` ("fix: Prefer CWD over stale instance files on startup
(#72)"). The intent was correct — on a fresh `event_type=startup`, the user
explicitly opened Claude Code in a directory and that intent should win over
stale instance files from a previous session. But the override didn't check
whether the resolved project_root had an OPEN transaction:

```python
if event_type == 'startup' and project_root:
    cwd_root = _find_git_root() or Path.cwd()
    if cwd_root.resolve() != Path(project_root).resolve():
        if has_valid_db(cwd_root) or not has_valid_db(Path(project_root)):
            project_root = cwd_root  # ← stomps the resolved project
```

When compact rotation triggered a SessionStart with `event_type=startup` and
the harness CWD pointed at a different project than the open transaction,
this block silently re-routed `project_root` to CWD. session-init then
`os.chdir`'d to the wrong project, set `EMPIRICA_CWD_RELIABLE=true`, and
ran `session-create` in the wrong DB. The original transaction was orphaned.

**Reproduction (verified):**
```python
# context: open transaction on /home/user/empirica, CWD=/home/user/empirica-outreach
import os
os.environ['EMPIRICA_CWD_RELIABLE'] = 'true'
from empirica.config.path_resolver import get_session_db_path
get_session_db_path()
# Before fix: returns /home/user/empirica-outreach/.empirica/sessions/sessions.db (WRONG)
# After fix:  returns /home/user/empirica/.empirica/sessions/sessions.db (correct)
```

**Fix:** Added an open-transaction guard that bails out of the override if
the resolved project has a `status=open` transaction file. Open transactions
are authoritative across compaction boundaries — CWD never wins.

```python
if event_type == 'startup' and project_root:
    suffix = _get_instance_suffix()
    tx_file = Path(project_root) / '.empirica' / f'active_transaction{suffix}.json'
    has_open_tx = False
    try:
        if tx_file.exists():
            with open(tx_file) as f:
                has_open_tx = json.load(f).get('status') == 'open'
    except Exception:
        pass

    if not has_open_tx:
        # original CWD-prefer logic still fires (handles #72 case)
        cwd_root = _find_git_root() or Path.cwd()
        ...
```

**Audit also covered:** `pre-compact.py`, `post-compact.py`,
`sentinel-gate.py`, `session-end-postflight.py` — confirmed clean. They
already use strict resolution (`find_project_root` without
`allow_cwd_fallback`) or use `_find_session_via_active_work` first.

**Test coverage:** `tests/test_open_transaction_guard.py` —
`TestSessionInitStartupOverrideGuard` reproduces the override condition in
isolation and asserts the guard holds (open tx → no override) plus the
regression check (no tx → override still fires).

**Lessons learned:** Any priority chain has CWD at the bottom, but
"prefer CWD" overrides bolted on top of the chain need to check for open
transactions explicitly. Active transactions are stronger than CWD because
they survive across CWD resets, terminal restarts, and compact rotations.

**Commit:** (committed with 11.27 + 11.28 below)

---

### 11.27 EMPIRICA_CWD_RELIABLE Cross-Check Bypassing Open Transactions (2026-04-08)

**Symptom:** Same as 11.26 — CLI commands route to the wrong project's DB
across CWD/transaction mismatch — but at the second amplification site.
Once 11.26 mis-routed session-init, every subsequent CLI command followed
suit because session-init had set `EMPIRICA_CWD_RELIABLE=true` in the env
inherited by child processes.

**Root cause:** `path_resolver.get_session_db_path()` lines 255-263 had a
gated CWD cross-check designed to prevent stale-context bleed (added in
commit `7aade9e4d`):

```python
cwd_reliable = os.getenv('EMPIRICA_CWD_RELIABLE', '').lower() == 'true'
context_is_local = True
if cwd_reliable and git_root and str(git_root) != context_project_path:
    local_db = Path(str(git_root)) / '.empirica' / 'sessions' / 'sessions.db'
    if local_db.exists():
        context_is_local = False  # prefer CWD over context
```

Same blind spot as 11.26: when the unified context resolver returned
`project_path = empirica` (correctly, via active_work / open transaction)
but `git_root = empirica-outreach` and CWD was reliable, the cross-check
fired and re-routed to outreach's DB. The intent ("detect cross-project
bleed from stale context, prefer git root") was correct for cases where
context was actually stale — but bypassed open transactions.

**Fix:** Same open-transaction guard. Read
`active_transaction{suffix}.json` from `context_project_path` and bail
out of the cross-check if `status=open`:

```python
if cwd_reliable and git_root and str(git_root) != context_project_path:
    suffix = R.instance_suffix()
    tx_file = Path(context_project_path) / '.empirica' / f'active_transaction{suffix}.json'
    has_open_tx = False
    try:
        if tx_file.exists():
            with open(tx_file) as f:
                has_open_tx = json.load(f).get('status') == 'open'
    except Exception:
        pass

    if not has_open_tx:
        # original cross-check still fires (handles 11.23 case)
        local_db = Path(str(git_root)) / '.empirica' / 'sessions' / 'sessions.db'
        ...
```

This is a fix-pair with 11.26 — fixing only one would leave the other as
the amplification site. Both were rooted in the same logical mistake of
adding "prefer CWD" overrides without checking open transactions.

**Test coverage:** `tests/test_open_transaction_guard.py` —
`TestPathResolverGuard` reproduces the bug condition empirically (writes
two projects, one with open tx, asserts the resolver stays on the
transaction project even with `EMPIRICA_CWD_RELIABLE=true`).

**Commit:** (committed with 11.26 + 11.28 below)

---

### 11.28 Auto-Memory Loaded From Wrong Project Across CWD Mismatch (2026-04-08)

**Symptom:** Even after 11.26 + 11.27 fix the internal Empirica resolver,
Claude Code's auto-memory loader still loads the wrong project's
`MEMORY.md` because it's wired to the harness CWD at session start. The
user works on project A but their terminal is in project B, and every
conversation starts with B's memory — including the breadcrumb cache,
project context, recent findings, etc.

**Why the resolver alone wasn't enough:** The unified context resolver
gives 100% correctness for Empirica internals (CLI, hooks, sentinel,
statusline) but Claude Code's auto-memory loader is OUT of Empirica's
control. It maps `Path.cwd() → ~/.claude/projects/-{cwd}/memory/MEMORY.md`
at conversation start, before any hook fires. We can't change the parent
shell's CWD from a hook subprocess. We CAN, however, change what's at the
file path the loader reads.

**Constraint analysis (David's "make CWD trust us" idea, grounded):**

| Mechanism | What it changes | Reach | Cost |
|-----------|-----------------|-------|------|
| Resolver (existing) | Internal Empirica code | All hooks, CLI, sentinel | Already paid |
| Memory swap (new) | Auto-memory dir contents | Claude Code auto-memory | 1-2 file copies per boundary |
| `.empirica` symlink swap | DB path resolution from CWD | One additional surface | Symlink fragility, conflicts with real CWD project state |
| Bind mount | Everything CWD-derived | Universal | Sudo, OS-specific, dangerous if left mounted |
| Shell wrapper for project-switch | Real CWD change | Universal | UX regression, breaks for non-wrapped shells |

Memory swap is the only one that meaningfully extends reach beyond the
resolver without taking on fragile failure modes. Resolver + memory swap
together cover everything actionable; cosmetic gaps (gitStatus shown to
Claude, "Primary working directory" line in system prompt) are
unreachable from any layer.

**Fix:** New `empirica.utils.memory_swap` module with three primary functions:

* `swap_memory(harness_cwd_project, active_tx_project, ...)` — backs up
  the harness-CWD project's `~/.claude/projects/-{cwd}/memory/`
  contents to a `.memory-swap-backup/` subdir, copies the active project's
  memory contents into the harness slot, writes a manifest file
  (`.memory-swap-manifest.json`) recording the swap.
* `restore_memory(harness_cwd_project)` — reverses the swap. Removes
  swapped-in files, restores originals from backup, deletes manifest
  and backup dir.
* `maybe_swap_for_active_transaction(claude_session_id)` — convenience
  entry point for hooks. Reads InstanceResolver context, compares
  against current CWD's git root, swaps if mismatched.

The swap is:
* **Idempotent** — calling twice with the same source is a no-op
* **Replacement-safe** — swapping with a different source restores the
  previous swap first to avoid layering
* **Round-trip safe** — swap + restore is byte-identical to the original
* **Manifest-tracked** — manifest records source project, timestamps,
  session_id, transaction_id, list of backed-up files, list of
  swapped-in files

**Wired into:**
* `post-compact.py` — swap on entry when context mismatch detected
* `session-end-postflight.py` — restore in `_cleanup_session_files`
* `handle_project_switch_command` — swap on project-switch (CLI)

**Test coverage:** 17 tests in `tests/test_memory_swap.py`:
* Path computation (Claude memory dir encoding)
* Swap copies source to target
* Swap backs up originals
* Swap writes manifest
* Swap is no-op when paths equal
* Swap is no-op when source missing
* Swap is idempotent
* Swap replaces existing swap (doesn't layer)
* Restore returns originals
* Restore removes swapped-in files
* Restore clears manifest + backup dir
* Restore is no-op when no swap
* Round-trip preserves content
* Round-trip handles nested directories
* `maybe_swap_for_active_transaction` swaps when context has project
* `maybe_swap_for_active_transaction` is no-op when no active project

**Defense in depth:** Memory swap is a complementary layer to the
resolver, not a replacement. The resolver is the correctness backbone
for internal code; memory swap closes the visibility gap to Claude Code.
If memory swap fails for any reason, internal code still works correctly
because the resolver is independent. If the resolver had a bug, memory
swap alone wouldn't be sufficient because internal code paths would
still mis-route.

**Future work (1.7.14 follow-up):** The plugin lib `project_resolver.py`
contains dead-code duplicates of `get_active_project_path` and
`get_active_session_id` that import from empirica core first and only
fall back to inline duplicates when import fails (which never happens
in production). The only logic that legitimately exists only in the
plugin lib is `find_project_root()` — and it could move into
`InstanceResolver` as well. Collapsing 3 resolver implementations to 1
would prevent future drift between hook-side and CLI-side logic.
Tracked separately.

**Commit:** (committed with 11.26 + 11.27 above)

---

### 11.29 Subagent CLI Calls Bleed to Parent Session via TTY Resolution (2026-04-29)

**Symptom:** Parallel general-purpose subagents running `empirica
preflight-submit` / `postflight-submit` per file produced reflex rows
tagged with the *parent's* `session_id` instead of their own. The
parent's loop then appeared "closed" mid-batch — every subsequent edit
in the parent (or another subagent) tripped the gate at
`sentinel-gate.py:1788-1843` with "Epistemic loop closed (POSTFLIGHT
completed)". Reported by @pschwinger as #95 Issue 1; he confirmed it
empirically by showing zero `subagent_sessions/*.json` files were
written during the bug window.

**Root cause:** `subagent-start.py` created a `child_session_id` in
the `subagent_sessions` table and a per-spawn file under
`.empirica/subagent_sessions/`, but it did NOT write
`~/.empirica/active_work_<subagent_uuid>.json`. When the subagent's
inline `empirica` CLI ran, the resolver chain found no active_work for
the subagent's `claude_session_id`, fell through to TTY-based
fallback, and resolved to the *parent's* `empirica_session_id`
(subagents inherit TTY from the parent). Reflexes from the subagent
got tagged with the parent's session, contaminating the parent's
PREFLIGHT/POSTFLIGHT pair.

This is the project_id-grain analog of 11.21 + 11.24 — the same
pattern (subagent-vs-parent identity) leaking through a different
mechanism (CLI session resolution rather than detection or session
existence).

**Fix:**

- `subagent-start.py` now writes
  `~/.empirica/active_work_<subagent_claude_session_id>.json` with
  `is_subagent: true` and the subagent's `child_session_id`. The
  subagent's CLI calls now resolve to their own session.
- `sentinel-gate.py:_detect_subagent` updated to flag-based detection
  (read `is_subagent` from active_work). Falls back to existing
  absence-detection for in-flight subagents predating the fix and for
  the broken-session-init failure mode.
- `subagent-stop.py` deletes the active_work file at SubagentStop —
  prevents stale-file pollution if `claude_session_id`s ever recur.

**Note:** Complementary prompt-level optimization (telling subagents
not to run measurement cycles since they're sentinel-exempt) belongs
in the empirica system prompt template and is queued as a follow-up.

**Commit:** `0d2970b64` (T4 in the 1.8.15 work). Tests in
`tests/test_subagent_active_work.py` (7 cases).

---

### 11.30 SystemExit-from-Library Walks Through Soft-Stage Wrappers (2026-04-29)

**Symptom:** POSTFLIGHT exits with code 1 and `persisted: false` even
though the loop has actually closed (transaction file written, reflex
row in DB). User can't tell whether the loop is open or closed; loop
correctness diverges from CLI exit code. Reported by @pschwinger as
#95 Issues 2+3 (he traced both to the same propagation path).

**Reproduction (his):** ran `_cortex_resolve_project_id()` directly
with `cwd=~/empirica/` (a sibling-folder project on his macOS multi-
`.empirica` setup). Function read `Path.cwd() / '.empirica' /
'project.yaml'`, called `cli/utils/project_resolver.py:resolve_project_id`,
which called `sys.exit(1)` because the project_id wasn't in the local
DB. SystemExit walked through every `except Exception: pass` wrapper
above (cortex_sync, postflight handler) and killed the command — but
stages 3-4 had already written the reflex.

**Root cause:**

1. `cli/utils/project_resolver.py:resolve_project_id` was a library
   helper that called `sys.exit(1)` on miss. Library functions should
   raise; only CLIs should call sys.exit.
2. `SystemExit` derives from `BaseException`, not `Exception`. Every
   `except Exception` wrapper (cortex_sync's `except Exception: pass`,
   the POSTFLIGHT pipeline's wrappers) silently failed to catch it.
3. `_cortex_resolve_project_id` read `project.yaml` from `Path.cwd()`
   even though the session row already had a canonical `project_id`.
   The CWD read introduced the multi-`.empirica` misroute hazard.
4. POSTFLIGHT stages 3-4 (close transaction + write reflex) ran
   unconditionally before stages 5-7 (storage, compliance, cortex
   sync) had any chance to fail. Half-success: reflex written, exit 1.

**Fix (multi-part, landed across 1.8.15 + 1.8.19):**

- POSTFLIGHT pipeline restructured (workflow_commands.py:3452+):
  - **Stage 0 (NEW)** pre-validates session row + project_id BEFORE
    any state mutation. Failure → early return with
    `{ok: false, persisted: false, loop_state: "open"}`. No mutation.
  - **Stages 3-4** hard-mutate (close transaction + reflex) only
    after pre-validation passes.
  - **Stages 5-7** soft-mutate, wrapped in `_soft_run` which
    accumulates failures into `result["warnings"]` without erasing
    the reflex. `_soft_run` catches SystemExit explicitly (defense in
    depth) — KeyboardInterrupt still propagates.
- `_cortex_resolve_project_id(session_id)` reads project_id from the
  session row directly. No more `project.yaml`/`Path.cwd()` involved
  in cortex sync. Eliminates the misroute pattern structurally.
- `_cortex_read_calibration_summary(project_path)` accepts the path
  from the caller (POSTFLIGHT's `resolved_project_path`); falls back
  to `Path.cwd()` only when unset.
- `_run_grounded_verification(..., project_path)` accepts the path
  too — same shape, drops two adjacent CWD-fallbacks
  (workflow_commands.py:2566, 2584).
- `cli/utils/project_resolver.py:resolve_project_id` now raises
  `ProjectNotFoundError` (a normal `Exception` subclass) instead of
  `sys.exit(1)`. Library callers can catch and recover; top-level CLI
  handlers' existing `except Exception` paths print the error via
  `handle_cli_error` and exit cleanly. Same UX, no SystemExit.

**End-state contract:** POSTFLIGHT is either *rejected pre-mutation*
(loop open, retry after fix) or *succeeded with optional warnings*
(loop closed, warnings visible in result). Never half-success.

**Out of scope:** the user's specific phantom UUID (`5f2f7a4d-...`)
came from a sibling `~/empirica/.empirica/` folder on his machine.
Multi-`.empirica` setups for the same logical project aren't
supported configurations. The fix above means the resolver leak that
exposed his setup is closed; it doesn't add multi-`.empirica` support.

**Commits:** `101a06c17` (POSTFLIGHT pipeline restructure — 1.8.15),
`c389f6a06` (cortex sync from session row — 1.8.19), `27d76bc13`
(resolve_project_id raises — 1.8.19), `ec94dca1f` (grounded
verification CWD fix — 1.8.19). Tests in
`tests/test_postflight_pipeline_restructure.py` (19 cases) and
`tests/test_project_resolver_raise.py` (8 cases).

---

## By Design (Not Bugs)

### Orphaned Transaction After Terminal Restart

**Scenario:** Terminal closes, new session has different instance IDs, old transaction can't be found.

**Auto-adoption (v1.6.21+):** `session-init.py` now scans the CWD project's `.empirica/`
for any `active_transaction*.json` with `status: open` on startup. If found, it adopts the
most recent orphaned transaction by re-keying to the new instance suffix and reusing the session.
This is safe because session-init already resolved the correct project via CWD/git root.

**Manual recovery** (if auto-adoption fails):
```bash
# Option 1: Adopt the transaction (works for any instance type)
empirica transaction-adopt --from <old_instance_id>

# Option 2: Abandon it
rm {project}/.empirica/active_transaction_<old_suffix>.json

# Option 3: Close it properly
empirica postflight-submit --session-id <old_session_id> ...
```

### project-switch Triggers POSTFLIGHT

**Behavior:** `project-switch` auto-triggers POSTFLIGHT on source project.
**Status:** By design - loops should close when switching projects.

---

## Common Failure Patterns

### CWD Reset by Claude Code

**Symptom:** Commands fail because they look in wrong project
**Fix:** Use TTY/instance session's project_path, not CWD

### Stale TTY Session

**Symptom:** Terminal closed and reopened, old session data used
**Fix:** `write_tty_session()` on session-create overwrites stale data

### Wrong Instance's Transaction

**Symptom:** Instance A sees instance B's transaction
**Fix:** Instance suffix on transaction files (`active_transaction_{suffix}.json`)

### Sentinel Blocks `cd && empirica check-submit`

**Symptom:** Sentinel blocks command chains with `&&`
**Fix:** Added special handling for `&&` chains with safe empirica commands

---

## Debugging Checklist

1. **Check instance_id:** `python3 -c "from empirica.utils.session_resolver import get_instance_id; print(get_instance_id())"`
   - tmux: `tmux_N` | X11: `x11:NNNNN` | macOS: `term:XXXX` | TTY: `term_pts_N`
2. **Check instance_projects file exists:** `ls ~/.empirica/instance_projects/`
3. **Check session matches:** Compare session_id in instance_projects vs active_transaction
4. **Check project_path:** Is it pointing to the right project?
5. **Check transaction status:** Is it `"open"` or closed?
6. **Check active_work exists (for subagent detection):** `ls ~/.empirica/active_work_*.json`

```bash
# Quick diagnostic
echo "=== Instance ID ===" && python3 -c "from empirica.utils.session_resolver import get_instance_id, _get_instance_suffix; print(f'id={get_instance_id()} suffix={_get_instance_suffix()}')"
echo "=== Instance Projects ===" && cat ~/.empirica/instance_projects/*.json 2>/dev/null
echo "=== Active Transaction ===" && cat .empirica/active_transaction_*.json 2>/dev/null
echo "=== TTY Session ===" && cat ~/.empirica/tty_sessions/$(tty | tr '/' '-' | sed 's/^-//').json 2>/dev/null
echo "=== Active Work ===" && ls -la ~/.empirica/active_work_*.json 2>/dev/null
```
