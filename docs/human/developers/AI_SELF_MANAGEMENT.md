# AI Self-Management Guide

**How AIs maintain knowledge and improve Empirica prompts**

---

## Self-Serve Knowledge

When uncertain about Empirica concepts, **query docs before asking the user or guessing**.

### Commands

```bash
# Topic lookup - "What are vectors?"
empirica docs-explain --topic "vectors"

# Question answering - "How do I create a session?"
empirica docs-explain --question "How do I create a session?"

# Quick coverage check (~50 tokens)
empirica docs-assess --summary-only --output json
```

### When to Use

| Trigger | Action |
|---------|--------|
| uncertainty > 0.5 | `docs-explain --topic "<concept>"` |
| "How do I...?" question | `docs-explain --question "..."` |
| Session start | `docs-assess --summary-only` |
| Pre-CHECK knowledge gap | `docs-explain` on unclear areas |

### Pattern

```
Don't know X → docs-explain X → still unclear → ask user
```

**Never guess or ask the user when docs can answer.**

---

## Self-Improvement Protocol

When you discover gaps or errors in the Empirica prompt, fix them.

### Triggers (Active)

1. **Command doesn't exist** - Prompt references a command that fails
2. **Incorrect syntax** - Example in prompt doesn't work
3. **Missing guidance** - Common scenario not covered
4. **Confusing instruction** - You misinterpret and fail

### Process

```
1. IDENTIFY   - Notice the gap/error during work
2. VALIDATE   - Test to confirm it's actually wrong
3. PROPOSE    - Tell user: "I found a prompt issue: [description]"
4. IMPLEMENT  - If approved, edit the prompt file directly
5. LOG        - empirica finding-log --finding "..." --impact 0.8
```

### Example

```
Scenario: Prompt says "empirica onboard" but command doesn't exist

1. IDENTIFY: "empirica onboard" returns "invalid choice"
2. VALIDATE: Check empirica --help, confirm no onboard command
3. PROPOSE: "The prompt references 'empirica onboard' which doesn't exist.
            Should I update it to use 'session-create' instead?"
4. IMPLEMENT: Edit CLAUDE.md with the fix
5. LOG: empirica finding-log --finding "Fixed prompt: onboard → session-create"
```

### Files to Edit

| File | When |
|------|------|
| `~/.claude/empirica-system-prompt.md` | Live tuning (immediate effect; gets overwritten on next `empirica setup-claude-code --force`) |
| `~/.claude/CLAUDE.md` | User overrides — preserved across `setup-claude-code` runs |
| `empirica/plugins/claude-code-integration/templates/empirica-system-prompt-lean.md` | Canonical lean source — the only system-prompt template (deployed on `setup-claude-code`, written to `~/.claude/empirica-system-prompt.md` and @included from CLAUDE.md) |

The previous `CANONICAL_CORE + model_deltas + sync_system_prompts.py`
pipeline was retired in 1.7 in favor of the lean-prompt-by-default
architecture. The canonical-core docs and the sync script are archived
under `docs/_archive/` and `_archive/scripts/` respectively.

---

## Integration with CASCADE

```
PREFLIGHT
  └─ docs-assess --summary-only (baseline knowledge)

NOETIC PHASE
  └─ docs-explain when uncertainty detected
  └─ Identify prompt gaps during investigation

CHECK
  └─ Validate knowledge before proceeding

PRAXIC PHASE
  └─ Execute with confidence
  └─ Fix prompt issues found during execution

POSTFLIGHT
  └─ Log any prompt improvements as findings
```

---

## Quick Reference

```bash
# Knowledge gap
empirica docs-explain --topic "cascade"
empirica docs-explain --question "How do I run CHECK?"

# Coverage check
empirica docs-assess --summary-only

# Log prompt fix
empirica finding-log --session-id $SID \
  --finding "Fixed prompt: [description]" \
  --impact 0.8
```

**Principle:** AIs should actively maintain the system they use, not just consume it.
