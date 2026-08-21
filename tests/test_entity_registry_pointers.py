"""Core names the registry spine and nothing else. David's clean-break ruling.

`entity_registry.source_db`/`source_table` tell a consumer where an entity's full
record lives. NLE reported the subtler half: 22 of 49 contacts had a correct
pointer and no row behind it. Measuring the whole registry found a second, larger
half — pointers naming tables that **do not exist in the database they name**:

    source_db=workspace  source_table=practitioner_presence   57 rows   never created, anywhere
    source_db=workspace  source_table=engagement              34 rows   real table is `engagements`
    source_db=workspace  source_table=organization            24 rows   real table is `organizations`

115 rows, from `source_table=entity_type` in the mint. **The type is not the
table**, and using one as the other is invisible until somebody dereferences it.

`source_db='cortex'` rows were deliberately excluded from that count — their table
lives in a database this process cannot see, so calling them broken would be
judging another repo's schema from here. NLE then dereferenced them on the cortex
host: all 17 valid. Not counting them was correct, and the discipline that made it
correct is the same one this file now enforces in code.

**The fix that was NOT taken.** An interim version gave core a
`contact -> contacts.contact_id` map so it could name the right table. That fixed
the defect and made core hardcode another practice's schema — a column rename over
there would silently degrade a pointer over here with nothing to announce it.
David ruled the clean break instead: core owns the spine, workspace authors detail,
and **whoever writes the detail row repoints the entity at it**.

So core writes `entity_registry` — self-referential and TRUE, because for a
core-minted entity the registry row IS the record so far. Not a placeholder: a
consumer that dereferences it finds the row it already has.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.data.repositories.workspace_db import SPINE_SOURCE_TABLE

SCHEMA = """
CREATE TABLE contacts (contact_id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE organizations (org_id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE engagements (engagement_id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE entity_registry (entity_id TEXT PRIMARY KEY, source_table TEXT NOT NULL);
"""


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "w.db")
    c.executescript(SCHEMA)
    try:
        yield c
    finally:
        c.close()


def test_the_spine_pointer_is_self_referential_and_resolvable(conn):
    """It must name a table that exists — the whole defect was naming ones that don't."""
    assert SPINE_SOURCE_TABLE == "entity_registry"
    real = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert SPINE_SOURCE_TABLE in real


#: Functions that register an entity WITHOUT authoring its detail row. These are
#: the spine-only paths and the ones the ruling binds.
SPINE_ONLY = ("mint_contact", "mint_entity", "upsert_practitioner_presence")

#: NARROW, NAMED EXEMPTION. `create_engagement` INSERTs the engagements row and
#: then registers it in the same call, so naming the table is not a claim about
#: somebody else's schema — it is the writer pointing at what it just wrote, which
#: is exactly what the ruling asks for ("whoever writes the detail row repoints the
#: entity at it"). A guard broad enough to catch this would force the one
#: legitimate writer to lie, and would then be disabled rather than obeyed.
EXEMPT_BECAUSE_IT_WRITES_THE_ROW = ("create_engagement",)


def _source_table_values_by_function(path):
    """Every value assigned to `source_table`, keyed by enclosing function.

    Records NON-literals too — as `None` — and that is the load-bearing detail. An
    earlier version collected string constants only, so a function assigning
    `SPINE_SOURCE_TABLE` produced no entry at all: the probe inspected zero of the
    three paths it exists to inspect and reported clean. Its own
    is-the-instrument-live assertion is what caught that.
    """
    import ast

    tree = ast.parse(path.read_text())
    out: dict[str, list[str | None]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.keyword) and sub.arg == "source_table":
                value = sub.value.value if isinstance(sub.value, ast.Constant) else None
                out.setdefault(node.name, []).append(value if isinstance(value, str) else None)
    return out


def test_the_spine_paths_name_no_detail_table():
    """THE ruling, enforced structurally rather than by review.

    Core writing `contacts` / `organizations` / `engagements` from a path that did
    not author the row is the coupling that was removed; it must not creep back as
    a literal, a map, or an f-string.

    AST-scoped to the enclosing function rather than line-matched, so the narrow
    exemption above is expressed as a rule and not as a magic line number.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "empirica"
    detail_tables = {"contacts", "organizations", "engagements"}

    offenders, checked = [], []
    for path in (
        root / "cli" / "command_handlers" / "entity_commands.py",
        root / "data" / "repositories" / "workspace_db.py",
    ):
        for fn, values in _source_table_values_by_function(path).items():
            if fn in EXEMPT_BECAUSE_IT_WRITES_THE_ROW:
                continue
            checked.append(fn)
            for value in values:
                if value in detail_tables:
                    offenders.append(f"{path.name}::{fn} -> {value!r}")

    assert set(SPINE_ONLY) & set(checked), (
        f"the probe inspected {checked} and none of the spine paths — it is looking "
        "in the wrong place, so a clean result proves nothing"
    )
    assert not offenders, f"core claims a workspace detail table it did not write: {offenders}"


def test_the_guard_would_have_fired_on_the_pre_ruling_code():
    """NEGATIVE CONTROL — a guard that has never fired is not a guard."""
    pre_fix = '            source_table="contacts",'
    code = pre_fix.split("#", 1)[0]
    assert "source_table" in code and '"contacts"' in code


def test_a_comment_mentioning_the_tables_is_not_an_offence():
    """The comments explaining the ruling name those tables on purpose.

    A guard that fired on its own rationale would be deleted rather than obeyed.
    """
    commented = '            source_table=SPINE_SOURCE_TABLE,  # not "contacts" — workspace repoints'
    code = commented.split("#", 1)[0]
    assert '"contacts"' not in code
