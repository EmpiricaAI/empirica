"""A `||` inside a quoted SQL argument is string concatenation, not an OR-chain (scratchpad note, goal 4577152d).

A note recorded that a read-only `sqlite3 ... "SELECT a || b"` was gated as praxic twice while the same query with plain columns passed. Run against
the real top-level classifier at HEAD, nine variants (double and single quotes, a quoted `|` and `||` inside the SQL, a pipe to head, `-header`, a
`2>&1`) all classify as read-only, so the report does not reproduce here; whatever gated those two calls was another part of the command. These pin
the property so a regression is caught, with the control that matters beside it: the same text OUTSIDE the quotes is an OR-chain, and a chained
write stays gated.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HOOK_DIR = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture(scope="module")
def gate():
    sys.path.insert(0, str(_HOOK_DIR.parent / "lib"))
    spec = importlib.util.spec_from_file_location("sentinel_gate_sqlite_concat", _HOOK_DIR / "sentinel-gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _safe(gate, command: str) -> bool:
    return gate.is_safe_bash_command({"command": command})


@pytest.mark.parametrize(
    "command",
    [
        'sqlite3 db.sqlite "SELECT a || b FROM t"',
        "sqlite3 db.sqlite 'SELECT a || b FROM t'",
        "sqlite3 db.sqlite \"SELECT substr(id,1,8) || ' ' || status FROM goals\"",
        "sqlite3 db.sqlite \"SELECT id || '|' || name FROM t\"",
        "sqlite3 db.sqlite \"select id from t where x like '%||%'\"",
        'sqlite3 db.sqlite "SELECT a || b FROM t" | head',
        'sqlite3 -header db.sqlite "SELECT a || b AS c FROM t" 2>&1',
    ],
)
def test_concatenation_inside_a_quoted_query_stays_a_read(gate, command):
    assert _safe(gate, command)


@pytest.mark.parametrize(
    "command",
    [
        'sqlite3 db.sqlite "SELECT 1" || rm -rf x',
        'sqlite3 db.sqlite "SELECT 1" || sqlite3 db.sqlite "DROP TABLE t"',
        'sqlite3 db.sqlite "SELECT a || b FROM t; DROP TABLE t"',
    ],
)
def test_an_unquoted_or_chain_or_a_write_after_the_concatenation_is_still_gated(gate, command):
    """Controls: the quoted-operator allowance did not open the chain or the statement list."""
    assert not _safe(gate, command)
