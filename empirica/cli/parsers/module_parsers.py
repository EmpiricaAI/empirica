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
