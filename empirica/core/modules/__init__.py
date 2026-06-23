"""Empirica practice-module system.

A practice-module bundles everything a practice needs to install as a
mesh-coordinated practitioner. Installation is two layers over one shared
declaration (``module.yaml``):

- **seat layer** → autonomy's ``install_seat.py`` (the CLAUDE.md managed-block)
- **plugin layer** → ``empirica module provision`` (skills/agents/automations/topics/env)

This package holds the manifest schema + validator. The MIT core reads the
manifest declaratively — it never imports a module's code to understand what
the manifest would install.
"""

from empirica.core.modules.manifest import (
    ManifestError,
    ModuleManifest,
    load_manifest,
    validate_manifest_file,
)

__all__ = [
    "ManifestError",
    "ModuleManifest",
    "load_manifest",
    "validate_manifest_file",
]
