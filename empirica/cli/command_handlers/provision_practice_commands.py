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
import re
import shlex
import shutil
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


def _cortex_seat() -> bool:
    """Whether a real Cortex is configured here (presence, no network call)."""
    try:
        from empirica.core.auth.cortex_oauth import cortex_configured

        return bool(cortex_configured())
    except Exception:
        return False


def _mesh_edition() -> str | None:
    """ "cortex", "local" or None: where this machine's practices would send their mailbox (core/mesh_endpoint.py)."""
    try:
        from empirica.core.mesh_endpoint import mesh_bearer

        return mesh_bearer().get("edition")
    except Exception:
        return None


def _wait_for_local_server(seconds: float = 20.0) -> bool:
    """Whether the local server answers within ``seconds`` of the service starting."""
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _mesh_edition() == "local":
            return True
        time.sleep(0.4)
    return False


_SH_TRICK = re.compile(r"""^'{3}exec' (?:"([^"]+)"|'([^']+)'|(\S+))""")  # pip: "path" or bare; uv: single quotes


def _launcher_python(exe: str) -> str | None:
    """The Python a console-script launcher runs, or None when it cannot be told.

    Three shapes: ``#!/path/python`` (and ``#!/usr/bin/env python3``); pip's and uv's ``#!/bin/sh`` trick for an interpreter
    path with a space or over 127 characters, whose second line is three single quotes, ``exec'`` and the path (double-quoted
    or bare from pip, single-quoted from uv);
    and anything else (a pyenv or asdf bash shim, a compiled launcher, ``env -S``), which is None. Running a non-Python
    program with ``-c "import fastapi, uvicorn"`` reported working installs as missing the extra (1.15.1 second pass, D1)."""
    try:
        with open(exe, "rb") as fh:
            head = fh.read(4096).decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    if not head or not head[0].startswith("#!"):
        return None
    parts = head[0][2:].split()
    if parts and os.path.basename(parts[0]) == "env":
        parts = parts[1:]
    if parts and os.path.basename(parts[0]).startswith("python"):
        return parts[0]
    if parts and os.path.basename(parts[0]) == "sh" and len(head) > 1:
        m = _SH_TRICK.match(head[1])
        path = next((g for g in m.groups() if g), None) if m else None
        if path and os.path.basename(path).startswith("python"):
            return path
    return None


def _api_extra_missing() -> str | None:
    """Why the binary the serve unit will run cannot start the server (no fastapi/uvicorn); None when it can, or when that
    cannot be told.

    The unit runs ``shutil.which("empirica")`` (persistent_serve.py), which may belong to another venv than this
    interpreter (pipx), so the check runs in that launcher's Python. An unrecognised launcher skips the check rather than
    block a working install. Without the extra the unit's `empirica serve` exits 1 and restarts until systemd gives up,
    while provisioning reported ok (1.15.1 sweep, P1)."""
    exe = shutil.which("empirica")
    interpreter = _launcher_python(exe) if exe else sys.executable
    if not interpreter:
        return None
    try:
        r = subprocess.run([interpreter, "-c", "import fastapi, uvicorn"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None  # could not ask: do not block on it
    if r.returncode == 0:
        return None
    lines = (r.stderr or "").strip().splitlines()
    return lines[-1] if lines else f"{interpreter} cannot import fastapi and uvicorn"


def _local_url() -> str:
    from empirica.core.mesh_endpoint import local_server_url

    return local_server_url()


def _serve_unit_installed() -> bool:
    """Whether this machine already has the serve unit (systemd-user .service or launchd .plist)."""
    try:
        from empirica.core.loop_scheduler.persistent_serve import PersistentServeService

        path = PersistentServeService().unit_path()
        return bool(path and path.exists())
    except Exception:
        return False


def _embedding_model_step(args, dry_run: bool, *, post=None) -> dict:
    """The ``embedding-model`` step: when the local server will embed for this machine (no Cortex, no Ollama model), have
    it load its model now, downloading it once (about 135 MB), so no hook ever waits on the download (goal db2cf3fa)."""
    step: dict = {"step": "embedding-model", "changed": False}
    if getattr(args, "no_local_server", False) or getattr(args, "no_cortex", False):
        return {**step, "note": "skipped (--no-local-server or --no-cortex)"}
    try:
        from empirica.core.qdrant.connection import local_server_service
        from empirica.core.qdrant.embeddings import _check_ollama_available, _load_config_file

        # Resolved as the embedding provider resolves them (env, then ~/.empirica/config.yaml): reading only the env missed an
        # Ollama or a provider set in the file, and downloaded a model this machine never uses (1.16.x, goal a34eef6d).
        conf = _load_config_file()
        provider = os.environ.get("EMPIRICA_EMBEDDINGS_PROVIDER", conf.get("provider", "auto")).lower()
        if provider not in ("auto", "local"):
            return {
                **step,
                "note": f"embeddings use the configured provider ({provider}): the local model is not needed",
            }
        if _check_ollama_available(
            os.environ.get("EMPIRICA_OLLAMA_URL", conf.get("ollama_url", "http://localhost:11434"))
        ):
            return {**step, "note": "Ollama has an embedding model: the local model is not needed"}
        url = local_server_service("embeddings")
        from empirica.core.qdrant.connection import store_url

        if url and (store_url() != local_server_service("vectors") or os.environ.get("CORTEX_QDRANT_URLS_BY_ORG")):
            # Same rule as the provider's auto choice: the local model's vectors belong in the local store only.
            return {**step, "note": "vectors go to a configured Qdrant: the local model is not used there"}
    except Exception:
        url = None
    if not url:
        return {**step, "note": "no local embedding service: semantic search uses a configured provider, or is off"}
    if dry_run:
        return {**step, "changed": True, "note": "dry-run: would load the embedding model (about 135 MB, once)"}
    _narrate("Loading the embedding model (about 135 MB the first time; it stays on this machine)...")
    try:
        status, _ = (post or _post_json)(f"{url}/api/embed", {"input": ["warm"]}, 600)
    except OSError as e:
        status = f"{type(e).__name__}: {e}"
    if status == 200:
        return {**step, "changed": True, "note": "embedding model ready"}
    return {
        **step,
        "error": f"the local embedding model did not load ({status})",
        "hint": "check the network and `empirica serve-service status`; `empirica doctor` shows the semantic-search state",
    }


def _post_json(url: str, body: dict, timeout: float):
    import urllib.request

    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def _local_server_step(args, proj_dir: Path, dry_run: bool) -> dict:
    """The ``local-server`` step: with no Cortex configured and no local server running, install and start the serve service,
    so practices on this machine can hand work to each other (David's ruling 2026-11-10: nothing to configure).

    Onboarding is where it happens, and it says so. ``--no-local-server`` and ``--no-cortex`` skip it; plain ``setup`` never
    installs a background service. Every way the server can end up not answering is an ``error`` with a hint, never a note
    under ``ok: true``: a practice that cannot hear its mail looks exactly like one with no mail (1.15.1 sweep)."""
    step: dict = {"step": "local-server", "changed": False}
    if getattr(args, "no_local_server", False) or getattr(args, "no_cortex", False):
        return {**step, "note": "skipped (--no-local-server or --no-cortex)"}
    edition = _mesh_edition()
    if edition == "cortex":
        return {**step, "note": "Cortex is configured: practices use it, no local server needed"}
    if edition == "local":
        return {**step, "note": "the local server is already running"}
    if _serve_unit_installed():
        # An older serve still running, a failed unit, or another process on the port. Reinstalling would rewrite the one
        # unit (and its one .bak) on every provision and still not restart an active unit.
        return {
            **step,
            "error": f"the serve service is installed but nothing answers as the local server at {_local_url()}",
            "hint": "check `empirica serve-service status` and its log. An older serve may still be running: "
            "`empirica serve-service uninstall`, then provision again. If another program uses the port, stop it, or "
            "uninstall and provision again with EMPIRICA_SERVE_PORT set (the port is fixed when the service is "
            "installed; on Linux the service stops whatever holds its port when it starts). If the log says uvicorn or "
            "fastapi is missing, install `empirica[api]`. `--no-local-server` skips this step",
        }
    missing = _api_extra_missing()
    if missing:
        return {
            **step,
            "error": f"the local server needs the [api] extra: {missing}",
            "hint": "install it (`pipx install --force 'empirica[api]'` or `pip install 'empirica[api]'`), then run this again",
        }
    # From HOME: serve binds its project from WorkingDirectory, and this is the machine's one serve unit (P2).
    ok, err = _run(["empirica", "serve-service", "install"], Path.home(), dry_run)
    if not ok:
        return {
            **step,
            "error": err,
            "hint": "start it by hand with `empirica serve` (or `empirica serve-service install`), then run this again",
        }
    if dry_run:
        return {**step, "changed": True, "note": "dry-run: would install and start the serve service"}
    if _wait_for_local_server():
        return {
            **step,
            "changed": True,
            "note": "installed and started the serve service: practices on this machine hand work to each other through it",
        }
    return {
        **step,
        "changed": True,
        "error": "installed the serve service, but the local server did not answer",
        "hint": "check `empirica serve-service status` and its log; if it says uvicorn or fastapi is missing, install "
        "`empirica[api]`",
    }


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
    from empirica.core.cockpit.launcher.config import profile_without_windows

    if second_pane and profile_without_windows(profile):
        # Refused before anything is written: the main entry would be added and then the second pane would fail.
        steps.append(
            {
                "step": "cockpit-profile",
                "changed": False,
                "error": f"profile {profile!r} lists projects without windows (no groups:), so --second-pane has no window to "
                "share; add a groups: section to it, or provision without --second-pane",
            }
        )
        return None
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


def _initial_commit_step(proj_dir: Path, dry_run: bool) -> dict:
    """An empty first commit when the practice repo has none, so git notes (the canonical log) can be written from the
    first artifact. project-init runs ``git init`` and commits nothing: on a fresh machine every artifact logged before the
    person's own first commit went to SQLite only (git_stored:false), and a rebuild from notes would lose it (goal
    db2cf3fa). Empty, so none of the person's files are committed for them; no git identity is invented."""
    step: dict = {"step": "initial-commit", "changed": False}
    top = (
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=proj_dir, capture_output=True, text=True, check=False
        ).stdout.strip()
        if proj_dir.is_dir()
        else ""
    )
    # Only the practice's own repo: a folder inside another repo resolves to that one, which is not ours to commit to.
    # project-init makes the repo and reports its own failure; in a dry run it has not run.
    if not top or Path(top).resolve() != proj_dir.resolve():
        return {**step, "note": "not its own git repository yet: nothing to commit to"}
    has_head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=proj_dir, capture_output=True, text=True, check=False
    )
    if has_head.returncode == 0:
        return step
    if dry_run:
        _narrate(f"[dry-run] would make an empty first commit in {proj_dir}")
        return {**step, "changed": True, "note": "dry-run: would make an empty first commit"}
    r = subprocess.run(
        ["git", "commit", "--allow-empty", "-q", "-m", "Initial commit (empirica provision-practice)"],
        cwd=proj_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return {
            **step,
            "error": (r.stderr or r.stdout or "git commit failed").strip().splitlines()[-1],
            "hint": "set git's identity (`git config --global user.name ...` and `git config --global user.email ...`), "
            "then run this again: until the practice has a commit, nothing it logs reaches its git notes",
        }
    return {**step, "changed": True, "note": "empty first commit, so git notes can be written"}


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
    # Only when nothing was said: an explicit --substrate is kept, and a half-given address (`--org acme` alone) gets the
    # usual "name both" message rather than `acme.<user>` beside a server that answers as `local.<user>` (1.15.1 sweep).
    if not tenant and not org and substrate != "local" and not getattr(args, "substrate", None) and not _cortex_seat():
        # A first machine with no Cortex has no tenant or org to inherit: it is the local edition, addressed
        # local.<user>.<practice> by the local server (1.15.1). A Cortex seat still has to name or inherit them, and a
        # --no-cortex practice (no mailbox at all) stays unaddressed, as before.
        from empirica.core.mesh_endpoint import LOCAL_ORG, local_user

        tenant, org, substrate = tenant or local_user(), org or LOCAL_ORG, "local"

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
    steps.append(_initial_commit_step(proj_dir, dry_run))

    steps.append(_local_server_step(args, proj_dir, dry_run))  # before registering, so registration reaches it
    steps.append(_embedding_model_step(args, dry_run))

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
