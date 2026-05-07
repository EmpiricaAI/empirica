# Installation Guide

**Time:** 5-10 minutes
**Prerequisites:** Python 3.10+, git

---

## Quick Install

### 1. Install the package
```bash
pip install empirica
```

### 2. Verify the CLI works
```bash
empirica --help
```

You should see the list of available commands.

### 3. Initialize a project in your repo
```bash
cd your-project           # must be a git repo or an existing directory
empirica project-init     # creates .empirica/ with config.yaml + project.yaml
```

This step creates the **project-local sessions database** that Empirica writes
to. Without it, every subsequent CLI command will fail with
`Cannot determine sessions.db path`.

### 4. (Claude Code users) Wire up the plugin integration

If you're using the Claude Code CLI (including with non-Anthropic models
via Ollama Cloud, etc.), run this once to install the Empirica plugin and
configure the statusline + hooks:

```bash
empirica setup-claude-code
```

This copies the plugin files into `~/.claude/plugins/local/empirica/`, adds
the `statusLine` block to `~/.claude/settings.json`, and registers the
Sentinel + compact + session-lifecycle hooks. The next time you launch
`claude` inside an Empirica project, the statusline will show up automatically
and `SessionStart` will create a session for you.

### 5. (Standalone users) Create your first session manually
```bash
empirica session-create --ai-id myai

# Output shows session_id and auto-detected project
```

Skip this if you're using Claude Code — the session-init hook from step 4
creates one automatically on `SessionStart`.

### 6. Verify it's working
```bash
empirica diagnose   # walks the full integration health — PASS/FAIL per check
```

If any check FAILs, the command prints an actionable hint pointing at the
next step. Especially useful if the statusline isn't showing up in Claude Code.

---

## Installation Options

### Option 1: PyPI (Recommended)
```bash
# Latest stable release
pip install empirica

# Specific version
pip install empirica==1.9.1

# With MCP server for Claude Desktop/Cursor
pip install empirica empirica-mcp

# Upgrade to latest
pip install --upgrade empirica
```

### Option 2: Homebrew (macOS)
```bash
brew tap nubaeon/tap
brew install empirica
```

### Option 3: Docker
```bash
# Standard image
docker pull nubaeon/empirica:1.9.1

# Security-hardened Alpine (recommended)
docker pull nubaeon/empirica:1.9.1-alpine

# Run
docker run -it -v $(pwd)/.empirica:/data/.empirica nubaeon/empirica:1.9.1 /bin/bash
```

### Option 4: From Source
```bash
# Clone repository
git clone https://github.com/Nubaeon/empirica.git
cd empirica

# Install in development mode
pip install -e .

# Or install normally
pip install .
```

### Option 5: With extras
```bash
# Install with TUI support (empirica tui + empirica chat)
pip install "empirica[tui]"

# Install with vision support
pip install "empirica[vision]"

# Install with all extras
pip install "empirica[all]"
```

**Headless installs:** the base `pip install empirica` works in CI/CD,
containers, and other environments without a terminal. `empirica
status --json --all`, scanner verbs, and all CLI primitives function
without `textual`. Only `empirica tui` and `empirica chat` need the
`[tui]` extra.

---

## Configuration

### Optional: API Keys

If you plan to use external integrations (future features), create a credentials file:

```bash
mkdir -p ~/.empirica
cat > ~/.empirica/credentials.yaml << EOF
# Optional: For future integrations
openai_api_key: "your-key-here"
EOF
```

**Note:** This is optional. Empirica works without any API keys for core functionality.

### Optional: Git Configuration

For checkpoint and handoff features:

```bash
# Ensure git is configured
git config --global user.name "Your Name"
git config --global user.email "your@email.com"
```

---

## Directory Structure

After installation, Empirica creates directories on first use:

```
~/.empirica/                    # User data directory
├── empirica.db                 # SQLite database
├── sessions/                   # Session data
├── projects/                   # Project tracking
└── credentials.yaml            # API keys (optional)

<your-repo>/.empirica/          # Project-specific data
├── sessions/                   # Local session cache
└── slides/                     # Vision assessments (if using vision)
```

**Note:** `.empirica/` directories are gitignored by default.

---

## Verification

### Test Basic Workflow
```bash
# 1. Create session
SESSION_ID=$(empirica session-create --ai-id test --output json | jq -r .session_id)

# 2. List sessions
empirica sessions-list

# 3. Show session
empirica sessions-show --session-id $SESSION_ID

# 4. Clean up (optional)
# Sessions are stored in SQLite, safe to leave them
```

### Check Command Groups
```bash
# Session commands
empirica sessions-list
empirica sessions-resume --ai-id test

# Project commands
empirica project-list

# Goal commands
empirica goals-list

# All working? You're good to go! ✓
```

---

## Troubleshooting

### Import Error
```bash
# Error: ModuleNotFoundError: No module named 'empirica'
# Solution: Ensure pip installed to correct Python environment

python --version      # Check Python version (3.8+ required)
which python          # Check Python path
pip show empirica     # Verify installation
```

### Database Error
```bash
# Error: sqlite3.OperationalError: unable to open database
# Solution: Check directory permissions

ls -la ~/.empirica/
chmod 755 ~/.empirica/
```

### Git Notes Error
```bash
# Error: fatal: refs/empirica/checkpoints does not exist
# Solution: Create first checkpoint to initialize git notes

empirica checkpoint-create --session-id <SESSION_ID>
```

### Command Not Found
```bash
# Error: empirica: command not found
# Solution: Add pip bin directory to PATH

# Find where pip installs binaries
pip show empirica | grep Location

# Add to PATH (bash)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# Add to PATH (zsh)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

---

## Platform-Specific Notes

### Linux
```bash
# Install system dependencies
sudo apt-get update
sudo apt-get install python3-pip python3-dev git

# Install Empirica
pip install empirica
```

### macOS
```bash
# Install via Homebrew (if needed)
brew install python git

# Install Empirica
pip3 install empirica
```

### Windows
```powershell
# Install Python from python.org (3.8+)
# Install git from git-scm.com

# Install Empirica
pip install empirica

# Note: Use PowerShell or Git Bash for best experience
```

---

## Development Installation

For contributors or advanced users:

```bash
# Clone repository
git clone https://github.com/Nubaeon/empirica.git
cd empirica

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Install in development mode
pip install -e .

# Run tests
pytest tests/

# Build documentation
cd docs
make html
```

---

## Optional Components

### Vision System (for slide assessment)
```bash
# Install vision extras
pip install empirica[vision]

# System dependencies
sudo apt-get install tesseract-ocr  # Linux
brew install tesseract              # macOS

# Test
python -m empirica.vision.slide_processor --help
```

### MCP Server (for Claude Desktop)
```bash
# Already included in main install
# Configure in Claude Desktop settings

# See: guides/MCP_INSTALLATION.md
```

---

## Uninstallation

```bash
# Uninstall package
pip uninstall empirica

# Remove user data (optional)
rm -rf ~/.empirica/

# Remove project data (optional)
# From each project directory:
rm -rf .empirica/
```

---

## Next Steps

- **Get started:** [01_START_HERE.md](01_START_HERE.md)
- **Learn the concepts:** [EMPIRICA_EXPLAINED_SIMPLE.md](EMPIRICA_EXPLAINED_SIMPLE.md)
- **See all commands:** [CLI Commands Unified](../developers/CLI_COMMANDS_UNIFIED.md)

---

## Getting Help

- **Issues:** Check [03_TROUBLESHOOTING.md](03_TROUBLESHOOTING.md)
- **Documentation:** Browse `docs/` directory
- **Source code:** [github.com/Nubaeon/empirica](https://github.com/Nubaeon/empirica)

---

**Installation complete!** Start with `empirica session-create --ai-id myai` and check [01_START_HERE.md](01_START_HERE.md) for next steps.
