"""Configuration command parsers."""


def add_config_parsers(subparsers):
    """Add configuration command parsers"""
    # Unified config command (consolidates config-init, config-show, config-validate, config-get, config-set)
    config_parser = subparsers.add_parser("config", help="Configuration management")
    config_parser.add_argument(
        "key", nargs="?", help="Configuration key (dot notation, e.g., routing.default_strategy)"
    )
    config_parser.add_argument("value", nargs="?", help="Value to set (if key provided)")
    config_parser.add_argument("--init", action="store_true", help="Initialize configuration (replaces config-init)")
    config_parser.add_argument(
        "--validate", action="store_true", help="Validate configuration (replaces config-validate)"
    )
    config_parser.add_argument("--section", help="Show specific section (e.g., routing, adapters)")
    config_parser.add_argument("--output", choices=["yaml", "json"], default="yaml", help="Output format")
    config_parser.add_argument("--verbose", action="store_true", help="Show detailed output")

    # REMOVED: config-init, config-show, config-validate, config-get, config-set
    # Use: config --init, config (no args), config --validate, config KEY, config KEY VALUE
