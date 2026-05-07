# Empirica Configuration Examples

**Example configuration files for new Empirica projects.**

---

## Available Examples

### `project.yaml.example`

Example `.empirica/project.yaml` configuration file.

| Property | Value |
|----------|-------|
| **Purpose** | Per-project settings (metadata, BEADS integration, subjects/workstreams) |
| **Location** | Copy to `<your-repo>/.empirica/project.yaml` |
| **Created by** | `empirica project-init` (automatic) |

**When to customize:**
- Enable BEADS by default for your team
- Define subjects/workstreams for multi-component projects
- Configure project-specific behavior

---

## Usage Options

### Option 1: Automatic (Recommended)

```bash
cd your-project
empirica project-init
# Interactive prompts will guide you
```

### Option 2: Manual Copy

```bash
cd your-project
cp docs/examples/project.yaml.example .empirica/project.yaml
# Edit to match your project
```

---

## See Also

- [Configuration Architecture](../reference/CONFIGURATION_REFERENCE.md)
- [Installation Guide](../human/end-users/02_INSTALLATION.md)
- [Getting Started](../human/end-users/01_START_HERE.md)
