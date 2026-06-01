# End-User Documentation

**Getting-started guides and conceptual explanations for Empirica users.**

---

## Recommended Reading Order

1. **[01_START_HERE.md](01_START_HERE.md)** — Entry point + 3-step quick start
2. **[02_INSTALLATION.md](02_INSTALLATION.md)** — All install options + verification
3. **[FIRST_TIME_SETUP.md](FIRST_TIME_SETUP.md)** — Modern setup flow (`project-init` + `setup-claude-code`)
4. **[EMPIRICA_EXPLAINED_SIMPLE.md](EMPIRICA_EXPLAINED_SIMPLE.md)** — Plain-English overview of the measurement model
5. **[04_QUICKSTART_CLI.md](04_QUICKSTART_CLI.md)** — Day-to-day CLI patterns
6. **[05_EPISTEMIC_VECTORS_EXPLAINED.md](05_EPISTEMIC_VECTORS_EXPLAINED.md)** — The 13 vectors

---

## All Guides

### Getting Started
| File | Purpose |
|------|---------|
| `01_START_HERE.md` | 3-step entry + foundation five vectors |
| `02_INSTALLATION.md` | PyPI / Homebrew / Docker / source installs |
| `03_TROUBLESHOOTING.md` | Common errors + fixes |
| `04_QUICKSTART_CLI.md` | CLI patterns for everyday use |
| `FIRST_TIME_SETUP.md` | Modern setup walkthrough |

### Conceptual
| File | Purpose |
|------|---------|
| `05_EPISTEMIC_VECTORS_EXPLAINED.md` | The 13 vectors — Foundation / Meta / Phase-dependent |
| `EMPIRICA_EXPLAINED_SIMPLE.md` | Plain-English overview of the measurement model |
| `EMPIRICA_NATURAL_LANGUAGE_GUIDE.md` | How to guide an AI in natural language through the CASCADE workflow |
| `ECOSYSTEM_OVERVIEW.md` | Where data lives (per-project / user-tenant / workspace) |

### Workflows
| File | Purpose |
|------|---------|
| `SESSION_GOAL_WORKFLOW.md` | Sessions, transactions, goals, tasks |
| `PROJECT_MANAGEMENT_FOR_USERS.md` | Project model + auto-detection + workspace |
| `MCP_INSTALLATION.md` | MCP server setup for GUI clients |

### Mesh & Coordination
| File | Purpose |
|------|---------|
| `MESH_CONCEPTS.md` | Why the mesh is different — practitioner/practice framing + epistemic actionable knowledge as the payload |
| `MESH_SETUP.md` | How to set up the optional cortex / extension / ntfy layers on top of core |

### Integrations
| File | Purpose |
|------|---------|
| `BEADS_QUICKSTART.md` | Optional BEADS issue tracker integration |
| `BEADS_GOALS_READY_GUIDE.md` | `goals-ready` — BEADS-unblocked + epistemically fit work |

---

## Quick Commands

After install + `empirica project-init`:

```bash
# Sanity check
empirica diagnose

# Bootstrap project context
empirica project-bootstrap

# Open a measurement window
empirica preflight-submit -

# Log artifacts as you work
empirica finding-log --finding "..." --impact 0.7
empirica goals-create --objective "..." --description "..."

# Close the window
empirica postflight-submit -

# Search across what you've learned
empirica project-search --task "..."           # this project
empirica project-search --task "..." --global  # cross-project
```

---

**Need help?** Start with [03_TROUBLESHOOTING.md](03_TROUBLESHOOTING.md)
or run `empirica diagnose`.
