---
name: code-audit
description: "Use when the user says '/code-audit', 'audit this code', 'check code quality', 'find duplication', 'find dead code', 'code cleanup', 'technical debt audit', 'code review module', or wants a structured noetic investigation of code quality. This skill runs external analysis tools and structured manual review, producing Empirica artifacts (findings, goals, decisions) that any praxic agent can execute."
version: 1.0.0
---

# Code Audit: Noetic Investigation Skill

**Investigate code quality. Produce structured remediation plans.**

This skill is purely noetic — it discovers, triages, and plans. It does NOT make changes.
The output (findings, goals, decisions) feeds directly into the Empirica workflow for any
praxic agent to pick up and execute.

---

## How to Run

```
/code-audit                              # Audit entire project
/code-audit --target src/handlers/       # Audit specific directory
/code-audit --target src/auth.py         # Audit specific file
/code-audit --focus duplication          # Focus on one dimension
```

---

## Phase 1: Scope

Determine what to audit. If the user specified a target, use it. Otherwise, audit the
current project root.

```bash
# Determine scope
TARGET="${1:-.}"  # Default to current directory

# Quick size assessment
find "$TARGET" -name "*.py" | wc -l          # File count
find "$TARGET" -name "*.py" -exec wc -l {} + | tail -1  # Total LOC
```

Log the audit scope:
```bash
empirica finding-log --finding "Audit scope: $TARGET — N files, N LOC" --impact 0.1
```

---

## Phase 2: Automated Tool Passes

Run external tools and parse results into findings. Each tool covers a different dimension.
Skip any tool that isn't installed — the audit still works without it.

### 2a. Linting & Style (ruff)

```bash
ruff check "$TARGET" --statistics --output-format json 2>/dev/null
```

**Parse results:**
- Group by rule category (import order, unused imports, complexity, etc.)
- Log aggregates as findings, not individual violations
- Impact scoring: unused imports = 0.2, complexity violations = 0.5, security = 0.8

```bash
# Example: aggregate finding
empirica finding-log --finding "ruff: 23 unused imports across 8 files (F401)" --impact 0.2
empirica finding-log --finding "ruff: 5 functions exceed complexity limit (C901)" --impact 0.5
```

### 2b. Dead Code (vulture)

```bash
vulture "$TARGET" --min-confidence 80
```

**Parse results:**
- Filter out false positives (dynamically called functions, CLI entry points)
- Log confirmed dead code as findings
- Log uncertain cases as unknowns

```bash
empirica finding-log --finding "vulture: 12 unused functions detected (80%+ confidence)" --impact 0.4
empirica unknown-log --unknown "vulture flagged handle_legacy_sync() — verify if called dynamically"
```

### 2c. Complexity Metrics (radon)

```bash
radon cc "$TARGET" -s -a --min C    # Only show functions rated C or worse
radon mi "$TARGET" -s --min B       # Maintainability index
```

**Parse results:**
- Functions rated C (11-20) → finding with impact 0.4
- Functions rated D (21-30) → finding with impact 0.6
- Functions rated F (31+) → finding with impact 0.8
- Log the worst offenders by name and file

```bash
empirica finding-log --finding "radon: process_vectors() in workflow_commands.py has CC=27 (D)" --impact 0.6
```

### 2d. Test Freshness (`scripts/test_freshness_audit.py`)

```bash
python3 scripts/test_freshness_audit.py --human
python3 scripts/test_freshness_audit.py --baseline   # counts only, track direction
```

**Not a coverage check.** Coverage asks "was this line executed?"; this asks
**"could this test have failed?"** Tests drift like any other artifact, and a
drifted test doesn't go red — it goes green over the wrong contract and *guards*
the defect.

| finding | what it means |
|---|---|
| `stale-fixture` | a hand-built `CREATE TABLE` missing lifecycle columns the real schema has — fixture and code agree with each other and disagree with production |
| `unfalsifiable-test` | no assertion; passes unless something raises |
| `tautological-assert` | `assert True` / `assert x == x` — cannot fail by construction |
| `frozen-over-churn` | *informational only* — module changed repeatedly, its test didn't. Often correct (a contract test surviving refactors is doing its job); a prompt to look, never a defect |

**Triage, don't bulk-fix.** A minimal fixture is a legitimate choice; what makes it
a finding is that the missing columns arrived in a *migration after it was written*.
Log real ones as findings, and record deliberate omissions so the next sweep stays
quiet on them.

### 2e. Type Errors (pyright)

```bash
pyright "$TARGET" --outputjson 2>/dev/null
```

**Parse results:**
- Count errors by category (missing types, incompatible types, possibly unbound)
- Log aggregates, not individual errors

```bash
empirica finding-log --finding "pyright: 45 type errors — 20 missing annotations, 15 incompatible, 10 unbound" --impact 0.3
```

---

## Phase 3: Structural Review (Manual)

These dimensions require AI judgment — tools can't catch them.

### 3a. File Size Analysis

```bash
find "$TARGET" -name "*.py" -exec wc -l {} + | sort -rn | head -20
```

**Thresholds:**
- \>2000 LOC → finding with impact 0.5 ("needs splitting")
- \>1000 LOC → finding with impact 0.3 ("consider splitting")
- \>500 LOC → note only

```bash
empirica finding-log --finding "project_commands.py: 4214 LOC — contains 17 unrelated handlers, needs splitting" --impact 0.6
```

### 3b. Duplication Detection

Search for functions defined in multiple places:

```bash
# Find duplicated function definitions
grep -rn "^def " "$TARGET" | awk -F: '{print $NF}' | sort | uniq -c | sort -rn | head -20

# Find copy-pasted patterns (same function name in multiple files)
grep -rn "def get_instance_id\|def find_project_root\|def resolve_project" "$TARGET"
```

**For each duplicate:** Read both copies. Assess whether they're identical, similar, or
intentionally different. Log accordingly:

```bash
# Identical copies
empirica finding-log --finding "get_instance_id() duplicated identically in 6 files" --impact 0.6

# Intentionally different (record WHY)
empirica decision-log --choice "Keep separate find_project_root() in hooks" \
  --rationale "Hooks must be standalone — can't import from package" \
  --reversibility exploratory
```

### 3c. Module Boundary Assessment

Read the import graph. Check for:
- **Circular imports** — A imports B, B imports A
- **Layer violations** — CLI importing from data, data importing from core
- **God modules** — single file that everything imports

```bash
# Check for late imports (circular dependency workaround)
grep -rn "import " "$TARGET" | grep "def \|if " | head -20
```

### 3d. Naming & Convention Consistency

Spot check:
- Mixed naming styles (snake_case vs camelCase)
- Inconsistent patterns (some files use `handle_X_command`, others use `X_handler`)
- Magic strings/numbers without constants

### 3e. Error Handling

```bash
# Find bare exception handlers
grep -rn "except Exception" "$TARGET" | grep "pass"
grep -rn "except:" "$TARGET"
```

```bash
empirica finding-log --finding "12 bare 'except Exception: pass' handlers — errors silently swallowed" --impact 0.5
```

### 3f. Matchers — over a formal grammar, or over natural language?

Any check keyed on **words or phrases** rather than on a grammar. Word lists,
antonym or negation sets, risk-word scans, `TODO`-alikes, "does this text say
X" predicates.

**The discriminator is FORMAL vs NATURAL, not keyword-matching-good-or-bad.**
Both are legitimate shapes and both ship in the same repos:

| | ✅ formal | ❌ natural |
|---|---|---|
| grammar | fixed, published (SQL, an AST, an enum, a config schema) | emergent, contested |
| a token's denotation | fixed — `SELECT` is always `SELECT` | context-dependent, drifts |
| a miss is | a parser bug — findable, fixable | invisible, and reads as a result |

**The tell:** *can I enumerate the tokens and be done?* Over a published grammar
the spec is finite. Over natural language it never is, and an unfinishable list
produces false negatives **by construction** — so zero hits reads as "nothing to
find" when it means "cannot see".

```bash
# Word/phrase lists that a checker matches against — audit each one's subject
rg -n "^_?[A-Z_]+(?:WORDS|MARKERS|TERMS|PAIRS|PATTERNS)\s*[:=]" "$TARGET"
```

For each: **what is it matching against?** Source code, config, or a wire format
→ formal, fine. Prose a human or a model wrote → the finding below.

**Before condemning one, measure it.** Run the predicate over the real corpus it
sees, exhaustively where the gate makes that possible, with a positive control
proving the instrument is live. A measured zero beats an argument from principle,
and it is the difference between "I don't like this" and a finding.

```bash
empirica finding-log --finding "Lexical matcher over artifact prose at core/x.py — fired 0 times across N real pairs; instrument live on constructed input" --impact 0.7
```

**The counter-move is not a longer list.** Prefer a signal the system already
produces from **behaviour** — a verdict, a test result, an observed outcome —
over one that must be inferred by reading text.

---

## Phase 4: Triage

Review all findings. Assign categories and prioritize.

**Categories:**
| Tag | Meaning |
|-----|---------|
| `duplication` | Same code in multiple places |
| `complexity` | Function/file too complex or too large |
| `dead-code` | Unused code that should be removed |
| `consistency` | Naming, patterns, conventions don't match |
| `architecture` | Module boundaries, layering, coupling |
| `reliability` | Error handling, edge cases, silent failures |
| `security` | Input validation, injection risks |

**Impact scoring guide:**
| Score | Meaning |
|-------|---------|
| 0.1-0.3 | Cosmetic — style, minor inconsistency |
| 0.4-0.6 | Structural — duplication, complexity, dead code |
| 0.7-0.9 | Critical — bug source, security risk, architecture violation |

---

## Phase 5: Create Remediation Goals

Group related findings into actionable goals. Each goal should be independently
executable by a praxic agent.

**Goal template:**
```bash
empirica goals-create --objective "Split project_commands.py into focused modules (artifact_log, workspace, ecosystem)"
```

Good goals have:
- **Clear scope** — which files, what changes
- **Success criteria** — how to verify it's done
- **Linked findings** — why this goal exists (reference finding IDs)
- **Dependencies** — what must happen first

**Goal sizing:** one goal = one praxic transaction. Full sizing guidance is in
`/epistemic-transaction` — it owns the transaction lifecycle, and this text had
drifted into three homes with three different wordings.

---

## Phase 6: Record Decisions

Not every finding needs a fix. Some are acceptable trade-offs. Record these explicitly:

```bash
# Accept a trade-off
empirica decision-log --choice "Accept hook utility duplication" \
  --rationale "Hooks must be standalone — extracting to shared module is the fix, not eliminating all copies" \
  --reversibility exploratory

# Defer work
empirica decision-log --choice "Defer type hint completion to separate effort" \
  --rationale "Functional correctness over type coverage for now" \
  --reversibility easily_reversible
```

**Why this matters:** Enterprise auditors want to see that decisions were conscious, not
accidental. A documented "we chose to keep this" is better than unexplained duplication.

---

## Phase 7: Summarize

Present the audit results to the user:

1. **Scope** — what was audited
2. **Tool results** — aggregate numbers from ruff, vulture, radon, pyright
3. **Top findings** — sorted by impact
4. **Goals created** — the remediation plan
5. **Decisions recorded** — what was accepted/deferred and why
6. **Unknowns remaining** — what needs further investigation

```bash
# Show the full picture
empirica goals-list
```

---

## Output Contract

After `/code-audit` completes, the following artifacts exist in the Empirica DB:

| Artifact Type | Purpose |
|--------------|---------|
| **Findings** (impact-scored) | What was discovered — issues, patterns, metrics |
| **Unknowns** | What needs further investigation before acting |
| **Decisions** | What was consciously accepted, deferred, or prioritized |
| **Goals** | Remediation work packages — ready for any praxic agent |

Any agent can then:
```bash
empirica goals-list                    # See the work
empirica preflight-submit - << 'EOF'   # Start a transaction
# ... pick up a goal and execute it
```

---

## Re-Running

The skill is idempotent. Running `/code-audit` again after remediation shows:
- Which findings are resolved (goals completed)
- Which persist (goals still open)
- New findings from changes (regression detection)

Compare audit runs over time to track code quality trajectory.
