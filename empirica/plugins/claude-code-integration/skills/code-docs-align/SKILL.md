---
name: code-docs-align
description: "Use when the user says '/code-docs-align', 'check if docs match code', 'verify docstrings', 'find stale comments', 'audit TODOs', 'check ref-doc accuracy', 'documentation accuracy', or wants to verify that documentation, docstrings, comments, and ref-docs actually reflect the current state of the code. This skill bridges /code-audit (code quality) and docs-assess (doc coverage) by checking ACCURACY — do the docs match what the code actually does?"
version: 1.0.0
---

# Code-Docs Alignment: Documentation Accuracy Investigation

**Verify that documentation matches code. Find stale, misleading, or phantom docs.**

This skill is purely noetic — it discovers mismatches between documentation and code.
It does NOT fix anything. The output (findings, goals, unknowns) feeds into the Empirica
workflow for praxic remediation.

**Why this matters:** For AI-based workflows and enterprise evaluation, stale documentation
is worse than missing documentation — it actively misleads. `/code-audit` checks code quality.
`docs-assess` checks doc coverage. This skill checks the gap: **do the docs match the code?**

---

## How to Run

```
/code-docs-align                              # Check entire project
/code-docs-align --target src/handlers/       # Check specific directory
/code-docs-align --focus docstrings           # Focus on one dimension
/code-docs-align --focus todos                # Focus on TODO/FIXME audit
/code-docs-align --focus ref-docs             # Focus on ref-doc accuracy
```

---

## Phase 0: PREFLIGHT

Open a transaction before investigating.

```bash
empirica preflight-submit - << 'EOF'
{
  "work_type": "docs",
  "task_context": "Code-docs alignment audit on <target>",
  "vectors": { "...": "YOUR OWN assessment across the 13 vectors" },
  "reasoning": "What you actually know about this codebase's docs right now"
}
EOF
```

**No canned vector values.** This skill used to print a fixed block —
`know: 0.2, uncertainty: 0.7` and so on — for you to submit verbatim. That
teaches the one habit the whole measurement layer exists to prevent: reporting
numbers you did not assess. A pasted vector set is not a low reading, it is a
*fabricated* one, and it corrupts the calibration record more than skipping the
transaction would.

Assess where you genuinely are. If you have already read the docs you are about
to audit, `know` is not 0.2, and saying so is the point.

If you were already grounded before opening — you read the target first — declare
`claims` in PREFLIGHT with grounding `read` or `ran` and proceed straight to
praxic. **No CHECK needed**; see `/epistemic-transaction`.

---

## Phase 1: Scope

Determine what to check. If the user specified a target, use it. Otherwise, check the
current project root.

```bash
# Determine scope
TARGET="${1:-.}"

# File count and LOC
find "$TARGET" -name "*.py" | wc -l
find "$TARGET" -name "*.py" -exec wc -l {} + | tail -1

# Prioritize recently-changed files (most likely to have stale docs)
git log --name-only --format="" HEAD~20..HEAD | sort -u | grep '\.py$'

# Collect registered ref-docs
empirica docs-assess --output json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); [print(r.get('path','')) for r in d.get('reference_docs',[])]"
```

Log the scope:
```bash
empirica finding-log --finding "Docs-align scope: $TARGET — N files, N LOC, N recently changed, N ref-docs registered" --impact 0.1
```

---

## Phase 2: Docstring Accuracy (AST-based)

For each high-priority file (recently changed or large), read and compare signatures
against docstrings. This requires AI judgment — parse the code, read the docstring,
check alignment.

### What to Check

For each function/method with a docstring:

1. **Parameters:** Compare function signature params against docstring Args section
   - Phantom param: documented but not in signature → finding (impact 0.6)
   - Missing param: in signature but not documented → finding (impact 0.3)
   - Wrong type: docstring type doesn't match annotation → finding (impact 0.4)

2. **Returns:** Compare return annotation against docstring Returns section
   - Wrong return type/description → finding (impact 0.6)
   - Return documented but function returns None → finding (impact 0.6)

3. **Raises:** Compare actual raise statements against docstring Raises section
   - Stale raises (documented but never raised) → finding (impact 0.5)
   - Undocumented raises (raised but not in docstring) → finding (impact 0.3)

4. **Behavioral claims:** Does the docstring describe what the function ACTUALLY does?
   - "Validates X" but no validation code → finding (impact 0.7)
   - "Returns None if not found" but actually raises → finding (impact 0.7)

### Impact Scoring

| Mismatch Type | Impact | Rationale |
|---------------|--------|-----------|
| Phantom param (in docstring, not in code) | 0.6 | Actively misleading |
| Missing param (in code, not in docstring) | 0.3 | Incomplete but not misleading |
| Wrong return description | 0.6 | Actively misleading |
| Stale raises clause | 0.5 | Moderately misleading |
| Behavioral mismatch | 0.7 | Dangerously misleading |

### Logging

```bash
empirica finding-log --finding "Phantom param 'timeout' in docstring of connect() at db/client.py:45 — param was removed in batch-3 cleanup" --impact 0.6
empirica unknown-log --unknown "process_batch() docstring mentions 'retry_count' param — unclear if this is intentional kwargs passthrough or stale"
```

---

## Phase 3: Inline Comment Staleness

Scan for comments that reference patterns, functions, or behaviors that no longer exist.

### What to Check

1. **References to removed code:**
   - Comments mentioning function/class names that no longer exist in the codebase
   - "See also X" where X was deleted
   - References to files that were moved or removed

2. **Contradicted behavior:**
   - "This uses bare except for safety" in files where bare excepts were already fixed
   - "Temporary workaround" for code that's been in place >6 months
   - "TODO: remove after migration" where migration is complete

3. **Stale section headers:**
   - `# --- Legacy handlers ---` where the legacy code was removed
   - Module-level docstrings describing features that were split elsewhere

### Detection Strategy

```bash
# Find all comments
grep -rn "^[[:space:]]*#" "$TARGET" --include="*.py" | head -50

# Cross-reference: do mentioned symbols still exist?
# For each comment mentioning a specific function/class name,
# check if that symbol still exists in the codebase
```

### Logging

```bash
empirica finding-log --finding "Stale comment at session_resolver.py:42 references get_identity_dir() — function was removed in batch-4" --impact 0.4
empirica finding-log --finding "Comment at workflow_commands.py:15 says 'bare except for safety' — already fixed to except Exception: in batch-5" --impact 0.3
```

---

## Phase 4: TODO/FIXME Audit

Audit every TODO and FIXME in the codebase. Each one is either stale (work done), active
(untracked work), or deferred (consciously parked).

### What to Check

```bash
# Collect all TODOs and FIXMEs
grep -rn "TODO\|FIXME\|HACK\|XXX" "$TARGET" --include="*.py"
```

For each:
1. **Is the described work already done?** → stale TODO → finding (impact 0.4)
2. **Is it untracked work that should be a goal?** → active TODO → unknown
3. **Was it consciously deferred?** → check decision-log → skip if already recorded
4. **Is it a stub placeholder?** → finding (impact 0.2) if the stub has been fleshed out

### Logging

```bash
# Stale TODO (feature is built)
empirica finding-log --finding "Stale TODO at memory_gap_detector.py:23 — feature was implemented in batch-2" --impact 0.4

# Active TODO (untracked work)
empirica unknown-log --unknown "TODO at firewall.py:89 — 'implement rate limiting' — is this planned or deferred?"

# Already covered by decision
# (skip — no artifact needed)
```

---

## Phase 5: Ref-Doc Alignment

For each registered reference document, check whether the symbols (functions, classes,
file paths) it mentions still exist in the codebase.

### What to Check

```bash
# Get registered ref-docs
empirica docs-assess --output json
```

For each ref-doc:
1. Read the document
2. Extract mentioned file paths, function names, class names
3. Verify each still exists in the codebase
4. Check if code examples still work (syntax, imports)
5. Flag dead references

### Impact Scoring

| Issue | Impact | Rationale |
|-------|--------|-----------|
| Dead file path reference | 0.7 | Document points to nothing |
| Dead function/class reference | 0.6 | Symbol was renamed/removed |
| Stale code example | 0.5 | Example won't work if copied |
| Outdated architectural claim | 0.7 | Misleads about system structure |

### Logging

```bash
empirica finding-log --finding "Ref-doc 'architecture.md' references empirica/core/metrics.py — file was removed" --impact 0.7
empirica finding-log --finding "Ref-doc 'api-guide.md' shows import from empirica.utils.helpers — module renamed to empirica.utils.session_resolver" --impact 0.6
```

---

## Phase 6: Plugin Meta-Check

Check SKILL.md files, CLAUDE.md, and plugin configuration for references to CLI commands,
flags, or workflows that may have changed.

### What to Check

1. **CLI commands in SKILL.md files:**
   ```bash
   empirica --help   # Get current command list
   ```
   Cross-reference against commands mentioned in all SKILL.md files.

2. **Flags in CLAUDE.md:**
   Check that CLI flags mentioned in the system prompt still exist.

3. **Hook references:**
   Verify that hook scripts reference valid tools and events.

### Logging

```bash
empirica finding-log --finding "SKILL.md for 'empirica' references 'empirica status' command — actual command is 'empirica project-status'" --impact 0.5
empirica unknown-log --unknown "CLAUDE.md references --type flag on project-search — verify this flag still exists"
```

---

## Phase 7: Triage + Goal Creation

Categorize all findings and group into remediation goals.

### Finding Tags

| Tag | Meaning |
|-----|---------|
| `phantom-param` | Documented param doesn't exist in code |
| `missing-param` | Code param missing from documentation |
| `stale-todo` | TODO describes work that's already done |
| `dead-ref-doc` | Ref-doc references code that doesn't exist |
| `stale-comment` | Comment describes removed/changed behavior |
| `wrong-raises` | Docstring raises section doesn't match code |
| `meta-drift` | SKILL.md/CLAUDE.md references stale commands |
| `behavioral-mismatch` | Docstring claims don't match actual behavior |

### Goal Creation

Group related findings into actionable goals:

```bash
# Example goals
empirica goals-create --objective "Fix phantom and missing params in docstrings across cli/command_handlers/"
empirica goals-create --objective "Remove 8 stale TODOs for completed features"
empirica goals-create --objective "Update ref-doc architecture.md — 5 dead symbol references"
empirica goals-create --objective "Clean stale inline comments referencing removed functions"
```

**Goal sizing:** one goal = one praxic transaction. Full sizing guidance is in
`/epistemic-transaction` — it owns the transaction lifecycle, and this text had
drifted into three homes with three different wordings.

---

## Phase 8: Summarize

Present the alignment audit results:

1. **Scope** — files checked, ref-docs checked, TODOs audited
2. **Docstring accuracy** — phantom params, missing params, wrong returns/raises
3. **Comment staleness** — stale comments found and categorized
4. **TODO status** — stale vs active vs deferred
5. **Ref-doc health** — dead references, outdated examples
6. **Meta accuracy** — SKILL.md/CLAUDE.md drift
7. **Goals created** — remediation plan
8. **Unknowns remaining** — ambiguous cases needing human judgment

```bash
# Show the full picture
empirica goals-list
```

---

## Output Contract

After `/code-docs-align` completes, the following artifacts exist in the Empirica DB:

| Artifact Type | Purpose |
|--------------|---------|
| **Findings** (impact-scored) | Documentation-code mismatches, stale content |
| **Unknowns** | Ambiguous cases requiring human judgment |
| **Decisions** | Conscious choices to keep certain doc patterns |
| **Goals** | Remediation work packages for praxic agents |

---

## Key Design Principle

This skill uses **AI judgment**, not just pattern matching. A phantom param might be
intentional (kwargs passthrough omitted from docs). A stale TODO might be a conscious
deferral. A behavioral claim might be approximately correct.

**When uncertain: log an unknown, not a finding.** False positives erode trust in the
audit results. A well-calibrated unknown is more valuable than a noisy finding.

---

## Re-Running

The skill is idempotent. Running `/code-docs-align` again after remediation shows:
- Which mismatches are resolved (goals completed)
- Which persist (goals still open)
- New mismatches from recent changes (regression detection)

Compare alignment runs over time to track documentation accuracy trajectory.
