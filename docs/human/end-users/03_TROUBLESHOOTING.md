# Troubleshooting Guide

**Common issues and solutions for Empirica**

---

## Installation Issues

### Problem: Command not found
```bash
empirica: command not found
```

**Cause:** Pip binaries not in PATH

**Solution:**
```bash
# Find pip install location
pip show empirica | grep Location

# Add to PATH (bash)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# Add to PATH (zsh)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### Problem: Module not found
```python
ModuleNotFoundError: No module named 'empirica'
```

**Cause:** Wrong Python environment or installation failed

**Solution:**
```bash
# Check Python version (3.10+ required)
python --version

# Verify installation
pip show empirica

# Reinstall if needed
pip uninstall empirica
pip install empirica
```

---

## Database Issues

### Problem: Database locked
```
sqlite3.OperationalError: database is locked
```

**Cause:** Multiple processes accessing database simultaneously

**Solution:**
```bash
# Check for running Empirica processes
ps aux | grep empirica

# Kill if necessary
pkill -f empirica

# Restart command
```

### Problem: Cannot open database
```
sqlite3.OperationalError: unable to open database file
```

**Cause:** Permission issues or missing directory

**Solution:**
```bash
# Create directory with correct permissions
mkdir -p ~/.empirica
chmod 755 ~/.empirica

# Check ownership
ls -la ~/.empirica

# Fix if needed
chown -R $USER:$USER ~/.empirica
```

### Problem: Database corrupted
```
sqlite3.DatabaseError: database disk image is malformed
```

**Cause:** Unexpected shutdown or disk issues

**Solution:**
```bash
# Database is per-project at .empirica/sessions/sessions.db
# Backup current database
cp .empirica/sessions/sessions.db .empirica/sessions/sessions.db.backup

# Try to recover
sqlite3 .empirica/sessions/sessions.db "PRAGMA integrity_check;"

# If unrecoverable, start fresh (loses project history)
rm .empirica/sessions/sessions.db

# Create new database
empirica session-create --ai-id recovery
```

---

## Session Issues

### Problem: Session not found
```
Error: Session with ID 'xyz' not found
```

**Cause:** Invalid session ID or database issue

**Solution:**
```bash
# List all sessions
empirica sessions-list

# Verify session ID is correct
# If session missing, check database:
sqlite3 .empirica/sessions/sessions.db "SELECT * FROM sessions;"
```

### Problem: Cannot create session
```
Error: Failed to create session
```

**Cause:** Database write permission or disk space issue

**Solution:**
```bash
# Check disk space
df -h ~/.empirica

# Check database permissions
ls -la .empirica/sessions/sessions.db

# Fix permissions
chmod 644 .empirica/sessions/sessions.db

# Check if directory is writable
touch ~/.empirica/test && rm ~/.empirica/test
```

---

## Git Integration Issues

### Problem: Git notes not working
```
fatal: ref refs/notes/empirica_findings does not exist
```

**Cause:** Git notes namespace not initialized. Empirica uses one ref
per artifact type (`refs/notes/empirica_findings`,
`refs/notes/empirica_decisions`, etc.).

**Solution:**
```bash
# Logging any artifact creates the ref
empirica finding-log --finding "Init test" --impact 0.1

# Inspect what exists
git for-each-ref refs/notes/empirica_*
```

### Problem: Cannot push notes
```
error: cannot update ref 'refs/notes/empirica_findings'
```

**Cause:** Git repository not configured or no commits

**Solution:**
```bash
# Ensure you're in a git repository
git status

# Make initial commit if needed
git commit --allow-empty -m "Initial commit"

# Create checkpoint
empirica checkpoint-create --session-id <SESSION_ID>
```

### Problem: Git identity not configured
```
fatal: unable to auto-detect email address
```

**Cause:** Git user not configured

**Solution:**
```bash
git config --global user.name "Your Name"
git config --global user.email "your@email.com"
```

---

## Assessment Issues

### Problem: PREFLIGHT not working
```
Error: Could not submit PREFLIGHT assessment
```

**Cause:** Missing session, no project initialized, or invalid JSON

**Solution:**
```bash
# Verify session exists
empirica sessions-show --session-id <SESSION_ID>

# Ensure project is initialized in your git repo
empirica project-init

# Try with JSON stdin (recommended)
empirica preflight-submit - << 'EOF'
{
  "task_context": "Test task",
  "vectors": {"know": 0.5, "uncertainty": 0.5}
}
EOF
```

### Problem: Invalid vector values
```
Error: Vector 'know' must be between 0.0 and 1.0
```

**Cause:** Incorrect value format in JSON input (e.g., > 1.0, nested
under a `foundation` key, missing).

**Solution:** All 13 vectors live flat at the root of `vectors`. There
are no tier sub-objects.
```json
{
  "vectors": {
    "engagement": 0.8,
    "know": 0.7,
    "do": 0.6,
    "context": 0.5,
    "uncertainty": 0.3
  }
}
```
You don't need all 13 every time — `know`, `do`, `context`,
`engagement`, `uncertainty` plus phase-relevant subset is sufficient.

---

## Project Issues

### Problem: Project not found
```
Error: Project with ID 'xyz' not found
```

**Cause:** Invalid project ID or not in git repository

**Solution:**
```bash
# List all projects
empirica project-list

# Create new project if needed
empirica project-create --name "My Project"

# Project auto-detection requires git
git remote -v  # Check git remotes
```

### Problem: Project bootstrap shows nothing
```
Warning: No breadcrumbs found for project
```

**Cause:** No findings/unknowns logged yet

**Solution:**
```bash
# Log some findings (session_id auto-derived from active transaction)
empirica finding-log --finding "Initial exploration complete" --impact 0.3

# Bootstrap again (auto-detects project from CWD)
empirica project-bootstrap --output json
```

---

## Goal Issues

### Problem: Cannot create goal
```
Error: Failed to create goal
```

**Cause:** Missing required fields or no active transaction (when not
passing `--session-id`).

**Solution:** Goals can be created with flags (most common) or JSON
stdin.
```bash
# Flags (most common — session_id auto-derived if a transaction is open)
empirica goals-create --objective "Clear objective" \
  --description "Markdown body with success criteria, links, context"

# Or with explicit session-id
empirica goals-create --session-id <SESSION_ID> --objective "..."
```
`--description` accepts up to 8000 chars of markdown and is rendered
in the TUI and extension.

### Problem: Task not completing
```
Error: Cannot complete task
```

**Cause:** Invalid task_id or missing required evidence flag.

**Solution:**
```bash
# List all tasks for goal
empirica goals-get-tasks --goal-id <GOAL_ID>

# Complete with evidence (commit SHA, test result, file path)
empirica goals-complete-task --task-id <ID> --evidence "commit abc123"
```
Note: the flag is `--task-id`, not `--goal-id`.

---

## BEADS Integration Issues

### Problem: Cannot discover goals
```
Error: No goals found
```

**Cause:** No goals published to git notes or wrong remote

**Solution:**
```bash
# Check git notes
git notes --ref=empirica_goals list

# Fetch from remote
git fetch origin refs/notes/empirica_goals:refs/notes/empirica_goals

# Try discovery again
empirica goals-discover
```

### Problem: Cannot claim goal
```
Error: Goal already claimed
```

**Cause:** Another agent already claimed this goal

**Solution:**
```bash
# Check goal status
empirica goals-list

# Find unclaimed goals
empirica goals-ready

# Or resume an existing goal
empirica goals-resume --goal-id <GOAL_ID> --ai-id myai
```

---

## Performance Issues

### Problem: Commands are slow
```
(Taking >10 seconds to respond)
```

**Cause:** Database size or git repository size

**Solution:**
```bash
# Check database size
ls -lh .empirica/sessions/sessions.db

# Vacuum database to optimize
sqlite3 .empirica/sessions/sessions.db "VACUUM;"

# Note: sessions are auto-managed. Old sessions are closed automatically.
```

---

## Output Issues

### Problem: JSON output malformed
```
Error parsing JSON response
```

**Cause:** Mixed stdout/stderr or encoding issue

**Solution:**
```bash
# Redirect stderr
empirica sessions-list --output json 2>/dev/null

# Use jq for validation
empirica sessions-list --output json | jq .

# Check for binary output issues
empirica sessions-list --output json | cat -v
```

---

## Getting More Help

### Built-in Diagnostics

```bash
empirica diagnose       # Claude Code integration health (~10 checks)
empirica doctor         # General install health
empirica system-status  # Runtime status (loops, listeners, daemon)
```

### Enable Verbose Mode

```bash
empirica --verbose <command>
```

### Diagnostic Information

```bash
empirica --version                              # Empirica version
python --version                                # Python (need 3.10+)
git --version                                   # Git
ls -lh .empirica/sessions/sessions.db           # DB size
empirica sessions-list --limit 5                # Recent sessions
empirica projects-list                          # Locally-known projects
```

### Reset Everything (Last Resort)
```bash
# Backup first!
tar -czf empirica-backup-$(date +%Y%m%d).tar.gz ~/.empirica/

# Remove all data
rm -rf ~/.empirica/

# Remove project data
rm -rf .empirica/

# Reinstall
pip uninstall empirica
pip install empirica

# Start fresh
empirica session-create --ai-id myai
```

---

## Still Having Issues?

1. **Check documentation:** Browse other docs in `docs/human/` directory
2. **Check the code:** Empirica is open source - look at the implementation
3. **Run status check:** `empirica status` for system overview
4. **CLI help:** `empirica <command> --help` for any command

---

## Common Gotchas

### 1. Session IDs are UUIDs
```bash
# Wrong: Using short IDs
empirica sessions-show --session-id abc

# Right: Full UUID
empirica sessions-show --session-id abc123-456-789-...
```

### 2. JSON stdin needs trailing dash
```bash
# Wrong:
echo '{"ai_id": "myai"}' | empirica session-create

# Right:
echo '{"ai_id": "myai"}' | empirica session-create -
```

### 3. Git integration requires commits
```bash
# Checkpoints won't work in empty repo
# Make at least one commit first
git commit --allow-empty -m "Initial commit"
```

### 4. Project auto-detection uses git remote
```bash
# Ensure git remote is set
git remote -v

# If no remote, project won't auto-detect
git remote add origin <URL>
```

### 5. Vectors must be 0.0-1.0
```bash
# All epistemic vectors are normalized to [0.0, 1.0]
# 0.0 = none/minimal
# 1.0 = complete/maximum
```

---

**Most issues are:** database permissions, git configuration, or malformed input. Check those first!
