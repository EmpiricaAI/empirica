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


def _which(cmd: str, path: str | None = None) -> str | None:
    """`shutil.which`, optionally against an explicit PATH rather than ours.

    The `path` argument matters for MCP entries that pin their own `env.PATH`:
    resolving such a command against doctor's PATH answers a different question
    than the one being asked.
    """
    return shutil.which(cmd, path=path) if path else shutil.which(cmd)


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


def check_engagement_registry_drift() -> Check:
    """Engagement dual-write drift — both orphan classes (prop_rif7asmh).

    An engagement needs an entity_registry row (what surfaces render) AND an
    engagements sidecar row (where dates/warmth/stage live). Nothing used to
    link the writes, and each entry point dropped a different half: one fleet
    box measured 82 registry-only (visible, dateless) + 12 sidecar-only
    (invisible). The write paths are fixed to be atomic; this check makes any
    REMAINING or future drift loudly visible instead of silently rendering
    wrong. WARN, not FAIL — pre-fix orphans are repairable, not fatal.
    """
    try:
        from empirica.data.repositories.workspace_db import WorkspaceDBRepository

        with WorkspaceDBRepository.open() as repo:
            # Shape FIRST. On a legacy-shaped table the drift query below throws
            # an opaque OperationalError, which reports a symptom and hides the
            # cause; and drift numbers are meaningless on a table nothing can
            # write to anyway.
            blockers = repo.engagement_schema_blockers()
            if blockers:
                cols = ", ".join(f"{b['column']} ({b['type']})" for b in blockers)
                return Check(
                    "Engagement registry drift",
                    FAIL,
                    f"engagements table is legacy-shaped — every insert is rejected: {cols}",
                    "This box's workspace.db was seeded from the retired CRM tables rather than "
                    "created fresh, so `engagements` carries NOT NULL columns no current code "
                    "path supplies. The additive self-heal cannot fix it (sqlite ALTER cannot "
                    "drop a NOT NULL, and rebuilding the table would rewrite real engagement "
                    "history — that needs explicit, backed-up, opt-in repair, not a silent "
                    "migration on open). Until then engagement creation fails on this box only. "
                    "Note the negative: `~/.empirica/crm/crm.db` existing does NOT mean you are "
                    "affected — clean boxes have it too. This check is the detector.",
                    data={"blocking_columns": blockers},
                )
            drift = repo.engagement_registry_drift()
    except Exception as e:
        return Check(
            "Engagement registry drift",
            WARN,
            "",
            f"could not read workspace db: {type(e).__name__}: {e}",
        )
    reg_only, side_only = drift["registry_only"], drift["sidecar_only"]
    if not reg_only and not side_only:
        return Check("Engagement registry drift", PASS, "registry and sidecar in sync")
    parts = []
    if reg_only:
        parts.append(f"{len(reg_only)} registry-only (render everywhere, no date fields)")
    if side_only:
        parts.append(f"{len(side_only)} sidecar-only (invisible on every surface)")
    return Check(
        "Engagement registry drift",
        WARN,
        "; ".join(parts),
        "registry-only need a sidecar row (create_engagement now writes both); "
        "sidecar-only need registration (re-run creation, or upsert_entity for each id)",
        data={"registry_only": reg_only[:20], "sidecar_only": side_only[:20]},
    )


def check_orphaned_presence() -> Check:
    """Live practitioners whose practice has no running listener.

    Since the heartbeat emitter became practice-scoped, each listener forwards
    only its OWN practice's presence records. A practice with live sessions and no
    running listener therefore goes dark on the mesh — previously any listener
    would have carried it. Zero-impact when every live practice has a listener,
    which is the normal state; this exists because "normal" here is circumstance,
    not construction.

    Also catches label drift between the two vocabularies: a record written as
    `workspace` while its listener runs as `empirica-workspace` is orphaned just as
    effectively as a missing listener.
    """
    try:
        from empirica.core.practitioner_presence import list_presence

        live = list_presence(include_stale=False)
    except Exception as e:
        return Check("Presence coverage", WARN, "", f"could not read presence store: {type(e).__name__}: {e}")

    if not live:
        return Check("Presence coverage", PASS, "no live practitioners")

    rc, out, _ = _run(["systemctl", "--user", "list-units", "--type=service", "--no-pager"], timeout=6.0)
    if rc != 0:
        return Check("Presence coverage", PASS, f"{len(live)} live (listener units not enumerable here)")
    listeners = {
        ln.split("empirica-listener-", 1)[1].split(".service", 1)[0]
        for ln in out.splitlines()
        if "empirica-listener-" in ln and ".service" in ln
    }
    orphans: dict[str, int] = {}
    for rec in live:
        practice = (rec.get("practice_ai_id") or "").strip() or "<unlabelled>"
        if practice not in listeners:
            orphans[practice] = orphans.get(practice, 0) + 1
    if not orphans:
        return Check("Presence coverage", PASS, f"{len(live)} live practitioner(s), all covered")
    detail = ", ".join(f"{k}={v}" for k, v in sorted(orphans.items()))
    return Check(
        "Presence coverage",
        WARN,
        f"{sum(orphans.values())} of {len(live)} live record(s) have no listener: {detail}",
        "start the practice's listener, or reconcile the practice_ai_id label with its listener name",
        data={"orphans": orphans, "listeners": sorted(listeners)},
    )


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


def _empirica_version_on_path() -> str | None:
    """The version of the `empirica` a user's shell would actually run, or None.

    Parses the number out of `empirica --version`, which also prints the Python
    line. Deliberately uses the CLI rather than this process's own
    `empirica.__version__`: `doctor` can be invoked from the MCP env itself, in
    which case importing locally would compare that env against itself and never
    report drift.
    """
    rc, out, _ = _run(["empirica", "--version"])
    if rc != 0 or not out:
        return None
    for tok in out.split():
        if tok and tok[0].isdigit():
            return tok
    return None


def _mcp_bundled_empirica_version(mcp_path: str) -> str | None:
    """The empirica version INSIDE the empirica-mcp environment, or None.

    `empirica-mcp` is typically its own isolated env (pipx, or a venv), so it
    bundles its own `empirica` — which can be arbitrarily older than the one on
    PATH. Reads that env's interpreter rather than the ambient one.

    Best-effort by design: an env whose interpreter cannot be located or queried
    returns None, and the caller degrades to the presence-only verdict rather than
    failing a health check over introspection trouble.
    """
    interp = Path(mcp_path).resolve().parent / "python"
    if not interp.is_file():
        return None
    rc, out, _ = _run(
        [str(interp), "-c", "import importlib.metadata as m; print(m.version('empirica'))"],
        timeout=8.0,
    )
    return out.strip() if rc == 0 and out.strip() else None


def check_empirica_mcp() -> Check:
    """Present AND not stale.

    Presence alone was the whole check, so an MCP env bundling an ancient empirica
    reported PASS — and every box in a 2026-07-30 fleet sweep that HAD an
    empirica-mcp seat had a stale one (1.12.33, 1.12.1, 1.8.12 against a current
    1.12.38). The Desktop MCP path silently ran months-old code while `doctor` said
    healthy, because nothing upgrades that env when the main install moves and
    nothing looked.

    A check that cannot fail on a case reports clean for it forever.
    """
    path = _which("empirica-mcp")
    if not path:
        return Check(
            "empirica-mcp on PATH",
            WARN,
            "",
            "pip install --user empirica-mcp  (only needed for Claude Desktop / IDE MCP clients)",
            data={"path": None},
        )

    bundled = _mcp_bundled_empirica_version(path)
    running = _empirica_version_on_path()
    data = {"path": path, "bundled_empirica": bundled, "running_empirica": running}

    # Unresolvable version on either side degrades to the old presence verdict —
    # never FAIL a health check because introspection was inconclusive.
    if not bundled or not running:
        return Check("empirica-mcp on PATH", PASS, path, data=data)

    if bundled != running:
        return Check(
            "empirica-mcp on PATH",
            WARN,
            f"{path} — bundles empirica {bundled}, but {running} is on PATH",
            "pipx upgrade empirica-mcp   (or reinstall it) — the Desktop/IDE MCP path "
            "runs the BUNDLED version, so it stays on old code until this env is refreshed",
            data=data,
        )
    return Check("empirica-mcp on PATH", PASS, f"{path} (empirica {bundled})", data=data)


def _cli_package_dir() -> Path | None:
    """Where the `empirica` on PATH actually loads its package from, or None.

    Asks the CLI, deliberately, and NOT this process's own ``empirica.__file__``.
    An interpreter invoked from inside a checkout puts the cwd on ``sys.path``, so
    ``import empirica`` resolves to the checkout while the console script — whose
    ``sys.path[0]`` is its own script dir — loads the installed copy. Two people hit
    exactly that trap on the same day, one of them me, an hour after reading the
    other's warning about it.
    """
    rc, out, _ = _run(["empirica", "--version"], timeout=15.0)
    if rc != 0:
        return None
    for line in out.splitlines():
        if line.startswith("Install:"):
            return Path(line.split(":", 1)[1].strip())
    return None


def _is_checkout(d: Path) -> bool:
    """Is `d` an empirica source checkout?

    By CONTENT — a ``pyproject.toml`` naming this project beside an ``empirica/``
    package — not by directory name, which any clone, backup or docs folder shadows.
    """
    pyproject = d / "pyproject.toml"
    if not (d / "empirica" / "__init__.py").is_file() or not pyproject.is_file():
        return False
    try:
        head = pyproject.read_text()[:2000]
    except OSError:
        return False
    return 'name = "empirica"' in head or "name = 'empirica'" in head


def _find_checkout(start: Path | None = None) -> Path | None:
    """An empirica checkout on this box, or None. **Not cwd-only.**

    The first version walked up from cwd and nothing else, which made the answer a
    property of where you were standing rather than of the install — so a developer
    running `doctor` from any other repo was told PASS while the skew was live. Every
    practitioner not sitting in core's tree, which is most of the fleet, got the
    reassuring answer.

    So: cwd's ancestors first (cheapest, and the most likely intent), then the
    daemon's project registry, which is a cwd-independent list of real paths on this
    machine. Registry entries can be stale; a path that no longer looks like a
    checkout is simply skipped.
    """
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if _is_checkout(d):
            return d

    registry = Path.home() / ".empirica" / "registry.yaml"
    if not registry.is_file():
        return None
    try:
        import yaml

        entries = (yaml.safe_load(registry.read_text()) or {}).get("projects") or []
    except Exception:
        return None
    for entry in entries:
        path = (entry or {}).get("path")
        if path and _is_checkout(Path(path)):
            return Path(path)
    return None


def check_cli_matches_checkout(cwd: Path | None = None) -> Check:
    """Is the `empirica` you would run the code you are standing in?

    **The version cannot answer this**, which is the whole point. A pipx copy and a
    working tree both report the same number while the code differs by any number of
    unreleased commits, so `--version` matching is not evidence and never was. This
    practice has recorded the same defect three times; twice the remediation was
    "reinstall", which is a fix for an instance and not for a class.

    The cost is not a stale binary — it is a **misattributed test result**. A peer ran
    a shipped fix against a copy predating it, got pre-fix behaviour, and was one
    message from reporting the fix broken. Absent this check that report is
    indistinguishable from a real regression, and the fix is what gets re-opened.

    WARN, never FAIL: running a released copy while sitting in a checkout is a normal
    thing to do on purpose. What is not normal is not knowing.

    And it never returns PASS for a comparison it did not make. Folding *not checked*
    into *passed* is how an exemption reports clean forever — SKIP is the honest verdict
    and doctor already uses it elsewhere in the same output.
    """
    pkg = _cli_package_dir()
    checkout = _find_checkout(cwd)

    if pkg is None:
        detail = f"could not resolve what `empirica` loads{f' (checkout at {checkout})' if checkout else ''}"
        return Check("CLI matches checkout", WARN, detail)

    if checkout is None:
        # No checkout anywhere on this box — a released copy is CORRECT here, but the
        # comparison was not performed, so say that rather than claiming agreement.
        return Check(
            "CLI matches checkout",
            SKIP,
            f"`empirica` loads {pkg} — no empirica checkout found on this box, nothing to compare against",
            data={"cli_package_dir": str(pkg), "checkout": None},
        )

    data = {"checkout": str(checkout), "cli_package_dir": str(pkg)}
    if pkg.resolve() == checkout.resolve():
        return Check("CLI matches checkout", PASS, f"editable — `empirica` runs {checkout}", data=data)

    return Check(
        "CLI matches checkout",
        WARN,
        f"`empirica` loads {pkg}, NOT the checkout at {checkout} — same version number either way",
        f"pipx install --force --editable {checkout}   (or run `python -m empirica.cli.cli_core` "
        "to exercise the tree). Until then a test of uncommitted or unreleased work is testing the "
        "installed copy, and a passing or failing result says nothing about your code.",
        data=data,
    )


def check_plugin_freshness() -> Check:
    """Is the DEPLOYED Claude Code plugin the same version as the package?

    The deployed plugin is a COPY. `pip install -U` refreshes the package and leaves
    the copy untouched, so a box runs old hooks while every version surface reports
    the new number. The supervisor backoff, the sentinel gate and the arming block all
    live in that copy — a release that fixes them fixes nothing until it is synced.

    **`doctor` is deliberately exempt from the CLI's plugin auto-heal** (it would
    re-enter), which means the one verb a practitioner runs to check install health is
    the one that neither heals this nor, until now, reported it. `diagnose` checks the
    plugin files EXIST — presence only, so a plugin several minor versions behind
    passes. Same presence-vs-freshness gap `check_empirica_mcp` closed above.

    Also surfaces `.plugin_autosync_failed`. `cli_core` writes that breadcrumb
    specifically so *"doctor/diagnose (and a human) surface this"* — and nothing did.
    A failing self-heal is worse than an absent one: the debounce marker reads
    "checked" while the box keeps running stale hooks.

    Reports, never heals — a diagnostic that repairs what it measures cannot tell you
    what it found.
    """
    plugin_dir = Path.home() / ".claude" / "plugins" / "local" / "empirica"
    if not plugin_dir.exists():
        return Check("Deployed plugin fresh", SKIP, "no Claude Code plugin installed — nothing to compare")

    stamp_file = plugin_dir / ".plugin-version"
    stamp = stamp_file.read_text().strip() if stamp_file.is_file() else None
    try:
        import empirica

        pkg = empirica.__version__
    except Exception:
        pkg = None

    failed = Path.home() / ".empirica" / ".plugin_autosync_failed"
    data = {"plugin_dir": str(plugin_dir), "deployed": stamp, "package": pkg, "autosync_failed": failed.is_file()}

    if failed.is_file():
        return Check(
            "Deployed plugin fresh",
            WARN,
            f"auto-sync FAILED: {failed.read_text().strip()[:180]}",
            "empirica plugin-sync   — the self-heal errored, so the box is still on the old hooks "
            "while the debounce marker reads 'checked'",
            data=data,
        )

    if pkg is None:
        return Check(
            "Deployed plugin fresh", WARN, f"deployed {stamp or 'unstamped'}; package version unreadable", data=data
        )

    if stamp is None:
        return Check(
            "Deployed plugin fresh",
            WARN,
            f"deployed plugin carries NO version stamp (package {pkg}) — predates stamping, so it is old",
            "empirica plugin-sync",
            data=data,
        )

    if stamp != pkg:
        return Check(
            "Deployed plugin fresh",
            WARN,
            f"deployed {stamp}, package {pkg} — hooks, sentinel gate and arming block are the OLD copy",
            "empirica plugin-sync   (or `empirica setup-claude-code --force`) — upgrading the package "
            "does not refresh the deployed copy",
            data=data,
        )

    return Check("Deployed plugin fresh", PASS, f"deployed {stamp} == package {pkg}", data=data)


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
    # Mirrors the Sentinel's optional noetic allowlist (SAFE_BASH_PREFIXES) so
    # every tool that's allowlisted-ahead-of-install is surfaced here — closing
    # the "told it's available when it isn't" gap. rg/fd/yq/ast-grep are the
    # priority recon set; gron/bat/tokei/scc are the lower-tier extras.
    tools: dict[str, tuple[list[str], str]] = {
        "rg": (["rg"], "ripgrep — fast, gitignore-aware search"),
        "fd": (["fd", "fdfind"], "fast, gitignore-aware file find"),
        "jq": (["jq"], "JSON query"),
        "yq": (["yq"], "YAML query"),
        "ast-grep": (["ast-grep"], "structural / AST-aware code search"),
        "gron": (["gron"], "flatten JSON to greppable paths"),
        "bat": (["bat", "batcat"], "syntax-highlighted file view"),
        "tokei": (["tokei"], "fast LOC / code stats"),
        "scc": (["scc"], "code counter + complexity"),
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
    check_data = {
        "path": str(pyaml_path),
        "ai_id": ai_id,
        "dir_basename": cwd.name,
        "org_id": data.get("org_id"),
        "tenant_slug": data.get("tenant_slug"),
        "mesh_id_prefix": data.get("mesh_id_prefix"),
    }
    # The convention is ai_id == directory basename. A mismatch is LATENT, not
    # live: nothing breaks until something reads ai_id and acts on it — and the
    # thing that does is `setup-claude-code --force`, which mints a listener
    # unit under whatever the file declares. It looks like a successful upgrade
    # and leaves the practice invisible to every peer addressing the canonical
    # form. Nine dormant projects on one box carried this mismatch when it was
    # first audited, each one armed to mint a broken listener on next setup.
    # WARN, not FAIL: a checkout under a non-canonical directory name (a git
    # worktree, a renamed clone) is legitimate, and there ai_id is the authority.
    if ai_id != cwd.name:
        return Check(
            "project.yaml present + has ai_id",
            WARN,
            f"ai_id={ai_id} but directory is {cwd.name}",
            "Peers address the canonical ai_id, and `setup-claude-code --force` "
            "will mint a listener under it. Reconcile project.yaml's ai_id with "
            "the directory name, or confirm the mismatch is deliberate.",
            data=check_data,
        )
    return Check(
        "project.yaml present + has ai_id",
        PASS,
        f"ai_id={ai_id}",
        data=check_data,
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
            "Run `empirica setup` (interactive wizard) or hand-edit ~/.empirica/credentials.yaml",
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
            "Run `empirica setup` wizard or hand-edit ~/.empirica/credentials.yaml",
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
            f"`empirica loop listen-install --ai-id {ai_id}` (or re-run `empirica setup`)",
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
    """Common locations for MCP client config that may register empirica/cortex servers.

    ``~/.claude.json`` is FIRST and is not optional: it is where Claude Code
    stores user-scope MCP servers (`claude mcp remove ... -s user` reports
    "File modified: ~/.claude.json"). It was absent from this list, so doctor
    inspected ``~/.claude/mcp.json``, found a clean entry, and reported PASS
    while the config Claude Code actually loaded carried an env.PATH on which
    the empirica CLI did not resolve — broken for weeks, and doctor was
    structurally incapable of seeing it.

    Reported by empirica.philipp.empirica-mesh-support and reproduced here: the
    two files had already diverged on this box as well.
    """
    home = Path.home()
    return [
        home / ".claude.json",  # Claude Code, user scope — the live store
        home / ".claude" / "mcp.json",  # Claude Code, legacy/project-adjacent
        home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",  # macOS Desktop
        home / ".config" / "Claude" / "claude_desktop_config.json",  # Linux Desktop fallback
    ]


def _mcp_entry_command_resolves(entry: dict) -> tuple[bool, str | None]:
    """Can this MCP server entry actually launch its command?

    Returns ``(resolves, missing_detail)``. An entry that names a command the
    client cannot execute is a *nominally present, functionally dead* server —
    which is precisely what a name-only check cannot distinguish. When the
    entry pins ``env.PATH``, resolution must be tested against THAT PATH, not
    the PATH doctor happens to be running under.
    """
    command = entry.get("command")
    if not isinstance(command, str) or not command:
        return True, None  # nothing to verify (e.g. a url/sse-style entry)

    # An absolute path is checked directly; PATH does not enter into it.
    cmd_path = Path(command)
    if cmd_path.is_absolute():
        if cmd_path.exists():
            return True, None
        return False, f"command does not exist: {command}"

    env = entry.get("env")
    pinned_path = env.get("PATH") if isinstance(env, dict) else None
    if not pinned_path:
        return True, None  # inherits the client's PATH — not ours to judge

    if _which(command, path=pinned_path):
        return True, None
    return False, f"`{command}` does not resolve on the entry's own env.PATH"


def check_mcp_version_skew() -> Check:
    """Core and empirica-mcp must move together — pipx upgrade splits them.

    `empirica-mcp` is an INJECTED package in the pipx venv, and `pipx upgrade
    empirica` upgrades only the main package: the injected one silently stays
    behind, doctor passed 0 FAIL, and the config check only verified a server
    was *configured*, not that its version matched core (GH #404). A 1.13.1
    server behind a 1.13.7 CLI serves six releases of drift with nothing
    anywhere saying so.

    Version comparison is by installed distribution metadata — the same
    instrument for both packages, not a version string one of them prints.

    Complementary to `check_empirica_mcp`, not a duplicate: that check covers
    the SEPARATE-ENV topology (a standalone empirica-mcp env bundling its own
    empirica, compared against the one on PATH). This one covers the INJECTED
    topology, where both distributions share one venv — there `bundled` always
    equals `running`, so the existing check passes while the empirica-mcp
    package itself sits releases behind. Each check SKIPs cleanly on the other's
    topology (separate env → empirica-mcp not importable here; injected →
    that check's versions match by construction).
    """
    import importlib.metadata as _im

    try:
        core_v = _im.version("empirica")
    except Exception:
        return Check("core/MCP version match", SKIP, "empirica distribution metadata unavailable")
    try:
        mcp_v = _im.version("empirica-mcp")
    except Exception:
        # Not installed in this environment is a legitimate state (MCP is
        # optional) — absence is not skew.
        return Check(
            "core/MCP version match",
            SKIP,
            "empirica-mcp not installed in this environment",
            data={"core": core_v, "mcp": None},
        )
    if core_v != mcp_v:
        return Check(
            "core/MCP version match",
            WARN,
            f"empirica {core_v} but empirica-mcp {mcp_v} — the MCP server is serving a different release",
            "pipx users: `pipx inject empirica empirica-mcp --force` (pipx upgrade does NOT "
            "upgrade injected packages). pip users: `pip install -U empirica-mcp`.",
            data={"core": core_v, "mcp": mcp_v},
        )
    return Check("core/MCP version match", PASS, f"both at {core_v}", data={"core": core_v, "mcp": mcp_v})


def check_mcp_config() -> Check:
    """Surface MCP config entries and verify they can actually launch.

    This check used to test only whether an entry EXISTED BY NAME. A server
    whose `command` cannot be resolved is nominally present and functionally
    dead, and the name test passes it — so "MCP servers configured: PASS" was
    reported for weeks against a config that returned "empirica CLI not found"
    on every call. Presence is not function; test the thing you are claiming.
    """
    found_configs = []
    server_names: set[str] = set()
    broken: list[str] = []
    # name -> {command} seen per file, so the SAME server defined differently in
    # two configs is surfaced. That divergence is what let the fault hide: one
    # file was clean, doctor read that one, and the one the client loaded was not.
    definitions: dict[str, set[str]] = {}

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

        for name, entry in servers.items():
            if not isinstance(entry, dict):
                continue
            definitions.setdefault(name, set()).add(str(entry.get("command") or ""))
            if name not in ("empirica", "cortex"):
                continue
            resolves, detail = _mcp_entry_command_resolves(entry)
            if not resolves:
                broken.append(f"{path.name}:{name} — {detail}")

    if not found_configs:
        return Check(
            "MCP servers configured",
            WARN,
            "no MCP config found",
            "Optional — only needed for Claude Desktop / Cursor / Windsurf",
            data={"configs": []},
        )

    diverged = sorted(n for n, cmds in definitions.items() if len(cmds) > 1)
    has_empirica = "empirica" in server_names
    has_cortex = "cortex" in server_names
    base_data = {
        "configs": found_configs,
        "has_empirica": has_empirica,
        "has_cortex": has_cortex,
        "broken": broken,
        "diverged": diverged,
    }

    if broken:
        return Check(
            "MCP servers configured",
            WARN,
            "; ".join(broken),
            "The entry exists but its command cannot launch — re-run `empirica setup --force`, "
            "or fix the entry's env.PATH to include the directory holding the CLI.",
            data=base_data,
        )

    if diverged:
        return Check(
            "MCP servers configured",
            WARN,
            f"same server defined differently across configs: {', '.join(diverged)}",
            "Claude Code loads ~/.claude.json for user-scope servers. When two configs disagree, "
            "the one you inspect may not be the one that runs — reconcile them.",
            data=base_data,
        )

    if has_empirica or has_cortex:
        detail_parts = [f"{c['path']}: {', '.join(c['servers'])}" for c in found_configs]
        return Check(
            "MCP servers configured",
            PASS,
            " | ".join(detail_parts),
            data=base_data,
        )
    return Check(
        "MCP servers configured",
        WARN,
        "MCP config present but no `empirica` or `cortex` server entry",
        "Run `empirica setup` to register the empirica MCP server",
        data=base_data,
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
        check_cli_matches_checkout(cwd),
        check_empirica_mcp(),
        check_plugin_freshness(),
        check_claude_code_cli(),
        check_git_present(),
        check_noetic_tools(),
        # Project state
        check_empirica_folder(cwd),
        check_project_yaml(cwd),
        check_sessions_db(cwd),
        check_git_remote(cwd),
        check_sync_state(cwd),
        check_engagement_registry_drift(),
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
        check_orphaned_presence(),
        check_listener_service(cwd),
        check_mcp_config(),
        check_mcp_version_skew(),
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
        # Rightsized. This printed 312 lines for a run where 22 of 24 checks
        # PASSED — ~290 lines describing things that are fine, with the two that
        # need action buried mid-array and no way to find them without reading all
        # of it.
        #
        # Two changes, neither removing information:
        #
        #   `attention` — the non-passing checks, hoisted. A caller (human, AI, or
        #   the MCP tool) sees what to act on without scanning. Omitted entirely
        #   when everything passes, so its PRESENCE is the signal.
        #
        #   empty `hint` / `data` dropped per check. Emitted on every passing
        #   check, carrying nothing.
        #
        # `ok`, `summary`, `checks` and `cwd` keep their shape and meaning, so the
        # MCP doctor tool and any other consumer are unaffected.
        def _slim(c: Any, *, full: bool = False) -> dict:
            d = {k: v for k, v in asdict(c).items() if v not in ("", {}, None)}
            # `data` is diagnostic payload — only useful when something is wrong.
            # Measured: 20 of 24 PASSING checks carried it, one of them 37 lines on
            # its own, and it was the bulk of the 312. Kept in full for anything
            # needing action (and in `attention`, which repeats those entries).
            if not full and c.status == PASS:
                d.pop("data", None)
            return d

        payload: dict[str, Any] = {
            "ok": fails == 0,
            "summary": {"total": len(checks), "pass": passed, "warn": warns, "fail": fails, "skip": skips},
        }
        needs_action = [_slim(c, full=True) for c in checks if c.status != PASS]
        if needs_action:
            payload["attention"] = needs_action
        payload["checks"] = [_slim(c) for c in checks]
        payload["cwd"] = str(cwd)
        print(json.dumps(payload, indent=2))
    if fails:
        return 1
    if warns and getattr(args, "strict_warn", False):
        return 2
    return 0
