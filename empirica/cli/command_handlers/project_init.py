#!/usr/bin/env python3
"""
Project Init Command - Initialize Empirica in a new git repository

Creates per-project configuration files:
- .empirica/config.yaml (database paths, settings)
- .empirica/project.yaml (project metadata, BEADS settings)
- docs/SEMANTIC_INDEX.yaml (optional, documentation index template)

Usage:
    cd my-new-project
    git init
    empirica project-init

Author: Rovo Dev
Date: 2025-12-19
"""

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _resolve_or_create_project(
    db, args, project_name, project_description, project_config_path, git_url, project_type, tags, output_format
):
    """Resolve existing project or create new one. Returns project_id."""
    import yaml

    # Explicit --project-id flag
    explicit_id = getattr(args, "project_id", None)
    if explicit_id:
        if output_format != "json":
            print(f"   🔗 Linking to existing project: {explicit_id[:8]}...")
        return explicit_id

    # Check project.yaml for existing ID
    if project_config_path.exists():
        try:
            with open(project_config_path) as f:
                existing_id = (yaml.safe_load(f) or {}).get("project_id")
            if existing_id and db.get_project(existing_id):
                if output_format != "json":
                    print(f"   ♻️  Reusing existing project_id: {existing_id[:8]}...")
                return existing_id
        except Exception:
            pass

    # Check by name
    existing = db.projects.get_project_by_name(project_name)
    if existing:
        if output_format != "json":
            print(f"   ♻️  Found existing project by name: {existing['id'][:8]}...")
        return existing["id"]

    # Create new
    return db.create_project(
        name=project_name,
        description=project_description,
        repos=[git_url] if git_url else None,
        project_type=project_type,
        project_tags=tags if tags else None,
    )


def _register_project_in_workspace(
    project_id, project_name, project_description, git_root, git_url, project_config, output_format
):
    """Register project in global workspace.db. Non-fatal on failure."""
    try:
        import json as _json_ws

        from .workspace_init import _register_in_workspace_db

        ws_metadata = _json_ws.dumps(
            {
                k: project_config.get(k, d)
                for k, d in [
                    ("domain", ""),
                    ("classification", "internal"),
                    ("evidence_profile", "auto"),
                    ("languages", []),
                    ("contacts", []),
                    ("engagements", []),
                    ("edges", []),
                ]
            }
        )
        _register_in_workspace_db(
            project_id=project_id,
            name=project_name,
            trajectory_path=str(git_root / ".empirica"),
            description=project_description,
            git_remote_url=git_url,
            project_type=project_config.get("type", "software"),
            metadata=ws_metadata,
        )
        if output_format != "json":
            print("   📋 Registered in workspace")
    except Exception as e:
        logger.warning(f"Failed to register in workspace.db: {e}")


def _collect_project_config_interactive(git_root) -> dict:
    """Collect project configuration via interactive prompts."""
    print("📋 Project Configuration\n")
    default_name = git_root.name
    project_name = input(f"Project name [{default_name}]: ").strip() or default_name
    project_description = input("Project description (optional): ").strip() or None

    type_choices = "software|content|research|data|design|operations|strategic|engagement|legal"
    type_input = input(f"\nProject type [{type_choices}] (software): ").strip().lower()
    project_type = type_input if type_input in type_choices.split("|") else "software"

    project_domain = input("Domain (e.g., ai/measurement, bio/genomics): ").strip()
    ep_input = input("Evidence profile [code/prose/hybrid/auto] (auto): ").strip().lower()
    evidence_profile = ep_input if ep_input in ("code", "prose", "hybrid", "auto") else "auto"

    enable_beads = input("\nEnable BEADS issue tracking by default? [y/N]: ").strip().lower() in ("y", "yes")
    create_semantic_index = input("Create SEMANTIC_INDEX.yaml template? [y/N]: ").strip().lower() in ("y", "yes")

    return {
        "project_name": project_name,
        "project_description": project_description,
        "enable_beads": enable_beads,
        "create_semantic_index": create_semantic_index,
        "project_type": project_type,
        "project_domain": project_domain,
        "evidence_profile": evidence_profile,
        "classification": "internal",
        "languages": [],
        "tags": [],
    }


def _collect_project_config_from_args(args, git_root) -> dict:
    """Collect project configuration from CLI args (non-interactive)."""
    return {
        "project_name": getattr(args, "project_name", None) or git_root.name,
        "project_description": getattr(args, "project_description", None),
        "enable_beads": getattr(args, "enable_beads", False),
        "create_semantic_index": getattr(args, "create_semantic_index", False),
        "project_type": getattr(args, "type", None) or "software",
        "project_domain": getattr(args, "domain", None) or "",
        "evidence_profile": getattr(args, "evidence_profile", None) or "auto",
        "classification": getattr(args, "classification", None) or "internal",
        "languages": getattr(args, "languages", None) or [],
        "tags": getattr(args, "tags", None) or [],
    }


def _report_git_init_failure(exc, output_format):
    """Surface an actionable error when 'git init' fails.

    Sandboxed harnesses (e.g. ecodex) sometimes pre-mount .git/ read-only,
    so `git init` cannot write HEAD/config. The recovery is either to make
    the mount writable, or skip git as the anchor by passing --project-id
    (caller already has a workspace identity from prior provisioning).
    """
    cwd = Path.cwd()
    git_dir = cwd / ".git"
    likely_readonly_mount = git_dir.exists() and not os.access(git_dir, os.W_OK)

    stderr_raw = getattr(exc, "stderr", None) or ""
    if isinstance(stderr_raw, bytes):
        stderr_raw = stderr_raw.decode("utf-8", errors="replace")
    stderr = stderr_raw.strip()

    recovery = [
        "Supply --project-id <existing-uuid> to skip git init and use cwd as the anchor",
        "Make .git/ writable (fix the sandbox mount)",
        "Run project-init from a directory where git can create .git/",
    ]

    if output_format == "json":
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "git init failed",
                    "stderr": stderr,
                    "likely_cause": "read-only .git/ mount (sandboxed harness)"
                    if likely_readonly_mount
                    else "git init failed",
                    "recovery": recovery,
                },
                indent=2,
            )
        )
    else:
        print("❌ Failed to initialize git repository")
        if stderr:
            print(f"   git error: {stderr}")
        if likely_readonly_mount:
            print("\n   .git/ exists but is not writable — likely a sandboxed harness with a read-only mount.")
        print("\n   Recovery options:")
        for i, hint in enumerate(recovery, 1):
            print(f"   {i}. {hint}")


def _ensure_git_root(interactive, output_format, args=None):
    """Ensure we're in a git repo, optionally initializing one. Returns git_root or None."""
    from empirica.config.path_resolver import get_git_root

    git_root = get_git_root()
    if git_root:
        return git_root

    # Alternative path: --project-id supplied → caller has a workspace identity
    # from prior provisioning. Skip the git init dance and use cwd as the anchor.
    # Lets sandboxed harnesses (read-only .git mounts, etc.) bootstrap without
    # depending on local git writability.
    explicit_project_id = getattr(args, "project_id", None) if args else None
    if explicit_project_id:
        cwd = Path.cwd()
        if output_format != "json":
            print(f"   ⊙ No git root detected, but --project-id supplied — using cwd as anchor: {cwd}")
        return cwd

    import subprocess

    explicit_non_interactive = not interactive
    try:
        if interactive:
            response = input("Not in a git repository. Initialize one? [Y/n]: ").strip().lower()
            if response in ("n", "no"):
                print("Aborted. Run 'git init' manually, then try again.")
                return None
            subprocess.run(["git", "init"], check=True)
        elif output_format == "json" or explicit_non_interactive:
            subprocess.run(["git", "init"], capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        _report_git_init_failure(exc, output_format)
        return None

    git_root = get_git_root()
    if not git_root:
        if output_format == "json":
            print(json.dumps({"ok": False, "error": "Not in a git repository"}))
        else:
            print("Error: Not in a git repository. Run 'git init' first.")
    return git_root


def _check_already_initialized(config_path, args, output_format):
    """Check if Empirica is already initialized. Returns True if should abort."""
    if not config_path.exists() or getattr(args, "force", False):
        return False
    if output_format == "json":
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Empirica already initialized in this repo",
                    "hint": "Use --force to reinitialize",
                },
                indent=2,
            )
        )
    else:
        print("❌ Empirica already initialized in this repo")
        print(f"   Config found: {config_path}")
        print("\nTip: Use --force to reinitialize")
    return True


def _get_git_remote_url():
    """Get git remote URL for repos field. Returns URL string or None."""
    import subprocess

    try:
        result = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=5)
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def _derive_ai_id(git_root) -> str:
    """Derive the canonical ai_id from the project's basename.

    Strict-canonical convention: AIs are addressed by the **exact**
    directory basename — prefix kept. `empirica-cortex` stays
    `empirica-cortex`, not `cortex`. Short aliases live only in
    human-conversational layers (skills + system prompt); code paths
    use the full basename so cortex routing + ntfy event filtering
    line up.

    At project-init time project.yaml doesn't exist yet, so InstanceResolver
    falls through to basename derivation — same result as the prior local
    implementation, but routed through the single canonical resolver. See
    docs/architecture/AI_ID_AS_ANCHOR.md.

    Examples:
        ~/empirical-ai/empirica           → 'empirica'
        ~/empirical-ai/empirica-cortex    → 'empirica-cortex'
        ~/empirical-ai/empirica-extension → 'empirica-extension'
        ~/code/myproject                  → 'myproject'

    Cortex orchestration uses this id for target_claudes / source_claude
    routing. Peer AIs and the cockpit address each instance via this id.
    """
    from empirica.utils.session_resolver import InstanceResolver

    # Defensive: never return empty (would happen if basename is literally
    # 'empirica-' and resolver returns None or empty).
    return InstanceResolver.ai_id(project_path=str(git_root)) or "claude-code"


def _merge_preserving_existing(template: dict, existing: dict) -> dict:
    """Merge a freshly-built project.yaml ``template`` with an ``existing`` one so
    hand-set / provisioning-written keys survive a ``--force`` repair re-init.

    Existing values WIN; the template only fills in keys the existing config lacks.
    This is what stops ``project-init --force`` from silently clobbering curated
    identity fields (org_id, tenant_slug, canonical_seat, mesh_id_prefix, remotes,
    rich description). An empty ``existing`` (fresh init) returns the template as-is.
    """
    if not existing:
        return template
    return {**template, **existing}


def _build_project_config(config_input, git_root, git_url):
    """Build the project.yaml config dict from collected inputs."""
    from datetime import datetime

    project_name = config_input["project_name"]
    languages = config_input["languages"]
    if not languages:
        languages = _auto_detect_languages(git_root)

    project_config = {
        "version": "2.0",
        "name": project_name,
        "description": config_input["project_description"] or f"{project_name} project",
        "project_id": None,
        "ai_id": _derive_ai_id(git_root),
        "type": config_input["project_type"],
        "domain": config_input["project_domain"],
        "classification": config_input["classification"],
        "status": "active",
        "evidence_profile": config_input["evidence_profile"],
        "languages": languages,
        "tags": config_input["tags"],
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "created_by": os.environ.get("USER", "unknown"),
    }
    if git_url:
        project_config["repository"] = git_url
    project_config.update(
        {
            "contacts": [],
            "engagements": [],
            "edges": [],
            "beads": {"default_enabled": config_input["enable_beads"]},
            "subjects": {},
            "auto_detect": {"enabled": True, "method": "path_match"},
            "domain_config": {},
            "calibration_weights": _seed_calibration_weights(config_input["project_type"]),
        }
    )
    return project_config


def _create_semantic_index_template(git_root, project_name):
    """Create SEMANTIC_INDEX.yaml template. Returns path or None."""
    import yaml

    docs_dir = git_root / "docs"
    docs_dir.mkdir(exist_ok=True)
    semantic_index_path = docs_dir / "SEMANTIC_INDEX.yaml"

    template = {
        "version": "2.0",
        "project": project_name,
        "index": {
            "README.md": {
                "tags": ["readme", "getting-started"],
                "concepts": ["Project overview"],
                "questions": ["What is this project?"],
                "use_cases": ["new_user_onboarding"],
            }
        },
        "total_docs_indexed": 1,
        "last_updated": "2025-12-19",
        "coverage": {"core_concepts": 1, "quickstart": 0, "architecture": 0, "api": 0},
    }
    with open(semantic_index_path, "w") as f:
        yaml.dump(template, f, default_flow_style=False, sort_keys=False)
    return semantic_index_path


def _format_init_output(
    output_format,
    project_id,
    project_name,
    git_root,
    config_path,
    project_config_path,
    semantic_index_path,
    enable_beads,
    create_semantic_index,
    reused_existing,
):
    """Format and print the project-init output."""
    if output_format == "json":
        result = {
            "ok": True,
            "project_id": project_id,
            "project_name": project_name,
            "git_root": str(git_root),
            "reused_existing": reused_existing,
            "files_created": {
                "config": str(config_path),
                "project_config": str(project_config_path),
                "semantic_index": str(semantic_index_path) if semantic_index_path else None,
            },
            "beads_enabled": enable_beads,
            "message": "Empirica initialized successfully" + (" (reused existing project)" if reused_existing else ""),
        }
        print(json.dumps(result, indent=2))
    else:
        print("\n✅ Empirica initialized successfully!\n")
        print("📁 Files created:")
        print(f"   • {config_path.relative_to(git_root)}")
        print(f"   • {project_config_path.relative_to(git_root)}")
        if semantic_index_path:
            print(f"   • {semantic_index_path.relative_to(git_root)}")

        print(f"\n🆔 Project ID: {project_id}")
        print(f"📦 Project Name: {project_name}")
        if enable_beads:
            print("🔗 BEADS: Enabled by default")

        print("\n📋 Next steps:")
        if enable_beads:
            print("   1. Initialize BEADS issue tracking:")
            print("      bd init")
            print("   2. Create your first session:")
            print("      empirica session-create --ai-id myai")
            print("   3. Create goals (BEADS will auto-link):")
            print("      empirica goals-create --objective '...' --success-criteria '...'")
        else:
            print("   1. Create your first session:")
            print("      empirica session-create --ai-id myai")
            print("   2. Start working with epistemic tracking:")
            print("      empirica preflight-submit <assessment.json>")

        if create_semantic_index:
            print("\n📖 Semantic index template created!")
            print("   Edit docs/SEMANTIC_INDEX.yaml to add your documentation metadata")


def handle_project_init_command(args):
    """Handle project-init command - initialize Empirica in a new repo"""
    try:
        import yaml

        from empirica.config.path_resolver import create_default_config, ensure_empirica_structure
        from empirica.data.session_database import SessionDatabase

        explicit_non_interactive = getattr(args, "non_interactive", False)
        has_tty = sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else False
        output_format = getattr(args, "output", "default")
        interactive = not explicit_non_interactive and has_tty and output_format != "json"

        git_root = _ensure_git_root(interactive, output_format, args)
        if not git_root:
            return None

        config_path = git_root / ".empirica" / "config.yaml"
        if _check_already_initialized(config_path, args, output_format):
            return None

        if output_format != "json":
            print("🚀 Initializing Empirica in this repository...")
            print(f"   Git root: {git_root}\n")

        ensure_empirica_structure()
        create_default_config()

        if interactive and output_format != "json":
            config_input = _collect_project_config_interactive(git_root)
        else:
            config_input = _collect_project_config_from_args(args, git_root)

        git_url = _get_git_remote_url()
        project_config_path = git_root / ".empirica" / "project.yaml"

        # Preserve hand-set / provisioning-written fields on --force re-init (a repair,
        # not a fresh init). _build_project_config produces a generic scaffold; blindly
        # writing it silently clobbers curated identity fields (org_id, tenant_slug,
        # canonical_seat, mesh_id_prefix, remotes, rich description). Merge so existing
        # values WIN and the template only fills genuinely-missing keys. Fresh inits have
        # no existing project.yaml, so this is a no-op there.
        existing_config = {}
        if project_config_path.exists():
            try:
                with open(project_config_path) as f:
                    existing_config = yaml.safe_load(f) or {}
            except Exception:
                existing_config = {}

        project_config = _merge_preserving_existing(
            _build_project_config(config_input, git_root, git_url), existing_config
        )

        with open(project_config_path, "w") as f:
            yaml.dump(project_config, f, default_flow_style=False, sort_keys=False)

        db_path = git_root / ".empirica" / "sessions" / "sessions.db"
        db = SessionDatabase(db_path=str(db_path))
        reused_existing = False

        project_name = config_input["project_name"]
        project_description = config_input["project_description"]
        project_id = _resolve_or_create_project(
            db,
            args,
            project_name,
            project_description,
            project_config_path,
            git_url,
            config_input["project_type"],
            config_input["tags"],
            output_format,
        )

        project_config["project_id"] = project_id
        with open(project_config_path, "w") as f:
            yaml.dump(project_config, f, default_flow_style=False, sort_keys=False)
        db.close()

        _register_project_in_workspace(
            project_id, project_name, project_description, git_root, git_url, project_config, output_format
        )

        semantic_index_path = None
        if config_input["create_semantic_index"]:
            semantic_index_path = _create_semantic_index_template(git_root, project_name)

        _format_init_output(
            output_format,
            project_id,
            project_name,
            git_root,
            config_path,
            project_config_path,
            semantic_index_path,
            config_input["enable_beads"],
            config_input["create_semantic_index"],
            reused_existing,
        )

        return {
            "ok": True,
            "project_id": project_id,
            "project_name": project_name,
            "git_root": str(git_root),
        }

    except Exception as e:
        from ..cli_utils import handle_cli_error

        handle_cli_error(e, "Project init", getattr(args, "verbose", False))
        return None


def _seed_calibration_weights(project_type: str) -> dict:
    """Generate default per-phase per-vector calibration weights for a project type.

    These are Tier 2 static defaults seeded at project-init. They provide
    sensible starting weights based on domain and phase until the dynamic
    EMA adaptation (empirica-extended) refines them from actual calibration gaps.

    Based on evidence from 478 grounded verifications showing:
    - Noetic phase: generally well-calibrated, execution vectors irrelevant
    - Praxic phase: systematically overestimates know/context/completion,
      underestimates uncertainty/impact
    """
    # Map project_type to calibration domain
    _TYPE_TO_DOMAIN = {
        "software": "software",
        "content": "consulting",
        "research": "research",
        "data": "research",
        "design": "consulting",
        "operations": "operations",
        "strategic": "consulting",
        "engagement": "consulting",
        "legal": "operations",
    }
    domain = _TYPE_TO_DOMAIN.get(project_type, "default")

    # Base noetic weights: investigation phase — execution vectors low
    noetic_base = {
        "know": 1.0,
        "context": 0.8,
        "signal": 0.9,
        "uncertainty": 1.0,
        "do": 0.3,
        "change": 0.2,
        "state": 0.3,
        "completion": 0.5,
        "impact": 0.3,
        "clarity": 0.7,
        "coherence": 0.6,
        "density": 0.4,
    }

    # Base praxic weights: from calibration gap evidence
    praxic_base = {
        "know": 1.0,
        "completion": 1.0,
        "context": 1.0,
        "impact": 0.9,
        "uncertainty": 0.8,
        "change": 0.8,
        "do": 0.7,
        "clarity": 0.7,
        "coherence": 0.7,
        "signal": 0.4,
        "density": 0.4,
        "state": 0.6,
    }

    # Domain-specific adjustments
    if domain == "software":
        praxic_base["change"] = 0.9  # code changes matter more
        praxic_base["state"] = 0.7  # environment awareness
    elif domain == "consulting":
        praxic_base["clarity"] = 0.9  # communication clarity critical
        noetic_base["context"] = 1.0  # client context essential
    elif domain == "research":
        noetic_base["signal"] = 1.0  # signal detection critical
        praxic_base["completion"] = 0.6  # open-ended work
    elif domain == "operations":
        praxic_base["state"] = 0.9  # system state awareness
        praxic_base["change"] = 0.9  # change tracking critical

    return {"noetic": noetic_base, "praxic": praxic_base}


def _auto_detect_languages(git_root: Path) -> list:
    """Auto-detect programming languages from build/config files."""
    detected = []
    indicators = {
        "python": ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"],
        "typescript": ["tsconfig.json"],
        "javascript": ["package.json"],
        "go": ["go.mod"],
        "rust": ["Cargo.toml"],
        "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
        "ruby": ["Gemfile"],
        "php": ["composer.json"],
        "c#": ["*.csproj", "*.sln"],
        "swift": ["Package.swift"],
        "r": ["DESCRIPTION", ".Rproj"],
    }
    for lang, files in indicators.items():
        for pattern in files:
            if "*" in pattern:
                if list(git_root.glob(pattern)):
                    detected.append(lang)
                    break
            elif (git_root / pattern).exists():
                detected.append(lang)
                break
    # Don't duplicate: if typescript detected, don't also add javascript from package.json
    if "typescript" in detected and "javascript" in detected:
        detected.remove("javascript")
    return detected
