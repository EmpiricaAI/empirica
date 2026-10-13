# Empirica Configuration Examples

**Example configuration files for Empirica projects.**

Checked against 1.14.9 on 2026-10-13.

---

## Available Examples

### `project.yaml.example`

Example `.empirica/project.yaml` configuration file.

| Property | Value |
|----------|-------|
| **Purpose** | Per-project settings (metadata, BEADS integration, subjects/workstreams, contacts, engagements, edges) |
| **Location** | `<your-repo>/.empirica/project.yaml` |
| **Written by** | `empirica project-init` (it writes its own `project.yaml`; this file is a reference, not what `project-init` copies) |

**When to customize:**
- Enable BEADS by default for your team (`beads.default_enabled`)
- Define subjects/workstreams for multi-component projects
- Configure project-specific behavior

The generated file also carries fields this example does not show, such
as `ai_id` and `calibration_weights`, and `project_id` is generated
(the example holds a placeholder). `evidence_profile` accepts `web` as
well as the `code|prose|hybrid|auto` the example lists.

### `database-config.yaml`

Example database configuration. Environment variables take precedence
over the file: `DATABASE_URL` first, then the `EMPIRICA_DB_*` variables,
then the `config.yaml` block (see the Configuration Architecture page for
the full order).

### `mcp-config.json.example`

Example MCP client configuration naming the `empirica-mcp` server.

---

## Usage Options

### Option 1: Automatic (Recommended)

```bash
cd your-project
empirica project-init
# Interactive prompts will guide you (add --enable-beads to turn BEADS on)
```

### Option 2: Manual Copy

```bash
cd your-project
cp docs/examples/project.yaml.example .empirica/project.yaml
# Edit to match your project: replace the project_id placeholder
# and add an ai_id, which the example does not carry
```

A hand-copied file has not been tested against `project-bootstrap`;
prefer `project-init`.

---

## See Also

- [Configuration Architecture](../reference/CONFIGURATION_REFERENCE.md)
- [Installation Guide](../human/end-users/02_INSTALLATION.md)
- [Getting Started](../human/end-users/01_START_HERE.md)
