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


def add_provision_practice_parsers(subparsers):
    """Register the ``provision-practice`` verb on the top-level subparsers."""
    p = subparsers.add_parser(
        "provision-practice",
        help="Create + register a new local practice/practitioner in one command",
        description="""
Idempotent single-practice onboarding — the local, no-spec.yaml sibling
of mesh-support's bulk provision-practices.py tool. Safe to re-run: each
step no-ops cleanly if already done.

Steps: mkdir <base-path>/<name> -> project-init -> patch
.empirica/project.yaml (ai_id/tenant/org/substrate) -> project-register
(cortex) -> optional forgejo backup remote wiring.

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
        default="~/empirical-ai",
        help="Parent directory the new practice is created under (default: ~/empirical-ai)",
    )
    p.add_argument("--tenant", help="Tenant slug (default: inferred from cwd's project.yaml)")
    p.add_argument("--org", help="Org slug (default: inferred from cwd's project.yaml)")
    p.add_argument("--substrate", default="cortex", help="Substrate value written to project.yaml (default: cortex)")
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
    p.add_argument("--dry-run", action="store_true", help="Print planned actions without executing")
    p.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
