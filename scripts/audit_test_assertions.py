#!/usr/bin/env python3
"""Which tests could never fail on a wrong answer? — the HONEST count.

A test with no assertion can still be legitimate (an idempotency test whose
whole contract is "second run must not raise"), and a test that LOOKS
assertion-free can assert perfectly well through a same-module helper
(``_expect(401, None)``). A naive AST scan counts both as dead, and the first
run of exactly that scan reported 46 — spot-checks immediately found both false
classes, so 46 was never a number to act on.

This version resolves one level of same-module helper calls before calling a
test assertion-free, and separates the remainder into:

  no_raise_style   the body is only calls into the subject under test — a
                   deliberate "must not raise" contract. Weak but honest;
                   flagged for awareness, not as defects.
  dead             no assert anywhere reachable AND the body does more than
                   exercise the subject — these can pass on any behaviour and
                   are the real finding.

Why per-file helper resolution is enough: pytest fixtures and cross-module
helpers that assert are rare in this suite (checked by sampling), and going
deeper trades a legible one-pass tool for import-resolution machinery. If the
number matters more later, deepen it then.
"""

from __future__ import annotations

import ast
import pathlib
import sys


def _asserts_directly(node: ast.AST) -> bool:
    """Assert statement, raise, pytest.raises/fail, or unittest assert* call."""
    for n in ast.walk(node):
        if isinstance(n, (ast.Assert, ast.Raise)):
            return True
        if isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if name in ("raises", "fail", "xfail", "skip") or name.startswith("assert"):
                return True
        # `with pytest.raises(...)` appears as a Call inside With items — the
        # Call branch above already catches it; listed here so a reader does
        # not "fix" the walk by adding a With case that double-counts.
    return False


def _called_names(node: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            out.add(n.func.id)
    return out


def audit(root: str = "tests") -> dict[str, list[str]]:
    no_raise_style: list[str] = []
    dead: list[str] = []

    for path in sorted(pathlib.Path(root).rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue

        module_fns: dict[str, ast.FunctionDef] = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        helper_asserts = {name: _asserts_directly(fn) for name, fn in module_fns.items()}

        for name, fn in module_fns.items():
            if not name.startswith("test_"):
                continue
            # A `@pytest.fixture` named test_* is not collected as a test at
            # all — poor style (it shadows the test namespace) but not a dead
            # test. The first cut of this check skipped decorators entirely and
            # confidently reported six fixtures as "helpers misnamed test_*":
            # the third false class this tool produced, each caught only by
            # spot-checking its output against the source. An audit tool is a
            # detector, and detectors earn trust by being watched firing
            # correctly — including on their own false positives.
            if any(
                (isinstance(d, ast.Attribute) and d.attr == "fixture")
                or (isinstance(d, ast.Name) and d.id == "fixture")
                or (
                    isinstance(d, ast.Call)
                    and isinstance(d.func, (ast.Attribute, ast.Name))
                    and (getattr(d.func, "attr", None) == "fixture" or getattr(d.func, "id", None) == "fixture")
                )
                for d in fn.decorator_list
            ):
                continue
            if _asserts_directly(fn):
                continue
            # One level of same-module helpers: `_expect(...)` that asserts
            # makes the test a real test, whatever the naive scan says.
            if any(helper_asserts.get(called) for called in _called_names(fn)):
                continue

            label = f"{path}:{fn.lineno}:{name}"
            # A body that is nothing but expression-statement calls (plus the
            # docstring) is the "must not raise" shape — the call IS the test.
            body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
            # `with patch(...): call()` and `for x in ...: call()` are the same
            # must-not-raise shape wearing control flow — the first cut of this
            # classifier missed both and mis-filed them as dead, which
            # spot-checking caught before the number went anywhere.
            only_calls = all(
                (isinstance(s, ast.Expr) and isinstance(s.value, ast.Call))
                or isinstance(s, (ast.Assign, ast.With, ast.For))
                for s in body
            )
            # A test_-named function that RETURNS a value is not a weak test —
            # it is a HELPER misnamed into the collector: pytest runs it as a
            # test that can never fail, forever, and warns about the return.
            returns_value = any(isinstance(n, ast.Return) and n.value is not None for n in ast.walk(fn))
            if returns_value:
                dead.append(label + "  [helper misnamed test_*]")
            else:
                (no_raise_style if only_calls else dead).append(label)

    return {"no_raise_style": no_raise_style, "dead": dead}


if __name__ == "__main__":
    result = audit(sys.argv[1] if len(sys.argv) > 1 else "tests")
    print(f"must-not-raise style (weak but honest): {len(result['no_raise_style'])}")
    for t in result["no_raise_style"]:
        print(f"  {t}")
    print(f"\nDEAD (cannot fail on a wrong answer): {len(result['dead'])}")
    for t in result["dead"]:
        print(f"  {t}")
