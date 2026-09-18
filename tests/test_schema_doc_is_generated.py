"""DATABASE_SCHEMA_UNIFIED.md's table inventory is generated, and CI says so when it drifts.

The doc was hand-maintained and measured 33-of-82 tables with 16 wrong column
counts under a "Last Updated" line. The generator renders the schema a fresh
install materialises (ALL_SCHEMAS + every migration); this test is the guard
that keeps the committed block equal to that render. A migration that adds a
column without regenerating the doc fails here, not in a reader's head.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "gen_schema_doc.py"


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location("gen_schema_doc", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fresh_block(gen):
    return gen.current_block()


def test_committed_inventory_matches_a_fresh_render(gen, fresh_block):
    """The guard. If this fails: python3 scripts/gen_schema_doc.py, commit the doc."""
    doc = gen.DOC.read_text(encoding="utf-8")
    have = gen.committed_block(doc)
    assert have is not None, "no generated block in the doc — run scripts/gen_schema_doc.py"
    assert have == fresh_block, "table inventory is stale — run scripts/gen_schema_doc.py and commit"


def test_render_is_deterministic_and_unstamped(gen, fresh_block):
    """A timestamp in the block would make the guard fail on every run forever."""
    import re

    assert gen.current_block() == fresh_block
    header = "\n".join(fresh_block.splitlines()[:12])
    assert not re.search(r"\d{4}-\d{2}-\d{2}", header), "no date stamp in the generated block"
    assert "Last Updated" not in fresh_block


def test_inventory_covers_every_registry_table(gen, fresh_block, tmp_path):
    db = tmp_path / "s.db"
    gen.materialise_current_schema(db)
    conn = sqlite3.connect(db)
    try:
        tables = [t for t in gen._tables(conn) if not gen._is_fts_shadow(t)]
        counts = {t: len(conn.execute(f"PRAGMA table_info('{t}')").fetchall()) for t in tables}
    finally:
        conn.close()
    assert len(tables) >= 60
    for t, n in counts.items():
        assert f"#### `{t}`\n**{n} columns**" in fresh_block, (t, n)


def test_splice_replaces_only_the_marked_block(gen):
    doc = "intro\n\n" + gen.BEGIN + "\nold\n" + gen.END + "\n\n## Key Foreign Key Relationships\nrest\n"
    out = gen.splice(doc, gen.BEGIN + "\nnew\n" + gen.END)
    assert out == "intro\n\n" + gen.BEGIN + "\nnew\n" + gen.END + "\n\n## Key Foreign Key Relationships\nrest\n"


def test_splice_inserts_before_relationships_when_no_block_exists(gen):
    doc = "intro\n\n## Key Foreign Key Relationships\nrest\n"
    out = gen.splice(doc, "BLOCK")
    assert out.index("BLOCK") < out.index("## Key Foreign Key Relationships")


def test_diff_db_names_live_only_tables(gen, tmp_path, capsys):
    live = tmp_path / "live.db"
    gen.materialise_current_schema(live)
    conn = sqlite3.connect(live)
    conn.execute("CREATE TABLE legacy_left_behind (id TEXT)")
    conn.execute("DROP TABLE notes")
    conn.commit()
    conn.close()

    assert gen.diff_db(live) == 0
    out = capsys.readouterr().out
    assert "live-only (1): legacy_left_behind" in out
    assert "registry-only (1): notes" in out


def test_check_mode_exit_codes(gen, monkeypatch, tmp_path):
    stale = tmp_path / "doc.md"
    stale.write_text("intro\n\n" + gen.BEGIN + "\nstale\n" + gen.END + "\n", encoding="utf-8")
    monkeypatch.setattr(gen, "DOC", stale)
    assert gen.main(["--check"]) == 1
    assert gen.main([]) == 0  # rewrites
    assert gen.main(["--check"]) == 0
