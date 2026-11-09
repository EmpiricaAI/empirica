"""Near-miss writes the Sentinel must gate: each shares a program, a verb or a flag spelling with a read (goal 546e7c33).

A read allowlist is a keyword matcher, and it fails in the direction that matters when a mutating command looks like a read:
a prefix that also starts a write, a flag that writes in one spelling and not another, a value read as a flag. This list is
the standing negative control; the reads beside it are the positive control, so a change that gates everything also fails.
Not here, by design: the `empirica` artifact verbs, which run in any phase, and `rg --replace` and `less +F`, which only read.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py"


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("sentinel_gate_near_miss", _HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "command",
    [
        "git stash",
        "git stash -u",
        "git tag -a v1 -m x",
        "git branch list",
        "git branch -D old",
        "git checkout -- .",
        "git restore src/app.py",
        "git reset --hard",
        "git clean -fdx",
        "git config user.name x",
        "git remote add up u",
        "git notes add -m x",
        "git worktree add ../w",
        "git fetch && git merge",
        "git pull",
        "git switch -c x",
        "gh api repos/o/r -XDELETE",
        "gh api --method=DELETE repos/o/r",
        "gh api repos/o/r/issues -fbody=x",
        "gh pr merge 1",
        "gh pr close 1",
        "gh release delete v1",
        "gh run rerun 1",
        "gh pr comment 1 -b x",
        "curl -XPOST https://x",
        "curl -d@b.json https://x",
        "curl -sXPUT https://x",
        "curl -o out https://x",
        "curl -O https://x/f",
        "wget https://x/f",
        "wget --post-data=a https://x",
        "sed -ni 's/a/b/' f",
        "sed -i.bak s/a/b/ f",
        "sed -e s/a/b/ -i f",
        "sort -o f f",
        "sort --output=f f",
        "find . -delete",
        "find . -exec rm {} +",
        "find . -fprint out",
        "fd -x rm",
        "fd -X rm",
        "yq -i .a=1 f.yaml",
        "yq --inplace .a=1 f.yaml",
        "jq . f > g",
        "tee f",
        "cat f | tee g",
        "ls > f",
        "ls; rm f",
        "lsof -t | xargs kill",
        "stat f && touch g",
        "head -c 10 /dev/zero > f",
        "tree -o out.txt",
        "file -C -m magic",
        "ruff check --fix",
        "ruff format",
        "ruff check --unsafe-fixes --fix",
        "awk '{print > \"o\"}' f",
        "awk 'BEGIN{system(\"rm f\")}'",
        "pip install x",
        "pip3 uninstall -y x",
        "python3 -m pip install x",
        "npm i x",
        "uv sync",
        "cargo add x",
        "empirica goals-list > f",
        "sqlite3 db 'DELETE FROM t'",
        'sqlite3 db "select 1; delete from t"',
        "sqlite3 db '.import f t'",
        'sqlite3 db ".output f"',
        "rg -l x | xargs rm",
        "rg --pre ./script x",
        "git grep -O vim x",
        "git log --output=f",
        "git diff --output=f",
        "git diff > f",
        "diff -u a b > p",
        "systemctl --user stop x",
        "tmux kill-server",
        "docker rm x",
        "docker stop x",
        "ssh host rm -rf /",
        "readlink -f x > y",
        "sha256sum f > s",
        "realpath x | sh",
        "bat --paging=never f > g",
        "env rm f",
        "nice rm f",
        "timeout 5 rm f",
        "xargs -a f rm",
        "watch rm f",
        "column -t f > g",
        "comm a b > c",
        "tokei > f",
        "printf x > f",
        "echo x >> ~/.bashrc",
        "pwd; rm -rf x",
    ],
)
def test_a_near_miss_write_is_gated(gate, command):
    assert not gate.is_safe_bash_command({"command": command})


@pytest.mark.parametrize(
    "command",
    [
        "git stash list",
        "git tag -l 'v1*'",
        "git branch -a",
        "git log --oneline -5",
        "git diff --stat",
        "gh run list --limit 1",
        "sed -n 1,5p f",
        "sed -e 's/-i/x/' f",
        "sort -u f",
        "find . -name '*.py'",
        "fd -e py",
        "yq .a f.yaml",
        "jq . f",
        "file -b f",
        "tree -L 2",
        "ruff check .",
        "rg -n x src",
        "ls -la",
        "stat f",
        "readlink -f x",
        "sha256sum f",
        "empirica goals-list",
    ],
)
def test_the_read_twin_is_not_gated(gate, command):
    assert gate.is_safe_bash_command({"command": command})
