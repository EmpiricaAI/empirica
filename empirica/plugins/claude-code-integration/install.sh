#!/bin/bash
# empirica installer - one-line install for Claude Code
# Usage: curl -fsSL https://raw.githubusercontent.com/EmpiricaAI/empirica/main/empirica/plugins/claude-code-integration/install.sh | bash
#
# This script installs empirica via pip and delegates plugin setup to
# `empirica setup-claude-code`, which is the single canonical method
# for installing/syncing the Claude Code plugin.
#
# What it does:
#   1. Finds a suitable Python >= 3.10
#   2. Installs empirica via pip (includes bundled plugin files)
#   3. Runs `empirica setup-claude-code` (configures hooks, CLAUDE.md, MCP, etc.)
#
# For updates, just re-run this script or: empirica setup-claude-code --force

set -e

PLUGIN_VERSION="1.12.33"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10

echo "🧠 Installing Empirica + Claude Code integration v${PLUGIN_VERSION}..."
echo ""

# ==================== FIND PYTHON ====================

find_python() {
    local candidates=()

    # Check common versioned binaries (highest first)
    for ver in 13 12 11 10; do
        command -v "python3.${ver}" &>/dev/null && candidates+=("python3.${ver}")
    done

    # Check plain python3
    command -v python3 &>/dev/null && candidates+=("python3")

    # Check macOS framework paths
    for ver in 13 12 11 10; do
        local fw="/Library/Frameworks/Python.framework/Versions/3.${ver}/bin/python3.${ver}"
        [ -f "$fw" ] && candidates+=("$fw")
    done

    # Check Homebrew paths (Apple Silicon + Intel)
    for ver in 13 12 11 10; do
        for prefix in /opt/homebrew /usr/local; do
            local brew="${prefix}/bin/python3.${ver}"
            [ -f "$brew" ] && candidates+=("$brew")
        done
    done

    # Test each candidate for minimum version
    for py in "${candidates[@]}"; do
        local py_ver
        py_ver=$("$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null) || continue
        local major minor
        major=$(echo "$py_ver" | cut -d. -f1)
        minor=$(echo "$py_ver" | cut -d. -f2)
        if [ "$major" -ge "$MIN_PYTHON_MAJOR" ] && [ "$minor" -ge "$MIN_PYTHON_MINOR" ]; then
            echo "$py"
            return 0
        fi
    done

    return 1
}

PYTHON_CMD=$(find_python)
if [ -z "$PYTHON_CMD" ]; then
    echo "❌ Error: Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ is required"
    echo ""
    python3 --version 2>&1 | sed 's/^/   Found: /' || echo "   python3 not found"
    echo ""
    echo "   Install Python 3.10+ via:"
    echo "     macOS:  brew install python@3.11"
    echo "     Ubuntu: sudo apt install python3.11"
    echo "     pyenv:  pyenv install 3.11 && pyenv global 3.11"
    exit 1
fi

PYTHON_VERSION=$("$PYTHON_CMD" --version 2>&1)
echo "   Using: $PYTHON_CMD ($PYTHON_VERSION)"

# ==================== INSTALL EMPIRICA ====================

echo ""
echo "📦 Installing empirica package..."

# Use pip to install (or upgrade) empirica
# This bundles the plugin files in empirica/plugins/claude-code-integration/
PIP_CMD="$PYTHON_CMD -m pip"

if $PIP_CMD show empirica &>/dev/null; then
    echo "   Empirica already installed, upgrading..."
    $PIP_CMD install --upgrade empirica 2>&1 | tail -1
else
    $PIP_CMD install empirica 2>&1 | tail -3
fi

# Verify installation
if ! command -v empirica &>/dev/null; then
    # Try adding common pip install locations to PATH
    export PATH="$HOME/.local/bin:$HOME/Library/Python/3.11/bin:$PATH"
    if ! command -v empirica &>/dev/null; then
        echo "❌ Error: empirica CLI not found after installation"
        echo "   You may need to add ~/.local/bin to your PATH:"
        echo "   export PATH=\"\$HOME/.local/bin:\$PATH\""
        exit 1
    fi
fi

EMPIRICA_VERSION=$(empirica --version 2>/dev/null || echo "unknown")
echo "   ✓ Empirica installed ($EMPIRICA_VERSION)"

# ==================== SETUP CLAUDE CODE PLUGIN ====================

echo ""
echo "⚙️  Setting up Claude Code integration..."
echo ""

# setup-claude-code handles everything:
# - Plugin files to ~/.claude/plugins/local/empirica/
# - CLAUDE.md system prompt
# - Hook configuration (Sentinel, compact, session lifecycle)
# - MCP server setup
# - Marketplace registration
# - Statusline configuration
empirica setup-claude-code --force

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Installation complete!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "NEXT STEPS:"
echo ""
echo "  1. Restart Claude Code to load the plugin"
echo "  2. Verify with: /plugin"
echo "  3. Connect MCP: /mcp"
echo ""
echo "TO UPDATE:"
echo ""
echo "  empirica setup-claude-code --force"
echo ""
echo "🧠 Happy epistemic coding!"
