"""Handler for ``empirica provision-practice <name>``.

Local-only, idempotent single-practitioner onboarding. The lightweight
sibling of mesh-support's spec.yaml-driven bulk ``provision-practices.py``
(function ``provision_one``) — same step sequence (mkdir, project-init,
patch project.yaml, project-register, optional forgejo backup wiring)
without the spec.yaml, bulk, or remote-SSH machinery. Shells out to
``empirica project-init``/``project-register`` rather than calling their
handlers in-process, since those handlers resolve state from ``os.getcwd()``
via module-level caches — a subprocess with ``cwd`` set is simpler and
safer than faking that global state.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


def _run(cmd: list[str], cwd: Path, dry_run: bool) -> tuple[bool, str]:
    """Run a subprocess in ``cwd``. Returns (ok, stdout_or_error)."""
    display = " ".join(shlex.quote(c) for c in cmd)
    if dry_run:
        print(f"  [dry-run] would run: {display}")
        return True, ""
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    return True, result.stdout


def _infer_tenant_org(cwd: Path) -> tuple[str | None, str | None]:
    """Best-effort: read tenant/org off the CURRENT directory's
    project.yaml, if it's an already-provisioned practice. Not a global
    config lookup — deliberately scoped to "provision a sibling of the
    practice I'm standing in", since tenant/org aren't modeled by
    ProjectConfig and have no other canonical source in core."""
    project_yaml = cwd / ".empirica" / "project.yaml"
    if not yaml or not project_yaml.exists():
        return None, None
    try:
        data = yaml.safe_load(project_yaml.read_text()) or {}
    except Exception:
        return None, None
    return data.get("tenant"), data.get("org")


def _patch_project_yaml(project_yaml: Path, ai_id: str, tenant: str, org: str, substrate: str, dry_run: bool) -> bool:
    """Write ai_id/tenant/org/substrate into project.yaml. Returns True if
    a change was made (False = already matched, clean no-op).

    Raw yaml read/write rather than ProjectConfig — that dataclass
    doesn't model tenant/org/substrate and would silently drop them on
    a round-trip (confirmed: empirica/config/project_config_loader.py).
    """
    if not yaml:
        raise RuntimeError("pyyaml required")
    data = yaml.safe_load(project_yaml.read_text()) or {}
    desired = {"ai_id": ai_id, "tenant": tenant, "org": org, "substrate": substrate}
    if all(data.get(k) == v for k, v in desired.items()):
        return False
    if dry_run:
        print(f"  [dry-run] would patch {project_yaml}: {desired}")
        return True
    data.update(desired)
    project_yaml.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False))
    return True


def _wire_forgejo(proj_dir: Path, name: str, owner: str, host: str, dry_run: bool) -> tuple[bool, str | None]:
    """Add a forgejo backup remote + wire empirica sync to it. Idempotent:
    skips ``remote add`` if the remote already exists. Mirrors
    mesh-support's provision_one() step 7 (manual SSH-keyed remote, not
    the cortex-managed token-minting flow behind ``forgejo-publish``)."""
    remote_url = f"{host}/{owner}/{name}.git"
    check = subprocess.run(["git", "remote", "get-url", "forgejo"], cwd=proj_dir, capture_output=True, text=True)
    if check.returncode != 0:
        ok, err = _run(["git", "remote", "add", "forgejo", remote_url], proj_dir, dry_run)
        if not ok:
            return False, err
    ok, err = _run(["empirica", "sync-config", "notes_remote", "forgejo"], proj_dir, dry_run)
    if not ok:
        return False, err
    ok, err = _run(["empirica", "sync-config", "enabled", "true"], proj_dir, dry_run)
    if not ok:
        return False, err
    _run(["empirica", "sync-push", "--remote", "forgejo"], proj_dir, dry_run)
    return True, None


def handle_provision_practice_command(args) -> int:
    """``empirica provision-practice <name> [options]``.

    Idempotent — re-running is a clean no-op past whatever step already
    landed: dir exists -> skip mkdir, project.yaml exists -> skip
    project-init, values already match -> skip yaml patch, forgejo
    remote exists -> skip remote add (sync-config/push still run, both
    safe to repeat).
    """
    output = getattr(args, "output", "human")
    dry_run = bool(getattr(args, "dry_run", False))
    name = args.name

    if yaml is None:
        msg = "pyyaml required (pip install pyyaml)"
        print(json.dumps({"ok": False, "error": msg}) if output == "json" else f"❌ {msg}", file=sys.stderr)
        return 1

    base_path = Path(getattr(args, "base_path", None) or "~/empirical-ai").expanduser()
    proj_dir = base_path / name

    cwd = Path.cwd()
    inferred_tenant, inferred_org = _infer_tenant_org(cwd)
    tenant = getattr(args, "tenant", None) or inferred_tenant
    org = getattr(args, "org", None) or inferred_org
    substrate = getattr(args, "substrate", None) or "cortex"

    if not tenant or not org:
        msg = (
            "--tenant/--org not given and couldn't be inferred from the current "
            "directory's .empirica/project.yaml — pass them explicitly, or run "
            "this from inside an existing practice to inherit its tenant/org."
        )
        print(json.dumps({"ok": False, "error": msg}) if output == "json" else f"❌ {msg}", file=sys.stderr)
        return 1

    steps: list[dict] = []

    dir_existed = proj_dir.exists()
    if not dir_existed:
        if not dry_run:
            proj_dir.mkdir(parents=True)
        steps.append({"step": "mkdir", "changed": True})
    else:
        steps.append({"step": "mkdir", "changed": False, "note": "already exists"})

    project_yaml = proj_dir / ".empirica" / "project.yaml"
    if not project_yaml.exists():
        ok, err = _run(["empirica", "project-init", "--output", "json"], proj_dir, dry_run)
        if not ok:
            msg = f"project-init failed: {err}"
            print(json.dumps({"ok": False, "error": msg, "steps": steps}) if output == "json" else f"❌ {msg}")
            return 1
        steps.append({"step": "project-init", "changed": True})
    else:
        steps.append({"step": "project-init", "changed": False, "note": "already initialized"})

    if dry_run and not project_yaml.exists():
        steps.append({"step": "patch-project-yaml", "changed": True, "note": "dry-run, file not yet created"})
    else:
        changed = _patch_project_yaml(
            project_yaml, ai_id=name, tenant=tenant, org=org, substrate=substrate, dry_run=dry_run
        )
        steps.append({"step": "patch-project-yaml", "changed": changed})

    register_cmd = ["empirica", "project-register", ".", "--output", "json"]
    if getattr(args, "no_cortex", False):
        register_cmd.append("--no-cortex")
    ok, err = _run(register_cmd, proj_dir, dry_run)
    steps.append({"step": "project-register", "changed": ok, "error": None if ok else err})

    forgejo_owner = getattr(args, "forgejo_owner", None)
    if forgejo_owner:
        forgejo_host = getattr(args, "forgejo_host", None)
        if not forgejo_host:
            steps.append(
                {
                    "step": "forgejo-backup-wired",
                    "changed": False,
                    "error": "--forgejo-owner set but no host: pass --forgejo-host "
                    "ssh://git@HOST:PORT or set EMPIRICA_FORGEJO_HOST",
                }
            )
        else:
            ok, err = _wire_forgejo(proj_dir, name, forgejo_owner, forgejo_host, dry_run)
            steps.append({"step": "forgejo-backup-wired", "changed": ok, "error": err})

    any_error = any(s.get("error") for s in steps)
    payload = {
        "ok": not any_error,
        "name": name,
        "proj_dir": str(proj_dir),
        "tenant": tenant,
        "org": org,
        "substrate": substrate,
        "dry_run": dry_run,
        "steps": steps,
    }

    if output == "json":
        print(json.dumps(payload, indent=2))
        return 0 if not any_error else 1

    verb = "dry-run" if dry_run else "provisioned"
    print(f"{'❌' if any_error else '✅'} {name} {verb} at {proj_dir}")
    for s in steps:
        marker = "✗" if s.get("error") else ("·" if not s["changed"] else "✓")
        note = f" ({s['note']})" if s.get("note") else ""
        line = f"  {marker} {s['step']}{note}"
        if s.get("error"):
            line += f"  ⚠ {s['error']}"
        print(line)
    return 0 if not any_error else 1
