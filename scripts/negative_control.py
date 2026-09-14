#!/usr/bin/env python3
"""Could the tests in this commit have caught the bug it fixes?

Coverage asks "was this line executed?". `test_freshness_audit.py` asks "has this
test quietly stopped meaning what it says?". Neither asks the question that
matters at the moment a fix ships:

    **Would these tests have FAILED against the code as it was before the fix?**

A test can carry real assertions, be freshly written, hand-build nothing stale,
and still be unable to catch the defect it was written for — because the data it
exercises never reaches the branch it claims to guard. It passes on the fix and
it would have passed on the bug. Nothing in a green suite distinguishes the two.

For a LIVE defect this is free: you watched the test fail before you fixed it.
For a LATENT one there is no red phase — the broken branch is unreachable from
natural data — so the test's ability to fail is itself an untested claim. That is
where this bit: four ordering tests written for a timestamp bug passed instantly,
and only a hand revert of the fix showed that three of them could fail at all.

THE PROPERTY IS ALREADY IN GIT. A bugfix commit contains both halves — the fix
and the tests. So: take the commit's tests, put them against the parent commit's
source, and run them. At least one must fail. No annotations, no declarations,
nothing for anyone to keep up to date.

    python3 scripts/negative_control.py                 # HEAD
    python3 scripts/negative_control.py <sha>           # any commit
    python3 scripts/negative_control.py --human
    python3 scripts/negative_control.py --strict        # exit 1 on a finding

NOT A GATE BY DEFAULT, deliberately, and for the reason `test_freshness_audit.py`
already argues: a sweep that blocks CI on a heuristic is one people disable. A
commit can legitimately have all-passing tests here — a pure refactor, a doc fix,
a new feature whose tests exercise nothing that previously existed. Those are
NOT_APPLICABLE or a judgment call for a human, never an automatic defect.

It never touches your working tree: the parent's source is assembled in a
throwaway git worktree.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: A commit whose tests all pass against the old code is only interesting if the
#: commit claims to FIX something. Refactors and docs legitimately pass.
_FIX_PREFIXES = ("fix", "bugfix", "hotfix")


def _git(*args: str, cwd: Path) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def _changed(sha: str, root: Path) -> tuple[list[str], list[str], list[str]]:
    """(test files at this commit, source files to revert, source files to delete).

    Deletion matters: a source file ADDED by the commit cannot be reverted to a
    parent that never had it. Removing it is the honest reconstruction — and the
    resulting import error is a legitimate failure, since a test for brand-new
    code could not have run before that code existed.
    """
    out = _git("diff", "--name-status", f"{sha}~1", sha, cwd=root)
    tests, revert, delete = [], [], []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        if status.startswith("D"):
            continue  # deleted by the commit; it exists in the parent, leave it
        if not path.endswith(".py"):
            continue
        if path.startswith("tests/") or Path(path).name.startswith("test_"):
            tests.append(path)
        elif status.startswith("A"):
            delete.append(path)
        else:
            revert.append(path)
    return tests, revert, delete


def _parse_pytest(stdout: str) -> dict:
    """Per-test outcomes from the short summary, plus the collected total."""
    failed = set(re.findall(r"^(?:FAILED|ERROR) (\S+?)(?:\s|$)", stdout, re.M))
    m = re.search(r"(\d+) passed", stdout)
    passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) (?:failed|error)", stdout)
    failed_n = int(m.group(1)) if m else 0
    collect_error = "ERROR collecting" in stdout or "INTERNALERROR" in stdout
    return {
        "failed_ids": sorted(failed),
        "passed": passed,
        "failed": failed_n,
        "collection_error": collect_error,
    }


def run(sha: str, root: Path | None = None) -> dict:
    """`root` is the repository to inspect — explicit so tests can point it at a fixture."""
    root = root or ROOT
    sha_full = _git("rev-parse", sha, cwd=root).strip()
    subject = _git("log", "-1", "--format=%s", sha_full, cwd=root).strip()
    tests, revert, delete = _changed(sha_full, root)

    verdict = {
        "commit": sha_full[:9],
        "subject": subject,
        "test_files": tests,
        "source_reverted": revert,
        "source_removed": delete,
    }

    if not tests:
        verdict["result"] = "NOT_APPLICABLE"
        verdict["why"] = "commit adds or changes no test files"
        return verdict
    if not revert and not delete:
        verdict["result"] = "NOT_APPLICABLE"
        verdict["why"] = "commit changes no source — nothing to put the tests against"
        return verdict

    tmp = Path(tempfile.mkdtemp(prefix="negctl-"))
    wt = tmp / "wt"
    try:
        _git("worktree", "add", "--detach", str(wt), sha_full, cwd=root)
        # The commit's TESTS against the parent's SOURCE.
        if revert:
            _git("checkout", f"{sha_full}~1", "--", *revert, cwd=wt)
        for p in delete:
            (wt / p).unlink(missing_ok=True)

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *tests],
            cwd=wt,
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        parsed = _parse_pytest(proc.stdout)
        verdict.update(parsed)
        verdict["result"] = "DISCRIMINATES" if (parsed["failed"] or parsed["collection_error"]) else "VACUOUS"
        verdict["why"] = (
            f"{parsed['failed']} of {parsed['failed'] + parsed['passed']} failed against the pre-fix source"
            if verdict["result"] == "DISCRIMINATES"
            else "every test passed against the pre-fix source — they cannot distinguish it from the fix"
        )
        # A fix whose tests all pass on the broken code is the finding. The same
        # result on a refactor is expected and says nothing.
        verdict["claims_a_fix"] = subject.split("(")[0].split(":")[0].strip().lower() in _FIX_PREFIXES
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=root, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)

    return verdict


def _human(v: dict) -> str:
    icon = {"DISCRIMINATES": "✅", "VACUOUS": "⚠️ ", "NOT_APPLICABLE": "➖"}[v["result"]]
    lines = [f"{icon} {v['result']}  {v['commit']}  {v['subject'][:72]}", f"   {v['why']}"]
    if v["result"] == "VACUOUS" and v.get("claims_a_fix"):
        lines.append("   This commit calls itself a fix. Tests that pass on the pre-fix source did not")
        lines.append("   verify the fix — they may be exercising a branch the bug never reached.")
    if v.get("failed_ids"):
        lines.append("   failed against pre-fix source (this is the good outcome):")
        lines += [f"     · {t}" for t in v["failed_ids"][:12]]
    if v["result"] == "DISCRIMINATES" and v.get("passed"):
        lines.append(f"   {v['passed']} also passed against the pre-fix source — normal if they guard")
        lines.append("   neighbouring behaviour, worth a look if they were written for this bug.")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("commit", nargs="?", default="HEAD")
    ap.add_argument("--human", action="store_true", help="readable output instead of JSON")
    ap.add_argument("--strict", action="store_true", help="exit 1 when a self-declared fix is VACUOUS")
    args = ap.parse_args()

    v = run(args.commit)
    print(_human(v) if args.human else json.dumps(v, indent=2))
    if args.strict and v["result"] == "VACUOUS" and v.get("claims_a_fix"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
