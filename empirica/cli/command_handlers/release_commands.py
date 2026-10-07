#!/usr/bin/env python3
"""
Epistemic Release Agent - release-ready command

A thorough pre-release verification that applies epistemic principles:
1. Version Sync - Ensures versions match across pyproject.toml, __init__.py, CLAUDE.md
2. Architecture Assessment - Turtle assess on key directories
3. PyPI Package Check - Verifies empirica and empirica-mcp packages
4. Privacy/Security Scan - Checks for sensitive/private/dev content
5. Documentation Assessment - Verifies docs are current

Usage:
    empirica release-ready                    # Full epistemic release check
    empirica release-ready --quick            # Quick check (skip architecture assess)
    empirica release-ready --output json      # JSON output for automation
"""

import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..cli_utils import handle_cli_error


class AssessmentStatus(Enum):
    """Status indicators for release readiness assessments."""

    PASS = "pass"  # noqa: S105
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class CheckResult:
    """Result of a single release readiness check."""

    name: str
    status: AssessmentStatus
    message: str
    details: list[str] = field(default_factory=list)
    moon: str = ""  # Moon phase indicator

    def to_dict(self) -> dict[str, Any]:
        """Convert check result to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "details": self.details,
            "moon": self.moon,
        }


class EpistemicReleaseAgent:
    """
    Epistemic Release Agent - Applies epistemic principles to release readiness.

    Philosophy:
    - "Know what you know" - Version consistency proves understanding
    - "Know what you don't know" - Architecture assess reveals gaps
    - "Protect what shouldn't be known" - Privacy scan guards secrets
    """

    def __init__(self, project_root: Path | None = None, quick: bool = False):
        """Initialize release agent with project root and quick mode setting."""
        self.root = project_root or Path.cwd()
        self.quick = quick
        self.results: list[CheckResult] = []
        self.version: str | None = None

    def _score_to_moon(self, score: float) -> str:
        """Convert 0-1 score to moon phase."""
        if score >= 0.85:
            return "🌕"  # Full moon - crystalline
        elif score >= 0.70:
            return "🌔"  # Waxing gibbous - solid
        elif score >= 0.50:
            return "🌓"  # First quarter - emergent
        elif score >= 0.30:
            return "🌒"  # Waxing crescent - forming
        else:
            return "🌑"  # New moon - dark

    def _status_to_moon(self, status: AssessmentStatus) -> str:
        """Convert status to moon phase."""
        return {
            AssessmentStatus.PASS: "🌕",
            AssessmentStatus.WARN: "🌓",
            AssessmentStatus.FAIL: "🌑",
            AssessmentStatus.SKIP: "🌒",
        }.get(status, "🌒")

    # =========================================================================
    # CHECK 1: Version Sync
    # =========================================================================
    def check_version_sync(self) -> CheckResult:
        """Verify version consistency across all files."""
        versions = {}
        details = []

        # pyproject.toml (primary source of truth)
        pyproject = self.root / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text()
            match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            if match:
                versions["pyproject.toml"] = match.group(1)
                self.version = match.group(1)
                details.append(f"pyproject.toml: {match.group(1)}")

        # empirica/__init__.py
        init_file = self.root / "empirica" / "__init__.py"
        if init_file.exists():
            content = init_file.read_text()
            match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                versions["empirica/__init__.py"] = match.group(1)
                details.append(f"empirica/__init__.py: {match.group(1)}")

        # CLAUDE.md system prompt version (look for "Lean v" pattern)
        claude_md_paths = [
            Path.home() / ".claude" / "CLAUDE.md",
            self.root / "CLAUDE.md",
            self.root / "docs" / "CLAUDE.md",
        ]
        for claude_md in claude_md_paths:
            if claude_md.exists():
                content = claude_md.read_text()
                match = re.search(r"Lean v(\d+\.\d+)", content)
                if match:
                    versions[f"CLAUDE.md ({claude_md})"] = f"prompt-v{match.group(1)}"
                    details.append(f"CLAUDE.md prompt: v{match.group(1)}")
                break

        # Copilot instructions
        copilot_md = self.root / ".github" / "copilot-instructions.md"
        if copilot_md.exists():
            content = copilot_md.read_text()
            match = re.search(r"Lean v(\d+\.\d+)", content)
            if match:
                versions["copilot-instructions.md"] = f"prompt-v{match.group(1)}"
                details.append(f"Copilot prompt: v{match.group(1)}")

        # Check package versions match
        pkg_versions = {k: v for k, v in versions.items() if not k.startswith("CLAUDE") and "copilot" not in k.lower()}
        unique_pkg_versions = set(pkg_versions.values())

        if len(unique_pkg_versions) == 1:
            status = AssessmentStatus.PASS
            message = f"Package version consistent: {next(iter(unique_pkg_versions))}"
        elif len(unique_pkg_versions) == 0:
            status = AssessmentStatus.FAIL
            message = "No version found in package files"
        else:
            status = AssessmentStatus.FAIL
            message = f"Package version mismatch: {pkg_versions}"

        result = CheckResult(name="Version Sync", status=status, message=message, details=details)
        result.moon = self._status_to_moon(status)
        return result

    # =========================================================================
    # CHECK 2: Architecture Assessment
    # =========================================================================
    def check_architecture(self) -> CheckResult:
        """Run turtle assessment on architecture."""
        if self.quick:
            return CheckResult(
                name="Architecture Assessment", status=AssessmentStatus.SKIP, message="Skipped (quick mode)", moon="🌒"
            )

        details = []
        scores = []
        unassessed = 0

        # Key directories to assess
        directories = [
            ("empirica/core", "Core modules"),
            ("empirica/cli", "CLI handlers"),
            ("empirica/data", "Data layer"),
        ]

        for dir_path, label in directories:
            full_path = self.root / dir_path
            if not full_path.exists():
                details.append(f"{label}: not found")
                continue

            try:
                result = subprocess.run(
                    ["empirica", "assess-directory", str(full_path), "--output", "json"],
                    capture_output=True,
                    text=True,
                    cwd=self.root,
                    timeout=60,
                )
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    avg_health = data.get("average_health", 0)
                    scores.append(avg_health)
                    moon = self._score_to_moon(avg_health)
                    details.append(f"{label}: {moon} {avg_health:.2f}")
                else:
                    unassessed += 1
                    details.append(f"{label}: assessment failed")
            except subprocess.TimeoutExpired:
                unassessed += 1
                details.append(f"{label}: timeout")
            except json.JSONDecodeError:
                unassessed += 1
                details.append(f"{label}: invalid JSON output")
            except Exception as e:
                unassessed += 1
                details.append(f"{label}: {str(e)[:50]}")

        if scores:
            avg_score = sum(scores) / len(scores)
            moon = self._score_to_moon(avg_score)

            if avg_score >= 0.70:
                status = AssessmentStatus.PASS
                message = f"Architecture health: {moon} {avg_score:.2f}"
            elif avg_score >= 0.50:
                status = AssessmentStatus.WARN
                message = f"Architecture needs attention: {moon} {avg_score:.2f}"
            else:
                status = AssessmentStatus.FAIL
                message = f"Architecture unhealthy: {moon} {avg_score:.2f}"
            if unassessed:
                # The average covers only the directories that answered; a PASS built on a subset must say so. It never turns a
                # WARN or FAIL into something better.
                if status == AssessmentStatus.PASS:
                    status = AssessmentStatus.WARN
                message += f" ({unassessed} of {len(directories)} directories could not be assessed)"
        else:
            status = AssessmentStatus.WARN
            message = "Could not assess architecture"
            moon = "🌒"

        result = CheckResult(name="Architecture Assessment", status=status, message=message, details=details)
        result.moon = moon if scores else "🌒"
        return result

    # =========================================================================
    # CHECK 3: PyPI Package Check
    # =========================================================================
    def check_pypi_packages(self) -> CheckResult:
        """Check empirica and empirica-mcp on PyPI."""
        details = []
        issues = []

        packages = ["empirica", "empirica-mcp"]

        for pkg in packages:
            try:
                result = subprocess.run(["pip", "index", "versions", pkg], capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    # Parse versions from output
                    output = result.stdout + result.stderr
                    match = re.search(rf"{pkg}\s+\(([^)]+)\)", output)
                    if match:
                        latest = match.group(1)
                        details.append(f"{pkg}: latest={latest}")

                        # Compare with local version
                        if self.version and pkg == "empirica":
                            if latest != self.version:
                                details.append(f"  Local: {self.version} (newer)")
                    else:
                        details.append(f"{pkg}: available on PyPI")
                else:
                    # Try alternative method
                    result2 = subprocess.run(["pip", "show", pkg], capture_output=True, text=True, timeout=30)
                    if result2.returncode == 0:
                        match = re.search(r"Version:\s*(\S+)", result2.stdout)
                        if match:
                            details.append(f"{pkg}: installed={match.group(1)}")
                    else:
                        details.append(f"{pkg}: not found on PyPI")
                        if pkg == "empirica":
                            issues.append(f"{pkg} not on PyPI")
            except subprocess.TimeoutExpired:
                details.append(f"{pkg}: timeout")
            except Exception as e:
                details.append(f"{pkg}: {str(e)[:30]}")

        if issues:
            status = AssessmentStatus.FAIL
            message = f"PyPI issues: {', '.join(issues)}"
        else:
            status = AssessmentStatus.PASS
            message = "PyPI packages verified"

        result = CheckResult(name="PyPI Packages", status=status, message=message, details=details)
        result.moon = self._status_to_moon(status)
        return result

    def _parse_gitignore(self) -> list[str]:
        """Parse .gitignore and return list of ignored patterns."""
        gitignore = self.root / ".gitignore"
        if not gitignore.exists():
            return []

        patterns = []
        for line in gitignore.read_text().splitlines():
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith("#"):
                continue
            # Normalize pattern
            patterns.append(line.rstrip("/"))
        return patterns

    def _rel_parts(self, path: Path) -> tuple[str, ...]:
        """The path's components relative to the project root (an absolute path outside the root keeps all of its own)."""
        try:
            return path.relative_to(self.root).parts if path.is_absolute() else path.parts
        except ValueError:
            return path.parts

    @staticmethod
    def _gitignore_match(parts: tuple[str, ...], pattern: str) -> bool:
        """One gitignore pattern against a relative path, by component rather than by substring.

        A pattern with a slash (not counting a trailing one) is anchored to the root and matches the path or any directory prefix
        of it; one without matches any single component. The substring test this replaces made 'build' match 'rebuild_tools'
        and '.env' match '.env.production', so files the project does NOT ignore were reported as ignored and never scanned.
        """
        anchored = pattern.startswith("/") or "/" in pattern.strip("/")
        pattern = pattern.strip("/")
        if not pattern:
            return False
        if anchored:
            return any(fnmatch.fnmatch("/".join(parts[:i]), pattern) for i in range(1, len(parts) + 1))
        return any(fnmatch.fnmatch(part, pattern) for part in parts)

    def _is_gitignored(self, path: Path, gitignore_patterns: list[str]) -> bool:
        """Is a path ignored by the project's .gitignore? Later patterns win and '!' re-includes, as in git."""
        parts = self._rel_parts(path)
        ignored = False
        for raw in gitignore_patterns:
            negate = raw.startswith("!")
            if self._gitignore_match(parts, raw[1:] if negate else raw):
                ignored = not negate
        return ignored

    def _is_excluded(self, path: Path, exclude_dirs) -> bool:
        """Is a path inside an excluded directory, judged by its components RELATIVE to the project root?

        The test was `excl in str(path)` on the ABSOLUTE path, so a project checked out under any directory whose name contained
        'build', 'dist' or 'venv' had every forbidden file skipped and the scan passed falsely, and '*.egg-info' was compared
        as a literal and never matched.
        """
        return any(fnmatch.fnmatch(part, excl) for part in self._rel_parts(path) for excl in exclude_dirs)

    # =========================================================================
    # CHECK 4: Privacy/Security Scan
    # =========================================================================
    def _scan_forbidden_files(self, forbidden_file_patterns, exclude_dirs, gitignore_patterns):
        """Scan for forbidden files that are not gitignored. Returns list of issues."""
        issues = []
        for pattern in forbidden_file_patterns:
            found = list(self.root.glob(f"**/{pattern}"))
            for f in found:
                if self._is_excluded(f, exclude_dirs):
                    continue
                if self._is_gitignored(f, gitignore_patterns):
                    continue
                issues.append(f"FORBIDDEN (not gitignored): {f.relative_to(self.root)}")
        return issues

    def _scan_hardcoded_secrets(self, content_patterns):
        """Scan Python source files for hardcoded secrets. Returns list of issues."""
        issues = []
        # Every file, in a stable order: `[:100]` scanned an arbitrary hundred of several hundred and the result still said
        # "No sensitive content detected", so a secret past the cut passed.
        py_files = sorted(self.root.glob("empirica/**/*.py"))
        for py_file in py_files:
            try:
                content = py_file.read_text()
                for pattern in content_patterns:
                    if re.search(pattern, content):
                        issues.append(f"SECRET: {py_file.relative_to(self.root)}")
                        break
            except Exception:
                pass
        return issues

    def _scan_warning_patterns(self, warn_file_patterns, exclude_dirs, gitignore_patterns):
        """Scan for warning-level patterns. Returns list of warnings."""
        warnings = []
        for pattern in warn_file_patterns:
            found = list(self.root.glob(f"**/{pattern}"))
            for f in found[:2]:
                if self._is_excluded(f, exclude_dirs):
                    continue
                if self._is_gitignored(f, gitignore_patterns):
                    continue
                warnings.append(f"DEV FILE: {f.relative_to(self.root)}")

        user_data_dirs = [".empirica/sessions", ".qdrant_data", ".beads", "notebooks"]
        for dir_name in user_data_dirs:
            dir_path = self.root / dir_name
            if dir_path.exists() and dir_path.is_dir():
                if not self._is_gitignored(dir_path, gitignore_patterns):
                    warnings.append(f"USER DATA (not gitignored!): {dir_name}")

        gitignore = self.root / ".gitignore"
        if gitignore.exists():
            gitignore_content = gitignore.read_text()
            critical_ignores = [".env", "*.key", "*.pem", ".empirica/"]
            missing = [p for p in critical_ignores if p not in gitignore_content]
            if missing:
                warnings.append(f".gitignore missing: {missing}")
        return warnings

    def check_privacy_security(self) -> CheckResult:
        """Scan for sensitive, private, or dev content that shouldn't be released."""
        gitignore_patterns = self._parse_gitignore()

        forbidden_file_patterns = [
            ".env",
            ".env.local",
            ".env.production",
            "secrets.json",
            "credentials.json",
            "config.secret",
            "*.pem",
            "*.key",
            "id_rsa",
            "id_ed25519",
            ".aws/credentials",
            ".gcp/credentials",
        ]
        content_patterns = [
            r"sk-[a-zA-Z0-9]{20,}",
            r"ANTHROPIC_API_KEY\s*=\s*['\"][^'\"]+",
            r"password\s*=\s*['\"][^'\"]{8,}",
            r"secret\s*=\s*['\"][^'\"]{8,}",
            r"/home/\w+/",
            r"C:\\Users\\\w+\\",
        ]
        warn_file_patterns = [
            "*.draft*",
            "*.wip*",
            "*scratch*",
            "*tmp*",
            "*.bak",
            "*.backup",
            "*_old*",
            "research/*",
            "private/*",
            "internal/*",
        ]
        exclude_dirs = [
            ".git",
            ".venv",
            "venv",
            ".venv-mcp",
            "node_modules",
            "__pycache__",
            ".empirica",
            ".beads",
            ".qdrant_data",
            "dist",
            "build",
            "*.egg-info",
        ]

        issues = self._scan_forbidden_files(forbidden_file_patterns, exclude_dirs, gitignore_patterns)
        issues.extend(self._scan_hardcoded_secrets(content_patterns))

        warnings = self._scan_warning_patterns(warn_file_patterns, exclude_dirs, gitignore_patterns)

        # Check for missing .gitignore
        if not (self.root / ".gitignore").exists():
            issues.append("No .gitignore file found!")

        details = issues + warnings
        if issues:
            status = AssessmentStatus.FAIL
            message = f"SECURITY: {len(issues)} forbidden items found"
        elif warnings:
            status = AssessmentStatus.WARN
            message = f"Privacy: {len(warnings)} items to review"
        else:
            status = AssessmentStatus.PASS
            message = "No sensitive content detected"

        result = CheckResult(name="Privacy/Security Scan", status=status, message=message, details=details[:10])
        result.moon = self._status_to_moon(status)
        return result

    # =========================================================================
    # CHECK 5: Documentation Assessment
    # =========================================================================
    def check_documentation(self) -> CheckResult:
        """Verify documentation is current."""
        details = []
        issues = []

        # Check key documentation files exist
        required_docs = [
            ("README.md", "Main readme"),
            ("CHANGELOG.md", "Changelog"),
            ("docs/", "Documentation directory"),
        ]

        for path, label in required_docs:
            full_path = self.root / path
            if full_path.exists():
                details.append(f"{label}: exists")
            else:
                issues.append(f"{label}: MISSING")

        # Check CHANGELOG has entry for current version
        changelog = self.root / "CHANGELOG.md"
        if changelog.exists() and self.version:
            content = changelog.read_text()
            if self.version in content:
                details.append(f"CHANGELOG: has v{self.version} entry")
            else:
                issues.append(f"CHANGELOG: missing v{self.version} entry")

        # Check README is not placeholder
        readme = self.root / "README.md"
        if readme.exists():
            content = readme.read_text()
            if len(content) < 500:
                issues.append("README: too short (placeholder?)")
            elif "TODO" in content or "FIXME" in content:
                issues.append("README: contains TODO/FIXME")

        if issues:
            status = AssessmentStatus.WARN
            message = f"Docs need attention: {len(issues)} issues"
        else:
            status = AssessmentStatus.PASS
            message = "Documentation verified"

        result = CheckResult(name="Documentation", status=status, message=message, details=details + issues)
        result.moon = self._status_to_moon(status)
        return result

    # =========================================================================
    # CHECK 6: Git Status
    # =========================================================================
    def check_git_status(self) -> CheckResult:
        """Check git status and branch."""
        details = []
        issues = []

        try:
            # Current branch
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, cwd=self.root
            )
            branch = result.stdout.strip()
            details.append(f"Branch: {branch}")

            if branch not in ["main", "master", "develop"]:
                issues.append(f"Not on main branch: {branch}")

            # Uncommitted changes
            result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=self.root)
            if result.stdout.strip():
                changes = len(result.stdout.strip().split("\n"))
                issues.append(f"Uncommitted changes: {changes} files")
            else:
                details.append("Working tree: clean")

            # Unpushed commits
            result = subprocess.run(
                ["git", "log", "@{u}..", "--oneline"], capture_output=True, text=True, cwd=self.root
            )
            if result.returncode != 0:
                # `@{u}..` fails (exit 128, empty stdout) when the branch has no upstream. An empty stdout was read as
                # "nothing unpushed", so a new local branch reported "Remote: up to date" with nothing pushed at all.
                issues.append("No upstream configured: cannot verify the pushed state")
            elif result.stdout.strip():
                commits = len(result.stdout.strip().split("\n"))
                issues.append(f"Unpushed commits: {commits}")
            else:
                details.append("Remote: up to date")

        except Exception as e:
            details.append(f"Git check error: {str(e)[:50]}")

        if issues:
            status = AssessmentStatus.WARN
            message = f"Git issues: {len(issues)}"
        else:
            status = AssessmentStatus.PASS
            message = "Git ready for release"

        result = CheckResult(name="Git Status", status=status, message=message, details=details + issues)
        result.moon = self._status_to_moon(status)
        return result

    # =========================================================================
    # Main Run
    # =========================================================================
    def run(self) -> dict[str, Any]:
        """Run all epistemic release checks."""
        checks = [
            self.check_version_sync,
            self.check_architecture,
            self.check_pypi_packages,
            self.check_privacy_security,
            self.check_documentation,
            self.check_git_status,
        ]

        for check in checks:
            try:
                result = check()
                self.results.append(result)
            except Exception as e:
                self.results.append(
                    CheckResult(
                        name=check.__name__.replace("check_", "").replace("_", " ").title(),
                        status=AssessmentStatus.FAIL,
                        message=f"Check failed: {str(e)[:50]}",
                        moon="🌑",
                    )
                )

        # Calculate overall status
        statuses = [r.status for r in self.results]
        if AssessmentStatus.FAIL in statuses:
            overall_status = "NOT READY"
            overall_moon = "🌑"
        elif AssessmentStatus.WARN in statuses:
            overall_status = "READY WITH WARNINGS"
            overall_moon = "🌓"
        else:
            overall_status = "READY"
            overall_moon = "🌕"

        return {
            # `ok` is "nothing FAILED": a verdict of READY WITH WARNINGS is ok, as the text it prints says. `clean` is the
            # strict reading (no warnings either); the handler picks one by --strict. `ok` used to mean `clean`, so the process
            # exited 1 under a line that said "RELEASE READY (with warnings)".
            "ok": AssessmentStatus.FAIL not in statuses,
            "clean": overall_status == "READY",
            "status": overall_status,
            "moon": overall_moon,
            "version": self.version,
            "checks": [r.to_dict() for r in self.results],
            "summary": {
                "pass": sum(1 for r in self.results if r.status == AssessmentStatus.PASS),
                "warn": sum(1 for r in self.results if r.status == AssessmentStatus.WARN),
                "fail": sum(1 for r in self.results if r.status == AssessmentStatus.FAIL),
                "skip": sum(1 for r in self.results if r.status == AssessmentStatus.SKIP),
            },
        }


def handle_release_ready_command(args):
    """Handle release-ready command - Epistemic release assessment."""
    try:
        project_root = Path(getattr(args, "project_root", None) or os.getcwd())
        quick = getattr(args, "quick", False)
        output_format = getattr(args, "output", "human")

        strict = bool(getattr(args, "strict", False))

        agent = EpistemicReleaseAgent(project_root=project_root, quick=quick)
        result = agent.run()
        # Warnings pass by default (exit 0) and fail under --strict: the exit code and the printed verdict now agree.
        result["strict"] = strict
        if strict:
            result["ok"] = bool(result.get("clean"))

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            # Human-readable output
            print()
            print("=" * 60)
            print(f"  {result['moon']} EPISTEMIC RELEASE ASSESSMENT")
            print("=" * 60)
            print()

            if result["version"]:
                print(f"  Version: {result['version']}")
                print()

            for check in result["checks"]:
                status_icon = {"pass": "✅", "warn": "⚠️", "fail": "❌", "skip": "⏭️"}.get(check["status"], "?")

                print(f"{check['moon']} {check['name']}")
                print(f"   {status_icon} {check['message']}")

                for detail in check["details"][:5]:
                    print(f"      • {detail}")
                print()

            print("=" * 60)
            summary = result["summary"]
            print(
                f"  Summary: {summary['pass']} pass, {summary['warn']} warn, "
                f"{summary['fail']} fail, {summary['skip']} skip"
            )
            print()

            if result["status"] == "READY":
                print("  🌕 RELEASE READY")
            elif result["status"] == "READY WITH WARNINGS" and strict:
                print("  🌑 NOT READY (--strict: warnings fail the gate)")
            elif result["status"] == "READY WITH WARNINGS":
                print("  🌓 RELEASE READY (with warnings)")
            else:
                print("  🌑 NOT READY FOR RELEASE")
            print("=" * 60)
            print()

        return 0 if result["ok"] else 1

    except Exception as e:
        handle_cli_error(e, "release-ready", getattr(args, "output", "json"))
        return 1


def handle_release_command(args):
    """Handle release command — thin wrapper around scripts/release.py.

    Runs the release pipeline as a subprocess, passing through all flags.
    This is a mechanical pipeline command (work_type=release) and does NOT
    require PREFLIGHT/POSTFLIGHT.
    """
    # Find the repo root (scripts/release.py lives at repo root)
    repo_root = Path(os.getcwd())
    release_script = repo_root / "scripts" / "release.py"

    if not release_script.exists():
        print(f"Error: scripts/release.py not found at {release_script}", file=sys.stderr)
        return 1

    # Build subprocess command, forwarding flags
    cmd = [sys.executable, str(release_script)]

    if getattr(args, "dry_run", False):
        cmd.append("--dry-run")
    if getattr(args, "prepare", False):
        cmd.append("--prepare")
    if getattr(args, "publish", False):
        cmd.append("--publish")
    if getattr(args, "version_only", False):
        cmd.append("--version-only")
    if getattr(args, "old_version", None):
        cmd.extend(["--old-version", args.old_version])

    try:
        result = subprocess.run(cmd, cwd=str(repo_root))
        return result.returncode
    except Exception as e:
        print(f"Error running release script: {e}", file=sys.stderr)
        return 1
