# Empirica Integration Plugin - Distribution Guide

This guide covers how to distribute the plugin.

> **For end-user install:** since v1.8.14 the plugin is bundled with the
> `empirica` Python package and wired up via `empirica setup`.
> See [`docs/human/end-users/02_INSTALLATION.md`](../../../docs/human/end-users/02_INSTALLATION.md)
> for the supported user-facing install path. This document is for plugin
> publishers (marketplace listings, alternate distribution channels).

Three distribution paths are covered below.

---

## Option 1: Official Claude Plugins Marketplace (Recommended)

**Best for:** Maximum visibility, trusted by all Claude Code users

### Steps:

1. **Fork the official marketplace:**
   ```bash
   # On GitHub
   Fork: https://github.com/anthropics/claude-plugins-official
   ```

2. **Clone your fork:**
   ```bash
   git clone https://github.com/YOUR-USERNAME/claude-plugins-official.git
   cd claude-plugins-official
   ```

3. **Add plugin to external_plugins:**
   ```bash
   mkdir -p external_plugins/empirica
   cp -r /path/to/this/plugin/* external_plugins/empirica/
   ```

4. **Update marketplace.json:**

   Edit `.claude-plugin/marketplace.json` and add entry:
   ```json
   {
     "name": "empirica",
     "description": "Epistemic continuity across memory compacting - automatic calibration integration",
     "version": "1.0.0",
     "author": {
       "name": "Empirica Team",
       "email": "your-email@example.com"
     },
     "source": "./external_plugins/empirica",
     "category": "productivity",
     "homepage": "https://github.com/YOUR-ORG/empirica"
   }
   ```

5. **Create Pull Request:**
   - Title: `Add empirica plugin`
   - Description: Explain what the plugin does, benefits, testing done
   - Include screenshots/examples if possible

6. **Review process:**
   - Anthropic team reviews for quality and security
   - May request changes
   - Once approved, merged and available to all users

### Requirements:
- ✅ Clear README with usage instructions
- ✅ Well-tested hooks (no breaking changes)
- ✅ Security review (hooks run shell commands)
- ✅ Proper error handling (graceful failures)
- ✅ Documentation of prerequisites (Empirica installation)

---

## Option 2: Own Git Repository (Full Control)

**Best for:** Rapid iteration, Empirica-specific updates

### Steps:

1. **Create new repository:**
   ```bash
   # On GitHub/GitLab
   Create repo: empirica-plugin
   ```

2. **Initialize and push:**
   ```bash
   cd /tmp/empirica-dist
   git init
   git add .
   git commit -m "Initial commit: Empirica integration plugin"
   git remote add origin https://github.com/YOUR-ORG/empirica-plugin.git
   git push -u origin main
   ```

3. **Create releases:**
   ```bash
   # Tag versions
   git tag -a v1.0.0 -m "Release v1.0.0"
   git push --tags
   ```

4. **Users install via URL:**

   Users add as custom marketplace:
   ```bash
   # In Claude Code
   /plugin > Add Marketplace
   # Enter: https://github.com/YOUR-ORG/empirica-plugin.git
   ```

   Or manual install from git:
   ```bash
   git clone https://github.com/YOUR-ORG/empirica-plugin.git
   cp -r empirica-plugin ~/.claude/plugins/local/empirica
   # Follow manual installation steps in INSTALL.md
   ```

### Maintenance:
- Update version in `.claude-plugin/plugin.json` on each release
- Tag releases with semantic versioning (v1.0.0, v1.1.0, etc.)
- Keep CHANGELOG.md updated

---

## Option 3: Include in Empirica Repository

**Best for:** Bundled with Empirica, automatic setup

### Steps:

1. **Add to Empirica repo:**
   ```bash
   cd /path/to/empirica
   mkdir -p plugins/claude-code-integration
   cp -r /tmp/empirica-dist/* plugins/claude-code-integration/
   ```

2. **Create installation script:**

   Create `scripts/install_claude_plugin.sh`:
   ```bash
   #!/bin/bash
   # Install empirica plugin for Claude Code

   PLUGIN_SOURCE="$(dirname "$0")/../plugins/claude-code-integration"
   PLUGIN_DEST="$HOME/.claude/plugins/local/empirica"

   echo "Installing Empirica integration plugin..."
   mkdir -p "$HOME/.claude/plugins/local"
   cp -r "$PLUGIN_SOURCE" "$PLUGIN_DEST"

   echo "Plugin copied to: $PLUGIN_DEST"
   echo ""
   echo "Next steps:"
   echo "1. Add plugin to ~/.claude/settings.json enabledPlugins"
   echo "2. Register local marketplace (see INSTALL.md)"
   echo "3. Restart Claude Code"
   ```

3. **Update Empirica installation docs:**

   Add to main README.md:
   ```markdown
   ### Claude Code Integration (Optional)

   If using Claude Code, install the Empirica integration plugin for automatic
   epistemic continuity across memory compacts:

   ```bash
   ./scripts/install_claude_plugin.sh
   ```

   See `plugins/claude-code-integration/README.md` for details.
   ```

4. **Include in pip package:**

   Update `setup.py` or `pyproject.toml`:
   ```python
   package_data={
       'empirica': [
           'plugins/claude-code-integration/**/*',
       ],
   },
   ```

### Auto-setup (Advanced):

Create post-install hook that auto-configures Claude Code plugin:

```python
# In setup.py
import os
import shutil
from pathlib import Path

def post_install():
    """Auto-install Claude Code plugin if Claude Code detected"""
    claude_dir = Path.home() / '.claude'
    if claude_dir.exists():
        plugin_src = Path(__file__).parent / 'plugins/claude-code-integration'
        plugin_dst = claude_dir / 'plugins/local/empirica'

        if not plugin_dst.exists():
            plugin_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(plugin_src, plugin_dst)
            print(f"✅ Installed Empirica plugin for Claude Code at {plugin_dst}")
            print("   Restart Claude Code to activate.")
```

---

## Recommended Approach

**Start with Option 3** (bundle with Empirica), then **submit to Option 1** (official marketplace) for broader reach.

### Timeline:

1. **Week 1:** Add to Empirica repo, test with users
2. **Week 2-3:** Gather feedback, fix bugs
3. **Week 4:** Submit to official marketplace with proven track record

### Benefits:
- Empirica users get it automatically
- Community testing before marketplace submission
- Higher chance of marketplace approval (proven plugin)

---

## Files Checklist

Before distribution, ensure all files are included:

- ✅ `.claude-plugin/plugin.json` - Plugin metadata
- ✅ `hooks/hooks.json` - Hook definitions
- ✅ `hooks/pre-compact.py` - PreCompact hook
- ✅ `hooks/post-compact.py` - SessionStart hook
- ✅ `hooks/curate-snapshots.py` - SessionEnd hook
- ✅ `README.md` - Main documentation
- ✅ `INSTALL.md` - Installation instructions (created)
- ✅ `CHANGELOG.md` - Version history (TODO)
- ✅ `LICENSE` - Open source license (TODO)

---

## Next Steps

1. Choose distribution strategy (recommend Option 3 → Option 1)
2. Add missing files (CHANGELOG.md, LICENSE)
3. Test installation on fresh system
4. Update Empirica documentation
5. Announce to community

## Questions?

- Official marketplace: See anthropics/claude-plugins-official issues
- Empirica repo: Open issue in your repo
- Community: Share in Claude Code Discord/forums
