# Empirica Configuration Reference

**Status:** Technical reference, checked against the loaders in `empirica/config/`, `empirica/core/calibration_config.py` and the call sites that read each file.

For end-user guides see [Start Here](../human/end-users/01_START_HERE.md), [Installation](../human/end-users/02_INSTALLATION.md), [Troubleshooting](../human/end-users/03_TROUBLESHOOTING.md), [CLI quickstart](../human/end-users/04_QUICKSTART_CLI.md) and [First-time setup](../human/end-users/FIRST_TIME_SETUP.md).

---

## What you can set, and where

Empirica has no single configuration file. Each setting lives with the thing it configures, in one of three places:

| Place | What goes there |
|---|---|
| **Per project**, `<project>/.empirica/` | `project.yaml` (identity, subjects, per-practice policy), `config.yaml` (store location, database, sync), `calibration.yaml` (thresholds and weights for this practice), `domains.yaml` (completion checklists), `compliance.yaml` (tuning for `empirica compliance-report`) |
| **Per user**, `~/.empirica/` | `credentials.yaml`, `config.yaml` (embeddings), `calibration.yaml` (global thresholds), `domains/*.yaml`, `notify.yaml`, `workflow-protocol.yaml`, and small flag files (`sentinel_enabled`, `statusline_mode`, `sentinel_paused*`, `trusted_hosts`) |
| **Inside the package**, `empirica/config/` | Shipped defaults: `mco/*.yaml`, `investigation_profiles.yaml`, `domains/*.yaml`, `ai_registry.json`. These are not a user override surface; the layers above override them where an override exists |

Environment variables sit on top of all of it. [Environment Variables](ENVIRONMENT_VARIABLES.md) is the complete list; this page names the ones that decide where configuration comes from.

**`empirica config` is a viewer for the shipped MCO files, not a settings store.** `empirica config` prints them (`--section` picks one of `model_profiles`, `personas`, `epistemic_conduct`, `ask_before_investigate`, `protocols`), `empirica config KEY` reads a dotted key, `--init` lists the MCO files and where they live, `--validate` checks them for missing files and incomplete profiles, and `empirica config KEY VALUE` changes nothing: it prints which YAML file you would edit. Nothing it reports comes from `project.yaml`, `config.yaml` or the credentials file.

---

## Finding the store

Which `.empirica` directory and which `sessions.db` a command uses is decided by `empirica/config/path_resolver.py`.

**Empirica root** (`get_empirica_root`), first match wins:

1. `EMPIRICA_WORKSPACE_ROOT`: `<value>/.empirica` (Docker and multi-AI setups)
2. `EMPIRICA_DATA_DIR`: the directory itself
3. the `root` key of `<git root>/.empirica/config.yaml`
4. `<git root>/.empirica`

With none of these (not in a git repo, nothing set) it raises; there is no working-directory fallback, because Claude Code can reset the cwd. The two environment paths are rejected if they point into a system directory (`/etc`, `/usr`, `/bin`, `/root`, `/proc`, and similar).

**Session database** (`get_session_db_path`), first match wins:

1. `EMPIRICA_SESSION_DB`: this file, bypassing instance resolution (tests, CI, Docker)
2. the project of the current instance, resolved from the open transaction, `active_work`, TTY and `instance_projects`. The working directory overrides it when it holds its own `.empirica/project.yaml` and the instance's project has no open transaction of this caller's; `EMPIRICA_CWD_RELIABLE=true` extends that to preferring the git root
3. the workspace registry (`~/.empirica/workspace/workspace.db`), keyed by the git root
4. `<empirica root>/sessions/sessions.db`

If none yields a database the error lists what was tried. Other resolution inputs: `EMPIRICA_PROJECT_PATH` is read by the statusline; `EMPIRICA_INSTANCE_ID` / `CLAUDE_INSTANCE_ID` set the instance id (see [Session Resolver API](SESSION_RESOLVER_API.md)).

```bash
python3 -c "from empirica.config.path_resolver import debug_paths; print(debug_paths())"
```

prints the git root, empirica root, session database, the two override variables and whether a `config.yaml` was loaded.

---

## `project.yaml`: the practice's identity and policy

**File:** `<project>/.empirica/project.yaml`. Created by `empirica project-init`, changed by `empirica project-update`, which merges its changes over the existing file so keys it does not model are kept.

```bash
empirica project-init --non-interactive --type research --domain bio/genomics
empirica project-update --type research --domain bio/genomics
empirica project-update --add-contact alice --roles reviewer evaluator
empirica project-update --add-edge project/other --relation extends
empirica project-update --migrate        # upgrade a v1.0 file to v2.0
```

### Fields the loader models

`load_project_config` (`project_config_loader.py`) reads these. Every field has a safe default, so a v1.0 file works unchanged. `project_id` is taken from `sessions.db` when it has one; the file's value is only the fallback for a fresh project.

| Field | Values | Default | Notes |
|---|---|---|---|
| `version` | `'1.0'`, `'2.0'` | `'1.0'` when absent | `project-init` writes `'2.0'` |
| `name`, `description` | text | `Unknown Project`, empty | |
| `ai_id` | string | project directory name | The practice's canonical identifier. Sessions, calibration and mesh addressing follow it. Prefix kept: `empirica-cortex` stays `empirica-cortex` |
| `type` | `software`, `content`, `research`, `data`, `design`, `operations`, `strategic`, `engagement`, `legal` (legacy: `product`, `application`, `feature`, `documentation`, `infrastructure`) | `software` | An unknown value logs a warning and becomes `software` |
| `domain` | free text, hierarchical (`ai/measurement`) | empty | |
| `classification` | `open`, `internal`, `restricted` | `internal` | Invalid becomes the default |
| `status` | `active`, `dormant`, `archived` | `active` | |
| `evidence_profile` | `code`, `prose`, `web`, `hybrid`, `auto` | `auto` | Which evidence collectors grade the transaction. `project-init` and `project-update` restrict the choices; the loader does not |
| `languages`, `tags` | lists | `[]` | |
| `created_at`, `created_by`, `repository` | text | none | Provenance |
| `contacts` | `[{id, roles}]` | `[]` | References to people; profiles live in the workspace CRM |
| `engagements` | `[{id, ...}]` | `[]` | References; lifecycle lives in the CRM |
| `edges` | `[{entity, relation}]` | `[]` | Typed links to other entities. `relation` is free text (`related` by default; `parent_of`, `owned_by`, `extends` appear in use) |
| `subjects` | `{name: {paths: [...], description}}` | `{}` | Maps directories to logical subjects |
| `default_subject` | subject name | none | |
| `auto_detect` | `{enabled, method}` | `{enabled: true, method: path_match}` | When enabled, the working directory is matched against subject `paths` to scope `project-bootstrap`. Paths are resolved against the current directory |
| `beads` | `{default_enabled}` | `{}` (off) | Default for the BEADS integration |
| `domain_config` | mapping | `{}` | Domain extension point |

### Keys other code reads

The loader ignores these; the modules named read them from the same file.

| Key | Read by | What it does |
|---|---|---|
| `hygiene_policy` | `config/hygiene_policy.py` | How aggressively this practice's artifact sweeps clean. Fields and defaults: `source_staleness_days: 30`, `unknown_triage_days: 14`, `goal_auto_close: evidence_only` (or `surface_only`), `auto_delete: test_noise_only` (or `off`), `dedup: exact_only` (or `fuzzy`). An invalid value falls back to its default, never raises |
| `artifact_graph` | CHECK gate | `strictness` (default 0.75), `connectivity_floor` (0.34), `patience` (0.80), each clamped to 0-1. Environment overrides: `EMPIRICA_ARTIFACT_GRAPH_STRICTNESS`, `EMPIRICA_ARTIFACT_GRAPH_FLOOR`, `EMPIRICA_ARTIFACT_GRAPH_PATIENCE`. Strictness below 0.05 is silent, below 0.40 reports, below 0.70 warns, at 0.70 and above enforces (CHECK flips `proceed` to `investigate` below the connectivity floor) |
| `calibration_weights` | POSTFLIGHT | Per-phase, per-vector weights (`noetic`, `praxic`, each `vector: weight`), seeded by `project-init` from the project type and backfilled by `project-bootstrap` for older projects |
| `calibration_exclusions` | grounded calibration | Known-bad measurement windows; see [Calibration](#calibration) |
| `org_id`, `tenant_slug`, `mesh_id_prefix`, `canonical_seat` | `setup`, mesh tooling | Tenant and mesh identity written during setup |
| `publish_channels`, `substrate`, `cockpit` | release checks, cockpit, listeners | Practice-specific |

---

## `config.yaml`: store, database, sync

**File:** `<project>/.empirica/config.yaml`, created by `project-init` (and by `create_default_config` in a git repo). Its presence is also what `project-init` treats as "already initialised".

```yaml
version: '2.0'
root: /path/to/project/.empirica      # read: step 3 of the root resolution above
paths: {sessions: sessions/sessions.db, identity: identity/, messages: messages/, metrics: metrics/, personas: personas/}
settings: {auto_checkpoint: true, git_integration: true, log_level: info}
env_overrides: [EMPIRICA_DATA_DIR, EMPIRICA_SESSION_DB]
```

Only `root` of those is read back (`paths`, `settings` and `env_overrides` are written as a record and have no reader). Sections that do have readers:

- **`database`** (`database_config.get_database_config`). SQLite is the default. For PostgreSQL, in priority order: `DATABASE_URL` (a `postgresql...` URL), then `EMPIRICA_DB_TYPE=postgresql` with `EMPIRICA_DB_HOST`, `EMPIRICA_DB_PORT` (5432), `EMPIRICA_DB_NAME` (`empirica`), `EMPIRICA_DB_USER` (`empirica`), `EMPIRICA_DB_PASSWORD`, then a `database:` block here with `type: sqlite|postgresql`, `sqlite.path`, `postgresql.{host,port,database,user,password}`; a value written `${VAR}` is replaced from the environment.
- **`sync`** is read and written by the sync commands.
- **`integrations.beads`** configures the BEADS integration.

---

## Thresholds and what "ready" means

Four independent layers decide how much the CHECK gate and the Sentinel ask for. [Sentinel Gate Reference](SENTINEL_GATE_REFERENCE.md) has the decision flow; this is where the numbers come from.

### 1. Cascade profiles (`empirica/config/mco/cascade_styles.yaml`)

Loaded by `ThresholdLoader`. Six profiles: `default`, `exploratory`, `rigorous`, `rapid`, `expert`, `novice`. Each carries `cascade.ready_know_threshold` and `cascade.ready_uncertainty_threshold` (default profile: 0.70 and 0.35), and a `calibration` block with the Brier adjustment bounds (default profile: ceilings 0.90 know and 0.15 uncertainty, `max_inflation` 0.05, `min_transactions` 5, `lookback` 20).

PREFLIGHT picks the profile from `work_context` and `work_type` (`ThresholdLoader.select_profile_for_work`):

| `work_context` | Profile |
|---|---|
| `greenfield`, `investigation` | `exploratory` |
| `iteration`, `refactor`, anything else | `default` |

When the context left `default`, `work_type` can override: `research` and `design` give `exploratory`; `audit` and `release` give `rigorous`. An `investigation` context keeps `exploratory` even for an audit.

### 2. `calibration.yaml`: the settable surface

A sparse override file, one per scope: `~/.empirica/calibration.yaml` (global) and `<project>/.empirica/calibration.yaml` (practice). Resolution is base defaults, then persona preset, then global, then practice; the practice wins. The extension's Sentinel Tuning tab reads and writes it through the daemon; there is no CLI verb. Keys:

```yaml
preset: <persona template name>     # optional
stance: <calibration stance name>   # optional
weights:       # foundation, comprehension, execution, engagement (normalised to sum 1)
  execution: 0.30
thresholds:    # each 0-1
  ready_uncertainty: 0.30           # the CHECK gate: proceed when uncertainty <= this (default 0.35)
```

The other threshold keys are `engagement_gate` (0.60), `uncertainty_trigger` (0.40), `confidence_to_proceed` (0.75) and `signal_quality_min` (0.60). Only `ready_uncertainty` and `engagement_gate` are gates; a file that is missing or malformed leaves every default untouched. A value set during an open transaction is queued in `calibration.pending.yaml` and promoted at the next PREFLIGHT, never mid-work. The Brier adjustment still applies on top of the base, and can only tighten.

### 3. Brier adjustment

`compute_dynamic_thresholds` raises the thresholds when the practice's grounded calibration is poor, per `ai_id` and, once there are enough points, per practitioner. It never lowers them below the base and never above the ceilings from the cascade profile.

### 4. Domain checklists (`DomainRegistry`)

A checklist says which deterministic checks must pass for "done" at a `(work_type, domain, criticality)` key, and its `coverage_min` also scales the PREFLIGHT auto-proceed uncertainty threshold. Sources, later wins: shipped `empirica/config/domains/*.yaml` (`default`, `consulting`, `cybersec`, `docs`, `marketing`, `operations`, `remote-ops`, `research`), then `~/.empirica/domains/*.yaml`, then the project's `.empirica/domains.yaml`.

```yaml
# user file: one domain per file
domain: my-domain
description: "..."
applies_to_work_types: []        # empty = all
criticalities:                   # low | medium | high | critical
  medium:
    required_checks: [tests, lint]
    optional_checks: []
    thresholds: {coverage_min: 0.3, check_pass_ratio: 1.0}
    max_iterations: 5
    hints_to_ai: []
```

The project file wraps domains as `{version: "1", domains: {name: {...}}}`. Lookup falls to the next lower criticality, then to the `default` domain, then to an empty checklist. `empirica domain-validate` checks the YAML files.

---

## Calibration

How a transaction's self-assessment is scored against evidence. Four layers, applied in this order:

1. **Evidence relevance** (`core/post_test/mapper.py`, `WORK_TYPE_RELEVANCE`, in code): scales how much each evidence source counts for the transaction's `work_type`. `research` gives git, code quality, test and codebase-model evidence weight 0 and artifact counts 1.5; `infra` cuts code quality and test evidence to 0.3.
2. **Per-vector, per-phase weights**: `calibration_weights` in `project.yaml`, above.
3. **Category weights** (`mco/confidence_weights.yaml`): how much foundation, comprehension, execution and meta vectors matter. Resolution is `work_type_category_weights` (`code`, `research`, `debug`, `docs`, `comms`, `design`, `infra`, `audit`, `data`, `config`, `release`), then `domain_category_weights` (`software`, `consulting`, `research`, `operations`, `default`), then built-in defaults. Code weights execution at 0.40; research weights comprehension at 0.35 and meta at 0.25.
4. **The score**: gaps grouped by category and weighted. Lower is better. `uncertainty` is excluded (it is derived from the same gaps).

`confidence_weights.yaml` also holds tier-confidence weights (`foundation_confidence_weights` and its siblings) used by the dashboard exporter, not by scoring.

### `calibration_exclusions`: known-bad measurement windows

A list in `project.yaml` (a file local to the checkout, not committed) for a period in which a sensor was wrong and has since been fixed.

```yaml
calibration_exclusions:
  - vectors: [do, state, change]     # required, each one of the tracked vectors
    source: git                      # optional grounding source
    from: '2026-08-01'               # optional, inclusive, YYYY-MM-DD UTC
    until: '2026-09-21'              # optional, exclusive
    reason: "git evidence was graded over a window wider than the transaction"
```

An entry needs a `source` or a window. One that has neither, or that cannot be read (an unknown vector name, an empty `source`, a mapping where a list belongs), is dropped with a warning and never read as "exclude everything". Excluded observations are left out when the grounded belief is replayed over `grounded_verifications`; rows are never rewritten. The injected bias block, `grounded_bias_corrections` and `calibration-report` use the replayed value and say what they left out (`excluded` in `.breadcrumbs.yaml`, `exclusions_applied` in the report JSON). A vector with an open calibration dispute is not replayed, because disputes are not recorded per observation.

---

## `compliance.yaml`: tuning the compliance report

**File:** `<project>/.empirica/compliance.yaml`, all keys optional; absent or malformed means built-in defaults.

```yaml
skip_checks: [tech_docs]            # check ids to drop from the report
extra_checks:                       # project-specific checks, run after the built-in suite
  - id: my_docs_coverage
    runner: scripts/check.py        # script path, absolute path or shell command; called with --output json
    description: "..."              # optional
    timeout_seconds: 60             # default 60
    regulatory: {eu_ai_act: {article: "Art. 11", requirement: "..."}}   # optional framework mapping
repo_hygiene:
  license_required: true            # each defaults to true; false skips the sub-check
  changelog_required: true
  release_scripts_required: true
tech_docs:
  tool: docs-assess                 # or docpistemic, rust-docs-assess
```

A runner must print a JSON object with at least `passed` (bool) and `status` (`pass` or `fail`); other fields pass through into the report. A skipped hygiene sub-check counts as neither pass nor fail.

---

## Credentials

**File:** `credentials.yaml` (or `.json`). Found by `CredentialsLoader`, first match:

1. the path in `EMPIRICA_CREDENTIALS_PATH`
2. `.empirica/credentials.yaml|json` two directories above the `empirica` package (the checkout, when running from source)
3. `~/.empirica/credentials.yaml|json`

`${VAR}` in a value is replaced from the environment; an unset variable is left as written, with a warning. With no file, a few legacy dotfiles (`.qwen_api`, `.minimax_key`, `.gemini_api`, `.open_router_api`, ...) are read. The file is rewritten atomically with mode 0600 because it holds refresh tokens.

| Block | Holds | Precedence |
|---|---|---|
| `cortex` | `url`, `api_key`, and `oauth` (`access_token`, `refresh_token`, `expires_at`, `token_endpoint`, `client_id`, `refresh_owner`) written by `empirica auth login` | **The file wins**, per field. `CORTEX_REMOTE_URL` (or `CORTEX_URL`) and `CORTEX_API_KEY` only fill what the file lacks, and a disagreeing environment value is ignored with a warning |
| `ntfy` | `url`, `topic`, `user`, `password`, `token` | **Environment wins**: `ORCHESTRATION_NTFY_URL`, `_TOPIC`, `_USER`, `_PASS`, `_TOKEN`, then this block, then `backends.ntfy` in `~/.empirica/notify.yaml`, then the built-in server and topic |
| `providers` | per-provider `api_key`, `base_url`, `default_model`, `available_models`, `auth_method` | |

`empirica auth status` shows credential state without printing a token. `empirica auth token` prints a valid access token, refreshing it if needed.

---

## Embeddings and Qdrant

Semantic search is on when `qdrant-client` is installed, unless `EMPIRICA_ENABLE_EMBEDDINGS=false`.

**Qdrant URL**, first match: an explicit per-request URL; the installed per-project resolver hook; `EMPIRICA_QDRANT_URL`; a probe of `http://localhost:6333`. A local Qdrant on the default port needs no configuration. With no server, retrieval is skipped.

**Embedding provider** (`core/qdrant/embeddings.py`): environment overrides the file. `EMPIRICA_EMBEDDINGS_PROVIDER` is `openai`, `ollama`, `jina`, `voyage`, `local` or `auto` (default; Ollama when reachable, else a local hash). `EMPIRICA_EMBEDDINGS_MODEL` names the model (defaults per provider, for example `text-embedding-3-small`, `qwen3-embedding:0.6b`, `jina-embeddings-v3`, `voyage-3-lite`). `EMPIRICA_OLLAMA_URL` defaults to `http://localhost:11434`. API keys: `OPENAI_API_KEY`, `JINA_API_KEY`, `VOYAGE_API_KEY`. The file form is an `embeddings:` section in `~/.empirica/config.yaml` with `provider`, `model`, `ollama_url`, `jina_api_key`, `voyage_api_key`; the legacy fallback is `~/.empirica/embeddings.conf` (`key=value` lines).

---

## Hooks and the plugin

| Setting | Where |
|---|---|
| Sentinel on/off, pause | `~/.empirica/sentinel_enabled`, `~/.empirica/sentinel_paused*`, `EMPIRICA_SENTINEL_*`; see the [Sentinel Gate Reference](SENTINEL_GATE_REFERENCE.md) |
| Statusline mode | `~/.empirica/statusline_mode`, `EMPIRICA_STATUS_MODE`; see the [Statusline Reference](STATUSLINE_REFERENCE.md) |
| Stop-hook turn limits | `transaction-enforcer.py` warns after 12 turns with an open transaction and blocks stopping after 20. Override in `.empirica-project/PROJECT_CONFIG.yaml` (or `PROJECT_CONFIG.yaml`) in the working directory with `transaction: {soft_reminder_turns: N, max_transaction_turns: N}`, or with `EMPIRICA_TX_SOFT_TURNS` and `EMPIRICA_TX_HARD_TURNS`, which win |
| Workflow protocol | `workflow-protocol.yaml`, searched in the project then `~/.empirica/`; created by `/ewm-interview`, loaded at session start |
| Trusted hosts | `~/.empirica/trusted_hosts`: a remote, container or CI host listed there is annotated trusted in Sentinel messages |

---

## Other environment variables

Variables that change behaviour without a file equivalent. The full list is [Environment Variables](ENVIRONMENT_VARIABLES.md).

| Variable | Effect |
|---|---|
| `EMPIRICA_CALIBRATION_FEEDBACK` | `false` removes calibration feedback from PREFLIGHT and CHECK output (default `true`). It does not affect POSTFLIGHT grounding or Sentinel gating, which always use raw vectors |
| `EMPIRICA_AUTOPILOT_MODE` | `true` makes the CHECK decision binding: a submitted decision that differs from the computed one is replaced by it (default `false`) |
| `EMPIRICA_ENFORCE_CASCADE_PHASES` | `true` enforces transaction phase ordering in signed git operations |
| `EMPIRICA_CORTEX_URL` | Cortex server that receives calibration feedback at POSTFLIGHT when the grounded calibration score is below 0.3 (default `http://localhost:8420`) |
| `EMPIRICA_HARNESS` | Which harness `empirica setup` configures and the hooks target (default `claude-code`) |
| `EMPIRICA_AI_ID` | Overrides the practice id the statusline renders |

---

## For developers: the loaders

| Module | Loads | Read by |
|---|---|---|
| `path_resolver.py` | root and database resolution, `config.yaml` `root` | everything |
| `project_config_loader.py` | `project.yaml` into `ProjectConfig` | bootstrap, session create, goals, `project-update`, hooks |
| `threshold_loader.py` | `mco/cascade_styles.yaml` (singleton; profile switching, overrides) | PREFLIGHT, CHECK, dynamic thresholds, Sentinel hooks |
| `mco_loader.py` | `model_profiles`, `personas`, `epistemic_conduct`, `ask_before_investigate`, `protocols`, `confidence_weights` (lazy) | bootstrap, monitor, `empirica config` |
| `profile_loader.py` | `investigation_profiles.yaml` | checkpoint commands |
| `domain_registry.py` | domain checklists | Sentinel hook, compliance loop, `domain-*` commands |
| `credentials_loader.py` | credentials, Cortex and ntfy resolution, OAuth | auth, serve, hooks |
| `database_config.py` | database backend | `SessionDatabase` |
| `hygiene_policy.py` | `hygiene_policy` | sources check |
| `compliance_report_commands.py` (command handler) | `compliance.yaml` | `compliance-report` |
| `core/calibration_config.py` | `calibration.yaml` layers | CHECK, PREFLIGHT, Sentinel, the daemon API |

```python
from pathlib import Path
from empirica.config.project_config_loader import load_project_config
from empirica.config.threshold_loader import ThresholdLoader

config = load_project_config(Path("."))          # None when there is no project.yaml
core_paths = config.get_subject_info("core")["paths"]

loader = ThresholdLoader.get_instance()
uncertainty_high = loader.get("uncertainty.high", 0.70)
```

---

## Troubleshooting

- **Which store is this command using?** Run `debug_paths()` above. If the error says it cannot determine `sessions.db`, its message lists each source it tried; set `EMPIRICA_SESSION_DB` or run from inside the practice directory.
- **A setting seems ignored.** Check the table at the top: `config.yaml` `paths` and `settings` are not read, `empirica config KEY VALUE` writes nothing, and the Sentinel's `EMPIRICA_SENTINEL_LOOPING` is overridden by `~/.empirica/sentinel_enabled`.
- **Cortex calls use the wrong key.** `credentials.yaml` wins over `CORTEX_API_KEY`; unset the stale variable.
- **Validate the shipped MCO files.** `empirica config --validate`.

---

## See also

- [Environment Variables](ENVIRONMENT_VARIABLES.md)
- [Sentinel Gate Reference](SENTINEL_GATE_REFERENCE.md)
- [Statusline Reference](STATUSLINE_REFERENCE.md)
- [CLI Commands Reference](../human/developers/CLI_COMMANDS_UNIFIED.md)
- [MCP Server Reference](../human/developers/MCP_SERVER_REFERENCE.md)
