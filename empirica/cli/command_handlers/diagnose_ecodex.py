"""Empirica Diagnose — ecodex frontend mode.

Sibling to ``diagnose.py`` (which targets Claude Code). This module exposes
the ecodex-specific checks: codex-empirica-plugin install state, statusline
runtime stdin wiring, translator binary + ``/healthz``, curated model
provider env keys, Rust compliance services (cargo fmt/clippy/check).

Reuses the ``CheckResult`` dataclass and status constants from
``diagnose.py`` so output formatting is identical and JSON consumers can
treat both frontends uniformly.

Wired into ``handle_diagnose_command`` via ``--frontend ecodex``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from empirica.cli.command_handlers.diagnose import (
    FAIL,
    PASS,
    SKIP,
    WARN,
    CheckResult,
    check_empirica_cli_on_path,
    check_python_version,
)


_ECODEX_PLUGIN_KEY = "empirica@nubaeon"

# Subset of vendored hook scripts whose drift matters most. We don't diff
# every file because some legitimately diverge (vendored copies may carry
# ecodex-specific patches over master). These three are the load-bearing
# ones — if they drift, the discipline pipeline behavior drifts with them.
_VENDORED_DRIFT_SENTINELS = (
    "hooks/sentinel-gate.py",
    "hooks/tool-router.py",
    "hooks/transaction-enforcer.py",
)


# ---------------------------------------------------------------------------
# Plugin install — codex-empirica-plugin
# ---------------------------------------------------------------------------


def _ecodex_plugin_cache_dir() -> Path | None:
    """Resolve the bundled-plugin cache dir.

    ecodex's installer stages the plugin under
    ``~/.codex/plugins/cache/nubaeon/empirica/<version>/``. Return the
    highest-version dir if any, else None.
    """
    base = Path.home() / ".codex" / "plugins" / "cache" / "nubaeon" / "empirica"
    if not base.is_dir():
        return None
    versioned = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name)
    return versioned[-1] if versioned else None


def check_ecodex_plugin_installed() -> CheckResult:
    """Verify the codex-empirica-plugin is installed in ~/.codex/plugins/cache."""
    cache_dir = _ecodex_plugin_cache_dir()
    if cache_dir is None:
        return CheckResult(
            name="ecodex plugin installed",
            status=FAIL,
            detail="No `~/.codex/plugins/cache/nubaeon/empirica/` directory",
            hint="Run `./ecodex/scripts/install.sh` from the ecodex repo",
        )
    # Codex plugin manifests live at <plugin>/.codex-plugin/plugin.json
    manifest = cache_dir / ".codex-plugin" / "plugin.json"
    if not manifest.is_file():
        return CheckResult(
            name="ecodex plugin installed",
            status=FAIL,
            detail=f"plugin.json missing at {manifest}",
            hint="Reinstall: `./ecodex/scripts/install.sh`",
            data={"cache_dir": str(cache_dir)},
        )
    try:
        manifest_data = json.loads(manifest.read_text())
    except json.JSONDecodeError as e:
        return CheckResult(
            name="ecodex plugin installed",
            status=FAIL,
            detail=f"plugin.json invalid JSON: {e}",
            hint="Reinstall: `./ecodex/scripts/install.sh`",
            data={"cache_dir": str(cache_dir)},
        )
    return CheckResult(
        name="ecodex plugin installed",
        status=PASS,
        detail=f"{cache_dir.name} at {cache_dir}",
        data={
            "cache_dir": str(cache_dir),
            "version": cache_dir.name,
            "plugin_key": manifest_data.get("name", _ECODEX_PLUGIN_KEY),
        },
    )


def _resolve_ecodex_repo_root() -> Path | None:
    """Best-effort resolve the ecodex repo root.

    Walks up from the doctor's own location looking for a clone of
    ``ecodex/`` at sibling depth, or honors ECODEX_REPO env override.
    Used by the vendored-drift check to compare on-disk source files
    against the canonical ~/.claude/plugins/local/empirica/ source.
    """
    override = os.environ.get("ECODEX_REPO")
    if override:
        candidate = Path(override).expanduser()
        if (candidate / "codex-rs" / "codex-empirica-plugin").is_dir():
            return candidate
    # Common sibling layouts in this monorepo.
    for guess in (
        Path.home() / "empirical-ai" / "ecodex",
        Path.cwd() / "ecodex",
        Path.cwd().parent / "ecodex",
    ):
        if (guess / "codex-rs" / "codex-empirica-plugin").is_dir():
            return guess
    return None


def check_ecodex_plugin_vendored_freshness() -> CheckResult:
    """Detect drift between vendored hook scripts in ecodex source vs the
    canonical CC empirica install at ``~/.claude/plugins/local/empirica/``.

    Tx-AK regression detector. The vendoring workflow is manual: maintainer
    runs ``ecodex/scripts/sync-empirica-assets.sh`` after empirica releases
    new hooks. If that step is missed, ecodex ships with stale discipline
    behavior — bugs fixed in master remain in vendored copies, new safelist
    entries don't propagate, etc. WARN (not FAIL) because some divergence
    is normal during empirica's active dev — the goal is surfacing, not
    blocking.

    Compares the three load-bearing hook scripts (sentinel-gate.py,
    tool-router.py, transaction-enforcer.py) by content hash. Skips when
    either source isn't available (e.g. CC empirica not installed; ecodex
    repo not at expected path).
    """
    import hashlib

    cc_empirica_hooks = (
        Path.home() / ".claude" / "plugins" / "local" / "empirica" / "hooks"
    )
    if not cc_empirica_hooks.is_dir():
        return CheckResult(
            name="ecodex plugin vendored freshness",
            status=SKIP,
            detail=(
                f"CC empirica master not present at {cc_empirica_hooks} — "
                "cannot compare drift"
            ),
        )
    repo_root = _resolve_ecodex_repo_root()
    if repo_root is None:
        return CheckResult(
            name="ecodex plugin vendored freshness",
            status=SKIP,
            detail=(
                "ecodex repo root not found (set ECODEX_REPO to override) — "
                "cannot compare drift"
            ),
        )
    vendored_root = (
        repo_root / "codex-rs" / "codex-empirica-plugin" / "assets" / "hooks_scripts"
    )
    if not vendored_root.is_dir():
        return CheckResult(
            name="ecodex plugin vendored freshness",
            status=SKIP,
            detail=f"vendored hooks_scripts/ missing at {vendored_root}",
        )

    drifted: list[dict[str, str]] = []
    fresh: list[str] = []
    for relpath in _VENDORED_DRIFT_SENTINELS:
        # Master uses hooks/<name>.py; vendored uses hooks_scripts/hooks/<name>.py.
        master_path = cc_empirica_hooks.parent / relpath
        vendored_path = vendored_root / relpath
        if not master_path.is_file() or not vendored_path.is_file():
            continue
        master_hash = hashlib.sha256(master_path.read_bytes()).hexdigest()
        vendored_hash = hashlib.sha256(vendored_path.read_bytes()).hexdigest()
        if master_hash != vendored_hash:
            master_mtime = int(master_path.stat().st_mtime)
            vendored_mtime = int(vendored_path.stat().st_mtime)
            age_seconds = master_mtime - vendored_mtime
            drifted.append(
                {
                    "file": relpath,
                    "vendored_age_seconds": str(age_seconds),
                    "vendored_age_human": _format_age(age_seconds),
                }
            )
        else:
            fresh.append(relpath)

    if not drifted:
        return CheckResult(
            name="ecodex plugin vendored freshness",
            status=PASS,
            detail=f"{len(fresh)} sentinel files in sync with master",
            data={"fresh_files": fresh},
        )
    summary = ", ".join(f"{d['file']} ({d['vendored_age_human']})" for d in drifted)
    return CheckResult(
        name="ecodex plugin vendored freshness",
        status=WARN,
        detail=f"vendored drift: {summary}",
        hint=(
            "Re-vendor with `ecodex/scripts/sync-empirica-assets.sh`, "
            "review the diff, bump PLUGIN_VERSION if hook contract changed, "
            "commit, rebuild, reinstall."
        ),
        data={"drifted": drifted, "fresh": fresh},
    )


def _format_age(seconds: int) -> str:
    """Human-readable drift description.

    Positive seconds = master is newer than vendored (vendored is stale —
    needs re-sync). Negative seconds = vendored is newer than master
    (vendored carries patches not yet upstreamed; usually fine but flagged
    so the maintainer is aware).
    """
    if seconds < 0:
        magnitude = _format_magnitude(-seconds)
        return f"vendored ahead by {magnitude}"
    magnitude = _format_magnitude(seconds)
    return f"vendored stale by {magnitude}"


def _format_magnitude(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def check_ecodex_plugin_writable_roots_declared() -> CheckResult:
    """Verify the empirica plugin's manifest declares ~/.empirica writable scope.

    Tx-AI regression detector. Without ``writableRoots: ["~/.empirica"]`` in
    the cached manifest, codex's WorkspaceWrite sandbox will block every
    empirica state write (sessions DB, instance pointers, transaction state)
    with EROFS — and sentinel-gate.py:2808 will catch the exception and
    silently fail-open ('allow'), making the entire discipline framework
    appear healthy while running as a no-op.

    The carve-out flows: manifest declares → codex plugin loader merges into
    SandboxPolicy.writable_roots at session bootstrap → landlock honors it.
    Symptom of regression: ``empirica project-switch`` (or any other empirica
    CLI invocation) hits 'Read-only file system' under ecodex's shell tool.
    """
    cache_dir = _ecodex_plugin_cache_dir()
    if cache_dir is None:
        return CheckResult(
            name="ecodex plugin writable_roots declared",
            status=SKIP,
            detail="No plugin install — see `ecodex plugin installed` check",
        )
    manifest = cache_dir / ".codex-plugin" / "plugin.json"
    if not manifest.is_file():
        return CheckResult(
            name="ecodex plugin writable_roots declared",
            status=SKIP,
            detail=f"plugin.json missing at {manifest}",
        )
    try:
        manifest_data = json.loads(manifest.read_text())
    except json.JSONDecodeError as e:
        return CheckResult(
            name="ecodex plugin writable_roots declared",
            status=FAIL,
            detail=f"plugin.json invalid JSON: {e}",
            hint="Reinstall: `./ecodex/scripts/install.sh`",
        )
    declared = manifest_data.get("writableRoots") or []
    if not isinstance(declared, list):
        return CheckResult(
            name="ecodex plugin writable_roots declared",
            status=FAIL,
            detail=f"writableRoots must be an array, got {type(declared).__name__}",
            hint="Edit plugin manifest: writableRoots: [\"~/.empirica\"]",
            data={"declared": declared},
        )
    has_empirica_home = any(
        isinstance(entry, str) and entry.strip() in {"~/.empirica", "~/.empirica/"}
        for entry in declared
    )
    if not has_empirica_home:
        return CheckResult(
            name="ecodex plugin writable_roots declared",
            status=FAIL,
            detail=(
                "writableRoots does NOT include ~/.empirica — agent-driven "
                "empirica CLI calls will hit EROFS under WorkspaceWrite sandbox "
                "and silently fail-open via sentinel-gate.py:2808"
            ),
            hint=(
                "Add to plugin manifest: writableRoots: [\"~/.empirica\"], "
                "then reinstall: `./ecodex/scripts/install.sh`"
            ),
            data={"declared": declared},
        )
    return CheckResult(
        name="ecodex plugin writable_roots declared",
        status=PASS,
        detail=f"writableRoots: {declared}",
        data={"declared": declared},
    )


def check_ecodex_plugin_hooks_feature_enabled() -> CheckResult:
    """Verify codex's plugin_hooks feature gate is on.

    Codex's `Feature::PluginHooks` is `Stage::UnderDevelopment` with
    `default_enabled: false`. Without `[features] plugin_hooks = true`
    in ~/.codex/config.toml, the entire plugin hook engine is disabled
    — empirica's PreToolUse / UserPromptSubmit / SessionStart / Stop
    hooks all silently no-op and the discipline pipeline goes dark
    in ecodex sessions.

    Tx-AC regression detector: this was the subtle root-cause behind
    "the proportionality block isn't reaching the agent" 2026-05-06.
    Symptom is invisible — no error, just no hook output in
    codex-tui.log. Easy to miss; cheap to verify here.
    """
    config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.is_file():
        return CheckResult(
            name="ecodex plugin_hooks feature enabled",
            status=SKIP,
            detail="~/.codex/config.toml missing",
            hint="See `ecodex plugin enabled in config` check",
        )
    text = config_path.read_text()
    # Lightweight match: under [features] block, look for plugin_hooks = true.
    in_features = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[features]"):
            in_features = True
            continue
        if line.startswith("[") and not line.startswith("[features]"):
            in_features = False
            continue
        if in_features and re.match(r"plugin_hooks\s*=\s*true", line):
            return CheckResult(
                name="ecodex plugin_hooks feature enabled",
                status=PASS,
                detail="[features] plugin_hooks = true in config",
            )
    return CheckResult(
        name="ecodex plugin_hooks feature enabled",
        status=FAIL,
        detail=(
            "[features] plugin_hooks not set true — codex's plugin hook engine "
            "is OFF by default (Stage::UnderDevelopment). Empirica's "
            "PreToolUse/UserPromptSubmit/SessionStart/Stop hooks won't fire. "
            "Sentinel gate, context injection, session bind all dark."
        ),
        hint=(
            "Add to ~/.codex/config.toml:\n\n"
            "[features]\n"
            "plugin_hooks = true\n"
            "plugins = true\n\n"
            "Then restart ecodex."
        ),
    )


def check_ecodex_plugin_enabled_in_config() -> CheckResult:
    """Verify ~/.codex/config.toml has `[plugins."empirica@nubaeon"]` enabled."""
    config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.is_file():
        return CheckResult(
            name="ecodex plugin enabled in config",
            status=FAIL,
            detail=f"{config_path} missing",
            hint="Run `./ecodex/scripts/install.sh` (will install default config)",
        )
    text = config_path.read_text()
    if f'[plugins."{_ECODEX_PLUGIN_KEY}"]' not in text:
        return CheckResult(
            name="ecodex plugin enabled in config",
            status=FAIL,
            detail=f"No `[plugins.\"{_ECODEX_PLUGIN_KEY}\"]` section in config",
            hint=f'Add to ~/.codex/config.toml:\n\n[plugins."{_ECODEX_PLUGIN_KEY}"]\nenabled = true',
        )
    # Coarse enabled check — toml parsing avoided to keep this stdlib-only
    return CheckResult(
        name="ecodex plugin enabled in config",
        status=PASS,
        detail=f"`[plugins.\"{_ECODEX_PLUGIN_KEY}\"]` declared",
    )


# ---------------------------------------------------------------------------
# Statusline — runtime stdin wiring
# ---------------------------------------------------------------------------


def check_ecodex_statusline_runtime_stdin() -> CheckResult:
    """Verify ecodex's plugin_statusline_runtime pipes session_id to the script.

    Known regression (T81 Tx-W diagnosis): ecodex's
    ``codex-rs/tui/src/plugin_statusline_runtime.rs`` invoked the script with
    ``Stdio::null()``, so the script always saw an empty input and rendered
    ``[ecodex:inactive]``. The fix is to pipe ``{"session_id": "..."}`` to
    the script's stdin (resolved from
    ``~/.empirica/instance_projects/tmux_<pane>.json`` or by cwd match).

    This check inspects the ecodex source directly when available and looks
    for ``Stdio::null()`` near the statusline-script invocation.
    """
    # Locate ecodex source — heuristic: $ECODEX_REPO_ROOT or common dev dirs.
    candidates = [
        Path(os.environ.get("ECODEX_REPO_ROOT", "")) if os.environ.get("ECODEX_REPO_ROOT") else None,
        Path.home() / "empirical-ai" / "ecodex",
        Path("/workspace/ecodex"),
    ]
    runtime_file: Path | None = None
    for c in candidates:
        if c is None:
            continue
        candidate = c / "codex-rs" / "tui" / "src" / "plugin_statusline_runtime.rs"
        if candidate.is_file():
            runtime_file = candidate
            break
    if runtime_file is None:
        return CheckResult(
            name="ecodex statusline runtime pipes session_id",
            status=SKIP,
            detail="ecodex source not found locally — check skipped",
            hint=(
                "Set ECODEX_REPO_ROOT to your ecodex checkout, or rerun this "
                "check from inside the ecodex repo"
            ),
        )
    text = runtime_file.read_text()
    # Only flag stdin's Stdio::null — stderr being nulled is correct
    # (we don't want script noise polluting the TUI footer), and the doc
    # comment explaining the fix mentions the old `Stdio::null()` literally.
    # Look specifically for `.stdin(Stdio::null())`.
    if ".stdin(Stdio::null())" in text:
        return CheckResult(
            name="ecodex statusline runtime pipes session_id",
            status=FAIL,
            detail=(
                "plugin_statusline_runtime.rs still calls .stdin(Stdio::null()) "
                "for the script subprocess — script will receive no input and "
                "render [ecodex:inactive]"
            ),
            hint=(
                "Switch to .stdin(Stdio::piped()) and write "
                "{\"session_id\":\"...\",\"cwd\":\"...\"} to child stdin. "
                "Resolve session_id from ~/.empirica/instance_projects/tmux_<pane>.json"
            ),
            data={"file": str(runtime_file)},
        )
    return CheckResult(
        name="ecodex statusline runtime pipes session_id",
        status=PASS,
        detail="No Stdio::null() in plugin_statusline_runtime.rs",
        data={"file": str(runtime_file)},
    )


def check_ecodex_statusline_script_runs() -> CheckResult:
    """Verify the bundled statusline script is executable + returns non-empty."""
    cache_dir = _ecodex_plugin_cache_dir()
    if cache_dir is None:
        return CheckResult(
            name="ecodex statusline script runnable",
            status=SKIP,
            detail="Plugin not installed — skipped",
            hint="See `ecodex plugin installed` check",
        )
    script = cache_dir / "hooks_scripts" / "scripts" / "statusline_empirica.py"
    if not script.is_file():
        return CheckResult(
            name="ecodex statusline script runnable",
            status=FAIL,
            detail=f"statusline_empirica.py missing at {script}",
            hint="Reinstall plugin: `./ecodex/scripts/install.sh`",
        )
    try:
        result = subprocess.run(
            [str(script)],
            input="",
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return CheckResult(
            name="ecodex statusline script runnable",
            status=FAIL,
            detail=f"Script invocation failed: {e}",
            hint="Check the script is executable: `chmod +x` the file",
            data={"script": str(script)},
        )
    output = (result.stdout or "").strip()
    if not output:
        return CheckResult(
            name="ecodex statusline script runnable",
            status=FAIL,
            detail="Script ran but produced no output",
            hint="Check Python error in stderr: " + (result.stderr or "(empty)"),
            data={"script": str(script), "stderr": result.stderr},
        )
    return CheckResult(
        name="ecodex statusline script runnable",
        status=PASS,
        detail=f"Script returned: {output[:80]}",
        data={"script": str(script), "output_preview": output[:200]},
    )


# ---------------------------------------------------------------------------
# Translator (codex-empirica-translator)
# ---------------------------------------------------------------------------


def _translator_port_listening(port: int = 18080) -> bool:
    """Cheap TCP probe — does anyone hold the translator port open?"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def check_ecodex_translator_listening() -> CheckResult:
    """Verify the codex-empirica-translator is listening on its expected port."""
    if not _translator_port_listening(18080):
        return CheckResult(
            name="ecodex translator listening",
            status=WARN,
            detail="Nothing listening on 127.0.0.1:18080",
            hint=(
                "Start the translator if you want to use Anthropic-protocol "
                "providers (Kimi, Anthropic). Example: "
                "`~/.local/bin/start-kimi-translator.sh &`"
            ),
        )
    return CheckResult(
        name="ecodex translator listening",
        status=PASS,
        detail="127.0.0.1:18080 accepting connections",
    )


def check_ecodex_translator_healthz() -> CheckResult:
    """Probe the translator's /healthz endpoint."""
    if not _translator_port_listening(18080):
        return CheckResult(
            name="ecodex translator /healthz",
            status=SKIP,
            detail="Translator not listening — skipped",
            hint="See `ecodex translator listening` check",
        )
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:18080/healthz", timeout=2
        ) as resp:
            status = resp.status
    except (urllib.error.URLError, TimeoutError) as e:
        return CheckResult(
            name="ecodex translator /healthz",
            status=WARN,
            detail=f"Translator on port but /healthz failed: {e}",
            hint=(
                "The translator may be an older build without /healthz. "
                "Rebuild with `cargo build -p codex-empirica-translator --release`"
            ),
        )
    if status >= 400:
        return CheckResult(
            name="ecodex translator /healthz",
            status=FAIL,
            detail=f"/healthz returned status {status}",
            hint="Restart the translator and re-test",
        )
    return CheckResult(
        name="ecodex translator /healthz",
        status=PASS,
        detail=f"/healthz returned {status}",
    )


# ---------------------------------------------------------------------------
# Provider keys + DNS reachability
# ---------------------------------------------------------------------------


def _read_codex_env_keys() -> set[str]:
    """Return the set of var names present in ~/.codex/.env (no values)."""
    env_path = Path.home() / ".codex" / ".env"
    if not env_path.is_file():
        return set()
    keys: set[str] = set()
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            keys.add(line.split("=", 1)[0].strip())
    return keys


def check_ecodex_provider_env_keys() -> CheckResult:
    """Verify env vars referenced by config.toml's [model_providers.*] are present in ~/.codex/.env.

    Read each [model_providers.<id>] block's ``env_key`` field and check
    membership in the .env file. Doesn't read values — just presence.
    """
    config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.is_file():
        return CheckResult(
            name="ecodex provider env keys",
            status=SKIP,
            detail="~/.codex/config.toml missing",
            hint="See `ecodex plugin enabled in config` check",
        )
    text = config_path.read_text()
    # Lightweight parse: find env_key = "X" lines under a [model_providers.*] section.
    referenced_keys: list[tuple[str, str]] = []  # (provider_id, env_var)
    current_provider: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[model_providers."):
            current_provider = stripped[len("[model_providers.") : -1]
        elif stripped.startswith("[") and not stripped.startswith("[model_providers."):
            current_provider = None
        elif current_provider and (
            stripped.startswith("env_key ")
            or stripped.startswith("env_key=")
            or stripped.startswith('env_key"')
        ):
            # env_key = "FOO_API_KEY". Match the bare field — NOT env_key_instructions.
            if "=" in stripped:
                value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    referenced_keys.append((current_provider, value))
    if not referenced_keys:
        return CheckResult(
            name="ecodex provider env keys",
            status=PASS,
            detail="No providers reference env_keys (all use unauthenticated endpoints)",
        )
    present = _read_codex_env_keys()
    env_overrides = {k for k, v in os.environ.items() if v}
    missing: list[tuple[str, str]] = []
    for provider, var in referenced_keys:
        if var in present or var in env_overrides:
            continue
        missing.append((provider, var))
    if not missing:
        return CheckResult(
            name="ecodex provider env keys",
            status=PASS,
            detail=f"All {len(referenced_keys)} provider env_keys present",
            data={"providers": [p for p, _ in referenced_keys]},
        )
    detail_lines = [f"{p} → ${v}" for p, v in missing]
    return CheckResult(
        name="ecodex provider env keys",
        status=WARN,
        detail=f"{len(missing)} provider env_keys missing: " + ", ".join(detail_lines),
        hint=(
            "Add the missing keys to ~/.codex/.env (chmod 600). The "
            "corresponding providers will fail at first request without "
            "them, but other providers continue to work."
        ),
        data={"missing": [{"provider": p, "env_var": v} for p, v in missing]},
    )


# ---------------------------------------------------------------------------
# Rust compliance — cargo fmt / clippy / check
# ---------------------------------------------------------------------------


def _ecodex_rust_workspace() -> Path | None:
    """Locate ecodex's codex-rs Cargo workspace if available."""
    candidates = [
        Path(os.environ.get("ECODEX_REPO_ROOT", "")) if os.environ.get("ECODEX_REPO_ROOT") else None,
        Path.home() / "empirical-ai" / "ecodex",
    ]
    for c in candidates:
        if c is None:
            continue
        ws = c / "codex-rs" / "Cargo.toml"
        if ws.is_file():
            return ws.parent
    return None


def check_ecodex_cargo_present() -> CheckResult:
    """Verify cargo is on PATH for the Rust compliance checks."""
    cargo = shutil.which("cargo")
    if cargo is None:
        return CheckResult(
            name="cargo on PATH",
            status=WARN,
            detail="`cargo` not found — Rust compliance checks unavailable",
            hint="Install Rust via https://rustup.rs",
        )
    return CheckResult(
        name="cargo on PATH",
        status=PASS,
        detail=cargo,
    )


def check_ecodex_cargo_fmt() -> CheckResult:
    """Run ``cargo fmt --check`` against ecodex's codex-rs workspace.

    Skipped when the workspace can't be located. WARN (not FAIL) when fmt
    diffs exist — they're stylistic, not correctness, but worth surfacing.
    """
    if shutil.which("cargo") is None:
        return CheckResult(
            name="ecodex cargo fmt clean",
            status=SKIP,
            detail="cargo not on PATH",
        )
    workspace = _ecodex_rust_workspace()
    if workspace is None:
        return CheckResult(
            name="ecodex cargo fmt clean",
            status=SKIP,
            detail="ecodex codex-rs workspace not found locally",
            hint="Set ECODEX_REPO_ROOT to your ecodex checkout",
        )
    try:
        result = subprocess.run(
            ["cargo", "fmt", "--check"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return CheckResult(
            name="ecodex cargo fmt clean",
            status=WARN,
            detail=f"cargo fmt invocation failed: {e}",
            hint="Run manually: `cargo fmt --check` from codex-rs/",
        )
    if result.returncode == 0:
        return CheckResult(
            name="ecodex cargo fmt clean",
            status=PASS,
            detail="No formatting violations",
        )
    diff_count = sum(1 for ln in (result.stdout or "").splitlines() if ln.startswith("Diff"))
    return CheckResult(
        name="ecodex cargo fmt clean",
        status=WARN,
        detail=f"~{diff_count} fmt violation(s) in workspace",
        hint="Run `cargo fmt` from codex-rs/ to fix; nightly `imports_granularity` warnings are expected",
        data={"diff_count": diff_count},
    )


def check_ecodex_cargo_check() -> CheckResult:
    """Run ``cargo check`` against ecodex's codex-rs workspace.

    Acts as a fast type-checker for the whole Rust side. Slow on cold
    cache (~30s) but fast on warm (~5s).
    """
    if shutil.which("cargo") is None:
        return CheckResult(
            name="ecodex cargo check passes",
            status=SKIP,
            detail="cargo not on PATH",
        )
    workspace = _ecodex_rust_workspace()
    if workspace is None:
        return CheckResult(
            name="ecodex cargo check passes",
            status=SKIP,
            detail="ecodex codex-rs workspace not found locally",
            hint="Set ECODEX_REPO_ROOT to your ecodex checkout",
        )
    try:
        result = subprocess.run(
            ["cargo", "check", "--workspace"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return CheckResult(
            name="ecodex cargo check passes",
            status=FAIL,
            detail=f"cargo check invocation failed: {e}",
            hint="Run manually: `cargo check --workspace` from codex-rs/",
        )
    if result.returncode == 0:
        return CheckResult(
            name="ecodex cargo check passes",
            status=PASS,
            detail="Workspace type-checks cleanly",
        )
    error_count = sum(1 for ln in (result.stderr or "").splitlines() if ln.startswith("error"))
    return CheckResult(
        name="ecodex cargo check passes",
        status=FAIL,
        detail=f"~{error_count} compile error(s)",
        hint="Run `cargo check --workspace` from codex-rs/ for details",
        data={"error_count": error_count},
    )


# ---------------------------------------------------------------------------
# Instance isolation key (ecodex T81 Tx-Z)
# ---------------------------------------------------------------------------


def check_empirica_proportionality_block_wired() -> CheckResult:
    """Verify the investigation-depth-proportionality block is wired into
    the empirica plugin's UserPromptSubmit hook (tool-router.py).

    Tx-AB regression detector: a future tool-router.py refactor that drops
    the build_investigation_proportionality_check call (or the block
    constant) silently disables the over-investigation guard.
    """
    candidates = [
        Path("/home/yogapad/empirical-ai/empirica/empirica/plugins/claude-code-integration/hooks/tool-router.py"),
        # Bundled-into-ecodex copy
        Path.home()
        / ".codex"
        / "plugins"
        / "cache"
        / "nubaeon"
        / "empirica"
        / "0.1.0"
        / "hooks_scripts"
        / "hooks"
        / "tool-router.py",
    ]
    router_file: Path | None = None
    for c in candidates:
        if c.is_file():
            router_file = c
            break
    if router_file is None:
        return CheckResult(
            name="empirica proportionality block wired",
            status=SKIP,
            detail="tool-router.py not found in either source or bundled location",
        )
    text = router_file.read_text()
    if "build_investigation_proportionality_check" not in text:
        return CheckResult(
            name="empirica proportionality block wired",
            status=FAIL,
            detail=f"build_investigation_proportionality_check not referenced in {router_file}",
            hint=(
                "Tx-AB fix: tool-router.py main() must call "
                "build_investigation_proportionality_check(prompt) and append "
                "its non-None return to context_parts before the EPP "
                "semantic-check block"
            ),
        )
    if "INVESTIGATION_PROPORTIONALITY_BLOCK" not in text:
        return CheckResult(
            name="empirica proportionality block wired",
            status=FAIL,
            detail="INVESTIGATION_PROPORTIONALITY_BLOCK constant missing — block was removed?",
            hint="Re-add the block constant to tool-router.py; see Tx-AB commit",
        )
    return CheckResult(
        name="empirica proportionality block wired",
        status=PASS,
        detail=f"Both block constant and builder function referenced in {router_file.name}",
    )


def check_ecodex_instance_isolation_key() -> CheckResult:
    """Verify the ecodex Rust source propagates EMPIRICA_INSTANCE_ID.

    Empirica's get_instance_id() priority list reads EMPIRICA_INSTANCE_ID
    first. ecodex's plugin (empirica_cli.rs) and TUI (chatwidget.rs) both
    set it from codex's session_id (thread_id UUID) so the entire
    empirica pipeline keys on codex's session — works identically across
    tmux/non-tmux/ssh/container/headless. Tx-Z's regression detector.

    This is a source-grep (like the statusline runtime check) — confirms
    the propagation is wired, not that it's running. Combined with the
    statusline-script-runnable + plugin-installed checks, that's enough
    coverage for the integration. A live env-var probe would only cover
    the doctor's OWN process, not ecodex's.
    """
    # Locate ecodex source
    candidates = [
        Path(os.environ.get("ECODEX_REPO_ROOT", "")) if os.environ.get("ECODEX_REPO_ROOT") else None,
        Path.home() / "empirical-ai" / "ecodex",
    ]
    repo_root: Path | None = None
    for c in candidates:
        if c is None:
            continue
        if (c / "codex-rs" / "Cargo.toml").is_file():
            repo_root = c
            break
    if repo_root is None:
        return CheckResult(
            name="ecodex instance isolation key propagated",
            status=SKIP,
            detail="ecodex source not found locally — check skipped",
            hint="Set ECODEX_REPO_ROOT to your ecodex checkout",
        )
    plugin_cli = repo_root / "codex-rs" / "codex-empirica-plugin" / "src" / "empirica_cli.rs"
    tui_chat = repo_root / "codex-rs" / "tui" / "src" / "chatwidget.rs"
    missing: list[str] = []
    if plugin_cli.is_file():
        text = plugin_cli.read_text()
        if 'EMPIRICA_INSTANCE_ID' not in text:
            missing.append("plugin/empirica_cli.rs (hook subprocesses won't get the key)")
    else:
        missing.append("plugin/empirica_cli.rs (file missing)")
    if tui_chat.is_file():
        text = tui_chat.read_text()
        if 'EMPIRICA_INSTANCE_ID' not in text:
            missing.append("tui/chatwidget.rs (statusline subprocess won't get the key)")
    else:
        missing.append("tui/chatwidget.rs (file missing)")
    if missing:
        return CheckResult(
            name="ecodex instance isolation key propagated",
            status=FAIL,
            detail=f"EMPIRICA_INSTANCE_ID propagation missing in: {', '.join(missing)}",
            hint=(
                "Tx-Z fix: plugin's empirica_cli.rs run_hook_script() must "
                "extract session_id from input JSON and set "
                "EMPIRICA_INSTANCE_ID on subprocess env; TUI's chatwidget.rs "
                "must `unsafe { std::env::set_var('EMPIRICA_INSTANCE_ID', "
                "session.thread_id.to_string()) }` at session bootstrap"
            ),
        )
    return CheckResult(
        name="ecodex instance isolation key propagated",
        status=PASS,
        detail="EMPIRICA_INSTANCE_ID propagated from plugin + TUI",
    )


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def run_all_checks_ecodex(*, fast: bool = False) -> list[CheckResult]:
    """Run every ecodex-frontend diagnostic check.

    Args:
        fast: When True, skip slow checks (cargo check). Useful for the
              skill's interactive walk-through; CI can leave fast=False.
    """
    results: list[CheckResult] = []
    # Foundation (shared with diagnose)
    results.append(check_python_version())
    results.append(check_empirica_cli_on_path())
    # Plugin install state
    results.append(check_ecodex_plugin_installed())
    results.append(check_ecodex_plugin_enabled_in_config())
    # Sandbox carve-out — without ~/.empirica in writableRoots, every empirica
    # state write hits EROFS and sentinel silently fail-opens. Subtle dark
    # failure (Tx-AI regression detector).
    results.append(check_ecodex_plugin_writable_roots_declared())
    # Vendored hook drift — vendoring is a manual maintainer step; if missed,
    # ecodex ships stale discipline. WARN-level surfacing (Tx-AK regression
    # detector). Skips when ECODEX_REPO can't be located or CC empirica
    # isn't installed locally.
    results.append(check_ecodex_plugin_vendored_freshness())
    # Feature gate — without this on, the plugin's hooks ALL silently no-op.
    # Subtle failure mode (no error, just dark integration); doctor catches it.
    results.append(check_ecodex_plugin_hooks_feature_enabled())
    # Statusline (the path most likely to break invisibly)
    results.append(check_ecodex_statusline_runtime_stdin())
    results.append(check_ecodex_statusline_script_runs())
    # Instance isolation — the layer that makes multi-instance work in any
    # terminal context (Tx-Z propagates codex thread_id as EMPIRICA_INSTANCE_ID)
    results.append(check_ecodex_instance_isolation_key())
    # Behavioral discipline — ensures Tx-AB proportionality block is in the
    # UserPromptSubmit hook so agents over-investigation gets countered.
    results.append(check_empirica_proportionality_block_wired())
    # Translator (Anthropic-protocol providers depend on this)
    results.append(check_ecodex_translator_listening())
    results.append(check_ecodex_translator_healthz())
    # Provider config
    results.append(check_ecodex_provider_env_keys())
    # Rust compliance — slow checks gated behind fast=False
    results.append(check_ecodex_cargo_present())
    if not fast:
        results.append(check_ecodex_cargo_fmt())
        results.append(check_ecodex_cargo_check())
    return results
