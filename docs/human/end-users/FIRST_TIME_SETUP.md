# First-Time Setup Guide

**For new Empirica users.** Three commands to a working install, then you're measuring.

---

## TL;DR

```bash
pip install empirica                 # 1. Install the CLI
cd your-project                       # 2. Any git repo
empirica project-init                 # 3. Mints .empirica/ + project.yaml
empirica setup-claude-code            # (optional, Claude Code users) installs plugin + hooks
empirica diagnose                     # Sanity check — green = ready
```

`project-init` is the step new users miss most. Without it, every other
command fails with `Cannot determine sessions.db path`. The
`diagnose` command walks every integration check and points at the
exact missing step if anything's off.

---

## Data Isolation — What You Get

Empirica writes data in **three places**, each isolated by design:

| Path | Contents | In git? | Pushed by default? |
|---|---|---|---|
| `<repo>/.empirica/` | Project DB + project.yaml + credentials cache | `.empirica/*` gitignored except `project.yaml` (and `credentials.yaml` if you opt in) | n/a |
| `<repo>/.git/refs/notes/empirica_*` | Per-artifact git notes (findings, decisions, etc.) | yes (in `.git/`) | **No** — `git push origin refs/notes/empirica_*` to share |
| `~/.empirica/` | User-tenant config: cortex creds, ntfy creds, registry of projects | n/a | n/a |

**Clean-slate guarantees:**
- Cloning a repo with Empirica history = empty `.empirica/` (gitignored)
- Cloning + `git fetch refs/notes/*` pulls in the team's epistemic trail
- Your user-tenant config in `~/.empirica/` never travels with a repo

---

## Step-by-Step

### 1. Install

```bash
pip install empirica
# (optional, for Claude Desktop / Cursor / Windsurf MCP):
pip install empirica-mcp
```

Verify:
```bash
empirica --version
```

### 2. Initialize the project

```bash
cd your-project          # any git repo
empirica project-init
```

This creates:
- `.empirica/` — gitignored except for `project.yaml`
- `.empirica/project.yaml` — project identity (committed):
  - `project_id` (UUID)
  - `name`, `description`, `repository`
  - `ai_id` — **derived from project basename**, e.g. `your-project`. Strips
    `empirica-` prefix if present so `empirica-cortex` → `cortex`. This is
    how your AI is addressed in cross-AI orchestration.
- `.empirica/sessions/sessions.db` — created on first session-create

### 3. (Optional) Set up Claude Code

If you're using Claude Code as your AI:

```bash
empirica setup-claude-code
```

This installs the empirica plugin to `~/.claude/plugins/local/empirica/`,
writes `~/.claude/CLAUDE.md`, registers PreToolUse / PreCompact /
SessionStart hooks in `~/.claude/settings.json`, and registers the MCP
server in `~/.claude/mcp.json`.

It also runs an **interactive credentials wizard** if you don't already
have `~/.empirica/credentials.yaml`. The wizard prompts for:
- **Cortex** (orchestration API) — URL + `ctx_…` API key — **optional**
- **ntfy** (push wake bridge) — URL + topic + auth token — **optional**

**Both creds are optional.** Empirica core (artifacts, goals,
calibration, project-search, sentinel gating) works fully without them.
You only need cortex+ntfy if you're opting into the cross-AI mesh layer
(peer-AI coordination, push-wake on inbox events, the browser
extension's ECO triage). If you skip the wizard, mesh features stay
inert; everything local still works. You can always re-run
`empirica setup-claude-code --force` later to add the creds when you
want them.

After the api_key prompt (if you supply one), the wizard fetches
`/v1/tenant/me` and persists `{org_id, tenant_slug, mesh_id_prefix}` to
your project.yaml so your AI gets fully-qualified mesh addressing on
first run. Skip the wizard with `--skip-credentials` if you already
have creds in env vars or files — or to stay fully local.

### 4. Diagnose

```bash
empirica diagnose
```

Walks ~10 checks (Python version, CLI on PATH, plugin install,
statusline, hooks, MCP server, cortex reachability, …) and prints
PASS / FAIL / WARN with actionable hints. Run this when something
isn't working.

---

## Your First Transaction

Empirica's measurement unit is the **transaction**: `PREFLIGHT → noetic
work → CHECK → praxic work → POSTFLIGHT`. Open the window, do the
work, close it.

```bash
# Create a session (transaction lifecycle hangs off this)
empirica session-create --ai-id $(basename $PWD) --output json
# → returns session_id

# Open a measurement window
empirica preflight-submit - << 'EOF'
{
  "task_context": "Fix the auth bug",
  "vectors": {"know": 0.5, "uncertainty": 0.5, "context": 0.6},
  "reasoning": "I've seen this area before but haven't read the actual code yet."
}
EOF

# Investigate (noetic phase) — log as you discover
empirica finding-log --finding "Token validation skips audience check" --impact 0.7
empirica unknown-log --unknown "Does the refresh path also skip it?"

# Gate the noetic → praxic transition
empirica check-submit - << 'EOF'
{
  "vectors": {"know": 0.8, "uncertainty": 0.2, "context": 0.85},
  "reasoning": "Understand the code path, ready to fix."
}
EOF

# Do the work (write code, run tests, commit)
# ...

# Close the measurement window
empirica postflight-submit - << 'EOF'
{
  "vectors": {"know": 0.9, "uncertainty": 0.15, "context": 0.9, "completion": 1.0},
  "reasoning": "Fix shipped, tests pass. Compare to PREFLIGHT — this is the learning delta."
}
EOF
```

PREFLIGHT → POSTFLIGHT delta is your **learning trajectory**. POSTFLIGHT
also collects grounded observations (tests, git, code-quality) from
deterministic services and surfaces divergence between your beliefs and
what the services measured — that's your **calibration signal**.

---

## Sharing Epistemic Data (Optional)

Empirica writes artifacts to `refs/notes/empirica_*` in your local git
notes. These are **not pushed automatically**. To share with a team:

```bash
# Push your epistemic trail
git push origin 'refs/notes/empirica_*:refs/notes/empirica_*'

# Pull a teammate's
git fetch origin 'refs/notes/empirica_*:refs/notes/empirica_*'
```

For shared cortex-mediated orchestration (AIs proposing work to each
other), see `docs/architecture/EVENT_LISTENER.md`.

---

## What Gets Created

```
your-project/
├── .empirica/                          # mostly gitignored
│   ├── project.yaml                    # ✅ committed (project identity)
│   ├── sessions/sessions.db            # ❌ gitignored
│   ├── credentials.yaml                # ❌ gitignored (or in ~/.empirica/)
│   └── breadcrumbs.yaml                # ❌ gitignored (per-AI calibration)
│
├── .beads/                             # optional BEADS issue tracker
│   ├── config.yaml                     # ✅ committed
│   ├── beads.db                        # ❌ gitignored
│   └── issues.jsonl                    # ✅ optional commit
│
└── .git/refs/notes/empirica_*          # ✅ in git, ❌ not pushed by default
```

---

## FAQ

**Q: Will I see other users' data when I clone a repo?**
No. `.empirica/sessions/sessions.db` is gitignored. The committed parts
(`project.yaml`, `.beads/config.yaml`) carry no per-user data.

**Q: Can I use Empirica in multiple repos?**
Yes — each repo is independent. The user-tenant config in `~/.empirica/`
is shared across all of them; per-project state stays per-project.

**Q: Where does `ai_id` come from?**
`empirica project-init` derives it from the exact project root
basename (e.g. `empirica-cortex` — `empirica-` prefix kept) and
writes it to `project.yaml`. To override: `--ai-id custom-name` on
project-init, or hand-edit `project.yaml`. On the wire, peers
address you by the canonical 3-form
`<org>.<tenant>.<exact-project-name>`; your local `ai_id` is the
third component.

**Q: How do I reset?**
```bash
# Delete one project's history
rm -rf .empirica/sessions/   # losing transactions
git update-ref -d refs/notes/empirica_findings  # losing artifact notes

# Fully reset (last resort)
rm -rf .empirica/ ~/.empirica/
empirica project-init  # start over
```

---

## Next Steps

1. **CLI basics:** [04_QUICKSTART_CLI.md](04_QUICKSTART_CLI.md)
2. **Logging + finding walkthrough:** [LOGGING_AND_FINDING.md](LOGGING_AND_FINDING.md)
3. **Understand the 13 vectors:** [05_EPISTEMIC_VECTORS_EXPLAINED.md](05_EPISTEMIC_VECTORS_EXPLAINED.md)
4. **Plain-English overview:** [EMPIRICA_EXPLAINED_SIMPLE.md](EMPIRICA_EXPLAINED_SIMPLE.md)
5. **Multi-project users — lifecycle:** [PROJECT_LIFECYCLE.md](PROJECT_LIFECYCLE.md)
6. **Want the optional mesh layer (cross-AI coordination)?** [MESH_SETUP.md](MESH_SETUP.md)
7. **Troubleshooting:** [03_TROUBLESHOOTING.md](03_TROUBLESHOOTING.md)

---

## Need Help?

- **`empirica diagnose`** for integration checks
- **`empirica --help`** or **`empirica <command> --help`**
- **GitHub Issues:** https://github.com/Nubaeon/empirica/issues
