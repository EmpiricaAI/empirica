# `empirica diagnose --frontend ecodex`

Health-check for ecodex installs (sibling of `empirica diagnose`'s
default Claude Code mode).

## What it checks

| Check | What it tests | Fix on failure |
|---|---|---|
| Python version | Interpreter is ≥ 3.10 | Reinstall empirica with newer Python |
| empirica CLI on PATH | `empirica` resolves | Reinstall empirica |
| ecodex plugin installed | `~/.codex/plugins/cache/nubaeon/empirica/<v>/.codex-plugin/plugin.json` exists + parses | `./ecodex/scripts/install.sh` |
| ecodex plugin enabled in config | `[plugins."empirica@nubaeon"]` block in `~/.codex/config.toml` | Add the block manually or reinstall |
| statusline runtime pipes session_id | ecodex's `plugin_statusline_runtime.rs` doesn't use `Stdio::null()` for the script subprocess | Switch to `Stdio::piped()` and write `{"session_id":...,"cwd":...}` to child stdin |
| statusline script runnable | The bundled `statusline_empirica.py` executes and produces output | Re-run installer; check `chmod +x` |
| translator listening | TCP probe on `127.0.0.1:18080` | Start translator: `~/.local/bin/start-kimi-translator.sh &` |
| translator /healthz | HTTP `GET /healthz` returns 200 | Rebuild translator: `cargo build -p codex-empirica-translator --release` |
| provider env keys | Each `[model_providers.*]` block's `env_key` is set in `~/.codex/.env` or env | Add to `~/.codex/.env` (chmod 600) |
| cargo on PATH | `cargo` resolves | Install Rust via rustup |
| cargo fmt clean | `cargo fmt --check` exits 0 in `codex-rs/` | Run `cargo fmt` from `codex-rs/` |
| cargo check passes | `cargo check --workspace` exits 0 | Address compile errors shown by `cargo check` |

## Modes

- **`--fast`** — skip slow checks (`cargo fmt`, `cargo check`). Useful when
  the `/diagnose` skill is walking a user through the report
  interactively. CI-like usage should leave `--fast` off.
- **`--output json`** — machine-readable. The `/diagnose` skill consumes
  this so the agent's reasoning is grounded in the same observations
  CI sees.

## Environment

- `ECODEX_REPO_ROOT` — point to your ecodex checkout when running the
  diagnostic from outside the repo. Without it, the cargo + statusline-
  runtime checks fall back to `~/empirical-ai/ecodex` and skip if missing.

## Architecture

This module is the **truth source**. Each check is a deterministic Python
function returning a `CheckResult` (`name, status, detail, hint, data`).
The `/diagnose` skill in `codex-empirica-plugin/skills/diagnose/` is a
thin reasoning layer that runs `empirica diagnose --frontend ecodex
--output json --fast`, walks failures with the user, and proposes the
`hint` as a fix.

This division means:
- Agents can't fake or skip checks (the script runs the same code paths
  regardless of who invokes it)
- CI can gate on `empirica diagnose --frontend ecodex; echo $?`
- Post-install verification is one command
- Every new ecodex feature can ship with a paired check function in
  `diagnose_ecodex.py`

## Adding a new check

1. Write a `def check_ecodex_<thing>() -> CheckResult` in
   `empirica/cli/command_handlers/diagnose_ecodex.py`. Return a
   `CheckResult` with status one of `PASS / FAIL / WARN / SKIP`. Provide
   `detail` (factual observation) and `hint` (actionable fix) on
   non-PASS.
2. Append to `run_all_checks_ecodex()` in dependency order. Earlier
   checks failing should let later checks SKIP themselves with a
   pointer to the upstream failure.
3. Add a one-line entry to the table in this doc.
4. If the check is slow (>2s typical), gate it behind the `fast`
   parameter so the skill's interactive walk-through stays snappy.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All checks passed (`PASS` or `SKIP`) |
| 1 | One or more `FAIL` |
| 2 | One or more `WARN` (no `FAIL`) |

## Sister tools

- `empirica diagnose` (default) — Claude Code integration
- `empirica doctor` — desktop / general install health (empirica-mcp,
  Cortex reachability, sync state)
- `empirica compliance-report` — language-level lint/types/tests against
  your project. Doctor checks integration; compliance-report checks code.
