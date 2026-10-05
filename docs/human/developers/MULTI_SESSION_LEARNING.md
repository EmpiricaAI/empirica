# Multi-Session Learning

**Empirica's compounding mechanism:** learning doesn't reset between
sessions. Findings persist, vectors start higher, transactions converge
faster. The AI gets smarter on a given project the longer you've worked
in it.

---

## The Mechanic

Each transaction carries triple linkage on every noetic artifact:

- `session_id` — temporal context (which working window)
- `transaction_id` — measurement context (PREFLIGHT → POSTFLIGHT)
- `goal_id` — structural context (which objective)

When Session N+1 opens, `project-bootstrap` loads the recent findings,
open unknowns, dead-ends, and decisions from prior transactions —
~800–2000 tokens of compressed prior state — so PREFLIGHT starts
informed.

```
Session 1, Transaction 1
  PREFLIGHT:  know=0.4, uncertainty=0.9
  Noetic:     log findings, unknowns, dead-ends
  POSTFLIGHT: know=0.65, uncertainty=0.55
              → ~450 tokens of artifacts stored

Session 2, Transaction 2 (project-bootstrap loaded)
  PREFLIGHT:  know=0.70, uncertainty=0.40   ← Starts higher
  Noetic:     fewer gaps to fill, targeted reads
  POSTFLIGHT: know=0.85, uncertainty=0.20   ← Faster convergence

Session N, Transaction N (compound)
  PREFLIGHT:  know=0.85, uncertainty=0.20
  Noetic:     surgical investigation of remaining unknowns
  POSTFLIGHT: know=0.95, uncertainty=0.10
```

Each transaction builds on every transaction that came before it.

---

## Worked Example — Bug Fix Across 2 Sessions

**Session 1:**
```bash
empirica session-create --ai-id $(basename $PWD)
empirica preflight-submit - << 'EOF'
{
  "task_context": "Fix auth bug in login flow",
  "vectors": {"know": 0.7, "uncertainty": 0.5, "context": 0.6}
}
EOF

empirica finding-log --finding "Login uses JWT, 1h expiry" --impact 0.7
empirica finding-log --finding "Refresh endpoint at /auth/refresh" --impact 0.5
empirica unknown-log --unknown "How are expired tokens handled?"
empirica unknown-log --unknown "Is token revocation implemented?"

empirica postflight-submit - << 'EOF'
{
  "vectors": {"know": 0.8, "uncertainty": 0.4, "context": 0.75}
}
EOF
# All artifacts stored — refs/notes/empirica_findings + sessions.db
```

**Session 2 (next day):**
```bash
empirica session-create --ai-id $(basename $PWD)
empirica project-bootstrap     # loads Session 1's findings + unknowns

# PREFLIGHT informed by Session 1's artifacts
empirica preflight-submit - << 'EOF'
{
  "task_context": "Continue auth bug — target Session 1's open unknowns",
  "vectors": {"know": 0.80, "uncertainty": 0.30, "context": 0.80}
}
EOF
# ↑ Starts where Session 1 ended

empirica finding-log --finding "Expired tokens → 401, redirect to /login" --impact 0.6
empirica finding-log --finding "Token revocation via Redis blacklist (24h TTL)" --impact 0.7

# Resolve the open unknowns from Session 1
empirica unknown-resolve --unknown-id <ID> --resolution "401 + redirect; see auth.py:142"
empirica unknown-resolve --unknown-id <ID> --resolution "Redis blacklist; see revoke.py"

empirica postflight-submit - << 'EOF'
{
  "vectors": {"know": 0.92, "uncertainty": 0.10, "context": 0.90, "completion": 1.0}
}
EOF
```

**Result:** Two focused sessions, each with clear deltas, instead of one
long uncertain one.

---

## Multi-AI Coordination

Two AIs working the same repo see each other's artifacts (via git notes
if pushed):

```
AI A (Day 1): Investigates database schema.
              POSTFLIGHT logs 8 findings about indexes, FK constraints.
              empirica sync-push

AI B (Day 2): empirica project-bootstrap loads AI A's findings.
              PREFLIGHT starts with high context on the database.
              Focuses on application layer instead of re-discovering schema.

AI A (Day 3): Returns. project-bootstrap shows AI B's application-layer findings.
              Full picture now.
```

**Push policy:** artifacts are local until you opt in:
```bash
empirica sync-config notes_remote <remote>   # once; there is no default remote
empirica sync-push                           # pushes refs/notes/empirica/* (+ breadcrumbs)
empirica sync-pull --rebuild                 # fetches them back and rebuilds SQLite
```

---

## Best Practices

### Log specifically

```bash
# ❌ Vague — useless to future you
empirica finding-log --finding "Auth works"

# ✅ Specific — actionable
empirica finding-log --finding "Auth uses OAuth2 with Google; access tokens 1h TTL, refresh in Redis 30d TTL" --impact 0.7
```

### Log unknowns early

```bash
empirica unknown-log --unknown "How are secrets rotated?"
# Next session's project-bootstrap surfaces this as an investigation target.
```

### Log dead-ends with reasons

```bash
empirica deadend-log \
  --approach "Looked for token rotation in main app code" \
  --why-failed "Logic lives in scheduler service, not main app"
# Future you (or another AI) won't waste time on the same hunt.
```

### POSTFLIGHT honestly

```bash
# If uncertainty is still high, don't pretend it isn't
empirica postflight-submit - << 'EOF'
{
  "vectors": {"know": 0.75, "uncertainty": 0.45, "completion": 0.7},
  "reasoning": "Found root cause but didn't validate fix end-to-end. Test coverage gap."
}
EOF
```

The system rewards calibration (belief matching observation), not high
scores.

---

## Cross-Project Compounding

```bash
# Search this project
empirica project-search --task "auth flow"

# Search across all projects' shared/public artifacts
empirica project-search --task "auth flow" --global
```

Opt artifacts in via `--visibility shared` (within-org) or
`--visibility public` (anyone) on `*-log` commands. Default is
`local` — explicit choice to share cross-project.

---

## What Gets Stored

| Storage | What | Where |
|---|---|---|
| SQLite | Sessions, transactions, artifacts | `.empirica/sessions/sessions.db` |
| Git notes | Same artifacts (mirror) | `refs/notes/empirica/<ns>/...` |
| Qdrant | Embeddings for semantic search | per-project + `global_learnings` |
| Breadcrumbs | Per-AI calibration | `.breadcrumbs.yaml` (repo root) |

The SQLite is canonical; git notes are the shareable mirror.

---

## See Also

- **CASCADE workflow:** [../end-users/EMPIRICA_NATURAL_LANGUAGE_GUIDE.md](../end-users/EMPIRICA_NATURAL_LANGUAGE_GUIDE.md)
- **Bootstrap:** `empirica project-bootstrap --help`
- **Cross-project search:** [../../reference/api/CROSS_PROJECT.md](../../reference/api/CROSS_PROJECT.md)
- **AI self-management:** [AI_SELF_MANAGEMENT.md](AI_SELF_MANAGEMENT.md)
