# Skill Pipeline Documentation

## Overview

The Empirica skill pipeline provides a progressive disclosure system for loading skill knowledge at runtime. It reduces token usage by 85% while maintaining full decision-making power.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Verbose Skills (empirica/plugins/.../skills/)               │
│ └─ Full Claude Code skills in SKILL.md format               │
│ └─ Loaded by Claude Code on semantic match                  │
└──────────────────────────┬──────────────────────────────────┘
                           │
                    empirica skill-extract
                    (one-time extraction)
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Condensed Skills (project_skills/*.yaml)                    │
│ └─ Extracted essence: steps, gotchas, references            │
│ └─ 0.5-1kb per skill (vs 5-10kb verbose)                    │
└──────────────────────────┬──────────────────────────────────┘
                           │
                    empirica project-bootstrap
                    (runtime injection)
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Runtime Context                                             │
│ ├─ breadcrumbs (findings, unknowns, goals)                  │
│ ├─ workflow_automation                                      │
│ └─ project_skills (loaded at session start)                 │
└─────────────────────────────────────────────────────────────┘
```

## Components

### 1. Verbose Skills (`empirica/plugins/claude-code-integration/skills/`)

Full Claude Code skills in SKILL.md format:

```yaml
---
name: skill-name
description: What the skill does (for semantic matching)
allowed-tools: Bash(empirica:*),Read,Grep,Glob
---

# Skill Title

## Instructions
[Detailed workflow guidance]

## Examples
[Usage examples]

## Reference
[Detailed documentation]
```

**Location:** `empirica/plugins/claude-code-integration/skills/<skill-name>/SKILL.md`
**Loaded by:** Claude Code (on semantic match when user asks "how do I...")
**Size:** 100+ lines per skill

### 2. Condensed Skills (`project_skills/`)

Extracted YAML format optimized for runtime:

```yaml
id: skill-id
title: Skill Title
tags: [tag1, tag2, tag3]

preconditions:
  - "Requirement 1"
  - "Requirement 2"

steps:
  - "Step 1: Description"
  - "Step 2: Description"

gotchas:
  - "Warning/pitfall 1"
  - "Warning/pitfall 2"

references:
  - "command --help"
  - "See docs/file.md"

summary: |
  Brief description of the skill purpose.
```

**Location:** `project_skills/<skill-id>.yaml`
**Loaded by:** `empirica project-bootstrap`
**Size:** 50-100 lines (0.5-1kb)

### 3. Skill Extraction (`skill-extract`)

Extracts verbose skills to condensed format:

```bash
# Extract single skill
empirica skill-extract --skill-dir empirica/plugins/claude-code-integration/skills/code-audit

# Extract all skills from directory
empirica skill-extract --skills-dir .claude/skills --output-file meta-agent-config.yaml
```

### 4. Skill Fetching (`skill-fetch`)

Fetches and normalizes skills from various sources:

```bash
# From URL (markdown)
empirica skill-fetch --name "my-skill" --url https://example.com/skill.md

# From local .skill archive (ZIP containing skill.yaml/skill.md)
empirica skill-fetch --name "my-skill" --file ./downloaded.skill --tags "empirica,workflow"
```

### 5. Skill Suggestion (`skill-suggest`)

Lists available skills:

```bash
empirica skill-suggest --task "how do I create a goal?"
```

Returns:
- Local skills (already in `project_skills/`)
- Available online sources (can be fetched)

## Runtime Injection

`project-bootstrap` automatically loads all skills from `project_skills/`:

```bash
empirica project-bootstrap --session-id <ID> --output json
```

Response includes:

```json
{
  "ok": true,
  "project_id": "...",
  "breadcrumbs": {...},
  "project_skills": {
    "count": 3,
    "skills": [
      {
        "id": "empirica-epistemic-framework",
        "title": "Empirica Epistemic Framework",
        "steps": ["PREFLIGHT: ...", "NOETIC: ...", ...],
        "gotchas": ["CHECK gate auto-computes...", ...],
        "references": ["empirica --help", ...]
      }
    ]
  }
}
```

## Token Economics

| Approach | Tokens | Description |
|----------|--------|-------------|
| Full Skills | 14kb | Loading multiple SKILL.md files |
| Condensed | 2kb | Extracted YAML via bootstrap |
| **Reduction** | **85%** | Same decision power, fewer tokens |

## Best Practices

1. **Create verbose skills** in `empirica/plugins/claude-code-integration/skills/` for Claude Code semantic matching
2. **Extract to condensed** using `skill-extract` after major changes
3. **Commit both** so team members get verbose and condensed versions
4. **Let bootstrap inject** condensed skills at session start
5. **Use gotchas** for common pitfalls and warnings
6. **Keep references** pointing to `--help` and doc files

## Files

- `project_skills/*.yaml` - Condensed runtime skills
- `empirica/plugins/claude-code-integration/skills/*/SKILL.md` - Verbose Claude Code skills
- `skill_extractor/` - Architecture docs and implementation guide
- `empirica/core/skills/parser.py` - Markdown to YAML parser
- `empirica/core/skills/extractor.py` - Extraction logic
- `empirica/cli/command_handlers/skill_commands.py` - CLI handlers
