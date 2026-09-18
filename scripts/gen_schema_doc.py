#!/usr/bin/env python3
"""Generate the table inventory in docs/reference/DATABASE_SCHEMA_UNIFIED.md
from the real schema, so it cannot drift again.

The doc was hand-maintained. Measured 2026-09-11 against a live practice db it
documented 33 of 82 tables and stated the wrong column count on 16 of those 33,
under a "Last Updated" line that made it read maintained. Nothing regenerated
it, so every migration since 2026-02-11 widened the gap silently.

Source of truth here is the schema a fresh install actually gets: ALL_SCHEMAS
applied, then every migration in the registry run through MigrationRunner —
the same materialisation `tests/schema_shapes.build_db(..., "current")` does,
because a schema string alone is the pre-migration world. The generated block
sits between marker comments; everything outside them (categories, prose,
relationship diagrams, access patterns) stays hand-authored.

    python3 scripts/gen_schema_doc.py           # rewrite the block in place
    python3 scripts/gen_schema_doc.py --check   # exit 1 if the committed block is stale
    python3 scripts/gen_schema_doc.py --diff-db <sessions.db>
                                                # tables a LIVE db has that the
                                                # registry does not, and vice versa

No timestamp is written into the generated block: a stamp would make --check
fail on every run forever (that mistake was made once already on another
generated reference).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "reference" / "DATABASE_SCHEMA_UNIFIED.md"
BEGIN = "<!-- BEGIN GENERATED: table inventory (scripts/gen_schema_doc.py) — do not edit by hand -->"
END = "<!-- END GENERATED -->"


def materialise_current_schema(path: Path) -> None:
    """ALL_SCHEMAS then the full migration registry — the schema a fresh
    install ends up with. Mirrors tests/schema_shapes.build_db('current')
    deliberately rather than importing it: a script must not depend on the
    test tree, and eight lines are cheaper than a fragile import."""
    sys.path.insert(0, str(REPO))
    from empirica.data.migrations.migration_runner import MigrationRunner
    from empirica.data.migrations.migrations import ALL_MIGRATIONS
    from empirica.data.schema import ALL_SCHEMAS

    conn = sqlite3.connect(path)
    try:
        for sql in ALL_SCHEMAS:
            conn.execute(sql)
        conn.commit()
        MigrationRunner(conn).run_all(ALL_MIGRATIONS)
        conn.commit()
    finally:
        conn.close()


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _is_fts_shadow(name: str) -> bool:
    # FTS5 virtual tables materialise as <name>_config/_data/_docsize/_idx; those
    # are storage internals, not tables anyone queries.
    return any(name.endswith(s) for s in ("_fts_config", "_fts_data", "_fts_docsize", "_fts_idx"))


def render_table(conn: sqlite3.Connection, name: str) -> list[str]:
    cols = conn.execute(f"PRAGMA table_info('{name}')").fetchall()
    fks = {r[3]: (r[2], r[4]) for r in conn.execute(f"PRAGMA foreign_key_list('{name}')")}
    indexes = [r[1] for r in conn.execute(f"PRAGMA index_list('{name}')") if not r[1].startswith("sqlite_")]
    lines = [f"#### `{name}`", f"**{len(cols)} columns**"]
    for _cid, col, ctype, notnull, default, pk in cols:
        bits = [f"`{col}`", ctype or "ANY"]
        if pk:
            bits.append("PRIMARY KEY")
        if notnull and not pk:
            bits.append("NOT NULL")
        if default is not None:
            bits.append(f"DEFAULT {default}")
        if col in fks:
            t, c = fks[col]
            bits.append(f"(FK: {t}.{c})")
        lines.append("- " + " ".join(bits))
    if indexes:
        lines.append(f"- *indexes:* {', '.join(f'`{i}`' for i in sorted(indexes))}")
    lines.append("")
    return lines


def render_block(conn: sqlite3.Connection) -> str:
    tables = [t for t in _tables(conn) if not _is_fts_shadow(t)]
    shadows = [t for t in _tables(conn) if _is_fts_shadow(t)]
    n_idx = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]
    n_trg = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]
    out = [
        BEGIN,
        "",
        "## Table Inventory (generated)",
        "",
        f"**{len(tables)} tables** (+ {len(shadows)} FTS5 shadow tables), **{n_idx} indexes**, "
        f"**{n_trg} triggers** — the schema a fresh install materialises: `ALL_SCHEMAS` plus "
        "every migration in `empirica/data/migrations/migrations.py`, applied in order.",
        "",
        "Regenerate with `python3 scripts/gen_schema_doc.py`; CI fails when this block is stale. "
        "A live database can hold tables outside this inventory (created lazily by code, or "
        "left behind by removed schemas): `python3 scripts/gen_schema_doc.py --diff-db <sessions.db>` lists them.",
        "",
        "Alphabetical. Column lines read `name TYPE [PRIMARY KEY|NOT NULL] [DEFAULT x] [(FK: table.col)]`.",
        "",
    ]
    for t in tables:
        out.extend(render_table(conn, t))
    out.append(END)
    return "\n".join(out)


def splice(doc: str, block: str) -> str:
    """Replace the marker block, or append it before '## Key Foreign Key Relationships'
    on the first run when no markers exist yet."""
    if BEGIN in doc and END in doc:
        head = doc[: doc.index(BEGIN)]
        tail = doc[doc.index(END) + len(END) :]
        return head + block + tail
    anchor = "## Key Foreign Key Relationships"
    if anchor in doc:
        i = doc.index(anchor)
        return doc[:i] + block + "\n\n" + doc[i:]
    return doc.rstrip("\n") + "\n\n" + block + "\n"


def current_block() -> str:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "schema.db"
        materialise_current_schema(db)
        conn = sqlite3.connect(db)
        try:
            return render_block(conn)
        finally:
            conn.close()


def committed_block(doc: str) -> str | None:
    if BEGIN not in doc or END not in doc:
        return None
    return doc[doc.index(BEGIN) : doc.index(END) + len(END)]


def diff_db(live: Path) -> int:
    with tempfile.TemporaryDirectory() as td:
        reg = Path(td) / "schema.db"
        materialise_current_schema(reg)
        rconn, lconn = sqlite3.connect(reg), sqlite3.connect(f"file:{live}?mode=ro", uri=True)
        try:
            registry, on_disk = set(_tables(rconn)), set(_tables(lconn))
        finally:
            rconn.close()
            lconn.close()
    live_only, registry_only = sorted(on_disk - registry), sorted(registry - on_disk)
    print(f"registry: {len(registry)} tables · live db: {len(on_disk)} tables")
    print(f"live-only ({len(live_only)}): {' '.join(live_only) or '-'}")
    print(f"registry-only ({len(registry_only)}): {' '.join(registry_only) or '-'}")
    return 0


def _doc_label() -> str:
    try:
        return str(DOC.relative_to(REPO))
    except ValueError:
        return str(DOC)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed block differs from a fresh render")
    ap.add_argument("--diff-db", type=Path, help="compare a live sessions.db's tables against the registry")
    args = ap.parse_args(argv)

    if args.diff_db:
        return diff_db(args.diff_db)

    fresh = current_block()
    doc = DOC.read_text(encoding="utf-8")
    if args.check:
        have = committed_block(doc)
        if have == fresh:
            print(f"{_doc_label()}: table inventory is current")
            return 0
        print(
            f"{_doc_label()}: table inventory is STALE "
            f"({'no generated block' if have is None else 'block differs from a fresh render'}). "
            "Run: python3 scripts/gen_schema_doc.py",
            file=sys.stderr,
        )
        return 1

    new_doc = splice(doc, fresh)
    if new_doc != doc:
        DOC.write_text(new_doc, encoding="utf-8")
        print(f"rewrote the generated block in {_doc_label()}")
    else:
        print(f"{_doc_label()}: already current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
