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
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

PLUGIN_NAME = "empirica"
PLUGIN_VERSION = "1.12.14"
# Written into the installed plugin dir; the empirica version its files came
# from. Drives drift-sync (empirica plugin-sync / session-init auto-heal).
PLUGIN_VERSION_STAMP = ".plugin-version"

# Timeout (seconds) for the lightweight per-prompt / per-SessionStart hooks
# (tool-router, context-shift tracker, monitor-arm, the loop/listener pickups).
# Raised from the old 3s/5s: on a many-instance restart-herd box (high load,
# slow cold Python starts) those hooks were timing out and Claude Code
# silently discarded their stdout — i.e. their context injection never landed.
# All are allowFailure=True, so a generous ceiling only helps; the heavy hooks
# (compaction, session-init, postflight) keep their own larger explicit timeouts.
LIGHT_HOOK_TIMEOUT = 10


def _resolve_empirica_version() -> str:
    """Return the installed empirica version, or 'unknown' on miss.

    Used to render `{{ empirica_version }}` placeholders in templates so the
    written system prompt always reflects what's actually installed (rather
    than whatever was hardcoded in the template at template-author time).
    """
    try:
        from empirica import __version__

        return str(__version__).strip() or "unknown"
    except Exception:
        try:
            from importlib.metadata import version

            return version("empirica")
        except Exception:
            return "unknown"


def _is_cortex_configured() -> bool:
    """True iff `cortex:` block in credentials.yaml carries usable url+api_key.

    Used to decide whether to inject cortex-specific guidance (mesh
    addressing, mailbox skills, ECO routing) into the rendered system
    prompt. Base empirica users without cortex shouldn't see guidance
    for primitives that will return 'cortex config missing' errors.
    """
    try:
        from empirica.config.credentials_loader import get_credentials_loader

        cfg = get_credentials_loader().get_cortex_config()
        return bool(cfg.get("url") and cfg.get("api_key"))
    except Exception:
        return False


def _strip_cortex_blocks(text: str) -> str:
    """Remove `{% if cortex %}…{% endif %}` chunks; strip the tags around
    `{% if cortex %}…{% endif %}` retentions are handled by the cortex-on
    path (we just drop the tags).

    Cortex-on path: tags stripped, content kept.
    Cortex-off path: tags + content dropped.
    """
    import re

    # DOTALL so .*? spans newlines inside the block.
    return re.sub(
        r"\{%\s*if cortex\s*%\}.*?\{%\s*endif\s*%\}\s*",
        "",
        text,
        flags=re.DOTALL,
    )


def _strip_cortex_tags(text: str) -> str:
    """Cortex-on: drop just the tags, keep the content."""
    import re

    text = re.sub(r"\{%\s*if cortex\s*%\}\s*", "", text)
    text = re.sub(r"\s*\{%\s*endif\s*%\}", "", text)
    return text


def _render_versioned_template(
    src: Path,
    dst: Path,
    cortex_enabled: bool | None = None,
) -> None:
    """Write `src` to `dst` with template placeholders substituted.

    Placeholders:
      {{ empirica_version }}  → installed empirica version (e.g. "1.9.8")
      {{ generated_date }}    → today's UTC date (e.g. "2026-05-05")

    Conditional blocks (cortex-specific guidance):
      {% if cortex %}…{% endif %} — kept when cortex configured, dropped
      otherwise. Detection via `~/.empirica/credentials.yaml` `cortex:`
      block (url + api_key both present). Override with the
      `cortex_enabled` arg for tests / explicit force.

    Closes Philipp's #100 — without this, the template's hardcoded version
    string drifts every release.
    """
    text = src.read_text(encoding="utf-8")
    version = _resolve_empirica_version()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    text = text.replace("{{ empirica_version }}", version)
    text = text.replace("{{ generated_date }}", today)

    if cortex_enabled is None:
        cortex_enabled = _is_cortex_configured()

    if cortex_enabled:
        text = _strip_cortex_tags(text)
    else:
        text = _strip_cortex_blocks(text)

    dst.write_text(text, encoding="utf-8")


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
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                py_ver = result.stdout.strip()
                major, minor = map(int, py_ver.split("."))
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
    temp_path = path.with_suffix(".tmp")
    with open(temp_path, "w") as f:
        json.dump(data, f, indent=2)
    temp_path.rename(path)


def _hook_exists(hooks_list: list, pattern: str) -> bool:
    """Check if a hook with the given pattern already exists"""
    for hook_entry in hooks_list:
        for hook in hook_entry.get("hooks", []):
            cmd = hook.get("command", "")
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
    if event not in settings["hooks"]:
        settings["hooks"][event] = []

    if not _hook_exists(settings["hooks"][event], detect_pattern):
        if use_extend:
            settings["hooks"][event].extend(entries)
        else:
            for entry in entries:
                settings["hooks"][event].append(entry)
        if output_format != "json":
            print(f"   ✓ {label} configured")
    else:
        if output_format != "json":
            print(f"   {label} already configured")


def _force_clean_hooks(settings, output_format):
    """Remove only Empirica hooks from settings, preserving other plugins' hooks.

    Previously this did settings['hooks'] = {} which nuked ALL hooks
    including Railway, Superpowers, and custom hooks. Now filters by
    plugin path to only remove Empirica's entries.
    """
    plugin_path_patterns = [
        f"plugins/local/{PLUGIN_NAME}/",  # Current name
        "plugins/local/empirica-integration/",  # Legacy name
        "plugins/local/empirica/",  # Short name
    ]
    for event in list(settings.get("hooks", {}).keys()):
        original_count = len(settings["hooks"][event])
        settings["hooks"][event] = [
            hook
            for hook in settings["hooks"][event]
            if not any(pattern in str(hook) for pattern in plugin_path_patterns)
        ]
        removed = original_count - len(settings["hooks"][event])
        if removed > 0:
            logger.debug(f"--force: removed {removed} Empirica hooks from {event}")
        # Clean up empty event lists
        if not settings["hooks"][event]:
            del settings["hooks"][event]

    settings.pop("statusLine", None)
    if output_format != "json":
        print("   --force: cleared Empirica hooks and statusLine (other plugins preserved)")

    # Also clean up legacy plugin name from enabledPlugins
    legacy_key = "empirica-integration@local"
    if legacy_key in settings.get("enabledPlugins", {}):
        del settings["enabledPlugins"][legacy_key]
        if output_format != "json":
            print("   --force: removed legacy empirica-integration@local from enabledPlugins")


def _configure_statusline(settings, plugin_dir, python_cmd, output_format):
    """Configure StatusLine command in settings.

    Claude Code pipes session JSON to statusline stdin — do NOT redirect stdin.
    """
    # Forward slashes: Claude Code runs `type: command` via Git Bash, which eats
    # Windows backslashes and collapses the path (issue #111). Forward slashes are
    # valid on Windows too, so normalise both the interpreter + script paths.
    python_cmd = python_cmd.replace("\\", "/")
    if "statusLine" not in settings:
        statusline_script = (plugin_dir / "scripts" / "statusline_empirica.py").as_posix()
        settings["statusLine"] = {"type": "command", "command": f"{python_cmd} {statusline_script}"}
        if output_format != "json":
            print("   ✓ StatusLine configured")
    else:
        if output_format != "json":
            print("   StatusLine already configured")


def _register_all_hooks(settings, plugin_dir, python_cmd, output_format):
    """Register all Empirica hooks into settings['hooks']."""
    # Forward slashes: Claude Code runs `type: command` hooks via Git Bash, which
    # eats Windows backslashes -> "command not found" on every event (issue #111).
    # Normalise the interpreter path + use POSIX plugin path (valid on Windows too).
    python_cmd = python_cmd.replace("\\", "/")
    plugin_dir = plugin_dir.as_posix()
    if "hooks" not in settings:
        settings["hooks"] = {}

    sentinel_script = f"{python_cmd} {plugin_dir}/hooks/sentinel-gate.py"
    _register_hook(
        settings,
        "PreToolUse",
        "sentinel-gate",
        [
            {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": sentinel_script, "timeout": 10}]},
            {"matcher": "Bash", "hooks": [{"type": "command", "command": sentinel_script, "timeout": 10}]},
        ],
        "PreToolUse (Sentinel) hooks",
        output_format,
        use_extend=True,
    )

    precompact_script = f"{python_cmd} {plugin_dir}/hooks/pre-compact.py"
    _register_hook(
        settings,
        "PreCompact",
        "pre-compact.py",
        [
            {"matcher": "auto|manual", "hooks": [{"type": "command", "command": precompact_script, "timeout": 30}]},
        ],
        "PreCompact hook",
        output_format,
    )

    postcompact_script = f"{python_cmd} {plugin_dir}/hooks/post-compact.py"
    sessioninit_script = f"{python_cmd} {plugin_dir}/hooks/session-init.py"
    ewm_script = f"{python_cmd} {plugin_dir}/hooks/ewm-protocol-loader.py"
    monitor_arm_script = f"{python_cmd} {plugin_dir}/hooks/session-monitor-arm.py"
    _register_hook(
        settings,
        "SessionStart",
        "post-compact.py",
        [
            {
                "matcher": "compact",
                "hooks": [
                    {"type": "command", "command": postcompact_script, "timeout": 30},
                    {"type": "command", "command": ewm_script, "timeout": 10, "allowFailure": True},
                    {
                        "type": "command",
                        "command": monitor_arm_script,
                        "timeout": LIGHT_HOOK_TIMEOUT,
                        "allowFailure": True,
                    },
                ],
            },
            {
                "matcher": "startup|resume",
                "hooks": [
                    {"type": "command", "command": sessioninit_script, "timeout": 30},
                    {"type": "command", "command": ewm_script, "timeout": 10, "allowFailure": True},
                    {
                        "type": "command",
                        "command": monitor_arm_script,
                        "timeout": LIGHT_HOOK_TIMEOUT,
                        "allowFailure": True,
                    },
                ],
            },
        ],
        "SessionStart hooks",
        output_format,
        use_extend=True,
    )

    postflight_script = f"{python_cmd} {plugin_dir}/hooks/session-end-postflight.py"
    curate_script = f"{python_cmd} {plugin_dir}/hooks/curate-snapshots.py --output json"
    _register_hook(
        settings,
        "SessionEnd",
        "session-end-postflight.py",
        [
            {
                "matcher": ".*",
                "hooks": [
                    {"type": "command", "command": postflight_script, "timeout": 20},
                    {"type": "command", "command": curate_script, "timeout": 15, "allowFailure": True},
                ],
            },
        ],
        "SessionEnd hooks",
        output_format,
    )

    substart_script = f"{python_cmd} {plugin_dir}/hooks/subagent-start.py"
    _register_hook(
        settings,
        "SubagentStart",
        "subagent-start.py",
        [
            {
                "matcher": ".*",
                "hooks": [{"type": "command", "command": substart_script, "timeout": 10, "allowFailure": True}],
            },
        ],
        "SubagentStart hook",
        output_format,
    )

    substop_script = f"{python_cmd} {plugin_dir}/hooks/subagent-stop.py"
    _register_hook(
        settings,
        "SubagentStop",
        "subagent-stop.py",
        [
            {
                "matcher": ".*",
                "hooks": [{"type": "command", "command": substop_script, "timeout": 15, "allowFailure": True}],
            },
        ],
        "SubagentStop hook",
        output_format,
    )

    router_script = f"{python_cmd} {plugin_dir}/hooks/tool-router.py"
    _register_hook(
        settings,
        "UserPromptSubmit",
        "tool-router.py",
        [
            {
                "matcher": ".*",
                "hooks": [
                    {"type": "command", "command": router_script, "timeout": LIGHT_HOOK_TIMEOUT, "allowFailure": True}
                ],
            },
        ],
        "UserPromptSubmit hook",
        output_format,
    )

    # Context-shift tracker (classifies solicited vs unsolicited prompts)
    cs_script = f"{python_cmd} {plugin_dir}/hooks/context-shift-tracker.py"
    if not _hook_exists(settings["hooks"].get("UserPromptSubmit", []), "context-shift-tracker.py"):
        settings["hooks"].setdefault("UserPromptSubmit", []).append(
            {
                "matcher": ".*",
                "hooks": [
                    {"type": "command", "command": cs_script, "timeout": LIGHT_HOOK_TIMEOUT, "allowFailure": True}
                ],
            }
        )
        if output_format != "json":
            print("   ✓ Context-shift tracker configured")

    # Loop install pickup — surfaces pending install requests from cockpit as
    # additionalContext on the next prompt so the running Claude can call
    # CronCreate via /loop.
    install_script = f"{python_cmd} {plugin_dir}/hooks/loop-install-pickup.py"
    if not _hook_exists(settings["hooks"].get("UserPromptSubmit", []), "loop-install-pickup.py"):
        settings["hooks"].setdefault("UserPromptSubmit", []).append(
            {
                "matcher": ".*",
                "hooks": [
                    {"type": "command", "command": install_script, "timeout": LIGHT_HOOK_TIMEOUT, "allowFailure": True}
                ],
            }
        )
        if output_format != "json":
            print("   ✓ Loop install pickup configured")

    # Loop uninstall pickup — symmetric inverse. When `empirica loop pause`
    # writes a pending uninstall request, this hook surfaces it so the
    # owning Claude can call CronDelete from inside that CC session. The
    # body pause-check at next fire is the backstop if Claude doesn't run
    # CronDelete in time.
    uninstall_script = f"{python_cmd} {plugin_dir}/hooks/loop-uninstall-pickup.py"
    if not _hook_exists(settings["hooks"].get("UserPromptSubmit", []), "loop-uninstall-pickup.py"):
        settings["hooks"].setdefault("UserPromptSubmit", []).append(
            {
                "matcher": ".*",
                "hooks": [
                    {
                        "type": "command",
                        "command": uninstall_script,
                        "timeout": LIGHT_HOOK_TIMEOUT,
                        "allowFailure": True,
                    }
                ],
            }
        )
        if output_format != "json":
            print("   ✓ Loop uninstall pickup configured")

    # Listener install pickup — surfaces pending listener install requests
    # from cockpit so the running Claude can arm the listener (curl + Monitor)
    # via the /inbox-listener skill.
    listener_install_script = f"{python_cmd} {plugin_dir}/hooks/listener-install-pickup.py"
    if not _hook_exists(settings["hooks"].get("UserPromptSubmit", []), "listener-install-pickup.py"):
        settings["hooks"].setdefault("UserPromptSubmit", []).append(
            {
                "matcher": ".*",
                "hooks": [
                    {
                        "type": "command",
                        "command": listener_install_script,
                        "timeout": LIGHT_HOOK_TIMEOUT,
                        "allowFailure": True,
                    }
                ],
            }
        )
        if output_format != "json":
            print("   ✓ Listener install pickup configured")

    # Listener uninstall pickup — symmetric inverse. When `empirica listener
    # pause` writes a pending uninstall request (Monitor task id + curl pid),
    # this hook surfaces it so the owning Claude can TaskStop the Monitor and
    # kill the held curl. Body pause-check at next wake is the backstop.
    listener_uninstall_script = f"{python_cmd} {plugin_dir}/hooks/listener-uninstall-pickup.py"
    if not _hook_exists(settings["hooks"].get("UserPromptSubmit", []), "listener-uninstall-pickup.py"):
        settings["hooks"].setdefault("UserPromptSubmit", []).append(
            {
                "matcher": ".*",
                "hooks": [
                    {
                        "type": "command",
                        "command": listener_uninstall_script,
                        "timeout": LIGHT_HOOK_TIMEOUT,
                        "allowFailure": True,
                    }
                ],
            }
        )
        if output_format != "json":
            print("   ✓ Listener uninstall pickup configured")

    entity_script = f"{python_cmd} {plugin_dir}/hooks/entity-extractor.py"
    _register_hook(
        settings,
        "PostToolUse",
        "entity-extractor.py",
        [
            {
                "matcher": "Edit|Write",
                "hooks": [
                    {"type": "command", "command": entity_script, "timeout": LIGHT_HOOK_TIMEOUT, "allowFailure": True}
                ],
            },
        ],
        "PostToolUse (entity extraction) hook",
        output_format,
    )

    task_script = f"{python_cmd} {plugin_dir}/hooks/task-completed.py"
    _register_hook(
        settings,
        "TaskCompleted",
        "task-completed.py",
        [
            {
                "matcher": ".*",
                "hooks": [{"type": "command", "command": task_script, "timeout": 10, "allowFailure": True}],
            },
        ],
        "TaskCompleted hook",
        output_format,
    )

    failure_script = f"{python_cmd} {plugin_dir}/hooks/tool-failure.py"
    _register_hook(
        settings,
        "PostToolUseFailure",
        "tool-failure.py",
        [
            {
                "matcher": ".*",
                "hooks": [
                    {"type": "command", "command": failure_script, "timeout": LIGHT_HOOK_TIMEOUT, "allowFailure": True}
                ],
            },
        ],
        "PostToolUseFailure hook",
        output_format,
    )

    stop_script = f"{python_cmd} {plugin_dir}/hooks/transaction-enforcer.py"
    _register_hook(
        settings,
        "Stop",
        "transaction-enforcer.py",
        [
            {
                "matcher": ".*",
                "hooks": [
                    {"type": "command", "command": stop_script, "timeout": LIGHT_HOOK_TIMEOUT, "allowFailure": True}
                ],
            },
        ],
        "Stop (transaction enforcer) hook",
        output_format,
    )


def _configure_settings(settings, settings_file, plugin_dir, python_cmd, force, output_format, plugin_key):
    """Configure settings.json: enable plugin, register hooks, set statusline.

    Extracted from handle_setup_claude_code_command to reduce handler complexity.
    """
    if output_format != "json":
        print("\n⚙️  Configuring settings.json...")

    settings = _ensure_json_file(settings_file, {})

    # Ensure enabledPlugins exists and enable the plugin
    if "enabledPlugins" not in settings:
        settings["enabledPlugins"] = {}
    plugin_key = f"{PLUGIN_NAME}@local"
    settings["enabledPlugins"][plugin_key] = True
    if output_format != "json":
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
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000"),
        }
        _write_json_file(active_work_file, active_work)
        if output_format != "json":
            print("   ✓ Created ~/.empirica/active_work.json")

    return home, claude_dir, plugins_dir, plugin_dir, marketplace_dir, empirica_dir


def _install_plugin_files(source_dir, plugin_dir, output_format):
    """Install plugin files: migrate old dirs, copy source, set permissions."""
    if output_format != "json":
        print("\n📦 Installing plugin files...")

    # Migration: remove old empirica-integration directory if it exists (renamed to empirica in 1.7.0)
    old_plugin_dir = plugin_dir.parent / "empirica-integration"
    if old_plugin_dir.exists() and old_plugin_dir != plugin_dir:
        shutil.rmtree(old_plugin_dir)
        if output_format != "json":
            print("   🔄 Migrated: removed old empirica-integration plugin directory")

    # Also clean orphaned cache (prevents duplicate hook execution)
    old_cache_dir = Path.home() / ".claude" / "plugins" / "cache" / "local" / "empirica-integration"
    if old_cache_dir.exists():
        shutil.rmtree(old_cache_dir)

    # Always sync plugin files — hooks and scripts must track the installed version.
    # Previous behavior skipped this if directory existed, causing stale scripts.
    if plugin_dir.exists():
        shutil.rmtree(plugin_dir)

    # Copy excluding __pycache__ and .git
    def ignore_patterns(directory, files):
        return [f for f in files if f in ("__pycache__", ".git", ".pyc")]

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

    if output_format != "json":
        print(f"   ✓ Plugin installed to {plugin_dir}")


def _installed_plugin_dir() -> Path:
    """The installed plugin location Claude Code loads hooks from."""
    return Path.home() / ".claude" / "plugins" / "local" / PLUGIN_NAME


def _read_plugin_version_stamp(plugin_dir: Path) -> str | None:
    try:
        return (plugin_dir / PLUGIN_VERSION_STAMP).read_text().strip() or None
    except OSError:
        return None


def _write_plugin_version_stamp(plugin_dir: Path, version: str | None = None) -> None:
    """Stamp the installed plugin with the empirica version its files came from."""
    try:
        (plugin_dir / PLUGIN_VERSION_STAMP).write_text((version or _resolve_empirica_version()).strip() + "\n")
    except OSError:
        pass


def _sync_plugin_files_in_place(source_dir: Path, plugin_dir: Path) -> None:
    """Overwrite installed plugin files from source IN PLACE (no rmtree), so it
    is safe to run from a hook executing inside plugin_dir. Refreshes + adds
    files; does NOT prune files removed from source (the full clean rebuild is
    `setup-claude-code --force`, which rmtrees first)."""

    def _ignore(_directory, files):
        return [f for f in files if f in ("__pycache__", ".git", ".pyc")]

    shutil.copytree(source_dir, plugin_dir, ignore=_ignore, dirs_exist_ok=True)
    hooks_dir = plugin_dir / "hooks"
    if hooks_dir.exists():
        for h in list(hooks_dir.glob("*.py")) + list(hooks_dir.glob("*.sh")):
            try:
                h.chmod(0o755)
            except OSError:
                pass


def handle_plugin_sync_command(args):
    """Re-sync the installed CC plugin from the bundled package source when the
    installed copy has drifted behind the running empirica version.

    Closes the deploy-staleness gap: hook fixes land in the package on upgrade
    but the installed ~/.claude copy only refreshes on a manual
    setup-claude-code. session-init shells out to this at SessionStart so a pip
    upgrade's hook fixes reach running CC sessions automatically (this is what
    prevents the recovery-verb 'Rushed assessment' deadlock from a stale gate).

    Default: sync only when the stamped version != running version. --force
    always syncs. Lightweight (in-place overwrite, no interactive setup).
    """
    output = getattr(args, "output", "human")
    force = getattr(args, "force", False)
    quiet = getattr(args, "quiet", False)
    version = _resolve_empirica_version()
    plugin_dir = _installed_plugin_dir()
    installed = _read_plugin_version_stamp(plugin_dir)

    def _emit(result, rc=0):
        if output == "json":
            print(json.dumps(result))
        elif not quiet:
            if result.get("synced"):
                print(f"🔄 empirica plugin synced {result.get('from') or '(unstamped)'} → {result['to']}")
            elif result.get("ok"):
                print(f"✓ empirica plugin current ({version})")
        return rc

    # No install present — nothing to sync (not an error: e.g. non-CC env).
    if not plugin_dir.exists():
        return _emit({"ok": True, "synced": False, "reason": "not_installed", "version": version})

    if installed == version and version != "unknown" and not force:
        return _emit({"ok": True, "synced": False, "reason": "current", "version": version})

    source_dir = _get_plugin_source_dir()
    if source_dir is None:
        if output == "json":
            print(json.dumps({"ok": False, "synced": False, "reason": "source_not_found", "version": version}))
        elif not quiet:
            print("⚠ empirica plugin source not found — run `empirica setup-claude-code --force`", file=sys.stderr)
        return 1

    _sync_plugin_files_in_place(source_dir, plugin_dir)
    _write_plugin_version_stamp(plugin_dir, version)
    return _emit({"ok": True, "synced": True, "from": installed, "to": version})


def _install_claude_md(plugin_dir, claude_dir, output_format):
    """Install Empirica system prompt and CLAUDE.md include reference.

    The Empirica prompt is the lean core (`empirica-system-prompt.md`,
    skills-on-demand) written to its own file and @included from the user's
    CLAUDE.md — so CLAUDE.md stays a thin shell carrying only the user's own
    nuance, while the ecosystem body lives in the @included prompt. There is
    no monolithic full-prompt variant: a self-contained system prompt for a
    non-Claude harness is out of scope here (community territory).
    """
    if output_format != "json":
        print("\n📝 Installing Empirica system prompt...")

    claude_md_src = plugin_dir / "templates" / "empirica-system-prompt-lean.md"
    prompt_label = "lean core (skills on demand)"

    claude_md_dst = claude_dir / "CLAUDE.md"
    empirica_prompt_dst = claude_dir / "empirica-system-prompt.md"
    include_line = "@~/.claude/empirica-system-prompt.md"

    if claude_md_src.exists():
        # Always write Empirica prompt to separate file (safe to overwrite).
        # Render the {{ empirica_version }} + {{ generated_date }} placeholders
        # at write-time so the prompt always reflects the installed package
        # version (closes Philipp's #100 — hardcoded version drifts every release).
        _render_versioned_template(claude_md_src, empirica_prompt_dst)
        if output_format != "json":
            print(f"   ✓ Empirica prompt ({prompt_label}) written to ~/.claude/empirica-system-prompt.md")

        if claude_md_dst.exists():
            existing_content = claude_md_dst.read_text()
            if include_line not in existing_content:
                new_content = f"{include_line}\n\n{existing_content}"
                claude_md_dst.write_text(new_content)
                if output_format != "json":
                    print("   ✓ Added include reference to existing ~/.claude/CLAUDE.md")
            else:
                if output_format != "json":
                    print("   ✓ Include reference already present in ~/.claude/CLAUDE.md")
        else:
            claude_md_dst.write_text(f"{include_line}\n")
            if output_format != "json":
                print("   ✓ Created ~/.claude/CLAUDE.md with Empirica include")
    else:
        if output_format != "json":
            print("   ⚠️  CLAUDE.md template not found in plugin")


def _register_marketplace(marketplace_dir, plugins_dir, claude_dir, plugin_dir, plugin_key, output_format):
    """Register plugin in marketplace, installed_plugins, and known_marketplaces."""
    if output_format != "json":
        print("\n📋 Registering in marketplace...")

    marketplace_file = marketplace_dir / "marketplace.json"
    marketplace = _ensure_json_file(
        marketplace_file,
        {
            "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
            "name": "local",
            "description": "Local development plugins",
            "owner": {"name": "Local", "email": "dev@localhost"},
            "plugins": [],
        },
    )

    plugin_names = [p.get("name") for p in marketplace.get("plugins", [])]
    if PLUGIN_NAME not in plugin_names:
        marketplace.setdefault("plugins", []).append(
            {
                "name": PLUGIN_NAME,
                "description": "Noetic firewall + CASCADE workflow automation for Claude Code",
                "version": PLUGIN_VERSION,
                "author": {"name": "Empirica Project", "url": "https://github.com/EmpiricaAI/empirica"},
                "source": f"./{PLUGIN_NAME}",
                "category": "productivity",
            }
        )
        _write_json_file(marketplace_file, marketplace)
        if output_format != "json":
            print("   ✓ Added to marketplace.json")

    # Installed plugins registration
    installed_plugins_file = claude_dir / "plugins" / "installed_plugins.json"
    installed_plugins = _ensure_json_file(installed_plugins_file, {"version": 2, "plugins": {}})

    install_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    installed_plugins["plugins"][plugin_key] = [
        {
            "scope": "user",
            "installPath": str(plugin_dir),
            "version": PLUGIN_VERSION,
            "installedAt": install_date,
            "lastUpdated": install_date,
            "isLocal": True,
        }
    ]
    _write_json_file(installed_plugins_file, installed_plugins)
    if output_format != "json":
        print("   ✓ Added to installed_plugins.json")

    # Known marketplaces
    known_marketplaces_file = claude_dir / "plugins" / "known_marketplaces.json"
    known_marketplaces = _ensure_json_file(known_marketplaces_file, {})

    if "local" not in known_marketplaces:
        known_marketplaces["local"] = {
            "source": {"source": "directory", "path": str(plugins_dir)},
            "installLocation": str(plugins_dir),
            "lastUpdated": install_date,
        }
        _write_json_file(known_marketplaces_file, known_marketplaces)
        if output_format != "json":
            print("   ✓ Local marketplace registered")


def _configure_mcp_server(claude_dir, home, force, output_format):
    """Find and configure the empirica-mcp MCP server. Returns (mcp_installed, mcp_cmd)."""
    if output_format != "json":
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

    existing = mcp_config.get("mcpServers", {}).get("empirica")
    needs_update = (
        not existing or force or existing.get("command") != mcp_cmd  # Binary path changed
    )
    if needs_update:
        mcp_config.setdefault("mcpServers", {})["empirica"] = {
            "command": mcp_cmd,
            "args": [],
            "type": "stdio",
            "tools": ["*"],
            "description": "Empirica epistemic framework - CASCADE workflow, goals, findings",
        }
        _write_json_file(mcp_file, mcp_config)
        if output_format != "json":
            if existing and existing.get("command") != mcp_cmd:
                print(f"   ✓ MCP server updated: {mcp_cmd}")
                print(f"     (was: {existing.get('command', 'unknown')})")
            else:
                print(f"   ✓ MCP server configured: {mcp_cmd}")
    else:
        if output_format != "json":
            print(f"   MCP server already configured: {mcp_cmd}")

    return True, mcp_cmd


def _try_install_mcp_via_pipx(home, output_format):
    """Attempt to install empirica-mcp via pipx. Returns mcp_cmd or None."""
    if shutil.which("pipx"):
        if output_format != "json":
            print("   Installing empirica-mcp via pipx...")
        try:
            result = subprocess.run(["pipx", "install", "empirica-mcp"], capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                mcp_cmd = shutil.which("empirica-mcp")
                if not mcp_cmd:
                    mcp_cmd = str(home / ".local" / "bin" / "empirica-mcp")
                if output_format != "json":
                    print("   ✓ empirica-mcp installed via pipx")
                return mcp_cmd
            else:
                if output_format != "json":
                    print(f"   ⚠️  pipx install failed: {result.stderr[:100]}")
        except Exception as e:
            if output_format != "json":
                print(f"   ⚠️  pipx install failed: {e}")
    else:
        if output_format != "json":
            print("   ⚠️  pipx not available - install empirica-mcp manually:")
            print("      pipx install empirica-mcp")
    return None


def _check_semantic_layer():
    """Check Ollama and Qdrant status. Returns (ollama_ok, embedding_ok, qdrant_ok)."""
    ollama_ok = False
    embedding_ok = False
    try:
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
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
    print(f"📍 Plugin:           {plugin_dir}")
    print("📝 Empirica prompt:  ~/.claude/empirica-system-prompt.md (refreshed)")
    print("📝 CLAUDE.md:        ~/.claude/CLAUDE.md (preserved; include line added if missing)")
    print("⚙️  Settings:         ~/.claude/settings.json")
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


def _print_credentials_summary(state: dict) -> None:
    """One-block credentials section for the human summary.

    Shows status per credential type + actionable next-step lines when
    something is missing. Quiet when both are green.
    """
    print()
    print("━" * 60)
    print("🔑 CREDENTIALS")
    print("━" * 60)
    cortex_glyph = "✓" if state["cortex_ok"] else "⚠"
    ntfy_glyph = "✓" if state["ntfy_ok"] else "⚠"
    print(f"   {cortex_glyph} cortex  ({state['cortex_url'] or 'not set'})")
    print(f"   {ntfy_glyph} ntfy    ({state['ntfy_url'] or 'not set'})")
    if state["issues"]:
        print()
        print("   To fix:")
        for issue in state["issues"]:
            print(f"     • {issue}")
        print()
        print("   Edit ~/.empirica/credentials.yaml directly, OR re-run")
        print("   setup-claude-code in an interactive terminal for the wizard.")
        print("   Skip this prompt next time: --skip-credentials")


def _resolve_tenant_overrides(args) -> dict:
    """Pull --org-id / --tenant-slug / --mesh-id-prefix from args.

    Missing values stay None so the REST fetch can fill them in.
    """
    return {
        "org_id": getattr(args, "org_id", None),
        "tenant_slug": getattr(args, "tenant_slug", None),
        "mesh_id_prefix": getattr(args, "mesh_id_prefix", None),
    }


def _fetch_tenant_metadata(
    cortex_url: str,
    api_key: str,
    timeout: float = 10.0,
) -> dict | None:
    """GET `{cortex_url}/v1/users/me` and pull {org_id, tenant_slug, mesh_id_prefix}.

    Returns the three fields on 2xx, None on HTTP error / network failure /
    malformed JSON. Mirrors the Bearer-auth + urllib pattern used by
    projects_commands._post_project — kept inline to avoid an import cycle
    with the bulk-register handler.
    """
    url = f"{cortex_url.rstrip('/')}/v1/users/me"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            body = json.loads(raw) if raw else {}
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError):
        return None
    return {
        "org_id": body.get("org_id"),
        "tenant_slug": body.get("tenant_slug"),
        "mesh_id_prefix": body.get("mesh_id_prefix"),
    }


def _persist_tenant_metadata(
    project_root: Path,
    *,
    org_id: str | None,
    tenant_slug: str | None,
    mesh_id_prefix: str | None,
) -> bool:
    """Merge tenant fields into `<project_root>/.empirica/project.yaml`.

    Returns True iff any field was newly written. Returns False if:
      - no fields supplied, or
      - no project.yaml under project_root (caller should warn separately), or
      - all supplied fields already match what's on disk.

    Existing top-level keys are preserved (atomic merge — mirrors
    save_cortex_config's safety contract).
    """
    if all(v is None for v in (org_id, tenant_slug, mesh_id_prefix)):
        return False
    project_yaml = project_root / ".empirica" / "project.yaml"
    if not project_yaml.exists():
        return False
    try:
        import yaml
    except ImportError:
        return False
    try:
        existing = yaml.safe_load(project_yaml.read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    if not isinstance(existing, dict):
        return False
    changed = False
    for key, value in (
        ("org_id", org_id),
        ("tenant_slug", tenant_slug),
        ("mesh_id_prefix", mesh_id_prefix),
    ):
        if value is not None and existing.get(key) != value:
            existing[key] = value
            changed = True
    # Derive the strict canonical 3-form seat (`org.tenant.project`) from the
    # effective mesh_id_prefix + the project's ai_id, and persist it so the
    # daemon/model can pass `seat` to cortex_session_init without re-composing.
    # Self-heals: recomputes even when the three fields above were unchanged
    # (e.g. a project.yaml that has mesh_id_prefix + ai_id but no seat yet).
    from empirica.config.project_config_loader import compose_canonical_seat

    seat = compose_canonical_seat(
        mesh_id_prefix=existing.get("mesh_id_prefix") or "",
        ai_id=existing.get("ai_id") or "",
    )
    if seat is not None and existing.get("canonical_seat") != seat:
        existing["canonical_seat"] = seat
        changed = True
    if not changed:
        return False
    project_yaml.write_text(
        yaml.safe_dump(existing, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return True


def _resolve_and_persist_tenant_metadata(
    args,
    output_format: str,
    project_root: Path | None = None,
) -> dict | None:
    """Resolve {org_id, tenant_slug, mesh_id_prefix} and merge into project.yaml.

    Precedence per field: CLI flag > REST `/v1/users/me` > unset.
    REST is skipped entirely when all three flags are supplied.
    When REST is needed but cortex creds are missing, we no-op silently.

    Returns the resolved metadata dict (with None entries for unresolved
    fields), or None when nothing could be resolved at all.
    """
    if project_root is None:
        project_root = Path.cwd()

    overrides = _resolve_tenant_overrides(args)
    metadata: dict[str, str | None]
    if all(overrides.values()):
        metadata = overrides
    else:
        try:
            from empirica.config.credentials_loader import get_credentials_loader

            loader = get_credentials_loader()
            loader.reload()
            cortex_cfg = loader.get_cortex_config()
        except Exception:
            cortex_cfg = {}
        cortex_url = cortex_cfg.get("url")
        api_key = cortex_cfg.get("api_key")
        if not (cortex_url and api_key):
            if any(overrides.values()) and output_format != "json":
                # Flags partially supplied but no api_key to fill the rest.
                metadata = overrides
            else:
                return None
        else:
            rest = _fetch_tenant_metadata(cortex_url, api_key)
            if rest is None:
                if output_format != "json":
                    print(
                        "   ⚠️  Couldn't fetch tenant metadata from cortex "
                        "(use --org-id / --tenant-slug / --mesh-id-prefix to set manually)"
                    )
                if any(overrides.values()):
                    metadata = overrides
                else:
                    return None
            else:
                metadata = {key: overrides[key] or rest.get(key) for key in ("org_id", "tenant_slug", "mesh_id_prefix")}

    wrote = _persist_tenant_metadata(project_root, **metadata)
    if output_format != "json":
        if wrote:
            print(
                f"   ✓ tenant metadata persisted to .empirica/project.yaml "
                f"(org_id={metadata['org_id']}, tenant_slug={metadata['tenant_slug']}, "
                f"mesh_id_prefix={metadata['mesh_id_prefix']})"
            )
        elif not (project_root / ".empirica" / "project.yaml").exists():
            print(
                f"   ℹ️  Tenant metadata resolved but no .empirica/project.yaml "
                f"in {project_root} — run 'empirica project-init' to persist"
            )
    return metadata


def _resolve_listener_ai_id(args) -> str | None:
    """Resolve the ai_id for listener service install — args > project.yaml > None."""
    if getattr(args, "ai_id", None):
        return args.ai_id
    pyaml = Path.cwd() / ".empirica" / "project.yaml"
    if not pyaml.exists():
        return None
    try:
        import yaml

        data = yaml.safe_load(pyaml.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            return data.get("ai_id")
    except Exception:
        return None
    return None


def _install_listener_service(args, output_format: str, skip: bool = False) -> dict | None:
    """Auto-install the persistent listener service for this project's ai_id.

    Returns a dict summary for the JSON output, or None when skipped /
    backend unavailable. Never raises — failures degrade silently with a
    warn in human mode (listener-service install is a nice-to-have, not
    a blocker for the rest of setup-claude-code).
    """
    if skip:
        if output_format != "json":
            print("   ⏭  listener service: skipped (--skip-listener-service)")
        return {"skipped": True}

    ai_id = _resolve_listener_ai_id(args)
    if not ai_id:
        if output_format != "json":
            print("   ℹ️  listener service: no ai_id (run `empirica project-init` first)")
        return None

    try:
        from empirica.core.loop_scheduler.persistent_listener import (
            ListenerServiceUnavailable,
            PersistentListenerService,
        )
    except ImportError as e:
        if output_format != "json":
            print(f"   ⚠️  listener service: import error: {e}")
        return None

    service = PersistentListenerService()
    if service.backend == "unavailable":
        if output_format != "json":
            print(
                f"   ⏭  listener service: backend unavailable on this host "
                f"({sys.platform}) — Linux/WSL2 needs systemd-user, "
                f"macOS needs launchctl"
            )
        return {"backend": "unavailable"}

    try:
        unit_path = service.install(ai_id)
    except ListenerServiceUnavailable as e:
        if output_format != "json":
            print(f"   ⚠️  listener service: {e}")
        return {"backend": service.backend, "error": str(e)}
    except subprocess.CalledProcessError as e:
        if output_format != "json":
            print(f"   ⚠️  listener service install failed: {e}")
        return {"backend": service.backend, "error": f"install failed: {e}"}

    status = service.status(ai_id)
    if output_format != "json":
        print(f"   ✓ listener service installed ({service.backend}): {unit_path} — active: {status.active}")
    return {
        "backend": service.backend,
        "ai_id": ai_id,
        "unit_path": str(unit_path),
        "log_path": status.log_path,
        "active": status.active,
    }


def _check_credentials_state() -> dict:
    """Return current credentials state for the setup summary.

    Reads via CredentialsLoader so env-var overrides count as 'set'.
    Returns:
      {
        "cortex_ok": bool,          # url + api_key both present
        "ntfy_ok": bool,            # url + topic + (token OR user+pw) present
        "cortex_url": str | None,
        "ntfy_url": str | None,
        "issues": list[str],        # human-readable missing-piece messages
      }
    """
    try:
        from empirica.config.credentials_loader import get_credentials_loader

        loader = get_credentials_loader()
        loader.reload()  # bypass cache
        cortex = loader.get_cortex_config()
        ntfy = loader.get_ntfy_config()
    except Exception as e:
        return {
            "cortex_ok": False,
            "ntfy_ok": False,
            "cortex_url": None,
            "ntfy_url": None,
            "issues": [f"Could not read credentials: {e}"],
        }

    issues: list[str] = []
    cortex_ok = bool(cortex.get("url") and cortex.get("api_key"))
    if not cortex_ok:
        missing = []
        if not cortex.get("url"):
            missing.append("url")
        if not cortex.get("api_key"):
            missing.append("api_key")
        issues.append(f"cortex: missing {', '.join(missing)}")

    ntfy_url_ok = bool(ntfy.get("url"))
    ntfy_auth_ok = bool(ntfy.get("token") or (ntfy.get("user") and ntfy.get("password")))
    # `topic` is no longer required in credentials.yaml — the listener
    # resolves the per-tenant canonical from cortex's channels endpoint
    # at startup. Per SER ser_dd1955ae07e04949a28bd5bc the wizard
    # never seeds it, so absence is the new normal.
    ntfy_ok = ntfy_url_ok and ntfy_auth_ok
    if not ntfy_ok:
        missing = []
        if not ntfy_url_ok:
            missing.append("url")
        if not ntfy_auth_ok:
            missing.append("token (or user+password)")
        issues.append(f"ntfy: missing {', '.join(missing)}")

    return {
        "cortex_ok": cortex_ok,
        "ntfy_ok": ntfy_ok,
        "cortex_url": cortex.get("url"),
        "ntfy_url": ntfy.get("url"),
        "issues": issues,
    }


def _run_credentials_wizard(state: dict, output_format: str) -> dict:
    """Interactive prompts to fill missing credentials.

    Returns updated state after the wizard runs. No-ops when:
      - output_format == 'json' (machine mode — don't block on stdin)
      - stdin isn't a TTY (piped / non-interactive shells)
      - both cortex and ntfy already OK

    User can hit Enter at any prompt to skip that field — partial fills
    are honored (the loader's env-var precedence means env-set values
    still win for fields the user skips).

    Writes via CredentialsLoader.save_cortex_config / save_ntfy_config —
    atomic merge, preserves other top-level keys.
    """
    import sys as _sys

    if state["cortex_ok"] and state["ntfy_ok"]:
        return state
    if output_format == "json":
        return state
    if not _sys.stdin.isatty():
        return state

    print()
    print("━" * 60)
    print("🔑 Credentials wizard — fill missing pieces")
    print("━" * 60)
    print("Press Enter at any prompt to skip that field.")
    print("Existing credentials.yaml values are preserved.")
    print()

    try:
        from empirica.config.credentials_loader import get_credentials_loader

        loader = get_credentials_loader()
    except Exception as e:
        print(f"⚠️  Could not load credentials helper: {e}")
        return state

    cortex_changed = False
    if not state["cortex_ok"]:
        print("→ Cortex (orchestration API)")
        default_url = state["cortex_url"] or "https://cortex.getempirica.com"
        url_in = input(f"   URL [{default_url}]: ").strip() or default_url
        key_in = input("   API key (starts with ctx_): ").strip()
        if url_in or key_in:
            try:
                loader.save_cortex_config(
                    url=url_in or None,
                    api_key=key_in or None,
                )
                cortex_changed = True
                print("   ✓ cortex block written")
            except Exception as e:
                print(f"   ⚠️  Failed to write cortex block: {e}")
        print()

    ntfy_changed = False
    if not state["ntfy_ok"]:
        print("→ ntfy (push wake bridge — listener subscribes here)")
        default_url = state["ntfy_url"] or "https://ntfy.getempirica.com"
        url_in = input(f"   URL [{default_url}]: ").strip() or default_url
        # NOTE: we intentionally don't prompt for `topic`. The listener
        # resolves the per-tenant canonical topic from cortex's
        # /v1/users/me/notification-channels endpoint at startup
        # (see notification_channels.resolve_orchestration_events_topic).
        # Seeding a default here used to write the retired bare
        # `orchestration-events` topic into every new credentials.yaml,
        # which has no ACL grant → 403 storm on every poll. Closing
        # SER ser_dd1955ae07e04949a28bd5bc empirica-side: per-tenant
        # canonical channels + per-AI tag, never per-practice topics
        # seeded by the client.
        print("   Auth: token (preferred) OR user+password")
        token_in = input("   Access token (starts with tk_, leave blank to use basic auth): ").strip()
        user_in = pw_in = ""
        if not token_in:
            user_in = input("   Username: ").strip()
            pw_in = input("   Password: ").strip()
        if url_in or token_in or (user_in and pw_in):
            try:
                loader.save_ntfy_config(
                    url=url_in or None,
                    token=token_in or None,
                    user=user_in or None,
                    password=pw_in or None,
                )
                ntfy_changed = True
                print("   ✓ ntfy block written (topic will be resolved from cortex at listener startup)")
            except Exception as e:
                print(f"   ⚠️  Failed to write ntfy block: {e}")
        print()

    if cortex_changed or ntfy_changed:
        # Re-check after writes
        return _check_credentials_state()
    return state


def _migrate_legacy_project_identity(force, output_format):
    """Stage 6.8 (--force only): migrate a legacy slug-shaped ``project_id`` in
    the CWD project to a single canonical UUID (the 1.12 model — see
    ``empirica.core.identity_migration``).

    The cortex-installed-or-not policy lives in ``run_force_migration``: cortex
    present → resolve + route unresolvable cases to ``project-register`` (never
    fork); cortex absent (public-facing) → safe local mint. Non-fatal and a
    clean no-op when not --force, not in a project, or already a UUID.
    """
    if not force:
        return None
    try:
        from empirica.core.identity_migration import run_force_migration

        result = run_force_migration(os.getcwd())
        if output_format != "json":
            status = result.get("status")
            if status == "migrated":
                print(
                    f"   --force: migrated project_id '{result['slug']}' → "
                    f"{result['project_id'][:8]}… (source: {result['source']})"
                )
            elif status == "unresolved":
                print(f"   --force: legacy project_id needs a UUID — {result['message']}")
        return result
    except Exception as e:
        if output_format != "json":
            print(f"   --force: project identity migration skipped ({type(e).__name__}: {e})")
        return {"status": "error", "message": str(e)}


def handle_setup_claude_code_command(args):
    """Handle setup-claude-code command"""
    try:
        output_format = getattr(args, "output", "human")
        force = getattr(args, "force", False)
        skip_mcp = getattr(args, "skip_mcp", False)
        skip_claude_md = getattr(args, "skip_claude_md", False)
        skip_credentials = getattr(args, "skip_credentials", False)

        # Find bundled plugins
        source_dir = _get_plugin_source_dir()
        if not source_dir:
            if output_format == "json":
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "error": "Could not find bundled plugin files",
                            "hint": "Run from a valid Empirica installation or dev environment",
                        },
                        indent=2,
                    )
                )
            else:
                print("❌ Error: Could not find bundled plugin files")
                print("   Run from a valid Empirica installation or dev environment")
            return None

        if output_format != "json":
            print("🧠 Setting up Claude Code integration...")
            print(f"   Source: {source_dir}\n")

        python_cmd = _find_python()
        if output_format != "json":
            print(f"   Using Python: {python_cmd}")

        # Stage 1: Create directories
        home, claude_dir, plugins_dir, plugin_dir, marketplace_dir, _empirica_dir = _setup_directories(output_format)

        # Stage 2: Install plugin files
        _install_plugin_files(source_dir, plugin_dir, output_format)
        _write_plugin_version_stamp(plugin_dir)  # drives drift-sync / session-init auto-heal

        # Stage 3: Install CLAUDE.md
        if not skip_claude_md:
            _install_claude_md(plugin_dir, claude_dir, output_format)

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

        # Stage 6.5: Credentials check + wizard. Fresh installs hit listener-
        # exit-code-2 the moment they toggle Events because no ntfy creds were
        # set (David, 2026-05-17). Run a state check; if interactive + missing,
        # prompt for the gap. Always reflect the result in the summary.
        creds_state = _check_credentials_state()
        if not skip_credentials:
            creds_state = _run_credentials_wizard(creds_state, output_format)

        # Stage 6.6: Tenant metadata resolution (cortex Phase 1 mesh — prop_jc5f4h5).
        # Fetch org_id/tenant_slug/mesh_id_prefix from /v1/users/me and merge
        # into .empirica/project.yaml so per-AI session bootstraps can compose
        # the three ai_id forms (short/tenant/mesh) without a cortex round-trip
        # every time. Flags --org-id / --tenant-slug / --mesh-id-prefix
        # override the REST fetch field-by-field.
        tenant_metadata = _resolve_and_persist_tenant_metadata(args, output_format)

        # Stage 6.7: Persistent listener service install (prop_flrtxxn32japbazq).
        # Auto-detected systemd-user (Linux) / launchd (macOS) install of
        # `empirica loop listen --instance <ai_id>` as a system-level service.
        # Without this, wake events queue in cortex+ntfy until a Claude
        # session opens — pull-when-session-starts. With it, the listener
        # stays alive and pushes wake events in real time.
        skip_listener_service = getattr(args, "skip_listener_service", False)
        listener_service_result = _install_listener_service(
            args,
            output_format,
            skip=skip_listener_service,
        )

        # Stage 6.8: Legacy project_id → UUID migration (--force only).
        # 1.12 kills slug-as-id; on the --force upgrade ritual, opportunistically
        # migrate the CWD project's identity to a single canonical UUID and
        # re-key its local history. Non-fatal.
        identity_migration_result = _migrate_legacy_project_identity(force, output_format)

        # Stage 7: Output
        if output_format == "json":
            return {
                "ok": True,
                "plugin_dir": str(plugin_dir),
                "claude_md": str(claude_dir / "CLAUDE.md") if not skip_claude_md else None,
                "settings_file": str(settings_file),
                "mcp_configured": mcp_installed,
                "mcp_command": mcp_cmd,
                "credentials": {
                    "cortex_ok": creds_state["cortex_ok"],
                    "ntfy_ok": creds_state["ntfy_ok"],
                    "issues": creds_state["issues"],
                },
                "tenant_metadata": tenant_metadata,
                "listener_service": listener_service_result,
                "identity_migration": identity_migration_result,
                "hooks_configured": [
                    "PreToolUse (Sentinel)",
                    "PreCompact",
                    "SessionStart",
                    "SessionEnd",
                    "SubagentStart",
                    "SubagentStop",
                    "UserPromptSubmit",
                ],
                "message": "Claude Code integration configured successfully",
            }
        else:
            _print_human_summary(plugin_dir, settings_file, mcp_installed, skip_claude_md, claude_dir)
            _print_credentials_summary(creds_state)

        return None

    except Exception as e:
        if getattr(args, "output", "human") == "json":
            print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        else:
            from ..cli_utils import handle_cli_error

            handle_cli_error(e, "Setup Claude Code", getattr(args, "verbose", False))
        return None
