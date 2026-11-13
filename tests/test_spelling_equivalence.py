"""The Sentinel gate and the decision record agree, read or write, on every ordinary spelling of a command (goal 3fcde812,
task 5: spelling equivalence).

The two parse flags separately. Over 56 spellings of sed, sort, find, curl, gh api and git branch (--x=y, --x y, -Xy,
clustered short flags, flags after operands), measured 2026-11-13: three disagreed, all the record's (sed -ni, sort -oFILE,
sort -uo read as reads). The gate is a discipline layer (David 2026-11-11): these are ordinary spellings, not a bypass hunt.
"""

from __future__ import annotations

import importlib.util as _ilu
import sys
from pathlib import Path

import pytest

from empirica.core import decision_record as dr

_ROOT = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration"


@pytest.fixture(scope="module")
def gate():
    for p in (str(_ROOT / "lib"), str(_ROOT / "hooks")):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = _ilu.spec_from_file_location("sentinel_gate_spelling", _ROOT / "hooks" / "sentinel-gate.py")
    assert spec and spec.loader
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _record_reads(cmd: str) -> bool:
    return not dr.infer_shell_effects(
        cmd, cwd="/work/proj", project_path="/work/proj", empirica_is_read=lambda c: False
    )["classes"]


FAMILIES = {
    "sed in place": [
        "sed -i 's/a/b/' f.txt",
        "sed -i.bak 's/a/b/' f.txt",
        "sed --in-place 's/a/b/' f.txt",
        "sed --in-place=.bak 's/a/b/' f.txt",
        "sed -ni 's/a/b/p' f.txt",
        "sed 's/a/b/' -i f.txt",
        "sed -e 's/a/b/' -i f.txt",
    ],
    "sed read": ["sed -n '1,5p' f.txt", "sed 's/a/b/' f.txt", "sed -e 's/a/b/' f.txt"],
    "sort output": [
        "sort -o out.txt in.txt",
        "sort -oout.txt in.txt",
        "sort --output=out.txt in.txt",
        "sort --output out.txt in.txt",
        "sort in.txt -o out.txt",
        "sort -uo out.txt in.txt",
    ],
    "sort read": ["sort in.txt", "sort -u in.txt", "sort -k2 -n in.txt"],
    "find delete": ["find . -name '*.tmp' -delete", "find . -delete", "find . -type f -name x -delete"],
    "find read": ["find . -name '*.py'", "find . -type f -newer x"],
    "curl write": [
        "curl -X POST https://x/api",
        "curl -XPOST https://x/api",
        "curl --request POST https://x/api",
        "curl --request=POST https://x/api",
        "curl -d a=1 https://x/api",
        "curl --data a=1 https://x/api",
        "curl --data=a=1 https://x/api",
        "curl -sXPOST https://x/api",
        "curl https://x/api -X POST",
    ],
    "curl read": [
        "curl -s https://x/api",
        "curl -sS -X GET https://x/api",
        "curl -XGET https://x/api",
        "curl -I https://x/api",
    ],
    "gh api write": [
        "gh api repos/o/r/issues -X POST",
        "gh api repos/o/r/issues --method POST",
        "gh api repos/o/r/issues --method=POST",
        "gh api -XPOST repos/o/r/issues",
        "gh api repos/o/r/issues -f title=x",
        "gh api repos/o/r/issues -ftitle=x",
        "gh api repos/o/r/issues --field title=x",
    ],
    "gh api read": ["gh api repos/o/r", "gh api repos/o/r --jq .name", "gh api -XGET repos/o/r"],
    "git branch write": [
        "git branch -D old",
        "git branch -d old",
        "git branch --delete old",
        "git branch -m a b",
        "git branch new",
    ],
    "git branch read": ["git branch", "git branch -a", "git branch --list 'x*'", "git branch -r"],
}

_CASES = [(family, cmd) for family, cmds in FAMILIES.items() for cmd in cmds]


@pytest.mark.parametrize("family, cmd", _CASES, ids=[c for _, c in _CASES])
def test_the_gate_and_the_record_agree_read_or_write(gate, family, cmd):
    gate_reads = bool(gate.is_safe_bash_command({"command": cmd}))
    assert gate_reads == _record_reads(cmd), f"[{family}] gate reads={gate_reads}, record reads={_record_reads(cmd)}"


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_each_family_is_classed_as_its_name_says(family):
    expect_read = family.endswith("read")
    assert all(_record_reads(c) == expect_read for c in FAMILIES[family]), family
