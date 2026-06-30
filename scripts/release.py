#!/usr/bin/env python3
"""
Automated Release Script for Empirica
Single source of truth: pyproject.toml version

Usage:
    python scripts/release.py --dry-run                           # Preview full release
    python scripts/release.py                                     # Execute full release
    python scripts/release.py --version-only --old-version 1.5.6  # Update versions only
    python scripts/release.py --old-version 1.5.6                 # Full release with sweep
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# ANSI colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"

# Chocolatey package ownership — kars85 is the chocolatey.org account that
# owns the empirica listing. See .nuspec <owners> field; release flow asserts
# they match before the choco push step so a manual nuspec edit doesn't
# silently change ownership.
CHOCOLATEY_OWNER = "kars85"


def log(msg: str, color: str = RESET):
    print(f"{color}{msg}{RESET}")


def error(msg: str):
    log(f"❌ ERROR: {msg}", RED)
    sys.exit(1)


def warning(msg: str):
    log(f"⚠️  WARNING: {msg}", YELLOW)


def success(msg: str):
    log(f"✅ {msg}", GREEN)


def info(msg: str):
    log(f"ℹ️  {msg}", BLUE)


class ReleaseManager:
    def __init__(self, dry_run: bool = False, old_version: Optional[str] = None):
        self.dry_run = dry_run
        self.repo_root = Path(__file__).parent.parent
        self.version: Optional[str] = None
        self.old_version: Optional[str] = old_version
        self.tarball_sha256: Optional[str] = None

    def read_version(self) -> str:
        """Read version from pyproject.toml"""
        pyproject_path = self.repo_root / "pyproject.toml"
        if not pyproject_path.exists():
            error(f"pyproject.toml not found at {pyproject_path}")

        content = pyproject_path.read_text()
        match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        if not match:
            error("Could not find version in pyproject.toml")

        version = match.group(1)
        info(f"Version from pyproject.toml: {version}")
        return version

    def calculate_sha256(self) -> str:
        """Calculate SHA256 of the tarball"""
        tarball_pattern = f"empirica-{self.version}.tar.gz"
        dist_dir = self.repo_root / "dist"
        tarball = dist_dir / tarball_pattern

        if not tarball.exists():
            if self.dry_run:
                info(f"Tarball not found (dry run): {tarball}")
                return "0" * 64
            error(f"Tarball not found: {tarball}")

        sha256 = hashlib.sha256()
        with open(tarball, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)

        sha256_hex = sha256.hexdigest()
        info(f"Tarball SHA256: {sha256_hex}")
        return sha256_hex

    def update_homebrew_formula(self):
        """Update Homebrew formula with new version and SHA256"""
        formula_path = self.repo_root / "packaging/homebrew/empirica.rb"
        if not formula_path.exists():
            warning(f"Homebrew formula not found: {formula_path}")
            return

        content = formula_path.read_text()

        # Update URL — handle both PyPI and GitHub release URL formats
        url_pattern = r'url "https://[^"]+/empirica-[^"]+\.tar\.gz"'
        new_url = f'url "https://files.pythonhosted.org/packages/source/e/empirica/empirica-{self.version}.tar.gz"'
        content = re.sub(url_pattern, new_url, content)

        # Update assert_match version
        assert_pattern = r'assert_match "[^"]+", shell_output'
        new_assert = f'assert_match "{self.version}", shell_output'
        content = re.sub(assert_pattern, new_assert, content)

        # Update SHA256
        sha_pattern = r'sha256 "[a-f0-9]{64}"'
        new_sha = f'sha256 "{self.tarball_sha256}"'
        content = re.sub(sha_pattern, new_sha, content)

        if not self.dry_run:
            formula_path.write_text(content)
            success(f"Updated Homebrew formula: {formula_path}")
        else:
            info(f"Would update Homebrew formula: {formula_path}")

    def update_homebrew_tap(self):
        """Copy updated formula to the Homebrew tap repo and push"""
        log("\n" + "="*60)
        log("🍺 Updating Homebrew tap")
        log("="*60)

        local_formula = self.repo_root / "packaging/homebrew/empirica.rb"
        if not local_formula.exists():
            warning(f"Local formula not found: {local_formula}")
            warning("Skipping homebrew tap update — compliance release_chain check will surface this on next run.")
            return

        # Look for tap repo in common locations
        tap_candidates = [
            self.repo_root.parent / "homebrew-tap",          # sibling dir
            Path.home() / "empirical-ai" / "homebrew-tap",   # home dir
        ]

        info(f"Searching for tap repo (looking for {len(tap_candidates)} candidate paths)")
        tap_repo = None
        for candidate in tap_candidates:
            marker = candidate / "empirica.rb"
            if marker.exists():
                tap_repo = candidate
                info(f"  ✓ found at: {tap_repo}")
                break
            else:
                info(f"  ✗ not at:   {candidate} (no empirica.rb)")

        if tap_repo is None:
            warning("Homebrew tap repo not found. Manual step needed:")
            warning(f"  cp {local_formula} <your-tap-repo>/empirica.rb")
            warning("  cd <your-tap-repo> && git commit -am 'Update empirica to {self.version}' && git push")
            warning("Compliance release_chain check will surface this gap until republished.")
            return

        tap_formula = tap_repo / "empirica.rb"

        if not self.dry_run:
            shutil.copy2(local_formula, tap_formula)
            success(f"Copied formula to {tap_formula}")

            # Commit and push
            self.run_command(["git", "add", "empirica.rb"], cwd=str(tap_repo))
            self.run_command([
                "git", "commit", "-m",
                f"Update empirica to {self.version}"
            ], cwd=str(tap_repo), check=False)
            self.run_command(["git", "push"], cwd=str(tap_repo))
            success(f"Homebrew tap updated and pushed: {tap_repo}")
        else:
            info(f"Would copy {local_formula} → {tap_formula}")
            info(f"Would commit and push in {tap_repo}")

    def update_dockerfile(self):
        """Update Dockerfile with new version"""
        dockerfile_path = self.repo_root / "Dockerfile"
        if not dockerfile_path.exists():
            warning(f"Dockerfile not found: {dockerfile_path}")
            return

        content = dockerfile_path.read_text()

        # Update version label
        content = re.sub(
            r'LABEL version="[^"]+"',
            f'LABEL version="{self.version}"',
            content
        )

        # Update wheel filename in COPY
        content = re.sub(
            r'COPY dist/empirica-[^-]+-py3-none-any\.whl',
            f'COPY dist/empirica-{self.version}-py3-none-any.whl',
            content
        )

        # Update wheel filename in RUN pip install
        content = re.sub(
            r'/tmp/empirica-[^-]+-py3-none-any\.whl',
            f'/tmp/empirica-{self.version}-py3-none-any.whl',
            content,
            count=2  # Both COPY and RUN lines
        )

        if not self.dry_run:
            dockerfile_path.write_text(content)
            success(f"Updated Dockerfile: {dockerfile_path}")
        else:
            info(f"Would update Dockerfile: {dockerfile_path}")

    def update_chocolatey_nuspec(self):
        """Update Chocolatey nuspec with new version"""
        nuspec_path = self.repo_root / "packaging/chocolatey/empirica.nuspec"
        if not nuspec_path.exists():
            warning(f"Chocolatey nuspec not found: {nuspec_path}")
            return

        content = nuspec_path.read_text()

        # Update version
        content = re.sub(
            r'<version>[^<]+</version>',
            f'<version>{self.version}</version>',
            content
        )

        if not self.dry_run:
            nuspec_path.write_text(content)
            success(f"Updated Chocolatey nuspec: {nuspec_path}")
        else:
            info(f"Would update Chocolatey nuspec: {nuspec_path}")

    def update_chocolatey_checksum(self):
        """Update SHA256 checksum in Chocolatey install script"""
        install_ps1 = self.repo_root / "packaging/chocolatey/tools/chocolateyinstall.ps1"
        if not install_ps1.exists():
            warning(f"Chocolatey install script not found: {install_ps1}")
            return

        if not self.tarball_sha256:
            warning("No SHA256 available — skipping Chocolatey checksum update")
            return

        content = install_ps1.read_text()
        content = re.sub(
            r"\$checksum\s*=\s*'[a-fA-F0-9]+'",
            f"$checksum = '{self.tarball_sha256}'",
            content,
        )
        if not self.dry_run:
            install_ps1.write_text(content)
            success(f"Updated Chocolatey checksum: {install_ps1}")
        else:
            info(f"Would update Chocolatey checksum: {install_ps1}")

    def build_and_push_chocolatey(self):
        """Build Chocolatey .nupkg and push to chocolatey.org.

        Push uses the Chocolatey REST API directly (PUT to
        push.chocolatey.org/api/v2/package/) rather than `choco push`
        subprocess. The CLI returns 400 on `push.chocolatey.org/`
        (issue #97); kars85 verified the REST endpoint works during
        the 1.8.14 manual push. Pack stays via `choco pack` since it
        produces the Windows-native .nupkg via the choco binary.
        """
        log("\n" + "=" * 60)
        log("🍫 Building and pushing Chocolatey package")
        log("=" * 60)

        if not shutil.which("choco"):
            info("choco CLI not found — skipping Chocolatey publish (run from Windows or a Choco-enabled CI runner)")
            return

        choco_dir = self.repo_root / "packaging/chocolatey"
        nuspec = choco_dir / "empirica.nuspec"
        if not nuspec.exists():
            warning(f"Chocolatey nuspec not found: {nuspec}")
            return

        # Guard against silent ownership drift: the chocolatey.org listing is
        # owned by CHOCOLATEY_OWNER, and pushing from any other account fails
        # with a 403. Verify the nuspec hasn't been edited away from that.
        nuspec_text = nuspec.read_text(encoding="utf-8")
        if f"<owners>{CHOCOLATEY_OWNER}</owners>" not in nuspec_text:
            error(
                f"Chocolatey nuspec <owners> does not match expected "
                f"'{CHOCOLATEY_OWNER}'. Update {nuspec} or change "
                f"CHOCOLATEY_OWNER in scripts/release.py."
            )

        nupkg = choco_dir / f"empirica.{self.version}.nupkg"

        self.run_command(["choco", "pack"], cwd=str(choco_dir))
        success(f"Built: {nupkg}")

        if not nupkg.exists() and not self.dry_run:
            error(f"Expected .nupkg not found: {nupkg}")

        api_key = os.environ.get("CHOCOLATEY_API_KEY")
        if not api_key:
            warning("CHOCOLATEY_API_KEY not set — built .nupkg but skipping push (set the env var or run 'choco apikey set')")
            return

        if self.dry_run:
            info(f"Would PUT {nupkg} to https://push.chocolatey.org/api/v2/package/ (REST)")
            return

        # REST API push — fixes #97 (`choco push` CLI returns 400)
        try:
            import requests
        except ImportError:
            error("`requests` not available; cannot push to Chocolatey via REST API")
            return

        push_url = "https://push.chocolatey.org/api/v2/package/"
        headers = {
            "X-NuGet-ApiKey": api_key,
            "Content-Type": "application/octet-stream",
        }
        try:
            with open(nupkg, "rb") as f:
                response = requests.put(
                    push_url, headers=headers, data=f, timeout=300,
                )
        except requests.RequestException as e:
            error(f"Chocolatey REST push failed: {e}")
            return

        if response.status_code in (200, 201, 202):
            success(
                f"Pushed to chocolatey.org: empirica {self.version} "
                f"(REST {response.status_code})"
            )
        else:
            error(
                f"Chocolatey REST push returned {response.status_code}: "
                f"{response.text[:500]}"
            )

    def update_version_strings(self):
        """Update version strings in all source files not covered by other methods.

        Covers: __init__.py, empirica-mcp/pyproject.toml, install.py,
        setup_claude_code.py, install.sh (both copies), plugin.json (both copies),
        CLAUDE.md (canonical + both template copies), Dockerfile.alpine.
        """
        version_files = [
            # (path, pattern, replacement)
            (
                self.repo_root / "empirica" / "__init__.py",
                r'__version__\s*=\s*"[^"]+"',
                f'__version__ = "{self.version}"',
            ),
            (
                self.repo_root / "empirica-mcp" / "pyproject.toml",
                r'^version\s*=\s*"[^"]+"',
                f'version = "{self.version}"',
            ),
            # empirica-mcp pins its core dep with == (anti-drift); bump it in
            # lockstep so the wrapper always requires the matching core version.
            (
                self.repo_root / "empirica-mcp" / "pyproject.toml",
                r'"empirica==[0-9]+\.[0-9]+\.[0-9]+"',
                f'"empirica=={self.version}"',
            ),
            (
                self.repo_root / "scripts" / "install.py",
                r'EMPIRICA_VERSION\s*=\s*"[^"]+"',
                f'EMPIRICA_VERSION = "{self.version}"',
            ),
            (
                self.repo_root / "empirica" / "cli" / "command_handlers" / "setup_claude_code.py",
                r'PLUGIN_VERSION\s*=\s*"[^"]+"',
                f'PLUGIN_VERSION = "{self.version}"',
            ),
            (
                self.repo_root / "empirica" / "plugins" / "claude-code-integration" / "install.sh",
                r'PLUGIN_VERSION="[^"]+"',
                f'PLUGIN_VERSION="{self.version}"',
            ),
            (
                self.repo_root / "empirica" / "plugins" / "claude-code-integration" / ".claude-plugin" / "plugin.json",
                r'"version":\s*"[^"]+"',
                f'"version": "{self.version}"',
            ),
            # Installed plugin VERSION file (drift detection at session start)
            (
                Path.home() / ".claude" / "plugins" / "local" / "empirica" / "VERSION",
                r'^[0-9]+\.[0-9]+\.[0-9]+',
                self.version,
            ),
            # __init__.py docstring version
            (
                self.repo_root / "empirica" / "__init__.py",
                r'^Version:\s*[0-9]+\.[0-9]+\.[0-9]+',
                f'Version: {self.version}',
            ),
            # README.md version badge and footer
            (
                self.repo_root / "README.md",
                r'badge/version-[0-9]+\.[0-9]+\.[0-9]+-blue\)\]\(https://github\.com/EmpiricaAI/empirica/releases/tag/v[0-9]+\.[0-9]+\.[0-9]+\)',
                f'badge/version-{self.version}-blue)](https://github.com/EmpiricaAI/empirica/releases/tag/v{self.version})',
            ),
            # README.md docker tag references (standalone — added 1.8.16 to plug the
            # gap that left "nubaeon/empirica:1.8.14" lying around after the 1.8.15
            # release_check sweep)
            (
                self.repo_root / "README.md",
                r'nubaeon/empirica:[0-9]+\.[0-9]+\.[0-9]+(-alpine)?',
                lambda m: f'nubaeon/empirica:{self.version}{m.group(1) or ""}',
            ),
            # README.md author footer: `**Version:** 1.8.X` (bold-markdown form
            # — earlier `^Version:` regex only matched the bare __init__.py
            # docstring form). Added after the 1.8.14→1.8.16 sweep gap left
            # the footer stuck on the older version.
            (
                self.repo_root / "README.md",
                r'\*\*Version:\*\*\s+[0-9]+\.[0-9]+\.[0-9]+',
                f'**Version:** {self.version}',
            ),
            # docs/human/end-users/02_INSTALLATION.md — pip pin + docker tags
            (
                self.repo_root / "docs" / "human" / "end-users" / "02_INSTALLATION.md",
                r'pip install empirica==[0-9]+\.[0-9]+\.[0-9]+',
                f'pip install empirica=={self.version}',
            ),
            (
                self.repo_root / "docs" / "human" / "end-users" / "02_INSTALLATION.md",
                r'nubaeon/empirica:[0-9]+\.[0-9]+\.[0-9]+(-alpine)?',
                lambda m: f'nubaeon/empirica:{self.version}{m.group(1) or ""}',
            ),
            # MCP server reference + system-prompt CLAUDE.md "Syncs with" label
            (
                self.repo_root / "docs" / "human" / "developers" / "MCP_SERVER_REFERENCE.md",
                r'\*\*Version:\*\*\s+[0-9]+\.[0-9]+\.[0-9]+',
                f'**Version:** {self.version}',
            ),
            (
                self.repo_root / "docs" / "human" / "developers" / "system-prompts" / "CLAUDE.md",
                r'\*\*Syncs with:\*\*\s+Empirica\s+v[0-9]+\.[0-9]+\.[0-9]+',
                f'**Syncs with:** Empirica v{self.version}',
            ),
            # Chocolatey install script version
            (
                self.repo_root / "packaging" / "chocolatey" / "tools" / "chocolateyinstall.ps1",
                r"\$packageVersion\s*=\s*'[^']+'",
                f"$packageVersion = '{self.version}'",
            ),
            # Canonical Core prompt version header
            (
                self.repo_root / "docs" / "human" / "developers" / "system-prompts" / "CANONICAL_CORE.md",
                r'Canonical Core v[0-9]+\.[0-9]+\.[0-9]+',
                f'Canonical Core v{self.version}',
            ),
            # PROJECT_CONFIG version
            (
                self.repo_root / ".empirica-project" / "PROJECT_CONFIG.yaml",
                r'version:\s*"[^"]+"',
                f'version: "{self.version}"',
            ),
            # docs/README.md current-version pointer (the one legit hit
            # that broken-sweep_version used to catch — the other 31
            # were historical refs that should NOT be rewritten)
            (
                self.repo_root / "docs" / "README.md",
                r'\*\*Version:\*\*\s+[0-9]+\.[0-9]+\.[0-9]+',
                f'**Version:** {self.version}',
            ),
            # docs/human/developers/EXTENDING_EMPIRICA.md "**Version:**" header
            (
                self.repo_root / "docs" / "human" / "developers" / "EXTENDING_EMPIRICA.md",
                r'\*\*Version:\*\*\s+[0-9]+\.[0-9]+\.[0-9]+',
                f'**Version:** {self.version}',
            ),
        ]

        # Dockerfile.alpine (same patterns as Dockerfile)
        alpine_path = self.repo_root / "Dockerfile.alpine"
        if alpine_path.exists():
            content = alpine_path.read_text()
            content = re.sub(r'LABEL version="[^"]+"', f'LABEL version="{self.version}"', content)
            content = re.sub(
                r'COPY dist/empirica-[^-]+-py3-none-any\.whl',
                f'COPY dist/empirica-{self.version}-py3-none-any.whl',
                content,
            )
            content = re.sub(
                r'/tmp/empirica-[^-]+-py3-none-any\.whl',
                f'/tmp/empirica-{self.version}-py3-none-any.whl',
                content,
                count=2,
            )
            if not self.dry_run:
                alpine_path.write_text(content)
                success(f"Updated: {alpine_path}")
            else:
                info(f"Would update: {alpine_path}")

        for filepath, pattern, replacement in version_files:
            if not filepath.exists():
                warning(f"Not found: {filepath}")
                continue

            content = filepath.read_text()
            new_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)

            if content == new_content:
                info(f"Already up to date: {filepath}")
                continue

            if not self.dry_run:
                filepath.write_text(new_content)
                success(f"Updated: {filepath}")
            else:
                info(f"Would update: {filepath}")

    def clear_bytecode_cache(self):
        """Clear __pycache__ so stale .pyc files don't shadow new version strings.

        Called after `update_version_strings` so long-running editable-install
        Python processes (e.g., empirica CLI between releases) don't report
        stale `__version__`.
        """
        cleared = 0
        for pycache in self.repo_root.rglob("__pycache__"):
            if pycache.is_dir():
                shutil.rmtree(pycache, ignore_errors=True)
                cleared += 1
        if cleared:
            info(f"Cleared {cleared} __pycache__ directories")

    # NOTE: `sweep_version` was removed in 1.9.9.
    #
    # It did a naive `content.replace(old_version, self.version)` across every
    # .md/.py/.toml/.yaml file in the repo, which rewrote historical version
    # references ("shipped in v1.9.6", "(v1.9.6+)" feature markers, test section
    # headers, etc.) into false history. The 1.9.7 → 1.9.8 cycle produced 32
    # working-tree changes — only 1 was a legit current-version pointer.
    #
    # Replacement: every legit current-version pointer file has an explicit
    # regex pattern in `update_version_strings`. Missing patterns are added
    # there as we discover them — that's a noticed-and-corrected miss, not a
    # silent false-history rewrite.

    def regenerate_cli_docs(self):
        """Regenerate CLI_COMMANDS_UNIFIED.md so the 'Framework version' header
        reflects the freshly-bumped __version__.

        The generator reads `empirica.__version__` (already bumped by
        update_version_strings); without this step the doc lags releases by
        one version and gets surfaced via cockpit / statusline / `empirica
        --help`. Non-fatal: a generator error is logged as a warning,
        release continues.
        """
        log("\n" + "="*60)
        log("📚 Regenerating CLI_COMMANDS_UNIFIED.md")
        log("="*60)

        generator = self.repo_root / "scripts" / "generate_cli_docs.py"
        if not generator.exists():
            warning("scripts/generate_cli_docs.py not found, skipping CLI docs regen")
            return

        if self.dry_run:
            info(f"[DRY RUN] Would run: python {generator}")
            return

        try:
            result = subprocess.run(
                ["python", str(generator)],
                cwd=str(self.repo_root),
                capture_output=True, text=True, timeout=60, check=False,
            )
            if result.returncode != 0:
                warning(f"CLI docs regen exited {result.returncode}: {result.stderr.strip()[:200]}")
            else:
                success("CLI_COMMANDS_UNIFIED.md regenerated")
        except Exception as exc:
            warning(f"CLI docs regen failed: {exc}")

    def sync_readme_whats_new(self):
        """Sync README 'What's New' section from CHANGELOG.

        Extracts the latest release entry from CHANGELOG.md and replaces
        the What's New section in README.md. This ensures the README
        always reflects the current release content, not just the version number.
        """
        log("\n" + "="*60)
        log("📝 Syncing README What's New from CHANGELOG")
        log("="*60)

        changelog_path = self.repo_root / "CHANGELOG.md"
        readme_path = self.repo_root / "README.md"

        if not changelog_path.exists() or not readme_path.exists():
            warning("CHANGELOG.md or README.md not found, skipping What's New sync")
            return

        # Extract latest CHANGELOG entry (between first ## and second ##)
        changelog = changelog_path.read_text()
        entries = re.split(r'^## ', changelog, flags=re.MULTILINE)
        if len(entries) < 2:
            warning("Could not parse CHANGELOG entries")
            return

        # entries[0] is the header, entries[1] is the latest release
        latest_entry = entries[1].strip()
        # Extract just the Added/Changed/Fixed bullets (skip the header line and ### Highlights)
        lines = latest_entry.split('\n')
        # Skip the version/date header line
        content_lines = lines[1:]

        # Build a condensed What's New from the CHANGELOG
        # Extract key items from ### Added, ### Changed, ### Fixed sections
        whats_new_items = []
        for line in content_lines:
            line = line.strip()
            if line.startswith('- **') and len(whats_new_items) < 10:
                whats_new_items.append(line)

        if not whats_new_items:
            warning("No bullet items found in latest CHANGELOG entry")
            return

        # Build the new What's New section
        new_whats_new = f"## What's New in {self.version}\n\n"
        new_whats_new += '\n'.join(whats_new_items[:8])  # Top 8 items

        # Replace in README. Match the FIRST What's New section by capturing
        # everything from the header up to (but not including) the next ## or
        # ### or --- divider. Older What's New sections survive as history.
        # Lazy `.+?` + lookahead delimiter handles multi-line bullets that the
        # earlier `(?:- \*\*[^\n]+\n)+` pattern broke on. count=1 ensures we
        # don't mangle older sections that happen to start with `- **`.
        readme = readme_path.read_text()
        pattern = re.compile(
            r"## What's New in [^\n]+\n.+?(?=\n## |\n### |\n---\n)",
            re.DOTALL,
        )
        match = pattern.search(readme)
        if match:
            readme = pattern.sub(new_whats_new, readme, count=1)
            if not self.dry_run:
                readme_path.write_text(readme)
                success(f"README What's New synced from CHANGELOG ({len(whats_new_items)} items)")
            else:
                info(f"Would sync README What's New ({len(whats_new_items)} items)")
        else:
            warning("Could not find What's New section pattern in README")

    def run_command(self, cmd: list[str], check: bool = True, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
        """Run a shell command"""
        cmd_str = " ".join(cmd)
        cwd_info = f" (in {cwd})" if cwd else ""
        if self.dry_run:
            info(f"Would run: {cmd_str}{cwd_info}")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        info(f"Running: {cmd_str}{cwd_info}")
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, cwd=cwd)
        if result.returncode != 0:
            if result.stderr:
                warning(f"stderr: {result.stderr.strip()}")
            if check:
                error(f"Command failed (exit {result.returncode}): {cmd_str}")
        return result

    def build_package(self):
        """Build Python package"""
        log("\n" + "="*60)
        log("📦 Building Python package")
        log("="*60)

        # Clean old builds
        for path in ["dist", "build", "empirica.egg-info"]:
            full_path = self.repo_root / path
            if full_path.exists():
                if not self.dry_run:
                    if full_path.is_dir():
                        shutil.rmtree(full_path)
                    else:
                        full_path.unlink()
                    info(f"Removed {path}")

        # Build
        self.run_command(["python3", "-m", "build", "--wheel", "--sdist"],
                         cwd=str(self.repo_root))
        success("Package built successfully")

    def build_mcp_package(self):
        """Build empirica-mcp package"""
        log("\n" + "="*60)
        log("📦 Building empirica-mcp package")
        log("="*60)

        mcp_dir = self.repo_root / "empirica-mcp"
        if not mcp_dir.exists():
            warning(f"empirica-mcp directory not found: {mcp_dir}")
            return

        # Clean old builds
        for path in ["dist", "build", "empirica_mcp.egg-info"]:
            full_path = mcp_dir / path
            if full_path.exists():
                if not self.dry_run:
                    if full_path.is_dir():
                        shutil.rmtree(full_path)
                    else:
                        full_path.unlink()
                    info(f"Removed empirica-mcp/{path}")

        # Build
        self.run_command(
            ["python3", "-m", "build", "--wheel", "--sdist"],
            cwd=str(mcp_dir)
        )
        success("empirica-mcp package built successfully")

    def publish_to_pypi(self):
        """Publish to PyPI"""
        log("\n" + "="*60)
        log("📤 Publishing to PyPI")
        log("="*60)

        if self.dry_run:
            info("Would publish to PyPI using twine")
            return

        self.run_command(["python3", "-m", "twine", "upload", f"dist/empirica-{self.version}*"])
        success(f"Published to PyPI: https://pypi.org/project/empirica/{self.version}/")

    def publish_mcp_to_pypi(self):
        """Publish empirica-mcp to PyPI"""
        log("\n" + "="*60)
        log("📤 Publishing empirica-mcp to PyPI")
        log("="*60)

        mcp_dir = self.repo_root / "empirica-mcp"
        if not (mcp_dir / "dist").exists():
            warning("empirica-mcp dist/ not found, skipping MCP publish")
            return

        if self.dry_run:
            info("Would publish empirica-mcp to PyPI using twine")
            return

        self.run_command([
            "python3", "-m", "twine", "upload",
            str(mcp_dir / "dist" / f"empirica_mcp-{self.version}*")
        ])
        success(f"Published empirica-mcp to PyPI: https://pypi.org/project/empirica-mcp/{self.version}/")

    def create_git_tag(self):
        """Create and push git tag"""
        log("\n" + "="*60)
        log("🏷️  Creating Git tag")
        log("="*60)

        tag = f"v{self.version}"

        # Commit ALL release updates (version pointer regex bumps + packaging).
        # Must include every file `update_version_strings` and friends touch.
        # Drift here is silent: missed files stay on the old version in the
        # release commit and need a follow-up bump. Keep this list in sync
        # with the version_files list in update_version_strings.
        self.run_command(["git", "add",
            "pyproject.toml",
            "packaging/", "Dockerfile", "Dockerfile.alpine",
            "README.md",
            "empirica/__init__.py",
            "empirica-mcp/pyproject.toml",
            "empirica/plugins/claude-code-integration/.claude-plugin/plugin.json",
            "empirica/plugins/claude-code-integration/install.sh",
            "empirica/cli/command_handlers/setup_claude_code.py",
            ".empirica-project/PROJECT_CONFIG.yaml",
            # docs/ current-version pointers (regex-bumped by update_version_strings)
            "docs/README.md",
            "docs/human/developers/EXTENDING_EMPIRICA.md",
            "docs/human/developers/MCP_SERVER_REFERENCE.md",
            "docs/human/end-users/02_INSTALLATION.md",
            # Regenerated by regenerate_cli_docs() during --prepare. Without it
            # here, the committed CLI reference (README links to it) drifts stale
            # every release and leaves an uncommitted edit in the tree.
            "docs/human/developers/CLI_COMMANDS_UNIFIED.md",
        ])
        self.run_command([
            "git", "commit", "-m",
            f"chore: automated release {self.version}\n\n"
            f"- Updated all distribution channels\n"
            f"- SHA256: {self.tarball_sha256}"
        ], check=False)  # May have no changes

        # Create tag
        self.run_command([
            "git", "tag", "-a", tag,
            "-m", f"Release {self.version}"
        ])

        # Push
        self.run_command(["git", "push", "origin", "main", "--tags"])
        success(f"Created and pushed tag: {tag}")

    def build_and_push_docker(self):
        """Build and push Docker images (Debian + Alpine)"""
        log("\n" + "="*60)
        log("🐳 Building and pushing Docker images")
        log("="*60)

        # Debian image
        debian_tags = [
            f"nubaeon/empirica:{self.version}",
            "nubaeon/empirica:latest"
        ]

        build_cmd = ["docker", "build", "."]
        for tag in debian_tags:
            build_cmd.extend(["-t", tag])

        self.run_command(build_cmd, cwd=str(self.repo_root))
        success("Docker image built (Debian)")

        for tag in debian_tags:
            self.run_command(["docker", "push", tag])
            success(f"Pushed: {tag}")

        # Alpine image
        alpine_dockerfile = self.repo_root / "Dockerfile.alpine"
        if alpine_dockerfile.exists():
            alpine_tags = [
                f"nubaeon/empirica:{self.version}-alpine",
            ]

            build_cmd = ["docker", "build", "-f", "Dockerfile.alpine", "."]
            for tag in alpine_tags:
                build_cmd.extend(["-t", tag])

            self.run_command(build_cmd, cwd=str(self.repo_root))
            success("Docker image built (Alpine)")

            for tag in alpine_tags:
                self.run_command(["docker", "push", tag])
                success(f"Pushed: {tag}")
        else:
            warning("Dockerfile.alpine not found, skipping Alpine build")

    def create_github_release(self):
        """Create GitHub release"""
        log("\n" + "="*60)
        log("📝 Creating GitHub release")
        log("="*60)

        tag = f"v{self.version}"
        wheel = f"dist/empirica-{self.version}-py3-none-any.whl"
        tarball = f"dist/empirica-{self.version}.tar.gz"

        # Include empirica-mcp assets if built
        mcp_wheel = f"empirica-mcp/dist/empirica_mcp-{self.version}-py3-none-any.whl"
        mcp_tarball = f"empirica-mcp/dist/empirica_mcp-{self.version}.tar.gz"
        assets = [wheel, tarball]
        mcp_wheel_path = self.repo_root / mcp_wheel
        mcp_tarball_path = self.repo_root / mcp_tarball
        if mcp_wheel_path.exists():
            assets.append(mcp_wheel)
        if mcp_tarball_path.exists():
            assets.append(mcp_tarball)

        notes = f"""## What's in v{self.version}

See CHANGELOG.md for detailed release notes.

### Installation
```bash
pip install empirica=={self.version}
```

### Docker
```bash
# Security-hardened Alpine (recommended)
docker pull nubaeon/empirica:{self.version}-alpine

# Debian slim
docker pull nubaeon/empirica:{self.version}
```

### Homebrew
```bash
brew tap empiricaai/tap
brew install empirica
```
"""

        # Race tolerance — CI's release workflow may publish first.
        # Try create; on failure check if release already exists and just
        # upload assets. Without this, the script sys.exit's mid-publish and
        # downstream steps (homebrew tap, chocolatey) silently skip.
        # (1.9.6 missed homebrew via exactly this race; 2026-05-17.)
        create_result = self.run_command([
            "gh", "release", "create", tag,
            *assets,
            "--title", f"v{self.version}",
            "--notes", notes,
        ], check=False)

        if create_result.returncode == 0:
            success(f"Created GitHub release: {tag}")
            return

        # Check whether the release exists (CI race) vs a real failure
        view_result = self.run_command(
            ["gh", "release", "view", tag], check=False,
        )
        if view_result.returncode == 0:
            warning(f"Release {tag} already exists (likely CI race) — uploading assets with --clobber")
            self.run_command(
                ["gh", "release", "upload", tag, *assets, "--clobber"],
            )
            success(f"Uploaded assets to existing GitHub release: {tag}")
            return

        # Real failure — surface it the way error() does (sys.exit).
        error(f"gh release create failed and release {tag} does not exist: {create_result.stderr.strip()}")

    def run_version_update(self):
        """Update version strings only (no build/publish)."""
        log("\n╔════════════════════════════════════════════════════════════╗")
        log("║  Empirica Version Update                                   ║")
        log("╚════════════════════════════════════════════════════════════╝\n")

        if self.dry_run:
            warning("DRY RUN MODE - No changes will be made\n")

        self.version = self.read_version()

        if not self.old_version:
            error("--old-version required for version-only mode")

        # Targeted regex updates (structural patterns).
        # `update_version_strings` covers every legit current-version pointer
        # explicitly. The naive `sweep_version` catch-all was removed in 1.9.9 —
        # missed patterns get added there, not papered over by a broad replace.
        self.update_version_strings()
        self.update_dockerfile()
        self.update_chocolatey_nuspec()
        self.clear_bytecode_cache()

        success(f"All version strings updated to {self.version}")
        info("Homebrew formula SHA256 will be updated during full release.")

    def ensure_main_branch(self):
        """Merge develop → main and switch to main for release.

        Release flow: develop (working) → main (release) → tag + publish.
        This avoids homebrew SHA256 conflicts from releasing on develop
        and merging to main afterward.
        """
        log("\n" + "="*60)
        log("🔀 Preparing main branch for release")
        log("="*60)

        # Check current branch
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=self.repo_root
        )
        current_branch = result.stdout.strip()

        if current_branch == "main":
            info("Already on main branch")
            return

        if current_branch != "develop":
            error(f"Release must be run from 'develop' or 'main', currently on '{current_branch}'")

        # Merge develop → main
        info(f"Merging develop → main...")
        self.run_command(["git", "checkout", "main"])
        self.run_command(["git", "pull", "origin", "main"], check=False)
        self.run_command(["git", "merge", "develop", "-m", f"Merge develop — Empirica {self.version} release"])
        success("Merged develop → main")

    def back_to_develop(self):
        """Switch back to develop after release and merge any release commits."""
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=self.repo_root
        )
        if result.stdout.strip() == "main":
            info("Switching back to develop...")
            self.run_command(["git", "checkout", "develop"])
            self.run_command(["git", "merge", "main", "-m", f"Merge main — post-release {self.version}"])
            self.run_command(["git", "push", "origin", "develop"], check=False)

    def run_ruff(self) -> bool:
        """Lint gate — mirrors the CI ruff check step.

        Caught 1.9.4 shipping with a leftover unused `import os` that broke
        CI's lint job after the tag was already pushed. Lint failures at
        this stage are cheap to fix; after the tag they cost a re-roll.
        """
        log("\n" + "=" * 60)
        log("🧹 ruff check (lint gate)")
        log("=" * 60)

        if self.dry_run:
            info("Would run: ruff check empirica/ empirica-mcp/ tests/")
            return True

        result = subprocess.run(
            ["ruff", "check", "empirica/", "empirica-mcp/", "tests/"],
            capture_output=True, text=True, timeout=120,
            cwd=str(self.repo_root),
        )
        if result.returncode == 0:
            success("ruff clean")
            return True
        log(f"\n{RED}ruff FAILED:{RESET}")
        for line in (result.stdout + result.stderr).strip().splitlines()[-30:]:
            log(f"  {line}")
        return False

    def run_pyright(self) -> bool:
        """Type-check gate — mirrors the CI pyright step."""
        log("\n" + "=" * 60)
        log("🔬 pyright (type-check gate)")
        log("=" * 60)

        if self.dry_run:
            info("Would run: pyright empirica/ empirica-mcp/")
            return True

        result = subprocess.run(
            ["pyright", "empirica/", "empirica-mcp/"],
            capture_output=True, text=True, timeout=300,
            cwd=str(self.repo_root),
        )
        if result.returncode == 0:
            success("pyright clean")
            return True
        log(f"\n{RED}pyright FAILED:{RESET}")
        for line in (result.stdout + result.stderr).strip().splitlines()[-30:]:
            log(f"  {line}")
        return False

    def run_pip_audit(self) -> bool:
        """CVE scan — mirrors the CI pip-audit step (hard fail on CVEs)."""
        log("\n" + "=" * 60)
        log("🔒 pip-audit (CVE gate)")
        log("=" * 60)

        if self.dry_run:
            info("Would run: pip-audit --skip-editable")
            return True

        try:
            result = subprocess.run(
                ["pip-audit", "--skip-editable"],
                capture_output=True, text=True, timeout=300,
                cwd=str(self.repo_root),
            )
        except FileNotFoundError:
            warning("pip-audit not installed — skipping CVE gate (install via `pip install pip-audit`)")
            return True  # informational on missing tool; CI is the source of truth

        if result.returncode == 0:
            success("pip-audit clean (no CVEs)")
            return True
        log(f"\n{RED}pip-audit FAILED:{RESET}")
        for line in (result.stdout + result.stderr).strip().splitlines()[-30:]:
            log(f"  {line}")
        return False

    def run_tests(self) -> bool:
        """Run test suite as a release gate. Returns True if tests pass."""
        log("\n" + "=" * 60)
        log("🧪 Running test suite (release gate)")
        log("=" * 60)

        if self.dry_run:
            info("Would run: python3 -m pytest tests/ -x -q --tb=short")
            return True

        # 600s ceiling: full suite is ~3-4min on cold cache. Scanner integration
        # alone can take ~80s — 300s left no headroom and timed out in 1.8.19
        # release prep. Bump gives ~2x safety margin.
        result = subprocess.run(
            ["python3", "-m", "pytest", "tests/", "-x", "-q", "--tb=short",
             "--ignore=tests/integration", "--ignore=tests/manual_test_goals.py",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=600,
            cwd=str(self.repo_root),
        )

        if result.returncode == 0:
            success("Tests passed!")
            if result.stdout:
                # Show summary line
                for line in result.stdout.strip().splitlines()[-3:]:
                    info(f"  {line}")
            return True
        else:
            log(f"\n{RED}Tests FAILED:{RESET}")
            # Show failure output
            output = result.stdout + result.stderr
            for line in output.strip().splitlines()[-20:]:
                log(f"  {line}")
            return False

    def run_import_check(self) -> bool:
        """Quick check that key CLI entry points import without error."""
        log("\n" + "=" * 60)
        log("🔍 Checking critical imports (smoke test)")
        log("=" * 60)

        checks = [
            ("session-create", "from empirica.cli.command_handlers.session_create import handle_session_create_command"),
            ("cli-core", "from empirica.cli.cli_core import main"),
            ("session-database", "from empirica.data.session_database import SessionDatabase"),
            ("path-resolver", "from empirica.config.path_resolver import get_session_db_path"),
        ]

        all_ok = True
        for name, import_stmt in checks:
            if self.dry_run:
                info(f"Would check: {name}")
                continue
            result = subprocess.run(
                ["python3", "-c", import_stmt],
                capture_output=True, text=True, timeout=10,
                cwd=str(self.repo_root),
            )
            if result.returncode == 0:
                success(f"  {name}: OK")
            else:
                log(f"  {RED}{name}: FAILED — {result.stderr.strip().splitlines()[-1]}{RESET}")
                all_ok = False

        return all_ok

    def check_auto_issues(self) -> bool:
        """Check for unresolved high-severity auto-captured issues. Returns True if clean."""
        log("\n" + "=" * 60)
        log("🔎 Checking for unresolved high-severity issues")
        log("=" * 60)

        if self.dry_run:
            info("Would run: empirica issue-list --status new --severity high")
            return True

        try:
            result = subprocess.run(
                ["empirica", "issue-list", "--status", "new", "--severity", "high", "--output", "json"],
                capture_output=True, text=True, timeout=15,
                cwd=str(self.repo_root),
            )
            if result.returncode != 0:
                warning("Could not check auto-captured issues (command failed). Skipping gate.")
                return True

            import json
            data = json.loads(result.stdout)
            issues = data.get("issues", [])
            if not issues:
                success("No unresolved high-severity issues")
                return True

            log(f"\n{RED}Found {len(issues)} unresolved high-severity issue(s):{RESET}")
            for issue in issues[:10]:
                log(f"  [{issue['id'][:8]}] {issue.get('message', '?')[:100]}")
            if len(issues) > 10:
                log(f"  ... and {len(issues) - 10} more")
            return False

        except (subprocess.TimeoutExpired, FileNotFoundError):
            warning("empirica CLI not available. Skipping auto-issue gate.")
            return True
        except Exception as e:
            warning(f"Auto-issue check failed: {e}. Skipping gate.")
            return True

    def run_prepare(self):
        """Prepare release: merge to main, build, test. Does NOT publish.

        This is the safe first half of the release pipeline. After running
        this, review the build artifacts and test results before publishing
        with --publish.
        """
        log("\n╔════════════════════════════════════════════════════════════╗")
        log("║  Empirica Release — PREPARE (merge + build + test)        ║")
        log("╚════════════════════════════════════════════════════════════╝\n")

        if self.dry_run:
            warning("DRY RUN MODE - No changes will be made\n")

        try:
            self.version = self.read_version()

            # Merge develop → main
            if not self.dry_run:
                self.ensure_main_branch()

            # Update version strings (targeted regex — `sweep_version`
            # removed in 1.9.9; see comment in clear_bytecode_cache)
            self.update_version_strings()
            self.clear_bytecode_cache()

            # Sync README What's New from CHANGELOG
            self.sync_readme_whats_new()

            # Regenerate CLI docs (CLI_COMMANDS_UNIFIED.md picks up __version__)
            self.regenerate_cli_docs()

            # Build packages
            self.build_package()
            self.build_mcp_package()

            # Calculate SHA256 and update packaging
            self.tarball_sha256 = self.calculate_sha256()
            self.update_homebrew_formula()
            self.update_dockerfile()
            self.update_chocolatey_nuspec()
            self.update_chocolatey_checksum()

            # Gate: import smoke test
            if not self.run_import_check():
                error("Import check failed — fix before publishing.")

            # Gate: ruff (lint) — mirrors CI's ruff check step. Cheap; catches
            # the kind of leftover-import drift that broke v1.9.4's post-tag CI.
            if not self.run_ruff():
                warning("Lint failed. Fix issues before running --publish.")
                warning("You are on the 'main' branch with built artifacts.")
                warning("To abort: git checkout develop && git reset --hard origin/main")
                info(f"\nOnce fixed, run: python scripts/release.py --publish")
                sys.exit(1)

            # Gate: pyright (types) — mirrors CI's pyright step.
            if not self.run_pyright():
                warning("Type-check failed. Fix issues before running --publish.")
                info(f"\nOnce fixed, run: python scripts/release.py --publish")
                sys.exit(1)

            # Gate: pip-audit (CVE scan) — mirrors CI's pip-audit step.
            if not self.run_pip_audit():
                warning("CVE scan failed. Fix vulnerabilities before running --publish.")
                info(f"\nOnce fixed, run: python scripts/release.py --publish")
                sys.exit(1)

            # Gate: test suite
            if not self.run_tests():
                warning("Tests failed. Fix issues before running --publish.")
                warning("You are on the 'main' branch with built artifacts.")
                warning("To abort: git checkout develop && git reset --hard origin/main")
                info(f"\nOnce fixed, run: python scripts/release.py --publish")
                sys.exit(1)

            # Gate: no unresolved high-severity auto-captured issues
            if not self.check_auto_issues():
                warning("Unresolved high-severity issues found. Fix or resolve before publishing.")
                warning("Use: empirica issue-list --status new --severity high")
                warning("Resolve with: empirica issue-resolve --session-id <SID> --issue-id <ID> --resolution '...'")
                info(f"\nOnce resolved, run: python scripts/release.py --publish")
                sys.exit(1)

            log("\n╔════════════════════════════════════════════════════════════╗")
            log("║  ✅ Prepare Complete — Ready to Publish                    ║")
            log("╚════════════════════════════════════════════════════════════╝\n")

            success(f"v{self.version} built and tested on main branch")
            info(f"Artifacts: dist/empirica-{self.version}*.tar.gz, *.whl")
            info(f"SHA256: {self.tarball_sha256}")
            info(f"\nNext: review changes, then run:")
            info(f"  python scripts/release.py --publish")

        except Exception as e:
            error(f"Prepare failed: {e}")

    def run_publish(self):
        """Publish a prepared release. Requires --prepare to have been run first."""
        log("\n╔════════════════════════════════════════════════════════════╗")
        log("║  Empirica Release — PUBLISH                               ║")
        log("╚════════════════════════════════════════════════════════════╝\n")

        if self.dry_run:
            warning("DRY RUN MODE - No changes will be made\n")

        try:
            self.version = self.read_version()

            # Verify we're on main with built artifacts
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, cwd=self.repo_root,
            )
            current_branch = result.stdout.strip()
            if current_branch != "main" and not self.dry_run:
                error(f"--publish requires main branch (currently on '{current_branch}'). Run --prepare first.")

            tarball = self.repo_root / "dist" / f"empirica-{self.version}.tar.gz"
            if not tarball.exists() and not self.dry_run:
                error(f"No built artifacts found at {tarball}. Run --prepare first.")

            self.tarball_sha256 = self.calculate_sha256()

            # Publish to all channels
            self.publish_to_pypi()
            self.publish_mcp_to_pypi()
            self.create_git_tag()
            self.build_and_push_docker()
            self.create_github_release()
            self.update_homebrew_tap()
            self.build_and_push_chocolatey()

            # Switch back to develop
            if not self.dry_run:
                self.back_to_develop()

            log("\n╔════════════════════════════════════════════════════════════╗")
            log("║  ✅ Release Published!                                     ║")
            log("╚════════════════════════════════════════════════════════════╝\n")

            success(f"Released empirica v{self.version}")
            info(f"PyPI: https://pypi.org/project/empirica/{self.version}/")
            info(f"PyPI (MCP): https://pypi.org/project/empirica-mcp/{self.version}/")
            info(f"Docker: docker pull nubaeon/empirica:{self.version}")
            info(f"Docker: docker pull nubaeon/empirica:{self.version}-alpine")
            info(f"GitHub: https://github.com/EmpiricaAI/empirica/releases/tag/v{self.version}")
            info(f"Homebrew: brew upgrade empirica")
            info(f"Chocolatey: choco upgrade empirica")

        except Exception as e:
            error(f"Publish failed: {e}")

    def run(self):
        """Execute full release process (prepare + publish in one shot).

        For safer releases, use --prepare then --publish separately.
        """
        log("\n╔════════════════════════════════════════════════════════════╗")
        log("║  Empirica Automated Release Pipeline                       ║")
        log("╚════════════════════════════════════════════════════════════╝\n")

        if self.dry_run:
            warning("DRY RUN MODE - No changes will be made\n")

        warning("Running full release (prepare + publish) in one shot.")
        warning("For safer releases, use: --prepare → review → --publish\n")

        try:
            self.version = self.read_version()

            # Merge develop → main
            if not self.dry_run:
                self.ensure_main_branch()

            # Update version strings (targeted regex — `sweep_version`
            # removed in 1.9.9; see comment in clear_bytecode_cache)
            self.update_version_strings()
            self.clear_bytecode_cache()

            # Build packages
            self.build_package()
            self.build_mcp_package()

            # Calculate SHA256 and update packaging
            self.tarball_sha256 = self.calculate_sha256()
            self.update_homebrew_formula()
            self.update_dockerfile()
            self.update_chocolatey_nuspec()
            self.update_chocolatey_checksum()

            # Gate: import smoke test
            if not self.run_import_check():
                error("Import check failed — aborting release.")

            # Gate: test suite
            if not self.run_tests():
                error("Tests failed — aborting release. Fix and retry.")

            # Publish
            self.publish_to_pypi()
            self.publish_mcp_to_pypi()
            self.create_git_tag()
            self.build_and_push_docker()
            self.create_github_release()
            self.update_homebrew_tap()
            self.build_and_push_chocolatey()

            # Switch back to develop
            if not self.dry_run:
                self.back_to_develop()

            log("\n╔════════════════════════════════════════════════════════════╗")
            log("║  ✅ Release Complete!                                      ║")
            log("╚════════════════════════════════════════════════════════════╝\n")

            success(f"Released empirica v{self.version}")
            info(f"PyPI: https://pypi.org/project/empirica/{self.version}/")
            info(f"PyPI (MCP): https://pypi.org/project/empirica-mcp/{self.version}/")
            info(f"Docker: docker pull nubaeon/empirica:{self.version}")
            info(f"Docker: docker pull nubaeon/empirica:{self.version}-alpine")
            info(f"GitHub: https://github.com/EmpiricaAI/empirica/releases/tag/v{self.version}")
            info(f"Homebrew: brew upgrade empirica")
            info(f"Chocolatey: choco upgrade empirica")

        except Exception as e:
            error(f"Release failed: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Automated release script for Empirica",
        epilog="""
Recommended flow:
  1. python scripts/release.py --prepare          # merge, build, test
  2. (review artifacts, smoke test manually)
  3. python scripts/release.py --publish           # push to all channels

Legacy (one-shot, less safe):
  python scripts/release.py                        # prepare + publish
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without executing"
    )
    parser.add_argument(
        "--old-version",
        help="Previous version for broad sweep replacement (e.g. 1.5.6)"
    )
    parser.add_argument(
        "--version-only",
        action="store_true",
        help="Update version strings only (no build/publish). Requires --old-version."
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Merge to main, build, and test — but do NOT publish. Review before --publish."
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish a prepared release (requires --prepare to have been run first)."
    )
    args = parser.parse_args()

    if args.prepare and args.publish:
        parser.error("Use --prepare and --publish separately, not together.")

    manager = ReleaseManager(dry_run=args.dry_run, old_version=args.old_version)
    if args.version_only:
        manager.run_version_update()
    elif args.prepare:
        manager.run_prepare()
    elif args.publish:
        manager.run_publish()
    else:
        manager.run()


if __name__ == "__main__":
    main()
