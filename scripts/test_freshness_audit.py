#!/usr/bin/env python3
"""Test Freshness Audit — the immune system for the test suite itself.

Tests are artifacts, and artifacts drift. We built a whole correction surface for
findings this week while the suite had no equivalent: nothing detects a test that
has quietly stopped meaning what it says.

**This is not a coverage tool.** Coverage asks "was this line executed?". These
checks ask the question coverage cannot: **"could this test have failed?"** A suite
can be green, high-coverage, and still be pinning a defect — measured instances
from one week in this repo alone:

  · Two tests asserted the API's `422 no resolve semantics` and `status='verified'`
    — both TRUE when written, both false after migrations 057/060, both green the
    whole time. The tests were guarding the drift.
  · The CRUD fixture hand-built `project_findings` WITHOUT its lifecycle columns,
    so fixture and endpoint agreed with each other and disagreed with production.
  · A peer shipped 32 green unit tests over four live production bugs, because the
    fixtures encoded the same wrong model as the code.
  · Four drift tests here passed *only because the bug they were adjacent to
    existed* — their premise was the defect.

Four checks, in descending order of how much they bit us:

1. STALE FIXTURE SCHEMA — a hand-built `CREATE TABLE` missing columns the real
   schema has. The dominant vector: 74 of 375 test files hand-build schemas.
2. UNFALSIFIABLE TEST — no assertion at all, so it can only fail by raising.
3. TAUTOLOGICAL ASSERT — `assert True`, `assert x == x`: cannot fail by
   construction. The unsatisfiable-predicate pattern, applied to tests.
4. FROZEN TEST OVER CHURNING CODE — a test file untouched while the module it
   names changed repeatedly. Weakest signal, reported separately, never a failure.

Output: JSON (default) or `--human`. Exit 0 always unless `--strict`; this is a
freshness *report*, and a sweep that blocks CI on a heuristic is one people
disable. `--baseline` prints counts only, for tracking direction over time —
per broccoli, *a rising count is the signal, not the absolute number*.

Usage:
    python3 scripts/test_freshness_audit.py                 # JSON report
    python3 scripts/test_freshness_audit.py --human         # readable
    python3 scripts/test_freshness_audit.py --check stale-fixture
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO / "tests"

#: Inline marker for a fixture that is stale ON PURPOSE — a positive control for
#: an old-schema code path, for instance. Put it on or just above the CREATE TABLE:
#:
#:     # freshness: intentional-stale — pre-057 schema, positive control for the 422 path
#:     CREATE TABLE project_findings (id TEXT PRIMARY KEY, finding TEXT);
#:
#: Marked fixtures are SKIPPED and COUNTED, never silently dropped — the count is
#: reported so a growing pile of exemptions is visible rather than invisible.
#:
#: This replaced a FILE-level exemption on 2026-07-31, after David asked whether
#: drift in the tests-about-the-tests is tracked. It was not: the file-level skip
#: had been applied at both call sites while its stated reason justified only one,
#: so the auditor was exempt from its own unfalsifiable/tautological checks and
#: reported zero findings against itself by construction — the `Exemption reports
#: clean forever` row, committed hours after I contributed that row upstream.
#:
#: The general lesson, and why this is per-fixture rather than per-file: **an
#: exemption must be scoped to the thing it is justified for.** A file is almost
#: never that thing.
_INTENTIONAL_STALE = "freshness: intentional-stale"

# `CREATE TABLE [IF NOT EXISTS] name ( ... )` — captures name + body.
_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)

#: Fixtures legitimately omit columns for speed. What makes an omission a
#: FRESHNESS signal rather than a design choice is that the column arrived in a
#: migration AFTER the fixture was written — that is drift, not minimalism. These
#: are the lifecycle columns migrations 057/060/061 added; a fixture lacking them
#: cannot exercise any correction path, which is exactly the blind spot that let
#: the API drift sit green.
_LIFECYCLE_COLUMNS = {
    "project_findings": {"is_resolved", "resolution", "resolved_timestamp", "superseded_by", "resolution_kind"},
    "project_dead_ends": {"is_invalidated", "invalidated_at", "invalidation_reason"},
    "mistakes_made": {"is_invalidated", "invalidated_at", "invalidation_reason"},
    "project_unknowns": {"is_resolved", "resolved_by", "resolved_timestamp"},
    "assumptions": {"status", "resolved_timestamp"},
}


def _real_schema() -> dict[str, set[str]]:
    """Column sets for the canonical schema, built by CREATING a fresh DB.

    Deliberately built from a REAL database rather than by parsing the schema
    modules: parsing would re-derive the same shape the fixtures got wrong, and a
    detector that shares its subject's assumptions is the wrong-domain-scan
    pattern. A live `SessionDatabase` runs every migration, so this is ground
    truth by construction.
    """
    sys.path.insert(0, str(REPO))
    from empirica.data.session_database import SessionDatabase

    with tempfile.TemporaryDirectory() as td:
        db = SessionDatabase(db_path=str(Path(td) / "schema_probe.db"))
        try:
            cur = db.conn.cursor()
            names = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            return {n: {c[1] for c in cur.execute(f"PRAGMA table_info({n})").fetchall()} for n in names}
        finally:
            db.close()


def _fixture_columns(body: str) -> set[str]:
    """Column names from a CREATE TABLE body — first token of each top-level part.

    SQL line comments are stripped FIRST. Without that, a `-- note` above a column
    definition swallows it: the comment has no terminating comma, so the parser
    reads comment-plus-column as one part and takes `--` as the name. Caught by
    running the detector against a fixture whose contents I had just written and
    could therefore falsify — it reported `is_resolved` missing from a table that
    visibly had it.
    """
    body = re.sub(r"--[^\n]*", "", body)
    cols: set[str] = set()
    depth = 0
    current: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            cols.add("".join(current).strip().split()[0] if "".join(current).strip() else "")
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        cols.add(tail.split()[0])
    # Constraint clauses aren't columns.
    return {c for c in cols if c and c.upper() not in {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT", "--"}}


def check_stale_fixtures(real: dict[str, set[str]], accepted: list | None = None) -> list[dict[str, Any]]:
    """Hand-built fixture schemas missing LIFECYCLE columns the real schema has.

    ``accepted`` collects fixtures carrying the intentional-stale marker so the
    caller can REPORT how many were skipped. A silent skip is how a checker starts
    reporting clean forever.
    """
    out = []
    accepted = accepted if accepted is not None else []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        # This detector's own test file is ENTIRELY fixture test-data for THIS
        # check — its CREATE TABLE strings are the positive and negative controls.
        # Exempting it here is scoped to the one check it is justified for, and it
        # is COUNTED below rather than skipped silently.
        #
        # The original version of this exemption was applied at two call sites while
        # its reason justified one, which made the auditor blind to unfalsifiable
        # drift in its own tests. Removing it wholesale was an over-correction; the
        # fix is scope, not absence.
        if path.name == "test_test_freshness_audit.py":
            accepted.append({"file": str(path.relative_to(REPO)), "table": "(whole file — fixture test-data)"})
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for match in _CREATE_TABLE.finditer(text):
            table, body = match.group(1), match.group(2)
            if table not in real or table not in _LIFECYCLE_COLUMNS:
                continue
            # Deliberately-stale fixtures are indistinguishable from accidental
            # ones by shape alone, so intent has to be DECLARED. Look in the 200
            # chars preceding the CREATE TABLE for the marker.
            preamble = text[max(0, match.start() - 200) : match.start()]
            if _INTENTIONAL_STALE in preamble or _INTENTIONAL_STALE in body:
                accepted.append({"file": str(path.relative_to(REPO)), "table": table})
                continue
            have = _fixture_columns(body)
            missing = (_LIFECYCLE_COLUMNS[table] & real[table]) - have
            if missing:
                out.append(
                    {
                        "check": "stale-fixture",
                        "file": str(path.relative_to(REPO)),
                        "table": table,
                        "missing_columns": sorted(missing),
                        "why": (
                            f"fixture builds {table} without {len(missing)} lifecycle column(s) the real "
                            "schema has — it cannot exercise any correction path, and a test that agrees "
                            "with the fixture can still disagree with production"
                        ),
                    }
                )
    return out


def _is_fixture(node) -> bool:
    """True for `@pytest.fixture` / `@fixture` — including the parametrised call form.

    A fixture may legitimately be NAMED `test_*` (e.g. `test_repo`) and contains no
    assertion by design; flagging it as an unfalsifiable test is a false positive,
    and it was the first thing this detector got wrong on real input. Caught by
    sampling its own output rather than trusting the count — the same discipline
    the checks themselves are about.
    """
    for dec in getattr(node, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name == "fixture":
            return True
    return False


def _walk_tests(tree: ast.AST):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
            and not _is_fixture(node)
        ):
            yield node


def check_unfalsifiable_and_tautological() -> list[dict[str, Any]]:
    """Tests with no assertion, and asserts that cannot fail."""
    out = []
    # NO self-exemption here: the auditor's own tests must be checkable for
    # assertion-free and tautological drift like any others. Who watches the
    # watcher is not a rhetorical question for a freshness tool.
    for path in sorted(TESTS_DIR.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rel = str(path.relative_to(REPO))
        for fn in _walk_tests(tree):
            asserts = [n for n in ast.walk(fn) if isinstance(n, ast.Assert)]
            # `pytest.raises` / `self.assertX` / a bare `raise` are real checks too.
            has_other = any(
                isinstance(n, ast.Attribute) and (n.attr == "raises" or n.attr.startswith("assert"))
                for n in ast.walk(fn)
            )
            if not asserts and not has_other:
                out.append(
                    {
                        "check": "unfalsifiable-test",
                        "file": rel,
                        "test": fn.name,
                        "line": fn.lineno,
                        "why": "no assertion — passes unless something raises, so it pins almost nothing",
                    }
                )
                continue
            for a in asserts:
                t = a.test
                tauto = (isinstance(t, ast.Constant) and bool(t.value)) or (
                    isinstance(t, ast.Compare)
                    and len(t.ops) == 1
                    and isinstance(t.ops[0], ast.Eq)
                    and ast.dump(t.left) == ast.dump(t.comparators[0])
                )
                if tauto:
                    out.append(
                        {
                            "check": "tautological-assert",
                            "file": rel,
                            "test": fn.name,
                            "line": a.lineno,
                            "why": "assertion cannot fail by construction — the unsatisfiable-predicate pattern, in a test",
                        }
                    )
    return out


def check_frozen_over_churn(min_code_commits: int = 8) -> list[dict[str, Any]]:
    """Test files untouched while the module they name changed repeatedly.

    The weakest of the four and reported as INFORMATIONAL: a stable test over
    churning code is often exactly right — a contract test that survives
    refactors is doing its job. It is worth surfacing only as a prompt to look,
    never as a defect, which is why it never counts toward `--strict`.
    """
    out = []

    def _commits(path: str) -> int:
        try:
            r = subprocess.run(
                ["git", "log", "--oneline", "--since=6 months ago", "--", path],
                capture_output=True,
                text=True,
                cwd=REPO,
                timeout=20,
            )
            return len(r.stdout.strip().splitlines()) if r.returncode == 0 else 0
        except Exception:
            return 0

    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        stem = path.stem[len("test_") :]
        candidates = list((REPO / "empirica").rglob(f"{stem}.py"))
        if len(candidates) != 1:
            continue  # ambiguous mapping — say nothing rather than guess
        code_commits = _commits(str(candidates[0].relative_to(REPO)))
        if code_commits < min_code_commits:
            continue
        if _commits(str(path.relative_to(REPO))) == 0:
            out.append(
                {
                    "check": "frozen-over-churn",
                    "severity": "informational",
                    "file": str(path.relative_to(REPO)),
                    "covers": str(candidates[0].relative_to(REPO)),
                    "code_commits_6mo": code_commits,
                    "why": "module changed repeatedly, its test did not — worth a look, not a defect",
                }
            )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit the test suite for drift and unfalsifiable tests")
    ap.add_argument("--human", action="store_true", help="readable output instead of JSON")
    ap.add_argument("--baseline", action="store_true", help="counts only, for tracking direction over time")
    ap.add_argument(
        "--check",
        choices=["stale-fixture", "unfalsifiable", "frozen", "all"],
        default="all",
    )
    ap.add_argument("--strict", action="store_true", help="exit 1 if any non-informational finding")
    args = ap.parse_args()

    findings: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    if args.check in ("stale-fixture", "all"):
        findings += check_stale_fixtures(_real_schema(), accepted)
    if args.check in ("unfalsifiable", "all"):
        findings += check_unfalsifiable_and_tautological()
    if args.check in ("frozen", "all"):
        findings += check_frozen_over_churn()

    counts: dict[str, int] = {}
    for f in findings:
        counts[f["check"]] = counts.get(f["check"], 0) + 1

    if args.baseline:
        print(json.dumps({"counts": counts, "total": len(findings), "accepted_intentional": len(accepted)}, indent=2))
        return 0

    if args.human:
        if not findings:
            print("✅ No test-freshness findings.")
            return 0
        print(f"🧪 Test freshness: {len(findings)} finding(s)"
              + (f" · {len(accepted)} intentional-stale fixture(s) accepted" if accepted else "") + "\n")
        for check, n in sorted(counts.items()):
            print(f"  {check}: {n}")
        print()
        for f in findings[:40]:
            loc = f"{f['file']}:{f.get('line', '')}".rstrip(":")
            print(f"  [{f['check']}] {loc}")
            print(f"      {f['why']}")
        if len(findings) > 40:
            print(f"\n  … {len(findings) - 40} more (use JSON output for the full list)")
    else:
        print(json.dumps({"ok": True, "counts": counts, "accepted_intentional": accepted, "findings": findings}, indent=2))

    if args.strict and any(f.get("severity") != "informational" for f in findings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
