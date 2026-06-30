"""
Version Consistency Tests

Ensures all version-bearing files in the project agree on the current version.
Catches the class of bug where release.py sweeps some files but misses others,
or where the README badge shows a stale version (the v1.6.12 PyPI badge bug).
"""

import json
import re
from pathlib import Path

import pytest


def _find_project_root() -> Path:
    """Walk up from this test file until we find pyproject.toml."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    raise FileNotFoundError("Could not find project root (no pyproject.toml found)")


PROJECT_ROOT = _find_project_root()


@pytest.fixture(scope="module")
def canonical_version() -> str:
    """Read the version from the root pyproject.toml (single source of truth)."""
    pyproject = PROJECT_ROOT / "pyproject.toml"
    content = pyproject.read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    assert match, "Could not find version in pyproject.toml"
    return match.group(1)


class TestVersionConsistency:
    """All version-bearing files must agree with pyproject.toml."""

    def test_init_version_matches(self, canonical_version: str) -> None:
        """pyproject.toml version matches empirica/__init__.py __version__."""
        init_file = PROJECT_ROOT / "empirica" / "__init__.py"
        content = init_file.read_text()
        match = re.search(r'^__version__\s*=\s*"([^"]+)"', content, re.MULTILINE)
        assert match, "Could not find __version__ in empirica/__init__.py"
        assert match.group(1) == canonical_version, (
            f"empirica/__init__.py __version__ is {match.group(1)!r}, "
            f"expected {canonical_version!r} (from pyproject.toml)"
        )

    def test_mcp_pyproject_version_matches(self, canonical_version: str) -> None:
        """pyproject.toml version matches empirica-mcp/pyproject.toml."""
        mcp_pyproject = PROJECT_ROOT / "empirica-mcp" / "pyproject.toml"
        content = mcp_pyproject.read_text()
        match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        assert match, "Could not find version in empirica-mcp/pyproject.toml"
        assert match.group(1) == canonical_version, (
            f"empirica-mcp/pyproject.toml version is {match.group(1)!r}, "
            f"expected {canonical_version!r} (from pyproject.toml)"
        )

    def test_mcp_core_dep_pinned_to_version(self, canonical_version: str) -> None:
        """empirica-mcp must pin its core dep with == to the current version, not
        a loose >=. A loose constraint let `pipx upgrade empirica-mcp` bump the
        wrapper while leaving empirica core stale (the version-drift footgun)."""
        mcp_pyproject = PROJECT_ROOT / "empirica-mcp" / "pyproject.toml"
        content = mcp_pyproject.read_text()
        # Must be an exact pin on the matching version — reject >=, ~=, etc.
        assert re.search(rf'"empirica=={re.escape(canonical_version)}"', content), (
            "empirica-mcp/pyproject.toml must pin 'empirica==" + canonical_version + "' "
            "(exact, matching core) to prevent wrapper/core version drift"
        )
        assert not re.search(r'"empirica>=', content), (
            "empirica-mcp/pyproject.toml has a loose 'empirica>=' constraint — "
            "use '==' so the wrapper can't drift from core"
        )

    def test_plugin_json_version_matches(self, canonical_version: str) -> None:
        """pyproject.toml version matches plugin.json version."""
        plugin_json = (
            PROJECT_ROOT / "empirica" / "plugins" / "claude-code-integration" / ".claude-plugin" / "plugin.json"
        )
        data = json.loads(plugin_json.read_text())
        assert "version" in data, "Could not find 'version' key in plugin.json"
        assert data["version"] == canonical_version, (
            f"plugin.json version is {data['version']!r}, expected {canonical_version!r} (from pyproject.toml)"
        )

    def test_install_sh_version_matches(self, canonical_version: str) -> None:
        """pyproject.toml version matches install.sh PLUGIN_VERSION."""
        install_sh = PROJECT_ROOT / "empirica" / "plugins" / "claude-code-integration" / "install.sh"
        content = install_sh.read_text()
        match = re.search(r'^PLUGIN_VERSION="([^"]+)"', content, re.MULTILINE)
        assert match, "Could not find PLUGIN_VERSION in install.sh"
        assert match.group(1) == canonical_version, (
            f"install.sh PLUGIN_VERSION is {match.group(1)!r}, expected {canonical_version!r} (from pyproject.toml)"
        )

    def test_readme_badge_version_matches(self, canonical_version: str) -> None:
        """README.md shields.io badge contains the correct version."""
        readme = PROJECT_ROOT / "README.md"
        content = readme.read_text()
        match = re.search(r"version-([0-9]+\.[0-9]+\.[0-9]+)-blue", content)
        assert match, "Could not find shields.io version badge (pattern: version-X.Y.Z-blue) in README.md"
        assert match.group(1) == canonical_version, (
            f"README.md badge version is {match.group(1)!r}, expected {canonical_version!r} (from pyproject.toml)"
        )
