#!/usr/bin/env python3
"""Could this test suite have failed on ANY change to this file? A small mutation test, run in a throwaway worktree.

Coverage asks "was this line executed?"; `audit_test_assertions.py` asks "does this test assert anything?"; `negative_control.py` asks "would
these tests have caught THIS bug?". None asks "would they notice if this comparison were flipped?". This does: it writes one mutant of the
target at a time, runs the named tests with `-x`, and reports which mutants SURVIVED (the tests still passed). A survivor is either an
equivalent mutant (no observable behaviour changed: a log line, a timeout) or a behaviour nothing pins.

    python3 scripts/mutation_pilot.py empirica/core/claims.py tests/test_claims_*.py
    python3 scripts/mutation_pilot.py empirica/x.py tests/test_x.py --limit 80 --lines 100-180 --k "not test_scans_source"

Safety. The working tree of a checkout is often the editable install for every process on the machine, so NOTHING is mutated there: a
detached git worktree of HEAD is created in a temp directory, the named tests that are not committed yet are copied into it, and it is
removed at the end. Tests run with PYTHONPATH pointing at the worktree, and the run aborts unless the target imports from there.

Two controls run first, and the run aborts if either is not green:
  * BASELINE: the tests pass on the untouched target;
  * NULL MUTATION: the target re-printed with ast.unparse and NO change passes. This one exists because a test that scans the SOURCE TEXT
    of the target fails on every re-printed file and would "kill" every mutant for a reason that has nothing to do with the mutation (a
    first run of the CHECK handler read 220 of 220 killed; the real figure was 21%). Deselect such tests with --k and treat them as a finding.

What it mutates: comparison operators, + - * /, and/or, `not`, `if` conditions (negated), and numeric and boolean constants (+1 / flipped).
It skips docstrings, f-strings and logging calls. It does not mutate strings, return values or calls, so the kill rate is an upper bound on
what a full tool would report. A survivor list needs reading: equivalent mutants are expected (a 5-second timeout became 6).
Not run in CI, on purpose: one pass over a large module takes minutes.
"""

from __future__ import annotations

import argparse
import ast
import collections
import copy
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CMP = {
    ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.In: ast.NotIn, ast.NotIn: ast.In,
}  # fmt: skip
BIN = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Add, ast.Div: ast.Mult, ast.FloorDiv: ast.Mult}
LOGGERS = {"logger", "logging", "log"}


def _is_log_call(node: ast.Call) -> bool:
    f = node.func
    return isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in LOGGERS


def _is_docstring(node: ast.Expr) -> bool:
    return isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


class Sites(ast.NodeVisitor):
    """Every eligible node, in traversal order, as (index, kind, lineno). `Mutate` counts in the same order."""

    def __init__(self) -> None:
        self.sites: list[tuple[int, str, int]] = []

    def _add(self, node: ast.AST, kind: str) -> None:
        self.sites.append((len(self.sites), kind, getattr(node, "lineno", 0)))

    def visit_Call(self, node: ast.Call) -> None:
        if not _is_log_call(node):
            self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        return

    def visit_Expr(self, node: ast.Expr) -> None:
        if not _is_docstring(node):
            self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        for op in node.ops:
            if type(op) in CMP:
                self._add(node, "compare")
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if type(node.op) in BIN:
            self._add(node, "binop")
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self._add(node, "boolop")
        self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        if isinstance(node.op, ast.Not):
            self._add(node, "not")
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self._add(node, "if-negate")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, (bool, int, float)):
            self._add(node, "const")


class Mutate(ast.NodeTransformer):
    """Apply exactly the mutation at site index `want`."""

    def __init__(self, want: int) -> None:
        self.want, self.i, self.done = want, 0, False

    def _hit(self) -> bool:
        hit = self.i == self.want
        self.i += 1
        return hit

    def visit_Call(self, node: ast.Call):
        return node if _is_log_call(node) else self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr):
        return node

    def visit_Expr(self, node: ast.Expr):
        return node if _is_docstring(node) else self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare):
        for k, op in enumerate(node.ops):
            if type(op) in CMP and self._hit():
                node.ops[k] = CMP[type(op)]()
                self.done = True
        return self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp):
        if type(node.op) in BIN and self._hit():
            node.op = BIN[type(node.op)]()
            self.done = True
        return self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp):
        if self._hit():
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
            self.done = True
        return self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp):
        if isinstance(node.op, ast.Not) and self._hit():
            self.done = True
            return node.operand
        return self.generic_visit(node)

    def visit_If(self, node: ast.If):
        if self._hit():
            node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
            self.done = True
        return self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, (bool, int, float)) and self._hit():
            self.done = True
            value = (not node.value) if isinstance(node.value, bool) else node.value + 1
            return ast.copy_location(ast.Constant(value=value), node)
        return node


def _run(py: str, tests: list[str], cwd: Path, env: dict, k: str | None, timeout: int) -> subprocess.CompletedProcess:
    cmd = [py, "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider", "-x", *(["-k", k] if k else [])]
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target", help="repo-relative path of the module to mutate")
    ap.add_argument("tests", nargs="+", help="test files that should notice a change to it")
    ap.add_argument("--limit", type=int, default=None, help="mutate at most N sites (a seeded sample)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--lines", help="only mutate sites on these lines, e.g. 67-103,580-607")
    ap.add_argument("--k", help="pytest -k expression (use `not test_name` to deselect source-text scanners)")
    ap.add_argument("--timeout", type=int, default=120, help="seconds per mutant before it counts as killed (a hang)")
    args = ap.parse_args()

    repo = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    wt = Path(tempfile.mkdtemp(prefix="mutation-pilot-")) / "wt"
    subprocess.run(["git", "worktree", "add", "--detach", "-q", str(wt), "HEAD"], cwd=repo, check=True)
    try:
        for t in args.tests:  # tests not committed yet are copied in; committed ones are already there
            src, dst = repo / t, wt / t
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        return _pilot(args, repo, wt)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo, capture_output=True)
        shutil.rmtree(wt.parent, ignore_errors=True)


def _pilot(args: argparse.Namespace, repo: Path, wt: Path) -> int:
    target = wt / args.target
    original = target.read_text()
    env = {**os.environ, "PYTHONPATH": str(wt), "EMPIRICA_NO_TELEMETRY": "1"}
    py = sys.executable
    module = args.target[:-3].replace("/", ".")
    seen = subprocess.run(
        [py, "-c", f"import {module} as m; print(m.__file__)"], cwd=wt, env=env, capture_output=True, text=True
    )
    if Path(seen.stdout.strip()) != target:
        print(
            f"ABORT: {module} imports from {seen.stdout.strip() or seen.stderr.strip()[-200:]}, not the worktree copy",
            file=sys.stderr,
        )
        return 2
    base = _run(py, args.tests, wt, env, args.k, 600)
    if base.returncode != 0:
        print("ABORT: the baseline is not green:\n" + base.stdout[-800:], file=sys.stderr)
        return 2
    tree0 = ast.parse(original)
    target.write_text(ast.unparse(tree0))
    try:
        control = _run(py, args.tests, wt, env, args.k, 600)
    finally:
        target.write_text(original)
    if control.returncode != 0:
        failed = "\n".join(line for line in control.stdout.splitlines() if line.startswith("FAILED"))[:1500]
        print(
            "ABORT: NULL-MUTATION CONTROL FAILED. The target re-printed with no change does not pass, so a test reads its source text\n"
            "or unparse alters behaviour, and every mutant would be 'killed' by that artifact. Deselect it with --k:\n"
            + failed,
            file=sys.stderr,
        )
        return 3
    visitor = Sites()
    visitor.visit(tree0)
    ranges = [tuple(map(int, r.split("-"))) for r in args.lines.split(",")] if args.lines else None
    order = [i for i, _kind, line in visitor.sites if ranges is None or any(a <= line <= b for a, b in ranges)]
    random.Random(args.seed).shuffle(order)  # noqa: S311 - a reproducible sample of mutants, not a secret
    order = order[: args.limit]
    print(
        f"{len(visitor.sites)} sites in {args.target}, mutating {len(order)}; baseline and null-mutation control green",
        flush=True,
    )
    results: list[tuple[int, str, int, str]] = []
    try:
        for n, idx in enumerate(order, 1):
            mutator = Mutate(idx)
            mutated = mutator.visit(copy.deepcopy(tree0))
            ast.fix_missing_locations(mutated)
            if not mutator.done:
                continue
            target.write_text(ast.unparse(mutated))
            try:
                done = _run(py, args.tests, wt, env, args.k, args.timeout)
                status = "survived" if done.returncode == 0 else "killed"
            except subprocess.TimeoutExpired:
                status = "killed"  # a mutant that hangs the tests is noticed
            finally:
                target.write_text(original)
            results.append((idx, visitor.sites[idx][1], visitor.sites[idx][2], status))
            if n % 25 == 0:
                print(f"  {n}/{len(order)}", flush=True)
    finally:
        target.write_text(original)
    killed = sum(1 for r in results if r[3] == "killed")
    print(f"\nkilled {killed} of {len(results)} ({100 * killed / max(1, len(results)):.0f}%)")
    by_kind: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for _i, kind, _line, status in results:
        by_kind[kind][status] += 1
    for kind, counts in sorted(by_kind.items()):
        print(f"  {kind:10s} killed {counts['killed']:3d}  survived {counts['survived']:3d}")
    survivors = sorted((line, kind) for _i, kind, line, status in results if status == "survived")
    print("survivors (line, kind):", survivors)
    return 0


if __name__ == "__main__":
    sys.exit(main())
