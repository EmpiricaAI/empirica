#!/usr/bin/env python3
"""Run the tests a tagger wrote BEFORE any action against the UNMODIFIED base, and say which findings they reproduce.

    python3 run_base_tests.py TAG.json REPO --python PY [--import-probe PKG] [--wrap "bwrap ... --"] [--out OUT.json]

TAG.json is one tagger output ({unit, tasks: [{test_path, test_source, ...}]}), a list of them, or a flat list of
{id, test_path, test_source}. For each task the test file is written into REPO, run with pytest, and removed again.

Classes (from the exit code and output, no model):
  reproduced      pytest failed with an AssertionError: the test fails on the base the way the finding says
  passes_on_base  the test passes on the base: the finding is not reproduced by its own test
  broken          anything else (collection or import error, an exception that is not an assertion, a timeout). A LOWER BOUND on
                  reproduction: some exceptions are the defect itself, but they cannot be told from a broken test without reading.

CONTROLS RUN FIRST. A passing, a failing and a deliberately broken test go through the same runner, each importing --import-probe
(the package under test). If they do not classify as pass / fail / broken the run aborts: a wrong interpreter makes every test
"broken" and reads as a result about the tests. Name the interpreter explicitly (the venv that has the project's dependencies).
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

CONTROL_SOURCES = {
    "pass": "import {probe}\n\ndef test_control_pass():\n    assert True\n",
    "fail": "import {probe}\n\ndef test_control_fail():\n    assert 1 == 2\n",
    "broken": "import {probe}\nimport module_that_does_not_exist_for_controls\n\ndef test_control_broken():\n    pass\n",
}
EXPECTED = {"pass": "passes_on_base", "fail": "reproduced", "broken": "broken"}


def classify(code, output: str) -> str:
    if code == 0:
        return "passes_on_base"
    if code == 1 and re.search(r"AssertionError|^E\s+assert ", output, re.M):
        return "reproduced"
    return "broken"


def run_test(repo: Path, rel_path: str, source: str, python: str, wrap: list[str], timeout: int = 120) -> dict:
    dst = Path(repo) / rel_path
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(source)
    try:
        r = subprocess.run([*wrap, python, "-m", "pytest", rel_path, "-q", "-p", "no:cacheprovider", "--tb=line", "-x"],
                           cwd=repo, capture_output=True, text=True, timeout=timeout)
        out, code = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        out, code = "timeout", "timeout"
    finally:
        dst.unlink(missing_ok=True)
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    return {"exit": code, "class": classify(code, out), "tail": tail, "output": out[-1500:]}


def run_controls(repo: Path, python: str, wrap: list[str], import_probe: str) -> dict[str, str]:
    got = {}
    for name, src in CONTROL_SOURCES.items():
        got[name] = run_test(repo, f"tests/test_pipe_control_{name}.py", src.format(probe=import_probe), python, wrap)["class"]
    if got != EXPECTED:
        raise RuntimeError(f"controls misclassified {got} (expected {EXPECTED}): fix the runner or interpreter before reading any result")
    return got


def tasks_from(payload) -> list[dict]:
    items = payload if isinstance(payload, list) else [payload]
    tasks: list[dict] = []
    for item in items:
        if "tasks" in item:
            tasks += [dict(t, id=f"{item.get('unit', 'x')}:{k}") for k, t in enumerate(item["tasks"])]
        else:
            tasks.append(item)
    return tasks


def run_all(tasks: list[dict], repo: Path, python: str, wrap: list[str], import_probe: str) -> list[dict]:
    run_controls(repo, python, wrap, import_probe)
    return [dict(run_test(repo, t["test_path"], t["test_source"], python, wrap), id=t["id"]) for t in tasks]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tag")
    ap.add_argument("repo")
    ap.add_argument("--python", required=True)
    ap.add_argument("--import-probe", default="empirica")
    ap.add_argument("--wrap", default="")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    rows = run_all(tasks_from(json.loads(Path(a.tag).read_text())), Path(a.repo), a.python, shlex.split(a.wrap), a.import_probe)
    for r in rows:
        print(r["id"], r["class"], r["tail"][:90])
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["class"]] = counts.get(r["class"], 0) + 1
    print(counts, file=sys.stderr)
    if a.out:
        Path(a.out).write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
