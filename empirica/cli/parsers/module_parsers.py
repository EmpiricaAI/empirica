"""Parsers for the ``empirica module`` command group (practice-module system).

``validate`` ships first (the piece that unblocks practices writing their
``module.yaml``). ``fetch`` (auth-gated artifact pull) and ``provision``
(plugin-layer install) land in later legs of the module-build SER.
"""

from __future__ import annotations


def add_module_parsers(subparsers):
    """Register the ``module`` command group."""
    module_root = subparsers.add_parser(
        "module",
        help="Practice-module manifest tooling (validate; fetch/provision land in later legs)",
    )
    module_subs = module_root.add_subparsers(dest="module_action", metavar="action")

    validate = module_subs.add_parser(
        "validate",
        help="Validate a module.yaml manifest (structural; fail-fast before install)",
    )
    validate.add_argument("path", help="Path to the module.yaml to validate")
    validate.add_argument(
        "--output",
        choices=("json", "text"),
        default="json",
        help="Output format (default: json)",
    )

    fetch = module_subs.add_parser(
        "fetch",
        help="Stage a module's distribution artifacts (auth-gated pre-step before seat + provision)",
    )
    fetch.add_argument("path", help="Path to the module.yaml")
    fetch.add_argument("--dry-run", action="store_true", help="Compute the fetch plan; write nothing")
    fetch.add_argument("--registry", help="Plugin-archive registry base URL (default: $EMPIRICA_MODULE_REGISTRY)")
    fetch.add_argument("--index-url", help="pip index URL for python_packages (default: $EMPIRICA_MODULE_INDEX_URL)")
    fetch.add_argument("--staging-root", help="Override the staging root (default: ~/.empirica/module_staging)")
    fetch.add_argument("--output", choices=("json", "text"), default="json", help="Output format (default: json)")

    provision = module_subs.add_parser(
        "provision",
        help="Plugin layer: place files, register automations, grant ntfy topics, check env",
    )
    provision.add_argument("path", help="Path to the module.yaml")
    provision.add_argument("--dry-run", action="store_true", help="Compute the provision plan; perform nothing")
    provision.add_argument("--plugin-root", help="Override plugin root (default: ~/.claude/plugins/local)")
    provision.add_argument("--staging-root", help="Staging root holding fetched artifacts")
    provision.add_argument("--cortex-url", help="Cortex base URL for ntfy ACL grants (default: credentials.yaml)")
    provision.add_argument("--org", help="Org slug for ntfy grant users (e.g. empirica); topics skip without it")
    provision.add_argument("--tenant", help="Tenant slug for the subscriber grant user")
    provision.add_argument("--output", choices=("json", "text"), default="json", help="Output format (default: json)")
