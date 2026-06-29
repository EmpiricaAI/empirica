"""
Empirica Doctor - install + mesh-participation health check.

Sibling to `diagnose` (which is Claude Code-centric). `doctor` checks the
state of an Empirica install regardless of frontend — install presence,
project state, cortex connectivity, ntfy mesh, listener arming, MCP server
config. Designed to be the single command an operator runs on a peer
machine to audit its install + mesh participation surface (closes
prop_vnsvs6th6bc5lhprbhylvdxwmi from cortex AI, 2026-05-18).

Designed to be callable from Claude Desktop via the empirica-mcp `doctor`
tool, returning structured JSON the AI can interpret without shell access.

Output modes:
  --output json     (default) — machine-readable
  --output human    colored text with fix hints

Exit codes:
  0 — all checks passed (or only WARN)
  1 — one or more FAIL checks
  2 — one or more WARN checks (no FAIL) — only when `--strict-warn`
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PASS = "PASS"  # noqa: S105
FAIL = "FAIL"
WARN = "WARN"
SKIP = "SKIP"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    hint: str = ""
    data: dict[str, Any] = field(default_factory=dict)


# ─── Helpers ────────────────────────────────────────────────────────────


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def _run(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return -1, "", str(e)


def _http_get(url: str, headers: dict | None = None, timeout: float = 5.0) -> tuple[int, str]:
    """GET helper using stdlib urllib. Returns (status_code, body) or (-1, error_str)."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, body
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return -1, str(e)


# ─── Install presence ──────────────────────────────────────────────────


def check_python() -> Check:
    v = sys.version_info
    if v >= (3, 10):
        return Check("Python version", PASS, f"{v.major}.{v.minor}.{v.micro}")
    return Check("Python version", FAIL, f"{v.major}.{v.minor}.{v.micro}", "Empirica requires Python 3.10+")


def check_empirica_cli() -> Check:
    path = _which("empirica")
    if not path:
        return Check("empirica CLI on PATH", FAIL, "", "pip install --user empirica  (then restart shell)")
    rc, out, _ = _run(["empirica", "--version"])
    version = out if rc == 0 else "unknown"
    return Check("empirica CLI on PATH", PASS, f"{path} ({version})", data={"path": path, "version": version})


def check_empirica_mcp() -> Check:
    path = _which("empirica-mcp")
    if not path:
        return Check(
            "empirica-mcp on PATH",
            WARN,
            "",
            "pip install --user empirica-mcp  (only needed for Claude Desktop / IDE MCP clients)",
            data={"path": None},
        )
    return Check("empirica-mcp on PATH", PASS, path, data={"path": path})


def check_claude_code_cli() -> Check:
    """`claude` CLI presence (optional — only needed for Claude Code users)."""
    path = _which("claude")
    if not path:
        return Check(
            "Claude Code CLI on PATH",
            WARN,
            "",
            "Install Claude Code from https://docs.claude.com/claude-code  (skip if using another frontend)",
            data={"path": None},
        )
    return Check("Claude Code CLI on PATH", PASS, path, data={"path": path})


def check_git_present() -> Check:
    path = _which("git")
    if not path:
        return Check(
            "git on PATH",
            FAIL,
            "",
            "Install git (https://git-scm.com)  — Empirica writes artifacts to refs/notes/empirica_*",
            data={"path": None},
        )
    return Check("git on PATH", PASS, path, data={"path": path})


def check_noetic_tools() -> Check:
    """Tier-1 noetic CLI tools that sharpen agentic recon (recommended, not required).

    These are read-only/inert and are on the Sentinel's noetic allowlist, so when
    present they flow free for a practitioner doing investigation — yq for YAML,
    fd for fast gitignore-aware find, ast-grep for structural (by-syntax) code
    search, rg/jq as the search + JSON workhorses. Absence is a WARN, never a
    failure: empirica works without them.
    """
    # (binaries, human description). fd is `fdfind` on Debian/Ubuntu; ast-grep's
    # short alias `sg` is deliberately NOT probed (it collides with the setgroups
    # command), so only the full `ast-grep` name counts as present.
    tools: dict[str, tuple[list[str], str]] = {
        "rg": (["rg"], "ripgrep — fast, gitignore-aware search"),
        "fd": (["fd", "fdfind"], "fast, gitignore-aware file find"),
        "jq": (["jq"], "JSON query"),
        "yq": (["yq"], "YAML query"),
        "ast-grep": (["ast-grep"], "structural / AST-aware code search"),
    }
    present: dict[str, str | None] = {}
    for label, spec in tools.items():
        present[label] = next((p for n in spec[0] if (p := _which(n))), None)
    missing = [t for t, p in present.items() if not p]
    found = {t: p for t, p in present.items() if p}
    if not missing:
        return Check("Noetic tools (Tier 1)", PASS, "all present: " + ", ".join(tools), data={"present": found})
    return Check(
        "Noetic tools (Tier 1)",
        WARN,
        f"{len(found)}/{len(tools)} present; missing: {', '.join(missing)}",
        "Install for sharper agentic recon (rg/fd/jq/yq/ast-grep) — all read-only, "
        "Sentinel-noetic. e.g. `cargo install ripgrep fd-find ast-grep` or your package manager.",
        data={"present": found, "missing": missing},
    )


# ─── Project state ─────────────────────────────────────────────────────


def check_empirica_folder(cwd: Path | None = None) -> Check:
    cwd = cwd or Path.cwd()
    folder = cwd / ".empirica"
    if not folder.exists():
        return Check(
            ".empirica/ folder", WARN, f"not present at {cwd}", "Run `empirica project-init` in a project directory"
        )
    subdirs = [d.name for d in folder.iterdir() if d.is_dir()]
    return Check(
        ".empirica/ folder", PASS, f"{folder} ({len(subdirs)} subdirs)", data={"path": str(folder), "subdirs": subdirs}
    )


def check_project_yaml(cwd: Path | None = None) -> Check:
    """`.empirica/project.yaml` exists, parses, and carries `ai_id`."""
    cwd = cwd or Path.cwd()
    pyaml_path = cwd / ".empirica" / "project.yaml"
    if not pyaml_path.exists():
        return Check(
            "project.yaml present + has ai_id",
            WARN,
            f"not at {pyaml_path}",
            "Run `empirica project-init`",
            data={"path": str(pyaml_path)},
        )
    try:
        import yaml
    except ImportError:
        return Check("project.yaml present + has ai_id", WARN, "PyYAML not installed", "pip install pyyaml")
    try:
        data = yaml.safe_load(pyaml_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return Check("project.yaml present + has ai_id", FAIL, f"unparseable: {e}", "Inspect/repair the file manually")
    if not isinstance(data, dict):
        return Check("project.yaml present + has ai_id", FAIL, "not a YAML object", "Inspect/repair the file manually")
    ai_id = data.get("ai_id")
    if not ai_id:
        return Check(
            "project.yaml present + has ai_id",
            WARN,
            f"{pyaml_path} (no ai_id)",
            "Re-run `empirica project-init --force` or edit project.yaml to add ai_id",
            data={"path": str(pyaml_path)},
        )
    return Check(
        "project.yaml present + has ai_id",
        PASS,
        f"ai_id={ai_id}",
        data={
            "path": str(pyaml_path),
            "ai_id": ai_id,
            "org_id": data.get("org_id"),
            "tenant_slug": data.get("tenant_slug"),
            "mesh_id_prefix": data.get("mesh_id_prefix"),
        },
    )


def check_sessions_db(cwd: Path | None = None) -> Check:
    """`.empirica/sessions/sessions.db` is openable + has the sessions table."""
    cwd = cwd or Path.cwd()
    db_path = cwd / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        return Check(
            "sessions DB accessible",
            WARN,
            f"not at {db_path}",
            "`empirica session-create --ai-id <id>` creates it on first run",
            data={"path": str(db_path)},
        )
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()
            session_count = 0
            if row:
                cnt = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
                session_count = cnt[0] if cnt else 0
    except sqlite3.Error as e:
        return Check(
            "sessions DB accessible",
            FAIL,
            f"sqlite error: {e}",
            "DB may be corrupt — back up and recreate via `empirica session-create`",
            data={"path": str(db_path)},
        )
    if not row:
        return Check(
            "sessions DB accessible",
            WARN,
            "no `sessions` table",
            "Run any `empirica session-create` to bootstrap schema",
            data={"path": str(db_path)},
        )
    return Check(
        "sessions DB accessible",
        PASS,
        f"{db_path} ({session_count} sessions)",
        data={"path": str(db_path), "sessions": session_count},
    )


# ─── Cortex connectivity ───────────────────────────────────────────────


def _resolve_cortex_creds() -> tuple[str | None, str | None]:
    """Cortex URL + api_key from env vars or credentials.yaml."""
    url = os.environ.get("CORTEX_REMOTE_URL") or os.environ.get("CORTEX_URL")
    api_key = os.environ.get("CORTEX_API_KEY")
    if url and api_key:
        return url.rstrip("/"), api_key
    try:
        from empirica.config.credentials_loader import get_credentials_loader

        cfg = get_credentials_loader().get_cortex_config()
        url = url or cfg.get("url")
        api_key = api_key or cfg.get("api_key")
    except Exception:
        pass
    return (url.rstrip("/") if url else None, api_key)


def check_cortex_creds() -> Check:
    """Cortex URL + api_key present (env vars or credentials.yaml)."""
    url, api_key = _resolve_cortex_creds()
    missing = []
    if not url:
        missing.append("url")
    if not api_key:
        missing.append("api_key")
    if missing:
        return Check(
            "Cortex credentials configured",
            WARN,
            f"missing: {', '.join(missing)}",
            "Run `empirica setup-claude-code` (interactive wizard) or hand-edit ~/.empirica/credentials.yaml",
            data={"url": url, "has_api_key": bool(api_key)},
        )
    return Check(
        "Cortex credentials configured", PASS, f"url={url} (api_key present)", data={"url": url, "has_api_key": True}
    )


def check_cortex_auth() -> Check:
    """GET /v1/users/me — validates auth + surfaces mesh Phase 1 fields."""
    url, api_key = _resolve_cortex_creds()
    if not (url and api_key):
        return Check(
            "Cortex auth + mesh fields", SKIP, "no creds configured", "See 'Cortex credentials configured' check above"
        )
    me_url = f"{url}/v1/users/me"
    status, body = _http_get(me_url, headers={"Authorization": f"Bearer {api_key}"})
    if status == -1:
        return Check(
            "Cortex auth + mesh fields",
            WARN,
            f"{me_url} unreachable: {body}",
            "Check network / VPN / CORTEX_REMOTE_URL",
        )
    if status == 401:
        return Check(
            "Cortex auth + mesh fields",
            FAIL,
            f"{me_url} → 401 Unauthorized",
            "Rotate api_key via cortex admin, then update ~/.empirica/credentials.yaml",
        )
    if status >= 400:
        return Check(
            "Cortex auth + mesh fields",
            FAIL,
            f"{me_url} → {status}",
            "Check cortex server logs",
            data={"status": status},
        )
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return Check("Cortex auth + mesh fields", WARN, f"{status} but malformed JSON", data={"status": status})
    mesh_fields = {k: payload.get(k) for k in ("org_id", "tenant_slug", "mesh_id_prefix")}
    missing = [k for k, v in mesh_fields.items() if not v]
    if missing:
        return Check(
            "Cortex auth + mesh fields",
            WARN,
            f"auth OK; mesh fields missing: {', '.join(missing)} (cortex behind Phase 1 SHA c89a907?)",
            "Update the cortex server install",
            data={"status": status, **mesh_fields},
        )
    return Check(
        "Cortex auth + mesh fields",
        PASS,
        f"auth OK; org={mesh_fields['org_id']} tenant={mesh_fields['tenant_slug']}",
        data={"status": status, **mesh_fields},
    )


# ─── ntfy mesh ─────────────────────────────────────────────────────────


def _resolve_ntfy_creds() -> dict[str, str | None]:
    """ntfy creds from env vars or credentials.yaml. Returns {url, topic, user, password, token}."""
    cfg = {
        "url": os.environ.get("ORCHESTRATION_NTFY_URL") or os.environ.get("NTFY_URL"),
        "topic": os.environ.get("ORCHESTRATION_NTFY_TOPIC"),
        "user": os.environ.get("ORCHESTRATION_NTFY_USER"),
        "password": os.environ.get("ORCHESTRATION_NTFY_PASS"),
        "token": os.environ.get("ORCHESTRATION_NTFY_TOKEN"),
    }
    if all(cfg.values()):
        return cfg
    try:
        from empirica.config.credentials_loader import get_credentials_loader

        file_cfg = get_credentials_loader().get_ntfy_config()
        for k in ("url", "topic", "user", "password", "token"):
            cfg[k] = cfg.get(k) or file_cfg.get(k)
    except Exception:
        pass
    return cfg


def check_ntfy_creds() -> Check:
    """ntfy URL + topic + (user+password OR token) configured."""
    cfg = _resolve_ntfy_creds()
    url, topic = cfg.get("url"), cfg.get("topic")
    has_basic = bool(cfg.get("user") and cfg.get("password"))
    has_token = bool(cfg.get("token"))
    missing = []
    if not url:
        missing.append("url")
    if not topic:
        missing.append("topic")
    if not (has_basic or has_token):
        missing.append("auth (token OR user+password)")
    if missing:
        return Check(
            "ntfy credentials configured",
            WARN,
            f"missing: {', '.join(missing)}",
            "Run `empirica setup-claude-code` wizard or hand-edit ~/.empirica/credentials.yaml",
        )
    return Check(
        "ntfy credentials configured",
        PASS,
        f"url={url} topic={topic} ({'token' if has_token else 'basic'})",
        data={"url": url, "topic": topic, "auth": "token" if has_token else "basic"},
    )


def check_ntfy_auth() -> Check:
    """GET /v1/account — validates ntfy auth works."""
    cfg = _resolve_ntfy_creds()
    url = cfg.get("url")
    if not url:
        return Check("ntfy reachable + auth", SKIP, "no ntfy url configured", "See 'ntfy credentials configured' above")
    account_url = f"{url.rstrip('/')}/v1/account"
    headers = {}
    if cfg.get("token"):
        headers["Authorization"] = f"Bearer {cfg['token']}"
    elif cfg.get("user") and cfg.get("password"):
        import base64

        creds = base64.b64encode(f"{cfg['user']}:{cfg['password']}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"
    else:
        return Check("ntfy reachable + auth", SKIP, "no auth configured")
    status, _ = _http_get(account_url, headers=headers)
    if status == -1:
        return Check("ntfy reachable + auth", WARN, f"{account_url} unreachable", "Check network / VPN / NTFY_URL")
    if status in (200, 201):
        return Check("ntfy reachable + auth", PASS, f"{account_url} → {status}", data={"status": status})
    if status in (401, 403):
        return Check(
            "ntfy reachable + auth",
            FAIL,
            f"{account_url} → {status} (bad creds)",
            "Verify token / user / password in credentials.yaml",
            data={"status": status},
        )
    return Check("ntfy reachable + auth", WARN, f"{account_url} → {status}", data={"status": status})


# ─── Listener / loops ──────────────────────────────────────────────────


def check_loops_registered() -> Check:
    """`empirica loop list` shows at least the canonical loops."""
    if not _which("empirica"):
        return Check("canonical loops registered", SKIP, "empirica CLI not on PATH")
    rc, out, _ = _run(["empirica", "loop", "list", "--output", "json"], timeout=10.0)
    if rc != 0:
        return Check("canonical loops registered", WARN, "`empirica loop list` failed", "Run manually for details")
    try:
        payload = json.loads(out) if out else {}
    except json.JSONDecodeError:
        return Check("canonical loops registered", WARN, "malformed loop-list output")
    loops = payload.get("loops", []) if isinstance(payload, dict) else (payload or [])
    if not loops:
        return Check(
            "canonical loops registered",
            WARN,
            "no loops registered",
            "Open cockpit (`empirica cockpit`) and toggle Events on, OR run `empirica loop install --canonical`",
            data={"loops": []},
        )
    names = [str(loop.get("name") or loop.get("loop_name") or "?") for loop in loops]
    return Check(
        "canonical loops registered", PASS, f"{len(loops)} loop(s): {', '.join(names[:5])}", data={"loops": names}
    )


def check_listener_service(cwd: Path | None = None) -> Check:
    """Persistent listener service (systemd-user / launchd) status for project's ai_id.

    Added 2026-05-18 for prop_flrtxxn32japbazq — the system-level service
    that keeps `empirica loop listen` alive outside Claude sessions, so
    wake events arrive in real time.
    """
    cwd = cwd or Path.cwd()
    pyaml_path = cwd / ".empirica" / "project.yaml"
    if not pyaml_path.exists():
        return Check("listener service installed", SKIP, "no project.yaml in cwd")
    try:
        import yaml

        data = yaml.safe_load(pyaml_path.read_text(encoding="utf-8")) or {}
        ai_id = data.get("ai_id") if isinstance(data, dict) else None
    except Exception:
        return Check("listener service installed", SKIP, "project.yaml unparseable")
    if not ai_id:
        return Check("listener service installed", SKIP, "no ai_id in project.yaml")

    try:
        from empirica.core.loop_scheduler.persistent_listener import (
            PersistentListenerService,
        )
    except ImportError:
        return Check("listener service installed", SKIP, "persistent_listener module missing")

    service = PersistentListenerService()
    status = service.status(ai_id)
    if status.backend == "unavailable":
        return Check(
            "listener service installed",
            WARN,
            "no supported backend on this host (systemd-user / launchd)",
            "Linux/WSL2 needs systemd-user; macOS needs launchctl",
            data={"backend": "unavailable", "ai_id": ai_id},
        )
    if not status.installed:
        return Check(
            "listener service installed",
            WARN,
            f"no {status.backend} service for ai_id={ai_id}",
            f"`empirica loop listen-install --ai-id {ai_id}` (or re-run `empirica setup-claude-code`)",
            data={"backend": status.backend, "installed": False, "ai_id": ai_id},
        )
    if not status.active:
        return Check(
            "listener service installed",
            WARN,
            f"{status.backend} service installed but inactive",
            f"Restart: `empirica loop listen-install --ai-id {ai_id}` (idempotent)",
            data={"backend": status.backend, "installed": True, "active": False, "ai_id": ai_id},
        )
    return Check(
        "listener service installed",
        PASS,
        f"{status.backend} service active for ai_id={ai_id}",
        data={
            "backend": status.backend,
            "installed": True,
            "active": True,
            "ai_id": ai_id,
            "unit_path": status.unit_path,
            "log_path": status.log_path,
        },
    )


# ─── MCP server config ─────────────────────────────────────────────────


def _find_mcp_config_paths() -> list[Path]:
    """Common locations for MCP client config that may register empirica/cortex servers."""
    home = Path.home()
    return [
        home / ".claude" / "mcp.json",  # Claude Code
        home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",  # macOS Desktop
        home / ".config" / "Claude" / "claude_desktop_config.json",  # Linux Desktop fallback
    ]


def check_mcp_config() -> Check:
    """Surface any MCP config file's mcpServers entries (read-only, no modification)."""
    found_configs = []
    server_names: set[str] = set()
    for path in _find_mcp_config_paths():
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        servers = data.get("mcpServers") or {}
        if not isinstance(servers, dict):
            continue
        found_configs.append({"path": str(path), "servers": list(servers.keys())})
        server_names.update(servers.keys())

    if not found_configs:
        return Check(
            "MCP servers configured",
            WARN,
            "no MCP config found",
            "Optional — only needed for Claude Desktop / Cursor / Windsurf",
            data={"configs": []},
        )

    has_empirica = "empirica" in server_names
    has_cortex = "cortex" in server_names
    if has_empirica or has_cortex:
        detail_parts = [f"{c['path']}: {', '.join(c['servers'])}" for c in found_configs]
        return Check(
            "MCP servers configured",
            PASS,
            " | ".join(detail_parts),
            data={"configs": found_configs, "has_empirica": has_empirica, "has_cortex": has_cortex},
        )
    return Check(
        "MCP servers configured",
        WARN,
        "MCP config present but no `empirica` or `cortex` server entry",
        "Run `empirica setup-claude-code` to register the empirica MCP server",
        data={"configs": found_configs, "has_empirica": False, "has_cortex": False},
    )


# ─── Sync state (pre-existing) ─────────────────────────────────────────


def check_git_remote(cwd: Path | None = None) -> Check:
    cwd = cwd or Path.cwd()
    if not (cwd / ".git").exists():
        return Check("git repo + remote", WARN, "not a git repo", "git init && git remote add origin <url>")
    rc, out, _ = _run(["git", "-C", str(cwd), "remote", "-v"])
    if rc != 0 or not out:
        return Check(
            "git repo + remote", WARN, "no remote configured", "git remote add origin <url>  — sync_push needs a remote"
        )
    remotes = [line.split()[0] for line in out.splitlines() if line]
    return Check("git repo + remote", PASS, f"{len(set(remotes))} configured", data={"remotes": list(set(remotes))})


def check_sync_state(cwd: Path | None = None) -> Check:
    cwd = cwd or Path.cwd()
    if not (cwd / ".git").exists():
        return Check("sync state", SKIP, "not a git repo")
    rc, out, _ = _run(["git", "-C", str(cwd), "status", "--porcelain"])
    if rc != 0:
        return Check("sync state", WARN, "git status failed")
    pending = len([line for line in out.splitlines() if line.strip()])
    if pending > 0:
        return Check(
            "sync state",
            WARN,
            f"{pending} uncommitted changes",
            "Call empirica sync-push to propagate to Cortex",
            data={"pending_changes": pending},
        )
    return Check("sync state", PASS, "clean", data={"pending_changes": 0})


def check_cortex_reachability() -> Check:
    """Basic auth-less reachability probe.

    Any HTTP response (including 404 / 401) means a server is listening —
    only network/DNS failure counts as unreachable. The auth check above
    is the authoritative auth signal; this is just "can we talk to the
    box at all?"
    """
    url, _ = _resolve_cortex_creds()
    cortex_url = url or "https://cortex.getempirica.com"
    base = cortex_url.rstrip("/")
    # Try /v1/health first (current API surface), fall back to /cortex/health,
    # then plain root. Any HTTP response = reachable.
    for probe in (f"{base}/v1/health", f"{base}/cortex/health", base):
        status, body = _http_get(probe)
        if status >= 0:
            # Server responded — that's reachability, regardless of status code.
            return Check(
                "Cortex reachability",
                PASS,
                f"{probe} → {status}",
                data={"url": cortex_url, "probe": probe, "status": status},
            )
        # Network/DNS failure — try next probe in case it's a path-specific block.
        last_error = body
    return Check(
        "Cortex reachability",
        WARN,
        f"{base} unreachable: {last_error}",
        "Check network or CORTEX_REMOTE_URL env var",
        data={"url": cortex_url, "error": last_error},
    )


# ─── Tailscale mesh ────────────────────────────────────────────────────


def check_tailscale() -> Check:
    """tailscale membership + peer count (needed for tailnet-routed Cortex / LLM backend)."""
    if not _which("tailscale"):
        return Check(
            "Tailscale mesh",
            SKIP,
            "tailscale CLI not installed",
            "Install if you depend on tailnet routing for Cortex / LLM backend",
        )
    rc, out, err = _run(["tailscale", "status", "--json"], timeout=5.0)
    if rc != 0:
        return Check(
            "Tailscale mesh",
            WARN,
            "`tailscale status` failed",
            "Run `tailscale up` to authenticate",
            data={"error": err or out},
        )
    try:
        payload = json.loads(out) if out else {}
    except json.JSONDecodeError:
        return Check("Tailscale mesh", WARN, "malformed tailscale status output")
    backend_state = payload.get("BackendState", "")
    self_node = payload.get("Self", {}) or {}
    peers = payload.get("Peer", {}) or {}
    if backend_state != "Running":
        return Check(
            "Tailscale mesh",
            WARN,
            f"backend state: {backend_state}",
            "Run `tailscale up`",
            data={"backend_state": backend_state},
        )
    own_ips = self_node.get("TailscaleIPs") or []
    own_ip = own_ips[0] if own_ips else ""
    return Check(
        "Tailscale mesh",
        PASS,
        f"connected ({own_ip}, {len(peers)} peer(s))",
        data={"ip": own_ip, "peers": len(peers), "magic_dns": payload.get("MagicDNSSuffix")},
    )


# ─── LLM backend (ollama) ──────────────────────────────────────────────


def check_ollama_backend() -> Check:
    """LLM backend reachable + at least one embedder loaded (TL;DR pipeline + Qdrant ingest)."""
    backend_url = os.environ.get("CORTEX_LLM_BACKEND_URL")
    if not backend_url:
        return Check(
            "LLM backend (ollama)",
            SKIP,
            "CORTEX_LLM_BACKEND_URL not set",
            "Set in env if you run the TL;DR-AI pipeline locally",
        )
    tags_url = f"{backend_url.rstrip('/')}/api/tags"
    status, body = _http_get(tags_url, timeout=3.0)
    if status == -1:
        return Check(
            "LLM backend (ollama)",
            WARN,
            f"{tags_url} unreachable: {body}",
            "Check ollama service / tailscale route",
            data={"url": backend_url, "error": body},
        )
    if status >= 400:
        return Check("LLM backend (ollama)", FAIL, f"{tags_url} → {status}", data={"status": status})
    try:
        payload = json.loads(body) if body else {}
        models = [m.get("name", "") for m in payload.get("models", [])]
    except (json.JSONDecodeError, AttributeError):
        return Check("LLM backend (ollama)", WARN, f"{tags_url} → {status} but malformed JSON")
    has_embed = any("embed" in m.lower() for m in models)
    has_chat = any("embed" not in m.lower() for m in models if m)
    if has_embed and has_chat:
        return Check(
            "LLM backend (ollama)",
            PASS,
            f"{len(models)} models loaded (embedder + chat present)",
            data={"url": backend_url, "models": models},
        )
    missing = []
    if not has_embed:
        missing.append("embedder")
    if not has_chat:
        missing.append("chat model")
    return Check(
        "LLM backend (ollama)",
        WARN,
        f"reachable but missing: {', '.join(missing)}",
        "ollama pull qwen3-embedding:0.6b  (or matching embedder for your stack)",
        data={"url": backend_url, "models": models, "missing": missing},
    )


# ─── Sibling projects (extension + outreach) ───────────────────────────


def _sibling_project_root(name: str) -> Path | None:
    """Locate a sibling empirica-* project relative to cwd's parent or ~/empirical-ai."""
    cwd = Path.cwd()
    candidates = [cwd.parent / name, Path.home() / "empirical-ai" / name]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def check_extension() -> Check:
    """empirica-extension presence + build state (Chrome / Desktop AI-mesh extension)."""
    root = _sibling_project_root("empirica-extension")
    if not root:
        return Check(
            "Empirica extension build",
            SKIP,
            "empirica-extension not found locally",
            "Clone if you want the AI-mesh extension",
        )
    dist = root / "dist"
    manifest = dist / "manifest.json"
    if not manifest.exists():
        return Check(
            "Empirica extension build",
            WARN,
            "dist/manifest.json missing — not built",
            f"cd {root} && npm install && npm run build",
            data={"root": str(root), "built": False},
        )
    try:
        m = json.loads(manifest.read_text())
        version = m.get("version", "?")
    except (json.JSONDecodeError, OSError):
        version = "?"
    return Check(
        "Empirica extension build",
        PASS,
        f"built (v{version})",
        data={"root": str(root), "version": version, "built": True},
    )


def check_outreach() -> Check:
    """empirica-outreach project presence + dependencies installed.

    Accepts either Python (pyproject.toml) or Node (package.json) shape —
    the project has shipped in both over its history. Refinement from
    cortex AI (prop_vvn45fwkfzcyldo2nk2cqrrr6e) after my completion note
    on the original doctor patch flagged a Node-shape false-positive
    when the local clone is Python.
    """
    root = _sibling_project_root("empirica-outreach")
    if not root:
        return Check("Outreach project", SKIP, "empirica-outreach not found locally")
    has_python = (root / "pyproject.toml").exists()
    has_node = (root / "package.json").exists()
    if not (has_python or has_node):
        return Check(
            "Outreach project", WARN, "neither pyproject.toml nor package.json found", data={"root": str(root)}
        )
    shape = "python" if has_python else "node"
    if has_python:
        # Python deps probe: .venv dir OR any *.egg-info (covers both venv +
        # `pip install -e .` patterns).
        deps_installed = (root / ".venv").exists() or any(root.glob("*.egg-info"))
        hint_cmd = f"cd {root} && pip install -e ."
    else:
        deps_installed = (root / "node_modules").exists()
        hint_cmd = f"cd {root} && npm install"
    project_yaml = root / ".empirica" / "project.yaml"
    if not deps_installed:
        return Check(
            "Outreach project",
            WARN,
            f"{shape} project — deps not installed",
            hint_cmd,
            data={"root": str(root), "shape": shape, "deps_installed": False},
        )
    detail = f"{shape} deps installed" + (" + project.yaml" if project_yaml.exists() else "")
    return Check(
        "Outreach project",
        PASS,
        detail,
        data={"root": str(root), "shape": shape, "deps_installed": True, "has_project_yaml": project_yaml.exists()},
    )


# ─── Project drift (local project.yaml vs Cortex membership) ───────────


def check_project_drift(cwd: Path | None = None) -> Check:
    """Local project_id should appear in /v1/users/me/projects (Cortex tenant scope)."""
    cwd = cwd or Path.cwd()
    project_yaml = cwd / ".empirica" / "project.yaml"
    if not project_yaml.exists():
        return Check("Project drift (Cortex membership)", SKIP, "no .empirica/project.yaml here")
    try:
        import yaml

        cfg = yaml.safe_load(project_yaml.read_text()) or {}
        local_pid = cfg.get("project_id")
    except Exception as e:
        return Check("Project drift (Cortex membership)", WARN, f"project.yaml unreadable: {e}")
    if not local_pid:
        return Check("Project drift (Cortex membership)", SKIP, "no project_id in project.yaml")
    url, api_key = _resolve_cortex_creds()
    if not (url and api_key):
        return Check("Project drift (Cortex membership)", SKIP, "no Cortex creds")
    me_url = f"{url}/v1/users/me/projects"
    status, body = _http_get(me_url, headers={"Authorization": f"Bearer {api_key}"})
    if status == -1:
        return Check("Project drift (Cortex membership)", WARN, f"{me_url} unreachable")
    if status >= 400:
        return Check("Project drift (Cortex membership)", WARN, f"{me_url} → {status}", data={"status": status})
    try:
        payload = json.loads(body) if body else {}
        projects = payload.get("projects", []) if isinstance(payload, dict) else (payload or [])
        # Cortex /v1/users/me/projects returns each project keyed by `id`,
        # not `project_id`. Accept both for compatibility with future shape changes.
        ids = {p.get("id") or p.get("project_id") for p in projects if isinstance(p, dict)}
        ids.discard(None)
    except (json.JSONDecodeError, AttributeError):
        return Check("Project drift (Cortex membership)", WARN, "malformed projects payload")
    if local_pid in ids:
        return Check(
            "Project drift (Cortex membership)",
            PASS,
            f"local project_id {local_pid[:8]}… present in Cortex user-scope ({len(ids)} total)",
            data={"project_id": local_pid, "scope_size": len(ids)},
        )
    return Check(
        "Project drift (Cortex membership)",
        WARN,
        f"local project_id {local_pid[:8]}… NOT in user.project_ids ({len(ids)} known)",
        'POST /v1/users/me/projects body={"project_id": "..."} to auto-link',
        data={"local_project_id": local_pid, "remote_ids": list(ids)},
    )


# ─── Top-level orchestrator ────────────────────────────────────────────


def run_all_checks(cwd: Path | None = None) -> list[Check]:
    """Run every check in dependency order.

    Check ordering matters: install presence checks run first; downstream
    checks SKIP themselves when their dependency fails.
    """
    return [
        # Install presence
        check_python(),
        check_empirica_cli(),
        check_empirica_mcp(),
        check_claude_code_cli(),
        check_git_present(),
        check_noetic_tools(),
        # Project state
        check_empirica_folder(cwd),
        check_project_yaml(cwd),
        check_sessions_db(cwd),
        check_git_remote(cwd),
        check_sync_state(cwd),
        # Cortex connectivity
        check_cortex_creds(),
        check_cortex_reachability(),
        check_cortex_auth(),
        check_project_drift(cwd),
        # ntfy mesh
        check_ntfy_creds(),
        check_ntfy_auth(),
        # Tailscale + LLM backend (optional infrastructure)
        check_tailscale(),
        check_ollama_backend(),
        # Sibling projects (mesh-adjacent)
        check_extension(),
        check_outreach(),
        # Listener / loops + MCP config
        check_loops_registered(),
        check_listener_service(cwd),
        check_mcp_config(),
    ]


# ─── Output formatting ─────────────────────────────────────────────────


def _format_human(checks: list[Check]) -> str:
    icons = {
        PASS: "\033[32m✓\033[0m",
        FAIL: "\033[31m✗\033[0m",
        WARN: "\033[33m⚠\033[0m",
        SKIP: "\033[90m⊘\033[0m",
    }
    lines = ["", "\033[1mEmpirica Doctor\033[0m", "=" * 60]
    for c in checks:
        icon = icons.get(c.status, "?")
        lines.append(f"{icon} {c.name}: {c.detail}")
        if c.hint and c.status != PASS:
            lines.append(f"    \033[90m→ {c.hint}\033[0m")
    fails = sum(1 for c in checks if c.status == FAIL)
    warns = sum(1 for c in checks if c.status == WARN)
    skips = sum(1 for c in checks if c.status == SKIP)
    passed = len(checks) - fails - warns - skips
    summary = f"\n{len(checks)} checks: {passed} pass, {warns} warn, {fails} fail, {skips} skipped"
    lines.append(summary)
    return "\n".join(lines)


def handle_doctor_command(args: Any) -> int:
    cwd = Path.cwd()
    checks = run_all_checks(cwd)
    fails = sum(1 for c in checks if c.status == FAIL)
    warns = sum(1 for c in checks if c.status == WARN)
    skips = sum(1 for c in checks if c.status == SKIP)
    passed = len(checks) - fails - warns - skips
    output_format = getattr(args, "output", "json")
    if output_format == "human":
        print(_format_human(checks))
    else:
        payload = {
            "ok": fails == 0,
            "summary": {"total": len(checks), "pass": passed, "warn": warns, "fail": fails, "skip": skips},
            "checks": [asdict(c) for c in checks],
            "cwd": str(cwd),
        }
        print(json.dumps(payload, indent=2))
    if fails:
        return 1
    if warns and getattr(args, "strict_warn", False):
        return 2
    return 0
