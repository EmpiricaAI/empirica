# Empirica CLI UX Issues Found - 2026-01-13

Session: Testing new user experience and upgrade paths

## Summary

| # | Issue | Severity | Category |
|---|-------|----------|----------|
| 1 | Missing migration for `auto_captured_issues` table | **High** | Upgrade |
| 2 | No duplicate project prevention | Medium | Data Integrity |
| 3 | Noisy optional dependency warnings | Low | UX |
| 4 | Git notes failure with misleading success | Medium | Error Handling |
| 5 | No git user detection before git notes | Medium | Error Handling |
| 6 | Inconsistent error handling across commands | Low | Consistency |

---

## Issue 1: Missing Database Migration (HIGH)

**File:** See `.github/ISSUE_migration_bug.md` for full details

**Problem:** `project-bootstrap` crashes when upgrading from older versions:
```
❌ Project bootstrap error: no such table: auto_captured_issues
```

**Root Cause:** Table created only in `IssueCapture.__init__()`, not during DB init.

**Fix:** Add proper migration system with versioned SQL scripts.

---

## Issue 2: No Duplicate Project Prevention (MEDIUM)

**Problem:** Can create multiple projects with same name for same repo:
```bash
empirica project-create --name empirica --repos '["github.com/foo/bar"]'
empirica project-create --name empirica --repos '["github.com/foo/bar"]'
empirica project-init  # Creates another one!
# Now have 3 projects all named "empirica"
```

**Impact:** Confuses session linking, orphans data, unclear which project is "active"

**Fix Options:**
1. Add unique constraint on (name, repo) pair
2. `project-init` should check for existing project first
3. Add `--force` flag to create duplicate if intentional

---

## Issue 3: Noisy Optional Dependency Warnings (LOW)

**Problem:** When optional features not configured, stderr shows:
```
Auto-embed failed: No module named 'qdrant_client'
Eidetic ingestion failed: No module named 'qdrant_client'
Qdrant not available for noetic embedding
```

**Impact:** Confuses new users who haven't configured Qdrant (which is optional)

**Fix Options:**
1. Only show if feature explicitly enabled in config
2. Show once per session, not on every command
3. Add `--quiet` flag to suppress

---

## Issue 4: Git Notes Failure with Misleading Success (MEDIUM)

**Problem:** When git notes fails, some commands still say success:

`goals-create`:
```
Failed to store goal in git: Command '...' returned non-zero exit status 128.
{"ok": true, ...}  # Still says ok!
```

`preflight-submit` (better):
```
{"ok": true, "storage_layers": {"git_notes": false},
 "message": "PREFLIGHT assessment submitted to database and git notes"}
 # Message says git notes, but storage_layers shows false
```

**Fix:**
1. Consistent `storage_layers` field across all commands
2. Message should reflect actual storage: "submitted to database (git notes unavailable)"
3. Add `"partial_success": true` when some storage layers fail

---

## Issue 5: No Git User Detection (MEDIUM)

**Problem:** Git notes requires configured user, but error is raw git output:
```
Author identity unknown
*** Please tell me who you are.
Run
  git config --global user.email "you@example.com"
  git config --global user.name "Your Name"
```

**Impact:** New users see cryptic error, don't know why or if it matters.

**Fix:**
1. Check `git config user.email` before attempting git notes
2. If not set, show friendly message: "Git notes disabled - configure with: git config..."
3. Add to onboard checklist

---

## Issue 6: Inconsistent Error Handling (LOW)

**Problem:** Commands handle errors differently:
- `goals-create`: Prints warning, returns `ok: true`
- `preflight-submit`: Returns `storage_layers` breakdown
- `finding-log`: Returns error for missing required params but help shows them optional

**Fix:** Establish consistent error handling pattern:
```json
{
  "ok": true,
  "warnings": ["git notes unavailable"],
  "storage_layers": {...},
  "message": "Accurate message reflecting actual outcome"
}
```

---

## Recommendations

### Short-term (v1.3.4)
1. Add `CREATE TABLE IF NOT EXISTS` for all tables in `SessionDatabase.__init__()`
2. Check for git user before git notes, skip gracefully
3. Fix misleading success messages

### Medium-term (v1.4.0)
1. Implement proper migration system with versioned scripts
2. Add `empirica db-migrate` command
3. Add unique constraint on projects

### Long-term
1. Consider SQLAlchemy Alembic for migrations
2. Add `empirica doctor` command to diagnose common issues
3. Add optional telemetry to catch these issues earlier

---

## Test Session Info

```
AI: claude-code (Opus 4.5)
Session: 31908f1b-613f-45e9-b562-103a8472d9c2
Project: empirica (1614b274-88e2-485f-990d-33534375d267)
Empirica Version: 1.3.3
Date: 2026-01-13
```
