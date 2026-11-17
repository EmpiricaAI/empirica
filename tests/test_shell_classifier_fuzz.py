"""Differential fuzzing of the Sentinel's shell reading, with bash as the oracle (goal 63a44ce6, David 2026-11-17).

Every review pass of 1.17.5 found the same class of defect: quotes, comments, escapes or substitutions read differently
from how bash reads them, so a write or a push looked like a read. Example tests kept missing the next combination.

Commands are generated from a small grammar in which every program is ``echo MK<n>`` and every substitution is
``printf %s SUB<k>Z | tr Z Y``, so the only things that run are echo, printf and tr, in a scratch directory. Bash runs
each command; what it printed says which statements ran (a line starting ``MK<n>``) and which substitutions happened
(``SUB<k>Y``). Two properties must hold, because their failure is the dangerous direction (something runs that the
classifier never judged):

- every statement bash ran is a statement the classifier sees;
- every substitution bash made is one the classifier finds.

The reverse (the classifier sees more than runs) is a false gate at worst and is not asserted.

Marked ``integration``: it needs bash 4+ on the machine, so it runs in the Nightly and in ``release.py --prepare``, not in
per-push CI. ``EMPIRICA_FUZZ_COUNT`` and ``EMPIRICA_FUZZ_SEED`` widen a local run.
"""

from __future__ import annotations

import os
import random
import re
import shutil
import subprocess

import pytest

from empirica.core import decision_record as dr

pytestmark = pytest.mark.integration

_BASH = shutil.which("bash", path="/usr/bin:/bin")
_COUNT = int(os.environ.get("EMPIRICA_FUZZ_COUNT", "1500"))
_SEED = int(os.environ.get("EMPIRICA_FUZZ_SEED", "1117"))
_MARK = re.compile(r"MK\d+")


def _bash_major() -> int:
    if not _BASH:
        return 0
    out = subprocess.run([_BASH, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, timeout=10)
    return int(out.stdout.strip() or 0)


needs_bash = pytest.mark.skipif(_bash_major() < 4, reason="needs bash 4+ as the oracle (macOS ships 3.2)")


class _Gen:
    """One random command, built so that only echo, printf and tr can run."""

    PLAIN = "abcxyz019._-"
    QUOTED = "abc xyz#;|&()$`\"\\'"

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.marks = 0
        self.subs = 0

    def substitution(self) -> str:
        self.subs += 1
        inner = f"printf %s SUB{self.subs}Z | tr Z Y"
        return f"$({inner})" if self.rng.random() < 0.6 else f"`{inner}`"

    def word(self) -> str:
        r = self.rng.random()
        if r < 0.25:
            return "".join(self.rng.choice(self.PLAIN) for _ in range(self.rng.randint(1, 4)))
        if r < 0.35:  # a # inside a word, or escaped, or right after an escape or a substitution
            return self.rng.choice(["a#b", "\\#x", "\\ #x", "x\\#", "\\(#x"]) + ("" if self.rng.random() < 0.5 else "y")
        if r < 0.5:  # single quotes: everything inside is literal
            body = "".join(c for c in (self.rng.choice(self.QUOTED) for _ in range(self.rng.randint(0, 6))) if c != "'")
            if self.rng.random() < 0.3:
                body += "$(printf %s SUB0Z | tr Z Y)"  # literal: must never count as made
            return f"'{body}'"
        if r < 0.75:  # double quotes: apostrophes and # are characters, $( ) and ` ` still substitute
            parts = []
            for _ in range(self.rng.randint(0, 3)):
                c = self.rng.random()
                if c < 0.3:
                    parts.append(self.rng.choice(["it's", "a'b", "#", "x#y", "; echo", "&& z", '\\"', "\\$(x)"]))
                elif c < 0.6:
                    parts.append(self.substitution())
                else:
                    parts.append("".join(self.rng.choice(self.PLAIN) for _ in range(self.rng.randint(1, 3))))
            return '"' + " ".join(parts) + '"'
        if r < 0.9:
            return self.substitution() + self.rng.choice(["", "#x", "y", "#"])
        return self.rng.choice(["\\$(x)", "\\`x\\`", "$((1))#q", "x=1#2"])

    def statement(self) -> str:
        self.marks += 1
        words = [f"echo MK{self.marks}"] + [self.word() for _ in range(self.rng.randint(0, 3))]
        return " ".join(words)

    def command(self) -> str:
        out = self.statement()
        for _ in range(self.rng.randint(0, 3)):
            if self.rng.random() < 0.2:  # a comment, which runs to the end of the line
                out += self.rng.choice([" #", "  # ", "\t#"]) + self.rng.choice(["", " c", "; echo MK999", " $(x)"])
                out += "\n"
            else:
                out += self.rng.choice([" ; ", " && ", " | ", "\n", ";"])
            out += self.statement()
        return out


def _run_bash(command: str, cwd) -> str:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(cwd), "LC_ALL": "C"}
    done = subprocess.run(
        [_BASH, "--norc", "--noprofile", "-c", command], cwd=cwd, env=env, capture_output=True, text=True, timeout=10
    )
    return done.stdout


def _ran(stdout: str) -> tuple[set[str], set[str]]:
    statements = {m.group(0) for line in stdout.splitlines() if (m := _MARK.match(line))}
    substitutions = set(re.findall(r"SUB(\d+)Y", stdout))
    return statements, substitutions


def _seen(command: str) -> tuple[set[str], set[str]]:
    """What the classifier reads: the statements it would judge, and the substitutions whose commands it classifies."""
    inners = [inner for _start, _end, inner in dr._substitutions(command)]
    substitutions = set(re.findall(r"SUB(\d+)Z", " ".join(inners)))
    statements: set[str] = set()
    for words in dr._statements(dr._flatten_substitutions(command)) or []:
        words = dr._without_keywords(words)
        if len(words) > 1 and words[0] == "echo" and _MARK.fullmatch(words[1]):
            statements.add(words[1])
    return statements, substitutions


def _hidden(count: int, seed: int, cwd) -> list[str]:
    """Commands where bash ran a statement or made a substitution the classifier did not see."""
    misses = []
    for i in range(count):
        command = _Gen(random.Random(seed * 100_003 + i)).command()  # noqa: S311 — reproducible test input, not crypto
        ran_st, ran_sub = _ran(_run_bash(command, cwd))
        seen_st, seen_sub = _seen(command)
        if (ran_st - seen_st) or (ran_sub - {"0"} - seen_sub):
            misses.append(f"{command!r}: ran {sorted(ran_st - seen_st)} subs {sorted(ran_sub - seen_sub)}")
        if len(misses) >= 5:
            break
    return misses


@needs_bash
def test_bash_never_runs_a_statement_or_substitution_the_classifier_did_not_see(tmp_path):
    misses = _hidden(_COUNT, _SEED, tmp_path)
    assert not misses, "\n".join(misses)


@needs_bash
def test_a_literal_substitution_is_never_made(tmp_path):
    """The generator's own sanity: a single-quoted $( ) prints as text, so SUB0 never becomes SUB0Y."""
    assert _run_bash("echo '$(printf %s SUB0Z | tr Z Y)'", tmp_path).strip() == "$(printf %s SUB0Z | tr Z Y)"


@needs_bash
def test_the_fuzzer_catches_the_scanners_it_replaced(tmp_path, monkeypatch):
    """Positive controls: the comment and quote handling 1.17.5 replaced must be caught, or the fuzzer proves nothing."""
    monkeypatch.setattr(dr, "_without_comments", lambda text: re.sub(r"#[^\n]*", "", text))  # any # starts a comment
    assert _hidden(400, 7, tmp_path), "a scanner that cuts at every # went unnoticed"
    monkeypatch.undo()
    monkeypatch.setattr(dr, "_literal_masked", lambda text: re.sub(r"'[^']*'", "''", text))  # apostrophes in "" too
    assert _hidden(400, 8, tmp_path), "a scanner that ignores double quotes went unnoticed"
