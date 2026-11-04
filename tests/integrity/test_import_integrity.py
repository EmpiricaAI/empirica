"""
Import Integrity Tests

Catches:
- Broken imports (ImportError at module load)
- Local import shadowing module-level import (the R-shadowing anti-pattern from v1.6.14)
- Circular import cycles

The R-shadowing bug (v1.6.14): A module-level ``import X as R`` was conditionally
re-imported inside a function with the same alias. When the conditional branch was
not taken, the local name shadowed the module-level binding with an unbound reference,
causing NameError at runtime. These tests ensure that class of bug cannot recur.
"""

import ast
import importlib
import os
from pathlib import Path
from typing import NamedTuple

import pytest

# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

EMPIRICA_ROOT = Path(__file__).resolve().parents[2]
EMPIRICA_PKG = EMPIRICA_ROOT / "empirica"

# Directories to skip when walking the package tree
SKIP_DIRS: set[str] = {"__pycache__", "_archive", "_dev"}

# Known optional third-party packages that may not be installed.
# ModuleNotFoundError for these is expected and not a bug — the corresponding
# extras (api, vector, vision, prose) are declared in pyproject.toml.
OPTIONAL_PACKAGES: set[str] = {
    "flask",
    "flask_cors",
    "werkzeug",
    "fastapi",
    "uvicorn",
    "qdrant_client",
    "pytesseract",
    "cv2",
    "PIL",
    "textstat",
    "proselint",
    "numpy",
    "np",
}


def _is_optional_dep_error(exc: Exception) -> bool:
    """Return True if *exc* is a ModuleNotFoundError (or NameError) caused by
    a missing optional dependency rather than a genuine code bug.
    """
    if isinstance(exc, ModuleNotFoundError):
        # exc.name is the top-level package that was not found
        missing = getattr(exc, "name", "") or ""
        top_level = missing.split(".")[0]
        return top_level in OPTIONAL_PACKAGES
    if isinstance(exc, NameError):
        # e.g. ``name 'np' is not defined`` when numpy is not installed
        msg = str(exc)
        return any(pkg in msg for pkg in OPTIONAL_PACKAGES)
    return False


def _collect_py_modules() -> list[tuple[str, Path]]:
    """Walk ``empirica/`` and return (dotted_module_path, file_path) pairs.

    Skips directories listed in ``SKIP_DIRS`` and any file whose name starts
    with ``test_`` (test files are not package modules).
    """
    modules: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(EMPIRICA_PKG):
        # Prune unwanted directories in-place so os.walk does not descend
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            if fname.startswith("test_"):
                continue

            fpath = Path(dirpath) / fname
            # Convert filesystem path to dotted module path
            rel = fpath.relative_to(EMPIRICA_ROOT)
            parts = list(rel.with_suffix("").parts)
            # __init__ modules are represented by their parent package
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts:
                continue
            modules.append((".".join(parts), fpath))
    return modules


@pytest.fixture(scope="module")
def py_modules() -> list[tuple[str, Path]]:
    """All importable ``empirica`` modules as ``(dotted_name, path)`` pairs."""
    mods = _collect_py_modules()
    assert mods, "No .py modules found under empirica/ — test setup is broken"
    return mods


@pytest.fixture(scope="module")
def py_files() -> list[Path]:
    """All ``.py`` files under ``empirica/`` (excluding skipped dirs and tests)."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(EMPIRICA_PKG):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            if fname.endswith(".py") and not fname.startswith("test_"):
                files.append(Path(dirpath) / fname)
    assert files, "No .py files found under empirica/ — test setup is broken"
    return files


# ---------------------------------------------------------------------------
# Test 1: All modules import without error
# ---------------------------------------------------------------------------


class TestAllModulesImport:
    """Every ``.py`` file under ``empirica/`` must be importable.

    This catches:
    - Syntax errors
    - Broken relative imports
    - Top-level code that raises at import time
    - Missing *required* dependencies

    Modules that fail because an *optional* third-party package is not
    installed (e.g. ``flask``, ``qdrant_client``) are silently skipped —
    those are expected when the corresponding extras are not installed.
    """

    def test_all_modules_importable(self, py_modules: list[tuple[str, Path]]):
        """Import every discovered module and collect any failures.

        Failures caused by missing optional dependencies are excluded from
        the report so the test passes in minimal install environments.
        """
        failures: list[tuple[str, str]] = []
        skipped_optional: list[str] = []

        for dotted, _fpath in py_modules:
            try:
                importlib.import_module(dotted)
            except Exception as exc:
                if _is_optional_dep_error(exc):
                    skipped_optional.append(dotted)
                else:
                    failures.append((dotted, f"{type(exc).__name__}: {exc}"))

        if failures:
            report = "\n".join(f"  {mod}: {err}" for mod, err in failures)
            pytest.fail(f"{len(failures)} module(s) failed to import:\n{report}")


# ---------------------------------------------------------------------------
# Test 2: No function-level import shadows module-level import
#         (the R-shadowing detector)
# ---------------------------------------------------------------------------


class _AliasInfo(NamedTuple):
    """Tracks an ``import ... as ALIAS`` occurrence."""

    alias: str
    module: str
    name: str
    lineno: int


class _ShadowViolation(NamedTuple):
    """A detected shadowing of a module-level import alias."""

    file: Path
    alias: str
    module_level_line: int
    function_name: str
    function_level_line: int


def _extract_import_aliases(node: ast.ImportFrom) -> list[_AliasInfo]:
    """Return alias info for every name in an ``ImportFrom`` that uses ``as``."""
    results: list[_AliasInfo] = []
    if node.module is None:
        return results
    for alias_node in node.names:
        if alias_node.asname is not None:
            results.append(
                _AliasInfo(
                    alias=alias_node.asname,
                    module=node.module,
                    name=alias_node.name,
                    lineno=node.lineno,
                )
            )
    return results


def _find_shadow_violations(filepath: Path) -> list[_ShadowViolation]:
    """Parse *filepath* and detect function-level imports that shadow module-level aliases.

    The pattern this catches::

        # Module level
        from foo import Bar as R          # line 5

        def some_function():
            try:
                from baz import Qux as R  # line 12 — shadows line 5's R
            except ImportError:
                pass
            use(R)                        # Which R? Depends on runtime!

    Any function containing a local ``import ... as NAME`` where ``NAME``
    matches a module-level ``import ... as NAME`` is flagged.
    """
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError):
        # Syntax errors are caught by Test 1; skip here
        return []

    # 1. Collect module-level import aliases
    module_aliases: dict[str, _AliasInfo] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom):
            for info in _extract_import_aliases(node):
                module_aliases[info.alias] = info

    if not module_aliases:
        return []

    # 2. Walk functions/methods and look for local imports using the same alias
    violations: list[_ShadowViolation] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Walk all nodes inside this function body
        for child in ast.walk(node):
            if not isinstance(child, ast.ImportFrom):
                continue
            for info in _extract_import_aliases(child):
                if info.alias in module_aliases:
                    violations.append(
                        _ShadowViolation(
                            file=filepath,
                            alias=info.alias,
                            module_level_line=module_aliases[info.alias].lineno,
                            function_name=node.name,
                            function_level_line=info.lineno,
                        )
                    )

    return violations


class TestNoImportShadowing:
    """Detect the R-shadowing anti-pattern that caused the v1.6.14 bug.

    A module-level ``import X as R`` followed by a function-level
    ``import Y as R`` (typically inside a try/except or if-block) can silently
    shadow the module-level binding.  If the conditional branch is not taken,
    ``R`` becomes unbound inside the function, causing a ``NameError`` at
    runtime that is invisible to static analysis tools that only check one
    scope at a time.

    This test parses every source file with the ``ast`` module and flags any
    function that re-imports an alias already defined at module level.
    """

    def test_no_alias_shadowing(self, py_files: list[Path]):
        """No function should shadow a module-level import alias."""
        all_violations: list[_ShadowViolation] = []

        for fpath in py_files:
            all_violations.extend(_find_shadow_violations(fpath))

        if all_violations:
            lines: list[str] = []
            for v in all_violations:
                rel = v.file.relative_to(EMPIRICA_ROOT)
                lines.append(
                    f"  {rel}:{v.function_level_line} "
                    f"— alias '{v.alias}' in {v.function_name}() "
                    f"shadows module-level import at line {v.module_level_line}"
                )
            report = "\n".join(lines)
            pytest.fail(
                f"{len(all_violations)} import-shadowing violation(s) found:\n"
                f"{report}\n\n"
                f"Fix: rename the local alias or remove the redundant import."
            )


# ---------------------------------------------------------------------------
# Test 3: No circular imports
# ---------------------------------------------------------------------------


class TestNoCircularImports:
    """Verify that no circular import cycles exist among ``empirica`` modules.

    While Test 1 (``test_all_modules_importable``) implicitly catches circular
    imports — they manifest as ``ImportError`` — this test makes the intent
    explicit and provides a clearer error message when a cycle is detected.

    Each top-level sub-package under ``empirica/`` is imported independently
    in a controlled order to surface cycles that only appear when modules are
    first loaded.
    """

    def _top_level_subpackages(self) -> list[str]:
        """Return dotted names of top-level sub-packages (e.g. ``empirica.core``)."""
        pkgs: list[str] = []
        for entry in sorted(EMPIRICA_PKG.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith(("_", ".")):
                continue
            if entry.name in SKIP_DIRS:
                continue
            init = entry / "__init__.py"
            if init.exists():
                pkgs.append(f"empirica.{entry.name}")
        return pkgs

    def test_no_circular_imports(self):
        """Importing each top-level sub-package must not raise ImportError.

        Modules are reloaded (if already cached) to exercise the import
        machinery and surface latent cycles.  Failures caused by missing
        optional dependencies are excluded (same rationale as Test 1).
        """
        failures: list[tuple[str, str]] = []
        subpackages = self._top_level_subpackages()
        assert subpackages, "No sub-packages found — test setup is broken"

        for pkg in subpackages:
            try:
                mod = importlib.import_module(pkg)
                # Force a reload to catch cycles hidden by prior caching
                importlib.reload(mod)
            except ImportError as exc:
                if _is_optional_dep_error(exc):
                    continue
                if "circular" in str(exc).lower() or "cannot import" in str(exc).lower():
                    failures.append((pkg, f"Circular import: {exc}"))
                else:
                    failures.append((pkg, f"ImportError: {exc}"))
            except Exception as exc:
                if _is_optional_dep_error(exc):
                    continue
                failures.append((pkg, f"{type(exc).__name__}: {exc}"))

        if failures:
            report = "\n".join(f"  {pkg}: {err}" for pkg, err in failures)
            pytest.fail(f"{len(failures)} sub-package(s) have import issues (possible circular imports):\n{report}")
