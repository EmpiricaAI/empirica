"""Domain registry + resolve command parsers."""


def add_resolve_parser(subparsers):
    """Add unified resolve command parser."""
    rp = subparsers.add_parser("resolve", help="Resolve any artifact by ID (auto-detects type)")
    rp.add_argument("artifact_id", help="Artifact ID or prefix (e.g., first 8 chars)")
    rp.add_argument("--resolved-by", default=None, help="Resolution reason")
    rp.add_argument("--output", choices=["text", "json"], default="json")


def add_domain_parsers(subparsers):
    """Add domain registry command parsers."""

    # domain-validate
    dv = subparsers.add_parser("domain-validate", help="Validate all YAML domain files")
    dv.add_argument("--output", choices=["text", "json"], default="text")
