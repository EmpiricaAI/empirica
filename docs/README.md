# Empirica Documentation

**Version:** 1.12.23 | **Status:** Production Ready

---

## Navigation

This folder contains technical documentation. For getting started:

| You Are | Go To |
|---------|-------|
| **New user** | [human/end-users/](human/end-users/) → Start with `01_START_HERE.md` |
| **Developer integrating AI** | [human/developers/](human/developers/) → Start with `CLAUDE_CODE_SETUP.md` |
| **AI loading context** | [architecture/](architecture/) → Start with `README.md` |

---

## Documentation Structure

```
docs/
├── human/                    # For human readers
│   ├── end-users/            # Installation, concepts, troubleshooting
│   └── developers/           # Integration, AI self-management, API
│       ├── skills/           # Skill pipeline documentation
│       └── Security/         # Security patterns and privacy
│
├── architecture/             # For AI context loading
│   ├── README.md             # Architecture index
│   ├── STORAGE_*             # Four-layer storage system
│   ├── SENTINEL_*            # Gate controller
│   ├── CASCADE_*             # Workflow documentation
│   └── NOETIC_PRAXIC_*       # Thinking phases
│
├── reference/                # API and configuration reference
│   ├── api/                  # Python API by module
│   └── epistemic/            # Epistemic vector reference
│
├── guides/                   # AI workflow guides
└── examples/                 # Configuration examples (YAML, JSON)
```

---

## Quick Links

### For Humans

| Guide | Purpose |
|-------|---------|
| [Getting Started](human/end-users/01_START_HERE.md) | First-time setup + plain-English overview |
| [Epistemic Vectors](human/end-users/05_EPISTEMIC_VECTORS_EXPLAINED.md) | Understanding the 13 vectors |
| [Claude Code Setup](human/developers/CLAUDE_CODE_SETUP.md) | Developer integration |

### For AI Systems

| Doc | Purpose |
|-----|---------|
| [Architecture Index](architecture/README.md) | All architecture docs |
| [Separation of Concerns](architecture/separation-of-concerns.md) | What goes where |
| [Storage Architecture](architecture/STORAGE_ARCHITECTURE_COMPLETE.md) | Four-layer data flow |
| [CLI Reference](human/developers/CLI_COMMANDS_UNIFIED.md) | All commands |

---

## Using docs-explain

Query documentation semantically:

```bash
# Topic lookup
empirica docs-explain --topic "epistemic vectors"
empirica docs-explain --topic "CASCADE workflow"

# Question answering
empirica docs-explain --question "How do I create a session?"

# Check coverage
empirica docs-assess --summary-only
```

---

## Documentation Health

Run audits to check documentation health:

```bash
# Full health audit
python scripts/doc_health_audit.py

# Quick coverage check
empirica docs-assess --summary-only
```

**Current Status:** 90+ active docs across architecture, reference, guides, and human docs

---

**Website:** [getempirica.com](https://getempirica.com)
