"""Module install executors — the plugin-layer side of the two-layer install.

``fetch_module`` is the **auth-gated artifact pre-step** (leg 2): it stages a
module's distribution artifacts so the plugin layer can place them. The seat
layer (autonomy's ``install_seat.py``) runs between fetch and provision; it
never fetches — that's this module's job, keeping install_seat single-purpose.

Design invariants (from the converged module SER):
- **Write-by-default, ``--dry-run`` previews** — mirrors install_seat's contract,
  so the front-door can dry-run the whole ``fetch → seat → provision`` chain.
- **Idempotent** — re-fetching an already-staged/installed artifact is a no-op.
- **Per-step receipt** — every artifact reports its own status so a bulk
  front-door can see exactly which step needs attention.
- **Reference-only secrets** — a ``secrets_ref`` is resolved via the
  secrets-manager at runtime; a raw key never reaches this code (the manifest
  validator already rejects one at the schema layer).

The registry is config-driven (env), so this carries no fake infrastructure: a
``plugin_archive`` resolves from a local path (dev/test) or a configured
registry base URL; absent both, the step reports ``unconfigured`` rather than
crashing.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from empirica.core.modules.manifest import ModuleManifest

STAGING_ROOT = Path.home() / ".empirica" / "module_staging"
CLAUDE_PLUGIN_ROOT = Path.home() / ".claude" / "plugins" / "local"
REGISTRY_ENV = "EMPIRICA_MODULE_REGISTRY"  # base URL for plugin-archive fetch
INDEX_ENV = "EMPIRICA_MODULE_INDEX_URL"  # auth-gated pip index for python_packages

# manifest automation.kind → empirica loop-registry kind (the registry has no
# "listener" kind; a persistent stream-watcher IS a monitor-kind loop).
_AUTOMATION_KIND_MAP = {"listener": "monitor", "interval": "interval", "cron": "cron"}


def _resolve_secret_ref(ref: str | None) -> tuple[str | None, str]:
    """Resolve a reference-only secret to its value (never a raw key in source).

    Returns ``(value, status)``. ``env:VAR`` reads the environment directly.
    ``<scheme>://...`` is resolved by the manager's CLI if present on PATH
    (``doppler``/``op``/``vault``), else reported ``manager_cli_absent`` — the
    caller degrades gracefully rather than failing the whole fetch.
    """
    if not ref:
        return None, "none"
    if ref.startswith("env:"):
        var = ref[len("env:") :]
        val = os.environ.get(var)
        return (val, "resolved") if val else (None, "env_unset")
    scheme = ref.split("://", 1)[0] if "://" in ref else ""
    if scheme and shutil.which(scheme):
        try:
            out = subprocess.run(
                [scheme, "secrets", "get", ref] if scheme == "doppler" else [scheme, "read", ref],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
            val = out.stdout.strip()
            return (val, "resolved") if val else (None, "manager_empty")
        except (subprocess.SubprocessError, OSError):
            return None, "manager_error"
    return None, "manager_cli_absent"


def _pkg_installed(spec: str) -> bool:
    """True if the (possibly ``name==ver``/``name>=ver``) package is importable."""
    name = spec
    for sep in ("==", ">=", "<=", "~=", ">", "<", "!=", "["):
        if sep in name:
            name = name.split(sep, 1)[0]
    name = name.strip()
    try:
        metadata.version(name)
        return True
    except metadata.PackageNotFoundError:
        return False


def _scrub_secret(text: str, secret: str | None) -> str:
    """Redact a resolved secret from any text that may be surfaced (receipts,
    error details). A bearer must never leak into output."""
    return text.replace(secret, "***") if secret and secret in text else text


def _pip_install(spec: str, index_url: str | None, bearer: str | None = None) -> tuple[bool, str]:
    """Install one package via the current interpreter's pip. Returns (ok, detail).

    For a ``git+https://github.com/`` spec with a resolved bearer, the token is
    injected into the URL passed to pip ONLY — never into the returned detail or
    the caller's receipt ``target`` — so a proprietary git package authenticates
    with the same PAT that gates the plugin archive, no ``~/.netrc`` ceremony.
    Falls back to the bearer-less invocation (and the operator's own git creds)
    when no bearer is resolved.
    """
    install_spec = spec
    if bearer and spec.startswith("git+https://github.com/"):
        install_spec = spec.replace("git+https://github.com/", f"git+https://{bearer}@github.com/", 1)
    cmd = [sys.executable, "-m", "pip", "install", install_spec]
    if index_url:
        cmd += ["--index-url", index_url]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=True)
        return True, "installed"
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "pip failed").strip().splitlines()[-1][:300]
        return False, _scrub_secret(detail, bearer)
    except (subprocess.SubprocessError, OSError) as e:
        return False, _scrub_secret(str(e)[:300], bearer)


def _fetch_archive(archive: str, dest: Path, registry_base: str | None, bearer: str | None) -> tuple[bool, str]:
    """Stage ``archive`` to ``dest``. Local path → copy; else registry+bearer → download."""
    local = Path(archive)
    if local.exists():
        shutil.copy2(local, dest)
        return True, "copied_local"
    if not registry_base:
        return False, "no_registry_configured"
    url = registry_base.rstrip("/") + "/" + archive
    req = urllib.request.Request(url)
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
            shutil.copyfileobj(resp, fh)
        return True, f"downloaded:{url}"
    except (urllib.error.URLError, OSError) as e:
        return False, str(e)[:300]


def fetch_module(
    manifest: ModuleManifest,
    *,
    dry_run: bool = False,
    staging_root: Path | None = None,
    registry_base: str | None = None,
    index_url: str | None = None,
) -> dict:
    """Stage a module's distribution artifacts (python_packages + plugin_archive).

    Returns a receipt: ``{ok, action, module, dry_run, staged_path, steps, errors}``
    where each step carries its own ``status`` (``would_*`` under dry-run).
    """
    root = staging_root or STAGING_ROOT
    registry_base = registry_base if registry_base is not None else os.environ.get(REGISTRY_ENV)
    index_url = index_url if index_url is not None else os.environ.get(INDEX_ENV)

    staged = root / manifest.name
    steps: list[dict] = []
    errors: list[str] = []

    if not dry_run:
        staged.mkdir(parents=True, exist_ok=True)

    # Resolve the secrets_ref bearer ONCE — the same token gates both proprietary
    # python_packages (git+https) and the plugin_archive download. Only resolve
    # when writing (dry-run never touches the secrets manager) and a ref exists.
    bearer, sref_status = (None, "none")
    if not dry_run and manifest.requires_runtime.secrets_ref:
        bearer, sref_status = _resolve_secret_ref(manifest.requires_runtime.secrets_ref)

    # python_packages → pip (idempotent: skip if already importable)
    for spec in manifest.artifacts.python_packages:
        if _pkg_installed(spec):
            steps.append({"kind": "python_package", "target": spec, "status": "skipped", "detail": "already installed"})
            continue
        if dry_run:
            steps.append({"kind": "python_package", "target": spec, "status": "would_install", "detail": "pip install"})
            continue
        ok, detail = _pip_install(spec, index_url, bearer)
        steps.append(
            {"kind": "python_package", "target": spec, "status": "installed" if ok else "error", "detail": detail}
        )
        if not ok:
            errors.append(f"python_package {spec}: {detail}")

    # plugin_archive → local copy or registry download (idempotent: skip if staged)
    archive = manifest.artifacts.plugin_archive
    if archive:
        dest = staged / Path(archive).name
        if dest.exists():
            steps.append({"kind": "plugin_archive", "target": archive, "status": "skipped", "detail": "already staged"})
        elif dry_run:
            src = "local" if Path(archive).exists() else (f"{registry_base}" if registry_base else "unconfigured")
            status = "unconfigured" if src == "unconfigured" else "would_fetch"
            steps.append({"kind": "plugin_archive", "target": archive, "status": status, "detail": src})
        else:
            need_remote = not Path(archive).exists()
            if need_remote and not registry_base:
                steps.append(
                    {"kind": "plugin_archive", "target": archive, "status": "unconfigured", "detail": "no registry"}
                )
            elif need_remote and manifest.requires_runtime.secrets_ref and bearer is None:
                steps.append(
                    {
                        "kind": "plugin_archive",
                        "target": archive,
                        "status": "unresolved_secret",
                        "detail": sref_status,
                    }
                )
                errors.append(f"plugin_archive {archive}: secret {sref_status}")
            else:
                ok, detail = _fetch_archive(archive, dest, registry_base, bearer)
                steps.append(
                    {
                        "kind": "plugin_archive",
                        "target": archive,
                        "status": "fetched" if ok else "error",
                        "detail": detail,
                    }
                )
                if not ok:
                    errors.append(f"plugin_archive {archive}: {detail}")

    return {
        "ok": not errors,
        "action": "fetch",
        "module": manifest.name,
        "dry_run": dry_run,
        "staged_path": str(staged),
        "steps": steps,
        "errors": errors,
    }


# ── provision (leg 3): the plugin layer ─────────────────────────────────────


def _place_plugin_artifact(
    manifest: ModuleManifest, staging_root: Path | None, plugin_root: Path | None, dry_run: bool
) -> dict:
    """Place the staged plugin archive into ~/.claude/plugins/local/<name>/."""
    archive = manifest.artifacts.plugin_archive
    dest = (plugin_root or CLAUDE_PLUGIN_ROOT) / manifest.name
    if not archive:
        return {
            "kind": "plugin_files",
            "target": manifest.name,
            "status": "no_artifact",
            "detail": "no plugin_archive declared",
        }
    if dest.exists() and any(dest.iterdir()):
        return {"kind": "plugin_files", "target": str(dest), "status": "skipped", "detail": "already populated"}
    staged = (staging_root or STAGING_ROOT) / manifest.name / Path(archive).name
    if not staged.exists():
        return {"kind": "plugin_files", "target": archive, "status": "not_staged", "detail": "run `module fetch` first"}
    if dry_run:
        return {
            "kind": "plugin_files",
            "target": str(dest),
            "status": "would_place",
            "detail": f"extract {staged.name}",
        }
    try:
        dest.mkdir(parents=True, exist_ok=True)
        if tarfile.is_tarfile(staged):
            with tarfile.open(staged) as tf:
                tf.extractall(dest, filter="data")  # filter=data → safe extraction (py3.11.4+)
        else:
            shutil.copy2(staged, dest / staged.name)
        return {"kind": "plugin_files", "target": str(dest), "status": "placed", "detail": staged.name}
    except (OSError, tarfile.TarError) as e:
        return {"kind": "plugin_files", "target": archive, "status": "error", "detail": str(e)[:300]}


def _ensure_plugin_manifest(dest: Path, manifest: ModuleManifest) -> bool:
    """Ensure ``<dest>/.claude-plugin/plugin.json`` exists so Claude Code
    recognizes the module as a plugin. Generate a minimal one from the module
    manifest when the archive didn't ship it. Returns True if a file was written."""
    pj = dest / ".claude-plugin" / "plugin.json"
    if pj.exists():
        return False
    pj.parent.mkdir(parents=True, exist_ok=True)
    pj.write_text(
        json.dumps(
            {"name": manifest.name, "version": manifest.version, "description": f"{manifest.name} — empirica module"},
            indent=2,
        )
    )
    return True


def _register_plugin(manifest: ModuleManifest, plugin_root: Path | None, dry_run: bool) -> dict:
    """Register the module as a ``<name>@local`` Claude Code plugin (Model B).

    Writes an entry into ``~/.claude/plugins/installed_plugins.json`` so CC
    discovers the module's ``skills``/``agents`` from its own plugin dir — clean
    separation from the empirica plugin, clean uninstall. Idempotent: an existing
    entry for the same installPath is a no-op. ``.claude-plugin/plugin.json`` is
    generated from the manifest if the archive didn't include one.

    NOTE: ``hooks`` discovery for local plugins additionally needs a
    ``~/.claude/settings.json`` entry (Claude Code's local-plugin hook
    auto-discovery is unreliable) — a separate follow-up. Registration covers
    skills + agents, the common module shape.
    """
    root = plugin_root or CLAUDE_PLUGIN_ROOT
    dest = root / manifest.name
    key = f"{manifest.name}@local"
    registry = root.parent / "installed_plugins.json"
    if dry_run:
        # The would-place step (above) hasn't extracted yet, so dest may not
        # exist — report the plan, not not_placed.
        return {"kind": "plugin_register", "target": key, "status": "would_register", "detail": str(registry)}
    if not dest.exists():
        return {"kind": "plugin_register", "target": key, "status": "not_placed", "detail": "no placed plugin dir"}
    try:
        data: dict = json.loads(registry.read_text()) if registry.exists() else {"version": 2, "plugins": {}}
        plugins = data.setdefault("plugins", {})
        if any(e.get("installPath") == str(dest) for e in plugins.get(key, [])):
            return {"kind": "plugin_register", "target": key, "status": "skipped", "detail": "already registered"}
        generated = _ensure_plugin_manifest(dest, manifest)
        now = datetime.now(timezone.utc).isoformat()
        plugins[key] = [
            {
                "scope": "user",
                "installPath": str(dest),
                "version": manifest.version,
                "installedAt": now,
                "lastUpdated": now,
                "isLocal": True,
            }
        ]
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps(data, indent=2))
        return {
            "kind": "plugin_register",
            "target": key,
            "status": "registered",
            "detail": "registered" + ("; generated plugin.json" if generated else ""),
        }
    except (OSError, json.JSONDecodeError) as e:
        return {"kind": "plugin_register", "target": key, "status": "error", "detail": str(e)[:300]}


def _register_automation(auto, dry_run: bool) -> dict:
    """Register one automation via the canonical ``empirica loop register`` (idempotent)."""
    kind = _AUTOMATION_KIND_MAP.get(auto.kind, auto.kind)
    cmd = ["empirica", "loop", "register", "--name", auto.name, "--kind", kind]
    if auto.kind == "interval" and auto.interval:
        cmd += ["--interval", auto.interval]
    elif auto.kind == "cron" and auto.cron:
        cmd += ["--cron", auto.cron]
    # loop register has no --command: it tracks the schedulable entry. A listener's
    # command-as-service supervision (systemd autostart/restart) is the front-door's
    # `empirica loop enable` step, not register's job.
    note = " (command-supervision via `loop enable`)" if auto.kind == "listener" else ""
    if dry_run:
        return {"kind": "automation", "target": auto.name, "status": "would_register", "detail": " ".join(cmd) + note}
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True)
        return {"kind": "automation", "target": auto.name, "status": "registered", "detail": f"kind={kind}{note}"}
    except subprocess.CalledProcessError as e:
        return {
            "kind": "automation",
            "target": auto.name,
            "status": "error",
            "detail": (e.stderr or e.stdout or "register failed").strip()[:300],
        }
    except (subprocess.SubprocessError, OSError) as e:
        return {"kind": "automation", "target": auto.name, "status": "error", "detail": str(e)[:300]}


def _post_grants(cortex_url: str, api_key: str, grants: list[dict]) -> tuple[bool, str]:
    """POST ntfy ACL grants to cortex admin (the contract cortex shipped)."""
    url = cortex_url.rstrip("/") + "/v1/admin/ntfy/grants"
    body = json.dumps({"grants": grants}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return (resp.status in (200, 207), f"http {resp.status}")
    except urllib.error.HTTPError as e:
        return False, f"http {e.code}"
    except (urllib.error.URLError, OSError) as e:
        return False, str(e)[:300]


def _grant_topics(manifest, cortex_url, cortex_api_key, org, tenant, dry_run) -> list[dict]:
    """Register + grant each declared ntfy topic via the cortex admin ACL endpoint."""
    topics = manifest.requires_runtime.topics
    if not topics:
        return []
    if not (cortex_url and cortex_api_key and org):
        return [
            {"kind": "topic", "target": t, "status": "unconfigured", "detail": "needs cortex url/api_key + --org"}
            for t in topics
        ]
    publisher = f"{org}-cortex-publisher"
    subscriber = f"{org}-u-{tenant}" if tenant else f"{org}-{manifest.name}-subscriber"
    out = []
    for t in topics:
        grants = [
            {"user": publisher, "topic": t, "permission": "rw"},
            {"user": subscriber, "topic": t, "permission": "read-only"},
        ]
        if dry_run:
            out.append({"kind": "topic", "target": t, "status": "would_grant", "detail": json.dumps(grants)})
            continue
        ok, detail = _post_grants(cortex_url, cortex_api_key, grants)
        out.append({"kind": "topic", "target": t, "status": "granted" if ok else "error", "detail": detail})
    return out


def _check_env(manifest) -> list[dict]:
    """Presence-check each required env var. The value is never read (reference-only)."""
    return [
        {"kind": "env", "target": var, "status": "present" if var in os.environ else "missing", "detail": ""}
        for var in manifest.requires_runtime.env
    ]


def _register_practice_domains(manifest: ModuleManifest, dry_run: bool) -> list[dict]:
    """Register the module's practice in each declared engagement domain
    (``provides.domains`` → ``practice_domains``). The practice is the module's
    canonical seat (``seat_name``). Idempotent via ``join_practice_domain``;
    unknown domain ids are reported as error steps (the engagement substrate
    validates them). No-op when ``provides.domains`` is empty.
    """
    domains = manifest.provides.domains
    if not domains:
        return []
    practice_id = manifest.seat_name
    if dry_run:
        return [
            {
                "kind": "practice_domain",
                "target": d,
                "status": "dry_run",
                "detail": f"would join practice {practice_id!r} to domain {d!r}",
            }
            for d in domains
        ]
    steps: list[dict] = []
    try:
        from empirica.data.repositories.workspace_db import WorkspaceDBRepository

        with WorkspaceDBRepository.open() as repo:
            for d in domains:
                try:
                    repo.join_practice_domain(practice_id, d)
                    steps.append(
                        {
                            "kind": "practice_domain",
                            "target": d,
                            "status": "ok",
                            "detail": f"practice {practice_id!r} joined domain {d!r}",
                        }
                    )
                except ValueError as ve:
                    steps.append({"kind": "practice_domain", "target": d, "status": "error", "detail": str(ve)})
    except Exception as e:  # workspace.db unavailable on this host
        steps.append({"kind": "practice_domain", "target": "*", "status": "error", "detail": f"workspace.db: {e}"})
    return steps


def provision_module(
    manifest: ModuleManifest,
    *,
    dry_run: bool = False,
    staging_root: Path | None = None,
    plugin_root: Path | None = None,
    cortex_url: str | None = None,
    cortex_api_key: str | None = None,
    org: str | None = None,
    tenant: str | None = None,
) -> dict:
    """Run the plugin layer: place files, register automations, grant topics, check env.

    Returns a receipt ``{ok, action, module, dry_run, steps, errors}``. A missing
    env var is reported (``missing``) but is NOT an error — the value resolves at
    runtime on the dispatching host, which may differ from the provisioning host.
    """
    if cortex_url is None or cortex_api_key is None:
        # credentials absent/unreadable → topics simply report unconfigured
        with contextlib.suppress(Exception):
            from empirica.config.credentials_loader import CredentialsLoader

            cfg = CredentialsLoader().get_cortex_config()
            cortex_url = cortex_url or cfg.get("url")
            cortex_api_key = cortex_api_key or cfg.get("api_key")

    steps: list[dict] = [_place_plugin_artifact(manifest, staging_root, plugin_root, dry_run)]
    if manifest.artifacts.plugin_archive:
        # Model B: register the placed dir as a <name>@local plugin so CC
        # discovers its skills/agents (clean separation, not folded into empirica).
        steps.append(_register_plugin(manifest, plugin_root, dry_run))
    steps += [_register_automation(a, dry_run) for a in manifest.provides.automations]
    steps += _grant_topics(manifest, cortex_url, cortex_api_key, org, tenant, dry_run)
    steps += _check_env(manifest)
    steps += _register_practice_domains(manifest, dry_run)

    errors = [f"{s['kind']} {s['target']}: {s['detail']}" for s in steps if s["status"] == "error"]
    return {
        "ok": not errors,
        "action": "provision",
        "module": manifest.name,
        "dry_run": dry_run,
        "steps": steps,
        "errors": errors,
    }
