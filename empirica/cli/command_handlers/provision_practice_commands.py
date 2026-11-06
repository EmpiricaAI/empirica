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
import os
import shlex
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


def _narrate(line: str) -> None:
    """Dry-run narration goes to stderr: with `--output json`, stdout must be exactly the JSON (ecodex could not parse it)."""
    print(line, file=sys.stderr)


def _run(cmd: list[str], cwd: Path, dry_run: bool) -> tuple[bool, str]:
    """Run a subprocess in ``cwd``. Returns (ok, stdout_or_error)."""
    display = " ".join(shlex.quote(c) for c in cmd)
    if dry_run:
        _narrate(f"  [dry-run] would run: {display}")
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
    # `tenant_slug`/`org_id` FIRST — that is the canonical spelling, written by
    # `setup` and read by ~24 other modules. Reading only the bare `tenant`/`org`
    # keys meant the inference never fired on a real practice: this repo's own
    # project.yaml carries `tenant_slug: david` / `org_id: org-empirica`, and
    # running `provision-practice --dry-run` from inside it still failed with
    # "couldn't be inferred from the current directory's .empirica/project.yaml"
    # — the documented behaviour ("run this from inside an existing practice to
    # inherit its tenant/org") was false for every practice except one this
    # command had provisioned itself.
    #
    # The bare keys stay as a fallback so practices provisioned by the old code
    # path keep resolving.
    tenant = data.get("tenant_slug") or data.get("tenant")
    org = data.get("org_id") or data.get("org")
    return tenant, org


def _patch_project_yaml(
    project_yaml: Path, ai_id: str, tenant: str | None, org: str | None, substrate: str, dry_run: bool
) -> bool:
    """Write ai_id/tenant/org/substrate into project.yaml. Returns True if
    a change was made (False = already matched, clean no-op).

    Raw yaml read/write rather than ProjectConfig — that dataclass
    doesn't model tenant/org/substrate and would silently drop them on
    a round-trip (confirmed: empirica/config/project_config_loader.py).
    """
    if not yaml:
        raise RuntimeError("pyyaml required")
    data = yaml.safe_load(project_yaml.read_text()) or {}
    # Write the CANONICAL key names. Writing bare `tenant`/`org` is what made
    # this command the sole outlier in the repo — it minted practices whose
    # tenant/org were invisible to the ~24 modules that read `tenant_slug`/
    # `org_id`, including its own sibling inference. Two spellings for one fact
    # is the two-sources-of-truth class; there is now one.
    desired = {"ai_id": ai_id, "substrate": substrate}
    # A local practice has no tenant to name; writing None would read as "present" to code that checks the key.
    if tenant:
        desired["tenant_slug"] = tenant
    if org:
        desired["org_id"] = org
    if all(data.get(k) == v for k, v in desired.items()):
        return False
    if dry_run:
        _narrate(f"  [dry-run] would patch {project_yaml}: {desired}")
        return True
    data.update(desired)
    project_yaml.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False))
    return True


_GITIGNORE_RULES = (".empirica/*", "!.empirica/project.yaml")
_GITIGNORE_BLOCK = (
    "# Empirica keeps local state under .empirica/ (sessions database, transaction files).\n"
    "# Only the practice's identity is tracked, so a clone is the same practice.\n" + "\n".join(_GITIGNORE_RULES) + "\n"
)


def _gitignore_lines(proj_dir: Path) -> list[str]:
    try:
        return [line.strip() for line in (proj_dir / ".gitignore").read_text().splitlines()]
    except OSError:
        return []


def _gitignore_note(proj_dir: Path) -> str | None:
    """A line for the step when the existing .gitignore hides the whole directory, project.yaml included."""
    if any(line in (".empirica", ".empirica/", "/.empirica", "/.empirica/") for line in _gitignore_lines(proj_dir)):
        return (
            ".gitignore ignores all of .empirica, so project.yaml is not tracked and a clone will not keep this "
            "practice's identity; left as it is. To track it: replace that line with `.empirica/*` and add "
            "`!.empirica/project.yaml`"
        )
    return None


def _ensure_gitignore(proj_dir: Path, dry_run: bool) -> bool:
    """Ignore .empirica except project.yaml. Returns True when the file was (or, in a dry run, would be) written.

    Without it every file under .empirica shows as untracked, the sessions database included, and the one file a
    clone needs to stay the same practice is not singled out. An existing .gitignore keeps its lines; one that
    already ignores the whole directory is the person's choice and is not rewritten (`_gitignore_note` says so).
    """
    lines = _gitignore_lines(proj_dir)
    if all(rule in lines for rule in _GITIGNORE_RULES) or _gitignore_note(proj_dir):
        return False
    path = proj_dir / ".gitignore"
    if dry_run:
        _narrate(f"  [dry-run] would write {path}: {', '.join(_GITIGNORE_RULES)}")
        return True
    existing = path.read_text() if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + ("\n" if existing else "") + _GITIGNORE_BLOCK)
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


def _add_to_cockpit_profile(
    profile: str,
    name: str,
    proj_dir: Path,
    dry_run: bool,
    failed: bool,
    pane_launch: str | None = None,
    pane_resume: str | None = None,
) -> dict:
    """The ``cockpit-profile`` step. Skipped, and said so, when an earlier step failed:
    a profile listing a practice that never came up would launch an empty pane."""
    from empirica.core.cockpit.launcher.config import ProfileError, add_practice_to_profile, profile_path

    launch = f"empirica cockpit launch --profile {profile}"
    try:
        target = profile_path(profile)
    except ProfileError as exc:
        return {"step": "cockpit-profile", "changed": False, "error": str(exc)}
    if failed:
        return {
            "step": "cockpit-profile",
            "changed": False,
            "note": "skipped: an earlier step failed",
            "launch": launch,
        }
    if dry_run:
        return {"step": "cockpit-profile", "changed": True, "note": f"dry-run, would add to {target}", "launch": launch}
    try:
        target, changed = add_practice_to_profile(profile, name, str(proj_dir), launch=pane_launch, resume=pane_resume)
    except ProfileError as exc:
        return {"step": "cockpit-profile", "changed": False, "error": str(exc)}
    note = str(target) if changed else f"already in {target}"
    return {"step": "cockpit-profile", "changed": changed, "note": note, "launch": launch}


def _program(command: str | None) -> str:
    """The program a pane command runs: its first word, without a directory."""
    try:
        words = shlex.split(command or "claude")
    except ValueError:
        words = (command or "claude").split()
    return os.path.basename(words[0]) if words else "claude"


def _pane_flags_error(
    profile: str | None, launch: str | None, resume: str | None, second_pane: str | None
) -> str | None:
    """Why the pane flags cannot be used as given, or None. They describe cockpit panes, so they need a profile; and the
    second pane is a second harness, not the first one again."""
    if (launch is not None or resume is not None or second_pane is not None) and not profile:
        return (
            "--launch/--resume/--second-pane say what the cockpit panes run and need --cockpit-profile; "
            "without it they would be ignored."
        )
    if second_pane and _program(second_pane) == _program(launch):
        return (
            f"--second-pane {second_pane!r} runs the same program as the practice's own pane ({_program(launch)}); "
            "a second pane is for a second harness, such as ecodex beside claude"
        )
    return None


def _add_second_pane(profile: str, name: str, proj_dir: Path, command: str, dry_run: bool, failed: bool) -> dict:
    """The ``cockpit-second-pane`` step: ``command`` as a second pane of the practice's window, in the same directory, under
    its own name (``<name>-<program>``) so it keeps its own identity and its own conversation."""
    from empirica.core.cockpit.launcher.config import ProfileError, add_practice_to_profile

    pane_name = f"{name}-{_program(command)}"
    step: dict = {"step": "cockpit-second-pane", "pane": pane_name}
    if failed:
        return {**step, "changed": False, "note": "skipped: an earlier step failed"}
    if dry_run:
        return {**step, "changed": True, "note": f"dry-run, would add {pane_name!r} beside {name!r}"}
    try:
        target, changed = add_practice_to_profile(profile, pane_name, str(proj_dir), launch=command, beside=name)
    except ProfileError as exc:
        return {**step, "changed": False, "error": str(exc)}
    return {**step, "changed": changed, "note": str(target) if changed else f"already in {target}"}


def _cockpit_steps(
    profile: str,
    name: str,
    proj_dir: Path,
    dry_run: bool,
    steps: list[dict],
    launch: str | None,
    resume: str | None,
    second_pane: str | None,
) -> str | None:
    """Append the cockpit-profile step, and the second-pane step when asked for, to ``steps``. Returns the launch command."""
    step = _add_to_cockpit_profile(
        profile,
        name,
        proj_dir,
        dry_run,
        failed=any(s.get("error") for s in steps),
        pane_launch=launch,
        pane_resume=resume,
    )
    steps.append(step)
    if second_pane:
        steps.append(
            _add_second_pane(profile, name, proj_dir, second_pane, dry_run, failed=any(s.get("error") for s in steps))
        )
    return step.get("launch")


def _print_human(name: str, proj_dir: Path, steps: list[dict], dry_run: bool, launch_command, any_error: bool) -> None:
    verb = "dry-run" if dry_run else "provisioned"
    print(f"{'❌' if any_error else '✅'} {name} {verb} at {proj_dir}")
    for s in steps:
        marker = "✗" if s.get("error") else ("·" if not s["changed"] else "✓")
        note = f" ({s['note']})" if s.get("note") else ""
        line = f"  {marker} {s['step']}{note}"
        if s.get("error"):
            line += f"  ⚠ {s['error']}"
        print(line)
    if launch_command and not any_error and not dry_run:
        print(f"\nLaunch your practices: {launch_command}")


def _resolve_substrate(args) -> str:
    """The substrate to record: an explicit --substrate, else `local` for a --no-cortex provision, else `cortex`.

    The roster documents substrate as cortex | git | local. --no-cortex used to skip only the Cortex POST while the
    parser pre-filled `cortex`, so a standalone practice was written down as a Cortex one (ecodex-lab).
    """
    explicit = getattr(args, "substrate", None)
    if explicit:
        return explicit
    return "local" if getattr(args, "no_cortex", False) else "cortex"


def _init_step(args, proj_dir: Path, project_yaml: Path, dry_run: bool) -> tuple[dict, str | None]:
    """Run project-init for a new practice, with the type, description and domain when given. Returns (step, error)."""
    project_type = getattr(args, "type", None)
    description = getattr(args, "description", None)
    domain = getattr(args, "domain", None)
    if project_yaml.exists():
        note = "already initialized"
        if project_type or description or domain:
            # Said, not dropped: a value the person passed must not vanish without a word.
            note += (
                "; --type, --description and --domain apply only when the practice is created and were not applied. "
                "Set them with `empirica project-update --type` or `empirica project-update --domain` in that directory"
            )
        return {"step": "project-init", "changed": False, "note": note}, None
    init_cmd = ["empirica", "project-init", "--output", "json"]
    if project_type:
        init_cmd += ["--type", project_type]
    if description:
        init_cmd += ["--project-description", description]
    if domain:
        init_cmd += ["--domain", domain]
    ok, err = _run(init_cmd, proj_dir, dry_run)
    if not ok:
        return {}, f"project-init failed: {err}"
    return {"step": "project-init", "changed": True}, None


def _gitignore_step(proj_dir: Path, dry_run: bool) -> dict:
    step: dict = {"step": "gitignore", "changed": _ensure_gitignore(proj_dir, dry_run)}
    note = _gitignore_note(proj_dir)
    if note:
        step["note"] = note
    return step


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

    base_path = Path(getattr(args, "base_path", None) or "~/empirica").expanduser()
    proj_dir = base_path / name

    cwd = Path.cwd()
    inferred_tenant, inferred_org = _infer_tenant_org(cwd)
    tenant = getattr(args, "tenant", None) or inferred_tenant
    org = getattr(args, "org", None) or inferred_org
    substrate = _resolve_substrate(args)

    profile = getattr(args, "cockpit_profile", None)
    launch_flag = getattr(args, "launch", None)
    resume_flag = getattr(args, "resume", None)
    second_pane = getattr(args, "second_pane", None)
    pane_error = _pane_flags_error(profile, launch_flag, resume_flag, second_pane)
    if pane_error:
        print(
            json.dumps({"ok": False, "error": pane_error}) if output == "json" else f"❌ {pane_error}", file=sys.stderr
        )
        return 1

    # A local (--no-cortex) practice has no tenant or org to name: a first-time standalone user has neither (David, 2026-10-24).
    if (not tenant or not org) and substrate != "local":
        msg = (
            "--tenant/--org not given and couldn't be inferred from the current "
            "directory's .empirica/project.yaml — pass them explicitly, or run "
            "this from inside an existing practice to inherit its tenant/org."
        )
        print(json.dumps({"ok": False, "error": msg}) if output == "json" else f"❌ {msg}", file=sys.stderr)
        return 1

    # project-init refuses an unknown domain too, but only after the directory below exists: refuse it first.
    from empirica.config.domain_registry import domain_refusal, known_domain_names

    domain_error = domain_refusal(getattr(args, "domain", None), known_domain_names())
    if domain_error:
        print(json.dumps({"ok": False, "error": domain_error}) if output == "json" else f"❌ {domain_error}")
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
    init_step, init_error = _init_step(args, proj_dir, project_yaml, dry_run)
    if init_error:
        print(
            json.dumps({"ok": False, "error": init_error, "steps": steps}) if output == "json" else f"❌ {init_error}"
        )
        return 1
    steps.append(init_step)

    if dry_run and not project_yaml.exists():
        steps.append({"step": "patch-project-yaml", "changed": True, "note": "dry-run, file not yet created"})
    else:
        changed = _patch_project_yaml(
            project_yaml, ai_id=name, tenant=tenant, org=org, substrate=substrate, dry_run=dry_run
        )
        steps.append({"step": "patch-project-yaml", "changed": changed})

    steps.append(_gitignore_step(proj_dir, dry_run))

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

    launch_command = (
        _cockpit_steps(profile, name, proj_dir, dry_run, steps, launch_flag, resume_flag, second_pane)
        if profile
        else None
    )

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
    if launch_command:
        payload["launch_command"] = launch_command

    if output == "json":
        print(json.dumps(payload, indent=2))
        return 0 if not any_error else 1

    _print_human(name, proj_dir, steps, dry_run, launch_command, any_error)
    return 0 if not any_error else 1
