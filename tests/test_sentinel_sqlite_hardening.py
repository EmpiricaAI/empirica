"""Broccoli sweep for 1.14.7: the sqlite classifier, the new safe prefixes, and the closed-loop message.

A read-only reviewer found, and the first block below reproduces on main versus develop:

* `_strip_sql_literals` (added in f55d0d43e) did not know SQL comments, so a quote inside `-- it's` or `/* ' */` opened a fake
  literal that swallowed a real DROP. It returned safe on develop, unsafe on main: a regression, proven against real sqlite3.
* Older holes in the same function, unchanged from main: `-cmd`/`-init` values were skipped unread, only the FIRST positional was
  scanned, a dot-command on a later line was not seen, and `writefile()` wrote a file from a SELECT.
* `claude --version` and `git merge-base` matched as bare prefixes (`claude --version -p 'rm -rf x'`, `git merge-basefoo`).
* `_closed_loop_message` accused a command substitution even when the command would be refused without it (`rm -rf build $(touch x)`)
  and scanned heredoc bodies the classifier ignores.
"""

from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"
LIB = HOOKS.parent / "lib"


@pytest.fixture(scope="module")
def gate():
    sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location("sentinel_gate_sqlite_hardening", HOOKS / "sentinel-gate.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


def _safe(gate, command: str) -> bool:
    return gate.is_safe_bash_command({"command": command})


# ── comments are not literals ───────────────────────────────────────────────

COMMENT_BYPASSES = [
    "sqlite3 x.db \"SELECT 1 -- it's\n; DROP TABLE t; -- '\"",
    "sqlite3 x.db \"SELECT 1 /* ' */ ; DROP TABLE t; /* ' */\"",
    "sqlite3 x.db \"SELECT 1 -- it's\n; DELETE FROM t; -- '\"",
    "sqlite3 x.db \"SELECT 1 /* a */ ; /* ' */ UPDATE t SET x = 1; /* ' */\"",
]


@pytest.mark.parametrize("command", COMMENT_BYPASSES)
def test_a_quote_inside_a_comment_cannot_hide_a_write(gate, command):
    assert not _safe(gate, command)


def test_the_comment_bypass_is_real_sqlite_behaviour_not_a_theory(tmp_path):
    """Positive control for the cases above: sqlite3 itself executes the DROP that the broken scan could not see."""
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI not installed")
    db = tmp_path / "x.db"
    sqlite3.connect(db).executescript("CREATE TABLE t(x);")
    subprocess.run(["sqlite3", str(db), "SELECT 1 -- it's\n; DROP TABLE t; -- '"], check=False, capture_output=True)

    assert sqlite3.connect(db).execute("SELECT count(*) FROM sqlite_master WHERE name='t'").fetchone()[0] == 0


def test_a_write_word_inside_a_comment_is_not_a_statement(gate):
    assert _safe(gate, 'sqlite3 x.db "SELECT 1 -- don\'t update anything"')
    assert _safe(gate, 'sqlite3 x.db "SELECT 1 /* delete me later */"')


def test_a_comment_marker_inside_a_literal_is_data_so_what_follows_it_is_still_scanned(gate):
    assert not _safe(gate, "sqlite3 x.db \"SELECT '-- x'; DROP TABLE t\"")
    assert _safe(gate, "sqlite3 x.db \"SELECT '-- DROP TABLE t'\"")


# ── older holes in the same function ────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        'sqlite3 -cmd ".shell touch m" x.db "SELECT 1"',
        'sqlite3 -init evil.sql x.db "SELECT 1"',
        'sqlite3 x.db "SELECT 1" "DROP TABLE t"',
        'sqlite3 x.db "SELECT 1" ".shell touch m"',
        'sqlite3 x.db "SELECT 1\n.shell touch m"',
        'sqlite3 x.db "SELECT 1\n.output out.txt\nSELECT 2"',
        "sqlite3 x.db \"SELECT writefile('m','x')\"",
        "sqlite3 x.db \"SELECT load_extension('evil')\"",
    ],
)
def test_the_holes_that_were_already_there_are_closed(gate, command):
    assert not _safe(gate, command)


@pytest.mark.parametrize(
    "command",
    [
        'sqlite3 x.db "SELECT 1" "SELECT 2"',
        'sqlite3 x.db ".schema"',
        'sqlite3 -header -column x.db "SELECT 1"',
        'sqlite3 -cmd ".headers on" x.db "SELECT 1"',
        "sqlite3 x.db \"SELECT replace(a, 'x', 'y') FROM t\"",
        "sqlite3 x.db \"SELECT readfile('f')\"",
    ],
)
def test_the_reads_that_must_keep_working_still_work(gate, command):
    """Controls: tightening must not turn plain reads into denials."""
    assert _safe(gate, command)


def test_replace_into_is_still_a_write(gate):
    assert not _safe(gate, 'sqlite3 x.db "REPLACE INTO t VALUES (1)"')
    assert not _safe(gate, 'sqlite3 x.db "INSERT OR REPLACE INTO t VALUES (1)"')


# ── prefixes need a word boundary ───────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "claude --version -p 'rm -rf x'",
        "claude --versionx",
        "claude --version-evil",
        "git merge-basefoo",
        "git merge-base-x",
    ],
)
def test_a_prefix_match_must_end_at_a_word(gate, command):
    assert not _safe(gate, command)


@pytest.mark.parametrize(
    "command", ["claude --version", "git merge-base HEAD~1 HEAD", "git merge-base --is-ancestor a b"]
)
def test_the_exact_commands_still_pass(gate, command):
    assert _safe(gate, command)


# ── the refusal message accuses only the real cause ─────────────────────────


def _msg(gate, command):
    return gate._closed_loop_message("Bash", {"command": command})


def test_a_command_that_is_refused_anyway_is_not_blamed_on_its_substitution(gate):
    msg = _msg(gate, "rm -rf build $(touch x)")

    assert "command substitution" not in msg and "to start next goal" in msg


def test_backticks_in_a_quoted_heredoc_body_are_not_accused(gate):
    msg = _msg(gate, "git commit -F - <<'EOF'\nfix `foo bar` handling\nEOF")

    assert "command substitution" not in msg


def test_a_praxic_commit_with_backticks_is_not_accused(gate):
    assert "command substitution" not in _msg(gate, 'git commit -m "fix `foo`"')


def test_a_call_that_would_pass_without_its_substitution_is_accused(gate):
    """Control: the accusation still fires where the substitution really is the whole reason."""
    msg = _msg(gate, 'empirica goals-create --objective "x" --description "see `--windowed` here"')

    assert "command substitution" in msg and "--windowed" in msg


def test_a_redirect_is_not_mistaken_for_a_statement_and_a_file_redirect_still_gates(gate):
    assert _safe(gate, 'sqlite3 x.db "SELECT 1" 2>/dev/null')
    assert _safe(gate, 'sqlite3 x.db "SELECT 1" 2>&1')
    assert not _safe(gate, 'sqlite3 x.db "SELECT 1" > out.txt')
