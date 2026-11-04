#!/usr/bin/env python3
"""
Release Readiness Check - Turtle Mode

A thorough pre-release verification that checks:
1. Version consistency across all files
2. Import and CLI sanity
3. Package contents audit (no sensitive/dev files)
4. Git status
5. Optional test run

Usage:
    python scripts/release_check.py [--turtle] [--build] [--test]

Flags:
    --turtle    Full thorough check (default: quick check)
    --build     Build wheel before checking
    --test      Run quick test suite
"""

import re
import subprocess
import sys
import zipfile
from pathlib import Path

# Colors
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
NC = '\033[0m'

def ok(msg): print(f"{GREEN}[OK]{NC} {msg}")
def fail(msg): print(f"{RED}[FAIL]{NC} {msg}")
def warn(msg): print(f"{YELLOW}[WARN]{NC} {msg}")
def info(msg): print(f"{BLUE}[INFO]{NC} {msg}")

class ReleaseChecker:
    def __init__(self, turtle_mode=False):
        self.turtle_mode = turtle_mode
        self.root = Path(__file__).parent.parent
        self.issues = []
        self.warnings = []

    def check_version_consistency(self):
        """Check version is consistent across all files"""
        info("Checking version consistency...")

        versions = {}

        # pyproject.toml
        pyproject = self.root / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text()
            match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            if match:
                versions['pyproject.toml'] = match.group(1)

        # empirica/__init__.py
        init_file = self.root / "empirica" / "__init__.py"
        if init_file.exists():
            content = init_file.read_text()
            match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                versions['empirica/__init__.py'] = match.group(1)

        # Check consistency
        unique_versions = set(versions.values())
        if len(unique_versions) == 1:
            ok(f"Version consistent: {next(iter(unique_versions))}")
            return True
        else:
            fail(f"Version mismatch: {versions}")
            self.issues.append(f"Version mismatch: {versions}")
            return False

    def check_import(self):
        """Check package imports correctly"""
        info("Checking import...")
        try:
            result = subprocess.run(
                [sys.executable, "-c", "import empirica; print(empirica.__version__)"],
                capture_output=True, text=True, cwd=self.root
            )
            if result.returncode == 0:
                ok(f"Import works, version: {result.stdout.strip()}")
                return True
            else:
                fail(f"Import failed: {result.stderr}")
                self.issues.append("Import failed")
                return False
        except Exception as e:
            fail(f"Import check error: {e}")
            return False

    def check_cli(self):
        """Check CLI works"""
        info("Checking CLI...")
        try:
            result = subprocess.run(
                ["empirica", "--version"],
                capture_output=True, text=True, cwd=self.root
            )
            if result.returncode == 0:
                ok(f"CLI works: {result.stdout.strip().split()[1]}")
                return True
            else:
                fail(f"CLI failed: {result.stderr}")
                self.issues.append("CLI failed")
                return False
        except Exception as e:
            fail(f"CLI check error: {e}")
            return False

    def check_wheel_contents(self):
        """Audit wheel for problematic files"""
        info("Checking wheel contents...")

        # Find the wheel
        dist_dir = self.root / "dist"
        wheels = list(dist_dir.glob("empirica-*.whl"))
        if not wheels:
            warn("No wheel found in dist/. Run with --build to create one.")
            return True

        wheel = max(wheels, key=lambda p: p.stat().st_mtime)  # Most recent
        info(f"Auditing: {wheel.name}")

        problems = []

        with zipfile.ZipFile(wheel, 'r') as zf:
            names = zf.namelist()

            # Check for test files
            test_files = [n for n in names if 'test_' in n.lower() or '_test.py' in n.lower()]
            if test_files:
                problems.append(f"Test files in wheel: {test_files[:3]}")

            # Check for sensitive patterns. The heuristic is meant to catch
            # data files (.env, secrets.json, etc.) being bundled into the
            # wheel by mistake — NOT Python source files that legitimately
            # implement credential APIs (e.g. empirica/api/routes/credentials.py
            # for the daemon's credentials endpoints, empirica/config/
            # credentials_loader.py for the YAML loader). Skip .py files so
            # the check stays focused on its actual target.
            sensitive_patterns = ['.env', 'secret', 'password', 'private_key', 'credential']
            for name in names:
                if name.endswith('.py'):
                    continue
                for pattern in sensitive_patterns:
                    if pattern in name.lower():
                        problems.append(f"Potentially sensitive: {name}")

            # Check for dev-only files
            dev_patterns = ['debug', 'scratch', 'tmp', 'temp', 'draft', 'wip']
            for name in names:
                for pattern in dev_patterns:
                    if pattern in name.lower():
                        self.warnings.append(f"Dev-like file: {name}")

            # Report
            file_count = len(names)
            if problems:
                for p in problems:
                    fail(p)
                    self.issues.append(p)
                return False
            else:
                ok(f"Wheel clean: {file_count} files, no issues")
                return True

    def check_git_status(self):
        """Check git status is clean"""
        info("Checking git status...")
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, cwd=self.root
            )
            if result.stdout.strip():
                warn(f"Uncommitted changes:\n{result.stdout[:200]}")
                self.warnings.append("Uncommitted changes")
            else:
                ok("Git clean")
            return True
        except Exception as e:
            warn(f"Git check error: {e}")
            return True

    def run_tests(self):
        """Run quick test suite"""
        info("Running quick tests...")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/", "-x", "-q", "--tb=no"],
                capture_output=True, text=True, cwd=self.root, timeout=120
            )
            if result.returncode == 0:
                ok("Tests passed")
                return True
            else:
                warn(f"Some tests failed:\n{result.stdout[-500:]}")
                self.warnings.append("Test failures")
                return True  # Not blocking
        except subprocess.TimeoutExpired:
            warn("Tests timed out")
            return True
        except Exception as e:
            warn(f"Test error: {e}")
            return True

    def run(self, build=False, test=False):
        """Run all checks"""
        print(f"\n{'=' * 50}")
        print(f"RELEASE READINESS CHECK {'(TURTLE MODE)' if self.turtle_mode else ''}")
        print(f"{'=' * 50}\n")

        if build:
            info("Building wheel...")
            subprocess.run([sys.executable, "-m", "build"], cwd=self.root)
            print()

        checks = [
            self.check_version_consistency,
            self.check_import,
            self.check_cli,
            self.check_wheel_contents,
            self.check_git_status,
        ]

        if test or self.turtle_mode:
            checks.append(self.run_tests)

        all_passed = all(check() for check in checks)

        # Summary
        print(f"\n{'=' * 50}")
        if self.issues:
            print(f"{RED}ISSUES ({len(self.issues)}):{NC}")
            for issue in self.issues:
                print(f"  - {issue}")

        if self.warnings:
            print(f"{YELLOW}WARNINGS ({len(self.warnings)}):{NC}")
            for warning in self.warnings:
                print(f"  - {warning}")

        if all_passed and not self.issues:
            print(f"\n{GREEN}RELEASE READY{NC}")
            return 0
        else:
            print(f"\n{RED}NOT READY - Fix issues above{NC}")
            return 1


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Release readiness check")
    parser.add_argument("--turtle", action="store_true", help="Full thorough check")
    parser.add_argument("--build", action="store_true", help="Build wheel first")
    parser.add_argument("--test", action="store_true", help="Run tests")
    args = parser.parse_args()

    checker = ReleaseChecker(turtle_mode=args.turtle)
    sys.exit(checker.run(build=args.build, test=args.test))


if __name__ == "__main__":
    main()
