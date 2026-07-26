"""
Empirica Diagnose Command - Integration Health Check

Walks through the Empirica + Claude Code integration step-by-step and
reports PASS / FAIL / WARN with an actionable hint per check. Designed
to answer the recurring "I installed it but the statusline isn't
showing" class of question without requiring back-and-forth diagnostic
ladders on GitHub issues.

Output modes:
  --output human    (default) — colored, emoji, fix hints
  --output json     — machine-readable JSON, suitable for `empirica
                     diagnose --output json | jq` or for issue reports

Exit codes:
  0  — all checks passed
  1  — one or more FAIL checks
  2  — one or more WARN checks (no FAIL)

Author: David S. L. van Assche, Claude
Date: 2026-04-08
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ANSI colors (mirrors statusline_empirica.py for consistency)
class _C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    GRAY = "\033[90m"
    CYAN = "\033[36m"


# Status constants
PASS = "PASS"  # noqa: S105
FAIL = "FAIL"
WARN = "WARN"
SKIP = "SKIP"


@dataclass
class CheckResult:
    """One diagnostic check outcome.

    Attributes:
        name: Short human-readable check name (e.g. "Plugin files installed")
        status: One of PASS / FAIL / WARN / SKIP
        detail: One-line factual observation (what was found)
        hint: Actionable suggestion if status != PASS (empty string if PASS)
        data: Optional structured data for the JSON output mode
    """

    name: str
    status: str
    detail: str = ""
    hint: str = ""
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_python_version() -> CheckResult:
    """Verify Python is recent enough for Empirica."""
    major, minor = sys.version_info[:2]
    version_str = f"{major}.{minor}.{sys.version_info.micro}"
    if (major, minor) < (3, 10):
        return CheckResult(
            name="Python version",
            status=FAIL,
            detail=f"Found {version_str}, need >= 3.10",
            hint="Install Python 3.10+ and reinstall empirica with that interpreter",
            data={"version": version_str},
        )
    return CheckResult(
        name="Python version",
        status=PASS,
        detail=f"{version_str}",
        data={"version": version_str},
    )


def check_empirica_cli_on_path() -> CheckResult:
    """Verify the empirica CLI command is on PATH."""
    cli = shutil.which("empirica")
    if not cli:
        return CheckResult(
            name="empirica CLI on PATH",
            status=FAIL,
            detail="`empirica` command not found in PATH",
            hint="Install with `pip install empirica` or `brew install empirica`",
        )
    return CheckResult(
        name="empirica CLI on PATH",
        status=PASS,
        detail=cli,
        data={"path": cli},
    )


def check_claude_dir() -> CheckResult:
    """Verify ~/.claude/ exists (Claude Code's config dir)."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        claude_dir = Path(config_dir).expanduser()
    else:
        claude_dir = Path.home() / ".claude"

    if not claude_dir.exists():
        return CheckResult(
            name="Claude Code config dir",
            status=FAIL,
            detail=f"{claude_dir} does not exist",
            hint=("Install Claude Code first (https://docs.anthropic.com/claude-code), then run `empirica setup`"),
            data={"path": str(claude_dir)},
        )
    return CheckResult(
        name="Claude Code config dir",
        status=PASS,
        detail=str(claude_dir) + (" (CLAUDE_CONFIG_DIR override)" if config_dir else ""),
        data={"path": str(claude_dir), "is_override": bool(config_dir)},
    )


def check_plugin_files(claude_dir: Path) -> CheckResult:
    """Verify the empirica plugin files are in ~/.claude/plugins/local/empirica/."""
    plugin_dir = claude_dir / "plugins" / "local" / "empirica"
    statusline_script = plugin_dir / "scripts" / "statusline_empirica.py"
    sentinel_script = plugin_dir / "hooks" / "sentinel-gate.py"
    plugin_manifest = plugin_dir / ".claude-plugin" / "plugin.json"

    if not plugin_dir.exists():
        return CheckResult(
            name="Plugin files installed",
            status=FAIL,
            detail=f"{plugin_dir} does not exist",
            hint="Run `empirica setup` to install the plugin files",
            data={"plugin_dir": str(plugin_dir)},
        )

    missing = []
    if not statusline_script.exists():
        missing.append("scripts/statusline_empirica.py")
    if not sentinel_script.exists():
        missing.append("hooks/sentinel-gate.py")
    if not plugin_manifest.exists():
        missing.append(".claude-plugin/plugin.json")

    if missing:
        return CheckResult(
            name="Plugin files installed",
            status=FAIL,
            detail=f"Plugin dir exists but missing: {', '.join(missing)}",
            hint="Run `empirica setup --force` to reinstall plugin files",
            data={"plugin_dir": str(plugin_dir), "missing": missing},
        )

    return CheckResult(
        name="Plugin files installed",
        status=PASS,
        detail=str(plugin_dir),
        data={"plugin_dir": str(plugin_dir)},
    )


def check_settings_json(claude_dir: Path) -> CheckResult:
    """Verify ~/.claude/settings.json exists and is valid JSON."""
    settings_file = claude_dir / "settings.json"
    if not settings_file.exists():
        return CheckResult(
            name="settings.json present",
            status=FAIL,
            detail=f"{settings_file} does not exist",
            hint="Run `empirica setup` to create it",
            data={"path": str(settings_file)},
        )
    try:
        with open(settings_file) as f:
            json.load(f)
    except json.JSONDecodeError as e:
        return CheckResult(
            name="settings.json present",
            status=FAIL,
            detail=f"{settings_file} exists but is not valid JSON: {e}",
            hint="Fix the JSON syntax or delete the file and run `empirica setup`",
            data={"path": str(settings_file), "error": str(e)},
        )
    return CheckResult(
        name="settings.json present",
        status=PASS,
        detail=str(settings_file),
        data={"path": str(settings_file)},
    )


def check_statusline_configured(claude_dir: Path) -> CheckResult:
    """Verify settings.json has a statusLine block pointing at empirica."""
    settings_file = claude_dir / "settings.json"
    if not settings_file.exists():
        return CheckResult(
            name="statusLine configured",
            status=SKIP,
            detail="settings.json missing (see previous check)",
        )
    try:
        with open(settings_file) as f:
            settings = json.load(f)
    except (json.JSONDecodeError, OSError):
        return CheckResult(
            name="statusLine configured",
            status=SKIP,
            detail="settings.json unreadable (see previous check)",
        )

    statusline = settings.get("statusLine")
    if not statusline:
        return CheckResult(
            name="statusLine configured",
            status=FAIL,
            detail="settings.json has no `statusLine` block",
            hint="Run `empirica setup` to configure the statusline",
            data={"has_statusLine": False},
        )

    cmd = statusline.get("command", "")
    if "statusline_empirica" not in cmd:
        return CheckResult(
            name="statusLine configured",
            status=WARN,
            detail=f"statusLine exists but not pointing at Empirica: {cmd}",
            hint=(
                "Another plugin owns the statusLine. Decide which to keep, then "
                "run `empirica setup --force` to override if needed."
            ),
            data={"has_statusLine": True, "command": cmd},
        )

    return CheckResult(
        name="statusLine configured",
        status=PASS,
        detail=cmd,
        data={"has_statusLine": True, "command": cmd},
    )


def check_hooks_registered(claude_dir: Path) -> CheckResult:
    """Verify the critical Empirica hooks are registered in settings.json.

    Claude Code hook architecture: post-compact.py is wired to SessionStart
    with matcher='compact', NOT a separate PostCompact event. session-init.py
    is wired to SessionStart with matcher='startup|resume'. Both share the
    SessionStart event with different matchers — we check for both scripts
    by name rather than by event.
    """
    settings_file = claude_dir / "settings.json"
    if not settings_file.exists():
        return CheckResult(
            name="Hooks registered",
            status=SKIP,
            detail="settings.json missing",
        )
    try:
        with open(settings_file) as f:
            settings = json.load(f)
    except (json.JSONDecodeError, OSError):
        return CheckResult(
            name="Hooks registered",
            status=SKIP,
            detail="settings.json unreadable",
        )

    hooks = settings.get("hooks", {})

    # Each entry: (description, event_name, script_substring)
    expected_hooks = [
        ("Sentinel gate (PreToolUse)", "PreToolUse", "sentinel-gate.py"),
        ("Pre-compact snapshot", "PreCompact", "pre-compact.py"),
        ("Post-compact recovery", "SessionStart", "post-compact.py"),
        ("Session init (startup/resume)", "SessionStart", "session-init.py"),
        ("Subagent start", "SubagentStart", "subagent-start.py"),
        ("Subagent stop", "SubagentStop", "subagent-stop.py"),
    ]

    missing = []
    found = []
    for description, event, script_name in expected_hooks:
        event_hooks = hooks.get(event, [])
        # Look for any hook command containing the script name
        has_script = any(
            script_name in (h.get("command", "") or "") for entry in event_hooks for h in (entry.get("hooks", []) or [])
        )
        if has_script:
            found.append(description)
        else:
            missing.append(description)

    if missing:
        return CheckResult(
            name="Hooks registered",
            status=FAIL,
            detail=f"Missing Empirica hooks: {', '.join(missing)}",
            hint="Run `empirica setup --force` to re-register hooks",
            data={"found": found, "missing": missing},
        )

    return CheckResult(
        name="Hooks registered",
        status=PASS,
        detail=f"All {len(found)} expected hooks registered",
        data={"found": found, "missing": missing},
    )


def check_statusline_runnable(claude_dir: Path) -> CheckResult:
    """Run the statusline script directly with a stub session and verify it
    produces output. This is the strongest signal that the script itself
    works — if it does, the issue is upstream (Claude Code wiring)."""
    plugin_dir = claude_dir / "plugins" / "local" / "empirica"
    script = plugin_dir / "scripts" / "statusline_empirica.py"
    if not script.exists():
        return CheckResult(
            name="Statusline script runnable",
            status=SKIP,
            detail="Plugin not installed (see previous check)",
        )

    stub = json.dumps(
        {
            "session_id": "diagnose-test",
            "cwd": str(Path.cwd()),
        }
    )

    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            input=stub,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="Statusline script runnable",
            status=FAIL,
            detail="Statusline script timed out (>5s)",
            hint="Check for blocking I/O, network calls, or DB lock contention",
            data={"timeout": True},
        )
    except Exception as e:
        return CheckResult(
            name="Statusline script runnable",
            status=FAIL,
            detail=f"Failed to invoke statusline script: {e}",
            hint=f"Verify the script is executable and Python is installed: `python3 {script}`",
            data={"error": str(e)},
        )

    if result.returncode != 0:
        return CheckResult(
            name="Statusline script runnable",
            status=FAIL,
            detail=(f"Statusline exited with code {result.returncode}: {(result.stderr or '')[:200]}"),
            hint="Check Empirica is importable: `python3 -c 'import empirica'`",
            data={"returncode": result.returncode, "stderr": result.stderr[:500]},
        )

    output = (result.stdout or "").strip()
    if not output:
        return CheckResult(
            name="Statusline script runnable",
            status=WARN,
            detail="Statusline ran but produced no output",
            hint=(
                "Statusline normally always prints something (`[no project]`, "
                "`[<name>:inactive]`, etc). Empty output suggests headless mode is "
                "active or the script short-circuited. Try `EMPIRICA_STATUS_MODE=full`."
            ),
            data={"output": ""},
        )

    return CheckResult(
        name="Statusline script runnable",
        status=PASS,
        detail=f"Produces output: {output[:80]}",
        data={"output": output[:200]},
    )


def check_project_initialized() -> CheckResult:
    """Verify the current directory (or an ancestor) has been initialized
    as an Empirica project via `empirica project-init`.

    This is the most common failure mode for new users: they install
    Empirica, launch Claude Code, and see no statusline because their
    working directory has no `.empirica/` — which means no project DB,
    no session, no statusline content.
    """
    cwd = Path.cwd()

    # Walk up the directory tree looking for .empirica/
    current = cwd
    while current != current.parent:
        candidate = current / ".empirica"
        if candidate.is_dir():
            project_yaml = candidate / "project.yaml"
            config_yaml = candidate / "config.yaml"
            if project_yaml.exists() or config_yaml.exists():
                detail = f"Found at {candidate}"
                if current != cwd:
                    detail += " (ancestor of cwd)"
                return CheckResult(
                    name="Empirica project initialized",
                    status=PASS,
                    detail=detail,
                    data={"project_root": str(current)},
                )
        current = current.parent

    return CheckResult(
        name="Empirica project initialized",
        status=FAIL,
        detail=f"No .empirica/ found in {cwd} or any ancestor",
        hint=(
            "Run `empirica project-init` in this repository (or any ancestor "
            "directory) to create the project. Without this, every Empirica "
            "CLI command fails with 'Cannot determine sessions.db path', and "
            "the Claude Code statusline shows `[no project]`."
        ),
        data={"cwd": str(cwd)},
    )


def check_active_session() -> CheckResult:
    """Verify there's an active Empirica session in the current project."""
    cwd = Path.cwd()
    db_path = cwd / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        return CheckResult(
            name="Active session in current project",
            status=SKIP,
            detail="Skipped — project not initialized (see previous check)",
            data={"db_path": str(db_path), "exists": False},
        )

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            SELECT session_id, ai_id, start_time, end_time
            FROM sessions
            WHERE end_time IS NULL
            ORDER BY start_time DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        conn.close()
    except sqlite3.Error as e:
        return CheckResult(
            name="Active session in current project",
            status=FAIL,
            detail=f"Cannot read project DB: {e}",
            hint="DB may be corrupt or schema is out of date — run `empirica project-bootstrap`",
            data={"db_path": str(db_path), "error": str(e)},
        )

    if not row:
        return CheckResult(
            name="Active session in current project",
            status=WARN,
            detail="Project DB exists but no active session",
            hint=(
                "Restart Claude Code in this directory (session-init hook will create one), "
                "or run `empirica session-create --ai-id claude-code` manually"
            ),
            data={"db_path": str(db_path), "active_session": None},
        )

    session_id, ai_id, start_time, _ = row
    return CheckResult(
        name="Active session in current project",
        status=PASS,
        detail=f"{session_id[:8]} ({ai_id}) started {start_time}",
        data={
            "session_id": session_id,
            "ai_id": ai_id,
            "start_time": start_time,
        },
    )


def check_marketplace_registered(claude_dir: Path) -> CheckResult:
    """Verify the local marketplace is registered with Claude Code."""
    known = claude_dir / "plugins" / "known_marketplaces.json"
    if not known.exists():
        return CheckResult(
            name="Local marketplace registered",
            status=WARN,
            detail=f"{known} does not exist",
            hint="Optional but recommended — run `empirica setup` to register",
            data={"path": str(known)},
        )
    try:
        with open(known) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return CheckResult(
            name="Local marketplace registered",
            status=WARN,
            detail=f"{known} exists but is not valid JSON",
            hint="Fix manually or run `empirica setup --force`",
        )
    if "local" not in data:
        return CheckResult(
            name="Local marketplace registered",
            status=WARN,
            detail="known_marketplaces.json exists but `local` entry missing",
            hint="Run `empirica setup` to add the local marketplace",
        )
    return CheckResult(
        name="Local marketplace registered",
        status=PASS,
        detail="`local` marketplace registered",
    )


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def run_all_checks() -> list[CheckResult]:
    """Run every diagnostic check in dependency order and return results.

    Order matters: later checks SKIP themselves when earlier dependencies
    fail (e.g. statusline_runnable skips if plugin files are missing).
    """
    results: list[CheckResult] = []

    # Foundation checks
    results.append(check_python_version())
    results.append(check_empirica_cli_on_path())

    claude_check = check_claude_dir()
    results.append(claude_check)
    if claude_check.status == FAIL:
        return results  # Can't go further without ~/.claude/

    claude_dir_path = Path(claude_check.data.get("path", ""))

    # Plugin and config checks
    results.append(check_plugin_files(claude_dir_path))
    results.append(check_settings_json(claude_dir_path))
    results.append(check_statusline_configured(claude_dir_path))
    results.append(check_hooks_registered(claude_dir_path))
    results.append(check_marketplace_registered(claude_dir_path))

    # Functional checks
    results.append(check_statusline_runnable(claude_dir_path))
    results.append(check_project_initialized())
    results.append(check_active_session())

    return results


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


_STATUS_EMOJI = {
    PASS: f"{_C.GREEN}✅{_C.RESET}",
    FAIL: f"{_C.RED}❌{_C.RESET}",
    WARN: f"{_C.YELLOW}⚠ {_C.RESET}",
    SKIP: f"{_C.GRAY}⊘ {_C.RESET}",
}


def format_human(results: list[CheckResult]) -> str:
    """Render results as colored human-readable output."""
    lines = [
        "",
        f"{_C.BOLD}Empirica + Claude Code Integration Diagnostic{_C.RESET}",
        f"{_C.GRAY}{'─' * 60}{_C.RESET}",
        "",
    ]

    counts = {PASS: 0, FAIL: 0, WARN: 0, SKIP: 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
        emoji = _STATUS_EMOJI.get(r.status, r.status)
        lines.append(f"{emoji} {_C.BOLD}{r.name}{_C.RESET}")
        if r.detail:
            lines.append(f"   {_C.GRAY}{r.detail}{_C.RESET}")
        if r.hint:
            lines.append(f"   {_C.CYAN}→ {r.hint}{_C.RESET}")
        lines.append("")

    lines.append(f"{_C.GRAY}{'─' * 60}{_C.RESET}")
    summary = (
        f"  {_C.GREEN}{counts.get(PASS, 0)} passed{_C.RESET}  "
        f"{_C.RED}{counts.get(FAIL, 0)} failed{_C.RESET}  "
        f"{_C.YELLOW}{counts.get(WARN, 0)} warnings{_C.RESET}  "
        f"{_C.GRAY}{counts.get(SKIP, 0)} skipped{_C.RESET}"
    )
    lines.append(summary)
    lines.append("")

    if counts.get(FAIL, 0) > 0:
        lines.append(f"{_C.RED}❌ Integration is not healthy. Fix the failed checks above.{_C.RESET}")
    elif counts.get(WARN, 0) > 0:
        lines.append(f"{_C.YELLOW}⚠ Integration mostly working — see warnings for optional improvements.{_C.RESET}")
    else:
        lines.append(f"{_C.GREEN}✅ Integration looks healthy.{_C.RESET}")
    lines.append("")

    return "\n".join(lines)


def format_json(results: list[CheckResult]) -> str:
    """Render results as machine-readable JSON."""
    counts = {PASS: 0, FAIL: 0, WARN: 0, SKIP: 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    payload = {
        "ok": counts.get(FAIL, 0) == 0,
        "summary": counts,
        "checks": [
            {
                "name": r.name,
                "status": r.status,
                "detail": r.detail,
                "hint": r.hint,
                "data": r.data,
            }
            for r in results
        ],
    }
    return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# CLI handler
# ---------------------------------------------------------------------------


def handle_diagnose_command(args) -> int:
    """Handle `empirica diagnose` — run integration checks for the chosen frontend.

    Frontends:
      - ``claude-code`` (default) — checks the Claude Code integration
        (~/.claude plugin install, statusline config, hooks, marketplace).
      - ``ecodex`` — checks the ecodex install (codex-empirica-plugin in
        ~/.codex, ecodex statusline runtime stdin wiring, codex-empirica-
        translator on 127.0.0.1:18080, curated provider env_keys, Rust
        cargo fmt+check). Implemented in :mod:`diagnose_ecodex`.
    """
    output_format = getattr(args, "output", "human")
    frontend = getattr(args, "frontend", "claude-code")
    fast = getattr(args, "fast", False)

    if frontend == "ecodex":
        from empirica.cli.command_handlers.diagnose_ecodex import (
            run_all_checks_ecodex,
        )

        results = run_all_checks_ecodex(fast=fast)
    else:
        results = run_all_checks()

    if output_format == "json":
        print(format_json(results))
    else:
        print(format_human(results))

    # Exit code: 0 all pass, 1 any fail, 2 any warn (no fail)
    has_fail = any(r.status == FAIL for r in results)
    has_warn = any(r.status == WARN for r in results)
    if has_fail:
        return 1
    if has_warn:
        return 2
    return 0
