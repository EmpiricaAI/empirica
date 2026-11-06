"""Argparse parser for ``empirica provision-practice <name>``.

Lightweight, local-only single-practitioner onboarding: create the
project directory, project-init, patch ai_id/tenant/org/substrate,
register with cortex, optionally wire a forgejo backup remote. The
one-click-setup counterpart to mesh-support's spec.yaml-driven bulk
``provision-practices.py`` (which composes this same core sequence for
N practitioners, remote hosts, and full env-capture registration).
"""

from __future__ import annotations

import os

from .checkpoint_parsers import _project_type_arg


def add_provision_practice_parsers(subparsers):
    """Register the ``provision-practice`` verb on the top-level subparsers."""
    p = subparsers.add_parser(
        "provision-practice",
        help="Create + register a new local practice/practitioner in one command",
        description="""
Idempotent single-practice onboarding — the local, no-spec.yaml sibling
of mesh-support's bulk provision-practices.py tool. Safe to re-run: each
step no-ops cleanly if already done.

Steps: mkdir <base-path>/<name> -> project-init (with --type,
--description and --domain when given) -> patch .empirica/project.yaml
(ai_id/tenant/org/substrate) -> .gitignore that ignores .empirica except
project.yaml -> project-register (cortex) -> optional forgejo backup
remote wiring.

--tenant/--org default to whatever's already set in the CURRENT
directory's .empirica/project.yaml (so running this from inside an
existing practice provisions a sibling under the same tenant/org).
Falls back to erroring with a clear message if neither is available.
        """,
    )
    p.add_argument("name", metavar="NAME", help="ai_id / directory name for the new practice")
    p.add_argument(
        "--base-path",
        metavar="PATH",
        default="~/empirica",
        help="Parent directory the new practice is created under (default: ~/empirica)",
    )
    p.add_argument(
        "--type",
        type=_project_type_arg,
        metavar="TYPE",
        help="What the new practice is: a project type (`empirica project-init --help` lists them with their meanings). "
        "Applied when the practice is created; left unset it is stored as unclassified",
    )
    p.add_argument(
        "--description",
        help="One sentence on what the practice is for. Applied when the practice is created; without it the "
        "description is '<name> project'",
    )
    p.add_argument(
        "--domain",
        help="The practice's domain: the name of a domain file (default, research, operations, consulting, marketing, "
        "docs, cybersec, remote-ops, or one of your own). A transaction that declares no domain starts from it. "
        "Applied when the practice is created; an unknown name is refused before anything is created",
    )
    p.add_argument("--tenant", help="Tenant slug (default: inferred from cwd's project.yaml)")
    p.add_argument("--org", help="Org slug (default: inferred from cwd's project.yaml)")
    p.add_argument(
        "--substrate",
        default=None,
        help="Substrate value written to project.yaml: cortex, git or local (default: local with --no-cortex, else cortex)",
    )
    p.add_argument(
        "--forgejo-owner",
        metavar="OWNER",
        help="If set, wire a forgejo backup remote under this owner (git remote add + sync-config + sync-push)",
    )
    p.add_argument(
        "--forgejo-host",
        metavar="SSH_URL",
        default=os.environ.get("EMPIRICA_FORGEJO_HOST"),
        help="Forgejo SSH remote for the backup, e.g. ssh://git@host:port. "
        "Falls back to the EMPIRICA_FORGEJO_HOST env var; required when "
        "--forgejo-owner is set (no built-in default).",
    )
    p.add_argument(
        "--no-cortex",
        action="store_true",
        help="Skip cortex registration (local writes only) — passed through to project-register",
    )
    p.add_argument(
        "--cockpit-profile",
        metavar="NAME",
        help="Also add this practice to the cockpit profile NAME (~/.empirica/cockpit/config-NAME.yaml, "
        "created if absent, with a TUI window) and print `empirica cockpit launch --profile NAME`. "
        "Run once per practice with the same NAME to build one layout.",
    )
    p.add_argument(
        "--launch",
        metavar="CMD",
        help="What the cockpit pane runs (default: claude). Needs --cockpit-profile. For a codex or ecodex practice: --launch ecodex",
    )
    p.add_argument(
        "--resume",
        metavar="CMD",
        help="What `cockpit refresh` runs to bring a dead pane back with its conversation. Needs --cockpit-profile. "
        "Default: the launch with --continue for claude, `<prog> resume --last` for codex and ecodex (flag-only launches; give --resume for a launch that ends in a prompt or is wrapped in a shell)",
    )
    p.add_argument(
        "--second-pane",
        metavar="CMD",
        help="Also run CMD (for example ecodex) as a second pane in this practice's window: the same directory, its own "
        "identity (<name>-<program>) and its own conversation. Needs --cockpit-profile. For a person who runs two harnesses",
    )
    p.add_argument("--dry-run", action="store_true", help="Print planned actions (to stderr) without executing")
    p.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
