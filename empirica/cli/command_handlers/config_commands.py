"""
Configuration Commands - CLI commands for managing Empirica configuration

Uses MCO (Meta-Agent Configuration Object) loader for AI behavioral config.
Legacy modality_switcher config is deprecated and no longer available.
"""

import json

import yaml

# MCO config loader (always available)
from empirica.config.mco_loader import get_mco_config

from ..cli_utils import handle_cli_error


def handle_config_command(args):
    """Unified config handler (consolidates all 5 config commands)"""
    # Route based on flags and arguments
    if getattr(args, "init", False):
        return handle_config_init_command(args)
    elif getattr(args, "validate", False):
        return handle_config_validate_command(args)
    elif args.key and args.value:
        # Set: config KEY VALUE
        return handle_config_set_command(args)
    elif args.key:
        # Get: config KEY
        return handle_config_get_command(args)
    else:
        # Show: config (no args)
        return handle_config_show_command(args)


def handle_config_init_command(args):
    """
    Initialize Empirica configuration.

    MCO configuration is read from YAML files in the empirica/config/mco/ directory.
    """
    try:
        print("\n🔧 Empirica Configuration")
        print("=" * 70)

        mco = get_mco_config()

        print("\n📂 MCO Configuration Location:")
        print(f"   {mco.config_dir}")

        print("\n📝 Configuration files:")
        config_files = [
            ("model_profiles.yaml", "Model-specific bias corrections"),
            ("personas.yaml", "Investigation budgets and epistemic priors"),
            ("cascade_styles.yaml", "Threshold profiles"),
            ("epistemic_conduct.yaml", "Bidirectional accountability triggers"),
            ("protocols.yaml", "Tool schemas"),
        ]
        for filename, description in config_files:
            filepath = mco.config_dir / filename
            exists = "✅" if filepath.exists() else "❌"
            print(f"   {exists} {filename} - {description}")

        print("\n💡 To customize configuration:")
        print(f"   Edit YAML files in: {mco.config_dir}")
        print("\n💡 Quick commands:")
        print("   empirica config           - View current configuration")
        print("   empirica config --validate - Validate configuration")

    except Exception as e:
        handle_cli_error(e, "Config Init", getattr(args, "verbose", False))


def handle_config_show_command(args):
    """
    Show current Empirica configuration.

    Displays MCO (Meta-Agent Configuration Object) configuration.
    """
    try:
        print("\n📋 Empirica Configuration")
        print("=" * 70)

        # Use MCO loader
        mco = get_mco_config()

        # Determine output format
        output_format = getattr(args, "format", "yaml")
        section = getattr(args, "section", None)

        # Build config dict
        config = {
            "model_profiles": mco.model_profiles,
            "personas": mco.personas,
            "epistemic_conduct": mco.epistemic_conduct,
            "ask_before_investigate": mco.ask_before_investigate,
            "protocols": mco.protocols,
        }

        # Get config (full or section)
        if section:
            if section in config:
                config = config[section]
                print(f"\nSection: {section}")
            else:
                print(f"❌ Section not found: {section}")
                print(f"   Available sections: {', '.join(config.keys())}")
                return
        else:
            print("\nMCO Configuration (Model Profiles, Personas, Epistemic Conduct)")

        print("=" * 70)

        # Display config
        if output_format == "json":
            print(json.dumps(config, indent=2))
        else:  # yaml
            print(yaml.dump(config, default_flow_style=False, sort_keys=False))

        # Show config sources
        if not section:
            print("\n" + "=" * 70)
            print("📂 Configuration Sources:")
            print(f"   MCO directory: {mco.config_dir}")
            print("   Files loaded:")
            print("     • model_profiles.yaml - Bias corrections per model")
            print("     • personas.yaml - Investigation budgets")
            print("     • epistemic_conduct.yaml - Bidirectional accountability")
            print("     • ask_before_investigate.yaml - Query heuristics")
            print("     • protocols.yaml - Tool schemas")

    except Exception as e:
        handle_cli_error(e, "Config Show", getattr(args, "verbose", False))


def _validate_model_profiles(mco):
    """Validate model profiles in MCO config. Returns (issues, warnings)."""
    warnings = []
    print("\n📊 Checking Model Profiles...")
    if not mco.model_profiles:
        warnings.append("No model profiles configured")
    else:
        for name, profile in mco.model_profiles.items():
            bias = profile.get("bias_profile", {})
            if not bias:
                warnings.append(f"{name}: Missing bias_profile")
            else:
                print(f"   ✅ {name}: Valid (uncertainty_awareness={bias.get('uncertainty_awareness', 'N/A')})")
    return warnings


def _validate_personas(mco):
    """Validate personas in MCO config. Returns warnings list."""
    warnings = []
    print("\n👤 Checking Personas...")
    if not mco.personas:
        warnings.append("No personas configured")
    else:
        for name, persona in mco.personas.items():
            investigation = persona.get("investigation_style", {})
            if not investigation:
                warnings.append(f"{name}: Missing investigation_style")
            else:
                print(f"   ✅ {name}: Valid (max_rounds={investigation.get('max_rounds', 'N/A')})")
    return warnings


def handle_config_validate_command(args):
    """
    Validate Empirica configuration.

    Checks MCO files for errors and warns about potential issues.
    """
    try:
        print("\n🔍 Validating Empirica Configuration")
        print("=" * 70)

        mco = get_mco_config()
        issues = []
        warnings = []

        warnings.extend(_validate_model_profiles(mco))
        warnings.extend(_validate_personas(mco))

        print("\n📜 Checking Epistemic Conduct...")
        if not mco.epistemic_conduct:
            warnings.append("No epistemic conduct configuration")
        else:
            print("   ✅ Epistemic conduct loaded")

        print("\n📂 Checking MCO Files...")
        required_files = ["model_profiles.yaml", "personas.yaml", "epistemic_conduct.yaml"]
        for filename in required_files:
            filepath = mco.config_dir / filename
            if filepath.exists():
                print(f"   ✅ {filename}")
            else:
                issues.append(f"Missing MCO file: {filename}")

        print("\n" + "=" * 70)
        if not issues and not warnings:
            print("✅ Configuration is valid with no issues")
        else:
            if issues:
                print(f"❌ Found {len(issues)} issue(s):")
                for issue in issues:
                    print(f"   • {issue}")
            if warnings:
                print(f"\n⚠️  Found {len(warnings)} warning(s):")
                for warning in warnings:
                    print(f"   • {warning}")
        print("=" * 70)

    except Exception as e:
        handle_cli_error(e, "Config Validate", getattr(args, "verbose", False))


def handle_config_get_command(args):
    """
    Get a specific configuration value.

    Uses dot notation to access nested values from MCO config.
    """
    try:
        key = args.key
        mco = get_mco_config()

        # Build config dict
        config = {
            "model_profiles": mco.model_profiles,
            "personas": mco.personas,
            "epistemic_conduct": mco.epistemic_conduct,
            "ask_before_investigate": mco.ask_before_investigate,
            "protocols": mco.protocols,
        }

        # Navigate to value using dot notation
        keys = key.split(".")
        value = config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                print(f"❌ Configuration key not found: {key}")
                return 1

        print(f"✅ {key}: {value}")

    except Exception as e:
        handle_cli_error(e, "Config Get", getattr(args, "verbose", False))


def handle_config_set_command(args):
    """
    Set a configuration value.

    Note: MCO configuration is read-only via CLI. Edit YAML files directly.
    """
    try:
        key = args.key
        value = args.value

        print("\n⚠️  MCO Configuration is Read-Only via CLI")
        print("=" * 70)

        mco = get_mco_config()

        print(f"\nTo set '{key}' = '{value}':")
        print("\n1. Edit the appropriate YAML file in:")
        print(f"   {mco.config_dir}")

        # Suggest which file to edit based on key
        if key.startswith("model_profiles"):
            print("\n2. Edit: model_profiles.yaml")
        elif key.startswith("personas"):
            print("\n2. Edit: personas.yaml")
        elif key.startswith("epistemic_conduct"):
            print("\n2. Edit: epistemic_conduct.yaml")
        elif key.startswith("ask_before_investigate"):
            print("\n2. Edit: ask_before_investigate.yaml")
        elif key.startswith("protocols"):
            print("\n2. Edit: protocols.yaml")
        else:
            print("\n2. Check available sections: model_profiles, personas, epistemic_conduct, protocols")

        print("\n3. Restart your session to load the new configuration")

    except Exception as e:
        handle_cli_error(e, "Config Set", getattr(args, "verbose", False))
