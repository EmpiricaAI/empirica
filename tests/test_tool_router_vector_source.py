"""The tool-router's vector read must target a table that exists.

`tool-router.py` runs on every UserPromptSubmit and read `epistemic_assessments`
until 2026-08-21. That table was dropped by a migration whose own comment reads
*"epistemic_assessments → (unused, just drop)"* — and this hook was its reader.
The whole body sits in a bare `except`, so the missing table yielded
`(None, None)`, `determine_mode(None)` returned `"unknown"`, and the router ran
inert on every prompt with nothing to show for it.

Reported by ecodex's round-12 audit against their vendored copy, which is how a
defect on the hottest path in the plugin surfaced from another repo rather than
from here.

Two things are guarded, because fixing the query alone would leave the class open:

1. **The table named in the query must exist in the real schema.** A name check,
   not a behaviour check — behaviour cannot see this, since the bare `except`
   converts a missing table into a plausible "no vectors yet".
2. **The failure must be visible.** A router that silently degrades to `unknown`
   is indistinguishable from one correctly reporting no-vectors-yet, and that
   indistinguishability is the entire reason this survived.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROUTER = (
    Path(__file__).resolve().parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "tool-router.py"
)

#: Tables a migration has dropped. Reading one is always a dead read.
DROPPED_TABLES = {"epistemic_assessments"}


#: A string is SQL only if it carries a statement keyword. Without this the
#: extractor matches ordinary prose — "from the task", "from workflow" — and a
#: guard that reports phantom tables gets deleted rather than fixed.
_SQL_STMT = re.compile(r"\b(?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b", re.I)
_SQL_TABLE = re.compile(r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)", re.I)


def _sql_table_names(source: str) -> set[str]:
    """Tables named in FROM/JOIN inside string literals that are actually SQL."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and _SQL_STMT.search(node.value):
            names |= {m.group(1).lower() for m in _SQL_TABLE.finditer(node.value)}
    return names


def test_the_router_reads_no_dropped_table():
    """NEGATIVE CONTROL for the class: a dropped table must never be queried."""
    referenced = _sql_table_names(ROUTER.read_text())
    dead = referenced & DROPPED_TABLES
    assert not dead, (
        f"tool-router queries dropped table(s) {sorted(dead)}. Behaviour cannot catch this — "
        "the bare except turns a missing table into a plausible 'no vectors yet'."
    )


def test_the_guard_would_fire_on_the_pre_fix_source():
    """A guard that has never fired is not a guard."""
    pre_fix = 'x = """SELECT vectors FROM epistemic_assessments WHERE session_id = ?"""'
    assert _sql_table_names(pre_fix) & DROPPED_TABLES == {"epistemic_assessments"}

    # And prose must not be mistaken for SQL, or the guard cries wolf and dies.
    prose = 'x = "read the vectors from the task, then from workflow state"'
    assert _sql_table_names(prose) == set()


@pytest.mark.parametrize("table", sorted(_sql_table_names(ROUTER.read_text())))
def test_every_table_the_router_reads_exists_in_a_freshly_built_db(table, tmp_path):
    """Built by the real constructor at a tmp path, so a rename breaks this at once.

    Not read from this machine's live database — that would measure the box, and
    a table present only because some old migration once created it here would
    pass while a fresh install fails.

    Nothing here is allowed to skip. A parametrised existence check that skips
    every case reports clean forever, which is the same false-negative shape as
    the dead read it is guarding.
    """
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase(db_path=str(tmp_path / "fresh.db"))
    try:
        present = {r[0].lower() for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        db.close()

    assert present, "the constructor produced no tables — the probe itself is broken"
    assert table in present, (
        f"tool-router reads `{table}`, absent from a freshly built database. "
        "A fresh install would run this read dead, exactly as the dropped "
        "epistemic_assessments read did."
    )


def test_a_failed_vector_read_is_reported_not_swallowed():
    """The bare except is what let a dead read look like an empty one."""
    src = ROUTER.read_text()
    fn_start = src.index("def get_active_session_vectors():")
    fn_end = src.index("def determine_mode(")
    body = src[fn_start:fn_end]
    assert "except Exception:" not in body, "a bare swallow here reintroduces the invisibility"
    assert "stderr" in body, "the failure must reach somewhere a human or a log can see it"
