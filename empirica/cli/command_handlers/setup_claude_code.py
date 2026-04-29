#!/usr/bin/env python3
"""
Setup Claude Code Command - Configure Claude Code integration for Empirica

This command configures:
- Plugin files in ~/.claude/plugins/local/empirica/
- CLAUDE.md system prompt in ~/.claude/CLAUDE.md
- Hooks in ~/.claude/settings.json (sentinel, compact, session lifecycle)
- MCP server in ~/.claude/mcp.json
- Marketplace registration

Replaces the bash install.sh for Homebrew users who already have empirica installed.

Author: Rovo Dev
Date: 2026-02-10
"""

import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

PLUGIN_NAME = "empirica"
PLUGIN_VERSION = "1.8.16"


def _find_python() -> str:
    """Find a suitable Python >= 3.10, mimicking install.sh logic"""
    min_major, min_minor = 3, 10

    candidates = []

    # Prefer plain python3 first (portable, standard)
    if shutil.which("python3"):
        candidates.append("python3")

    # Then check versioned binaries as fallback (highest first)
    for ver in [13, 12, 11, 10]:
        cmd = f"python3.{ver}"
        if shutil.which(cmd):
            candidates.append(cmd)

    # Check macOS framework paths
    for ver in [13, 12, 11, 10]:
        fw = f"/Library/Frameworks/Python.framework/Versions/3.{ver}/bin/python3.{ver}"
        if Path(fw).exists():
            candidates.append(fw)

    # Check Homebrew paths
    for ver in [13, 12, 11, 10]:
        for prefix in ["/opt/homebrew", "/usr/local"]:
            brew = f"{prefix}/bin/python3.{ver}"
            if Path(brew).exists():
                candidates.append(brew)

    # Test each candidate for minimum version
    for py in candidates:
        try:
            result = subprocess.run(
                [py, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                py_ver = result.stdout.strip()
                major, minor = map(int, py_ver.split('.'))
                if major >= min_major and minor >= min_minor:
                    return py
        except Exception:
            continue

    # Fallback to current interpreter
    return sys.executable


def _get_plugin_source_dir() -> Path | None:
    """Find the bundled plugin source directory.

    The canonical source lives inside the empirica package at:
    empirica/plugins/claude-code-integration/
    """
    module_dir = Path(__file__).parent.parent.parent  # empirica/cli/command_handlers -> empirica/
    bundled_path = module_dir / "plugins" / "claude-code-integration"

    if bundled_path.exists() and (bundled_path / "hooks").exists():
        return bundled_path

    return None


def _ensure_json_file(path: Path, default: dict) -> dict:
    """Ensure JSON file exists and return its contents"""
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return default.copy()


def _write_json_file(path: Path, data: dict):
    """Write JSON file atomically"""
    temp_path = path.with_suffix('.tmp')
    with open(temp_path, 'w') as f:
        json.dump(data, f, indent=2)
    temp_path.rename(path)


def _hook_exists(hooks_list: list, pattern: str) -> bool:
    """Check if a hook with the given pattern already exists"""
    for hook_entry in hooks_list:
        for hook in hook_entry.get('hooks', []):
            cmd = hook.get('command', '')
            if pattern in cmd:
                return True
    return False


def _register_hook(settings, event, detect_pattern, entries, label, output_format, use_extend=False):
    """Register hook entries for an event if not already present.

    Args:
        settings: The settings dict (must already have 'hooks' key).
        event: Hook event name (e.g. 'PreToolUse').
        detect_pattern: Pattern string to check via _hook_exists.
        entries: List of hook entry dicts to add.
        label: Human-readable label for status messages.
        output_format: 'json' suppresses print output.
        use_extend: If True, use extend() instead of append() for multi-entry hooks.
    """
    if event not in settings['hooks']:
        settings['hooks'][event] = []

    if not _hook_exists(settings['hooks'][event], detect_pattern):
        if use_extend:
            settings['hooks'][event].extend(entries)
        else:
            for entry in entries:
                settings['hooks'][event].append(entry)
        if output_format != 'json':
            print(f"   ✓ {label} configured")
    else:
        if output_format != 'json':
            print(f"   {label} already configured")


def _force_clean_hooks(settings, output_format):
    """Remove only Empirica hooks from settings, preserving other plugins' hooks.

    Previously this did settings['hooks'] = {} which nuked ALL hooks
    including Railway, Superpowers, and custom hooks. Now filters by
    plugin path to only remove Empirica's entries.
    """
    plugin_path_patterns = [
        f'plugins/local/{PLUGIN_NAME}/',      # Current name
        'plugins/local/empirica-integration/', # Legacy name
        'plugins/local/empirica/',             # Short name
    ]
    for event in list(settings.get('hooks', {}).keys()):
        original_count = len(settings['hooks'][event])
        settings['hooks'][event] = [
            hook for hook in settings['hooks'][event]
            if not any(
                pattern in str(hook)
                for pattern in plugin_path_patterns
            )
        ]
        removed = original_count - len(settings['hooks'][event])
        if removed > 0:
            logger.debug(f"--force: removed {removed} Empirica hooks from {event}")
        # Clean up empty event lists
        if not settings['hooks'][event]:
            del settings['hooks'][event]

    settings.pop('statusLine', None)
    if output_format != 'json':
        print("   --force: cleared Empirica hooks and statusLine (other plugins preserved)")

    # Also clean up legacy plugin name from enabledPlugins
    legacy_key = "empirica-integration@local"
    if legacy_key in settings.get('enabledPlugins', {}):
        del settings['enabledPlugins'][legacy_key]
        if output_format != 'json':
            print("   --force: removed legacy empirica-integration@local from enabledPlugins")


def _configure_statusline(settings, plugin_dir, python_cmd, output_format):
    """Configure StatusLine command in settings.

    Claude Code pipes session JSON to statusline stdin — do NOT redirect stdin.
    """
    if 'statusLine' not in settings:
        statusline_script = plugin_dir / "scripts" / "statusline_empirica.py"
        settings['statusLine'] = {
            "type": "command",
            "command": f"{python_cmd} {statusline_script}"
        }
        if output_format != 'json':
            print("   ✓ StatusLine configured")
    else:
        if output_format != 'json':
            print("   StatusLine already configured")


def _register_all_hooks(settings, plugin_dir, python_cmd, output_format):
    """Register all Empirica hooks into settings['hooks']."""
    if 'hooks' not in settings:
        settings['hooks'] = {}

    sentinel_script = f"{python_cmd} {plugin_dir}/hooks/sentinel-gate.py"
    _register_hook(settings, 'PreToolUse', 'sentinel-gate', [
        {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": sentinel_script, "timeout": 10}]},
        {"matcher": "Bash", "hooks": [{"type": "command", "command": sentinel_script, "timeout": 10}]},
    ], "PreToolUse (Sentinel) hooks", output_format, use_extend=True)

    precompact_script = f"{python_cmd} {plugin_dir}/hooks/pre-compact.py"
    _register_hook(settings, 'PreCompact', 'pre-compact.py', [
        {"matcher": "auto|manual", "hooks": [{"type": "command", "command": precompact_script, "timeout": 30}]},
    ], "PreCompact hook", output_format)

    postcompact_script = f"{python_cmd} {plugin_dir}/hooks/post-compact.py"
    sessioninit_script = f"{python_cmd} {plugin_dir}/hooks/session-init.py"
    ewm_script = f"{python_cmd} {plugin_dir}/hooks/ewm-protocol-loader.py"
    _register_hook(settings, 'SessionStart', 'post-compact.py', [
        {
            "matcher": "compact",
            "hooks": [
                {"type": "command", "command": postcompact_script, "timeout": 30},
                {"type": "command", "command": ewm_script, "timeout": 10, "allowFailure": True}
            ]
        },
        {
            "matcher": "startup|resume",
            "hooks": [
                {"type": "command", "command": sessioninit_script, "timeout": 30},
                {"type": "command", "command": ewm_script, "timeout": 10, "allowFailure": True}
            ]
        },
    ], "SessionStart hooks", output_format, use_extend=True)

    postflight_script = f"{python_cmd} {plugin_dir}/hooks/session-end-postflight.py"
    curate_script = f"{python_cmd} {plugin_dir}/hooks/curate-snapshots.py --output json"
    _register_hook(settings, 'SessionEnd', 'session-end-postflight.py', [
        {
            "matcher": ".*",
            "hooks": [
                {"type": "command", "command": postflight_script, "timeout": 20},
                {"type": "command", "command": curate_script, "timeout": 15, "allowFailure": True}
            ]
        },
    ], "SessionEnd hooks", output_format)

    substart_script = f"{python_cmd} {plugin_dir}/hooks/subagent-start.py"
    _register_hook(settings, 'SubagentStart', 'subagent-start.py', [
        {"matcher": ".*", "hooks": [{"type": "command", "command": substart_script, "timeout": 10, "allowFailure": True}]},
    ], "SubagentStart hook", output_format)

    substop_script = f"{python_cmd} {plugin_dir}/hooks/subagent-stop.py"
    _register_hook(settings, 'SubagentStop', 'subagent-stop.py', [
        {"matcher": ".*", "hooks": [{"type": "command", "command": substop_script, "timeout": 15, "allowFailure": True}]},
    ], "SubagentStop hook", output_format)

    router_script = f"{python_cmd} {plugin_dir}/hooks/tool-router.py"
    _register_hook(settings, 'UserPromptSubmit', 'tool-router.py', [
        {"matcher": ".*", "hooks": [{"type": "command", "command": router_script, "timeout": 3, "allowFailure": True}]},
    ], "UserPromptSubmit hook", output_format)

    # Context-shift tracker (classifies solicited vs unsolicited prompts)
    cs_script = f"{python_cmd} {plugin_dir}/hooks/context-shift-tracker.py"
    if not _hook_exists(settings['hooks'].get('UserPromptSubmit', []), 'context-shift-tracker.py'):
        settings['hooks'].setdefault('UserPromptSubmit', []).append({
            "matcher": ".*",
            "hooks": [{"type": "command", "command": cs_script, "timeout": 5, "allowFailure": True}]
        })
        if output_format != 'json':
            print("   ✓ Context-shift tracker configured")

    # Loop install pickup — surfaces pending install requests from cockpit as
    # additionalContext on the next prompt so the running Claude can call
    # CronCreate via /loop.
    install_script = f"{python_cmd} {plugin_dir}/hooks/loop-install-pickup.py"
    if not _hook_exists(settings['hooks'].get('UserPromptSubmit', []), 'loop-install-pickup.py'):
        settings['hooks'].setdefault('UserPromptSubmit', []).append({
            "matcher": ".*",
            "hooks": [{"type": "command", "command": install_script, "timeout": 5, "allowFailure": True}]
        })
        if output_format != 'json':
            print("   ✓ Loop install pickup configured")

    entity_script = f"{python_cmd} {plugin_dir}/hooks/entity-extractor.py"
    _register_hook(settings, 'PostToolUse', 'entity-extractor.py', [
        {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": entity_script, "timeout": 5, "allowFailure": True}]},
    ], "PostToolUse (entity extraction) hook", output_format)

    task_script = f"{python_cmd} {plugin_dir}/hooks/task-completed.py"
    _register_hook(settings, 'TaskCompleted', 'task-completed.py', [
        {"matcher": ".*", "hooks": [{"type": "command", "command": task_script, "timeout": 10, "allowFailure": True}]},
    ], "TaskCompleted hook", output_format)

    failure_script = f"{python_cmd} {plugin_dir}/hooks/tool-failure.py"
    _register_hook(settings, 'PostToolUseFailure', 'tool-failure.py', [
        {"matcher": ".*", "hooks": [{"type": "command", "command": failure_script, "timeout": 5, "allowFailure": True}]},
    ], "PostToolUseFailure hook", output_format)

    stop_script = f"{python_cmd} {plugin_dir}/hooks/transaction-enforcer.py"
    _register_hook(settings, 'Stop', 'transaction-enforcer.py', [
        {"matcher": ".*", "hooks": [{"type": "command", "command": stop_script, "timeout": 5, "allowFailure": True}]},
    ], "Stop (transaction enforcer) hook", output_format)


def _configure_settings(settings, settings_file, plugin_dir, python_cmd, force, output_format, plugin_key):
    """Configure settings.json: enable plugin, register hooks, set statusline.

    Extracted from handle_setup_claude_code_command to reduce handler complexity.
    """
    if output_format != 'json':
        print("\n⚙️  Configuring settings.json...")

    settings = _ensure_json_file(settings_file, {})

    # Ensure enabledPlugins exists and enable the plugin
    if 'enabledPlugins' not in settings:
        settings['enabledPlugins'] = {}
    plugin_key = f"{PLUGIN_NAME}@local"
    settings['enabledPlugins'][plugin_key] = True
    if output_format != 'json':
        print("   ✓ Plugin enabled")

    if force:
        _force_clean_hooks(settings, output_format)

    _configure_statusline(settings, plugin_dir, python_cmd, output_format)
    _register_all_hooks(settings, plugin_dir, python_cmd, output_format)

    # Write settings.json
    _write_json_file(settings_file, settings)


def _setup_directories(output_format):
    """Create all required directories and bootstrap active_work.json.

    Returns:
        Tuple of (home, claude_dir, plugins_dir, plugin_dir, marketplace_dir, empirica_dir)
    """
    home = Path.home()
    claude_dir = home / ".claude"
    plugins_dir = claude_dir / "plugins" / "local"
    plugin_dir = plugins_dir / PLUGIN_NAME
    marketplace_dir = plugins_dir / ".claude-plugin"
    empirica_dir = home / ".empirica"

    claude_dir.mkdir(parents=True, exist_ok=True)
    plugins_dir.mkdir(parents=True, exist_ok=True)
    marketplace_dir.mkdir(parents=True, exist_ok=True)
    empirica_dir.mkdir(parents=True, exist_ok=True)
    (empirica_dir / "instance_projects").mkdir(exist_ok=True, mode=0o700)
    (empirica_dir / "statusline_cache").mkdir(exist_ok=True, mode=0o700)

    # Bootstrap active_work.json
    active_work_file = empirica_dir / "active_work.json"
    if not active_work_file.exists():
        active_work = {
            "project_path": None,
            "folder_name": None,
            "claude_session_id": None,
            "empirica_session_id": None,
            "source": "setup-claude-code",
            "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+0000')
        }
        _write_json_file(active_work_file, active_work)
        if output_format != 'json':
            print("   ✓ Created ~/.empirica/active_work.json")

    return home, claude_dir, plugins_dir, plugin_dir, marketplace_dir, empirica_dir


def _install_plugin_files(source_dir, plugin_dir, output_format):
    """Install plugin files: migrate old dirs, copy source, set permissions."""
    if output_format != 'json':
        print("\n📦 Installing plugin files...")

    # Migration: remove old empirica-integration directory if it exists (renamed to empirica in 1.7.0)
    old_plugin_dir = plugin_dir.parent / "empirica-integration"
    if old_plugin_dir.exists() and old_plugin_dir != plugin_dir:
        shutil.rmtree(old_plugin_dir)
        if output_format != 'json':
            print("   🔄 Migrated: removed old empirica-integration plugin directory")

    # Also clean orphaned cache (prevents duplicate hook execution)
    old_cache_dir = Path.home() / '.claude' / 'plugins' / 'cache' / 'local' / 'empirica-integration'
    if old_cache_dir.exists():
        shutil.rmtree(old_cache_dir)

    # Always sync plugin files — hooks and scripts must track the installed version.
    # Previous behavior skipped this if directory existed, causing stale scripts.
    if plugin_dir.exists():
        shutil.rmtree(plugin_dir)

    # Copy excluding __pycache__ and .git
    def ignore_patterns(directory, files):
        return [f for f in files if f in ('__pycache__', '.git', '.pyc')]

    shutil.copytree(source_dir, plugin_dir, ignore=ignore_patterns)

    # Make hooks executable
    hooks_dir = plugin_dir / "hooks"
    if hooks_dir.exists():
        for hook_file in hooks_dir.glob("*.py"):
            hook_file.chmod(0o755)
        for hook_file in hooks_dir.glob("*.sh"):
            hook_file.chmod(0o755)

    scripts_dir = plugin_dir / "scripts"
    if scripts_dir.exists():
        for script_file in scripts_dir.glob("*.py"):
            script_file.chmod(0o755)

    if output_format != 'json':
        print(f"   ✓ Plugin installed to {plugin_dir}")


def _install_claude_md(plugin_dir, claude_dir, use_full, output_format):
    """Install Empirica system prompt and CLAUDE.md include reference."""
    if output_format != 'json':
        print("\n📝 Installing Empirica system prompt...")

    # Select prompt template: lean (default) or full (traditional, opt-in)
    if use_full:
        claude_md_src = plugin_dir / "templates" / "CLAUDE.md"
        prompt_label = "full (traditional)"
    else:
        claude_md_src = plugin_dir / "templates" / "empirica-system-prompt-lean.md"
        prompt_label = "lean core (skills on demand)"

    claude_md_dst = claude_dir / "CLAUDE.md"
    empirica_prompt_dst = claude_dir / "empirica-system-prompt.md"
    include_line = "@~/.claude/empirica-system-prompt.md"

    if claude_md_src.exists():
        # Always write Empirica prompt to separate file (safe to overwrite)
        shutil.copy2(claude_md_src, empirica_prompt_dst)
        if output_format != 'json':
            print(f"   ✓ Empirica prompt ({prompt_label}) written to ~/.claude/empirica-system-prompt.md")

        if claude_md_dst.exists():
            existing_content = claude_md_dst.read_text()
            if include_line not in existing_content:
                new_content = f"{include_line}\n\n{existing_content}"
                claude_md_dst.write_text(new_content)
                if output_format != 'json':
                    print("   ✓ Added include reference to existing ~/.claude/CLAUDE.md")
            else:
                if output_format != 'json':
                    print("   ✓ Include reference already present in ~/.claude/CLAUDE.md")
        else:
            claude_md_dst.write_text(f"{include_line}\n")
            if output_format != 'json':
                print("   ✓ Created ~/.claude/CLAUDE.md with Empirica include")
    else:
        if output_format != 'json':
            print("   ⚠️  CLAUDE.md template not found in plugin")


def _register_marketplace(marketplace_dir, plugins_dir, claude_dir, plugin_dir, plugin_key, output_format):
    """Register plugin in marketplace, installed_plugins, and known_marketplaces."""
    if output_format != 'json':
        print("\n📋 Registering in marketplace...")

    marketplace_file = marketplace_dir / "marketplace.json"
    marketplace = _ensure_json_file(marketplace_file, {
        "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
        "name": "local",
        "description": "Local development plugins",
        "owner": {"name": "Local", "email": "dev@localhost"},
        "plugins": []
    })

    plugin_names = [p.get('name') for p in marketplace.get('plugins', [])]
    if PLUGIN_NAME not in plugin_names:
        marketplace.setdefault('plugins', []).append({
            "name": PLUGIN_NAME,
            "description": "Noetic firewall + CASCADE workflow automation for Claude Code",
            "version": PLUGIN_VERSION,
            "author": {"name": "Empirica Project", "url": "https://github.com/Nubaeon/empirica"},
            "source": f"./{PLUGIN_NAME}",
            "category": "productivity"
        })
        _write_json_file(marketplace_file, marketplace)
        if output_format != 'json':
            print("   ✓ Added to marketplace.json")

    # Installed plugins registration
    installed_plugins_file = claude_dir / "plugins" / "installed_plugins.json"
    installed_plugins = _ensure_json_file(installed_plugins_file, {"version": 2, "plugins": {}})

    install_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    installed_plugins['plugins'][plugin_key] = [{
        "scope": "user",
        "installPath": str(plugin_dir),
        "version": PLUGIN_VERSION,
        "installedAt": install_date,
        "lastUpdated": install_date,
        "isLocal": True
    }]
    _write_json_file(installed_plugins_file, installed_plugins)
    if output_format != 'json':
        print("   ✓ Added to installed_plugins.json")

    # Known marketplaces
    known_marketplaces_file = claude_dir / "plugins" / "known_marketplaces.json"
    known_marketplaces = _ensure_json_file(known_marketplaces_file, {})

    if 'local' not in known_marketplaces:
        known_marketplaces['local'] = {
            "source": {"source": "directory", "path": str(plugins_dir)},
            "installLocation": str(plugins_dir),
            "lastUpdated": install_date
        }
        _write_json_file(known_marketplaces_file, known_marketplaces)
        if output_format != 'json':
            print("   ✓ Local marketplace registered")


def _configure_mcp_server(claude_dir, home, force, output_format):
    """Find and configure the empirica-mcp MCP server. Returns (mcp_installed, mcp_cmd)."""
    if output_format != 'json':
        print("\n🔌 Configuring MCP server...")

    # Find empirica-mcp — prefer the binary matching the current Python environment
    # This prevents stale pipx binaries from shadowing dev installs
    mcp_cmd = None
    # Priority 1: Same virtualenv as the running empirica CLI
    venv_prefix = Path(sys.executable).parent
    venv_mcp = venv_prefix / "empirica-mcp"
    if venv_mcp.exists():
        mcp_cmd = str(venv_mcp)
    # Priority 2: shutil.which (whatever's first in PATH)
    if not mcp_cmd:
        mcp_cmd = shutil.which("empirica-mcp")
    # Priority 3: pipx default location
    if not mcp_cmd:
        local_bin = home / ".local" / "bin" / "empirica-mcp"
        if local_bin.exists():
            mcp_cmd = str(local_bin)

    if not mcp_cmd:
        mcp_cmd = _try_install_mcp_via_pipx(home, output_format)

    if not mcp_cmd:
        return False, None

    mcp_file = claude_dir / "mcp.json"
    mcp_config = _ensure_json_file(mcp_file, {"mcpServers": {}})

    existing = mcp_config.get('mcpServers', {}).get('empirica')
    needs_update = (
        not existing
        or force
        or existing.get('command') != mcp_cmd  # Binary path changed
    )
    if needs_update:
        mcp_config.setdefault('mcpServers', {})['empirica'] = {
            "command": mcp_cmd,
            "args": [],
            "type": "stdio",
            "tools": ["*"],
            "description": "Empirica epistemic framework - CASCADE workflow, goals, findings"
        }
        _write_json_file(mcp_file, mcp_config)
        if output_format != 'json':
            if existing and existing.get('command') != mcp_cmd:
                print(f"   ✓ MCP server updated: {mcp_cmd}")
                print(f"     (was: {existing.get('command', 'unknown')})")
            else:
                print(f"   ✓ MCP server configured: {mcp_cmd}")
    else:
        if output_format != 'json':
            print(f"   MCP server already configured: {mcp_cmd}")

    return True, mcp_cmd


def _try_install_mcp_via_pipx(home, output_format):
    """Attempt to install empirica-mcp via pipx. Returns mcp_cmd or None."""
    if shutil.which("pipx"):
        if output_format != 'json':
            print("   Installing empirica-mcp via pipx...")
        try:
            result = subprocess.run(
                ["pipx", "install", "empirica-mcp"],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                mcp_cmd = shutil.which("empirica-mcp")
                if not mcp_cmd:
                    mcp_cmd = str(home / ".local" / "bin" / "empirica-mcp")
                if output_format != 'json':
                    print("   ✓ empirica-mcp installed via pipx")
                return mcp_cmd
            else:
                if output_format != 'json':
                    print(f"   ⚠️  pipx install failed: {result.stderr[:100]}")
        except Exception as e:
            if output_format != 'json':
                print(f"   ⚠️  pipx install failed: {e}")
    else:
        if output_format != 'json':
            print("   ⚠️  pipx not available - install empirica-mcp manually:")
            print("      pipx install empirica-mcp")
    return None


def _check_semantic_layer():
    """Check Ollama and Qdrant status. Returns (ollama_ok, embedding_ok, qdrant_ok)."""
    ollama_ok = False
    embedding_ok = False
    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            ollama_ok = True
            if "qwen3-embedding:8b" in result.stdout:
                embedding_ok = True
                print("⚠ Ollama: qwen3-embedding:8b detected (4096d) — this may cause dimension mismatches")
                print("    Empirica expects 1024d. Pull the default tag instead:")
                print("    ollama pull qwen3-embedding")
            elif "qwen3-embedding" in result.stdout:
                embedding_ok = True
                print("✓ Ollama: installed, qwen3-embedding available (1024d)")
            elif "nomic-embed-text" in result.stdout:
                embedding_ok = True
                print("✓ Ollama: installed, nomic-embed-text available (768d)")
                print("    If Qdrant collections were created at 1024d, switch models and run:")
                print("    empirica rebuild --qdrant")
            else:
                print("⚠ Ollama: installed, but no embedding model pulled")
                print("    Fix: ollama pull qwen3-embedding")
        else:
            print("⚠ Ollama: installed but not running")
            print("    Fix: ollama serve")
    except FileNotFoundError:
        print("✗ Ollama: not installed")
        print("    Install: curl -fsSL https://ollama.com/install.sh | sh")
        print("    Then: ollama pull qwen3-embedding")
    except Exception:
        print("⚠ Ollama: could not check status")

    qdrant_ok = False
    qdrant_url = os.environ.get("EMPIRICA_QDRANT_URL", "http://localhost:6333")
    try:
        import urllib.request
        urllib.request.urlopen(qdrant_url, timeout=2)
        qdrant_ok = True
        print(f"✓ Qdrant: running at {qdrant_url}")
    except Exception:
        print(f"✗ Qdrant: not running at {qdrant_url}")
        print("    Docker: docker run -d -p 6333:6333 -v ~/.qdrant:/qdrant/storage qdrant/qdrant")
        print("    Binary: https://github.com/qdrant/qdrant/releases")

    return ollama_ok, embedding_ok, qdrant_ok


def _print_human_summary(plugin_dir, settings_file, mcp_installed, skip_claude_md, claude_dir):
    """Print the human-readable setup summary including semantic layer check."""
    print("\n" + "━" * 60)
    print(f"✅ {PLUGIN_NAME} v{PLUGIN_VERSION} configured successfully!")
    print("━" * 60)
    print()
    print(f"📍 Plugin:     {plugin_dir}")
    print("📝 CLAUDE.md:  ~/.claude/CLAUDE.md")
    print("⚙️  Settings:   ~/.claude/settings.json")
    print()
    print("━" * 60)
    print("WHAT'S CONFIGURED:")
    print("━" * 60)
    print()
    print("🛡️  Sentinel Gate (Noetic Firewall)")
    print("    - Noetic tools (Read, Grep, etc.) always allowed")
    print("    - Praxic tools (Edit, Write, Bash) require CHECK")
    print()
    print("📋 CASCADE Workflow (Pre/Post Compact)")
    print("    - Auto-saves epistemic state before compact")
    print("    - Auto-loads context after compact")
    print()
    print("📊 StatusLine")
    print("    - Shows session ID, phase, know/uncertainty vectors")
    print()
    if mcp_installed:
        print("🔌 MCP Server")
        print("    - Full Empirica API available to Claude")
        print()
    print("🎯 Skills")
    print("    - /empirica - Full command reference")
    print()

    # Semantic layer check
    print("━" * 60)
    print("SEMANTIC LAYER (for pattern injection & memory):")
    print("━" * 60)
    print()

    ollama_ok, embedding_ok, qdrant_ok = _check_semantic_layer()

    print()
    if ollama_ok and embedding_ok and qdrant_ok:
        print("✓ Semantic layer ready — PREFLIGHT will inject patterns,")
        print("  findings, dead-ends, and calibration from prior sessions")
    else:
        print("⚠ Without the semantic layer, Empirica works but:")
        print("  - No pattern/anti-pattern injection in PREFLIGHT")
        print("  - No cross-session memory (findings, dead-ends)")
        print("  - No project-search or project-embed")
        print("  - No eidetic/episodic memory across compactions")
    print()

    print("━" * 60)
    print("NEXT STEPS:")
    print("━" * 60)
    print()
    print("1. Restart Claude Code to load the plugin")
    print()
    print("2. Verify with: /plugin")
    print(f"   Should show: {PLUGIN_NAME}@local")
    print()
    print("3. Connect MCP server: /mcp")
    print("   Should show: empirica connected")
    if not (ollama_ok and embedding_ok and qdrant_ok):
        print()
        print("4. Set up semantic layer (recommended):")
        if not ollama_ok:
            print("   curl -fsSL https://ollama.com/install.sh | sh")
        if ollama_ok and not embedding_ok:
            print("   ollama pull qwen3-embedding")
        if not qdrant_ok:
            print("   docker run -d -p 6333:6333 -v ~/.qdrant:/qdrant/storage qdrant/qdrant")
    print()
    print("To disable sentinel gating temporarily:")
    print("  export EMPIRICA_SENTINEL_LOOPING=false")
    print()
    print("🧠 Happy epistemic coding!")


def handle_setup_claude_code_command(args):
    """Handle setup-claude-code command"""
    try:
        output_format = getattr(args, 'output', 'human')
        force = getattr(args, 'force', False)
        skip_mcp = getattr(args, 'skip_mcp', False)
        skip_claude_md = getattr(args, 'skip_claude_md', False)
        use_full = getattr(args, 'full_prompt', False)

        # Find bundled plugins
        source_dir = _get_plugin_source_dir()
        if not source_dir:
            if output_format == 'json':
                print(json.dumps({
                    "ok": False,
                    "error": "Could not find bundled plugin files",
                    "hint": "Run from a valid Empirica installation or dev environment"
                }, indent=2))
            else:
                print("❌ Error: Could not find bundled plugin files")
                print("   Run from a valid Empirica installation or dev environment")
            return None

        if output_format != 'json':
            print("🧠 Setting up Claude Code integration...")
            print(f"   Source: {source_dir}\n")

        python_cmd = _find_python()
        if output_format != 'json':
            print(f"   Using Python: {python_cmd}")

        # Stage 1: Create directories
        home, claude_dir, plugins_dir, plugin_dir, marketplace_dir, _empirica_dir = \
            _setup_directories(output_format)

        # Stage 2: Install plugin files
        _install_plugin_files(source_dir, plugin_dir, output_format)

        # Stage 3: Install CLAUDE.md
        if not skip_claude_md:
            _install_claude_md(plugin_dir, claude_dir, use_full, output_format)

        # Stage 4: Configure settings.json
        settings_file = claude_dir / "settings.json"
        settings = _ensure_json_file(settings_file, {})
        plugin_key = f"{PLUGIN_NAME}@local"
        _configure_settings(settings, settings_file, plugin_dir, python_cmd, force, output_format, plugin_key)

        # Stage 5: Marketplace registration
        _register_marketplace(marketplace_dir, plugins_dir, claude_dir, plugin_dir, plugin_key, output_format)

        # Stage 6: MCP server
        mcp_installed = False
        mcp_cmd = None
        if not skip_mcp:
            mcp_installed, mcp_cmd = _configure_mcp_server(claude_dir, home, force, output_format)

        # Stage 7: Output
        if output_format == 'json':
            return {
                "ok": True,
                "plugin_dir": str(plugin_dir),
                "claude_md": str(claude_dir / "CLAUDE.md") if not skip_claude_md else None,
                "settings_file": str(settings_file),
                "mcp_configured": mcp_installed,
                "mcp_command": mcp_cmd,
                "hooks_configured": [
                    "PreToolUse (Sentinel)",
                    "PreCompact",
                    "SessionStart",
                    "SessionEnd",
                    "SubagentStart",
                    "SubagentStop",
                    "UserPromptSubmit"
                ],
                "message": "Claude Code integration configured successfully"
            }
        else:
            _print_human_summary(plugin_dir, settings_file, mcp_installed, skip_claude_md, claude_dir)

        return None

    except Exception as e:
        if getattr(args, 'output', 'human') == 'json':
            print(json.dumps({
                "ok": False,
                "error": str(e)
            }, indent=2))
        else:
            from ..cli_utils import handle_cli_error
            handle_cli_error(e, "Setup Claude Code", getattr(args, 'verbose', False))
        return None
