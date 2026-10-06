#!/usr/bin/env python3
"""Run ONE action task: a headless `claude -p` edits a clean clone, then the tagger's own test and the related tests judge the result.

    python3 run_action.py REPO PROMPT.md --model ID --test-path tests/test_x.py --test-file SRC.py --scope a.py b.py \\
        --python PY --out DIR [--budget 1.5] [--sandbox bwrap|none] [--venv-bin DIR]

REPO must be a throwaway clone at the base commit (the agent edits it). The agent sees only PROMPT.md: give it the objective, the
predicted steps and the file scope, never the hidden test. Afterwards:
  1. the diff against the base commit is kept (DIR/diff.patch) and files outside --scope are listed;
  2. the hidden test (--test-file) is copied to --test-path, run, and removed;
  3. existing tests that mention the touched modules are run; any that fail are re-run on a pristine export of the base commit, so
     a regression means "fails now, passed before" and a failure that already existed is not blamed on the agent.
success = the hidden test passes AND there is no regression. Scope violations and untracked files are reported, not scored.
It does not judge whether the diff is GOOD (a comment-only change can pass a weak test): read the diff, or hand it to a reviewer.

--sandbox bwrap hides the real ~/.claude, ~/.empirica and ~/.config behind empty tmpfs mounts (the login file stays readable) and
confines writes to REPO. `claude -p` runs with --permission-mode dontAsk and an allow-list, so nothing raises a prompt for a human.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

READ_ONLY = ["Read", "Bash(rg *)", "Bash(fd *)", "Bash(sed -n *)", "Bash(wc *)", "Bash(head *)", "Bash(tail *)", "Bash(cat *)", "Bash(ls *)",
             "Bash(git log *)", "Bash(git show *)", "Bash(git diff *)"]
EDIT = ["Edit", "Write", "Bash(python3 -m pytest *)"]


def sandbox_prefix(repo: Path, home: Path | None = None, venv_bin: str | None = None) -> list[str]:
    home = Path(home or Path.home())
    path = ":".join([p for p in (venv_bin, str(home / ".local/bin"), "/usr/local/bin", "/usr/bin", "/bin") if p])
    cmd = ["bwrap", "--bind", "/", "/"]
    for hidden in (".claude", ".empirica", ".config"):
        cmd += ["--tmpfs", str(home / hidden)]
    cmd += ["--ro-bind", str(home / ".claude/.credentials.json"), str(home / ".claude/.credentials.json")]
    cmd += ["--bind", str(repo), str(repo), "--dev", "/dev", "--proc", "/proc", "--unshare-pid", "--die-with-parent", "--chdir", str(repo)]
    return [*cmd, "env", "-i", f"PATH={path}", f"HOME={home}", "TERM=dumb"]


def build_command(sandbox: str, repo: Path, home: Path | None, claude_bin: str, model: str, prompt: str, budget: float, venv_bin: str | None) -> list[str]:
    claude = [claude_bin, "-p", prompt, "--model", model, "--tools", "Read,Edit,Write,Bash", "--allowedTools", *READ_ONLY, *EDIT,
              "--permission-mode", "dontAsk", "--permission-prompts", "none", "--output-format", "stream-json", "--verbose",
              "--max-budget-usd", str(budget), "--strict-mcp-config", "--setting-sources", "project", "--disable-slash-commands"]
    return [*sandbox_prefix(repo, home, venv_bin), *claude] if sandbox == "bwrap" else claude


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True).stdout


def _pytest(repo: Path, files: list[str], python: str, timeout: int) -> dict:
    if not files:
        return {"exit": 0, "failed": [], "tail": "no related tests", "class": "passes_on_base"}
    r = subprocess.run([python, "-m", "pytest", *files, "-q", "-p", "no:cacheprovider", "--tb=line"], cwd=repo, capture_output=True, text=True, timeout=timeout)
    failed = re.findall(r"^(?:FAILED|ERROR) (\S+)", r.stdout, re.M)
    last = (r.stdout.strip().splitlines() or [""])[-1]
    return {"exit": r.returncode, "failed": failed, "tail": last, "class": "passes_on_base" if r.returncode == 0 else ("reproduced" if r.returncode == 1 and re.search(r"AssertionError|^E\s+assert ", r.stdout, re.M) else "broken")}


def related_tests(repo: Path, changed: list[str], limit: int = 40) -> list[str]:
    found: set[str] = set()
    tests = sorted((repo / "tests").rglob("test_*.py")) if (repo / "tests").is_dir() else []
    for f in changed:
        if not f.endswith(".py") or f.startswith("tests/"):
            continue
        dotted, stem = f[:-3].replace("/", "."), Path(f).stem
        pat = re.compile(rf"{re.escape(dotted)}|\b{re.escape(stem)}\b")
        found.update(str(t.relative_to(repo)) for t in tests if pat.search(t.read_text(errors="ignore")))
    return sorted(found)[:limit]


def _export_base(repo: Path, sha: str, dest: Path) -> Path:
    data = subprocess.run(["git", "archive", sha], cwd=repo, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest)  # noqa: S202 - our own git archive, extracted under a temp dir
    return dest


def run_action(repo: Path, prompt: str, model: str, budget: float, scope: list[str], out: Path, python: str, test_path: str, test_source: str,
               claude_bin: str = "claude", sandbox: str = "bwrap", timeout: int = 1200, venv_bin: str | None = None, home: Path | None = None) -> dict:
    repo, out = Path(repo), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    base_sha = _git(repo, "rev-parse", "HEAD").strip()
    cmd = build_command(sandbox, repo, home, claude_bin, model, prompt, budget, venv_bin)
    t0 = time.time()
    with open(out / "stream.jsonl", "w") as stream, open(out / "stderr.txt", "w") as err:
        try:
            code = subprocess.run(cmd, cwd=repo, stdout=stream, stderr=err, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    meta: dict = {"model": model, "exit": code, "wall_s": round(time.time() - t0, 1), "budget_usd": budget}
    for line in (out / "stream.jsonl").read_text().splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "result":
            meta.update({"subtype": d.get("subtype"), "num_turns": d.get("num_turns"), "total_cost_usd": d.get("total_cost_usd"), "final_text": d.get("result")})
    (out / "diff.patch").write_text(_git(repo, "diff", base_sha))
    changed = _git(repo, "diff", "--name-only", base_sha).split()
    meta["files_changed"] = changed
    meta["files_untracked"] = _git(repo, "ls-files", "--others", "--exclude-standard").split()
    meta["outside_scope"] = [f for f in changed if f not in scope]

    hidden = repo / test_path
    hidden.parent.mkdir(parents=True, exist_ok=True)
    hidden.write_text(test_source)
    try:
        meta["hidden"] = _pytest(repo, [test_path], python, 300)
    finally:
        hidden.unlink(missing_ok=True)

    rel = related_tests(repo, changed)
    after = _pytest(repo, rel, python, 900)
    regressions: list[str] = []
    if after["failed"]:
        with tempfile.TemporaryDirectory() as tmp:
            pristine = _pytest(_export_base(repo, base_sha, Path(tmp)), sorted({f.split("::")[0] for f in after["failed"]}), python, 900)
        regressions = [f for f in after["failed"] if f not in pristine["failed"]]
    meta.update({"related_tests": rel, "related_after": after, "regressions": regressions})
    meta["success"] = meta["hidden"]["exit"] == 0 and not regressions
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    shutil.rmtree(repo / ".pytest_cache", ignore_errors=True)
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("repo")
    ap.add_argument("prompt")
    ap.add_argument("--model", required=True)
    ap.add_argument("--test-path", required=True)
    ap.add_argument("--test-file", required=True)
    ap.add_argument("--scope", nargs="+", required=True)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget", type=float, default=1.5)
    ap.add_argument("--sandbox", choices=["bwrap", "none"], default="bwrap")
    ap.add_argument("--venv-bin", default=None)
    ap.add_argument("--timeout", type=int, default=1200)
    a = ap.parse_args(argv)
    meta = run_action(Path(a.repo), Path(a.prompt).read_text(), a.model, a.budget, a.scope, Path(a.out), a.python, a.test_path,
                      Path(a.test_file).read_text(), sandbox=a.sandbox, timeout=a.timeout, venv_bin=a.venv_bin or str(Path(a.python).parent) if a.sandbox == "bwrap" else None)
    print(json.dumps({k: meta.get(k) for k in ("model", "exit", "success", "total_cost_usd", "wall_s", "outside_scope", "regressions")}))
    return 0 if meta["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
